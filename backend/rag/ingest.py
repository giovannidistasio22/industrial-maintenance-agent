"""
Ingestion pipeline
PDF -> Text extraction -> Chunking -> Embeddings -> Vector DB (Chroma)

Esegui questo script UNA VOLTA (o ogni volta che aggiungi/modifichi documenti)
per costruire/aggiornare il database vettoriale.
"""

import os
from pathlib import Path

from langchain_community.document_loaders import PyPDFLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_ollama import OllamaEmbeddings
from langchain_chroma import Chroma

# FASE 11 (Docker): percorsi ed endpoint configurabili via variabili
# d'ambiente. I default restano quelli dello sviluppo locale.
DOCS_DIR = os.getenv(
    "DOCS_DIR",
    str(Path(__file__).resolve().parents[2] / "data" / "manuals"),
)
PERSIST_DIR = "chroma_db"
EMBEDDING_MODEL = "nomic-embed-text"  # scaricare con: ollama pull nomic-embed-text
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL")
CHROMA_HOST = os.getenv("CHROMA_HOST")  # se impostato: server Chroma remoto
CHROMA_PORT = int(os.getenv("CHROMA_PORT", "8000"))

# Nome della collection: quello che langchain-chroma usa di default (sia
# l'agente sia questo script devono puntare alla stessa collection).
try:
    from langchain_chroma import _LANGCHAIN_DEFAULT_COLLECTION_NAME as COLLECTION
except ImportError:  # versioni piu' vecchie
    COLLECTION = "langchain"


def load_documents(docs_dir: str):
    documents = []
    for filename in os.listdir(docs_dir):
        if filename.lower().endswith(".pdf"):
            path = os.path.join(docs_dir, filename)
            print(f"Carico: {filename}")
            loader = PyPDFLoader(path)
            docs = loader.load()  # ogni doc = una pagina, con metadata["source"] e ["page"]
            documents.extend(docs)
    return documents


def chunk_documents(documents):
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=1000,
        chunk_overlap=150,
        separators=["\n\n", "\n", ". ", " ", ""],
    )
    return splitter.split_documents(documents)


def build_vector_store(chunks):
    embeddings = OllamaEmbeddings(model=EMBEDDING_MODEL, base_url=OLLAMA_BASE_URL)
    if CHROMA_HOST:
        # FASE 11: server Chroma remoto (Docker). Si ricrea la collection da
        # zero: cosi' il re-run di ingest (es. ogni 'docker compose up')
        # non duplica i chunk gia' presenti.
        import chromadb

        client = chromadb.HttpClient(host=CHROMA_HOST, port=CHROMA_PORT)
        try:
            client.delete_collection(COLLECTION)
            print(f"   -> collection '{COLLECTION}' esistente eliminata (rebuild completo)")
        except Exception:
            pass  # prima volta: la collection non esiste ancora
        # collection_name NON esplicito: uso il default di langchain-chroma,
        # lo stesso che usa l'agente (rag.py) nel server remoto.
        vectorstore = Chroma.from_documents(
            documents=chunks,
            embedding=embeddings,
            client=client,
        )
    else:
        vectorstore = Chroma.from_documents(
            documents=chunks,
            embedding=embeddings,
            persist_directory=PERSIST_DIR,
        )
    return vectorstore


def main():
    if not os.path.isdir(DOCS_DIR):
        raise FileNotFoundError(f"Cartella '{DOCS_DIR}' non trovata. Mettici i PDF prima di eseguire.")

    print("1/3 - Caricamento documenti...")
    documents = load_documents(DOCS_DIR)
    print(f"   -> {len(documents)} pagine caricate")

    print("2/3 - Chunking...")
    chunks = chunk_documents(documents)
    print(f"   -> {len(chunks)} chunk creati")

    print("3/3 - Creazione embeddings e salvataggio nel vector DB...")
    build_vector_store(chunks)
    if CHROMA_HOST:
        print(f"Fatto. Vector DB (server {CHROMA_HOST}:{CHROMA_PORT}), collection '{COLLECTION}'.")
    else:
        print(f"Fatto. Vector DB salvato in '{PERSIST_DIR}/'")


if __name__ == "__main__":
    main()
