"""
Test 3: Goodput Stability Test
FSDP Multi-Node Distributed Training — Reliability & Billed Compute Efficiency
Hardware: B300 (2-3 nodes, 16-24 GPUs)
Competitor: crusoe (primary); aws / nebius (cost comparison via billing data)

Two-phase structure:

  Phase A — Setup
    From a clean account, deploy a multi-node GPU Kubernetes cluster with
    InfiniBand, install Kubeflow Training Operator, validate inter-node
    bandwidth via NCCL test, and submit the TorchTitan PyTorchJob.
    The engineer logs every action as it happens. The harness timestamps
    each step and computes a live EFI score.

    EFI (Engineering Friction Index):
        EFI = (services × 3) + (config_files × 2) + (cli_commands × 1) + (errors × 2)

  Phase B — Training Run
    Monitor a long-running distributed training job (TorchTitan Llama 3.1 8B,
    FSDP, 16 GPUs across 2 nodes). Log step counts, checkpoint events, failure
    injections, and recoveries. Compute goodput and MTTR at session end.

    Goodput = (total_steps - sum_of_checkpoint_loss) / total_steps × 100
    MTTR    = mean time from failure injection to training resumed (seconds)

Phase A commands:
    service <description>    console / UI action               [EFI +3]
    cli     <description>    CLI command                        [EFI +1]
    config  <description>    config file written or edited      [EFI +2]
    error   <description>    error or required retry            [EFI +2]
    note    <text>           free-form observation (no EFI)
    done                     close Phase A → begin Phase B
    status                   show running EFI and step counts
    help                     show this message
    abort                    cancel session

Phase B commands:
    submit                   PyTorchJob applied (kubectl apply) → starts clock
    step    <n>              current step count from training logs
    ckpt    <n>              checkpoint saved at step n
    inject  <description>    failure injected now (OOM / evict / node-fail etc.)
    resume  <n>              training resumed at step n after failure
    mfu     <0–100>          MFU reading from TorchTitan logs (enter repeatedly)
    bw      <GB/s>           NCCL all_reduce bandwidth reading (GB/s)
    note    <text>           free-form observation
    done                     close Phase B and write scorecard
    status                   show current metrics
    help                     show this message
    abort                    cancel session

Usage (via harness.py):
    python harness.py --test 3 --competitor crusoe
"""

from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional


# ── EFI weights ──────────────────────────────────────────────────────────────

EFI_WEIGHTS = {"service": 3, "config": 2, "error": 2, "cli": 1}

# ── Dataclasses ───────────────────────────────────────────────────────────────

@dataclass
class PhaseAStep:
    num: int
    kind: str
    efi_points: int
    ts: str
    description: str


@dataclass
class PhaseAResult:
    elapsed_s: float
    elapsed_hms: str
    efi: int
    step_count: int
    counts: dict
    steps: list


@dataclass
class StepReading:
    ts: str
    step: int


@dataclass
class CheckpointEvent:
    ts: str
    step: int


@dataclass
class FailureEvent:
    ts: str
    inject_step: int
    description: str
    resume_step: Optional[int] = None
    resume_ts: Optional[str] = None
    last_ckpt_before: Optional[int] = None
    checkpoint_loss: Optional[int] = None        # steps that had to be re-run
    mttr_s: Optional[float] = None


@dataclass
class MFUReading:
    ts: str
    value: float
    sample_num: int


@dataclass
class BWReading:
    ts: str
    value_gbs: float
    sample_num: int


@dataclass
class PhaseBResult:
    t_submit: str
    total_steps: int
    useful_steps: int
    goodput_pct: float
    failures: list
    mttr_s: Optional[float]
    checkpoint_loss_total: int
    mfu_avg: Optional[float]
    mfu_min: Optional[float]
    mfu_max: Optional[float]
    mfu_samples: int
    bw_avg_gbs: Optional[float]
    bw_samples: int
    observation_window_s: float
    observation_window_hms: str


@dataclass
class Session:
    competitor: str
    session: str
    started_at: str
    finished_at: str
    total_elapsed_s: float
    total_elapsed_hms: str
    phase_a: PhaseAResult
    phase_b: PhaseBResult


# ── Helpers ───────────────────────────────────────────────────────────────────

def now_ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def hms(seconds: float) -> str:
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{sec:02d}"


def avg(lst: list) -> Optional[float]:
    return round(sum(lst) / len(lst), 2) if lst else None


def write_event(fh, event: dict) -> None:
    fh.write(json.dumps(event) + "\n")
    fh.flush()


