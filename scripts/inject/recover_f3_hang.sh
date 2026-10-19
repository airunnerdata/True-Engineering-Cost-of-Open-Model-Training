#!/usr/bin/env bash
# recover_f3_hang.sh — F3 recovery
# Removes the pause sentinel, unblocking the training loop.
# Use this after logging 'recover F3' in the harness.
#
# Usage: bash recover_f3_hang.sh

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

kubectl exec -n "$NS" "$POD" -- rm -f /tmp/surf_pause

TS=$(date -u +%Y-%m-%dT%H:%M:%SZ)
echo "RECOVER_TS=$TS  F3=RESUMED  pod=$POD"
echo "Pause sentinel removed — training loop will resume within ~5s."
