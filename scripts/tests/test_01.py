"""
Test 1: Managed Orchestration — CMK + Command Center vs. Native Tooling
Operational Burden & Observability

Runs the same training workload with injected failures on Crusoe CMK,
AWS EKS, and Nebius Kubernetes. Measures how quickly each platform
surfaces problems and how much human effort is required to resolve them.

Failure scenarios (all platforms):
    F1 — OOM Kill          GPU memory exhaustion → pod crash
    F2 — Pod Eviction      Pod deleted → reschedule + recovery
    F3 — Silent GPU Hang   GPU goes idle (no Kubernetes event) → hang detection
    F4 — Node Cordon       Node marked unschedulable → pod rescheduled

Per-failure metrics:
    alert_lag    — seconds from inject to first platform-surfaced alert
    ttr          — seconds from inject to workload resumption
    manual_steps — count of operator actions between action and recover

Operational Burden Score (OBS):
    OBS = Σ(manual_steps × 1) + Σ(alert_lag_minutes × 2)
    No-alert penalty: 60 min added per failure with no automated alert

Decision-maker translation:
    annual_eng_hours = total_manual_steps × 3 min × incident_rate_weekly × 52 / 60
    (default incident_rate_weekly = 2)

Usage (via harness.py):
    python harness.py --test 1 --competitor crusoe
    python harness.py --test 1 --competitor aws
    python harness.py --test 1 --competitor nebius

Output:
    results/raw/test_01_{competitor}_{session}.jsonl
"""

import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

try:
    import readline  # enables arrow-key history in interactive prompt
except ImportError:
    pass  # Windows — graceful degradation

_RAW = Path(__file__).parent.parent.parent / "results" / "raw"
_RAW.mkdir(parents=True, exist_ok=True)

# ── Failure scenarios ──────────────────────────────────────────────────────────

FAILURES = [
    (
        "F1",
        "OOM Kill",
        "GPU memory exhaustion → pod crash → Kubernetes restart event",
        "Run inject_f1_oom.sh, then type 'inject F1' to start the clock.",
    ),
    (
        "F2",
        "Pod Eviction",
        "Pod forcibly deleted → reschedule and resume training",
        "Run inject_f2_evict.sh, then type 'inject F2' to start the clock.",
    ),
    (
        "F3",
        "Silent GPU Hang",
        "GPU goes idle with no Kubernetes event — tests monitoring sensitivity",
        "Run inject_f3_hang.sh, then type 'inject F3' to start the clock.",
    ),
    (
        "F4",
        "Node Cordon",
        "Node marked unschedulable + pod evicted → reschedule to new node",
        "Run inject_f4_cordon.sh, then type 'inject F4' to start the clock.",
    ),
]

FAILURE_IDS = {f[0] for f in FAILURES}

# ── Scoring constants ──────────────────────────────────────────────────────────

OBS_WEIGHTS = {
    "step":          1,   # per manual remediation step
    "alert_lag_min": 2,   # per minute of alert lag
}
NO_ALERT_PENALTY_MIN = 60   # OBS penalty when platform never surfaced an alert
STEP_MINUTES         = 3    # assumed engineering minutes per manual step
INCIDENT_RATE_WEEKLY = 2    # assumed incidents per week for annual projection

# ── Help text ──────────────────────────────────────────────────────────────────

HELP_TEXT = """
  ┌─ Commands ──────────────────────────────────────────────────────────────┐
  │  inject <F1|F2|F3|F4>   — failure injected; clock starts               │
  │  alert  <F1|F2|F3|F4>   — platform surfaced alert (no kubectl needed)  │
  │  no-alert               — platform never surfaced alert; log as miss    │
  │  action <F1|F2|F3|F4>   — operator begins remediation                  │
  │  step   <description>   — manual remediation step            [OBS +1]  │
  │  recover <F1|F2|F3|F4>  — workload running again; clock stops          │
  │  no-recover             — workload did not recover in observation       │
  │  note   <text>          — free-form observation (no score impact)       │
  │  status                 — show running OBS scores per failure           │
  │  help                   — show this message                             │
  │  abort                  — cancel session                                │
  └─────────────────────────────────────────────────────────────────────────┘
"""

