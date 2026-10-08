"""
FASE 10 - Observability: Logging + Tracing + Metrics

Tre strumenti, un'unica fonte di verità per turno:

1. LOGGING  - un evento strutturato per ogni passo: turn_start, llm_call,
              tool_call, rag_retrieval, error, turn_end (con session_id,
              turn_id, durata, token, tool, documenti, errore).
              JSON lines su logs/agent.jsonl + riga umana su console.
2. TRACING  - un trace per turno: albero di span
              chat_turn -> llm_call / tool_call -> rag_retrieval -> llm_call
              con offset, durata, attributi, token ed errori.
              In memoria (endpoint /traces) + export JSON in logs/traces/
              + rendering "waterfall" testuale.
3. METRICS  - contatori e istogrammi in-process (zero dipendenze esterne):
              turni, chiamate LLM, tool per nome, retrieval RAG, errori per
              componente, work order creati, token totali, latenze p50/p95/max.
              Esposte via GET /metrics.

La pipeline osservata è:
    User query -> Agent -> LLM call -> Tool call -> RAG -> LLM -> Response

Per ogni turno si può rispondere a:
    - quanto tempo ha impiegato        (duration_ms del turno e di ogni span)
    - quanti token sono stati usati     (prompt/completion, per chiamata e totali)
    - quale tool è stato chiamato      (span tool_call + metriche per tool)
    - quali documenti sono stati recuperati (span rag_retrieval: file + pagina)
    - dove si è verificato un errore    (span con status=error + evento log)

Principio guida: l'osservabilità NON deve mai rompere l'agente.
Senza un trace attivo gli span sono no-op; i fallimenti di I/O (log, export)
sono protetti e non propagati.
"""

import contextvars
import json
import os
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Percorsi e limiti
# ---------------------------------------------------------------------------

LOG_DIR = Path(os.getenv("OBSERVABILITY_LOG_DIR", "logs"))
LOG_FILE = LOG_DIR / "agent.jsonl"
TRACE_DIR = LOG_DIR / "traces"
_MAX_TRACES_PER_SESSION = int(os.getenv("OBSERVABILITY_MAX_TRACES_PER_SESSION", "50"))
_HIST_CAP = 1000  # valori conservati per istogramma (le percentili restano stabili)

_io_lock = threading.Lock()


# ---------------------------------------------------------------------------
# 1) LOGGING - eventi strutturati
# ---------------------------------------------------------------------------

def log_event(event: str, session_id: Optional[str] = None, turn_id: Optional[str] = None,
              level: str = "info", **fields: Any) -> None:
    """Scrive un evento: JSON line su logs/agent.jsonl + riga umana su console.

    Se session_id/turn_id non sono passati, li recupera dal trace attivo
    (correlazione automatica). Non solleva mai eccezioni."""
    if session_id is None or turn_id is None:
        trace = tracer.current_trace()
        if trace is not None:
            session_id = session_id or trace.session_id
            turn_id = turn_id or trace.turn_id

    record = {
        "ts": round(time.time(), 3),
        "level": level,
        "event": event,
        "session_id": session_id,
        "turn_id": turn_id,
        **fields,
    }
    line = json.dumps(record, ensure_ascii=False, default=str)
    with _io_lock:
        try:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass

    # riga umana compatta (query e args restano solo nel JSON, possono essere lunghi)
    compact = " ".join(f"{k}={v}" for k, v in fields.items() if k not in ("query", "args"))
    print(f"[{level.upper()}][{event}] session={session_id} turn={turn_id} {compact}".rstrip())


# ---------------------------------------------------------------------------
# 2) TRACING - span e trace per turno
# ---------------------------------------------------------------------------

