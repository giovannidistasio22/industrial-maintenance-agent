"""
FASE 8 - Integration test: la catena completa Agent -> Tool -> CMMS, in-process.

  - L'API CMMS (main6) gira in-process via httpx.ASGITransport (niente porte,
    niente PostgreSQL): i dati che l'agente vede sono quelli del seed.
  - L'LLM è scriptato: si verifica l'orchestrazione, non il modello.
  - Il RAG è uno stub: nessun dipendenza dall'indice vettoriale.

Qui si testa il LIVELLO HTTP (main8): /chat, il flusso di conferma
human-in-the-loop, il fail-safe sui messaggi ambigui, /history, /health.
"""

import pytest

try:
    import main8  # noqa: F401
    _IMPORT_ERROR = None
except Exception as exc:
    main8 = None
    _IMPORT_ERROR = exc

pytestmark = pytest.mark.skipif(
    main8 is None,
    reason=f"import main8 non riuscito (Chroma/LLM non disponibili?): {_IMPORT_ERROR}",
)


def _case_p102():
    return {
        "question": "P-102 ha problemi di temperatura.",
        "machine": "P-102",
        "expected_tools": ["get_sensor_data", "get_open_work_orders"],
        "canned_answer": "Il bearing di P-102 è salito a 93°C: trend anomalo.",
        "expected_behavior": "propose_work_order",
    }


def test_chat_full_chain_confirm(agent_client, make_scripted_llm, clean_work_orders):
    """Domanda -> diagnosi -> proposta -> conferma -> work order creato nel CMMS."""
    import cmms_client

    make_scripted_llm(_case_p102())

    r = agent_client.post("/chat", json={"message": _case_p102()["question"]})
    body = r.json()
    assert [d["tool"] for d in body["gathered_data"]] == _case_p102()["expected_tools"]
    assert body["confirmation_required"] is True
    assert body["pending_action"]["machine_id"] == "P-102"

    # I dati sensori provengono davvero dal CMMS (seed: picco a 93°C)
    import json as _json
    sensors = next(d for d in body["gathered_data"] if d["tool"] == "get_sensor_data")
    assert "93" in _json.dumps(sensors["result"])

    r2 = agent_client.post(
        "/chat", json={"message": "Sì, confermo.", "session_id": body["session_id"]}
    )
    b2 = r2.json()
    assert b2["confirmation_required"] is False
    assert "work order" in b2["answer"].lower()

    wos = cmms_client.get_open_work_orders("P-102")["open_work_orders"]
    agent_wos = [w for w in wos if w["created_by"] == "maintenance-agent"]
    assert len(agent_wos) == 1
    assert agent_wos[0]["machine_id"] == "P-102"


def test_deny_confirmation_creates_nothing(agent_client, make_scripted_llm, clean_work_orders):
    """Rifiuto esplicito: nessun work order deve essere creato (fail-safe)."""
    import cmms_client

    make_scripted_llm(_case_p102())

    body = agent_client.post("/chat", json={"message": _case_p102()["question"]}).json()
    assert body["confirmation_required"] is True

    b2 = agent_client.post(
        "/chat", json={"message": "No, non ora.", "session_id": body["session_id"]}
    ).json()
    assert b2["confirmation_required"] is False
    assert "non ho creato" in b2["answer"].lower()

    wos = cmms_client.get_open_work_orders("P-102")["open_work_orders"]
    assert not any(w["created_by"] == "maintenance-agent" for w in wos)


def test_unrelated_message_cancels_proposal(agent_client, make_scripted_llm, clean_work_orders):
    """Messaggio che non è né sì né no: la proposta viene annullata in sicurezza
    (mai eseguita) e il messaggio viene processato come nuovo turno."""
    import cmms_client

    make_scripted_llm(_case_p102())

    body = agent_client.post("/chat", json={"message": _case_p102()["question"]}).json()
    assert body["confirmation_required"] is True

    b2 = agent_client.post(
        "/chat",
        json={"message": "E quali sono le soglie di allarme?", "session_id": body["session_id"]},
    ).json()

    # La proposta pendente è stata annullata, non eseguita
    wos = cmms_client.get_open_work_orders("P-102")["open_work_orders"]
    assert not any(w["created_by"] == "maintenance-agent" for w in wos)
    assert "annullato" in b2["answer"].lower()
    # ...e la conversazione prosegue (il grafo è stato riavviato sul nuovo messaggio)
    assert b2["pending_action"] is None or b2["pending_action"]["machine_id"] == "P-102"


def test_informative_question_no_confirmation(agent_client, make_scripted_llm):
    """Domanda puramente informativa: nessun tool, nessuna proposta di scrittura."""
    case = {
        "question": "Come funziona una pompa centrifuga?",
        "machine": None,
        "expected_tools": [],
        "canned_answer": "Una pompa centrifuga converte energia meccanica in energia idraulica.",
        "expected_behavior": "answer_only",
    }
    make_scripted_llm(case)

    body = agent_client.post("/chat", json={"message": case["question"]}).json()
    assert body["gathered_data"] == []
    assert body["confirmation_required"] is False
    assert body["active_machine"] is None


def test_history_endpoint(agent_client, make_scripted_llm):
    make_scripted_llm(_case_p102())
    body = agent_client.post("/chat", json={"message": _case_p102()["question"]}).json()
    sid = body["session_id"]

    r = agent_client.get(f"/sessions/{sid}/history")
    assert r.status_code == 200
    h = r.json()
    assert h["active_machine"] == "P-102"
    assert h["pending_action"] is not None  # la proposta è ancora in attesa
    roles = [m["role"] for m in h["messages"]]
    assert "user" in roles and "assistant" in roles


def test_history_unknown_session_404(agent_client):
    r = agent_client.get("/sessions/00000000-0000-0000-0000-000000000000/history")
    assert r.status_code == 404


def test_health_reports_cmms_state(agent_client):
    r = agent_client.get("/health")
    assert r.status_code == 200
    # il CMMS è in-process e raggiungibile
    assert r.json() == {"status": "ok", "cmms": "up"}
