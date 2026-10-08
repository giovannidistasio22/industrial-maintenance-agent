"""
FASE 3 - Chatbot con Tool Calling (Agent)
User -> FastAPI -> Agent (LLM + tools CMMS + RAG) -> Answer
"""

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Any
from agent import run_agent

app = FastAPI(title="Industrial Maintenance Agent - Fase 3")


class ChatRequest(BaseModel):
    message: str


class ToolStep(BaseModel):
    tool: str
    args: dict[str, Any]


class ChatResponse(BaseModel):
    answer: str
    steps: List[ToolStep]


@app.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest):
    if not request.message.strip():
        raise HTTPException(status_code=400, detail="Il campo 'message' non può essere vuoto.")

    answer, steps = run_agent(request.message)
    return ChatResponse(answer=answer, steps=steps)


@app.get("/health")
def health():
    return {"status": "ok"}