class Span:
    """Un'unità di lavoro misurata dentro un trace (una chiamata LLM, un tool,
    un retrieval RAG). Gli span formano un albero: i figli nascono dentro il
    blocco `with tracer.span(...)` del padre."""

    def __init__(self, name: str, parent_id: Optional[str] = None, attributes: Optional[dict] = None):
        self.span_id = uuid.uuid4().hex[:12]
        self.name = name
        self.parent_id = parent_id
        self.start = time.perf_counter()
        self.end: Optional[float] = None
        self.attributes: Dict[str, Any] = dict(attributes or {})
        self.events: List[dict] = []
        self.status = "ok"
        self.error: Optional[str] = None
        self.children: List["Span"] = []
        self.tokens = {"prompt": 0, "completion": 0}

    def set_error(self, message: str) -> None:
        self.status = "error"
        self.error = message

    def add_event(self, name: str, **fields: Any) -> None:
        self.events.append({"name": name, "ts": round(time.time(), 3), **fields})

    @property
    def duration_ms(self) -> Optional[float]:
        if self.end is None:
            return None
        return round((self.end - self.start) * 1000, 1)


class Trace:
    """L'intero turno: uno span radice 'chat_turn' e l'albero degli span figli."""

    def __init__(self, session_id: str, turn_id: str, attributes: Optional[dict] = None):
        self.session_id = session_id
        self.turn_id = turn_id
        self.started_at = time.time()
        self.root = Span(
            "chat_turn",
            attributes={"session_id": session_id, "turn_id": turn_id, **(attributes or {})},
        )
        self.spans: List[Span] = [self.root]
        self._stack: List[Span] = [self.root]

    @contextmanager
    def span(self, name: str, attributes: Optional[dict] = None):
        span = Span(name, parent_id=self._stack[-1].span_id, attributes=attributes)
        self._stack[-1].children.append(span)
        self.spans.append(span)
        self._stack.append(span)
        try:
            yield span
        except Exception as exc:
            span.set_error(f"{exc.__class__.__name__}: {exc}")
            raise
        finally:
            span.end = time.perf_counter()
            self._stack.pop()
            # I token accumulati sullo span (dal client tracciato) finiscono
            # nelle metriche globali UNA SOLA volta, alla chiusura dello span.
            metrics.add_tokens(span.tokens["prompt"], span.tokens["completion"])

    def current_span(self) -> Span:
        return self._stack[-1]

    def to_dict(self) -> dict:
        """Albero JSON dello span radice (offset rispetto all'inizio del turno)."""
        base = self.root.start

        def walk(span: Span) -> dict:
            return {
                "span_id": span.span_id,
                "name": span.name,
                "offset_ms": round((span.start - base) * 1000, 1),
                "duration_ms": span.duration_ms,
                "status": span.status,
                "error": span.error,
                "attributes": span.attributes,
                "events": span.events,
                "tokens": span.tokens,
                "children": [walk(c) for c in span.children],
            }

        return walk(self.root)


class Tracer:
    """Registra i trace per sessione (in memoria, con tetto) e gestisce lo span
    corrente via contextvar: ogni richiesta HTTP ha il proprio contesto, quindi
    i turni concorrenti non si contaminano."""

    def __init__(self):
        self._traces: Dict[str, List[Trace]] = {}
        self._lock = threading.Lock()
        self._active_trace: "contextvars.ContextVar" = contextvars.ContextVar("active_trace", default=None)

    def start_trace(self, session_id: str, turn_id: str, attributes: Optional[dict] = None) -> Trace:
        trace = Trace(session_id, turn_id, attributes)
        with self._lock:
            traces = self._traces.setdefault(session_id, [])
            traces.append(trace)
            if len(traces) > _MAX_TRACES_PER_SESSION:
                del traces[: len(traces) - _MAX_TRACES_PER_SESSION]
        self._active_trace.set(trace)
        return trace

    def end_trace(self) -> Optional[Trace]:
        trace = self._active_trace.get()
        self._active_trace.set(None)
        if trace is not None:
            trace.root.end = time.perf_counter()
            metrics.add_tokens(trace.root.tokens["prompt"], trace.root.tokens["completion"])
        return trace

    def current_trace(self) -> Optional[Trace]:
        return self._active_trace.get()

    def current_span(self) -> Optional[Span]:
        trace = self._active_trace.get()
        return trace.current_span() if trace else None

    @contextmanager
    def span(self, name: str, attributes: Optional[dict] = None):
        """Crea uno span figlio dello span corrente. No-op (yield None) se non c'è
        un trace attivo: test e script senza osservabilità girano indisturbati."""
        trace = self._active_trace.get()
        if trace is None:
            yield None
            return
        with trace.span(name, attributes) as span:
            yield span

    def session_traces(self, session_id: str) -> List[Trace]:
        with self._lock:
            return list(self._traces.get(session_id, []))

    def save_trace(self, trace: Trace) -> Optional[Path]:
        """Export JSON del trace in logs/traces/ (per ispezione a posteriori)."""
        try:
            TRACE_DIR.mkdir(parents=True, exist_ok=True)
            path = TRACE_DIR / f"{trace.session_id}_{trace.turn_id}.json"
            with _io_lock:
                path.write_text(
                    json.dumps(trace.to_dict(), ensure_ascii=False, indent=2, default=str),
                    encoding="utf-8",
                )
            return path
        except OSError:
            return None


