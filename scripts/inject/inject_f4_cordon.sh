#!/usr/bin/env bash
# inject_f4_cordon.sh — F4: Node Cordon
# Marks the GPU node unschedulable and evicts the training pod,
# simulating a node failure or planned maintenance event.
# Kubernetes must reschedule the pod to another node (or fail if
# no other GPU node is available — which is worth observing).
#
# Usage: bash inject_f4_cordon.sh
#
# To restore the node after the test: bash recover_f4_cordon.sh

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

NODE=$(kubectl get pod -n "$NS" "$POD" -o jsonpath='{.spec.nodeName}')

TS=$(date -u +%Y-%m-%dT%H:%M:%SZ)
echo "INJECT_TS=$TS  F4=CORDON  pod=$POD  node=$NODE"
echo "Cordoning node $NODE ..."
kubectl cordon "$NODE"

echo "Evicting pod $POD ..."
kubectl delete pod -n "$NS" "$POD" --grace-period=0 --force

echo "Done — node=$NODE is now unschedulable, pod evicted."
echo "Watch monitoring UI for node/pod alert."
echo ""
echo "NOTE: With backoffLimit=0 the Job will not reschedule automatically."
echo "Recovery steps: uncordon node, delete job, reapply job manifest."
echo "To uncordon: bash recover_f4_cordon.sh $NODE"