def print_phase_a_status(counts: dict, running_efi: int, steps: list) -> None:
    print(f"\n  EFI so far : {running_efi}")
    for kind, weight in EFI_WEIGHTS.items():
        n = counts.get(kind, 0)
        print(f"    {kind:<12} ×{weight}  →  {n:2d}  ({n * weight} pts)")
    print(f"  Steps logged: {len(steps)}\n")


def print_phase_b_status(
    total_steps: int,
    useful_steps: int,
    failures: list,
    mfu_readings: list,
    bw_readings: list,
    t_submit_epoch: float,
) -> None:
    elapsed = time.time() - t_submit_epoch
    goodput = round(useful_steps / total_steps * 100, 1) if total_steps > 0 else 0.0
    mttr_vals = [f.mttr_s for f in failures if f.mttr_s is not None]
    print(f"\n  Elapsed         : {hms(elapsed)}")
    print(f"  Steps (total)   : {total_steps}")
    print(f"  Steps (useful)  : {useful_steps}")
    print(f"  Goodput         : {goodput}%")
    print(f"  Failures        : {len(failures)}")
    if mttr_vals:
        print(f"  Avg MTTR        : {avg(mttr_vals):.0f}s")
    if mfu_readings:
        vals = [r.value for r in mfu_readings]
        print(f"  MFU avg/min/max : {avg(vals)}% / {min(vals)}% / {max(vals)}%  ({len(vals)} samples)")
    if bw_readings:
        vals = [r.value_gbs for r in bw_readings]
        print(f"  NCCL BW avg     : {avg(vals):.1f} GB/s  ({len(vals)} samples)")
    print()


def print_scorecard(phase_a: PhaseAResult, phase_b: PhaseBResult, session: str) -> None:
    print("\n" + "=" * 68)
    print("  TEST 3 RESULTS")
    print("=" * 68)
    print()
    print("  PHASE A — SETUP")
    print("  " + "─" * 58)
    print(f"  Setup time      : {phase_a.elapsed_hms}")
    print(f"  Total steps     : {phase_a.step_count}")
    print(f"  Services  (×3)  : {phase_a.counts.get('service', 0):3d}   →  {phase_a.counts.get('service', 0) * 3} pts")
    print(f"  Config files(×2): {phase_a.counts.get('config', 0):3d}   →  {phase_a.counts.get('config', 0) * 2} pts")
    print(f"  CLI commands(×1): {phase_a.counts.get('cli', 0):3d}   →  {phase_a.counts.get('cli', 0) * 1} pts")
    print(f"  Errors    (×2)  : {phase_a.counts.get('error', 0):3d}   →  {phase_a.counts.get('error', 0) * 2} pts")
    print("  " + "─" * 58)
    print(f"  EFI             : {phase_a.efi}")
    print()
    print("  PHASE B — TRAINING RUN")
    print("  " + "─" * 58)
    print(f"  Total steps     : {phase_b.total_steps}")
    print(f"  Useful steps    : {phase_b.useful_steps}")
    print(f"  Goodput         : {phase_b.goodput_pct}%")
    print(f"  Failures        : {len(phase_b.failures)}")
    if phase_b.mttr_s is not None:
        print(f"  Avg MTTR        : {phase_b.mttr_s:.0f}s ({phase_b.mttr_s / 60:.1f} min)")
    print(f"  Ckpt loss total : {phase_b.checkpoint_loss_total} steps")
    for i, f in enumerate(phase_b.failures, 1):
        loss = f.checkpoint_loss if f.checkpoint_loss is not None else "?"
        mttr = f"{f.mttr_s:.0f}s" if f.mttr_s is not None else "?"
        print(f"    Failure {i}      : {f.description[:40]}  loss={loss} steps  MTTR={mttr}")
    if phase_b.mfu_samples > 0:
        print(f"  MFU avg         : {phase_b.mfu_avg}%")
        print(f"  MFU min / max   : {phase_b.mfu_min}% / {phase_b.mfu_max}%")
        print(f"  MFU samples     : {phase_b.mfu_samples}")
    if phase_b.bw_samples > 0:
        print(f"  NCCL BW avg     : {phase_b.bw_avg_gbs} GB/s  ({phase_b.bw_samples} samples)")
    print(f"  Observation     : {phase_b.observation_window_hms}")
    print("  " + "─" * 58)
    print()
    print(f"  Results written to: test_03_{session}.jsonl")
    print("=" * 68 + "\n")


# ── Phase A ───────────────────────────────────────────────────────────────────

