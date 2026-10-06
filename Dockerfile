# FASE 11 - Immagine per i servizi CMMS e Agente
#
# Una sola immagine per entrambi i servizi: tutto il codice e' in src/,
# i due servizi differiscono solo per il comando di avvio (vedi
# docker-compose.yml):
#   - cmms:  python seed.py && uvicorn main6:app --port 8010
#   - agent: uvicorn main9:app --port 8003
#
# Build:  docker compose up --build
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
COPY requirements-docker.txt .
RUN pip install --no-cache-dir -r requirements-docker.txt

# Codice e documenti (docs/ e' usato dal servizio one-shot 'ingest'
# per costruire l'indice Chroma).
COPY src/ ./src/
COPY docs/ ./docs/

WORKDIR /app/src
