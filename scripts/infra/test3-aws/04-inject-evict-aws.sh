#!/usr/bin/env bash
# Failure injection: Pod Eviction (AWS 3-node goodput run, torchtitan-surf-n3b-aws)
# Force-deletes the master pod. TorchTitan resumes from the last checkpoint.
# Use at T+4h and T+24h of the 48-hour run.
set -euo pipefail

NAMESPACE=training
JOB=torchtitan-surf-n3b-aws
LOG=/tmp/test3aws-events.log
CONTEXT="arn:aws:eks:us-west-2:791221762878:cluster/surf-b300-aws"

echo "=== Failure Injection: Pod Eviction (${JOB}) ==="

MASTER_POD=$(kubectl --context ${CONTEXT} get pods -n ${NAMESPACE} \
  -l training.kubeflow.org/job-name=${JOB},training.kubeflow.org/replica-type=master \
  -o jsonpath='{.items[0].metadata.name}')

STEP_BEFORE=$(kubectl --context ${CONTEXT} logs -n ${NAMESPACE} ${MASTER_POD} 2>/dev/null \
  | grep "step:" | tail -1 | grep -oE 'step:[[:space:]]+[0-9]+' | grep -oE '[0-9]+$' || echo "unknown")

INJECT_TIME=$(date -u +%Y-%m-%dT%H:%M:%SZ)
echo "  Master pod  : ${MASTER_POD}"
echo "  Step before : ${STEP_BEFORE}"
echo "  Inject time : ${INJECT_TIME}"
echo "TYPE=evict  TIME=${INJECT_TIME}  STEP_BEFORE=${STEP_BEFORE}" >> ${LOG}

kubectl --context ${CONTEXT} delete pod ${MASTER_POD} -n ${NAMESPACE} --grace-period=0 --force

echo ""
echo "  Watching for recovery..."
until kubectl --context ${CONTEXT} get pods -n ${NAMESPACE} --no-headers 2>/dev/null \
    | grep "${JOB}.*master" | grep -q "1/1"; do sleep 5; done

MASTER_POD=$(kubectl --context ${CONTEXT} get pods -n ${NAMESPACE} \
  -l training.kubeflow.org/job-name=${JOB},training.kubeflow.org/replica-type=master \
  -o jsonpath='{.items[0].metadata.name}')

until kubectl --context ${CONTEXT} logs -n ${NAMESPACE} ${MASTER_POD} 2>/dev/null \
  | grep -q "step:"; do sleep 5; done

RESUME_TIME=$(date -u +%Y-%m-%dT%H:%M:%SZ)
RESUME_STEP=$(kubectl --context ${CONTEXT} logs -n ${NAMESPACE} ${MASTER_POD} 2>/dev/null \
  | grep "step:" | tail -1 | grep -oE 'step:[[:space:]]+[0-9]+' | grep -oE '[0-9]+$' || echo "unknown")

echo "  Resume time : ${RESUME_TIME}"
echo "  Resume step : ${RESUME_STEP}"
echo "RESUME_TIME=${RESUME_TIME}  RESUME_STEP=${RESUME_STEP}" >> ${LOG}
echo "  Recovery complete. Log: ${LOG}"
