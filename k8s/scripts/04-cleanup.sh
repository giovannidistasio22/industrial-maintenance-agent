#!/usr/bin/env bash
# 4/4: elimina il cluster (e tutto quello che ci gira).
# kind e' pensato per essere throwaway: quando hai finito, via.
set -euo pipefail

echo "==> Rimuovo il cluster kind (i PVC spariscono con il nodo)"
kind delete cluster --name im-agent

echo "Fatto. L'immagine Docker resta locale: se vuoi ripulirla,"
echo "  docker rmi industrial-maintenance-agent:ci"
