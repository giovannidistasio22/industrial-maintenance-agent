"""
Test di integrazione dell'osservabilità (senza Ollama).

Verifica la catena completa osservata:
    User query -> POST /chat -> trace (albero di span) -> risposta con 'observability'
con LLM scriptato (ScriptedLLM) e RAG stub: i test misurano l'orchestrazione
dell'osservabilità (span, riepilogo, endpoint /traces e /metrics), non il modello.

Coprono:
  - POST /chat genera un trace e restituisce il riepilogo 'observability'
    (durata, token, tool chiamati, documenti recuperati, errori)
  - GET /traces/{session_id} restituisce l'albero di span + waterfall
  - GET /metrics espone i contatori in-process
  - un tool in errore è visibile nello span (status=error), nel riepilogo
    e nelle metriche
"""

import pytest

from backend.services import observability

# La catena completa (backend.main -> graph -> nodes -> tools -> rag/Chroma) deve
# essere importabile: su macchine senza l'indice Chroma (o filesystem dove
# SQLite non può scrivere, es. mount SFTP) il test si salta, non fallisce
# (stesso pattern di test_agent_dataset.py / test_unit_observability.py).
try:
    from backend.main import app  # noqa: F401
    _IMPORT_ERROR = None
except Exception as exc:
    _IMPORT_ERROR = exc


def _iter_span_names(node):
    yield node["name"]
    for child in node.get("children", []):
        yield from _iter_span_names(child)


def _find_spans(node, name):
    out = []
    if node["name"] == name:
        out.append(node)
    for child in node.get("children", []):
        out.extend(_find_spans(child, name))
    return out


@pytest.mark.skipif(
    _IMPORT_ERROR is not None,
    reason=f"import dell'app agente non riuscito (Chroma non disponibile?): {_IMPORT_ERROR}",
)
def test_chat_turn_produces_trace_and_summary(agent_client, make_scripted_llm):
    """Un turno normale genera un trace e restituisce il riepilogo
    'observability' nella risposta: durata, token, tool, documenti, errori."""
    case = {
        "question": "P-102 ha problemi di temperatura.",
        "machine": "P-102",
        "expected_tools": ["get_sensor_data", "search_manual"],
        "expected_behavior": "answer_only",
        "canned_answer": "Trend di temperatura cuscinetto in aumento su P-102.",
    }
    make_scripted_llm(case)

    r = agent_client.post("/chat", json={"message": case["question"]})
    assert r.status_code == 200
    body = r.json()

    # Riepilogo osservabilità nella risposta: le 5 domande della Fase 10
    obs = body["observability"]
    assert obs["turn_id"] == body["turn_id"]
    assert obs["duration_ms"] is not None                    # quanto tempo
    # LLM scriptato (ScriptedLLM, non TracedChatModel): nessun span llm_call,
    # quindi 0 chiamate e 0 token (gli span llm_call li crea il LLM tracciato).
    assert obs["llm_calls"] == 0
    assert obs["tokens"]["total"] == 0
    assert "get_sensor_data" in obs["tools_called"]           # quale tool
    assert "search_manual" in obs["tools_called"]
    assert isinstance(obs["docs_retrieved"], list)            # quali documenti
    assert obs["errors"] == []                               # dove gli errori (nessuno)

    # GET /traces/{session_id}: albero di span + waterfall
    t = agent_client.get(f"/traces/{body['session_id']}")
    assert t.status_code == 200
    trace = t.json()["traces"][0]
    # Con LLM scriptato e RAG stub gli span sono: chat_turn -> tool_call (x2).
    # Gli span llm_call/rag_retrieval li creano il LLM tracciato e il RAG reale.
    names = list(_iter_span_names(trace["spans"]))
    assert trace["spans"]["name"] == "chat_turn"
    assert names.count("tool_call") == 2
    assert "tool_call" in trace["waterfall"]

    # GET /metrics: contatori in-process
    m = agent_client.get("/metrics")
    assert m.status_code == 200
    snap = m.json()
    assert snap["counters"]["turns_total"] >= 1
    assert snap["counters"]["tool_calls_total"]["get_sensor_data"] >= 1
    assert "turn_latency_ms" in snap["histograms"]


@pytest.mark.skipif(
    _IMPORT_ERROR is not None,
    reason=f"import dell'app agente non riuscito (Chroma non disponibile?): {_IMPORT_ERROR}",
)
def test_tool_error_visible_in_trace_summary_and_metrics(agent_client, make_scripted_llm):
    """Un tool in errore (macchina inesistente) è visibile nello span
    (status=error), nel riepilogo 'observability' e nelle metriche."""
    case = {
        "question": "Come sta X-999?",
        "machine": "X-999",
        "expected_tools": ["get_machine_status"],
        "expected_behavior": "answer_only",
        "canned_answer": "X-999 non è in anagrafica.",
    }
    make_scripted_llm(case)

    before = observability.metrics.snapshot()["counters"]["tool_errors_total"].get(
        "get_machine_status", 0
    )

    r = agent_client.post("/chat", json={"message": case["question"]})
    assert r.status_code == 200
    body = r.json()
    obs = body["observability"]
    assert "get_machine_status" in obs["tools_called"]
    assert any("tool_call" in e for e in obs["errors"])

    # Lo span tool_call nel trace ha status=error
    t = agent_client.get(f"/traces/{body['session_id']}")
    assert t.status_code == 200
    trace = t.json()["traces"][0]
    tool_spans = _find_spans(trace["spans"], "tool_call")
    assert any(s["status"] == "error" and s["error"] for s in tool_spans)

    # Metriche: l'errore tool è stato registrato (delta, il singleton persiste)
    after = observability.metrics.snapshot()["counters"]["tool_errors_total"].get(
        "get_machine_status", 0
    )
    assert after >= before + 1