# ---------------------------------------------------------------------------
# 3) METRICS - contatori e istogrammi in-process
# ---------------------------------------------------------------------------

class Metrics:
    """Contatori e istogrammi in-process (zero dipendenze esterne).

    In produzione si esporterebbero a Prometheus/Grafana; qui il GET /metrics
    restituisce lo snapshot JSON con gli stessi dati."""

    def __init__(self):
        self._lock = threading.Lock()
        self.turns_total = 0
        self.turns_with_errors = 0
        self.llm_calls_total = 0
        self.llm_errors_total = 0
        self.rag_retrievals_total = 0
        self.work_orders_created_total = 0
        self.tool_calls_total: Dict[str, int] = {}
        self.tool_errors_total: Dict[str, int] = {}
        self.errors_total: Dict[str, int] = {}
        self.tokens = {"prompt": 0, "completion": 0}
        self.histograms: Dict[str, List[float]] = {
            "turn_latency_ms": [],
            "llm_latency_ms": [],
            "tool_latency_ms": [],
            "rag_latency_ms": [],
        }

    # --- registrazioni --------------------------------------------------------

    def record_turn(self, duration_ms: float, error: bool = False) -> None:
        with self._lock:
            self.turns_total += 1
            self._add_hist("turn_latency_ms", duration_ms)
            if error:
                self.turns_with_errors += 1

    def record_llm(self, duration_ms: float, error: bool = False) -> None:
        with self._lock:
            self.llm_calls_total += 1
            self._add_hist("llm_latency_ms", duration_ms)
            if error:
                self.llm_errors_total += 1

    def record_tool(self, tool: str, duration_ms: float, error: bool = False) -> None:
        with self._lock:
            self.tool_calls_total[tool] = self.tool_calls_total.get(tool, 0) + 1
            self._add_hist("tool_latency_ms", duration_ms)
            if error:
                self.tool_errors_total[tool] = self.tool_errors_total.get(tool, 0) + 1

    def record_rag(self, duration_ms: float) -> None:
        with self._lock:
            self.rag_retrievals_total += 1
            self._add_hist("rag_latency_ms", duration_ms)

    def record_work_order(self) -> None:
        with self._lock:
            self.work_orders_created_total += 1

    def record_error(self, component: str) -> None:
        with self._lock:
            self.errors_total[component] = self.errors_total.get(component, 0) + 1

    def add_tokens(self, prompt_tokens: int, completion_tokens: int) -> None:
        with self._lock:
            self.tokens["prompt"] += prompt_tokens
            self.tokens["completion"] += completion_tokens

    def _add_hist(self, name: str, value: float) -> None:
        values = self.histograms[name]
        values.append(value)
        if len(values) > _HIST_CAP:
            del values[: len(values) - _HIST_CAP]

    # --- snapshot ---------------------------------------------------------------

    @staticmethod
    def _percentiles(values: List[float]) -> dict:
        if not values:
            return {"count": 0, "p50": None, "p95": None, "max": None, "avg": None}
        s = sorted(values)

        def pct(p: float) -> float:
            k = (len(s) - 1) * p / 100
            f, c = int(k), min(int(k) + 1, len(s) - 1)
            return round(s[f] + (s[c] - s[f]) * (k - f), 1)

        return {
            "count": len(s),
            "p50": pct(50),
            "p95": pct(95),
            "max": round(s[-1], 1),
            "avg": round(sum(s) / len(s), 1),
        }

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "counters": {
                    "turns_total": self.turns_total,
                    "turns_with_errors": self.turns_with_errors,
                    "llm_calls_total": self.llm_calls_total,
                    "llm_errors_total": self.llm_errors_total,
                    "rag_retrievals_total": self.rag_retrievals_total,
                    "work_orders_created_total": self.work_orders_created_total,
                    "tool_calls_total": dict(self.tool_calls_total),
                    "tool_errors_total": dict(self.tool_errors_total),
                    "errors_total": dict(self.errors_total),
                },
                "tokens": {
                    "prompt": self.tokens["prompt"],
                    "completion": self.tokens["completion"],
                    "total": self.tokens["prompt"] + self.tokens["completion"],
                },
                "histograms": {name: self._percentiles(values) for name, values in self.histograms.items()},
            }


