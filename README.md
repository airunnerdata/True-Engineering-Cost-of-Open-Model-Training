# Surf Report - GPU Cloud Benchmark

A structured benchmark comparing three GPU cloud platforms - **AWS**, **Crusoe Cloud**, and **Gold Tier Neocloud** - across the day-to-day operational realities of running ML training workloads at scale.

---

## Platforms

| Platform | Orchestration |
|----------|--------------|
| AWS | Amazon EKS |
| Crusoe Cloud | Crusoe Managed Kubernetes (CMK) |
| Gold Tier Neocloud | Gold Tier Neocloud Managed Kubernetes (mk8s) |

---

## Tests

### Test 1 - Managed Orchestration / Operational Burden

Measures the operational burden placed on an ML engineering team when GPU training failures occur. Four failure scenarios (OOM Kill, Pod Eviction, Silent GPU Hang, Node Cordon) are injected into a live training job. Alert detection time and manual recovery effort are measured via structured harness logging.

**Key metric**: Operational Burden Score (OBS) = Σ(alert_lag_min x 2) + Σ(manual_steps x 1), 60-min penalty for no automated detection.

| Platform | Scenario | Alerts Fired | OBS Score | Avg Alert Lag |
|----------|----------|:---:|---:|---:|
| AWS | Misconfigured CloudWatch | 0 / 4 | 480 | - |
| AWS | Corrected CloudWatch | 4 / 4 | 52.1 | 6.5 min |
| Gold Tier Neocloud | mk8s + built-in dashboard | 4 / 4 | 13.9 | 1.7 min |
| Crusoe Cloud | CMK + Command Center | 4 / 4 | **8.4** | **1.1 min** |

- [Methodology > methods/test1.md](methods/test1.md)
- [Metrics > results/test1.csv](results/test1.csv)

---

### Test 2 - GPU Provisioning Friction & Sustained Performance

Measures cluster provisioning complexity (EFI) from scratch to a running GPU training job, plus sustained GPU utilization (MFU) over a 4 hour window. Time-to-first-GPU and node scale-out latency are also captured.

**Key metrics**: EFI (same formula as Test 4), Time-to-First-GPU (seconds from job submit to GPU active), scale-out latency (seconds from scale command to node Ready + GPU schedulable).

| Platform | EFI | Setup Time | TTFG | Scale Latency |
|----------|---:|---:|---:|---:|
| AWS - CLI | 13 | 27:47 | 73s | 121s |
| AWS - Console | 28 | 34:51 | 53s | 144s |
| Gold Tier Neocloud - CLI | 11 | 07:16 | 106s | 57s |
| Gold Tier Neocloud - Console | 20 | 11:24 | 166s | 71s |
| Crusoe Cloud - CLI | **8** | **06:44** | 96s | **48s** |
| Crusoe Cloud- Console | 18 | 10:13 | 124s | 73s |

Each platform was measured in both CLI and console modes. CLI provisioning lowers EFI across all three - most dramatically on Crusoe Cloud (18 → 8) and Gold Tier Neocloud (20 → 11), whose flag-driven CLIs require no config files, versus AWS where eksctl still needs 2 YAML config files (28 → 13). TTFG is not directly comparable across vendors here (AWS ran A10G; Crusoe Cloud and Gold Tier Neocloud ran L40S).

- [Methodology > methods/test2.md](methods/test2.md)
- [Metrics > results/test2.csv](results/test2.csv)

---

### Test 3 - Goodput Stability

Measures **goodput** - the fraction of paid GPU time that produces useful forward training progress - on a 48-hour multi-node Llama 3.1 70B (TorchTitan, TP=8 x DP_shard=3, 24 GPUs) run subjected to repeated infrastructure failures. Three failures are injected at T+4h, T+12h, and T+24h (pod eviction, node cordon + eviction, pod eviction) and recovery is measured from checkpoint.

**Key metric**: Goodput % = [ 1 - ( r / MTBI ) ] × 100
-Goodput (%) = [(2,880 min - Total Lost Minutes)/2,880 min] × 100
-r = Total Recovery Overhead per Event = MTTR + ((Steps Lost × Step Time)/60)
-MTBI = Mean Time Between Interruptions (16 hours or 960 minutes)

| Platform | Goodput | MTTR (min) | Checkpoint & Recovery Overhead per event | Interrupt Rate (per 24h) | MTBI (h) |
|----------|--------:|-----------:|----------------------------------:|-------------------------:|---------:|
| Crusoe Cloud (B300) | **99.1%** | **8.6** | **8.6** | 1.5 | 16 |
| Gold Tier Neocloud (B200) | 94.2% | 13.7 | 55.9 | 1.5 | 16 |
| AWS (B300) | 95.9% | 12.7 | 39.2 | 1.5 | 16 |

> MTBI reflects the fixed injection cadence (3 failures / 48 h) and is identical by design across platforms — it is not a measure of organic reliability.

**Price-performance** (Crusoe Cloud at $8.96/GPU-hr; lowest in each column in bold):

