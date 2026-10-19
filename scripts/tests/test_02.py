"""
Test 2: Managed Kubernetes Provisioning Speed & GPU Utilization Efficiency
Focus: Cluster Setup Speed and Steady-State GPU MFU
Hardware: A100 / H100 / L40S
Competitors: aws, crusoe, nebius

Two-phase structure:

  Phase A — Setup
    From a clean account, deploy a GPU-enabled Kubernetes cluster configured
    for the benchmark training workload. The engineer logs every action as it
    happens: CLI commands, console/UI steps, config files written, and errors.
    The harness timestamps each step and computes a live EFI score.

    EFI (Engineering Friction Index):
        EFI = (services × 3) + (config_files × 2) + (cli_commands × 1) + (errors × 2)

  Phase B — Workload
    Submit the standard training job and measure:
        time_to_first_gpu   — job submit → first confirmed GPU training step
        mfu                 — GPU Model FLOP Utilization % during steady-state
                              (target: ≥ 4 hours of readings)
        scale_latency       — wall-clock seconds from scale trigger to new node
                              ready (optional; only if scale-out is tested)

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
    submit                   job submitted (kubectl apply)      → starts TTFG clock
    active                   first GPU step confirmed in logs   → stops TTFG clock
    mfu     <0–100>          log one MFU reading (enter repeatedly)
    scale-start              node scale-out triggered
    scale-done               new node Ready; scale-out complete → logs latency
    note    <text>           free-form observation
    done                     close Phase B and write scorecard
    status                   show Phase B metrics so far
    help                     show this message
    abort                    cancel session

Usage (via harness.py):
    python harness.py --test 2 --competitor crusoe
    python harness.py --test 2 --competitor aws
    python harness.py --test 2 --competitor nebius

Output:
    results/raw/test_02_{competitor}_{session}.jsonl
"""

import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

try:
    import readline
except ImportError:
    pass

_RAW = Path(__file__).parent.parent.parent / "results" / "raw"
_RAW.mkdir(parents=True, exist_ok=True)

# ── EFI weights ────────────────────────────────────────────────────────────────

EFI_WEIGHTS = {
    "service": 3,
    "config":  2,
    "error":   2,
    "cli":     1,
}

# ── Data structures ────────────────────────────────────────────────────────────

@dataclass
class PhaseAStep:
    kind: str       # service | cli | config | error
    description: str
    ts: datetime
    efi_points: int


@dataclass
class MFUReading:
    value: float    # 0–100
    ts: datetime


@dataclass
class ScaleEvent:
    t_start: Optional[datetime] = None
    t_done:  Optional[datetime] = None

    @property
    def latency_s(self) -> Optional[float]:
        if self.t_start and self.t_done:
            return (self.t_done - self.t_start).total_seconds()
        return None


@dataclass
class PhaseAResult:
    steps: list = field(default_factory=list)   # list[PhaseAStep]
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None

    @property
    def elapsed_s(self) -> Optional[float]:
        if self.started_at and self.finished_at:
            return (self.finished_at - self.started_at).total_seconds()
        return None

    @property
    def efi(self) -> int:
        return sum(s.efi_points for s in self.steps)

    def count(self, kind: str) -> int:
        return sum(1 for s in self.steps if s.kind == kind)


@dataclass
class PhaseBResult:
    t_submit:   Optional[datetime] = None
    t_active:   Optional[datetime] = None
    mfu_readings: list = field(default_factory=list)   # list[MFUReading]
    scale_events: list = field(default_factory=list)   # list[ScaleEvent]
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None

    @property
    def time_to_first_gpu_s(self) -> Optional[float]:
        if self.t_submit and self.t_active:
            return (self.t_active - self.t_submit).total_seconds()
        return None

    @property
    def mfu_avg(self) -> Optional[float]:
        if not self.mfu_readings:
            return None
        return sum(r.value for r in self.mfu_readings) / len(self.mfu_readings)

    @property
    def mfu_min(self) -> Optional[float]:
        return min((r.value for r in self.mfu_readings), default=None)

    @property
    def mfu_max(self) -> Optional[float]:
        return max((r.value for r in self.mfu_readings), default=None)

    @property
    def observation_window_s(self) -> Optional[float]:
        if len(self.mfu_readings) >= 2:
            return (self.mfu_readings[-1].ts - self.mfu_readings[0].ts).total_seconds()
        return None


