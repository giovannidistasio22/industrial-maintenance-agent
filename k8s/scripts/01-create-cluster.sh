#!/usr/bin/env bash
# FASE 15 - 1/4: crea il cluster kind e ci carica l'immagine.
#
# Prerequisiti: docker, kind, kubectl
#   (kind:  go install sigs.k8s.io/kind@latest  oppure scarica il
#    binario da https://kind.sigs.k8s.io)
set -euo pipefail
cd "$(dirname "$0")/.."   # k8s/

echo "==> Cluster kind (nodo = container Docker, non una VM)"
kind create cluster --name im-agent --config kind.yaml

echo "==> Immagine dello stack (la stessa della CI)"
docker build -t industrial-maintenance-agent:ci ..
kind load docker-image industrial-maintenance-agent:ci --name im-agent

echo "==> Addon ingress-nginx (per l'Ingress)"
kubectl apply -f https://kind.sigs.k8s.io/examples/ingress-nginx/ingress-nginx.yaml

echo "==> Addon metrics-server (per l'HPA)"
kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml
# kind usa un kubelet con certificati self-signed: serve il flag
# --kubelet-insecure-tls (workaround documentato da kind).
kubectl patch deploy metrics-server -n kube-system --patch \
  '{"spec":{"template":{"spec":{"containers":[{"name":"metrics-server","args":["--kubelet-insecure-tls"]}]}}}}'

echo "==> Verifica"
kubectl get nodes
kubectl get pods -n ingress-nginx
echo "Cluster pronto. Prossimo passo: 02-deploy.sh"
