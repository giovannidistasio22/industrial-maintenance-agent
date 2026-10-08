# RAG (Retrieval-Augmented Generation)

La pipeline RAG risponde alle domande sui **manuali tecnici** (PDF in
`data/manuals/`) recuperando i chunk più rilevanti dal vector DB (Chroma) e
generando la risposta con Ollama, citando le fonti (file + pagina).

## Componenti

| File | Ruolo |
|---|---|
| `backend/rag/ingest.py` | Ingestione one-shot: PDF → chunk → embeddings → Chroma. |
| `backend/rag/pipeline.py` | Pipeline runtime: retrieval (Chroma) + generazione (Ollama) con citazione fonti. |

## Ingestione (`python -m backend.rag.ingest`)

1. **Caricamento**: tutti i PDF in `DOCS_DIR` (default: `data/manuals/`),
   una pagina per documento, con metadata `source` (nome file) e `page`.
2. **Chunking**: `RecursiveCharacterTextSplitter` — `chunk_size=1000`,
   `chunk_overlap=150`, separatori `\n\n`, `\n`, `. `, ` `, ``.
3. **Embeddings**: `nomic-embed-text` via Ollama.
4. **Vector DB**: Chroma —
   - se `CHROMA_HOST` è impostato: **server Chroma remoto** (il servizio
     `chroma` di docker-compose). La collection (default di langchain-chroma,
     `langchain`) viene **ricreata da zero** a ogni ingest, così i re-run
     non duplicano i chunk.
   - altrimenti: **persistenza locale** in `chroma_db/` (sviluppo).

## Pipeline runtime (`RagPipeline`)

- **Retriever**: `vectorstore.as_retriever(search_kwargs={"k": 4})` —
  i 4 chunk più simili alla domanda.
- **Prompt**: "usa SOLO il contesto fornito; se non è sufficiente dillo,
  non inventare" (temperature 0).
- **Output**: risposta + lista fonti `[{file, page}]` (deduplicate).
- **Osservabilità**: span `rag_retrieval` (query, k, documenti recuperati)
  e `llm_call` (latenza, token, errori); metriche RAG e log JSONL.

## Configurazione (variabili d'ambiente)

| Variabile | Default | Note |
|---|---|---|
| `DOCS_DIR` | `data/manuals` (relativo al root del repo) | Cartella dei PDF. |
| `OLLAMA_BASE_URL` | `localhost:11434` | Nel container: `http://host.docker.internal:11434` (Ollama sull'host) o `http://ollama:11434` (profile `ollama`). |
| `CHROMA_HOST` | — (Chroma locale) | Se impostato, usa il server Chroma remoto. |
| `CHROMA_PORT` | `8000` | Porta del server Chroma. |

Modelli Ollama richiesti: `llama3.1` (LLM) e `nomic-embed-text`
(embeddings):

```bash
ollama pull llama3.1
ollama pull nomic-embed-text
```

## Nota

`tests/evaluation/build_index.py` permette di ricostruire l'indice con
parametri di chunking diversi (per gli esperimenti RAG v2, vedi
`docs/evaluation.md`).
