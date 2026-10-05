"""
FASE 8 - Unit test: livello cmms_client (Agent -> HTTP -> CMMS).

L'API CMMS è simulata con httpx.MockTransport: niente server, niente DB,
niente rete. Si verifica il contratto del client:
  - URL e parametri corretti
  - normalizzazione degli argomenti (machine_id maiuscolo)
  - gestione errori: NON solleva mai eccezioni, restituisce sempre un dict
    (così il grafo può mostrarlo all'LLM invece di andare in crash)
"""

import httpx

import cmms_client


def _patch(monkeypatch, handler):
    # Replica la configurazione del client reale (header X-API-Key incluso)
    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="http://cmms.test",
        headers={"X-API-Key": cmms_client.API_KEY},
    )
    monkeypatch.setattr(cmms_client, "_client", client)
    return client


# ---------------------------------------------------------------------------
# get_machine_status
# ---------------------------------------------------------------------------

def test_get_machine_status_ok(monkeypatch):
    seen = {}

    def handler(request):
        seen["path"] = request.url.path
        seen["key"] = request.headers.get("X-API-Key")
        return httpx.Response(200, json={"id": "P-102", "type": "centrifugal pump", "status": "running"})

    _patch(monkeypatch, handler)
    result = cmms_client.get_machine_status("P-102")

    assert result["id"] == "P-102"
    assert seen["path"] == "/machines/P-102"
    assert seen["key"] == cmms_client.API_KEY


def test_machine_id_normalized_to_upper(monkeypatch):
    seen = []

    def handler(request):
        seen.append(request.url.path)
        return httpx.Response(200, json={"id": "P-102"})

    _patch(monkeypatch, handler)
    cmms_client.get_machine_status("p-102")
    assert seen == ["/machines/P-102"]


def test_get_machine_status_404_returns_error_dict(monkeypatch):
    def handler(request):
        return httpx.Response(404, json={"detail": "Macchina 'X-999' non trovata nel CMMS."})

    _patch(monkeypatch, handler)
    result = cmms_client.get_machine_status("X-999")
    assert "error" in result
    assert "404" in result["error"]


def test_get_machine_status_401_credentials(monkeypatch):
    def handler(request):
        return httpx.Response(401, json={"detail": "API key mancante o non valida."})

    _patch(monkeypatch, handler)
    result = cmms_client.get_machine_status("P-102")
    assert "error" in result
    assert "credenziali" in result["error"]


def test_cmms_unreachable_returns_error_dict(monkeypatch):
    def handler(request):
        raise httpx.ConnectError("connection refused")

    _patch(monkeypatch, handler)
    result = cmms_client.get_machine_status("P-102")
    assert "error" in result
    assert "non raggiungibile" in result["error"]


def test_timeout_returns_error_dict(monkeypatch):
    def handler(request):
        raise httpx.TimeoutException("timeout")

    _patch(monkeypatch, handler)
    result = cmms_client.get_machine_status("P-102")
    assert "error" in result
    assert "Timeout" in result["error"]


# ---------------------------------------------------------------------------
# get_sensor_data
# ---------------------------------------------------------------------------

def test_get_sensor_data_url_and_params(monkeypatch):
    seen = {}

    def handler(request):
        seen["path"] = request.url.path
        seen["limit"] = request.url.params.get("limit")
        return httpx.Response(200, json={"machine_id": "P-102", "readings": []})

    _patch(monkeypatch, handler)
    result = cmms_client.get_sensor_data("P-102")
    assert result == {"machine_id": "P-102", "readings": []}
    assert seen["path"] == "/machines/P-102/sensors"
    assert seen["limit"] == "5"


# ---------------------------------------------------------------------------
# get_open_work_orders
# ---------------------------------------------------------------------------

def test_get_open_work_orders_wraps_list(monkeypatch):
    orders = [{"id": "WO-1042", "machine_id": "P-102", "status": "open"}]

    def handler(request):
        assert request.url.path == "/work-orders"
        assert request.url.params.get("status") == "active"
        return httpx.Response(200, json=orders)

    _patch(monkeypatch, handler)
    result = cmms_client.get_open_work_orders("P-102")
    # L'API restituisce una lista; il client la impacchetta in un dict
    assert result == {"machine_id": "P-102", "open_work_orders": orders}


def test_get_open_work_orders_error_passthrough(monkeypatch):
    def handler(request):
        return httpx.Response(404, json={"detail": "Macchina 'X-999' non trovata nel CMMS."})

    _patch(monkeypatch, handler)
    result = cmms_client.get_open_work_orders("X-999")
    assert "error" in result


# ---------------------------------------------------------------------------
# create_work_order
# ---------------------------------------------------------------------------

def test_create_work_order_payload(monkeypatch):
    seen = {}

    def handler(request):
        seen["method"] = request.method
        seen["path"] = request.url.path
        import json
        seen["body"] = json.loads(request.content)
        return httpx.Response(201, json={"id": "WO-1043", "machine_id": "P-102", "status": "open"})

    _patch(monkeypatch, handler)
    result = cmms_client.create_work_order("p-102", "Controllare temperatura bearing", priority="high")

    assert result["id"] == "WO-1043"
    assert seen["method"] == "POST"
    assert seen["path"] == "/work-orders"
    assert seen["body"] == {
        "machine_id": "P-102",
        "description": "Controllare temperatura bearing",
        "priority": "high",
        "created_by": "maintenance-agent",
    }
