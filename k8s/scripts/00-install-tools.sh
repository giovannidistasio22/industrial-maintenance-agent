#!/usr/bin/env bash
# 0/4: installa gli strumenti e verifica i prerequisiti.
#
# kind e kubectl NON sono pacchetti Python (pip install non esiste):
# sono binari. Qui li scarichiamo dalle fonti ufficiali.
#
# Prerequisito: docker (kind crea il nodo del cluster DENTRO un
# container Docker).
set -euo pipefail

echo "==> Verifico Docker (kind ci gira dentro)"
command -v docker >/dev/null || {
  echo "FAIL: docker non e' installato. kind ha bisogno di Docker."
  exit 1
}
docker info >/dev/null 2>&1 || {
  echo "FAIL: il daemon Docker non risponde (sudo systemctl status docker)."
  exit 1
}

# ----------------------------------------------------------------- kubectl
if command -v kubectl >/dev/null; then
  echo "==> kubectl gia' presente: $(kubectl version --client --short 2>/dev/null || kubectl version --client)"
else
  echo "==> Installo kubectl (binario ufficiale, versione stable)"
  KVER=$(curl -Ls https://dl.k8s.io/release/stable.txt)
  curl -Lo /tmp/kubectl "https://dl.k8s.io/release/${KVER}/bin/linux/amd64/kubectl"
  sudo install -o root -g root -m 0755 /tmp/kubectl /usr/local/bin/kubectl
  rm /tmp/kubectl
fi

# ------------------------------------------------------------------- kind
if command -v kind >/dev/null && kind --version >/dev/null 2>&1; then
  echo "==> kind gia' presente: $(kind --version)"
else
  echo "==> Installo kind (binario ufficiale, ultima versione)"
  # L'URL e' platform-specific (kind-linux-amd64 / kind-linux-arm64):
  # il nome 'kind' nudo NON esiste nel bucket.
  case "$(uname -m)" in
    x86_64)  KIND_ARCH=amd64 ;;
    aarch64) KIND_ARCH=arm64 ;;
    *) echo "FAIL: architettura non supportata: $(uname -m)"; exit 1 ;;
  esac
  curl -Lo /tmp/kind "https://kind.sigs.k8s.io/dl/latest/kind-linux-${KIND_ARCH}"
  sudo install -o root -g root -m 0755 /tmp/kind /usr/local/bin/kind
  rm /tmp/kind
fi

echo
echo "==> Versioni"
kubectl version --client
kind --version
echo "Strumenti pronti. Prossimo passo: 01-create-cluster.sh"
