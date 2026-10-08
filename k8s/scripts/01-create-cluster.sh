#!/usr/bin/env bash
# 1/4: crea il cluster kind e ci carica l'immagine.
#
# Prerequisiti: docker, kind, kubectl
#   -> installali con scripts/00-install-tools.sh (NON pip: sono binari)
set -euo pipefail
cd "$(dirname "$0")/.."   # k8s/
CLUSTER=im-agent

echo "==> Cluster kind (nodo = container Docker, non una VM)"
if kind get clusters | grep -qx "$CLUSTER"; then
  echo "    cluster '$CLUSTER' gia' presente: lo lascio com' e'"
else
  kind create cluster --name $CLUSTER --config kind.yaml
fi

echo "==> Immagine dello stack (la stessa della CI)"
docker build -t industrial-maintenance-agent:ci ..
kind load docker-image industrial-maintenance-agent:ci --name $CLUSTER

echo "==> cloud-provider-kind (Ingress + LoadBalancer nativi)"
# Da kind v0.33 l'addon ingress-nginx non esiste piu': Ingress e
# LoadBalancer sono forniti da cloud-provider-kind, un binario che gira
# SULL'HOST e monitora il cluster (successore dell'addon rimosso).
if ! command -v cloud-provider-kind >/dev/null; then
  TAG=$(curl -s https://api.github.com/repos/kubernetes-sigs/cloud-provider-kind/releases/latest \
    | python3 -c "import json,sys; print(json.load(sys.stdin)['tag_name'])")
  case "$(uname -m)" in
    x86_64)  CPK_ARCH=amd64 ;;
    aarch64) CPK_ARCH=arm64 ;;
    *) echo "FAIL: architettura non supportata: $(uname -m)"; exit 1 ;;
  esac
  # nel nome dell'asset la versione NON ha il prefisso 'v'
  curl -sLo /tmp/cpk.tar.gz "https://github.com/kubernetes-sigs/cloud-provider-kind/releases/download/${TAG}/cloud-provider-kind_${TAG#v}_linux_${CPK_ARCH}.tar.gz"
  tar -xzf /tmp/cpk.tar.gz -C /tmp
  sudo install /tmp/cloud-provider-kind /usr/local/bin/
  rm /tmp/cpk.tar.gz /tmp/cloud-provider-kind
fi

# Il nodo kind di default e' un control-plane etichettato 'no external
# load balancers': etichetta da togliere, altrimenti Ingress/LB non
# raggiungono i pod.
if kubectl get node ${CLUSTER}-control-plane --show-labels | grep -q exclude-from-external-load-balancers; then
  kubectl label node ${CLUSTER}-control-plane node.kubernetes.io/exclude-from-external-load-balancers-
fi

# cloud-provider-kind e' un demone: deve restare vivo perche' l'Ingress
# funzioni. Lo partiamo in background (log in k8s/.cloud-provider-kind.log).
if pgrep -f "cloud-provider-kind" >/dev/null; then
  echo "    gia' in esecuzione (pid $(pgrep -f 'cloud-provider-kind' | head -1))"
else
  nohup cloud-provider-kind >> .cloud-provider-kind.log 2>&1 &
  echo "    avviato in background: log in .cloud-provider-kind.log"
fi

echo "==> Addon metrics-server (per l'HPA)"
kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml
# kind usa un kubelet con certificati self-signed: serve il flag
# --kubelet-insecure-tls (workaround documentato da kind).
kubectl patch deploy metrics-server -n kube-system --patch \
  '{"spec":{"template":{"spec":{"containers":[{"name":"metrics-server","args":["--kubelet-insecure-tls"]}]}}}}'

echo "==> Verifica"
kubectl get nodes
kubectl get pods -n kube-system | grep metrics-server
echo "Cluster pronto. Prossimo passo: 02-deploy.sh"