def run_phase_a(competitor: str, session: str, fh) -> PhaseAResult:
    counts = {k: 0 for k in EFI_WEIGHTS}
    running_efi = 0
    steps: list[PhaseAStep] = []
    t_start = time.time()

    write_event(fh, {"event": "phase_a_start", "ts": now_ts(),
                     "competitor": competitor, "session": session})

    print("\n  Phase A — Cluster Setup")
    print("  Log each action: service / cli / config / error / note")
    print("  Type 'done' when the PyTorchJob is submitted and training is running.")
    print("  Type 'help' for command reference.\n")

    while True:
        try:
            raw = input("  A> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n  Aborted.")
            sys.exit(0)

        if not raw:
            continue

        parts = raw.split(None, 1)
        cmd = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""

        if cmd == "done":
            break

        elif cmd in EFI_WEIGHTS:
            if not arg:
                print(f"  Usage: {cmd} <description>")
                continue
            pts = EFI_WEIGHTS[cmd]
            running_efi += pts
            counts[cmd] += 1
            step = PhaseAStep(
                num=len(steps) + 1,
                kind=cmd,
                efi_points=pts,
                ts=now_ts(),
                description=arg,
            )
            steps.append(step)
            write_event(fh, {"event": "phase_a_step", "ts": step.ts,
                              "competitor": competitor, "session": session,
                              "kind": cmd, "description": arg,
                              "efi_points": pts, "running_efi": running_efi,
                              "step_num": step.num})
            print(f"  [{cmd.upper()}] EFI +{pts} → {running_efi}  (step {step.num})")

        elif cmd == "note":
            if arg:
                write_event(fh, {"event": "note", "ts": now_ts(),
                                  "competitor": competitor, "session": session,
                                  "phase": "A", "value": arg})
                print(f"  [NOTE] {arg}")

        elif cmd == "status":
            print_phase_a_status(counts, running_efi, steps)

        elif cmd == "abort":
            print("  Session aborted.")
            sys.exit(0)

        elif cmd == "help":
            print("""
  Phase A commands:
    service <desc>   console / UI action           [EFI +3]
    cli     <desc>   CLI command                    [EFI +1]
    config  <desc>   config file written/edited     [EFI +2]
    error   <desc>   error or required retry        [EFI +2]
    note    <text>   free-form observation
    done             close Phase A → begin Phase B
    status           show running EFI
    abort            cancel session
""")
        else:
            print(f"  Unknown command '{cmd}'. Type 'help' for reference.")

    elapsed = time.time() - t_start
    result = PhaseAResult(
        elapsed_s=round(elapsed, 3),
        elapsed_hms=hms(elapsed),
        efi=running_efi,
        step_count=len(steps),
        counts=counts,
        steps=[asdict(s) for s in steps],
    )
    write_event(fh, {"event": "phase_a_done", "ts": now_ts(),
                     "competitor": competitor, "session": session,
                     **{k: v for k, v in asdict(result).items() if k != "steps"}})
    print(f"\n  ✓ Phase A complete — EFI={running_efi}  time={hms(elapsed)}")
    return result


# ── Phase B ───────────────────────────────────────────────────────────────────

