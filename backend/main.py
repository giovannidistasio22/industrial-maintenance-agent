"""
API dell'agente di manutenzione industriale (FastAPI).

Ogni POST /chat genera un trace (albero di span) e un riepilogo
'observability' nella risposta: durata, token, tool chiamati, documenti
recuperati, errori.

  - POST   /chat                    -> turno dell'agente (con HITL)
  - GET    /traces/{session_id}     -> i trace della sessione (JSON + waterfall)
  - GET    /metrics                 -> snapshot delle metriche in-process
  - GET    /sessions/{id}/history   -> cronologia della conversazione
  - DELETE /sessions/{id}          -> reset della sessione
  - GET    /health                  -> liveness (+ stato del CMMS)

Avvio (dalla radice del repo, con il CMMS su 8010 e Ollama già attivi):
  uvicorn backend.main:app --reload --port 8003
"""

import time
import uuid
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from backend.api.schemas import ChatRequest, ChatResponse
from backend.agent.graph import (
    run_graph, get_history, get_active_machine, reset_session,
    get_pending_action, resume_confirmation, cancel_pending_confirmation,
)
from backend.agent.nodes import interpret_confirmation
from backend.services.cmms_client import cmms_is_up
from backend.services.observability import tracer, metrics, log_event, turn_summary, waterfall

app = FastAPI(title="Industrial Maintenance Agent")

# CORS per il frontend TypeScript (frontend/). In dev il proxy di Vite
# evita il problema; serve quando l'app buildata (frontend/dist) e' servita
# da un'origine diversa (es. 'vite preview' o un server statico).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest):
    if not request.message.strip():
        raise HTTPException(status_code=400, detail="Il campo 'message' non può essere vuoto.")

    session_id = request.session_id or str(uuid.uuid4())
    turn_id = str(uuid.uuid4())

    # FASE 10: apre il trace del turno (span radice 'chat_turn'); tutti gli span
    # creati dentro (llm_call, tool_call, rag_retrieval) diventano figli.
    tracer.start_trace(session_id, turn_id, {"query": request.message})
    log_event("turn_start", session_id=session_id, turn_id=turn_id, query=request.message)

    t0 = time.perf_counter()
    state, note, error = None, "", None
    try:
        pending = get_pending_action(session_id)

        if pending is None:
            # Nessuna conferma in sospeso: turno normale.
            state = run_graph(request.message, session_id)
        else:
            # C'è una proposta in attesa di conferma: interpretiamo la risposta
            # PRIMA di decidere come procedere (mai assumere "sì" per default).
            interpretation = interpret_confirmation(request.message, pending)

            if interpretation.intent == "confirm":
                state = resume_confirmation(True, session_id, request.message)
            elif interpretation.intent == "deny":
                state = resume_confirmation(False, session_id, request.message)
            else:
                # "unrelated": l'utente non ha risposto sì/no. Fail-safe:
                # annulliamo la proposta pendente (NON la eseguiamo) e
                # trattiamo il messaggio come l'inizio di un turno normale.
                cancel_pending_confirmation(session_id)
                state = run_graph(request.message, session_id)
                note = "*(Ho annullato la proposta di work order precedente, dato che non hai risposto sì/no.)*"
    except Exception as exc:
        # FASE 10: l'errore viene registrato (metriche + log) e il trace
        # restituito dal finally lo mostrerà con status=error.
        error = exc
        metrics.record_error("chat")
        log_event("error", session_id=session_id, turn_id=turn_id, level="error",
                  component="chat", error=f"{exc.__class__.__name__}: {exc}")
    finally:
        # FASE 10: chiude il trace, registra la metrica del turno e salva
        # l'export JSON (per ispezione a posteriori).
        duration_ms = (time.perf_counter() - t0) * 1000
        metrics.record_turn(duration_ms, error=error is not None)
        finished = tracer.end_trace()
        summary = turn_summary(finished)
        if finished:
            path = tracer.save_trace(finished)
            if path:
                summary["trace_file"] = str(path)
        log_event("turn_end", session_id=session_id, turn_id=turn_id,
                  duration_ms=round(duration_ms, 1), error=error is not None,
                  llm_calls=summary.get("llm_calls", 0),
                  total_tokens=summary.get("tokens", {}).get("total", 0),
                  n_errors=len(summary.get("errors", [])))

    if error is not None:
        raise HTTPException(status_code=500, detail=f"Errore durante l'esecuzione del turno: {error}")

    answer = state.get("final_answer", "")
    if note:
        answer = f"{note}\n\n{answer}" if answer else note

    return ChatResponse(
        session_id=session_id,
        turn_id=turn_id,
        answer=answer,
        active_machine=state.get("active_machine"),
        confirmation_required=bool(state.get("__interrupt__")),
        pending_action=state.get("pending_action"),
        gathered_data=state.get("gathered_data", []),
        trace=state.get("trace", []),
        observability=summary,
    )


@app.get("/traces/{session_id}")
def session_traces(session_id: str):
    """I trace della sessione: albero di span JSON + riepilogo + waterfall."""
    traces = tracer.session_traces(session_id)
    if not traces:
        raise HTTPException(status_code=404, detail="Nessun trace per questa sessione.")
    return {
        "session_id": session_id,
        "traces": [
            {
                "turn_id": t.turn_id,
                "started_at": t.started_at,
                "duration_ms": t.root.duration_ms,
                "summary": turn_summary(t),
                "spans": t.to_dict(),
                "waterfall": waterfall(t),
            }
            for t in traces
        ],
    }


@app.get("/metrics")
def get_metrics():
    """Snapshot delle metriche in-process (contatori, token, istogrammi p50/p95)."""
    return metrics.snapshot()


@app.get("/sessions/{session_id}/history")
def history(session_id: str):
    messages = get_history(session_id)
    if not messages:
        raise HTTPException(status_code=404, detail="Sessione non trovata o vuota.")
    return {
        "session_id": session_id,
        "active_machine": get_active_machine(session_id),
        "pending_action": get_pending_action(session_id),
        "messages": messages,
    }


@app.delete("/sessions/{session_id}")
def delete_session(session_id: str):
    if not reset_session(session_id):
        raise HTTPException(status_code=501, detail="Il checkpointer in uso non supporta la cancellazione.")
    return {"session_id": session_id, "deleted": True}


@app.get("/health")
def health():
    cmms = cmms_is_up()
    return {"status": "ok" if cmms else "degraded", "cmms": "up" if cmms else "down"}
