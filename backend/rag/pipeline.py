"""
RAG core
Retriever -> documenti rilevanti -> LLM (con contesto e citazione fonti)

FASE 10: il retrieval e la chiamata LLM sono tracciati (span 'rag_retrieval'
e 'llm_call') e i token della risposta RAG sono contati dal client Ollama
condiviso, così l'osservabilità vede anche la pipeline RAG.
"""

import os
import time

from langchain_ollama import ChatOllama, OllamaEmbeddings
from langchain_chroma import Chroma
from langchain_core.prompts import ChatPromptTemplate

from backend.services.observability import tracer, metrics, TrackedOllamaClient, log_event

PERSIST_DIR = "chroma_db"
EMBEDDING_MODEL = "nomic-embed-text"
LLM_MODEL = "llama3.1"
K = 4  # numero di chunk recuperati dal retriever

# FASE 11 (Docker): endpoint configurabili via variabili d'ambiente.
# - OLLAMA_BASE_URL: server Ollama (default: localhost:11434; nel container
#   l'Ollama dell'host e' raggiungibile a http://host.docker.internal:11434)
# - CHROMA_HOST/CHROMA_PORT: se CHROMA_HOST e' impostato si usa un server
#   Chroma REMOTO (il servizio 'chroma' di docker-compose) invece della
#   directory persistente locale.
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL")
CHROMA_HOST = os.getenv("CHROMA_HOST")
CHROMA_PORT = int(os.getenv("CHROMA_PORT", "8000"))

PROMPT_TEMPLATE = """Sei un assistente tecnico per manutenzione industriale.
Rispondi alla domanda dell'utente usando SOLO le informazioni contenute nel contesto qui sotto.
Se il contesto non contiene informazioni sufficienti, dillo chiaramente invece di inventare.

Contesto:
{context}

Domanda: {question}

Risposta:"""


class RagPipeline:
    def __init__(self):
        embeddings = OllamaEmbeddings(model=EMBEDDING_MODEL, base_url=OLLAMA_BASE_URL)
        if CHROMA_HOST:
            # FASE 11: server Chroma remoto (il servizio 'chroma' di docker-compose)
            self.vectorstore = Chroma(
                host=CHROMA_HOST,
                port=CHROMA_PORT,
                embedding_function=embeddings,
            )
        else:
            # Default (sviluppo locale): Chroma persistente in cartella locale
            self.vectorstore = Chroma(
                persist_directory=PERSIST_DIR,
                embedding_function=embeddings,
            )
        self.retriever = self.vectorstore.as_retriever(search_kwargs={"k": K})
        # FASE 10: client tracciato -> i token della chiamata RAG finiscono
        # nello span corrente e nelle metriche globali.
        # (langchain-ollama >= 1.1 non accetta un client custom: si avvolge
        # il client interno _client con il proxy tracciato dopo la
        # costruzione.)
        llm = ChatOllama(model=LLM_MODEL, temperature=0, base_url=OLLAMA_BASE_URL)
        llm._client = TrackedOllamaClient(llm._client)
        self.llm = llm
        self.prompt = ChatPromptTemplate.from_template(PROMPT_TEMPLATE)

    def answer(self, question: str):
        # FASE 10: span 'rag_retrieval' — quali documenti sono stati recuperati
        with tracer.span("rag_retrieval", {"query": question, "k": K}) as span:
            t0 = time.perf_counter()
            docs = self.retriever.invoke(question)

            sources = []
            seen = set()
            for d in docs:
                src = d.metadata.get("source", "unknown")
                page = d.metadata.get("page", None)
                key = (src, page)
                if key not in seen:
                    seen.add(key)
                    sources.append({"file": src, "page": page})

            if span is not None:
                span.attributes["docs"] = sources
            metrics.record_rag((time.perf_counter() - t0) * 1000)
            log_event("rag_retrieval", query=question, k=K, docs=sources)

        context = "\n\n---\n\n".join(d.page_content for d in docs)

        # FASE 10: span 'llm_call' per la risposta RAG (i token arrivano dal
        # client tracciato, che li attribuisce allo span corrente). Latenza e
        # errori finiscono nelle metriche, come per le chiamate dell'agente.
        with tracer.span("llm_call", {"model": LLM_MODEL, "call_type": "rag_answer"}) as span:
            t0 = time.perf_counter()
            try:
                chain = self.prompt | self.llm
                response = chain.invoke({"context": context, "question": question})
            except Exception as exc:
                if span is not None:
                    span.set_error(f"{exc.__class__.__name__}: {exc}")
                metrics.record_llm((time.perf_counter() - t0) * 1000, error=True)
                log_event("llm_call", level="error", model=LLM_MODEL, call_type="rag_answer",
                          error=f"{exc.__class__.__name__}: {exc}")
                raise
            duration_ms = (time.perf_counter() - t0) * 1000
            metrics.record_llm(duration_ms)
            log_event("llm_call", model=LLM_MODEL, call_type="rag_answer",
                      duration_ms=round(duration_ms, 1),
                      prompt_tokens=span.tokens["prompt"] if span else 0,
                      completion_tokens=span.tokens["completion"] if span else 0)

        return response.content, sources
