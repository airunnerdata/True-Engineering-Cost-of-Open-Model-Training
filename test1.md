# Test 1 Methodology: Managed Orchestration / Operational Burden
## Failure Injection & Observability Analysis

---

## Objective

Measure the operational burden placed on an ML engineering team when GPU training failures occur on each cloud platform - across four failure archetypes. 

---

## Platforms

| Platform | Orchestration | GPU | Monitoring |
|----------|--------------|-----|------------|
| AWS | Amazon EKS (eksctl) | g5.xlarge (NVIDIA A10G) | CloudWatch Container Insights + CloudWatch Alarms |
| Crusoe Cloud | Crusoe Managed Kubernetes (CMK) | L40S-48gb.1x (NVIDIA L40S) | Crusoe Command Center |
| Gold Tier Neocloud | Gold Tier Neocloud Managed Kubernetes (mk8s) | gpu-l40s / 1gpu (NVIDIA L40S) | Gold Tier Neocloud built-in node dashboard |

---

## Test Protocol

### Starting Conditions

Each run begins with a GPU training pod running in the `training` namespace. The workload (`surf_train.py`) is a single-GPU PyTorch matrix-multiplication loop running approximately 2400 steps (~40 minutes). GPU utilization is sustained at ~90–100% per step via a busy-loop to ensure GPU metrics are meaningful for hang detection.

The monitoring UI for each platform is open and visible throughout each failure injection.

### Interactive Logging

An engineer executes all injection and recovery steps manually while the harness (`harness.py --test 1 --competitor <platform>`) acts as a structured event logger and timer. Every phase is logged in real time. The harness writes a timestamped JSONL event stream to `results/raw/` and produces a final scorecard at session end.

### Failure Scenarios

Four failure scenarios are injected in sequence. Each follows the same four-phase cycle: **INJECT > ALERT > ACTION > RECOVER**.

| # | Failure | Description | Injection Method |
|---|---------|-------------|-----------------|
| F1 | **OOM Kill** | Training process exhausts container memory limit, pod exits with code 137 (OOMKilled) | `inject_f1_oom.sh` - touches `/tmp/surf_oom` sentinel; training process allocates RAM until cgroup limit is hit |
| F2 | **Pod Eviction** | Training pod is force-deleted, simulating a node eviction event | `inject_f2_evict.sh` - `kubectl delete pod --grace-period=0 --force` |
| F3 | **Silent GPU Hang** | Training loop stalls without crashing; pod stays Running but GPU utilization drops to ~0% | `inject_f3_hang.sh` - touches `/tmp/surf_pause` sentinel; training loop polls and stalls |
| F4 | **Node Cordon** | GPU node is marked unschedulable and training pod is evicted, simulating planned maintenance or node failure | `inject_f4_cordon.sh` - `kubectl cordon <node>` + force-delete pod |

### Phase Definitions

| Phase | Definition |
|-------|-----------|
| **INJECT** | Injection script is run; harness clock starts |
| **ALERT** | The monitoring UI surfaces the failure visibly (alarm fires, metric drops, topology changes). Logged as elapsed minutes since inject. If nothing fires within a reasonable window, logged as `no-alert` (60-minute OBS penalty applied). |
| **ACTION** | Engineer describes the remediation steps taken |
| **RECOVER** | Training job is deleted and reapplied; pod reaches Running and first step is logged |

### Training Workload

All platforms run the same workload: `surf_train.py`, a single-GPU PyTorch matrix-multiplication loop using the public `pytorch/pytorch:2.1.0-cuda12.1-cudnn8-runtime` image, submitted as a Kubernetes Job in the `training` namespace. Key properties:

- `backoffLimit: 0` - Job does not auto-restart after failure
- `restartPolicy: Never` - Pod does not restart after container exit
- Memory limit: `8Gi` - enforces OOM kill for F1
- GPU busy-loop: each step loops matrix multiplications for 0.9s to sustain ~90% GPU utilization
- F1 hook: `/tmp/surf_oom` - training process allocates RAM until OOMKilled
- F3 hook: `/tmp/surf_pause` - training loop stalls while file exists

---

## Operational Burden Score (OBS)

OBS quantifies the human effort and response latency for each failure. It is calculated from events logged during the run.

### Formula

```
OBS = sum(alert_lag_min x 2) + sum(manual_steps x 1)

Where:
  alert_lag_min  = minutes from inject to platform surfacing the alert
  manual_steps   = count of manual remediation steps required
  no-alert       = 60-minute penalty applied per failure with no automated detection
```

### Interpretation

Lower OBS indicates lower operational burden - faster automated detection and fewer manual recovery steps. A platform with no automated monitoring scores 60 points per failure (240 for a full 4-failure run) before any manual steps.

---

## AWS-Specific Notes

### Monitoring Setup Complexity

AWS CloudWatch Container Insights requires non-trivial setup for GPU-aware Kubernetes monitoring:

1. **OIDC provider** association via `eksctl utils associate-iam-oidc-provider`
2. **IRSA** (IAM Role for Service Accounts) for the CloudWatch agent via `eksctl create iamserviceaccount`
3. **EKS addon** (`amazon-cloudwatch-observability`) installation and activation
4. **Alarm configuration** - four CloudWatch alarms, each requiring careful metric selection and dimension matching

Multiple configuration issues were encountered and resolved during setup:

| Issue | Root Cause | Fix |
|-------|-----------|-----|
| `AccessDenied: sts:AssumeRoleWithWebIdentity` | IRSA trust policy referenced old OIDC provider after cluster rebuild | Delete and recreate IRSA via eksctl |
| F1 alarm never fired | `pod_number_of_container_restarts` blind to `restartPolicy: Never` pods | Swap to `pod_status_failed` |
| F2/F4 alarms false-fired | `treat-missing-data: notBreaching` - metric goes missing (not zero) when pod dies | Change to `breaching`; swap to `pod_status_running` with correct dimensions (requires `PodName` dimension) |
| F3 alarm always in ALARM | GPU utilization 0–3% due to 1-second sleep between steps | Add GPU busy-loop to sustain ~90% utilization during normal operation |
| F3 alarm `INSUFFICIENT_DATA` | DCGM Prometheus scrape config not producing CloudWatch metrics | Swap to native `pod_gpu_utilization` from Container Insights (no DCGM required) |

Two AWS scenarios are reported.

---

## Scope Boundaries

- **Multi-node training**: All runs use a single GPU node
- **Alert notification routing**: No SNS/email/PagerDuty notification tested - detection is measured at the UI/alarm console level
- **Recovery automation**: All recovery is manual; no auto-remediation scripts evaluated
- **Steady-state monitoring cost**: CloudWatch pricing for Container Insights metrics is not measured
- **Platform-specific alerting**: Crusoe Cloud Command Center and Gold Tier Neocloud dashboard do not offer configurable alerts in their current form - detection is visual only