def run_phase_b(competitor: str, session: str, fh) -> PhaseBResult:
    step_readings: list[StepReading] = []
    checkpoints: list[CheckpointEvent] = []
    failures: list[FailureEvent] = []
    mfu_readings: list[MFUReading] = []
    bw_readings: list[BWReading] = []

    current_step = 0
    last_ckpt_step = 0
    t_submit_epoch: Optional[float] = None
    t_submit_ts: Optional[str] = None
    active_failure: Optional[FailureEvent] = None

    write_event(fh, {"event": "phase_b_start", "ts": now_ts(),
                     "competitor": competitor, "session": session})

    print("\n  Phase B — Training Run")
    print("  Press Enter after 'done' in Phase A to begin.")
    print("  Commands: submit / step <n> / ckpt <n> / inject <desc> / resume <n>")
    print("            mfu <pct> / bw <GB/s> / note <text> / status / done\n")

    while True:
        try:
            raw = input("  B> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n  Aborted.")
            sys.exit(0)

        if not raw:
            continue

        parts = raw.split(None, 1)
        cmd = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""

        if cmd == "submit":
            t_submit_epoch = time.time()
            t_submit_ts = now_ts()
            write_event(fh, {"event": "job_submit", "ts": t_submit_ts,
                              "competitor": competitor, "session": session})
            print(f"  [SUBMIT] Clock started at {t_submit_ts}")

        elif cmd == "step":
            if not arg.isdigit():
                print("  Usage: step <n>")
                continue
            n = int(arg)
            current_step = n
            r = StepReading(ts=now_ts(), step=n)
            step_readings.append(r)
            write_event(fh, {"event": "step_reading", "ts": r.ts,
                              "competitor": competitor, "session": session,
                              "step": n, "sample_num": len(step_readings)})
            elapsed_str = hms(time.time() - t_submit_epoch) if t_submit_epoch else "—"
            print(f"  [STEP] step={n}  elapsed={elapsed_str}")

        elif cmd == "ckpt":
            if not arg.isdigit():
                print("  Usage: ckpt <n>")
                continue
            n = int(arg)
            last_ckpt_step = n
            evt = CheckpointEvent(ts=now_ts(), step=n)
            checkpoints.append(evt)
            write_event(fh, {"event": "checkpoint", "ts": evt.ts,
                              "competitor": competitor, "session": session,
                              "step": n, "ckpt_num": len(checkpoints)})
            print(f"  [CKPT] Checkpoint saved at step={n}")

        elif cmd == "inject":
            if not arg:
                print("  Usage: inject <description>")
                continue
            ts = now_ts()
            active_failure = FailureEvent(
                ts=ts,
                inject_step=current_step,
                description=arg,
                last_ckpt_before=last_ckpt_step,
            )
            failures.append(active_failure)
            write_event(fh, {"event": "failure_inject", "ts": ts,
                              "competitor": competitor, "session": session,
                              "failure_num": len(failures),
                              "inject_step": current_step,
                              "last_ckpt_before": last_ckpt_step,
                              "description": arg})
            print(f"  [INJECT] Failure #{len(failures)} at step={current_step}  last_ckpt={last_ckpt_step}")
            print(f"           '{arg}'")

        elif cmd == "resume":
            if not arg.isdigit():
                print("  Usage: resume <n>  (step number training resumed at)")
                continue
            if active_failure is None:
                print("  No active failure — use 'inject <desc>' first.")
                continue
            n = int(arg)
            ts = now_ts()
            mttr = (datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ") -
                    datetime.strptime(active_failure.ts, "%Y-%m-%dT%H:%M:%SZ")).total_seconds()
            loss = n - (active_failure.last_ckpt_before or 0)
            active_failure.resume_step = n
            active_failure.resume_ts = ts
            active_failure.mttr_s = round(mttr, 1)
            active_failure.checkpoint_loss = loss
            current_step = n
            write_event(fh, {"event": "failure_resume", "ts": ts,
                              "competitor": competitor, "session": session,
                              "failure_num": len(failures),
                              "resume_step": n,
                              "checkpoint_loss": loss,
                              "mttr_s": round(mttr, 1)})
            print(f"  [RESUME] Training resumed at step={n}  "
                  f"checkpoint_loss={loss} steps  MTTR={mttr:.0f}s ({mttr/60:.1f} min)")
            active_failure = None

        elif cmd == "mfu":
            try:
                val = float(arg)
            except ValueError:
                print("  Usage: mfu <0-100>")
                continue
            r = MFUReading(ts=now_ts(), value=val, sample_num=len(mfu_readings) + 1)
            mfu_readings.append(r)
            running_avg = avg([x.value for x in mfu_readings])
            write_event(fh, {"event": "mfu_reading", "ts": r.ts,
                              "competitor": competitor, "session": session,
                              "value": val, "sample_num": r.sample_num,
                              "running_avg": running_avg})
            print(f"  [MFU] {val}%  (avg={running_avg}%  n={r.sample_num})")

        elif cmd == "bw":
            try:
                val = float(arg)
            except ValueError:
                print("  Usage: bw <GB/s>")
                continue
            r = BWReading(ts=now_ts(), value_gbs=val, sample_num=len(bw_readings) + 1)
            bw_readings.append(r)
            write_event(fh, {"event": "bw_reading", "ts": r.ts,
                              "competitor": competitor, "session": session,
                              "value_gbs": val, "sample_num": r.sample_num})
            print(f"  [BW] {val} GB/s  (n={r.sample_num})")

        elif cmd == "note":
            if arg:
                write_event(fh, {"event": "note", "ts": now_ts(),
                                  "competitor": competitor, "session": session,
                                  "phase": "B", "value": arg})
                print(f"  [NOTE] {arg}")

        elif cmd == "status":
            if t_submit_epoch:
                print_phase_b_status(
                    current_step, current_step - sum(
                        f.checkpoint_loss for f in failures
                        if f.checkpoint_loss is not None),
                    failures, mfu_readings, bw_readings, t_submit_epoch)
            else:
                print("  Job not yet submitted — type 'submit' first.")

        elif cmd == "done":
            break

        elif cmd == "abort":
            print("  Session aborted.")
            sys.exit(0)

        elif cmd == "help":
            print("""
  Phase B commands:
    submit             PyTorchJob applied → starts clock
    step   <n>         current step count from training logs
    ckpt   <n>         checkpoint saved at step n
    inject <desc>      failure injected (OOM / evict / node-fail etc.)
    resume <n>         training resumed at step n after failure
    mfu    <0-100>     MFU reading from TorchTitan logs
    bw     <GB/s>      NCCL all_reduce bandwidth (GB/s)
    note   <text>      free-form observation
    done               close Phase B and write scorecard
    status             show current metrics
    abort              cancel session
""")
        else:
            print(f"  Unknown command '{cmd}'. Type 'help'.")

    # ── Compute final metrics ─────────────────────────────────────────────────
    t_end = time.time()
    elapsed_b = t_end - (t_submit_epoch or t_end)

    total_steps = current_step
    checkpoint_loss_total = sum(
        f.checkpoint_loss for f in failures if f.checkpoint_loss is not None)
    useful_steps = max(0, total_steps - checkpoint_loss_total)
    goodput_pct = round(useful_steps / total_steps * 100, 2) if total_steps > 0 else 0.0

    mttr_vals = [f.mttr_s for f in failures if f.mttr_s is not None]
    mttr_avg = round(avg(mttr_vals), 1) if mttr_vals else None

    mfu_vals = [r.value for r in mfu_readings]
    bw_vals = [r.value_gbs for r in bw_readings]

    result = PhaseBResult(
        t_submit=t_submit_ts or now_ts(),
        total_steps=total_steps,
        useful_steps=useful_steps,
        goodput_pct=goodput_pct,
        failures=[asdict(f) for f in failures],
        mttr_s=mttr_avg,
        checkpoint_loss_total=checkpoint_loss_total,
        mfu_avg=avg(mfu_vals),
        mfu_min=min(mfu_vals) if mfu_vals else None,
        mfu_max=max(mfu_vals) if mfu_vals else None,
        mfu_samples=len(mfu_vals),
        bw_avg_gbs=round(avg(bw_vals), 2) if bw_vals else None,
        bw_samples=len(bw_vals),
        observation_window_s=round(elapsed_b, 3),
        observation_window_hms=hms(elapsed_b),
    )

    write_event(fh, {"event": "phase_b_done", "ts": now_ts(),
                     "competitor": competitor, "session": session,
                     **asdict(result)})
    return result


