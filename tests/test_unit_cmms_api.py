"""
FASE 8 - Unit test: API CMMS (main6) con DB SQLite in-memory.

Il CMMS gira in-process (TestClient + SQLite): si verifica il contratto
dell'API — anagrafica macchine, letture sensori, storico, work order,
autenticazione — senza PostgreSQL e senza porte aperte.
"""

import pytest
from fastapi.testclient import TestClient

import main6


@pytest.fixture()
def client(cmms_app):
    return TestClient(cmms_app)


@pytest.fixture()
def auth():
    return {"X-API-Key": main6.API_KEY}


# ---------------------------------------------------------------------------
# Health / auth
# ---------------------------------------------------------------------------

def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "database": "up"}


def test_missing_api_key_rejected(client):
    r = client.get("/machines")
    assert r.status_code == 401


def test_wrong_api_key_rejected(cmms_app):
    r = TestClient(cmms_app).get("/machines", headers={"X-API-Key": "chiav sbagliata"})
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# Macchine
# ---------------------------------------------------------------------------

def test_get_machine(client, auth):
    r = client.get("/machines/P-102", headers=auth)
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == "P-102"
    assert body["type"] == "centrifugal pump"
    assert body["status"] == "running"


def test_get_machine_case_insensitive(client, auth):
    assert client.get("/machines/p-102", headers=auth).status_code == 200


def test_get_machine_unknown_404(client, auth):
    r = client.get("/machines/X-999", headers=auth)
    assert r.status_code == 404
    assert "non trovata" in r.json()["detail"]


# ---------------------------------------------------------------------------
# Sensori
# ---------------------------------------------------------------------------

def test_sensors_last5_chronological(client, auth):
    r = client.get("/machines/P-102/sensors", headers=auth)
    assert r.status_code == 200
    body = r.json()
    readings = body["readings"]
    assert len(readings) == 5
    # ordine cronologico (la più recente per ultima)
    timestamps = [x["timestamp"] for x in readings]
    assert timestamps == sorted(timestamps)
    # il seed di P-102 termina con il picco di temperatura 93°C
    assert readings[-1]["bearing_temp_c"] == 93


def test_sensors_limit_param(client, auth):
    r = client.get("/machines/P-102/sensors?limit=2", headers=auth)
    assert len(r.json()["readings"]) == 2


def test_sensors_unknown_machine_404(client, auth):
    assert client.get("/machines/X-999/sensors", headers=auth).status_code == 404


# ---------------------------------------------------------------------------
# Storico manutenzione
# ---------------------------------------------------------------------------

def test_maintenance_history(client, auth):
    r = client.get("/machines/P-102/maintenance", headers=auth)
    assert r.status_code == 200
    history = r.json()["history"]
    assert len(history) == 3
    assert history[0]["description"] == "Bearing replacement (both DE/NDE)"
    assert history[0]["technician"] == "M. Rossi"


# ---------------------------------------------------------------------------
# Work order
# ---------------------------------------------------------------------------

def test_work_orders_active_filter(client, auth):
    r = client.get("/work-orders?machine_id=P-102&status=active", headers=auth)
    assert r.status_code == 200
    orders = r.json()
    # nel seed P-102 ha solo WO-1042 aperto (gli altri sono closed)
    assert [w["id"] for w in orders] == ["WO-1042"]
    assert all(w["status"] == "open" for w in orders)


def test_work_orders_invalid_status_422(client, auth):
    r = client.get("/work-orders?status=chiuso", headers=auth)
    assert r.status_code == 422


def test_create_work_order(client, auth, clean_work_orders):
    r = client.post(
        "/work-orders",
        headers=auth,
        json={"machine_id": "P-102", "description": "Verificare trend temperatura bearing",
              "priority": "high", "created_by": "test"},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["id"] == "WO-1043"  # il primo dopo i seed (1030, 1035, 1042)
    assert body["status"] == "open"
    assert body["priority"] == "high"

    # è visibile tra i work order attivi
    r2 = client.get("/work-orders?machine_id=P-102&status=active", headers=auth)
    assert "WO-1043" in [w["id"] for w in r2.json()]


def test_create_work_order_unknown_machine_404(client, auth):
    r = client.post(
        "/work-orders",
        headers=auth,
        json={"machine_id": "X-999", "description": "qualunque descrizione"},
    )
    assert r.status_code == 404


def test_create_work_order_unknown_technician_404(client, auth):
    r = client.post(
        "/work-orders",
        headers=auth,
        json={"machine_id": "P-102", "description": "qualunque descrizione", "assigned_to": 999},
    )
    assert r.status_code == 404
