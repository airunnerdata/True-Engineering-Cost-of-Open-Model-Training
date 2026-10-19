#!/usr/bin/env bash
# Failure injection: Node Cordon + Pod Eviction (AWS 3-node, torchtitan-surf-n3b-aws)
# Cordons a worker node and force-deletes its pod.
# Uncordons IMMEDIATELY after eviction to avoid rescheduling deadlock.
# Use at T+12h of the 48-hour run.
set -euo pipefail

NAMESPACE=training
JOB=torchtitan-surf-n3b-aws
LOG=/tmp/test3aws-events.log
CONTEXT="arn:aws:eks:us-west-2:791221762878:cluster/surf-b300-aws"

echo "=== Failure Injection: Node Cordon (${JOB}) ==="

WORKER_POD=$(kubectl --context ${CONTEXT} get pods -n ${NAMESPACE} \
  -l training.kubeflow.org/job-name=${JOB},training.kubeflow.org/replica-type=worker \
  -o jsonpath='{.items[0].metadata.name}')
WORKER_NODE=$(kubectl --context ${CONTEXT} get pod ${WORKER_POD} -n ${NAMESPACE} \
  -o jsonpath='{.spec.nodeName}')
MASTER_POD=$(kubectl --context ${CONTEXT} get pods -n ${NAMESPACE} \
  -l training.kubeflow.org/job-name=${JOB},training.kubeflow.org/replica-type=master \
  -o jsonpath='{.items[0].metadata.name}')

STEP_BEFORE=$(kubectl --context ${CONTEXT} logs -n ${NAMESPACE} ${MASTER_POD} 2>/dev/null \
  | grep "step:" | tail -1 | grep -oE 'step:[[:space:]]+[0-9]+' | grep -oE '[0-9]+$' || echo "unknown")

INJECT_TIME=$(date -u +%Y-%m-%dT%H:%M:%SZ)
echo "  Worker pod  : ${WORKER_POD}"
echo "  Worker node : ${WORKER_NODE}"
echo "  Step before : ${STEP_BEFORE}"
echo "  Inject time : ${INJECT_TIME}"
echo "TYPE=cordon  TIME=${INJECT_TIME}  STEP_BEFORE=${STEP_BEFORE}  NODE=${WORKER_NODE}" >> ${LOG}

echo "  Cordoning worker node..."
kubectl --context ${CONTEXT} cordon ${WORKER_NODE}

echo "  Evicting worker pod..."
kubectl --context ${CONTEXT} delete pod ${WORKER_POD} -n ${NAMESPACE} --grace-period=0 --force

# Uncordon immediately so the rescheduled worker pod can land back on the node
echo "  Uncordoning node immediately (prevents rescheduling deadlock)..."
kubectl --context ${CONTEXT} uncordon ${WORKER_NODE}
echo "UNCORDON_TIME=$(date -u +%Y-%m-%dT%H:%M:%SZ)" >> ${LOG}

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
