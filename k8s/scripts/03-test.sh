#!/usr/bin/env bash
# FASE 15 - 3/4: testa lo stack deployato.
#
# Due modi di raggiungere i servizi:
#   A) NodePort + kind extraPortMappings (kind.yaml):
#        cmms  -> http://localhost:8010
#        agent -> http://localhost:8003
#   B) Ingress (richiede ingress-nginx + voci /etc/hosts):
#        127.0.0.1  cmms.im.local agent.im.local
#        cmms  -> http://cmms.im.local
#        agent -> http://agent.im.local
set -euo pipefail
cd "$(dirname "$0")/.."   # k8s/

echo "==> Health CMMS (NodePort)"
curl -sf http://localhost:8010/health; echo

echo "==> Health agente (NodePort)"
curl -sf http://localhost:8003/health; echo

echo "==> Turno completo (diagnosi + conferma work order)"
curl -sf -X POST http://localhost:8003/chat \
  -H 'Content-Type: application/json' \
  -d '{"message": "P-102 ha problemi di temperatura."}'; echo

echo
echo "==> (Opzionale) via Ingress: aggiungi le voci /etc/hosts e prova"
echo "    sudo sh -c 'echo \"127.0.0.1 cmms.im.local agent.im.local\" >> /etc/hosts'"
echo "    curl -sf http://cmms.im.local/health"
echo "    curl -sf http://agent.im.local/health"
