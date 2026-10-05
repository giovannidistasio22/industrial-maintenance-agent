"""
FASE 10 - Test unitari dell'osservabilità (senza Ollama, senza CMMS).

Coprono:
  - Tracer: span nidificati, marcatura errori, no-op senza trace attivo
  - Metrics: contatori, istogrammi, snapshot
  - TracedChatModel: span automatici per invoke e with_structured_output
  - log_event: JSON line su file
  - call_tool (nodo reale): un tool in errore finisce nelle metriche
"""

import json
import time

import pytest
from langchain_core.messages import AIMessage
from pydantic import BaseModel

import observability
from observability import (
    Tracer, Metrics, TracedChatModel, TrackedOllamaClient,
    turn_summary, waterfall, log_event, make_tracked_llm,
)


# ---------------------------------------------------------------------------
# Helper: LLM finto (niente rete, niente Ollama)
# ---------------------------------------------------------------------------

class FakeLLM:
    model = "fake-llm"

    def invoke(self, prompt, **kwargs):
        return AIMessage(content="risposta")

    def with_structured_output(self, schema):
        class _Proxy:
            def invoke(self, prompt, **kwargs):
                return schema()
        return _Proxy()


class Decision(BaseModel):
    ok: bool = True


@pytest.fixture(autouse=True)
def _hermetic_logs(monkeypatch, tmp_path):
    """Log e trace in una cartella temporanea: i test non sporcano il progetto."""
    monkeypatch.setattr(observability, "LOG_DIR", tmp_path)
    monkeypatch.setattr(observability, "LOG_FILE", tmp_path / "agent.jsonl")
    monkeypatch.setattr(observability, "TRACE_DIR", tmp_path / "traces")


# ---------------------------------------------------------------------------
# Tracer
# ---------------------------------------------------------------------------

def test_tracer_nested_spans():
    tracer = Tracer()
    trace = tracer.start_trace("s1", "t1", {"query": "q"})
    with tracer.span("tool_call", {"tool": "x"}) as tool_span:
        with tracer.span("llm_call", {"model": "m"}) as llm_span:
            llm_span.tokens["prompt"] = 10
    trace.root.end = time.perf_counter()

    assert tool_span in trace.root.children
    assert llm_span in tool_span.children
    assert tool_span.duration_ms is not None
    assert tool_span.tokens["prompt"] == 0  # i token sono sullo span figlio

    wf = waterfall(trace)
    assert "tool_call" in wf and "llm_call" in wf


def test_tracer_error_marking():
    tracer = Tracer()
    trace = tracer.start_trace("s1", "t1")
    with pytest.raises(RuntimeError):
        with tracer.span("llm_call"):
            raise RuntimeError("boom")
    trace.root.end = time.perf_counter()

    llm_spans = [s for s in trace.spans if s.name == "llm_call"]
    assert llm_spans[0].status == "error"
    assert "boom" in llm_spans[0].error
    assert "llm_call: RuntimeError: boom" in turn_summary(trace)["errors"]


def test_tracer_noop_without_active_trace():
    tracer = Tracer()
    with tracer.span("tool_call") as span:
        assert span is None  # nessun trace attivo: no-op, non crasha
    assert tracer.current_span() is None
    assert tracer.current_trace() is None


def test_tracer_session_storage_and_export(monkeypatch, tmp_path):
    monkeypatch.setattr(observability, "TRACE_DIR", tmp_path / "traces")
    tracer = Tracer()
    trace = tracer.start_trace("s1", "t1")
    trace.root.end = time.perf_counter()
    path = tracer.save_trace(trace)
    assert path is not None and path.exists()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["name"] == "chat_turn"
    assert data["attributes"]["session_id"] == "s1"
    assert tracer.session_traces("s1") == [trace]
    assert tracer.session_traces("s2") == []


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def test_metrics_snapshot():
    m = Metrics()
    m.record_turn(100.0)
    m.record_turn(300.0, error=True)
    m.record_llm(50.0)
    m.record_llm(60.0, error=True)
    m.record_tool("get_sensor_data", 10.0)
    m.record_tool("get_sensor_data", 20.0, error=True)
    m.record_rag(5.0)
    m.record_work_order()
    m.record_error("chat")
    m.add_tokens(100, 50)

    snap = m.snapshot()
    assert snap["counters"]["turns_total"] == 2
    assert snap["counters"]["turns_with_errors"] == 1
    assert snap["counters"]["llm_calls_total"] == 2
    assert snap["counters"]["llm_errors_total"] == 1
    assert snap["counters"]["tool_calls_total"]["get_sensor_data"] == 2
    assert snap["counters"]["tool_errors_total"]["get_sensor_data"] == 1
    assert snap["counters"]["rag_retrievals_total"] == 1
    assert snap["counters"]["work_orders_created_total"] == 1
    assert snap["counters"]["errors_total"]["chat"] == 1
    assert snap["tokens"] == {"prompt": 100, "completion": 50, "total": 150}
    assert snap["histograms"]["turn_latency_ms"]["count"] == 2
    assert snap["histograms"]["turn_latency_ms"]["p50"] is not None
    assert snap["histograms"]["tool_latency_ms"]["count"] == 2


