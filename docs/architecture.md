# Architettura

## Vista d'insieme

```
┌──────────────────────────────────────────────────────────────────────┐
│  Frontend (React + Vite)  —  frontend/                             │
│  chat, conferma azioni HITL, storico sessioni                      │
└──────────────────────────┬───────────────────────────────────────────┘
                           │ HTTP (JSON)
┌──────────────────────────▼───────────────────────────────────────────┐
│  Backend (FastAPI)  —  backend/main.py                              │
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

## Componenti

### Frontend (`frontend/`)
SPA React + Vite (TypeScript). Chatta con l'agente via API, mostra lo
stato delle azioni HITL (azione proposta → conferma/annulla) e lo storico
della sessione. In sviluppo usa il proxy di Vite verso l'agente
(`AGENT_URL`, default `http://localhost:8003`); in produzione può essere
servita dalla stessa origine dell'agente (vedi `vite.config.ts`).

### Backend / Agente (`backend/`)
- **`backend/main.py`** — app FastAPI: `/chat` (esegue il graph),
  `/traces/{session_id}`, `/metrics`, `/sessions/{id}/history`,
  `DELETE /sessions/{id}`, `/health`.
- **`backend/agent/`** — graph LangGraph:
  - `graph.py` — costruzione del graph, checkpointer (SQLite, `CHECKPOINT_DB`),
    API `run_graph` / `resume_confirmation` / `cancel_pending_confirmation`.
  - `nodes.py` — i nodi del graph (vedi `docs/agent-design.md`).
  - `state.py` — `AgentState` (TypedDict con reducer `add_messages`).
- **`backend/tools/registry.py`** — registry dei tool: wrapper
  `StructuredTool` attorno al client CMMS (lettura macchine/sensori/manutenzione/
  ordini di lavoro/tecnici, scrittura ordini di lavoro) + tool RAG
  `search_manual`.
- **`backend/rag/`** — pipeline RAG (vedi `docs/rag.md`).
- **`backend/services/`**
  - `cmms_client.py` — client HTTP per l'API CMMS (`CMMS_BASE_URL`,
    `CMMS_API_KEY`, `CMMS_TIMEOUT`).
  - `observability.py` — tracing (span per turno/nodo/tool/RAG/LLM),
    metriche (turni, latenze, token, errori), log JSONL
    (`OBSERVABILITY_LOG_DIR`, default `logs/`), export trace in
    `logs/traces/*.json`.
- **`backend/api/schemas.py`** — schemi della API (ChatRequest/ChatResponse/ToolCall).
- **`backend/models/schemas.py`** — schemi Pydantic per gli output
  strutturati dei nodi (ContextResolution, AnalysisDecision,
  EvaluationDecision, WriteProposal, ConfirmationInterpretation, ToolName).

### CMMS (`cmms/`)
API REST FastAPI + PostgreSQL (SQLAlchemy):
- `cmms/main.py` — endpoint: `GET /machines`, `GET /machines/{id}`,
  `GET /machines/{id}/sensors`, `GET /machines/{id}/maintenance`,
  `GET/POST /work-orders`, `GET /technicians`, `GET /health`.
  Autenticazione con API key (`CMMS_API_KEY`, default `dev-cmms-key`).
- `cmms/api/schemas.py` — schemi Pydantic dell'API.
- `cmms/models/models.py` — modelli SQLAlchemy (Machine, Sensor,
  MaintenanceRecord, WorkOrder, Technician).
- `cmms/database/db.py` — engine/session (`DATABASE_URL`, default
  `postgresql+psycopg://cmms:cmms@localhost:5432/cmms`).
- `cmms/database/seed.py` — seed dei dati demo (`python -m cmms.database.seed`).

### Dati (`data/`)
- `data/manuals/` — manuali tecnici PDF (pompa, compressore, procedure,
  sicurezza, troubleshooting) → input della pipeline RAG.
- `data/evaluation/` — dataset e risultati di evaluation
  (vedi `docs/evaluation.md`).

### Test (`tests/`)
- `tests/unit/` — unit test (RAG, client CMMS, API CMMS, osservabilità).
- `tests/integration/` — test end-to-end dell'agente (dataset
  `agent_dataset.json`) e osservabilità.
- `tests/evaluation/` — harness di evaluation (evaluator, judge,
  build_index) + test delle metriche.
- `tests/conftest.py` — fixture condivise (client FastAPI, LLM scriptato).

## Flusso di una richiesta

1. `POST /chat` → `run_graph(query, session_id)`.
2. Il graph risolve il contesto (macchina attiva), analizza la domanda
   (LLM strutturato), decide quale tool chiamare (CMMS o RAG), valuta i
   dati, genera la risposta.
3. Se la risposta implica una **scrittura** (es. nuovo ordine di lavoro),
   il graph si ferma in `request_confirmation`: l'azione resta in
   `pending_action` nel checkpointer e la risposta restituisce
   `pending_action` al client.
4. L'utente conferma/annulla → `resume_confirmation` riprende il graph
   dal checkpoint (o `cancel_pending_confirmation` lo scarta).

Tutto il turno è tracciato: span, metriche, log JSONL e trace export
(`logs/traces/`), esposti via `/traces/{session_id}` e `/metrics`.