# ── Entry point ───────────────────────────────────────────────────────────────

def run(
    competitor: str,
    session: str = "",
    nodes: int = 2,
    kubeconfig: str = None,
) -> None:
    ts_start = now_ts()
    t_start_epoch = time.time()

    if not session:
        session = f"{competitor}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"

    out_dir = os.path.join(
        os.path.dirname(__file__), "..", "..", "results", "raw")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"test_03_{competitor}_{session}.jsonl")

    print(f"\n{'='*68}")
    print(f"  TEST 3 — Goodput Stability Test")
    print(f"  Competitor : {competitor.upper()}")
    print(f"  Nodes      : {nodes} × B300 (288 GB SXM)")
    print(f"  Session    : {session}")
    print(f"  Output     : test_03_{competitor}_{session}.jsonl")
    print(f"{'='*68}\n")

    with open(out_path, "w") as fh:
        write_event(fh, {"event": "start", "ts": ts_start,
                         "competitor": competitor, "session": session,
                         "nodes": nodes})

        phase_a = run_phase_a(competitor, session, fh)

        input("\n  Phase A complete. Press Enter to begin Phase B → ")

        phase_b = run_phase_b(competitor, session, fh)

    t_end_epoch = time.time()
    total_elapsed = t_end_epoch - t_start_epoch

    summary = {
        "event": "_summary",
        "competitor": competitor,
        "session": session,
        "started_at": ts_start,
        "finished_at": now_ts(),
        "total_elapsed_s": round(total_elapsed, 3),
        "total_elapsed_hms": hms(total_elapsed),
        "phase_a": asdict(phase_a) if not isinstance(phase_a, dict) else phase_a,
        "phase_b": asdict(phase_b) if not isinstance(phase_b, dict) else phase_b,
    }
    # Re-open to append summary (keeps JSONL valid even if crash mid-session)
    with open(out_path, "a") as fh:
        write_event(fh, summary)

    print_scorecard(phase_a, phase_b, session)