# ---------------------------------------------------------------------------
# 4) LLM wrapper: span automatici + conteggio token
# ---------------------------------------------------------------------------

class TrackedOllamaClient:
    """Proxy di ollama.Client: registra i token di ogni chiamata chat
    (prompt_eval_count/eval_count) nello span corrente.

    I token globali NON vengono aggiornati qui ma alla chiusura dello span
    (vedi Trace.span / Tracer.end_trace): così ogni chiamata è contata una
    sola volta, anche quando span e metriche vengono aggiornati in momenti
    diversi.

    Nota: langchain-ollama chiama il client con stream=True (anche per le
    chiamate "non streaming"): i token sono nell'ultimo chunk (done=True),
    quindi lo stream viene passato in trasparenza e i token si leggono a
    fine iterazione, nel contesto di chi lo consuma (lo span giusto).

    È la stessa fonte usata dall'evaluator della Fase 9: è l'unica affidabile
    anche per l'output strutturato, che perde l'usage_metadata sul risultato
    Pydantic."""

    def __init__(self, real_client: Optional[Any] = None):
        import ollama

        if real_client is None:
            # FASE 11: se non viene passato un client, rispetta OLLAMA_BASE_URL
            # (nel container l'Ollama dell'host e' a host.docker.internal:11434).
            host = os.getenv("OLLAMA_BASE_URL")
            self._real = ollama.Client(host=host) if host else ollama.Client()
        else:
            self._real = real_client

    def chat(self, *args, **kwargs):
        response = self._real.chat(*args, **kwargs)
        if kwargs.get("stream"):
            return self._tracked_stream(response)
        self._record_tokens(response)
        return response

    def _tracked_stream(self, stream):
        """Passa i chunk in trasparenza; a fine iterazione legge i token
        dall'ultimo chunk (Ollama li mette sul chunk con done=True)."""
        last = None
        for chunk in stream:
            last = chunk
            yield chunk
        self._record_tokens(last)

    def _record_tokens(self, response: Any) -> None:
        if response is None:
            return
        prompt_tokens = int(getattr(response, "prompt_eval_count", 0) or 0)
        completion_tokens = int(getattr(response, "eval_count", 0) or 0)
        span = tracer.current_span()
        if span is not None:
            span.tokens["prompt"] += prompt_tokens
            span.tokens["completion"] += completion_tokens

    def __getattr__(self, name):
        return getattr(self._real, name)