# ── Data structures ────────────────────────────────────────────────────────────

@dataclass
class FailureResult:
    failure_id: str
    label: str
    description: str
    t_inject:  Optional[datetime] = None
    t_alert:   Optional[datetime] = None
    t_action:  Optional[datetime] = None
    t_recover: Optional[datetime] = None
    alert_surfaced:   bool = True   # False → no-alert logged
    recover_achieved: bool = True   # False → no-recover logged
    manual_steps: list = field(default_factory=list)
    notes: list = field(default_factory=list)

    @property
    def alert_lag_s(self) -> Optional[float]:
        if self.t_inject and self.t_alert:
            return (self.t_alert - self.t_inject).total_seconds()
        return None

    @property
    def alert_lag_min(self) -> Optional[float]:
        s = self.alert_lag_s
        return s / 60.0 if s is not None else None

    @property
    def ttr_s(self) -> Optional[float]:
        if self.t_inject and self.t_recover:
            return (self.t_recover - self.t_inject).total_seconds()
        return None

    @property
    def obs_contribution(self) -> float:
        step_score = len(self.manual_steps) * OBS_WEIGHTS["step"]
        if not self.alert_surfaced:
            lag_score = NO_ALERT_PENALTY_MIN * OBS_WEIGHTS["alert_lag_min"]
        elif self.alert_lag_min is not None:
            lag_score = self.alert_lag_min * OBS_WEIGHTS["alert_lag_min"]
        else:
            lag_score = 0.0
        return step_score + lag_score


@dataclass
class Session:
    competitor: str
    session_id: str
    started_at: datetime
    events: list = field(default_factory=list)
    failure_results: list = field(default_factory=list)

    @property
    def obs_score(self) -> float:
        return sum(r.obs_contribution for r in self.failure_results)

    @property
    def total_manual_steps(self) -> int:
        return sum(len(r.manual_steps) for r in self.failure_results)

    @property
    def annual_eng_hours(self) -> float:
        return (self.total_manual_steps * STEP_MINUTES * INCIDENT_RATE_WEEKLY * 52) / 60.0

    def elapsed_hms(self) -> str:
        delta = datetime.now(timezone.utc) - self.started_at
        h, rem = divmod(int(delta.total_seconds()), 3600)
        m, s = divmod(rem, 60)
        return f"{h:02d}:{m:02d}:{s:02d}"


# ── Helpers ────────────────────────────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _ts(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _hms(total_seconds: float) -> str:
    h, rem = divmod(int(total_seconds), 3600)
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


# ── Status display ─────────────────────────────────────────────────────────────

def _print_status(session: Session, current: Optional[FailureResult] = None) -> None:
    print()
    print(f"  ┌─ OBS Running Scores ───────────────────────────────────────────")
    for r in session.failure_results:
        lag_str = (
            f"{r.alert_lag_min:.1f} min" if r.alert_lag_min is not None
            else ("NO-ALERT" if not r.alert_surfaced else "—")
        )
        ttr_str = _hms(r.ttr_s) if r.ttr_s else ("NO-REC" if not r.recover_achieved else "—")
        print(f"  │  {r.failure_id:<4}  steps={len(r.manual_steps):2d}  "
              f"alert_lag={lag_str:<12}  ttr={ttr_str:<10}  obs={r.obs_contribution:5.1f}")
    if current and current not in session.failure_results:
        print(f"  │  {current.failure_id:<4}  (in progress)")
    print(f"  │  {'─' * 56}")
    print(f"  │  OBS TOTAL  {session.obs_score:6.1f}     "
          f"Steps: {session.total_manual_steps}     "
          f"Elapsed: {session.elapsed_hms()}")
    print(f"  └───────────────────────────────────────────────────────────────")
    print()


# ── Per-failure runner ─────────────────────────────────────────────────────────