@dataclass
class Session:
    competitor: str
    session_id: str
    started_at: datetime
    events: list = field(default_factory=list)
    phase_a: Optional[PhaseAResult] = None
    phase_b: Optional[PhaseBResult] = None

    def elapsed_hms(self) -> str:
        delta = datetime.now(timezone.utc) - self.started_at
        return _hms(int(delta.total_seconds()))


# ── Helpers ────────────────────────────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _ts(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _hms(total_seconds: int) -> str:
    h, rem = divmod(total_seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _log(session: Session, path: Path, event_type: str, **kwargs) -> dict:
    record = {
        "event": event_type,
        "ts": _ts(_now()),
        "competitor": session.competitor,
        "session": session.session_id,
        **kwargs,
    }
    session.events.append(record)
    with open(path, "a") as f:
        f.write(json.dumps(record) + "\n")
    return record


# ── Help text ──────────────────────────────────────────────────────────────────

HELP_A = """
  ┌─ Phase A Commands (Setup) ────────────────────────────────────────────────┐
  │  service <description>    console / UI action               [EFI +3]     │
  │  cli     <description>    CLI command executed              [EFI +1]     │
  │  config  <description>    config file written or edited     [EFI +2]     │
  │  error   <description>    error encountered / retry needed  [EFI +2]     │
  │  note    <text>           free-form observation (no EFI)                 │
  │  status                   show running EFI and step counts               │
  │  done                     mark Phase A complete → begin Phase B          │
  │  help                     show this message                              │
  │  abort                    cancel session                                 │
  └───────────────────────────────────────────────────────────────────────────┘
"""

HELP_B = """
  ┌─ Phase B Commands (Workload) ─────────────────────────────────────────────┐
  │  submit                   job submitted (kubectl apply)                  │
  │  active                   first GPU step confirmed in logs               │
  │  mfu     <0–100>          log one GPU MFU % reading                     │
  │  scale-start              node scale-out triggered                       │
  │  scale-done               new node Ready; scale-out complete             │
  │  note    <text>           free-form observation                          │
  │  status                   show Phase B metrics so far                   │
  │  done                     close Phase B and write scorecard              │
  │  help                     show this message                              │
  │  abort                    cancel session                                 │
  └───────────────────────────────────────────────────────────────────────────┘
"""


# ── Status displays ────────────────────────────────────────────────────────────

def _print_status_a(session: Session, result: PhaseAResult) -> None:
    print()
    print(f"  ┌─ Phase A — Setup Progress ────────────────────────────────────────")
    print(f"  │  Elapsed   : {session.elapsed_hms()}")
    print(f"  │  Steps     : {len(result.steps)}")
    print(f"  │  Services  : {result.count('service')}  × 3 = {result.count('service') * 3}")
    print(f"  │  Config    : {result.count('config')}  × 2 = {result.count('config') * 2}")
    print(f"  │  CLI cmds  : {result.count('cli')}  × 1 = {result.count('cli') * 1}")
    print(f"  │  Errors    : {result.count('error')}  × 2 = {result.count('error') * 2}")
    print(f"  │  {'─' * 50}")
    print(f"  │  EFI so far: {result.efi}")
    print(f"  └───────────────────────────────────────────────────────────────────")
    print()


def _print_status_b(session: Session, result: PhaseBResult) -> None:
    print()
    print(f"  ┌─ Phase B — Workload Progress ─────────────────────────────────────")
    if result.t_submit:
        print(f"  │  Submitted : {_ts(result.t_submit)}")
    else:
        print(f"  │  Submitted : —")
    if result.t_active:
        ttfg = result.time_to_first_gpu_s
        print(f"  │  GPU Active: {_ts(result.t_active)}  (TTFG: {ttfg:.0f}s / {ttfg/60:.1f} min)")
    else:
        print(f"  │  GPU Active: —")
    n = len(result.mfu_readings)
    if n:
        win = result.observation_window_s
        win_str = _hms(int(win)) if win else "—"
        print(f"  │  MFU       : avg={result.mfu_avg:.1f}%  min={result.mfu_min:.1f}%  "
              f"max={result.mfu_max:.1f}%  n={n}  window={win_str}")
    else:
        print(f"  │  MFU       : no readings yet")
    for i, se in enumerate(result.scale_events):
        lat = f"{se.latency_s:.0f}s" if se.latency_s else "in progress"
        print(f"  │  Scale #{i+1} : {lat}")
    print(f"  │  Elapsed   : {session.elapsed_hms()}")
    print(f"  └───────────────────────────────────────────────────────────────────")
    print()


# ── Phase A runner ─────────────────────────────────────────────────────────────

def _run_phase_a(session: Session, path: Path) -> PhaseAResult:
    result = PhaseAResult(started_at=_now())
    _log(session, path, "phase_a_start")

    print()
    print(f"  {'═' * 64}")
    print(f"  PHASE A — SETUP")
    print(f"  {'─' * 64}")
    print(f"  Log every action as you perform it. Use the command that")
    print(f"  matches the type of action:")
    print(f"")
    print(f"    service  — opening a console page, using a UI form")
    print(f"    cli      — running a CLI command in the terminal")
    print(f"    config   — writing or editing a config/YAML file")
    print(f"    error    — an error, failed command, or required retry")
    print(f"")
    print(f"  Type 'done' when the cluster is provisioned and GPU nodes")
    print(f"  are verified schedulable. Type 'help' for full command list.")
    print(HELP_A)

    while True:
        try:
            raw = input("  [phase-a] > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n  Session interrupted.")
            sys.exit(1)

        if not raw:
            continue

        parts = raw.split(None, 1)
        cmd = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""

        if cmd == "help":
            print(HELP_A)
            continue

        if cmd == "status":
            _print_status_a(session, result)
            continue

        if cmd == "note":
            if not arg:
                print("  Usage: note <text>")
                continue
            _log(session, path, "note", phase="A", value=arg)
            print(f"  📝 Note logged.")
            continue

        if cmd == "abort":
            try:
                confirm = input("  Abort? Session will end. (y/N): ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                confirm = "n"
            if confirm == "y":
                print("  Session aborted.")
                sys.exit(0)
            continue

        if cmd == "done":
            result.finished_at = _now()
            elapsed = result.elapsed_s
            _log(session, path, "phase_a_done",
                 elapsed_s=elapsed,
                 efi=result.efi,
                 step_count=len(result.steps),
                 counts={k: result.count(k) for k in EFI_WEIGHTS})
            print(f"\n  ✅ Phase A complete — {_hms(int(elapsed))} elapsed  ·  EFI: {result.efi}")
            print(f"     {result.count('service')} services  ·  {result.count('config')} configs  "
                  f"·  {result.count('cli')} CLI cmds  ·  {result.count('error')} errors")
            print()
            return result

        if cmd in EFI_WEIGHTS:
            if not arg:
                print(f"  Usage: {cmd} <description>")
                continue
            pts = EFI_WEIGHTS[cmd]
            step = PhaseAStep(kind=cmd, description=arg, ts=_now(), efi_points=pts)
            result.steps.append(step)
            _log(session, path, "phase_a_step",
                 kind=cmd, description=arg, efi_points=pts,
                 running_efi=result.efi, step_num=len(result.steps))
            icon = {"service": "🖥 ", "cli": "⚡", "config": "📄", "error": "❌"}[cmd]
            print(f"  {icon}  [{cmd.upper():7s} +{pts}]  {arg}   (EFI: {result.efi})")
            continue

        print(f"  Unknown command '{cmd}'. Type 'help' for options.")


# ── Phase B runner ─────────────────────────────────────────────────────────────

def _run_phase_b(session: Session, path: Path) -> PhaseBResult:
    result = PhaseBResult(started_at=_now())
    _log(session, path, "phase_b_start")

    print()
    print(f"  {'═' * 64}")
    print(f"  PHASE B — WORKLOAD")
    print(f"  {'─' * 64}")
    print(f"  1. Submit the training job ('submit' after kubectl apply)")
    print(f"  2. Log 'active' when first GPU step appears in pod logs")
    print(f"  3. Enter MFU readings throughout the steady-state window")
    print(f"     (target: at least 4 hours / ~16 readings at 15-min intervals)")
    print(f"  4. Log scale events if node scale-out is being tested")
    print(f"  5. Type 'done' to close Phase B and write the scorecard")
    print(HELP_B)

    active_scale: Optional[ScaleEvent] = None

    while True:
        try:
            raw = input("  [phase-b] > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n  Session interrupted.")
            sys.exit(1)

        if not raw:
            continue

        parts = raw.split(None, 1)
        cmd = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""

        if cmd == "help":
            print(HELP_B)
            continue

        if cmd == "status":
            _print_status_b(session, result)
            continue

        if cmd == "note":
            if not arg:
                print("  Usage: note <text>")
                continue
            _log(session, path, "note", phase="B", value=arg)
            print(f"  📝 Note logged.")
            continue

        if cmd == "abort":
            try:
                confirm = input("  Abort? Session will end. (y/N): ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                confirm = "n"
            if confirm == "y":
                print("  Session aborted.")
                sys.exit(0)
            continue

        if cmd == "submit":
            if result.t_submit:
                print("  Job already marked as submitted.")
                continue
            result.t_submit = _now()
            _log(session, path, "job_submit")
            print(f"  ⚡ Job submitted at {_ts(result.t_submit)}")
            print(f"  Watch pod logs: kubectl logs -f <pod> -n training")
            print(f"  Type 'active' when you see the first training step.")
            continue

        if cmd == "active":
            if not result.t_submit:
                print("  Log 'submit' first.")
                continue
            if result.t_active:
                print("  GPU already marked active.")
                continue
            result.t_active = _now()
            ttfg = result.time_to_first_gpu_s
            _log(session, path, "gpu_active", time_to_first_gpu_s=ttfg)
            print(f"  🟢 First GPU step confirmed — "
                  f"TTFG: {ttfg:.0f}s ({ttfg / 60:.1f} min)")
            print(f"  Now enter MFU readings as you collect them.")
            print(f"  Usage: mfu <value>  (e.g. 'mfu 47.3')")
            continue

        if cmd == "mfu":
            try:
                val = float(arg)
                if not (0 <= val <= 100):
                    raise ValueError
            except (ValueError, TypeError):
                print("  Usage: mfu <0–100>   (e.g. 'mfu 47.3')")
                continue
            reading = MFUReading(value=val, ts=_now())
            result.mfu_readings.append(reading)
            _log(session, path, "mfu_reading",
                 value=val, sample_num=len(result.mfu_readings),
                 running_avg=round(result.mfu_avg, 2))
            n = len(result.mfu_readings)
            win = result.observation_window_s
            win_str = _hms(int(win)) if win else "—"
            print(f"  📊 MFU {val:.1f}%  (n={n}  avg={result.mfu_avg:.1f}%  window={win_str})")
            if n == 1:
                print(f"  Target: ≥16 readings over ≥4 hours for steady-state analysis.")
            continue

        if cmd == "scale-start":
            if active_scale:
                print("  A scale event is already in progress. Log 'scale-done' first.")
                continue
            active_scale = ScaleEvent(t_start=_now())
            result.scale_events.append(active_scale)
            _log(session, path, "scale_start", event_num=len(result.scale_events))
            print(f"  📈 Scale-out started. Type 'scale-done' when new node is Ready.")
            continue

        if cmd == "scale-done":
            if not active_scale or active_scale.t_done:
                print("  No active scale event. Use 'scale-start' first.")
                continue
            active_scale.t_done = _now()
            lat = active_scale.latency_s
            _log(session, path, "scale_done",
                 event_num=len(result.scale_events), latency_s=lat)
            print(f"  ✅ Scale-out complete — latency: {lat:.0f}s ({lat / 60:.1f} min)")
            active_scale = None
            continue

        if cmd == "done":
            n_mfu = len(result.mfu_readings)
            if n_mfu < 4:
                print(f"  Only {n_mfu} MFU reading(s) logged. Minimum recommended is 16.")
                try:
                    confirm = input("  Close Phase B anyway? (y/N): ").strip().lower()
                except (EOFError, KeyboardInterrupt):
                    confirm = "n"
                if confirm != "y":
                    continue
            result.finished_at = _now()
            _log(session, path, "phase_b_done",
                 time_to_first_gpu_s=result.time_to_first_gpu_s,
                 mfu_avg=round(result.mfu_avg, 2) if result.mfu_avg else None,
                 mfu_min=result.mfu_min,
                 mfu_max=result.mfu_max,
                 mfu_samples=n_mfu,
                 observation_window_s=result.observation_window_s,
                 scale_events=[
                     {"latency_s": se.latency_s}
                     for se in result.scale_events
                 ])
            print(f"\n  ✅ Phase B complete.")
            return result

        print(f"  Unknown command '{cmd}'. Type 'help' for options.")


# ── Final scorecard ────────────────────────────────────────────────────────────

def _write_summary(session: Session, pa: PhaseAResult, pb: PhaseBResult, path: Path) -> None:
    finished_at = _now()
    total_s = (finished_at - session.started_at).total_seconds()

    ttfg = pb.time_to_first_gpu_s
    win  = pb.observation_window_s

    summary = {
        "event":       "_summary",
        "competitor":  session.competitor,
        "session":     session.session_id,
        "started_at":  _ts(session.started_at),
        "finished_at": _ts(finished_at),
        "total_elapsed_s":   total_s,
        "total_elapsed_hms": _hms(int(total_s)),
        "phase_a": {
            "elapsed_s":   pa.elapsed_s,
            "elapsed_hms": _hms(int(pa.elapsed_s)) if pa.elapsed_s else None,
            "efi":         pa.efi,
            "step_count":  len(pa.steps),
            "counts": {k: pa.count(k) for k in EFI_WEIGHTS},
            "steps": [
                {
                    "num":        i + 1,
                    "kind":       s.kind,
                    "efi_points": s.efi_points,
                    "ts":         _ts(s.ts),
                    "description": s.description,
                }
                for i, s in enumerate(pa.steps)
            ],
        },
        "phase_b": {
            "t_submit":            _ts(pb.t_submit)  if pb.t_submit  else None,
            "t_active":            _ts(pb.t_active)  if pb.t_active  else None,
            "time_to_first_gpu_s": ttfg,
            "time_to_first_gpu_min": round(ttfg / 60, 2) if ttfg else None,
            "mfu_avg":             round(pb.mfu_avg, 2) if pb.mfu_avg else None,
            "mfu_min":             pb.mfu_min,
            "mfu_max":             pb.mfu_max,
            "mfu_samples":         len(pb.mfu_readings),
            "observation_window_s": win,
            "observation_window_hms": _hms(int(win)) if win else None,
            "mfu_readings": [
                {"ts": _ts(r.ts), "value": r.value}
                for r in pb.mfu_readings
            ],
            "scale_events": [
                {
                    "t_start":    _ts(se.t_start) if se.t_start else None,
                    "t_done":     _ts(se.t_done)  if se.t_done  else None,
                    "latency_s":  se.latency_s,
                }
                for se in pb.scale_events
            ],
        },
    }

    with open(path, "a") as f:
        f.write(json.dumps(summary) + "\n")

    # ── Print scorecard ────────────────────────────────────────────────────────
    W = 68
    print()
    print("=" * W)
    print(f"  TEST 2 RESULTS — {session.competitor.upper()}")
    print("=" * W)

    print(f"\n  PHASE A — SETUP")
    print(f"  {'─' * 62}")
    elapsed_a = _hms(int(pa.elapsed_s)) if pa.elapsed_s else "—"
    print(f"  Setup time      : {elapsed_a}")
    print(f"  Total steps     : {len(pa.steps)}")
    print(f"  Services (×3)   : {pa.count('service'):3d}   → {pa.count('service') * 3:3d} pts")
    print(f"  Config files(×2): {pa.count('config'):3d}   → {pa.count('config') * 2:3d} pts")
    print(f"  CLI commands(×1): {pa.count('cli'):3d}   → {pa.count('cli') * 1:3d} pts")
    print(f"  Errors (×2)     : {pa.count('error'):3d}   → {pa.count('error') * 2:3d} pts")
    print(f"  {'─' * 62}")
    print(f"  EFI             : {pa.efi}")

    print(f"\n  PHASE B — WORKLOAD")
    print(f"  {'─' * 62}")
    ttfg_str = (f"{ttfg:.0f}s ({ttfg/60:.1f} min)" if ttfg else "—")
    print(f"  Time to first GPU  : {ttfg_str}")
    if pb.mfu_avg is not None:
        win_str = _hms(int(win)) if win else "—"
        print(f"  MFU avg            : {pb.mfu_avg:.1f}%")
        print(f"  MFU min / max      : {pb.mfu_min:.1f}% / {pb.mfu_max:.1f}%")
        print(f"  MFU samples        : {len(pb.mfu_readings)}")
        print(f"  Observation window : {win_str}")
    else:
        print(f"  MFU                : no readings")
    if pb.scale_events:
        for i, se in enumerate(pb.scale_events):
            lat_str = f"{se.latency_s:.0f}s ({se.latency_s/60:.1f} min)" if se.latency_s else "incomplete"
            print(f"  Scale-out #{i+1}       : {lat_str}")
    else:
        print(f"  Scale-out          : not tested")

    print(f"\n  Session duration  : {_hms(int(total_s))}")
    print(f"  Results written to: {path.name}")
    print("=" * W)
    print()


# ── Entry point ────────────────────────────────────────────────────────────────

def run(
    competitor: str,
    session: str,
    nodes: int = 2,
    kubeconfig: str = None,
) -> None:
    """
    Two-phase interactive harness for Test 2.
    Phase A: engineer logs setup steps live as they provision the cluster.
    Phase B: engineer logs job submission, MFU readings, and scale events.
    """
    output_path = _RAW / f"test_02_{competitor}_{session}.jsonl"

    sess = Session(
        competitor=competitor,
        session_id=session,
        started_at=_now(),
    )

    _log(sess, output_path, "start", nodes=nodes)

    print(f"\n  Output → {output_path}")
    print(f"\n  Test 2: Managed Kubernetes Provisioning Speed & GPU Utilization")
    print(f"  Platform : {competitor.upper()}")
    print(f"  Nodes    : {nodes}")
    print(f"\n  This test has two phases:")
    print(f"    Phase A  — log every setup action live as you provision the cluster")
    print(f"    Phase B  — submit the training job and collect MFU readings")
    print(f"\n  Press Enter to begin Phase A.")
    try:
        input("  > ")
    except (EOFError, KeyboardInterrupt):
        sys.exit(0)

    phase_a = _run_phase_a(sess, output_path)
    sess.phase_a = phase_a

    print(f"\n  Phase A done. Press Enter to begin Phase B.")
    try:
        input("  > ")
    except (EOFError, KeyboardInterrupt):
        sys.exit(0)

    phase_b = _run_phase_b(sess, output_path)
    sess.phase_b = phase_b

    _write_summary(sess, phase_a, phase_b, output_path)