def test_metrics_empty_snapshot():
    snap = Metrics().snapshot()
    assert snap["counters"]["turns_total"] == 0
    assert snap["histograms"]["llm_latency_ms"]["count"] == 0
    assert snap["histograms"]["llm_latency_ms"]["p50"] is None


# ---------------------------------------------------------------------------
# TracedChatModel (span automatici + fallback token)
# ---------------------------------------------------------------------------

def test_traced_chatmodel_spans_and_metrics(monkeypatch):
    tracer = Tracer()
    m = Metrics()
    monkeypatch.setattr(observability, "tracer", tracer)
    monkeypatch.setattr(observability, "metrics", m)

    llm = TracedChatModel(FakeLLM())
    trace = tracer.start_trace("s1", "t1")
    llm.invoke("ciao")
    llm.with_structured_output(Decision).invoke("ciao")
    trace.root.end = time.perf_counter()

    llm_spans = [s for s in trace.spans if s.name == "llm_call"]
    assert len(llm_spans) == 2
    assert llm_spans[0].attributes["call_type"] == "text"
    assert llm_spans[1].attributes["call_type"] == "structured"
    assert llm_spans[1].attributes["schema"] == "Decision"
    assert m.llm_calls_total == 2
    assert turn_summary(trace)["llm_calls"] == 2


def test_traced_chatmodel_error_span(monkeypatch):
    class FailingLLM:
        model = "fake-llm"

        def invoke(self, prompt, **kwargs):
            raise ConnectionError("ollama down")

    tracer = Tracer()
    m = Metrics()
    monkeypatch.setattr(observability, "tracer", tracer)
    monkeypatch.setattr(observability, "metrics", m)

    llm = TracedChatModel(FailingLLM())
    trace = tracer.start_trace("s1", "t1")
    with pytest.raises(ConnectionError):
        llm.invoke("ciao")
    trace.root.end = time.perf_counter()

    llm_spans = [s for s in trace.spans if s.name == "llm_call"]
    assert llm_spans[0].status == "error"
    assert "ollama down" in llm_spans[0].error
    assert m.llm_errors_total == 1
    assert len(turn_summary(trace)["errors"]) == 1


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def test_log_event_writes_jsonl():
    log_event("test_event", session_id="s", turn_id="t", foo="bar")
    lines = observability.LOG_FILE.read_text(encoding="utf-8").splitlines()
    rec = json.loads(lines[-1])
    assert rec["event"] == "test_event"
    assert rec["session_id"] == "s"
    assert rec["foo"] == "bar"


def test_log_event_correlates_with_active_trace(monkeypatch):
    tracer = Tracer()
    monkeypatch.setattr(observability, "tracer", tracer)  # log_event usa il tracer di modulo
    tracer.start_trace("sX", "tX")
    try:
        log_event("tool_call", tool="x")
    finally:
        tracer.end_trace()
    rec = json.loads(observability.LOG_FILE.read_text(encoding="utf-8").splitlines()[-1])
    assert rec["session_id"] == "sX" and rec["turn_id"] == "tX"


# ---------------------------------------------------------------------------
# Integrazione con il nodo call_tool (metriche reali, senza trace)
# ---------------------------------------------------------------------------

# La catena completa (main8 -> graph -> nodes -> tools2 -> rag/Chroma) deve
# essere importabile: su macchine senza l'indice Chroma (o filesystem dove
# SQLite non puo' scrivere) il test si salta, non fallisce (stesso pattern
# di test_agent_dataset.py).
try:
    import main8  # noqa: F401
    _IMPORT_ERROR = None
except Exception as exc:
    main8 = None
    _IMPORT_ERROR = exc


@pytest.mark.skipif(
    main8 is None,
    reason=f"import main8 non riuscito (Chroma non disponibile?): {_IMPORT_ERROR}",
)
def test_tool_error_recorded_in_metrics(agent_client, make_scripted_llm):
    """Un tool che restituisce un errore (macchina inesistente) viene
    registrato nelle metriche globali, anche senza trace attivo."""
    case = {
        "question": "Come sta X-999?",
        "machine": "X-999",
        "expected_tools": ["get_machine_status"],
        "expected_behavior": "answer_only",
        "canned_answer": "X-999 non è in anagrafica.",
    }
    make_scripted_llm(case)
    r = agent_client.post("/chat", json={"message": case["question"]})
    assert r.status_code == 200
    assert observability.metrics.tool_errors_total.get("get_machine_status", 0) >= 1
    assert observability.metrics.tool_calls_total.get("get_machine_status", 0) >= 1


# ---------------------------------------------------------------------------
# TrackedOllamaClient (token: streaming e non, senza doppio conteggio)
# ---------------------------------------------------------------------------

