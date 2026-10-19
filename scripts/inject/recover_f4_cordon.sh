#!/usr/bin/env bash
# recover_f4_cordon.sh — F4 recovery
# Uncordons the node so it can accept new pods again.
# Call this after logging 'recover F4' in the harness.
#
# Usage: bash recover_f4_cordon.sh <node-name>
#        (node name printed by inject_f4_cordon.sh)

set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <node-name>"
  echo "Node name was printed when inject_f4_cordon.sh ran."
  exit 1
fi

NODE="$1"
kubectl uncordon "$NODE"

TS=$(date -u +%Y-%m-%dT%H:%M:%SZ)
echo "RECOVER_TS=$TS  F4=UNCORDONED  node=$NODE"
echo "Node $NODE is schedulable again."
