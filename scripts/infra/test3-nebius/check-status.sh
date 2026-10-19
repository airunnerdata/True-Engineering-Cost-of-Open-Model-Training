#!/usr/bin/env bash
# Hourly status + stats snapshot for the Nebius 3-node goodput run.
# Appends a timestamped block to results/nebius-watch-log.md and prints a one-line
# verdict (HEALTHY / RECOVERING / UNHEALTHY) on the last stdout line for automation.
set -uo pipefail
CTX=nebius-mk8s-surf-b200-mew1-i00yh47gwapc8t3dnb
NS=training
JOB=torchtitan-surf-n3b
LOG=/Users/jake/Dropbox/Benchmarks/Crusoe/results/nebius-watch-log.md

NOW_UTC=$(date -u +%Y-%m-%dT%H:%M:%SZ)
NOW_CDT=$(TZ=America/Chicago date '+%H:%M %Z')

PODS=$(kubectl --context "$CTX" get pods -n "$NS" --no-headers 2>/dev/null | grep "$JOB" \
  | awk '{print $1"="$3"(restarts="$4")"}' | tr '\n' ' ')
MASTER=$(kubectl --context "$CTX" get pods -n "$NS" \
  -l training.kubeflow.org/job-name=$JOB,training.kubeflow.org/replica-type=master \
  -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
JOBSTATE=$(kubectl --context "$CTX" get pytorchjob "$JOB" -n "$NS" -o jsonpath='{.status.conditions[-1].type}' 2>/dev/null)

LASTSTEP_LINE=$(kubectl --context "$CTX" logs -n "$NS" "$MASTER" 2>/dev/null | grep "step:" | tail -1)
STEP=$(echo "$LASTSTEP_LINE" | grep -oE 'step: +[0-9]+' | grep -oE '[0-9]+' || echo "")
MFU=$(echo "$LASTSTEP_LINE"  | grep -oE 'mfu: +[0-9.]+%' || echo "n/a")
TPS=$(echo "$LASTSTEP_LINE"  | grep -oE 'tps: +[0-9]+' || echo "n/a")
TFLOPS=$(echo "$LASTSTEP_LINE" | grep -oE 'tflops: +[0-9.]+' || echo "n/a")
LOSS=$(echo "$LASTSTEP_LINE"  | grep -oE 'loss: +[0-9.]+' || echo "n/a")

# GPU util: average across all 24 GPUs (one quick smi per node)
GPU_AVG="n/a"; GPU_DETAIL=""
UTILS=""
for p in $(kubectl --context "$CTX" get pods -n "$NS" --no-headers 2>/dev/null | grep "$JOB" | awk '{print $1}'); do
  u=$(kubectl --context "$CTX" exec -n "$NS" "$p" -c pytorch -- nvidia-smi --query-gpu=utilization.gpu --format=csv,noheader,nounits 2>/dev/null | tr '\n' ' ')
  UTILS="$UTILS $u"
done
if [ -n "$(echo $UTILS | tr -d ' ')" ]; then
  GPU_AVG=$(echo $UTILS | tr ' ' '\n' | grep -E '^[0-9]+$' | awk '{s+=$1;n++} END{if(n>0)printf "%.0f%% avg over %d GPUs", s/n, n; else print "n/a"}')
fi

# Recent restart / failure signatures in last 10 min
RECENT_RESTART=$(kubectl --context "$CTX" get pods -n "$NS" --no-headers 2>/dev/null | grep "$JOB" \
  | awk '{print $4}' | paste -sd+ - | bc 2>/dev/null || echo "?")
ERRSIG=$(kubectl --context "$CTX" logs -n "$NS" "$MASTER" --tail=60 2>/dev/null \
  | grep -ciE "Traceback|CUDA error|Error 802|RendezvousClosed|OutOfMemory|OOMKilled")
CRASHLOOP=$(kubectl --context "$CTX" get pods -n "$NS" --no-headers 2>/dev/null | grep "$JOB" | grep -c "CrashLoopBackOff")
NOTREADY=$(kubectl --context "$CTX" get pods -n "$NS" --no-headers 2>/dev/null | grep "$JOB" | grep -cvE "Running|Completed")

# disk on NFS
DISK=$(kubectl --context "$CTX" exec -n "$NS" deploy/nfs-server -- df -h /exports 2>/dev/null | awk 'NR==2{print $3"/"$2" ("$5")"}')

# Verdict
VERDICT="UNHEALTHY"
RUNNING_COUNT=$(kubectl --context "$CTX" get pods -n "$NS" --no-headers 2>/dev/null | grep "$JOB" | grep -c "1/1.*Running")
if [ "$CRASHLOOP" -gt 0 ]; then VERDICT="UNHEALTHY"
elif [ "$RUNNING_COUNT" -eq 3 ] && [ -n "$STEP" ]; then VERDICT="HEALTHY"
elif [ "$RUNNING_COUNT" -eq 3 ] && [ -z "$STEP" ]; then VERDICT="RECOVERING"   # up but not stepping yet (loading ckpt)
elif [ "$NOTREADY" -gt 0 ]; then VERDICT="RECOVERING"
fi

{
  echo ""
  echo "### $NOW_UTC  ($NOW_CDT)  —  **$VERDICT**"
  echo ""
  echo "| field | value |"
  echo "|---|---|"
  echo "| pods | $PODS |"
  echo "| job state | $JOBSTATE |"
  echo "| step | ${STEP:-—} |"
  echo "| loss / grad | $LOSS |"
  echo "| MFU | $MFU |"
  echo "| TPS | $TPS |"
  echo "| TFLOPs/GPU | $TFLOPS |"
  echo "| GPU util | $GPU_AVG |"
  echo "| total restarts | $RECENT_RESTART |"
  echo "| crashloop pods | $CRASHLOOP |"
  echo "| err-sigs (last 60 log lines) | $ERRSIG |"
  echo "| NFS disk | $DISK |"
} >> "$LOG"

echo "$NOW_UTC $VERDICT step=${STEP:-none} mfu=$MFU tps=$TPS gpu=$GPU_AVG restarts=$RECENT_RESTART crashloop=$CRASHLOOP"
# Final line = machine-readable verdict
echo "VERDICT=$VERDICT"
