# Industrial Maintenance Agent

Agente conversazionale per la manutenzione industriale: risponde a domande su
macchine, sensori, ordini di lavoro e procedure di manutenzione, attingendo a
**dati CMMS in tempo reale** (via API REST) e a **manuali tecnici** (via RAG
con Chroma + Ollama). Le azioni di scrittura (es. creazione di un ordine di
lavoro) richiedono **conferma esplicita dell'operatore** (HITL).

## Architettura

```
┌──────────────────────────────────────────────────────────────────────┐
│  Frontend (React + Vite)                                           │
│  frontend/  — chat, conferma azioni, storico sessioni              │
└──────────────────────────┬───────────────────────────────────────────┘
                           │ HTTP (JSON)
┌──────────────────────────▼───────────────────────────────────────────┐
│  Backend (FastAPI)  —  backend/                                     │
│  /chat  /traces  /metrics  /sessions  /health                      │
│                                                                     │
│  ┌───────────────────────────────────────────────────────────────┐  │
│  │  Agente LangGraph  (backend/agent/)                         │  │
│  │  resolve_context → analyze_query → call_tool → evaluate_data│  │
│  │  → generate_answer → propose_write_action → HITL            │  │
│  └──────────┬──────────────────────┬─────────────────────────────┘  │
│             │                      │                                │
│  ┌──────────▼──────────┐  ┌────────▼──────────────┐                │
│  │  RAG (backend/rag/) │  │  Tools (backend/tools)│                │
│  │  Chroma + Ollama    │  │  registry CMMS        │                │
│  └──────────┬──────────┘  └────────┬──────────────┘                │
└─────────────┼──────────────────────┼────────────────────────────────┘
              │                      │ HTTP
┌─────────────▼──────────┐  ┌────────▼────────────────────────────────┐
│  Chroma (vector DB)    │  │  CMMS (FastAPI + PostgreSQL)  — cmms/  │
│  manuali tecnici (PDF) │  │  macchine, sensori, manutenzione,      │
└────────────────────────┘  │  ordini di lavoro, tecnici             │
                            └─────────────────────────────────────────┘
```

- **backend/** — API FastAPI + agente LangGraph (graph, state, nodes), RAG
  (pipeline + ingest), registry dei tool CMMS, client CMMS, osservabilità
  (traces, metriche, log JSONL), schemi API e modelli strutturati.
- **cmms/** — API REST del CMMS: macchine, sensori, manutenzione, ordini di
  lavoro, tecnici; persistenza PostgreSQL (SQLAlchemy).
- **frontend/** — SPA React + Vite (chat con l'agente, conferma azioni HITL).
- **data/** — manuali tecnici PDF (`data/manuals/`) e dataset/results di
  evaluation (`data/evaluation/`).
- **tests/** — test unitari, di integrazione e harness di evaluation.
- **docker/** — `Dockerfile` e `docker-compose.yml` per lo stack completo.
- **k8s/** — manifest Kubernetes (agent, CMMS, Chroma, ingest, HPA, ingress).
- **docs/** — documentazione tecnica (architettura, design agente, RAG,
  evaluation, deployment) e tutorial per fasi.

## Quickstart (Docker)

Prerequisiti: Docker + Docker Compose, **Ollama** in esecuzione con i modelli
`llama3.1` e `nomic-embed-text` (default: `http://host.docker.internal:11434`).

```bash
docker compose -f docker/docker-compose.yml up --build
```

Servizi avviati: `cmms-db` (PostgreSQL), `cmms` (API CMMS + seed), `chroma`
(vector DB), `ingest` (one-shot: indice Chroma da `data/manuals/*.pdf`),
`agent` (agente + API FastAPI).

| Servizio | Porta (default, overridabile via `.env`) |
|---|---|
| agent (API + agente) | `8005` (`AGENT_PORT`) |
| cmms (API CMMS) | `8011` (`CMMS_PORT`) |
| chroma | `8006` (`CHROMA_HOST_PORT`) |
| postgres | `5433` (`POSTGRES_PORT`) |

Prova:

```bash
curl -X POST http://localhost:8005/chat \
  -H 'Content-Type: application/json' \
  -d '{"query": "Come si sostituisce la guarnizione della pompa P-101?", "session_id": "demo"}'
```

Per il frontend:

```bash
cd frontend
npm install
npm run dev   # http://localhost:5173 (proxy verso l'agente)
```

## Sviluppo locale (senza Docker)

```bash
# 1. dipendenze
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2. PostgreSQL (o docker run -p 5432:5432 -e POSTGRES_PASSWORD=cmms -e POSTGRES_USER=cmms -e POSTGRES_DB=cmms postgres:16)
# 3. Chroma: docker run -p 8000:8000 chromadb/chroma  (oppure Chroma embedded locale)

# 4. seed + CMMS
DATABASE_URL=postgresql+psycopg://cmms:cmms@localhost:5432/cmms \
  python -m cmms.database.seed
DATABASE_URL=postgresql+psycopg://cmms:cmms@localhost:5432/cmms \
  uvicorn cmms.main:app --port 8010

# 5. indice RAG + agente
DOCS_DIR=data/manuals python -m backend.rag.ingest
DOCS_DIR=data/manuals uvicorn backend.main:app --port 8003
```

Variabili d'ambiente principali (vedi `docs/rag.md` e `docs/deployment.md`):
`OLLAMA_BASE_URL`, `CHROMA_HOST`, `CHROMA_PORT`, `CMMS_BASE_URL`,
`CMMS_API_KEY`, `DATABASE_URL`, `CHECKPOINT_DB`, `OBSERVABILITY_LOG_DIR`.

## Test ed evaluation

```bash
# unit + integration + evaluation (metriche)
pytest

# solo una categoria
pytest tests/unit
pytest tests/integration
pytest tests/evaluation/test_eval_metrics.py

# lint
ruff check backend cmms tests
```

L'evaluation completa (harness + LLM-as-judge) è in `tests/evaluation/` e i
dataset/report in `data/evaluation/` — dettagli in `docs/evaluation.md`.

## Documentazione

| Doc | Contenuto |
|---|---|
| [`docs/architecture.md`](docs/architecture.md) | Architettura d'insieme e componenti |
| [`docs/agent-design.md`](docs/agent-design.md) | Design dell'agente: graph, HITL, state, checkpointer |
| [`docs/rag.md`](docs/rag.md) | Pipeline RAG: ingest, chunking, Chroma, Ollama |
| [`docs/evaluation.md`](docs/evaluation.md) | Framework di evaluation e metriche |
| [`docs/deployment.md`](docs/deployment.md) | Docker Compose, Kubernetes, CI/CD, monitoring |
| [`docs/tutorial.md`](docs/tutorial.md) | Tutorial per fasi (storico dello sviluppo) |

## Note

- **AWS è intenzionalmente fuori scope**: il deployment target è Docker
  Compose (LAN) e Kubernetes (kind / cluster on-prem).
- I file delle fasi precedenti dello sviluppo sono in `archive/`
  (non vengono più usati).