def _run_failure(idx: int, session: Session, path: Path) -> FailureResult:
    failure_id, label, description, inject_hint = FAILURES[idx]
    result = FailureResult(
        failure_id=failure_id,
        label=label,
        description=description,
    )

    print()
    print(f"  {'═' * 64}")
    print(f"  FAILURE {idx + 1} of {len(FAILURES)}  —  {failure_id}: {label}")
    print(f"  {description}")
    print(f"  {'─' * 64}")
    print(f"  {inject_hint}")
    print(f"  Elapsed so far: {session.elapsed_hms()}")
    print()

    _log(session, path, "failure_start", failure=failure_id, failure_idx=idx)

    phase = "INJECT"   # INJECT → ALERT → ACTION → RECOVER → DONE

    while phase != "DONE":
        phase_label = {
            "INJECT":  f"[{failure_id} · inject]",
            "ALERT":   f"[{failure_id} · watching]",
            "ACTION":  f"[{failure_id} · action]",
            "RECOVER": f"[{failure_id} · recover]",
        }.get(phase, ">")

        try:
            raw = input(f"  {phase_label} > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n  Session interrupted.")
            sys.exit(1)

        if not raw:
            continue

        parts = raw.split(None, 1)
        cmd = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""

        # ── Always-available ───────────────────────────────────────────────────
        if cmd == "note":
            if not arg:
                print("  Usage: note <text>")
                continue
            result.notes.append(arg)
            _log(session, path, "note", failure=failure_id, phase=phase, value=arg)
            print(f"  📝 Note logged.")
            continue

        if cmd == "status":
            _print_status(session, result)
            continue

        if cmd == "help":
            print(HELP_TEXT)
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

        # ── INJECT ─────────────────────────────────────────────────────────────
        if phase == "INJECT":
            if cmd == "inject":
                fid = arg.upper() if arg else failure_id
                if fid != failure_id:
                    print(f"  Expected 'inject {failure_id}' — got '{fid}'")
                    continue
                result.t_inject = _now()
                _log(session, path, "inject", failure=failure_id)
                print(f"\n  ⚡ {failure_id} injected at {_ts(result.t_inject)}")
                print(f"  Watch the monitoring UI only — no kubectl yet.")
                print(f"  Type 'alert {failure_id}' when the platform surfaces the issue.")
                print(f"  Type 'no-alert' if nothing fires.")
                print()
                phase = "ALERT"
            else:
                print(f"  Type 'inject {failure_id}' after running the injection script.")

        # ── ALERT ──────────────────────────────────────────────────────────────
        elif phase == "ALERT":
            if cmd == "alert":
                fid = arg.upper() if arg else failure_id
                if fid != failure_id:
                    print(f"  Expected 'alert {failure_id}'")
                    continue
                result.t_alert = _now()
                result.alert_surfaced = True
                lag_s = result.alert_lag_s
                _log(session, path, "alert", failure=failure_id, alert_lag_s=lag_s)
                print(f"\n  🔔 Alert recorded — lag: {lag_s:.0f}s ({lag_s / 60:.1f} min)")
                print(f"  Type 'action {failure_id}' when you begin remediation.")
                print()
                phase = "ACTION"

            elif cmd == "no-alert":
                result.alert_surfaced = False
                _log(session, path, "no_alert", failure=failure_id,
                     penalty_min=NO_ALERT_PENALTY_MIN)
                print(f"\n  ⚠️  No-alert — platform did not surface {failure_id} automatically.")
                print(f"  OBS penalty: {NO_ALERT_PENALTY_MIN} min × {OBS_WEIGHTS['alert_lag_min']}"
                      f" = {NO_ALERT_PENALTY_MIN * OBS_WEIGHTS['alert_lag_min']} pts")
                print(f"  Type 'action {failure_id}' to begin manual investigation.")
                print()
                phase = "ACTION"

            else:
                print(f"  Watching for platform alert. "
                      f"Type 'alert {failure_id}' or 'no-alert'.")

        # ── ACTION ─────────────────────────────────────────────────────────────
        elif phase == "ACTION":
            if cmd == "action":
                fid = arg.upper() if arg else failure_id
                if fid != failure_id:
                    print(f"  Expected 'action {failure_id}'")
                    continue
                result.t_action = _now()
                _log(session, path, "action", failure=failure_id)
                print(f"\n  🛠  Remediation started.")
                print(f"  Log each manual step: 'step <description>'  [OBS +{OBS_WEIGHTS['step']} each]")
                print(f"  Type 'recover {failure_id}' when training output resumes.")
                print()
                phase = "RECOVER"
            else:
                print(f"  Type 'action {failure_id}' to mark the start of remediation.")

        # ── RECOVER ────────────────────────────────────────────────────────────
        elif phase == "RECOVER":
            if cmd == "step":
                if not arg:
                    print("  Usage: step <description>")
                    continue
                result.manual_steps.append(arg)
                _log(session, path, "step", failure=failure_id, value=arg,
                     step_num=len(result.manual_steps))
                print(f"  ✓  Step {len(result.manual_steps)} logged  [OBS +{OBS_WEIGHTS['step']}]")

            elif cmd == "recover":
                fid = arg.upper() if arg else failure_id
                if fid != failure_id:
                    print(f"  Expected 'recover {failure_id}'")
                    continue
                result.t_recover = _now()
                result.recover_achieved = True
                ttr = result.ttr_s
                _log(session, path, "recover", failure=failure_id,
                     ttr_s=ttr, manual_step_count=len(result.manual_steps))
                print(f"\n  ✅ {failure_id} recovered")
                print(f"     TTR:          {_hms(ttr)}")
                print(f"     Alert lag:    "
                      + (f"{result.alert_lag_min:.1f} min" if result.alert_lag_min else "NO-ALERT"))
                print(f"     Manual steps: {len(result.manual_steps)}")
                print(f"     OBS this failure: {result.obs_contribution:.1f}")
                phase = "DONE"

            elif cmd == "no-recover":
                result.recover_achieved = False
                _log(session, path, "no_recover", failure=failure_id,
                     manual_step_count=len(result.manual_steps))
                print(f"\n  ❌ No-recover — {failure_id} did not recover in the observation window.")
                phase = "DONE"

            else:
                print(f"  'step <desc>' to log a step, "
                      f"'recover {failure_id}' when training resumes.")

    session.failure_results.append(result)
    return result