class _FakeChunk:
    def __init__(self, prompt=0, completion=0):
        self.prompt_eval_count = prompt
        self.eval_count = completion


class _FakeRealClient:
    def __init__(self, stream=False):
        self._stream = stream

    def chat(self, *args, **kwargs):
        if self._stream:
            def gen():
                yield _FakeChunk()  # chunk intermedi: senza conteggi
                yield _FakeChunk(prompt=120, completion=35)  # ultimo: i token
            return gen()
        return _FakeChunk(prompt=10, completion=5)

    def embed(self, *args, **kwargs):
        return [0.0] * 8


def test_tracked_client_counts_streaming_tokens(monkeypatch):
    """langchain-ollama usa stream=True: i token sono nell'ultimo chunk e
    finiscono nello span corrente, poi (alla chiusura) nelle metriche globali."""
    tracer = Tracer()
    m = Metrics()
    monkeypatch.setattr(observability, "tracer", tracer)
    monkeypatch.setattr(observability, "metrics", m)

    client = TrackedOllamaClient(_FakeRealClient(stream=True))
    tracer.start_trace("s1", "t1")
    with tracer.span("llm_call") as span:
        chunks = list(client.chat(model="llama3.1", messages=[], stream=True))
    assert len(chunks) == 2  # lo stream è passato in trasparenza
    assert span.tokens == {"prompt": 120, "completion": 35}
    # alla chiusura dello span i token sono nelle metriche globali
    assert m.tokens == {"prompt": 120, "completion": 35}
    tracer.end_trace()


def test_tracked_client_counts_non_streaming_tokens(monkeypatch):
    tracer = Tracer()
    m = Metrics()
    monkeypatch.setattr(observability, "tracer", tracer)
    monkeypatch.setattr(observability, "metrics", m)

    client = TrackedOllamaClient(_FakeRealClient(stream=False))
    tracer.start_trace("s1", "t1")
    with tracer.span("llm_call") as span:
        client.chat(model="llama3.1", messages=[])
    assert span.tokens == {"prompt": 10, "completion": 5}
    assert m.tokens == {"prompt": 10, "completion": 5}
    tracer.end_trace()


def test_tracked_client_no_double_counting(monkeypatch):
    """I token contati dal client (sullo span) non vengono aggiunti di nuovo
    alle metriche globali: una sola volta, alla chiusura dello span."""
    tracer = Tracer()
    m = Metrics()
    monkeypatch.setattr(observability, "tracer", tracer)
    monkeypatch.setattr(observability, "metrics", m)

    client = TrackedOllamaClient(_FakeRealClient(stream=True))
    tracer.start_trace("s1", "t1")
    with tracer.span("llm_call"):
        list(client.chat(model="m", messages=[], stream=True))
    # 120+35 una sola volta (non 2x): il client non chiama metrics.add_tokens
    assert m.tokens == {"prompt": 120, "completion": 35}
    tracer.end_trace()


def test_tracked_client_delegates_unknown_methods(monkeypatch):
    """I metodi non tracciati (es. embed) sono delegati al client reale."""
    client = TrackedOllamaClient(_FakeRealClient())
    assert client.embed("testo") == [0.0] * 8


# ---------------------------------------------------------------------------
# turn_summary (documenti recuperati + errori)
# ---------------------------------------------------------------------------

def test_turn_summary_docs_and_errors():
    tracer = Tracer()
    trace = tracer.start_trace("s1", "t1")
    with tracer.span("tool_call", {"tool": "search_manual"}):
        with tracer.span("rag_retrieval", {"k": 4}) as rag_span:
            rag_span.attributes["docs"] = [{"file": "pump_manual.pdf", "page": 3}]
    with tracer.span("tool_call", {"tool": "get_sensor_data"}) as tspan:
        tspan.set_error("CMMS (404): macchina non trovata")
    trace.root.end = time.perf_counter()

    s = turn_summary(trace)
    assert s["tools_called"] == ["search_manual", "get_sensor_data"]
    assert s["docs_retrieved"] == [{"file": "pump_manual.pdf", "page": 3}]
    assert any("get_sensor_data" in e and "404" in e for e in s["errors"])


# ---------------------------------------------------------------------------
# make_tracked_llm (regressione: langchain-ollama >= 1.1 ignora client=)
# ---------------------------------------------------------------------------

def test_make_tracked_llm_wraps_internal_client():
    """langchain-ollama >= 1.1 non accetta piu' un client custom (il campo
    'client' e' stato rimosso e il parametro e' ignorato in silenzio): il
    client sincrono viene creato internamente in _client. make_tracked_llm
    deve avvolgerlo con il proxy tracciato, altrimenti i token di TUTTE le
    chiamate (testo, strutturato, RAG) andrebbero persi."""
    llm = make_tracked_llm("llama3.1")
    assert isinstance(llm, TracedChatModel)
    assert isinstance(llm._inner._client, TrackedOllamaClient)
