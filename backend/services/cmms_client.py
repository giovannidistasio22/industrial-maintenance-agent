"""
Agent - Client HTTP verso l'API del CMMS

L'agente NON conosce il database: parla solo con l'API REST del CMMS
(Agent -> Tool -> CMMS API -> PostgreSQL). Le firme delle funzioni sono
le stesse del vecchio mock_cmms.py, quindi grafo e nodi restano invariati.

Gli errori (macchina inesistente, CMMS spento, API key errata, timeout...)
NON sollevano eccezioni: diventano un dizionario {"error": "..."} che il
grafo passa all'LLM come qualsiasi altro risultato, così l'agente può
spiegare il problema all'utente invece di andare in crash.
"""

import os
from typing import Any

import httpx

BASE_URL = os.getenv("CMMS_BASE_URL", "http://localhost:8010")
API_KEY = os.getenv("CMMS_API_KEY", "dev-cmms-key")
TIMEOUT = float(os.getenv("CMMS_TIMEOUT", "5"))

_client = httpx.Client(base_url=BASE_URL, headers={"X-API-Key": API_KEY}, timeout=TIMEOUT)


def _request(method: str, path: str, **kwargs) -> Any:
    try:
        response = _client.request(method, path, **kwargs)
    except httpx.TimeoutException:
        return {"error": f"Timeout: il CMMS ({BASE_URL}) non ha risposto entro {TIMEOUT:.0f}s."}
    except httpx.RequestError as exc:
        return {"error": f"CMMS non raggiungibile ({BASE_URL}): {exc.__class__.__name__}."}

    if response.status_code == 401:
        return {"error": "Il CMMS ha rifiutato le credenziali (API key non valida)."}
    if response.status_code >= 400:
        try:
            detail = response.json().get("detail", response.text)
        except ValueError:
            detail = response.text
        return {"error": f"CMMS ({response.status_code}): {str(detail)[:200]}"}
    return response.json()


def _tag(machine_id: str) -> str:
    return (machine_id or "").strip().upper()


def get_machine_status(machine_id: str) -> dict:
    """GET /machines/{id}"""
    return _request("GET", f"/machines/{_tag(machine_id)}")


def get_sensor_data(machine_id: str) -> dict:
    """GET /machines/{id}/sensors  (ultime 5 letture, in ordine cronologico)"""
    return _request("GET", f"/machines/{_tag(machine_id)}/sensors", params={"limit": 5})


def get_maintenance_history(machine_id: str) -> dict:
    """GET /machines/{id}/maintenance"""
    return _request("GET", f"/machines/{_tag(machine_id)}/maintenance")


def get_open_work_orders(machine_id: str) -> dict:
    """GET /work-orders?machine_id=...&status=active  (open + in_progress)"""
    result = _request("GET", "/work-orders", params={"machine_id": _tag(machine_id), "status": "active"})
    if isinstance(result, dict):  # errore
        return result
    return {"machine_id": _tag(machine_id), "open_work_orders": result}


def create_work_order(machine_id: str, description: str, priority: str = "medium") -> dict:
    """POST /work-orders

    NON è registrata tra i tool che il grafo chiama in autonomia: crea dati veri,
    quindi richiede una conferma esplicita dell'utente (vedi README, "prossimi passi").
    """
    return _request(
        "POST", "/work-orders",
        json={"machine_id": _tag(machine_id), "description": description,
              "priority": priority, "created_by": "maintenance-agent"},
    )


def cmms_is_up() -> bool:
    try:
        return _client.get("/health").status_code == 200
    except httpx.RequestError:
        return False
