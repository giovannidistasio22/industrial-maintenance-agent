"""
Agent test: dataset-driven.

Ogni caso di tests/integration/agent_dataset.json dichiara:
  - question:            la domanda del tecnico
  - expected_tools:      i tool che l'agente deve chiamare, IN ORDINE
  - expected_information: informazioni che devono comparire nella risposta
  - expected_behavior:   answer_only | propose_work_order | propose_and_deny
  - expected_gathered:   (opzionale) dati che devono provenire davvero dal CMMS

L'LLM è scriptato (ScriptedLLM): il test verifica l'ORCHESTRAZIONE
(quali tool, in che ordine, con che argomenti, e il flusso di conferma
human-in-the-loop), non la qualità del modello.

La catena Agent -> Tool -> CMMS è reale: il CMMS gira in-process
(SQLite in-memory), solo l'LLM è finto.
"""

import json
from pathlib import Path

import pytest

# La catena completa (backend.main -> graph -> nodes -> tools -> rag/Chroma) deve
# essere importabile: su macchine senza l'indice Chroma i test si saltano,
# non falliscono.
try:
    from backend.main import app  # noqa: F401
    _IMPORT_ERROR = None
except Exception as exc:
    _IMPORT_ERROR = exc

pytestmark = pytest.mark.skipif(
    _IMPORT_ERROR is not None,
    reason=f"import dell'app agente non riuscito (Chroma/LLM non disponibili?): {_IMPORT_ERROR}",
)

DATASET = json.loads(
    (Path(__file__).resolve().parent / "agent_dataset.json").read_text(encoding="utf-8")
)


@pytest.mark.parametrize("case", DATASET, ids=lambda c: c["id"])
def test_agent_case(agent_client, make_scripted_llm, clean_work_orders, case):
    from backend.services import cmms_client

    make_scripted_llm(case)
    client = agent_client

    # --- Turno 1: la domanda ---------------------------------------------
    r = client.post("/chat", json={"message": case["question"]})
    assert r.status_code == 200
    body = r.json()
    session_id = body["session_id"]

    # 1) I tool chiamati sono quelli attesi, nell'ordine atteso
    called = [d["tool"] for d in body["gathered_data"]]
    assert called == case["expected_tools"], f"tool chiamati: {called}"

    # 2) La risposta contiene le informazioni attese
    for token in case["expected_information"]:
        assert token.lower() in body["answer"].lower(), f"mancante nella risposta: {token!r}"

    # 3) I dati raccolti provengono davvero dal CMMS (Agent -> Tool -> CMMS)
    for check in case.get("expected_gathered", []):
        entry = next(d for d in body["gathered_data"] if d["tool"] == check["tool"])
        assert check["must_contain"] in json.dumps(entry["result"], ensure_ascii=False), (
            f"il risultato di {check['tool']} non contiene {check['must_contain']!r}"
        )

    behavior = case["expected_behavior"]

    # --- Comportamento atteso ---------------------------------------------
    if behavior == "answer_only":
        assert body["confirmation_required"] is False
        assert body["pending_action"] is None
        return

    # propose_work_order / propose_and_deny: c'è una proposta in attesa
    assert body["confirmation_required"] is True, "mancava la richiesta di conferma"
    assert body["pending_action"]["machine_id"] == case["machine"]

    # --- Turno 2: la risposta esplicita alla proposta ----------------------
    answer = "No, non ora." if behavior == "propose_and_deny" else "Sì, confermo."
    r2 = client.post("/chat", json={"message": answer, "session_id": session_id})
    assert r2.status_code == 200
    b2 = r2.json()
    assert b2["confirmation_required"] is False
    assert b2["pending_action"] is None

    # L'effetto sul CMMS è quello atteso
    wos = cmms_client.get_open_work_orders(case["machine"])["open_work_orders"]
    agent_wos = [w for w in wos if w["created_by"] == "maintenance-agent"]

    if behavior == "propose_work_order":
        assert len(agent_wos) == 1, "il work order confermato non è stato creato nel CMMS"
        assert agent_wos[0]["machine_id"] == case["machine"]
    else:
        assert agent_wos == [], "con un rifiuto non deve essere creato nessun work order"