# ── Final scorecard ────────────────────────────────────────────────────────────

def _write_summary(session: Session, path: Path) -> None:
    finished_at = _now()
    total_s = (finished_at - session.started_at).total_seconds()

    per_failure = {}
    for r in session.failure_results:
        per_failure[r.failure_id] = {
            "label":             r.label,
            "alert_surfaced":    r.alert_surfaced,
            "recover_achieved":  r.recover_achieved,
            "t_inject":          _ts(r.t_inject)  if r.t_inject  else None,
            "t_alert":           _ts(r.t_alert)   if r.t_alert   else None,
            "t_action":          _ts(r.t_action)  if r.t_action  else None,
            "t_recover":         _ts(r.t_recover) if r.t_recover else None,
            "alert_lag_s":       r.alert_lag_s,
            "alert_lag_min":     round(r.alert_lag_min, 2) if r.alert_lag_min else None,
            "ttr_s":             r.ttr_s,
            "ttr_hms":           _hms(r.ttr_s) if r.ttr_s else None,
            "manual_steps":      r.manual_steps,
            "manual_step_count": len(r.manual_steps),
            "obs_contribution":  round(r.obs_contribution, 2),
        }

    summary = {
        "event":             "_summary",
        "competitor":        session.competitor,
        "session":           session.session_id,
        "started_at":        _ts(session.started_at),
        "finished_at":       _ts(finished_at),
        "total_elapsed_s":   total_s,
        "total_elapsed_hms": _hms(total_s),
        "failures":          per_failure,
        "obs": {
            "score":              round(session.obs_score, 2),
            "total_manual_steps": session.total_manual_steps,
            "annual_eng_hours":   round(session.annual_eng_hours, 1),
            "assumptions": {
                "step_minutes":         STEP_MINUTES,
                "incident_rate_weekly": INCIDENT_RATE_WEEKLY,
                "no_alert_penalty_min": NO_ALERT_PENALTY_MIN,
                "alert_lag_weight":     OBS_WEIGHTS["alert_lag_min"],
                "step_weight":          OBS_WEIGHTS["step"],
            },
        },
    }

    with open(path, "a") as f:
        f.write(json.dumps(summary) + "\n")

    # ── Print scorecard ────────────────────────────────────────────────────────
    W = 68
    print()
    print("=" * W)
    print(f"  TEST 1 RESULTS — {session.competitor.upper()}")
    print("=" * W)

    print(f"\n  FAILURE RESPONSE METRICS")
    print(f"  {'─' * 62}")
    print(f"  {'ID':<5}  {'Failure':<20}  {'Alert Lag':>10}  {'TTR':>8}  {'Steps':>5}  {'OBS':>6}")
    print(f"  {'─' * 62}")
    for r in session.failure_results:
        lag_str = (
            f"{r.alert_lag_min:.1f} min" if r.alert_lag_min is not None
            else ("NO-ALERT" if not r.alert_surfaced else "—")
        )
        ttr_str = _hms(r.ttr_s) if r.ttr_s else ("NO-REC" if not r.recover_achieved else "—")
        print(f"  {r.failure_id:<5}  {r.label:<20}  {lag_str:>10}  "
              f"{ttr_str:>8}  {len(r.manual_steps):>5}  {r.obs_contribution:>6.1f}")
    print(f"  {'─' * 62}")
    print(f"  {'TOTAL':<28}  {'':>10}  {'':>8}  {session.total_manual_steps:>5}  "
          f"{session.obs_score:>6.1f}")

    print(f"\n  OPERATIONAL BURDEN SCORE (OBS):  {session.obs_score:.1f}")
    print(f"  Formula: Σ(steps × {OBS_WEIGHTS['step']}) "
          f"+ Σ(alert_lag_min × {OBS_WEIGHTS['alert_lag_min']})")
    print(f"  No-alert penalty: {NO_ALERT_PENALTY_MIN} min × {OBS_WEIGHTS['alert_lag_min']}"
          f" = {NO_ALERT_PENALTY_MIN * OBS_WEIGHTS['alert_lag_min']} pts per undetected failure")

    print(f"\n  DECISION-MAKER TRANSLATION")
    print(f"  {'─' * 62}")
    print(f"  {STEP_MINUTES} min/step  ·  {INCIDENT_RATE_WEEKLY} incidents/week  ·  52 weeks/year")
    print(f"  Estimated annual engineering hours:  {session.annual_eng_hours:.1f} hrs")

    print(f"\n  Session duration:  {_hms(total_s)}")
    print(f"  Results written to:  {path.name}")
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
    Interactive failure-response observer for Test 1.
    The engineer injects failures into a running training workload and
    logs platform behavior — alert timing and manual remediation steps.
    """
    output_path = _RAW / f"test_01_{competitor}_{session}.jsonl"

    sess = Session(
        competitor=competitor,
        session_id=session,
        started_at=_now(),
    )

    _log(sess, output_path, "start")

    print(f"\n  Output → {output_path}")
    print(f"\n  Test 1: Managed Orchestration — Failure Response Observer")
    print(f"  Platform: {competitor.upper()}")
    print(f"\n  You will inject {len(FAILURES)} failures into a running training workload")
    print(f"  and observe how the platform responds. For each failure:")
    print(f"\n    1. Run the injection script  →  type 'inject <ID>'")
    print(f"    2. Watch monitoring UI only   →  type 'alert <ID>' or 'no-alert'")
    print(f"    3. Begin remediation          →  type 'action <ID>'")
    print(f"    4. Log each manual step       →  'step <description>'")
    print(f"    5. Training resumes           →  type 'recover <ID>'")
    print(HELP_TEXT)
    print(f"  Ensure surf_train.py is running on the cluster before starting.")
    print(f"  Press Enter when ready.")
    try:
        input("  > ")
    except (EOFError, KeyboardInterrupt):
        sys.exit(0)

    for i in range(len(FAILURES)):
        _run_failure(i, sess, output_path)

    _write_summary(sess, output_path)
