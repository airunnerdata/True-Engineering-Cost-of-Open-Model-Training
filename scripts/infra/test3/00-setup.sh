#!/usr/bin/env bash
# Test 3 — Pre-flight setup script
# Run once after nodepool is Ready, before NCCL test or training job.
# Usage: bash scripts/infra/test3/00-setup.sh

set -euo pipefail

CLUSTER=surf-b300-cluster
NAMESPACE=training
HF_SECRET=hf-token

echo "=== Step 1: Get cluster credentials ==="
crusoe kubernetes clusters get-credentials ${CLUSTER}

echo ""
echo "=== Step 2: Verify B300 nodes ==="
kubectl get nodes -o wide
echo ""
kubectl describe nodes | grep -A4 "nvidia.com/gpu"

echo ""
echo "=== Step 3: Install Kubeflow Training Operator ==="
kubectl apply --server-side -k "github.com/kubeflow/training-operator/manifests/overlays/standalone?ref=v1.8.1"

echo ""
echo "=== Step 4: Wait for Training Operator to be ready ==="
kubectl wait --for=condition=available deployment/training-operator \
  -n kubeflow --timeout=120s

echo ""
echo "=== Step 5: Create training namespace ==="
kubectl get namespace ${NAMESPACE} 2>/dev/null || kubectl create namespace ${NAMESPACE}

echo ""
echo "=== Step 6: Create HuggingFace token secret ==="
echo "  Paste your HF token when prompted (hf_...):"
read -rs HF_TOKEN
kubectl create secret generic ${HF_SECRET} \
  --from-literal=token="${HF_TOKEN}" \
  --namespace ${NAMESPACE} \
  --dry-run=client -o yaml | kubectl apply -f -
echo "  Secret '${HF_SECRET}' created in namespace '${NAMESPACE}'."

echo ""
echo "=== Setup complete. Next: run NCCL pre-flight test ==="
echo "  kubectl apply -f scripts/infra/test3/01-nccl-test.yaml"
