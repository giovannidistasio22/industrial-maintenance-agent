#!/usr/bin/env bash
# Script di deploy, ESEGUITO SULLO SERVER dalla CI (runner self-hosted).
#
# La CI non "spara" comandi a caso sul server: esegue questo script,
# revisionato nel repo. Il deploy e' riproducibile e leggibile.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "==> Aggiorno il codice"
git pull

echo "==> Ricostruisco l'immagine e riavvio lo stack (volumi preservati)"
docker compose -f docker/docker-compose.yml up --build -d

echo "==> Attendo il health del CMMS (DB up)"
for i in $(seq 1 60); do
  if curl -sf http://localhost:8010/health | grep -q '"database":"up"'; then
    echo "CMMS ok (tentativo $i)"
    break
  fi
  [ "$i" -eq 60 ] && { echo "FAIL: CMMS non risponde"; docker compose -f docker/docker-compose.yml logs --tail=20 cmms; exit 1; }
  sleep 2
done

echo "==> Health dell'agente"
for i in $(seq 1 30); do
  if curl -sf http://localhost:8003/health > /dev/null; then
    echo "Agente ok (tentativo $i)"
    break
  fi
  [ "$i" -eq 30 ] && { echo "FAIL: agente non risponde"; docker compose -f docker/docker-compose.yml logs --tail=20 agent; exit 1; }
  sleep 2
done

echo "==> Deploy completato"
