# Test 3 Methodology: Goodput Stability
## Distributed Training Resilience & Failure Recovery Analysis

---

## Objective

Measure the goodput - the fraction of paid GPU time that produces useful forward training progress - of a real multi-node large-model training job subjected to repeated infrastructure failures, across three cloud platforms. The test answers the question a training team faces on a long run: *when nodes fail, how much work do you lose, how long until you are training again, and how much of your bill was wasted?*

---

## Platforms

| Platform | Orchestration | GPU | Interconnect | Checkpoint Storage |
|----------|--------------|-----|--------------|--------------------|
| AWS | Amazon EKS (eksctl) | p6-b300.48xlarge (NVIDIA B300, 8/node) | EFA (16 NIC/node, RDMA) | Local NVMe RAID0 re-exported via in-cluster NFS |
| Crusoe Cloud | Crusoe Cloud Managed Kubernetes (CMK) | B300 SXM6 (288 GB HBM3e, 8/node) | InfiniBand | NFS-shared NVMe (master exports to workers) |
| Gold Tier Neocloud | Gold Tier Neocloud Managed Kubernetes (mk8s) | B200 SXM (180 GB HBM3e, 8/node) | InfiniBand | In-cluster NFS over network-SSD block volume |

All three runs use the same model, framework, parallelism, dataset, and failure schedule. Hardware differs by what each platform offers; differences are noted and normalized where they affect comparability (see *MFU Measurement*).

---

## Test Protocol

### Starting Conditions

Each run begins with a Kubeflow `PyTorchJob` (3 replicas: 1 master + 2 workers) scheduled one pod per node, 8 GPUs per pod, training Llama 3.1 70B with TorchTitan. Before the timed window opens, the job must reach steady state: all ranks rendezvous, NCCL initializes over the platform fabric, the C4 dataset cache is warmed, `torch.compile` completes, and the first checkpoint is written and verified loadable. **T0** is the timestamp of the first logged training step at steady state.

The run is observed for a **48-hour window** from T0. Shared checkpoint storage must be reachable read-write by all ranks at a single path (Distributed Checkpoint requires every rank shard at one mount point for load).

### Distributed Training Configuration

| Parameter | Value |
|-----------|-------|
| Model | Llama 3.1 70B (70,553,706,496 params) |
| Framework | TorchTitan (`torchtitan.train`) |
| Parallelism | TP=8, DP_shard=3 (24 GPUs); TP=8, DP_shard=2 on the 2-node variant |
| Local batch size | 4 sequences per GPU group |
| Sequence length | 8,192 tokens |
| Dataset | allenai/c4 (streamed via HuggingFace) |
| Precision / compile | BF16, `torch.compile` enabled, selective activation checkpointing |
| Checkpoint mode | Distributed Checkpoint (DCP), async, model+optimizer state |
| Rendezvous | c10d static (`--nnodes=3`, `rdzv_backend=c10d`) |

### Automated Logging

Unlike Tests 1, 2, and 4, Test 3 is not driven interactively. The job runs unattended; failure injections are fired by shell scripts at fixed offsets, and an event log plus periodic watch-log snapshots record state. Each injection script records the injection type, the step before failure, and - after recovery - the resume time and resume step, appended to a per-platform events log (`/tmp/test3<platform>-events.log`). A watch log (`results/test3<platform>-watch-log.md`) captures status snapshots, MTTR, and any organic incidents.

### Failure Injection Schedule

Three failures are injected at fixed offsets from T0. Each follows the cycle **INJECT > DETECT > RECOVER**.

| Offset | Failure | Target | Injection Method |
|--------|---------|--------|------------------|
| T+4h | **Pod Eviction** | master | `kubectl delete pod <master> --grace-period=0 --force` |
| T+12h | **Node Cordon + Pod Eviction** | worker | `kubectl cordon <node>` + force-delete worker pod, then immediate `uncordon` |
| T+24h | **Pod Eviction** | master | `kubectl delete pod <master> --grace-period=0 --force` |

The cordon script issues `uncordon` immediately after eviction.

### Phase Definitions

| Phase | Definition |
|-------|-----------|
| **INJECT** | Injection script runs; failure clock starts. Step-before-failure is recorded from the master log. |
| **DETECT** | The job is observed to have stalled (no forward step progress). For a single-rank loss this manifests as the surviving ranks blocking in NCCL rather than the pod exiting. |
| **RECOVER** | Pods are rescheduled, ranks re-rendezvous, the latest complete checkpoint is loaded, and the first new training step is logged. |

---

## Metrics


### Goodput Ratio

The fraction of billed GPU time spent on retained forward progress.

