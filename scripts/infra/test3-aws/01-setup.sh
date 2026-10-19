#!/usr/bin/env bash
# Test 3 AWS — full cluster setup
# Run this once after the capacity reservation activates (2026-06-09T11:30Z)
# and eksctl create cluster completes (~15 min).
#
# Usage:
#   eksctl create cluster -f 00-eks-cluster.yaml   # ~15 min, run first
#   ./01-setup.sh
set -euo pipefail

CLUSTER=surf-b300-aws
REGION=us-west-2
NS=training
HF_TOKEN=$(cat /Users/jake/Dropbox/Benchmarks/Crusoe/bin/auth-huggingface.txt | tr -d '[:space:]')
CONTEXT="arn:aws:eks:${REGION}:791221762878:cluster/${CLUSTER}"

echo "=== [1/7] Update kubeconfig ==="
aws eks update-kubeconfig --region ${REGION} --name ${CLUSTER}
kubectl config use-context ${CONTEXT}
kubectl get nodes -o wide   # should show 3x p6-b300 nodes

echo ""
echo "=== [2/7] Create placement group (if not already created by eksctl) ==="
aws ec2 create-placement-group \
  --group-name surf-b300-pg \
  --strategy cluster \
  --region ${REGION} 2>/dev/null || echo "  (placement group already exists)"

echo ""
echo "=== [3/7] Install kubeflow training operator ==="
kubectl apply --server-side -k \
  "github.com/kubeflow/training-operator/manifests/overlays/standalone?ref=v1.8.1"
echo "  Waiting for training-operator..."
kubectl wait --for=condition=available deploy/training-operator \
  -n kubeflow --timeout=120s

echo ""
echo "=== [4/7] Create namespace + HuggingFace token secret ==="
kubectl create namespace ${NS} --dry-run=client -o yaml | kubectl apply -f -
kubectl create secret generic hf-token \
  --from-literal=token="${HF_TOKEN}" \
  -n ${NS} --dry-run=client -o yaml | kubectl apply -f -

echo ""
echo "=== [5/7] Label master node for NFS affinity ==="
# Pick the first node alphabetically as the NFS/master node
MASTER_NODE=$(kubectl get nodes --no-headers \
  -o custom-columns='NAME:.metadata.name' | sort | head -1)
echo "  Labeling ${MASTER_NODE} as role=nfs-master"
kubectl label node "${MASTER_NODE}" role=nfs-master --overwrite

# Write node name to a tmp file so the YAML scripts can use it
echo "${MASTER_NODE}" > /tmp/surf-aws-master-node.txt
echo "  Master node: ${MASTER_NODE}"

echo ""
echo "=== [6/7] Initialize NVMe RAID0 + NFS on master node ==="
# Run a privileged DaemonSet on the master node to set up NVMe storage and NFS.
# This creates /mnt/ckpt on the host as a RAID0 of 4x NVMe drives (~15 TB).
# (Namespace + DaemonSet + Service only; PV/PVC come next once the IP is known.)
kubectl apply -f 02-nvme-nfs-ds.yaml
echo "  Waiting for nvme-nfs-init pod to become ready (RAID0 + NFS export)..."
kubectl rollout status ds/nvme-nfs-init -n ${NS} --timeout=360s || true
sleep 20  # allow NFS export to settle

echo ""
echo "=== [7/7] Create NFS PersistentVolume + PVC ==="
# Get NFS ClusterIP from the service created above
NFS_IP=$(kubectl get svc nfs-server -n ${NS} \
  -o jsonpath='{.spec.clusterIP}' 2>/dev/null || echo "")
if [ -z "${NFS_IP}" ]; then
  echo "  ERROR: nfs-server service not found. Check 02-nvme-nfs-ds.yaml."
  exit 1
fi
echo "  NFS ClusterIP: ${NFS_IP}"

# Substitute the real ClusterIP into the PV (immutable field, applied once)
sed "s/__NFS_IP__/${NFS_IP}/" 02b-nfs-pv.yaml | kubectl apply -f -
kubectl get pvc surf-ckpt-pvc -n ${NS}

echo ""
echo "=== Setup complete ==="
echo "  Cluster    : ${CLUSTER}"
echo "  Master node: $(cat /tmp/surf-aws-master-node.txt)"
echo "  NFS IP     : ${NFS_IP}"
echo ""
echo "Next steps:"
echo "  1. Confirm nodes are Ready:          kubectl get nodes"
echo "  2. Confirm training-operator is up:  kubectl get pods -n kubeflow"
echo "  3. Update master nodeSelector in 03-torchtitan-48h-3node-aws.yaml"
echo "     to: kubernetes.io/hostname: $(cat /tmp/surf-aws-master-node.txt)"
echo "  4. Update surf-ckpt-pv nfs.server in the YAML to: ${NFS_IP}"
echo "  5. Run NCCL test (optional):         kubectl apply -f 01b-nccl-test.yaml"
echo "  6. Launch training:                  kubectl apply -f 03-torchtitan-48h-3node-aws.yaml -n ${NS}"
