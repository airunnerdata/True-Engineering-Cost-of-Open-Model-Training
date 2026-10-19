# Test 2 Methodology: GPU Provisioning Friction & Sustained Performance
## EFI, MFU, Time-to-First-GPU, and Scale-Out Latency

---

## Objective

Measure the engineering complexity of provisioning a GPU Kubernetes cluster from scratch, the sustained GPU utilization of a live training workload over a multi-hour window, and the node scale-out latency under load.

---

## Platforms

| Platform | Orchestration | GPU | Scenario |
|----------|--------------|-----|---------|
| AWS | Amazon EKS (eksctl) | g5.xlarge (NVIDIA A10G) | CLI-driven via eksctl (AWS-CLI) |
| AWS | Amazon EKS (Console) | g5.xlarge (NVIDIA A10G) | Console-driven equivalent (AWS-Console) |
| Crusoe Cloud| Crusoe Cloud Managed Kubernetes (CMK) | l40s-48gb.1x (NVIDIA L40S) | Console + CLI |
| Gold Tier Neocloud | Gold Tier Neocloud Managed Kubernetes (mk8s) | gpu-l40s-d / 1gpu-16vcpu-96gb (NVIDIA L40S) | Console + CLI |

---

## Test Protocol

### Starting Conditions

Each run begins from scratch - no pre-existing cluster, no pre-staged infrastructure. The engineer creates and configures everything from the point of having a working cloud account and authenticated CLI.

- **AWS-CLI**: Existing AWS account with eksctl, kubectl, and AWS CLI installed and authenticated. VPC and subnets are the default us-west-2 resources (already present on the account).
- **AWS-Console**: Same account, same starting point, but all provisioning is performed through the AWS web console. eksctl is not used. 
- **Crusoe**: Existing Crusoe Cloud account with the Crusoe Cloud CLI installed and authenticated.
- **Gold Tier Neocloud**: Existing Gold Tier Neocloud account with the Gold Tier Neocloud CLI installed and authenticated.

### Two-Phase Structure

The test is divided into two phases, each logged separately.

**Phase A - Cluster Provisioning**: From the first action taken (opening a console, running a CLI command) to the point where a GPU node is Ready and `nvidia.com/gpu: 1` is allocatable. Phase A produces the EFI score and setup time.

**Phase B - Workload**: From job submission to session end. Captures time-to-first-GPU, MFU readings, and scale-out latency.

### Interactive Logging

An engineer executes all steps manually while the harness (`scripts/tests/test_02.py`) acts as a structured event logger and timer. Phase A steps are logged in real time using typed commands (`service`, `cli`, `config`, `error`). Phase B metrics are entered as they are observed. The harness writes a timestamped JSONL event stream to `results/raw/` and produces a final scorecard on session end.

---

## Phase A - Engineering Friction Index (EFI)

EFI quantifies the provisioning effort required to reach a schedulable GPU node. The formula is identical to Test 4.

### Formula

```
EFI = (distinct services x 3)
    + (config files x 2)
    + (CLI commands x 1)
    + (error / retry events x 2)
```

### Event Definitions

| Event Type | Definition | EFI Weight |
|------------|-----------|:----------:|
| **Service** | A distinct cloud service, console page, or managed add-on navigated or configured (e.g., EKS console, IAM, Crusoe Cloud Orchestration). Deduplicated - revisiting the same service does not add additional points. | x3 |
| **Config file** | A YAML, JSON, or configuration file created or edited (e.g., eksctl cluster manifest). | x2 |
| **CLI command** | A terminal command executed (e.g., `eksctl create cluster`, `Crusoe Cloud kubernetes clusters get-credentials`). Each discrete invocation is counted separately. | x1 |
| **Error / retry** | Any error requiring a retry, workaround, or remediation step. | x2 |

**AWS-CLI vs. AWS-Console**: The eksctl tool artificially lowers the AWS EFI by abstracting IAM role creation, VPC/subnet selection, and managed nodegroup provisioning into two config files and a single CLI command. The console-equivalent workflow requires manually navigating IAM to create both a cluster role and a node role (with three policy attachments), then stepping through the EKS create cluster wizard and add node group wizard separately. This difference represents the hidden complexity that eksctl absorbs on the engineer's behalf.

---

## Phase B - Workload Metrics

### Time-to-First-GPU (TTFG)

TTFG measures the elapsed seconds from `kubectl apply` (job submission) to the first confirmed GPU step in the training pod logs. It captures image pull time, pod scheduling latency, container startup, and CUDA initialization.

### GPU Model FLOP Utilization (MFU)

