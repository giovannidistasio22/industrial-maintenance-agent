"""
FASE 4 - Chatbot con workflow LangGraph esplicito
User -> FastAPI -> Graph (analyze -> tool/RAG loop -> evaluate -> answer) -> Response
"""

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Dict, Any, Optional
from graph import run_graph

app = FastAPI(title="Industrial Maintenance Agent - Fase 4 (LangGraph)")


class ChatRequest(BaseModel):
    message: str


class ToolCall(BaseModel):
    tool: str
    args: Dict[str, Any]
    result: Any


class TraceStep(BaseModel):
    node: str
    reasoning: Optional[str] = None


class ChatResponse(BaseModel):
    answer: str
    gathered_data: List[ToolCall]
    trace: List[Dict[str, Any]]


@app.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest):
    if not request.message.strip():
        raise HTTPException(status_code=400, detail="Il campo 'message' non può essere vuoto.")

    final_state = run_graph(request.message)

    return ChatResponse(
        answer=final_state.get("final_answer", ""),
        gathered_data=final_state.get("gathered_data", []),
        trace=final_state.get("trace", []),
    )


@app.get("/health")
def health():
    return {"status": "ok"}
