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
