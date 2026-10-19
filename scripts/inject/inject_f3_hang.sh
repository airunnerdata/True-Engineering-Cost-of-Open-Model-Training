#!/usr/bin/env bash
# inject_f3_hang.sh — F3: Silent GPU Hang
# Creates the pause sentinel file inside the running training pod.
# The training loop stalls silently — GPU utilization drops to 0%,
# no Kubernetes events are generated, pod remains Running.
#
# This is the hardest failure to detect: only GPU-level monitoring
# (nvidia-smi / DCGM / Command Center) will show the utilization drop.
# kubectl get pods, events, and logs all appear normal.
#
# Usage: bash inject_f3_hang.sh
#
# To recover: bash recover_f3_hang.sh

set -euo pipefail

NS=training
SELECTOR="app=surf-training"

POD=$(kubectl get pod -n "$NS" -l "$SELECTOR" \
      --field-selector=status.phase=Running \
      -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)

if [[ -z "$POD" ]]; then
  echo "ERROR: no running surf-training pod found in namespace $NS"
  exit 1
fi

TS=$(date -u +%Y-%m-%dT%H:%M:%SZ)
echo "INJECT_TS=$TS  F3=HANG  pod=$POD"
echo "Creating pause sentinel /tmp/surf_pause inside $POD ..."

kubectl exec -n "$NS" "$POD" -- touch /tmp/surf_pause

echo "Done — training loop is now stalled. GPU utilization will drop to 0%."
echo "Watch the monitoring UI. Type 'alert F3' in the harness when it surfaces the hang."
echo "Pod status remains 'Running' — this is intentional."
echo ""
echo "To recover: bash recover_f3_hang.sh"
