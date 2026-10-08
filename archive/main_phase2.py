"""
FASE 2 - Chatbot con RAG
User -> FastAPI -> Retriever -> LLM -> Response (+ fonti)
"""

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from rag import RagPipeline

app = FastAPI(title="Industrial Chatbot - Fase 2 (RAG)")

rag = RagPipeline()  # carica vector DB già costruito con ingest.py


class ChatRequest(BaseModel):
    message: str


class Source(BaseModel):
    file: str
    page: Optional[int] = None


class ChatResponse(BaseModel):
    answer: str
    sources: List[Source]


@app.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest):
    if not request.message.strip():
        raise HTTPException(status_code=400, detail="Il campo 'message' non può essere vuoto.")

    answer, sources = rag.answer(request.message)
    return ChatResponse(answer=answer, sources=sources)


@app.get("/health")
def health():
    return {"status": "ok"}
