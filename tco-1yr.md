# 1-Year TCO Model: Labor + Compute
## Total Cost of Ownership for On-Demand Multi-Node Training

---

## Objective

Translate the Test 1–4 benchmark measurements into a forward-looking, 1-year total cost of ownership (TCO) for a team running the Test 3 training workload (Llama 3.1 70B, 24 GPUs, TorchTitan TP=8 x DP_shard=3) **on-demand** on each platform — provisioning a cluster, running a job, and tearing it down, repeated on a fixed cadence. The model combines two cost axes:

1. **Labor** — the engineering effort to provision, operate, and tear down infrastructure, derived from measured deployment story points.
2. **Compute** — the cost to deliver the useful training work, derived from the Test 3 price-performance metrics.

The model is built so that compute cost is **workload-normalized** (cost to complete the *same training job*), which is the basis on which throughput (`$/TFLOP-hour`) — not raw GPU-hour price — determines who is cheapest.

---

## Constants

| Constant | Value |
|---|---|
| Points per FTE per week | 10 |
| Hours per week | 40 |
| Work weeks per year | 52 |
| **Hours per story point** | **4** (40 / 10) |
| **Points per FTE-year** | **520** (10 x 52) |
| Base salary | $150,000 |
| Fully burdened salary (+22%) | $183,000 |

---

## Deployment cadence

Infrastructure is **not** run 24/7/365; it is deployed on-demand and torn down each cycle. Two cadences are modeled:

- **Bi-weekly** — 26 deployments/year
- **2 runs/week** — 104 deployments/year (production scale)

Each deployment is one full **provision -> 48-hour run -> teardown** cycle. A 48-hour run is the average length of the TorchTitan job measured in Test 3 (T0 2026-05-27 21:17Z -> 2026-05-29 21:17Z), delivering 36,150 steps at 4.71 s/step on Crusoe Cloud.

---

## Labor model

Per-deployment and one-time effort is taken from `results/deployment_steps.csv` (measured story points), converted to FTE via the constants above.

| Component | Basis | Crusoe Cloud | Gold Tier Neocloud | AWS | Frequency |
|---|---|--:|--:|--:|---|
| Provision | Measured (Test 2) | 15 | 15 | 19 | per run |
| Teardown | Estimated* | 3 | 4 | 8 | per run |
| Cold-account onboarding | Measured (Test 4) | 38 | 42 | 96 | one-time (Yr 1) |
| Observability setup | Measured (Test 1) | 7 | 7 | 49 | one-time (Yr 1) |
| Incident remediation | Estimated** | 0.5 | 0.5 | 1.5 | per incident |

> \*Teardown points are estimated from the actual teardowns performed during testing: Crusoe Cloud/Gold Tier Neocloud are 1–3 CLI deletes; AWS requires nodegroup + NAT gateway + Elastic IP release + security-group cleanup + CloudFormation stack deletion + orphan sweep.
>
> \**Per-incident remediation is estimated: Crusoe Cloud and Gold Tier Neocloud have built-in, topology-aware monitoring and fast MTTR (8.2 / 13.7 min), so remediation is a few steps; AWS's higher alert lag and manual CloudWatch workflow are modeled at ~3x the per-incident effort. Incidents are set at **2 per run** (held constant across platforms).

```
labor_points/yr = (provision + teardown) x deployments
                + cold_start + observability_setup           (one-time)
                + incident_pts x incidents_per_run x deployments
labor_FTE       = labor_points/yr / 520
labor_cost/yr   = labor_FTE x $183,000
```

### Worked FTE calculation (production cadence, 104 runs/yr)

Conversion basis: 10 pts/FTE-week x 52 = **520 points per FTE-year**. Incidents = 2/run x 104 runs = **208 incidents/yr**.

Provision, cold-start, and observability points are taken directly from `results/deployment_steps.csv` (the same story points published in Tests 1, 2, and 4); only teardown and per-incident effort are estimates. (Bi-weekly cadence uses the same method with 26 runs and 52 incidents.)

---

## Compute model (workload-normalized)

The cost driver is **useful work delivered**, not wall-clock GPU-hours. We fix the per-run workload **W** to the Test 3 Crusoe Cloud reference run and require every platform to deliver the same W:

```
W = 887 TFLOPs/GPU x 24 GPUs x 48 h = 1,021,824 TFLOP-hours per run
```

A slower GPU needs proportionally more GPU-hours to deliver W, so cost is governed by `$/TFLOP-hour`:

```
GPU-hours/run   = W / achieved_TFLOPs_per_GPU
compute_cost/run = GPU-hours/run x $/GPU-hour   (identically, W x $/TFLOP-hour)
compute_cost/yr  = compute_cost/run x deployments
```
---

## Scope and caveats

- **Workload-normalized compute** answers "cost to complete the same training job." On a fixed wall-clock GPU-hour basis (paying for time regardless of work done), Gold Tier Neocloud's cheaper B200 hour looks lower — but that bills for slower progress. `$/TFLOP-hour` is the correct lens for cost-to-train and is the basis published in Test 3.
- **Two estimated inputs** — teardown points and per-incident remediation — are flagged above; all other inputs are measured. Incidents per run (2) and run length (48 h) are modeling assumptions and the dominant tunable levers.
- **Excludes**: data egress / storage-at-rest between runs, reserved-capacity discounts, and any multi-run batching efficiencies (each deployment is modeled as an independent provision/teardown cycle).
- **One-time costs** (cold start, observability) are charged once in Year 1; a steady-state year is marginally lower.
