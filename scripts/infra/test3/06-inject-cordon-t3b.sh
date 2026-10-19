#!/usr/bin/env bash
# Failure injection: Node Cordon + Pod Eviction (3-node goodput run, torchtitan-surf-t3b)
# Cordons a worker node and force-deletes its worker pod, simulating
# a node going offline. TorchTitan resumes from last checkpoint.
# Use at T+12h of the 48-hour run.
#
# Usage: bash scripts/infra/test3/06-inject-cordon-t3b.sh

set -euo pipefail
NAMESPACE=training
JOB=torchtitan-surf-t3b
LOG=/tmp/test3b-events.log

echo "=== Failure Injection: Node Cordon (${JOB}) ==="

WORKER_POD=$(kubectl get pods -n ${NAMESPACE} \
  -l training.kubeflow.org/job-name=${JOB},training.kubeflow.org/replica-type=worker \
  -o jsonpath='{.items[0].metadata.name}')
WORKER_NODE=$(kubectl get pod ${WORKER_POD} -n ${NAMESPACE} \
  -o jsonpath='{.spec.nodeName}')
MASTER_POD=$(kubectl get pods -n ${NAMESPACE} \
  -l training.kubeflow.org/job-name=${JOB},training.kubeflow.org/replica-type=master \
  -o jsonpath='{.items[0].metadata.name}')

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

# Uncordon immediately so the replacement pod can schedule
echo "  Uncordoning node so replacement pod can schedule..."
kubectl uncordon ${WORKER_NODE}

echo ""
echo "  Watching for recovery..."
until kubectl get pods -n ${NAMESPACE} --no-headers 2>/dev/null \
    | grep "${JOB}.*master" | grep -q "1/1"; do sleep 5; done

MASTER_POD=$(kubectl get pods -n ${NAMESPACE} \
  -l training.kubeflow.org/job-name=${JOB},training.kubeflow.org/replica-type=master \
  -o jsonpath='{.items[0].metadata.name}')

until kubectl logs -n ${NAMESPACE} ${MASTER_POD} 2>/dev/null | grep -q "step:"; do sleep 5; done

RESUME_TIME=$(date -u +%Y-%m-%dT%H:%M:%SZ)
RESUME_STEP=$(kubectl logs -n ${NAMESPACE} ${MASTER_POD} 2>/dev/null \
  | grep "step:" | tail -1 | grep -oE 'step:[[:space:]]+[0-9]+' | grep -oE '[0-9]+$' || echo "unknown")

echo "  Resume time : ${RESUME_TIME}"
echo "  Resume step : ${RESUME_STEP}"
echo "RESUME_TIME=${RESUME_TIME}  RESUME_STEP=${RESUME_STEP}" >> ${LOG}

echo "  Log: ${LOG}"
