"""
FASE 7 - Agente con Human in the Loop
User (session_id) -> FastAPI -> Graph (READ autonomo, WRITE con conferma) -> Response

Logica di /chat, ad ogni messaggio in arrivo:
  1. C'è una conferma pendente per questa sessione (il grafo è fermo su
     request_confirmation)?
     - NO  -> turno normale (run_graph).
     - SÌ  -> interpreta il messaggio (interpret_confirmation):
         - "confirm"   -> resume(True):  esegue l'azione (create_work_order)
         - "deny"      -> resume(False): annulla, nessuna azione eseguita
         - "unrelated" -> annulla in sicurezza (fail-safe) E processa il
                          messaggio come un turno normale nuovo, così l'utente
                          non resta "bloccato" se cambia argomento
  2. Se il turno risultante propone una NUOVA azione, la risposta lo segnala
     con confirmation_required=True e pending_action.
"""

import uuid
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Dict, Any, Optional
from graph import (
    run_graph, get_history, get_active_machine, reset_session,
    get_pending_action, resume_confirmation, cancel_pending_confirmation,
)
from nodes import interpret_confirmation
from cmms_client import cmms_is_up

app = FastAPI(title="Industrial Maintenance Agent - Fase 7 (Human in the Loop)")


class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None


class ToolCall(BaseModel):
    tool: str
    args: Dict[str, Any]
    result: Any


class ChatResponse(BaseModel):
    session_id: str
    answer: str
    active_machine: Optional[str] = None
    confirmation_required: bool = False
    pending_action: Optional[Dict[str, Any]] = None
    gathered_data: List[ToolCall] = []
    trace: List[Dict[str, Any]] = []


def _response(session_id: str, state: dict, note: str = "") -> ChatResponse:
    answer = state.get("final_answer", "")
    if note:
        answer = f"{note}\n\n{answer}" if answer else note
    return ChatResponse(
        session_id=session_id,
        answer=answer,
        active_machine=state.get("active_machine"),
        confirmation_required=bool(state.get("__interrupt__")),
        pending_action=state.get("pending_action"),
        gathered_data=state.get("gathered_data", []),
        trace=state.get("trace", []),
    )


@app.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest):
    if not request.message.strip():
        raise HTTPException(status_code=400, detail="Il campo 'message' non può essere vuoto.")

    session_id = request.session_id or str(uuid.uuid4())
    pending = get_pending_action(session_id)

    if pending is None:
        # Nessuna conferma in sospeso: turno normale.
        state = run_graph(request.message, session_id)
        return _response(session_id, state)

    # C'è una proposta in attesa di conferma: interpretiamo la risposta PRIMA
    # di decidere come procedere (mai assumere "sì" per default).
    interpretation = interpret_confirmation(request.message, pending)

    if interpretation.intent == "confirm":
        state = resume_confirmation(True, session_id, request.message)
        return _response(session_id, state)

    if interpretation.intent == "deny":
        state = resume_confirmation(False, session_id, request.message)
        return _response(session_id, state)

    # "unrelated": l'utente non ha risposto sì/no, ha detto altro. Fail-safe:
    # annulliamo la proposta pendente (NON la eseguiamo "per sicurezza") e
    # trattiamo il messaggio come l'inizio di un turno normale, così la
    # conversazione prosegue invece di restare bloccata sulla domanda ignorata.
    cancel_pending_confirmation(session_id)
    state = run_graph(request.message, session_id)
    note = "*(Ho annullato la proposta di work order precedente, dato che non hai risposto sì/no.)*"
    return _response(session_id, state, note=note)


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