class TracedChatModel:
    """Proxy di un ChatModel: per ogni invocazione (testo o output strutturato)
    crea uno span 'llm_call' con modello, tipo di chiamata, latenza, token ed
    eventuali errori.

    I token arrivano dal TrackedOllamaClient (che li attribuisce allo span
    corrente); se l'LLM non usa il client tracciato, si fa il fallback
    sull'usage_metadata della risposta."""

    def __init__(self, inner: Any):
        self._inner = inner
        self._model = getattr(inner, "model", "unknown")

    def invoke(self, prompt, **kwargs):
        with tracer.span("llm_call", {"model": self._model, "call_type": "text"}) as span:
            t0 = time.perf_counter()
            try:
                response = self._inner.invoke(prompt, **kwargs)
            except Exception as exc:
                if span is not None:
                    span.set_error(f"{exc.__class__.__name__}: {exc}")
                metrics.record_llm((time.perf_counter() - t0) * 1000, error=True)
                log_event("llm_call", level="error", model=self._model, call_type="text",
                          error=f"{exc.__class__.__name__}: {exc}")
                raise
            duration_ms = (time.perf_counter() - t0) * 1000

            # Fallback: LLM senza client tracciato -> uso l'usage_metadata.
            # Se c'è uno span attivo i token restano sullo span (le metriche
            # globali vengono aggiornate alla chiusura dello span, punto 2):
            # qui aggiorno le metriche solo se NON c'è span, per non contare
            # due volte.
            p_tok = c_tok = 0
            if span is None or (span.tokens["prompt"] == 0 and span.tokens["completion"] == 0):
                usage = getattr(response, "usage_metadata", None) or {}
                p_tok = int(usage.get("input_tokens", 0) or 0)
                c_tok = int(usage.get("output_tokens", 0) or 0)
                if span is not None:
                    span.tokens = {"prompt": p_tok, "completion": c_tok}
                else:
                    metrics.add_tokens(p_tok, c_tok)

            metrics.record_llm(duration_ms)
            log_event(
                "llm_call",
                model=self._model,
                call_type="text",
                duration_ms=round(duration_ms, 1),
                prompt_tokens=span.tokens["prompt"] if span else p_tok,
                completion_tokens=span.tokens["completion"] if span else c_tok,
            )
            return response

    def with_structured_output(self, schema):
        inner = self._inner.with_structured_output(schema)
        schema_name = getattr(schema, "__name__", str(schema))
        model_name = self._model

        class _TracedStructured:
            def invoke(self, prompt, **kwargs):
                with tracer.span("llm_call",
                                 {"model": model_name, "call_type": "structured", "schema": schema_name}) as span:
                    t0 = time.perf_counter()
                    try:
                        result = inner.invoke(prompt, **kwargs)
                    except Exception as exc:
                        if span is not None:
                            span.set_error(f"{exc.__class__.__name__}: {exc}")
                        metrics.record_llm((time.perf_counter() - t0) * 1000, error=True)
                        log_event("llm_call", level="error", model=model_name, call_type="structured",
                                  schema=schema_name, error=f"{exc.__class__.__name__}: {exc}")
                        raise
                    duration_ms = (time.perf_counter() - t0) * 1000
                    metrics.record_llm(duration_ms)
                    log_event(
                        "llm_call",
                        model=model_name,
                        call_type="structured",
                        schema=schema_name,
                        duration_ms=round(duration_ms, 1),
                        prompt_tokens=span.tokens["prompt"] if span else 0,
                        completion_tokens=span.tokens["completion"] if span else 0,
                    )
                    return result

        return _TracedStructured()

    def __getattr__(self, name):
        return getattr(self._inner, name)


# ---------------------------------------------------------------------------
# Istanze condivise (singleton di modulo)
# ---------------------------------------------------------------------------

tracer = Tracer()
metrics = Metrics()

_tracked_client: Optional[TrackedOllamaClient] = None


def tracked_ollama_client() -> TrackedOllamaClient:
    """Il client Ollama condiviso: traccia i token di TUTTE le chiamate LLM
    (agente + RAG). Creato una volta, riutilizzato da nodes.py e rag.py."""
    global _tracked_client
    if _tracked_client is None:
        _tracked_client = TrackedOllamaClient()
    return _tracked_client


