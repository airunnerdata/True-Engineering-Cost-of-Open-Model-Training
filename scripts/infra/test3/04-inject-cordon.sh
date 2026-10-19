#!/usr/bin/env bash
# Failure injection: Node Cordon + Pod Eviction
# Cordons the worker node and force-deletes the worker pod, simulating
# a node going offline. TorchTitan resumes from last checkpoint (~80s back).
# Use at hour 12 of the 48-hour run.
#
# Usage: bash scripts/infra/test3/04-inject-cordon.sh

set -euo pipefail
NAMESPACE=training
JOB=torchtitan-surf-t3
LOG=/tmp/test3-events.log

echo "=== Failure Injection: Node Cordon ==="

MASTER_POD=$(kubectl get pods -n ${NAMESPACE} \
  -l training.kubeflow.org/job-name=${JOB},training.kubeflow.org/replica-type=master \
  -o jsonpath='{.items[0].metadata.name}')
WORKER_POD=$(kubectl get pods -n ${NAMESPACE} \
  -l training.kubeflow.org/job-name=${JOB},training.kubeflow.org/replica-type=worker \
  -o jsonpath='{.items[0].metadata.name}')
WORKER_NODE=$(kubectl get pod ${WORKER_POD} -n ${NAMESPACE} \
  -o jsonpath='{.spec.nodeName}')

STEP_BEFORE=$(kubectl logs -n ${NAMESPACE} ${MASTER_POD} 2>/dev/null \
  | grep "step:" | tail -1 | grep -oE 'step:[[:space:]]+[0-9]+' | grep -oE '[0-9]+$' || echo "unknown")

INJECT_TIME=$(date -u +%Y-%m-%dT%H:%M:%SZ)
echo "  Master pod  : ${MASTER_POD}"
echo "  Worker pod  : ${WORKER_POD}"
echo "  Worker node : ${WORKER_NODE}"
echo "  Step before : ${STEP_BEFORE}"
echo "  Inject time : ${INJECT_TIME}"
echo "TYPE=cordon  TIME=${INJECT_TIME}  STEP_BEFORE=${STEP_BEFORE}  NODE=${WORKER_NODE}" >> ${LOG}

echo "  Cordoning worker node..."
kubectl cordon ${WORKER_NODE}

echo "  Evicting worker pod..."
kubectl delete pod ${WORKER_POD} -n ${NAMESPACE} --grace-period=0 --force

echo ""
echo "  ✓ Node cordoned and pod evicted. Watching for recovery..."
echo "  NOTE: Run 'kubectl uncordon ${WORKER_NODE}' after recovery."

until kubectl get pods -n ${NAMESPACE} --no-headers 2>/dev/null \
    | grep "torchtitan.*master" | grep -q "1/1"; do sleep 5; done
until kubectl logs -n ${NAMESPACE} ${MASTER_POD} 2>/dev/null | grep -q "step:"; do sleep 5; done

RESUME_TIME=$(date -u +%Y-%m-%dT%H:%M:%SZ)
RESUME_STEP=$(kubectl logs -n ${NAMESPACE} ${MASTER_POD} 2>/dev/null \
  | grep "step:" | tail -1 | grep -oE 'step:[[:space:]]+[0-9]+' | grep -oE '[0-9]+$' || echo "unknown")

echo "  Resume time : ${RESUME_TIME}"
echo "  Resume step : ${RESUME_STEP}"
echo "RESUME_TIME=${RESUME_TIME}  RESUME_STEP=${RESUME_STEP}" >> ${LOG}

# Auto-uncordon
kubectl uncordon ${WORKER_NODE}
echo "  ✓ Node uncordoned. Recovery complete. Check ${LOG} for MTTR calculation."
