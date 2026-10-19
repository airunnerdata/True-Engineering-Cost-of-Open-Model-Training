#!/usr/bin/env bash
# setup-cloudwatch-test1.sh
# Sets up CloudWatch observability on the AWS EKS cluster for Test 1.
#
# What this does:
#   1. Installs the amazon-cloudwatch-observability EKS addon
#      (Container Insights metrics + Fluent Bit logs in one add-on)
#   2. Deploys DCGM Exporter DaemonSet for GPU utilization metrics
#   3. Configures CloudWatch Agent to scrape DCGM Prometheus endpoint
#   4. Creates CloudWatch Alarms for each Test 1 failure scenario:
#        F1 — OOM Kill          pod restart count spike
#        F2 — Pod Eviction      running pod count drops to 0
#        F3 — Silent GPU Hang   GPU utilization < 5% for 3 min
#        F4 — Node Cordon       node ready condition = false
#
# Prerequisites:
#   - kubectl pointing at the surf-aws cluster (aws eks update-kubeconfig ...)
#   - aws CLI configured with AdministratorAccess
#   - eksctl available (for IRSA addon attachment)
#   - The training namespace exists: kubectl create namespace training
#
# Usage:
#   bash setup-cloudwatch-test1.sh
#
# Idempotent: safe to re-run.

set -euo pipefail
log() { echo "[$(date '+%H:%M:%S')] $*"; }

# ── Config ─────────────────────────────────────────────────────────────────────
CLUSTER_NAME="${CLUSTER_NAME:-surf-aws}"
REGION="${REGION:-us-west-2}"
NAMESPACE="training"
ALARM_PREFIX="surf-test1"

# SNS topic ARN for alarm notifications — leave empty to skip email alerts.
# Create one with: aws sns create-topic --name surf-test1-alerts
SNS_ARN="${SNS_ARN:-}"

# GPU utilization threshold for F3 hang detection (percent)
GPU_UTIL_THRESHOLD=5
GPU_UTIL_EVAL_PERIODS=3   # 3 consecutive 1-min periods at < 5% = hang
# ───────────────────────────────────────────────────────────────────────────────

log "Cluster : $CLUSTER_NAME"
log "Region  : $REGION"
log "NS      : $NAMESPACE"
log ""

# ── 1. CloudWatch Observability addon ─────────────────────────────────────────
log "Step 1: Installing amazon-cloudwatch-observability addon..."

# Create IAM service account for the addon (IRSA)
if ! eksctl get iamserviceaccount \
        --cluster "$CLUSTER_NAME" \
        --namespace amazon-cloudwatch \
        --name cloudwatch-agent 2>/dev/null | grep -q cloudwatch-agent; then
    log "  Creating IRSA for CloudWatch agent..."
    eksctl create iamserviceaccount \
        --cluster "$CLUSTER_NAME" \
        --namespace amazon-cloudwatch \
        --name cloudwatch-agent \
        --attach-policy-arn arn:aws:iam::aws:policy/CloudWatchAgentServerPolicy \
        --attach-policy-arn arn:aws:iam::aws:policy/AWSXRayDaemonWriteAccess \
        --approve \
        --override-existing-serviceaccounts
    log "  IRSA created."
else
    log "  IRSA already exists — skipping."
fi

# Install or update the addon
ADDON_STATUS=$(aws eks describe-addon \
    --cluster-name "$CLUSTER_NAME" \
    --addon-name amazon-cloudwatch-observability \
    --region "$REGION" \
    --query 'addon.status' \
    --output text 2>/dev/null || echo "NOT_FOUND")

if [[ "$ADDON_STATUS" == "NOT_FOUND" ]]; then
    log "  Creating addon (this takes ~2 min)..."
    ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
    SA_ROLE=$(aws iam list-roles \
        --query "Roles[?contains(RoleName, 'cloudwatch-agent')].Arn" \
        --output text | head -1)
    aws eks create-addon \
        --cluster-name "$CLUSTER_NAME" \
        --addon-name amazon-cloudwatch-observability \
        --service-account-role-arn "$SA_ROLE" \
        --region "$REGION"
    log "  Waiting for addon to become ACTIVE..."
    aws eks wait addon-active \
        --cluster-name "$CLUSTER_NAME" \
        --addon-name amazon-cloudwatch-observability \
        --region "$REGION"
    log "  Addon ACTIVE."
