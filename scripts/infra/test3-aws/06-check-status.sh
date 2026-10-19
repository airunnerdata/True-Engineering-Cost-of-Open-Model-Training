#!/usr/bin/env bash
# Status + stats snapshot for the AWS p6-b300 3-node goodput run.
# Appends a markdown block to results/test3aws-watch-log.md and prints a VERDICT line.
set -uo pipefail

export PATH="$PATH"
source /Users/jake/Dropbox/Benchmarks/Crusoe/bin/auth-aws.sh >/dev/null 2>&1

CTX="arn:aws:eks:us-west-2:791221762878:cluster/surf-b300-aws"
NS=training
JOB=torchtitan-surf-n3b-aws
MP=${JOB}-master-0
WLOG=/Users/jake/Dropbox/Benchmarks/Crusoe/results/test3aws-watch-log.md
K="kubectl --context ${CTX}"

NOW=$(date -u +%Y-%m-%dT%H:%M:%SZ)

PODS=$($K get pods -n $NS -l training.kubeflow.org/job-name=$JOB --no-headers 2>/dev/null \
  | awk '{print $1"="$3"(r"$4")"}' | tr '\n' ' ')
RESTARTS=$($K get pods -n $NS -l training.kubeflow.org/job-name=$JOB \
  -o jsonpath='{range .items[*]}{.status.containerStatuses[0].restartCount}{" "}{end}' 2>/dev/null \
  | awk '{s=0;for(i=1;i<=NF;i++)s+=$i;print s}')
CRASHLOOP=$(echo "$PODS" | grep -o "CrashLoopBackOff" | wc -l | tr -d ' ')

LASTSTEP=$($K logs -n $NS $MP --tail=2000 2>/dev/null | grep "step:" | tail -1)
STEP=$(echo "$LASTSTEP" | grep -oE 'step: +[0-9,]+' | grep -oE '[0-9,]+' | head -1)
LOSS=$(echo "$LASTSTEP" | grep -oE 'loss: +[0-9.]+' | head -1)
MFU=$(echo "$LASTSTEP" | grep -oE 'mfu: *[0-9.]+%' | head -1)
TPS=$(echo "$LASTSTEP" | grep -oE 'tps: +[0-9,]+' | head -1)
TFLOPS=$(echo "$LASTSTEP" | grep -oE 'tflops: +[0-9.]+' | head -1)

EFA=$($K logs -n $NS $MP 2>/dev/null | grep -m1 -oE 'Selected provider is efa[^)]*\)' | head -1)
[ -z "$EFA" ] && EFA="(not yet logged)"

ERRSIG=$($K logs -n $NS $MP --tail=80 2>/dev/null \
  | grep -ciE "ncclRemoteError|CUDA error|Unreachable remote|RendezvousClosed|Watchdog" )

# NFS disk on master node (via the nfs-server pod)
NFSDISK=$($K exec -n $NS deploy/nfs-server -c nfs-server -- df -h /exports 2>/dev/null | tail -1 | awk '{print $3"/"$2" ("$5")"}')

# Verdict
VERDICT="HEALTHY"
if [ "${CRASHLOOP:-0}" -gt 0 ] || [ "${ERRSIG:-0}" -gt 0 ]; then VERDICT="UNHEALTHY"; fi
if echo "$PODS" | grep -qiE "Pending|Init|ContainerCreating|Error"; then VERDICT="RECOVERING"; fi
if [ -z "$STEP" ]; then VERDICT="STARTING"; fi

{
  echo ""
  echo "### ${NOW}  —  **${VERDICT}**"
  echo ""
  echo "| field | value |"
  echo "|---|---|"
  echo "| pods | ${PODS} |"
  echo "| total restarts | ${RESTARTS:-?} |"
  echo "| crashloop pods | ${CRASHLOOP:-0} |"
  echo "| step | ${STEP:-—} |"
  echo "| loss / mfu / tps / tflops | ${LOSS:-—} / ${MFU:-—} / ${TPS:-—} / ${TFLOPS:-—} |"
  echo "| EFA provider | ${EFA} |"
  echo "| err-sigs (last 80 lines) | ${ERRSIG:-0} |"
  echo "| NFS disk | ${NFSDISK:-?} |"
} >> "$WLOG"

echo "VERDICT=${VERDICT} step=${STEP:-none} mfu=${MFU:-none} restarts=${RESTARTS:-?} crashloop=${CRASHLOOP:-0} errsig=${ERRSIG:-0}"
