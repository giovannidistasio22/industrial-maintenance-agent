# Immagine unica per i servizi CMMS e Agente
#
# Tutto il codice e' in radice (backend/ e cmms/), i due servizi differiscono
# solo per il comando di avvio (vedi docker/docker-compose.yml):
#   - cmms:  python -m cmms.database.seed && uvicorn cmms.main:app --port 8010
#   - agent: uvicorn backend.main:app --port 8003
#
# Build:  docker compose -f docker/docker-compose.yml up --build
FROM python:3.12-slim

# Aggiorna i pacchetti OS della base image: il tag python:3.12-slim
# "galleggia", ma un'immagine gia' buildata resta ferma sulla versione
# Debian del momento. Aggiornando qui, ogni build incorpora gli ultimi
# fix di sicurezza Debian (es. CVE su libpcre2) e la gate Trivy passa.
RUN apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dipendenze prima del codice: cosi' le modifiche al codice non forzano
# la reinstallazione dei pacchetti (cache dei layer Docker).
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Codice e documenti (data/manuals/ e' usato dal servizio one-shot 'ingest'
# per costruire l'indice Chroma).
COPY backend/ ./backend/
COPY cmms/ ./cmms/
COPY data/manuals/ ./data/manuals/