elif [[ "$ADDON_STATUS" == "ACTIVE" ]]; then
    log "  Addon already ACTIVE — skipping."
else
    log "  Addon status: $ADDON_STATUS — check console if not ACTIVE after ~2 min."
fi

# ── 2. DCGM Exporter DaemonSet ─────────────────────────────────────────────────
# Exposes DCGM_FI_DEV_GPU_UTIL and other GPU metrics on port 9400.
# CloudWatch Agent scrapes this via Prometheus (configured in step 3).
log ""
log "Step 2: Deploying DCGM Exporter DaemonSet..."

kubectl apply -f - <<'DCGM_DS'
apiVersion: apps/v1
kind: DaemonSet
metadata:
  name: dcgm-exporter
  namespace: kube-system
  labels:
    app: dcgm-exporter
spec:
  selector:
    matchLabels:
      app: dcgm-exporter
  template:
    metadata:
      labels:
        app: dcgm-exporter
      annotations:
        prometheus.io/scrape: "true"
        prometheus.io/port: "9400"
        prometheus.io/path: "/metrics"
    spec:
      tolerations:
        - key: nvidia.com/gpu
          operator: Exists
          effect: NoSchedule
      nodeSelector:
        kubernetes.io/os: linux
      containers:
        - name: dcgm-exporter
          image: nvcr.io/nvidia/k8s/dcgm-exporter:3.3.6-3.4.2-ubuntu22.04
          ports:
            - containerPort: 9400
              name: metrics
          securityContext:
            capabilities:
              add: ["SYS_ADMIN"]
          resources:
            limits:
              memory: "256Mi"
              cpu: "200m"
          env:
            - name: DCGM_EXPORTER_LISTEN
              value: ":9400"
            - name: DCGM_EXPORTER_KUBERNETES
              value: "true"
---
apiVersion: v1
kind: Service
metadata:
  name: dcgm-exporter
  namespace: kube-system
  labels:
    app: dcgm-exporter
  annotations:
    prometheus.io/scrape: "true"
    prometheus.io/port: "9400"
spec:
  selector:
    app: dcgm-exporter
  ports:
    - name: metrics
      port: 9400
      targetPort: 9400
DCGM_DS

log "  Waiting for DCGM Exporter pods to be Running (~60s)..."
kubectl rollout status daemonset/dcgm-exporter -n kube-system --timeout=120s
log "  DCGM Exporter running."

# ── 3. CloudWatch Agent Prometheus config ──────────────────────────────────────
# Tells the CloudWatch Agent (installed by the addon) to scrape DCGM metrics
# and emit them as CloudWatch custom metrics under the ContainerInsights namespace.
log ""
log "Step 3: Configuring CloudWatch Agent to scrape DCGM metrics..."

kubectl apply -f - <<CWAGENT_CFG
apiVersion: v1
kind: ConfigMap
metadata:
  name: prometheus-config
  namespace: amazon-cloudwatch
data:
  prometheus.yaml: |
    global:
      scrape_interval: 60s
    scrape_configs:
      - job_name: dcgm_exporter
        kubernetes_sd_configs:
          - role: endpoints
            namespaces:
              names: [kube-system]
        relabel_configs:
          - source_labels: [__meta_kubernetes_service_label_app]
            regex: dcgm-exporter
            action: keep
          - source_labels: [__meta_kubernetes_endpoint_port_name]
            regex: metrics
            action: keep
        metric_relabel_configs:
          - source_labels: [__name__]
            regex: "DCGM_FI_DEV_GPU_UTIL|DCGM_FI_DEV_FB_USED|DCGM_FI_DEV_FB_FREE|DCGM_FI_DEV_POWER_USAGE"
            action: keep
  cwagentconfig.json: |
    {
      "logs": {
        "metrics_collected": {
          "prometheus": {
            "prometheus_config_path": "/etc/prometheusconfig/prometheus.yaml",
            "emf_processor": {
              "metric_declaration": [
                {
                  "source_labels": ["job"],
                  "label_matcher": "dcgm_exporter",
                  "dimensions": [["ClusterName", "Namespace", "pod", "gpu"]],
                  "metric_selectors": [
                    "DCGM_FI_DEV_GPU_UTIL",
                    "DCGM_FI_DEV_FB_USED",
                    "DCGM_FI_DEV_FB_FREE",
                    "DCGM_FI_DEV_POWER_USAGE"
                  ]
                }
              ]
            }
          }
        }
      }
    }