| Platform | $ per GPU-hour | $ per Effective TFLOP-hour |
|----------|---------------:|-----------------:|
| Crusoe Cloud (B300) | 8.96 | **0.01019** |
| Gold Tier Neocloud (B200) | **7.15** | 0.01128 |
| AWS (B300) | 11.70 | 0.01564 |

> $ per TFLOP-hour normalizes for throughput (achieved TFLOPs/GPU): Crusoe Cloud delivers the lowest cost per unit of compute despite a higher GPU-hour rate than Gold Tier Neocloud's B200.

- [Methodology > methods/test3.md](methods/test3.md)
- [Metrics > results/test3.csv](results/test3.csv)

---

### Test 4 - The Deployment Race

Measures wall-clock time and engineering complexity to deploy a GPU training workload from a cold account start to a first verified training step. The test answers the question a new ML engineer faces on day one: *how long does it actually take to get a GPU running a training job?*

**Key metrics**: Wall-clock time (minutes) and Engineering Friction Index (EFI) = (services x 3) + (config files x 2) + (CLI commands x 1) + (errors x 2).

| Platform | Scenario | EFI | Wall Clock | Result |
|----------|----------|---:|---:|---:|
| AWS | Worst case - p3.2xlarge, no capacity | 49 | DNF | Did not finish |
| AWS | Best case - g5.xlarge, pre-probed | 46 | 40:07 | ✓ |
| Gold Tier Neocloud | mk8s with H100 SXM | 43 | 48:24 | ✓ |
| Crusoe Cloud | CMK with H100 SXM | **33** | **34:26** | ✓ |

- [Methodology > methods/test4.md](methods/test4.md)
- [Metrics > results/test4.csv](results/test4.csv)

---

## 1-Year ROI

Forward-looking total return on investment for a team running the Test 3 workload (Llama 3.1 70B, 24 GPUs) **on-demand** — provision → 48 h run → teardown — combining **labor** (from measured deployment story points) and **workload-normalized compute** (cost to complete the same training job, governed by `$/TFLOP-hour`). Constants: 10 pts/FTE-week, $183,000 fully-burdened salary.

**At 2 runs/week (104 deployments/year):**

| Platform | Team (FTE) | Labor / yr | Compute / yr | **Total TCO / yr** | % over Crusoe Cloud |
|----------|----------:|-----------:|-------------:|-------------------:|--------------:|
| **Crusoe Cloud (B300)** | 3.89 | $711,236 | $1,082,798 | **$1,794,034** | — |
| Gold Tier Neocloud (B200) | 4.90 | $749,244 | $1,305,761 | $2,055,005 | +13% |
| AWS (B300) | 6.28 | $1,149,029 | $1,662,148 | $2,811,177 | +56% |

Crusoe Cloud is lowest on **both** axes: its B300 throughput (879 Effective TFLOPs/GPU) plus competitive `$/TFLOP-hour` minimizes compute, while a streamlined provision/teardown/incident path needs ~2 fewer engineers than AWS. 

- [Methodology > methods/tco-1yr.md](methods/tco-1yr.md)
- [Model > results/tco-1yr.csv](results/tco-1yr.csv)

---

## Deployment Steps

Step-by-step logs of every action taken during each test, tagged by milestone, EFI type (service / config\_file / cli\_command / error), and story points. Covers both tests across all three platforms.

| Test | Platform | Steps | Points | Errors | Notes |
|------|----------|------:|------:|------:|-------|
| 1 | AWS | 21 | 49 | 4 | CloudWatch alarm setup required 4 iterative fixes before all alarms reached OK state |
| 1 | Gold Tier Neocloud | 5 | 7 | 0 | Built-in dashboard required no setup |
| 1 | Crusoe Cloud | 5 | 7 | 0 | Command Center required no setup |
| 2 | AWS-CLI | 8 | 19 | 1 | eksctl abstracts IAM/VPC/nodegroup into 2 config files; EFI=13 |
| 2 | AWS-Console | 11 | 29 | 1 | Manual IAM role creation required for cluster + nodegroup; EFI=28 |
| 2 | Gold Tier Neocloud | 8 | 15 | 0 | Console-only; GPU Operator pre-installed via node image; EFI=20 |
| 2 | Crusoe Cloud | 8 | 15 | 0 | Console-only; GPU Operator pre-installed as cluster add-on; EFI=18 |
| 4 | AWS | 33 | 96 | 0 | Includes VPC, IRSA, OIDC, GPU nodegroup provisioning from cold account |
| 4 | Gold Tier Neocloud | 22 | 42 | 1 | GPU Operator required via Helm; standalone device plugin unsupported |
| 4 | Crusoe Cloud | 20 | 38 | 1 | CCR image push blocked; pivoted to public pytorch image |

[results/deployment_steps.csv](results/deployment_steps.csv)

---

## Harness

All tests are run via the interactive harness:

```bash
python scripts/harness.py --test <1|2|3|4> --competitor <aws|crusoe cloud|Gold Tier Neocloud>
```

The harness logs every event to a timestamped JSONL file in `results/raw/` and computes a final scorecard on session end.
