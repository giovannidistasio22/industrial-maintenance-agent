# Deployment

## Docker Compose (stack completo)

Lo stack gira con un solo comando:

```bash
docker compose -f docker/docker-compose.yml up --build
```

| Servizio | Cosa fa | Porta host (default) |
|---|---|---|
| `cmms-db` | PostgreSQL 16 (volume persistente `cmms_pgdata`) | `5433` (`POSTGRES_PORT`) |
| `cmms` | API REST CMMS (`cmms/main.py`) + seed automatico all'avvio | `8011` (`CMMS_PORT`) |
| `chroma` | Server Chroma (volume persistente) | `8006` (`CHROMA_HOST_PORT`) |
| `ingest` | One-shot: indice Chroma da `data/manuals/*.pdf` | — |
| `agent` | Agente + API FastAPI (`backend/main.py`) | `8005` (`AGENT_PORT`) |

Le porte host sono overridabili via `.env` (quelle interne dei container non
cambiano). **Ollama** resta sull'host (default
`http://host.docker.internal:11434`); per girarlo anche in Docker:

```bash
docker compose --profile ollama up
docker compose exec ollama ollama pull llama3.1
docker compose exec ollama ollama pull nomic-embed-text
# e imposta OLLAMA_BASE_URL=http://ollama:11434 (in un file .env o nell'ambiente)
```

Deploy sul server (usato anche da CI):

```bash
bash deploy/deploy.sh   # git pull + docker compose up --build + health check
```

## Kubernetes (kind)

Manifest in `k8s/` (namespace `im-agent`): `cmms-db`, `chroma`, `cmms`,
`ingest`, `agent`, `ingress`, `hpa-agent`, `configmap`, `secret`, `kind`.

Script in `k8s/scripts/` (da eseguire in ordine):

```bash
bash k8s/scripts/00-install-tools.sh   # docker, kind, kubectl, cloud-provider-kind
bash k8s/scripts/01-create-cluster.sh  # cluster kind + immagine + cloud-provider-kind
bash k8s/scripts/02-deploy.sh          # deploy nell'ordine: dati -> cmms -> ingest -> agent -> ingress/HPA
bash k8s/scripts/03-test.sh            # health check + turno completo
bash k8s/scripts/04-cleanup.sh         # destroy
```

Note:

- Da kind v0.33 l'addon ingress-nginx è rimosso: **Ingress e LoadBalancer**
  sono forniti da `cloud-provider-kind` (binario sull'host).
- Accesso rapido via NodePort + `extraPortMappings` in `kind.yaml`:
  CMMS → `http://localhost:18010`, agente → `http://localhost:18003`
  (le 18xxx non confliggono con lo stack docker-compose).
- L'HPA scala i pod dell'agente; il pod `agent` ha le annotazioni
  Prometheus (vedi Monitoring).

## CI/CD (GitHub Actions)

- **`.github/workflows/ci.yml`** — su push/PR:
  1. `lint` — ruff (`backend/ cmms/ tests/`) + eslint + `tsc --noEmit` (frontend).
  2. `unit-tests` — `pytest tests/unit tests/evaluation/test_eval_metrics.py`.
  3. `integration-tests` — `pytest tests/integration` (catena completa
     Agent → Tool → CMMS con HTTP reale e LLM scriptato).
  4. `docker-build` — build di `docker/Dockerfile` + smoke test
     (`uvicorn backend.main:app`, health check).
  5. `security` — Trivy sull'immagine (HIGH/CRITICAL bloccano) +
     `pip-audit` su `requirements.txt` (con allowlist documentata per le
     CVE note di chromadb 1.5.9, server solo in rete interna).
  6. `deploy` — **solo su `main` e solo se tutto è passato**: gira su
     runner **self-hosted** (i runner pubblici non raggiungono la LAN
     10.0.40.x) ed esegue `deploy/deploy.sh`.
- **`.github/workflows/security-nightly.yml`** — ogni notte ricompila
  l'immagine e la scansiona (Trivy + pip-audit): la base image
  `python:3.12-slim` è un tag fluttuante, quindi una nuova CVE emerge come
  job rosso la mattina. Non deploya: è solo allarme precoce.

## Monitoring

Lo stack espone osservabilità nativa (vedi `backend/services/observability.py`):

- **`GET /metrics`** (agente) — snapshot JSON: turni, latenze (p50/p95),
  token, errori, work order, latenze tool/RAG/LLM.
- **`GET /traces/{session_id}`** (agente) — trace complete (span
  graph/nodo/tool/RAG/LLM) della sessione.
- **Log JSONL** — in `OBSERVABILITY_LOG_DIR` (default `logs/`), una riga per
  evento (turno, tool_call, rag_retrieval, llm_call, errori).
- **Trace export** — `logs/traces/{session_id}.json` (limite
  `OBSERVABILITY_MAX_TRACES_PER_SESSION`, default 50).

Per la raccolta in un cluster con Prometheus, il pod `agent` ha le
annotazioni:

```yaml
prometheus.io/scrape: "true"
prometheus.io/port: "8003"
prometheus.io/path: "/metrics"
```

**AWS è intenzionalmente fuori scope**: i target di deployment sono Docker
Compose (LAN) e Kubernetes (kind / on-prem).
