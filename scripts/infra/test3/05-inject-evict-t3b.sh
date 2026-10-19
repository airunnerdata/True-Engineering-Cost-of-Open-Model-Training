#!/usr/bin/env bash
# Failure injection: Pod Eviction (3-node goodput run, torchtitan-surf-t3b)
# Force-deletes the master pod. TorchTitan resumes from the last
# checkpoint (every 25 steps) — worst-case ~80s of steps re-done.
# Use at T+4h and T+24h of the 48-hour run.
#
# Usage: bash scripts/infra/test3/05-inject-evict-t3b.sh

set -euo pipefail
NAMESPACE=training
JOB=torchtitan-surf-t3b
LOG=/tmp/test3b-events.log

echo "=== Failure Injection: Pod Eviction (${JOB}) ==="

MASTER_POD=$(kubectl get pods -n ${NAMESPACE} \
  -l training.kubeflow.org/job-name=${JOB},training.kubeflow.org/replica-type=master \
  -o jsonpath='{.items[0].metadata.name}')

STEP_BEFORE=$(kubectl logs -n ${NAMESPACE} ${MASTER_POD} 2>/dev/null \
  | grep "step:" | tail -1 | grep -oE 'step:[[:space:]]+[0-9]+' | grep -oE '[0-9]+$' || echo "unknown")

INJECT_TIME=$(date -u +%Y-%m-%dT%H:%M:%SZ)
echo "  Master pod  : ${MASTER_POD}"
echo "  Step before : ${STEP_BEFORE}"
echo "  Inject time : ${INJECT_TIME}"
echo "TYPE=evict  TIME=${INJECT_TIME}  STEP_BEFORE=${STEP_BEFORE}" >> ${LOG}

kubectl delete pod ${MASTER_POD} -n ${NAMESPACE} --grace-period=0 --force

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
echo ""
echo "  Recovery complete. Log: ${LOG}"
