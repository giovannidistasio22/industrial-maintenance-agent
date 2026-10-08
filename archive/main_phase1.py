"""
FASE 1 - Chatbot base
User -> FastAPI -> LLM (Ollama) -> Response
"""

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from langchain_ollama import ChatOllama

app = FastAPI(title="Industrial Chatbot - Fase 1")

# Modello Ollama locale. Deve essere già scaricato con `ollama pull llama3.1`
llm = ChatOllama(model="llama3.1", temperature=0)


class ChatRequest(BaseModel):
    message: str


class ChatResponse(BaseModel):
    answer: str


@app.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest):
    if not request.message.strip():
        raise HTTPException(status_code=400, detail="Il campo 'message' non può essere vuoto.")

    response = llm.invoke(request.message)
    return ChatResponse(answer=response.content)


@app.get("/health")
def health():
    return {"status": "ok"}