CWAGENT_CFG

# Restart CloudWatch Agent pods to pick up new config
kubectl rollout restart daemonset/cloudwatch-agent -n amazon-cloudwatch 2>/dev/null \
    && log "  CloudWatch Agent restarted." \
    || log "  Note: CloudWatch Agent rollout restart skipped (pod name may differ — check console)."

log "  GPU metrics will appear in CloudWatch under:"
log "    Namespace : ContainerInsights/Prometheus"
log "    Metric    : DCGM_FI_DEV_GPU_UTIL"
log "  Allow ~3 min for first datapoints to arrive."

# ── 4. CloudWatch Alarms ───────────────────────────────────────────────────────
log ""
log "Step 4: Creating CloudWatch Alarms..."

ALARM_ACTIONS=""
if [[ -n "$SNS_ARN" ]]; then
    ALARM_ACTIONS="--alarm-actions $SNS_ARN --ok-actions $SNS_ARN"
fi

# ── F1: OOM Kill — pod transitions to Failed state ────────────────────────────
# pod_status_failed emits 1 when a pod enters Failed phase (OOMKilled, crash).
# Catches restartPolicy: Never pods that don't restart — pod_number_of_container_restarts
# stays 0 in that case and is blind to OOMKill.
log "  F1: Pod failed status alarm..."
aws cloudwatch put-metric-alarm \
    --region "$REGION" \
    --alarm-name "${ALARM_PREFIX}-F1-OOM-pod-failed" \
    --alarm-description "Test1/F1: Pod entered Failed phase (OOMKilled or crash) in training namespace" \
    --namespace ContainerInsights \
    --metric-name pod_status_failed \
    --dimensions \
        Name=ClusterName,Value="$CLUSTER_NAME" \
        Name=Namespace,Value="$NAMESPACE" \
    --statistic Maximum \
    --period 60 \
    --evaluation-periods 1 \
    --threshold 1 \
    --comparison-operator GreaterThanOrEqualToThreshold \
    --treat-missing-data notBreaching \
    $ALARM_ACTIONS
log "    ✓ ${ALARM_PREFIX}-F1-OOM-pod-failed"

# ── F2: Pod Eviction — running pod count drops to 0 ───────────────────────────
# treat-missing-data: breaching is intentional — if Container Insights stops
# emitting pod_number_of_running_pods (which happens when no pod exists),
# we treat the absence of data as 0 running pods and alarm.
log "  F2: Running pod count alarm..."
aws cloudwatch put-metric-alarm \
    --region "$REGION" \
    --alarm-name "${ALARM_PREFIX}-F2-evict-pod-count" \
    --alarm-description "Test1/F2: No running pods in training namespace (eviction detected)" \
    --namespace ContainerInsights \
    --metric-name pod_number_of_running_pods \
    --dimensions \
        Name=ClusterName,Value="$CLUSTER_NAME" \
        Name=Namespace,Value="$NAMESPACE" \
    --statistic Minimum \
    --period 60 \
    --evaluation-periods 1 \
    --threshold 1 \
    --comparison-operator LessThanThreshold \
    --treat-missing-data breaching \
    $ALARM_ACTIONS
log "    ✓ ${ALARM_PREFIX}-F2-evict-pod-count"

