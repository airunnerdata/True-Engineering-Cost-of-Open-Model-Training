#!/usr/bin/env bash
# inject_f1_oom.sh — F1: OOM Kill
# Exhausts the container's system RAM (cgroup limit = 8 Gi) by allocating
# and touching 9 GiB of memory inside the running training pod.  The Linux
# OOM killer fires against the cgroup, Kubernetes marks the pod OOMKilled
# (exit 137), and the Job does not restart (backoffLimit: 0).
#
# Why system RAM instead of GPU VRAM:
#   GPU OOM in a subprocess only kills that subprocess; it leaves the training
#   process untouched.  The container memory limit enforced by the cgroup kills
#   the entire pod when any process inside exceeds it.
#
# Usage: bash inject_f1_oom.sh

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
echo "INJECT_TS=$TS  F1=OOM  pod=$POD"
echo "Touching /tmp/surf_oom inside $POD — training process will allocate RAM until OOMKilled ..."
echo "Watch pod logs for 'OOM_HOOK' lines. Expect OOMKilled in ~30 s."

# The training process polls for /tmp/surf_oom each step and, when it finds it,
# starts allocating 256 MiB chunks until the cgroup limit kills the process.
# This ensures the TRAINING process is OOMKilled (not a transient exec subprocess).
kubectl exec -n "$NS" "$POD" -- touch /tmp/surf_oom

echo ""
echo "Sentinel created. Follow progress:"
echo "  kubectl logs -f $POD -n $NS"
echo "Then check status:"
echo "  kubectl describe pod $POD -n $NS | grep -A5 'Last State'"
