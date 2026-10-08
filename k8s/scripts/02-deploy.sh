#!/usr/bin/env bash
# 2/4: deploya lo stack nel cluster, nell'ordine giusto.
#
# k8s non ha 'depends_on' (e' un sistema dichiarativo: descrivi LO
# STATO, non l'ordine). Quindi l'ordine lo mettiamo NOI qui, con
# 'kubectl wait' tra un passo e l'altro:
#
#   namespace + config + secret
#   -> cmms-db + chroma (i dati)
#   -> cmms (serve il DB)
#   -> ingest (serve chroma + Ollama)
#   -> agent (serve cmms + l'indice)
#   -> ingress + HPA
set -euo pipefail
cd "$(dirname "$0")/.."   # k8s/
NS=im-agent

echo "==> Namespace, ConfigMap, Secret"
kubectl apply -f namespace.yaml
kubectl apply -f configmap.yaml -f secret.yaml

echo "==> Dati: PostgreSQL + Chroma"
kubectl apply -f cmms-db.yaml -f chroma.yaml
kubectl -n $NS wait --for=condition=ready pod -l app=cmms-db --timeout=180s
kubectl -n $NS wait --for=condition=ready pod -l app=chroma --timeout=180s

echo "==> CMMS (API + seed)"
kubectl apply -f cmms.yaml
kubectl -n $NS wait --for=condition=ready pod -l app=cmms --timeout=180s

echo "==> Ingest (one-shot: costruisce l'indice)"
kubectl apply -f ingest.yaml
kubectl -n $NS wait --for=condition=complete job/ingest --timeout=600s

echo "==> Agente"
kubectl apply -f agent.yaml
kubectl -n $NS wait --for=condition=ready pod -l app=agent --timeout=180s

echo "==> Ingress + HPA"
kubectl apply -f ingress.yaml -f hpa-agent.yaml

echo "==> Stato finale"
kubectl -n $NS get deploy,svc,job,hpa
echo "Deploy completato. Prossimo passo: 03-test.sh"
