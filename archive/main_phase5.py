"""
FASE 5 - Chatbot con memoria conversazionale
User (session_id) -> FastAPI -> Graph con checkpointer -> Response

Il client deve rimandare lo stesso session_id ad ogni messaggio della stessa
conversazione. Se non lo manda, il server ne genera uno nuovo e lo restituisce.
"""

import uuid
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Dict, Any, Optional
from graph import run_graph, get_history, get_active_machine, reset_session

app = FastAPI(title="Industrial Maintenance Agent - Fase 5 (Memoria)")


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
    standalone_query: str
    gathered_data: List[ToolCall]
    trace: List[Dict[str, Any]]


@app.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest):
    if not request.message.strip():
        raise HTTPException(status_code=400, detail="Il campo 'message' non può essere vuoto.")

    session_id = request.session_id or str(uuid.uuid4())
    state = run_graph(request.message, session_id)

    return ChatResponse(
        session_id=session_id,
        answer=state.get("final_answer", ""),
        active_machine=state.get("active_machine"),
        standalone_query=state.get("standalone_query", request.message),
        gathered_data=state.get("gathered_data", []),
        trace=state.get("trace", []),
    )


@app.get("/sessions/{session_id}/history")
def history(session_id: str):
    messages = get_history(session_id)
    if not messages:
        raise HTTPException(status_code=404, detail="Sessione non trovata o vuota.")
    return {
        "session_id": session_id,
        "active_machine": get_active_machine(session_id),
        "messages": messages,
    }


@app.delete("/sessions/{session_id}")
def delete_session(session_id: str):
    if not reset_session(session_id):
        raise HTTPException(status_code=501, detail="Il checkpointer in uso non supporta la cancellazione.")
    return {"session_id": session_id, "deleted": True}


@app.get("/health")
def health():
    return {"status": "ok"}
