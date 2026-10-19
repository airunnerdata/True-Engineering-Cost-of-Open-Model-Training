#!/usr/bin/env bash
# inject_f2_evict.sh — F2: Pod Eviction
# Forcibly deletes the running training pod, simulating an eviction event.
# With backoffLimit: 0 the Job will not reschedule automatically;
# recovery requires manually resubmitting the job.
#
# Usage: bash inject_f2_evict.sh

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
echo "INJECT_TS=$TS  F2=EVICT  pod=$POD"
echo "Deleting pod $POD ..."

kubectl delete pod -n "$NS" "$POD" --grace-period=0 --force

echo "Done — pod deleted. Watch monitoring UI for eviction alert."
echo "To recover: kubectl delete job surf-training-test1 -n $NS && kubectl apply -f training-job-test1.yaml"
