# Test 4 Methodology: The Deployment Race
## Day-in-the-Life Time-to-Value Analysis

---

## Objective

Measure the wall-clock time and engineering complexity required to deploy a GPU training workload from a cold account start to a first verified training step - across three cloud platforms. 

---

## Platforms

| Platform | Orchestration | GPU |
|----------|--------------|-----|
| AWS | Amazon EKS (eksctl) | g5.xlarge (NVIDIA A10G) |
| Crusoe Cloud | Crusoe Cloud Managed Kubernetes (CMK) | h100-80gb-sxm-ib.1x (NVIDIA H100 SXM) |
| Gold Tier Neocloud | Gold Tier Neocloud Managed Kubernetes (mk8s) | gpu-h100-sxm / 1gpu-16vcpu-200gb (NVIDIA H100 SXM) |

---

## Test Protocol

### Starting Conditions

Each run begins from the earliest possible clean state for each competitor:

- **AWS**: Existing AWS account with no pre-provisioned GPU infrastructure. IAM, VPC, EKS cluster, and node groups are created from scratch during the run.
- **Crusoe Cloud**: Existing Crusoe Cloud account. CMK cluster, node pool, container registry, and storage are created during the run.
- **Gold Tier Neocloud**: Existing Gold Tier Neocloud account. Managed Kubernetes cluster, node group, and GPU Operator are provisioned during the run.

No infrastructure is pre-staged. No scripts are pre-written. The engineer navigates consoles and terminals as a new user would on their first deployment.

### Interactive Logging

An engineer executes all deployment steps manually on the target platform while the harness (`harness.py --test 4 --competitor <platform>`) acts as a structured event logger and timer. Every step is logged in real time. The harness writes a timestamped JSONL event stream to `results/raw/` and produces a final scorecard at session end.

### Milestones

Five milestones define the race. The clock runs continuously from start to finish line.

| # | Milestone | Definition |
|---|-----------|-----------|
| 1 | **Account created** | Login confirmed; CLI authenticated with programmatic credentials |
| 2 | **Billing configured** | Payment method active; cost controls and quota requests submitted |
| 3 | **First instance launched** | GPU node running and reachable via kubectl; GPU schedulable |
| 4 | **First job submitted** | Training Kubernetes Job manifest applied to the cluster |
| 5 | **First training step confirmed** ← *finish line* | `step=0 loss=...` appears in pod logs; GPU confirmed active via nvidia-smi |

The total elapsed time from the start of Milestone 1 to the completion of Milestone 5 is the **wall-clock score**.

### Training Workload

All platforms run the same workload: a single-GPU PyTorch matrix-multiplication training loop using the public `pytorch/pytorch:2.1.0-cuda12.1-cudnn8-runtime` image, submitted as a Kubernetes Job in the `training` namespace. The workload is intentionally minimal - its purpose is to confirm that a GPU is allocated, CUDA is functional, and the training loop produces output. No model weights, datasets, or external storage are required.

---

## Engineering Friction Index (EFI)

EFI quantifies the human effort required to complete the deployment. It is calculated from events logged during the run and locked pre-test.

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
| **Service** | A distinct cloud service or console construct navigated or configured (e.g., IAM, ECR, VPC). Deduplicated - revisiting the same service does not add additional points. | x3 |
| **Config file** | A YAML, JSON, or configuration file created or edited by the engineer (e.g., cluster manifest, job manifest, credentials file). | x2 |
| **CLI command** | A terminal command executed (e.g., `eksctl create cluster`, `kubectl apply`). Each discrete invocation is counted separately. | x1 |
| **Error / retry** | Any error requiring a retry, workaround, or remediation step (e.g., capacity failure, authentication error, invalid flag). | x2 |

### Interpretation

Higher EFI indicates more engineering friction - more services to touch, more configuration to write, more commands to run, or more errors to recover from. A lower EFI on a faster wall-clock time indicates a smoother, more automated deployment path.

---

## AWS-Specific Notes

### Capacity Probing

AWS GPU instance availability varies by instance type, Availability Zone, and region. Before the timed AWS run, a capacity pre-flight probe was performed using `aws ec2 create-capacity-reservation` across all Availability Zones in five regions (us-east-1, us-east-2, us-west-1, us-west-2, eu-west-1) for p3.2xlarge (V100) and g5.xlarge (A10G) instance types.

The probe found p3.2xlarge to be unsupported (not capacity-exhausted - structurally unavailable) across all 17 tested Availability Zones. The g5.xlarge was available in all tested zones. The timed run used g5.xlarge.

### Two AWS Scenarios

Because instance availability is a material variable, two AWS scenarios are reported:

- **Worst case**: An earlier run using p3.2xlarge without pre-probing. The run did not complete (DNF) after exhausting all Availability Zones across the target region.
- **Best case**: The timed run using g5.xlarge after confirming capacity via pre-flight probe. This represents the experience of an engineer who knows how to check capacity before provisioning.

---

## Scope Boundaries

- **Multi-node training**: All runs use a single GPU node. Multi-node networking and collective communication are not tested.
- **Persistent storage integration**: Object storage and volume mounts are noted where applicable but not required for the benchmark workload.
- **Custom container images**: Where platform container registry push workflows were blocked or impractical, the public `pytorch/pytorch` image was used and the deviation is noted in `deployment_steps.csv`.
- **Cost**: Compute costs are not measured. All runs were terminated promptly after the finish line was reached.
- **Steady-state operations**: Test 4 measures time-to-first-value only. Ongoing operational burden, monitoring, and failure recovery are addressed in Test 1.