```
Goodput = completed_training_step_hours / total_billed_hours

Equivalently, over the window T:
Goodput = (T - sum(MTTR_i) - sum(recompute_i)) / T

  MTTR_i       = wall-clock downtime for interrupt i (no forward progress)
  recompute_i  = checkpoint_loss_i x step_time (steps recomputed after resume)
```

Both the downtime and the recomputation of lost steps are charged against goodput: billed time that did not advance the run is wasted, whether the GPUs were idle (recovering) or busy redoing work already done once.

### Recovery Time (MTTR)

```
MTTR = T_resume - T_failure
```

`T_failure` is the injection timestamp; `T_resume` is the timestamp of the first new training step logged after the failure. 

### Checkpoint Loss

```
checkpoint_loss = step_at_failure - step_of_last_complete_checkpoint
```

The number of training steps that must be recomputed after resuming from the most recent *complete* checkpoint. A checkpoint is complete only when DCP has written its `.metadata` finalizer; a checkpoint whose shards exist but whose finalizer is missing (e.g., the write was interrupted by the failure) is not loadable and the run falls back to the prior interval.

### Interrupt Rate

```
interrupt_rate = (injected + organic interrupts) / (window_hours / 24)
```

Counts every pause in forward progress per 24-hour window, including both the scheduled injections and any organic failures (node faults, storage stalls, CNI faults). Lower is better.

### Cost per Effective Compute-Hour

```
cost_per_effective_compute_hour = total_platform_cost / completed_training_step_hours
```

Derived post-run from published on-demand instance pricing x billed wall-clock x node count, divided by the goodput-adjusted productive hours. Captures the combined effect of price, throughput, and resilience on the real cost of useful work.

---

## MFU Measurement

Throughput is reported as **tokens/sec** and **achieved TFLOPs/GPU** (TorchTitan's measured `tps` and `tflops`, which depend only on the model FLOPs and step time, not on the hardware peak). Model FLOP Utilization is then:

$$\text{MFU} = \frac{\text{achieved TFLOPs/GPU}}{\text{theoretical peak TFLOPS of the GPU}}$$

---

## Checkpointing and Shared Storage

DCP load requires all rank shards visible at one path, so each platform exports a single shared filesystem that every pod mounts at `/outputs`:

- **Crusoe Cloud / Gold Tier Neocloud**: the master node exports NVMe (Crusoe Cloud) or a network-SSD block volume (Gold Tier Neocloud) over NFS; workers mount the NFS service ClusterIP.
- **AWS**: the master node's eight local NVMe SSDs are assembled into a RAID0 (`mdadm`, ~15 TB), formatted ext4, and re-exported over NFS to the workers.

Checkpoint interval is a tuning parameter, not a fixed constant: 25 steps where the storage path sustains the ~787 GB write within the interval (Crusoe Cloud), and 200 steps where the write is network-bound and a shorter interval would cause async writes to overlap and stall training (Gold Tier Neocloud, AWS). The interval directly sets the expected checkpoint loss (≈ interval / 2 for a uniformly-timed failure) and is recorded per run.

---

## Platform-Agnostic Findings

Two behaviors reproduced identically on all three platforms across all injections and are properties of the training stack, not of any single cloud:

1. **Single-rank loss stalls the job rather than self-healing.** With static c10d rendezvous, losing one rank (master *or* worker) leaves the survivors blocked in a collective until the heartbeat timeout; the Kubeflow operator recreates the missing pod but the new pod cannot rejoin the stale rendezvous. Recovery requires bouncing all pods to force a fresh rendezvous. The observed MTTR therefore includes a detection-and-bounce component on top of the intrinsic reschedule time.
2. **An in-flight checkpoint is lost on eviction.** If a failure lands while a checkpoint is still finalizing over the network storage path, that checkpoint never receives its `.metadata` finalizer and is unloadable, so the run falls back one additional interval. This roughly doubles checkpoint loss for failures that coincide with a checkpoint write.

---

## Scope Boundaries

- **Convergence**: The benchmark measures systems goodput, not model quality. Loss is tracked only to confirm the run is healthy; final loss is not a scored metric.
- **Auto-remediation**: Recovery is operator-driven (pod bounce). No job-level fault-tolerance controller (e.g., elastic rendezvous, auto-bounce hook) is evaluated.
- **Storage product parity**: Each platform uses a self-hosted NFS over its native block/NVMe rather than a managed parallel filesystem. 
- **Cost**: Cost per effective compute-hour is computed from published on-demand pricing; negotiated, committed-use, or spot pricing is not modeled.
- **Hardware parity**: Crusoe Cloud and AWS run B300; Gold Tier Neocloud runs B200. The B200/B300 dense BF16 peak is identical, so throughput is comparable, but B300's larger HBM (288 GB vs 180 GB) is not exploited via a larger batch in order to hold the configuration constant across platforms.