# ── F3: Silent GPU Hang — utilization drops below threshold ───────────────────
# Metric arrives from DCGM via ContainerInsights/Prometheus (step 3 above).
# Allow 3 evaluation periods (3 min) to avoid false positives during checkpoints.
log "  F3: GPU utilization alarm (DCGM)..."
aws cloudwatch put-metric-alarm \
    --region "$REGION" \
    --alarm-name "${ALARM_PREFIX}-F3-hang-gpu-util" \
    --alarm-description "Test1/F3: GPU utilization < ${GPU_UTIL_THRESHOLD}% for ${GPU_UTIL_EVAL_PERIODS} min (silent hang)" \
    --namespace ContainerInsights/Prometheus \
    --metric-name DCGM_FI_DEV_GPU_UTIL \
    --dimensions \
        Name=ClusterName,Value="$CLUSTER_NAME" \
    --statistic Average \
    --period 60 \
    --evaluation-periods "$GPU_UTIL_EVAL_PERIODS" \
    --threshold "$GPU_UTIL_THRESHOLD" \
    --comparison-operator LessThanThreshold \
    --treat-missing-data missing \
    $ALARM_ACTIONS
log "    ✓ ${ALARM_PREFIX}-F3-hang-gpu-util"

# ── F4: Node Cordon — pod count drops to 0 (cordon + evict) ──────────────────
# Note: kubectl cordon marks a node unschedulable but does NOT change the node
# Ready condition — node_status_condition_ready stays 1 when cordoned.
# The inject script also force-deletes the pod, so we detect F4 via pod count
# dropping to 0 (same signal as F2, but triggered by node-level event).
log "  F4: Pod count alarm (cordon evicts pod)..."
aws cloudwatch put-metric-alarm \
    --region "$REGION" \
    --alarm-name "${ALARM_PREFIX}-F4-cordon-pod-count" \
    --alarm-description "Test1/F4: No running pods after node cordon+eviction in training namespace" \
    --namespace ContainerInsights \
    --metric-name pod_number_of_running_pods \
    --dimensions \
        Name=ClusterName,Value="$CLUSTER_NAME" \
        Name=Namespace,Value="$NAMESPACE" \
    --statistic Minimum \
    --period 60 \
    --evaluation-periods 1 \
    --threshold 1 \
    --comparison-operator LessThanThreshold \
    --treat-missing-data breaching \
    $ALARM_ACTIONS
log "    ✓ ${ALARM_PREFIX}-F4-cordon-pod-count"

# ── 5. Verify ──────────────────────────────────────────────────────────────────
log ""
log "Step 5: Verifying alarm states..."
aws cloudwatch describe-alarms \
    --alarm-names \
        "${ALARM_PREFIX}-F1-OOM-pod-failed" \
        "${ALARM_PREFIX}-F2-evict-pod-count" \
        "${ALARM_PREFIX}-F3-hang-gpu-util" \
        "${ALARM_PREFIX}-F4-cordon-pod-count" \
    --region "$REGION" \
    --query 'MetricAlarms[*].{Name:AlarmName,State:StateValue}' \
    --output table

log ""
log "═══════════════════════════════════════════════════════════════"
log "CloudWatch Test 1 setup complete."
log ""
log "Alarms to watch during the test (CloudWatch Console → Alarms):"
log "  F1  ${ALARM_PREFIX}-F1-OOM-pod-failed       (pod_status_failed >= 1)"
log "  F2  ${ALARM_PREFIX}-F2-evict-pod-count       (running pods < 1, missing=breaching)"
log "  F3  ${ALARM_PREFIX}-F3-hang-gpu-util         (DCGM GPU util < 5% for 3 min)"
log "  F4  ${ALARM_PREFIX}-F4-cordon-pod-count      (running pods < 1 after cordon+evict)"
log ""
log "Expected initial state: INSUFFICIENT_DATA for all until pod is Running"
log "  F1/F2/F4: Container Insights needs ~2 min to start emitting pod metrics"
log "  F3: INSUFFICIENT_DATA until DCGM metrics arrive (~3 min after pod start)"
log ""
log "Console URL:"
log "  https://${REGION}.console.aws.amazon.com/cloudwatch/home?region=${REGION}#alarmsV2:"
log "═══════════════════════════════════════════════════════════════"