MFU is measured as reported GPU utilization (%) via `nvidia-smi --query-gpu=utilization.gpu --format=csv,noheader` sampled from inside the running training pod. Readings are taken manually at approximately 15-minute intervals over the observation window (minimum 3.5 hours).

The training workload (`surf_train.py`) sustains ~90Ð100% GPU utilization per step via a busy-loop: each step runs matrix multiplications for 0.9 seconds before computing the loss and stepping the optimizer. This ensures GPU utilization reflects real compute demand rather than idle overhead.

**Alternative**

If we wanted to extend the benchmark to ask "how much of the hardware's theoretical compute do you actually get?", we'd need a CUDA-instrumented approach and a more realistic workload (real model, larger batch, tensor cores, FP16/BF16) to get comparable numbers across platforms.

$$\text{MFU} = \frac{\text{Observed Throughput (tokens/sec)} \times \text{FLOPs/token}}{\text{Theoretical Peak FLOPS of GPU}}$$

### Scale-Out Latency

Scale-out latency measures the elapsed seconds from the scale command being issued to the second node reaching `Ready` status with `nvidia.com/gpu: 1` allocatable. The scale command is issued asynchronously (`--async` or equivalent) and a polling loop measures elapsed time:

```bash
start=$SECONDS
until [ "$(kubectl get nodes --no-headers | grep -c Ready)" -eq 2 ]; do sleep 3; done
echo "Scale-out latency: $((SECONDS - start))s"
```

### Training Workload

All platforms run the same workload: `surf_train.py`, a single-GPU PyTorch matrix-multiplication loop using the public `pytorch/pytorch:2.1.0-cuda12.1-cudnn8-runtime` image, submitted as a Kubernetes Job in the `training` namespace. Key properties:

- `TOTAL_STEPS = 20000` - approximately 5.5 hours at 1 step/second; provides a sufficient window for 4+ hours of MFU observation
- `MATRIX_SIZE = 2048` - 2048x2048 float32 matrix multiplications
- GPU busy-loop: each step loops matrix multiplications for 0.9s to sustain ~90Ð100% GPU utilization
- `backoffLimit: 0`, `restartPolicy: Never` - job does not auto-restart
- Training script delivered via Kubernetes ConfigMap (`surf-train-script`)

---

## Scope Boundaries

- **Multi-node training**: All runs use a single GPU node for the training workload. Scale-out measures provisioning speed only, not distributed training performance.
- **GPU memory utilization**: MFU is measured as compute utilization (`utilization.gpu`), not memory bandwidth or memory utilization.
- **Autoscaler**: Scale-out is triggered manually. No Kubernetes cluster autoscaler is configured. Scale-out latency reflects the time for the cloud provider to provision and register a new node, not autoscaler trigger latency.
- **Cost**: Compute costs are not measured.
- **Persistent storage**: No object storage or persistent volumes are used. Checkpoints are written to `/tmp` within the pod.

---

## Platform-Specific Notes

### Crusoe

The Crusoe Cloud CMK cluster was provisioned with three add-ons selected at cluster creation time: NVIDIA GPU Operator, NVIDIA Network Operator, and Crusoe Cloud CSI. All three are selected via checkboxes in the Crusoe Cloud Console create cluster form - no post-cluster Helm install is required. The GPU Operator takes approximately 4Ð5 minutes after node join to fully initialize (driver, device plugin, DCGM exporter, and container toolkit all reach Running state). Scale-out was performed via `Crusoe Cloud kubernetes nodepools update --count 2`.

### Gold Tier Neocloud

Gold Tier Neocloud provisions GPU nodes with the NVIDIA GPU Operator pre-installed as part of the node image. No separate Helm install is required, unlike Test 4 where the standalone NVIDIA device plugin was unsupported and Helm-based GPU Operator installation was necessary. The improvement reflects a Gold Tier Neocloud platform update between Test 4 and Test 2. Scale-out was performed via `Gold Tier Neocloud mk8s node-group update --fixed-node-count 2 --async`.

### AWS eksctl Abstraction

The AWS-CLI scenario uses eksctl to provision the EKS cluster and managed nodegroup. eksctl reads a single YAML config file and handles:

- IAM cluster role creation
- IAM node role creation
- VPC/subnet wiring
- Managed nodegroup launch template
- NVIDIA device plugin (pre-installed via the GPU-optimized EKS AMI on Kubernetes 1.32+)

Without eksctl, the equivalent console workflow requires navigating IAM, EKS, and EC2 separately and manually attaching the correct policies to both roles. This is captured in the AWS-Console EFI estimate.


