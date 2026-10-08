#!/usr/bin/env bash
# 3/4: testa lo stack deployato.
#
# Due modi di raggiungere i servizi:
#   A) NodePort + kind extraPortMappings (kind.yaml):
#        cmms  -> http://localhost:18010
#        agent -> http://localhost:18003
#   (porte 18xxx: 8010/8003 le tiene lo stack docker-compose)
#   B) Ingress (gestito da cloud-provider-kind, avviato da 01):
#        l'Ingress ha un IP esterno (rete kind); si instrada per header
#        Host (o con voci /etc/hosts):
#        curl -H "Host: cmms.im.local"  http://<IP>/health
#        curl -H "Host: agent.im.local" http://<IP>/health
set -euo pipefail
cd "$(dirname "$0")/.."   # k8s/

echo "==> Health CMMS (NodePort)"
curl -sf http://localhost:18010/health; echo

echo "==> Health agente (NodePort)"
curl -sf http://localhost:18003/health; echo

echo "==> Turno completo (diagnosi + conferma work order)"
curl -sf -X POST http://localhost:18003/chat \
  -H 'Content-Type: application/json' \
  -d '{"message": "P-102 ha problemi di temperatura."}'; echo

echo
echo "==> (Opzionale) via Ingress (cloud-provider-kind)"
# L'IP dell'Ingress compare dopo qualche secondo (il provider crea il
# LoadBalancer). Aspettiamolo.
ING_IP=""
for i in $(seq 1 30); do
  ING_IP=$(kubectl -n im-agent get ingress agent -o jsonpath='{.status.loadBalancer.ingress[0].ip}' 2>/dev/null || true)
  [ -n "$ING_IP" ] && break
  sleep 2
done
if [ -z "$ING_IP" ]; then
  echo "    IP Ingress non ancora disponibile (cloud-provider-kind attivo?)"
  echo "    kubectl -n im-agent get ingress"
  exit 0
fi
echo "    IP Ingress: $ING_IP"
curl -sf -H "Host: cmms.im.local"  "http://$ING_IP/health"; echo
curl -sf -H "Host: agent.im.local" "http://$ING_IP/health"; echo