def make_tracked_llm(model: str):
    """ChatOllama pronto all'uso: tracciato (span + token).

    Usato da nodes.py per il LLM dell'agente.

    Nota: langchain-ollama >= 1.1 non accetta piu' un client custom (il campo
    'client' e' stato rimosso: il client sincrono viene creato internamente in
    _client). Lo avvolgiamo quindi con il proxy tracciato DOPO la
    costruzione: tutte le chiamate chat (testo, output strutturato) passano
    dal proxy e i token finiscono nello span corrente e nelle metriche
    globali."""
    from langchain_ollama import ChatOllama

    # FASE 11: Ollama potrebbe non essere su localhost (nel container e'
    # l'Ollama dell'host, a host.docker.internal:11434).
    base_url = os.getenv("OLLAMA_BASE_URL")
    llm = ChatOllama(model=model, temperature=0, base_url=base_url)
    llm._client = TrackedOllamaClient(llm._client)
    return TracedChatModel(llm)


# ---------------------------------------------------------------------------
# Riepilogo del turno (per la risposta dell'API) e waterfall testuale
# ---------------------------------------------------------------------------

def turn_summary(trace: Optional[Trace]) -> dict:
    """Le risposte alle domande della Fase 10, per un turno:
    quanto tempo, quanti token, quali tool, quali documenti, dove gli errori."""
    if trace is None:
        return {}

    llm_spans = [s for s in trace.spans if s.name == "llm_call"]
    tool_spans = [s for s in trace.spans if s.name == "tool_call"]
    rag_spans = [s for s in trace.spans if s.name == "rag_retrieval"]

    docs: List[dict] = []
    for s in rag_spans:
        for doc in s.attributes.get("docs", []):
            if doc not in docs:
                docs.append(doc)

    # Gli errori indicano DOVE sono accaduti: per i tool il nome del tool,
    # per le chiamate LLM il modello (es. "tool_call(get_sensor_data): CMMS (404): ...").
    errors = []
    for s in trace.spans:
        if s.status == "error":
            entry = f"{s.name}: {s.error}"
            where = s.attributes.get("tool") or s.attributes.get("model")
            if where:
                entry = f"{s.name}({where}): {s.error}"
            errors.append(entry)

    tokens = {"prompt": 0, "completion": 0}
    for s in llm_spans:
        tokens["prompt"] += s.tokens["prompt"]
        tokens["completion"] += s.tokens["completion"]

    return {
        "turn_id": trace.turn_id,
        "duration_ms": trace.root.duration_ms,
        "llm_calls": len(llm_spans),
        "tokens": {
            "prompt": tokens["prompt"],
            "completion": tokens["completion"],
            "total": tokens["prompt"] + tokens["completion"],
        },
        "tools_called": [s.attributes.get("tool") for s in tool_spans],
        "docs_retrieved": docs,
        "errors": errors,
    }


def waterfall(trace: Trace) -> str:
    """Rendering testuale del trace: un albero con offset e durate,
    come un mini 'flame graph' da terminale."""
    lines: List[str] = []

    def walk(span: Span, depth: int) -> None:
        offset = (span.start - trace.root.start) * 1000
        a = span.attributes
        extra = ""
        if span.name == "llm_call":
            extra = f" model={a.get('model')} {a.get('call_type')}"
            if a.get("schema"):
                extra += f"({a['schema']})"
            extra += f" tokens={span.tokens['prompt']}+{span.tokens['completion']}"
        elif span.name == "tool_call":
            extra = f" tool={a.get('tool')} args={a.get('args')}"
        elif span.name == "rag_retrieval":
            extra = f" k={a.get('k')} docs={len(a.get('docs', []))}"
        status = "" if span.status == "ok" else f"  ERROR: {span.error}"
        lines.append(f"{'  ' * depth}{offset:8.0f}ms  {span.name} {extra} [{span.duration_ms}ms]{status}")
        for child in span.children:
            walk(child, depth + 1)

    walk(trace.root, 0)
    return "\n".join(lines)
