"""
Test 4: The Day in the Life Deployment Race
E2E Lifecycle Time-to-Value Analysis

Measures the wall-clock time and engineering complexity of deploying a
GPU training workload from a cold account start to a first verified
training step — across Crusoe, AWS, and Nebius.

EFI formula (locked pre-test, per test plan):
    EFI = (distinct services × 3) + (config files × 2)
        + (CLI commands × 1) + (error/retry events × 2)

Milestones (all timestamped):
    1. Account created
    2. Billing configured
    3. First instance launched
    4. First job submitted
    5. First training step confirmed  ← FINISH LINE

Interactive mode: the engineer executes deployment steps manually on the
target platform while this script acts as a structured logger and timer.
Every event is timestamped and written to a JSONL file in results/raw/.

Usage (via harness.py):
    python harness.py --test 4 --competitor aws
    python harness.py --test 4 --competitor nebius
    python harness.py --test 4 --competitor crusoe

Output:
    results/raw/test_04_{competitor}_{session}.jsonl
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

# ── Milestones ─────────────────────────────────────────────────────────────────

MILESTONES = [
    (
        "account_created",
        "Account created",
        "New account, no pre-existing infrastructure. Record the moment "
        "login is confirmed.",
    ),
    (
        "billing_configured",
        "Billing configured",
        "Payment method confirmed active. Billing dashboard accessible.",
    ),
    (
        "first_instance_launched",
        "First instance launched",
        "First GPU instance or node is running and reachable (SSH/kubectl).",
    ),
    (
        "first_job_submitted",
        "First job submitted",
        "Training job submitted to the cluster/scheduler.",
    ),
    (
        "first_training_step_confirmed",
        "First training step confirmed  ← FINISH LINE",
        "First verified training step logged to stdout. Job is computing.",
    ),
]

# ── EFI weights (locked pre-test) ─────────────────────────────────────────────

EFI_WEIGHTS = {
    "service":     3,   # distinct cloud services / constructs touched
    "config_file": 2,   # YAML / JSON / Terraform / config files created
    "cli_command": 1,   # CLI commands executed
    "error":       2,   # error or retry events
}

HELP_TEXT = """
  ┌─ Commands ──────────────────────────────────────────────────────────┐
  │  s  <name>    service    — cloud service/construct touched  [EFI +3]│
  │  f  <name>    file       — config file created             [EFI +2] │
  │  c  <cmd>     cmd        — CLI command executed            [EFI +1] │
  │  e  <desc>    error      — error or retry event            [EFI +2] │
  │  note <text>             — free-form note (no EFI impact)           │
  │  next                    — milestone complete, advance to next       │
  │  status                  — show running EFI score + event counts    │
  │  help                    — show this message                        │
  │  abort                   — cancel session (results not saved)       │
  └─────────────────────────────────────────────────────────────────────┘
"""

# ── Data structures ────────────────────────────────────────────────────────────

@dataclass
class MilestoneResult:
    key: str
    label: str
    description: str
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    @property
    def elapsed_s(self) -> Optional[float]:
        if self.started_at and self.completed_at:
            return (self.completed_at - self.started_at).total_seconds()
        return None


@dataclass
class Session:
    competitor: str
    session_id: str
    started_at: datetime
    events: list = field(default_factory=list)

    # EFI accumulators
    services: list = field(default_factory=list)     # deduplicated
    config_files: list = field(default_factory=list)
    cli_commands: list = field(default_factory=list)
    errors: list = field(default_factory=list)

    @property
    def efi_score(self) -> int:
        return (
            len(self.services)     * EFI_WEIGHTS["service"] +
            len(self.config_files) * EFI_WEIGHTS["config_file"] +
            len(self.cli_commands) * EFI_WEIGHTS["cli_command"] +
            len(self.errors)       * EFI_WEIGHTS["error"]
        )

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


# ── EFI status display ─────────────────────────────────────────────────────────

def _print_status(session: Session) -> None:
    print()
    print(f"  ┌─ EFI Running Total ─────────────────────────────────────────")
    print(f"  │  Services     {len(session.services):3d}  × 3 = {len(session.services) * 3:4d}"
          f"  {session.services or '—'}")
    print(f"  │  Config files {len(session.config_files):3d}  × 2 = {len(session.config_files) * 2:4d}"
          f"  {session.config_files or '—'}")
    print(f"  │  CLI commands {len(session.cli_commands):3d}  × 1 = {len(session.cli_commands) * 1:4d}")
    print(f"  │  Errors       {len(session.errors):3d}  × 2 = {len(session.errors) * 2:4d}"
          f"  {session.errors or '—'}")
    print(f"  │  {'─' * 48}")
    print(f"  │  EFI SCORE        {session.efi_score:4d}          Elapsed: {session.elapsed_hms()}")
    print(f"  └─────────────────────────────────────────────────────────────")
    print()


# ── Milestone runner ───────────────────────────────────────────────────────────

def _run_milestone(idx: int, session: Session, path: Path) -> MilestoneResult:
    key, label, description = MILESTONES[idx]
    result = MilestoneResult(key=key, label=label, description=description)
    result.started_at = _now()

    _log(session, path, "milestone_start", milestone=key, milestone_idx=idx)

    print()
    print(f"  {'─' * 60}")
    print(f"  MILESTONE {idx + 1} of {len(MILESTONES)}  —  {label}")
    print(f"  {description}")
    print(f"  {'─' * 60}")
    print(f"  Elapsed so far: {session.elapsed_hms()}")
    print(f"  Log events as you go. 'next' when done. 'help' for commands.")
    print()

    while True:
        try:
            raw = input("  > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n  Session interrupted.")
            sys.exit(1)

        if not raw:
            continue

        parts = raw.split(None, 1)
        cmd = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""

        # ── Service ───────────────────────────────────────────────────────────
        if cmd in ("s", "service"):
            if not arg:
                print("  Usage: s <service name>  (e.g. s IAM)")
                continue
            if arg in session.services:
                print(f"  ↩  '{arg}' already logged — no duplicate EFI charge")
            else:
                session.services.append(arg)
                _log(session, path, "service", milestone=key, value=arg)
                print(f"  ✓  Service: {arg}  [EFI +{EFI_WEIGHTS['service']}]"
                      f"  (total services: {len(session.services)})")

        # ── Config file ───────────────────────────────────────────────────────
        elif cmd in ("f", "file"):
            if not arg:
                print("  Usage: f <filename>  (e.g. f cluster.yaml)")
                continue
            session.config_files.append(arg)
            _log(session, path, "config_file", milestone=key, value=arg)
            print(f"  ✓  Config file: {arg}  [EFI +{EFI_WEIGHTS['config_file']}]"
                  f"  (total files: {len(session.config_files)})")

        # ── CLI command ───────────────────────────────────────────────────────
        elif cmd in ("c", "cmd"):
            if not arg:
                print("  Usage: c <command>  (e.g. c eksctl create cluster)")
                continue
            session.cli_commands.append(arg)
            _log(session, path, "cli_command", milestone=key, value=arg)
            print(f"  ✓  Command logged  [EFI +{EFI_WEIGHTS['cli_command']}]"
                  f"  (total commands: {len(session.cli_commands)})")

        # ── Error / retry ─────────────────────────────────────────────────────
        elif cmd in ("e", "error"):
            if not arg:
                print("  Usage: e <description>  (e.g. e NodeGroup failed, retried)")
                continue
            session.errors.append(arg)
            _log(session, path, "error", milestone=key, value=arg)
            print(f"  ✗  Error: {arg}  [EFI +{EFI_WEIGHTS['error']}]"
                  f"  (total errors: {len(session.errors)})")

        # ── Note ──────────────────────────────────────────────────────────────
        elif cmd == "note":
            if not arg:
                print("  Usage: note <text>")
                continue
            _log(session, path, "note", milestone=key, value=arg)
            print(f"  📝 Note logged.")

        # ── Status ────────────────────────────────────────────────────────────
        elif cmd in ("status", "efi"):
            _print_status(session)

        # ── Help ──────────────────────────────────────────────────────────────
        elif cmd == "help":
            print(HELP_TEXT)

        # ── Next (complete milestone) ─────────────────────────────────────────
        elif cmd == "next":
            result.completed_at = _now()
            _log(session, path, "milestone_complete",
                 milestone=key, milestone_idx=idx,
                 elapsed_s=result.elapsed_s)
            step_time = _hms(result.elapsed_s)
            print(f"\n  ✓  Milestone {idx + 1} complete — "
                  f"step time: {step_time}  |  total: {session.elapsed_hms()}")
            break

        # ── Abort ─────────────────────────────────────────────────────────────
        elif cmd == "abort":
            try:
                confirm = input("  Abort? Results will NOT be saved. (y/N): ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                confirm = "n"
            if confirm == "y":
                print("  Session aborted.")
                sys.exit(0)

        else:
            print(f"  Unknown command: '{raw}'. Type 'help' for available commands.")

    return result


# ── Final scorecard ────────────────────────────────────────────────────────────

def _write_summary(session: Session, results: list[MilestoneResult], path: Path) -> None:
    finished_at = results[-1].completed_at
    total_s = (finished_at - session.started_at).total_seconds()

    summary = {
        "event": "_summary",
        "competitor": session.competitor,
        "session": session.session_id,
        "started_at": _ts(session.started_at),
        "finished_at": _ts(finished_at),
        "total_elapsed_s": total_s,
        "total_elapsed_hms": _hms(total_s),
        "milestones": {
            r.key: {
                "label": r.label,
                "started_at": _ts(r.started_at),
                "completed_at": _ts(r.completed_at),
                "elapsed_s": r.elapsed_s,
                "elapsed_hms": _hms(r.elapsed_s),
            }
            for r in results
        },
        "efi": {
            "score": session.efi_score,
            "services_count":     len(session.services),
            "config_files_count": len(session.config_files),
            "cli_commands_count": len(session.cli_commands),
            "errors_count":       len(session.errors),
            "breakdown": {
                "services_weighted":     len(session.services)     * EFI_WEIGHTS["service"],
                "config_files_weighted": len(session.config_files) * EFI_WEIGHTS["config_file"],
                "cli_commands_weighted": len(session.cli_commands) * EFI_WEIGHTS["cli_command"],
                "errors_weighted":       len(session.errors)       * EFI_WEIGHTS["error"],
            },
        },
        "services":     session.services,
        "config_files": session.config_files,
        "cli_commands": session.cli_commands,
        "errors":       session.errors,
    }

    with open(path, "a") as f:
        f.write(json.dumps(summary) + "\n")

    # ── Print scorecard ────────────────────────────────────────────────────────
    W = 64
    print()
    print("=" * W)
    print(f"  TEST 4 RESULTS — {session.competitor.upper()}")
    print("=" * W)

    print(f"\n  TIME TO FIRST TRAINING STEP")
    print(f"  {'─' * 56}")
    for r in results:
        tick = "✓" if r.completed_at else "·"
        label = r.label[:50]
        print(f"  {tick}  {label:<52}  {_hms(r.elapsed_s):>8}")
    print(f"  {'─' * 56}")
    print(f"     TOTAL (account created → first training step)"
          f"  {_hms(total_s):>8}")

    print(f"\n  ENGINEERING FRICTION INDEX (EFI)")
    print(f"  {'─' * 56}")
    print(f"  Distinct services touched    {len(session.services):3d}  × 3 ="
          f"  {len(session.services) * EFI_WEIGHTS['service']:4d}")
    print(f"  Config files created         {len(session.config_files):3d}  × 2 ="
          f"  {len(session.config_files) * EFI_WEIGHTS['config_file']:4d}")
    print(f"  CLI commands executed        {len(session.cli_commands):3d}  × 1 ="
          f"  {len(session.cli_commands) * EFI_WEIGHTS['cli_command']:4d}")
    print(f"  Error / retry events         {len(session.errors):3d}  × 2 ="
          f"  {len(session.errors) * EFI_WEIGHTS['error']:4d}")
    print(f"  {'─' * 56}")
    print(f"  EFI SCORE                                       {session.efi_score:4d}")

    if session.services:
        print(f"\n  Services: {', '.join(session.services)}")
    if session.errors:
        print(f"  Errors  : {', '.join(session.errors)}")

    print(f"\n  Results written to: {path.name}")
    print("=" * W)
    print()


# ── Entry point ────────────────────────────────────────────────────────────────

def run(
    competitor: str,
    session: str,
    nodes: int = 2,          # unused in Test 4 — accepted for harness signature compat
    kubeconfig: str = None,  # unused in Test 4
) -> None:
    """
    Interactive deployment race logger.
    The engineer deploys on the target platform; this script times and
    scores every step, producing a JSONL log and final EFI scorecard.
    """
    output_path = _RAW / f"test_04_{competitor}_{session}.jsonl"

    sess = Session(
        competitor=competitor,
        session_id=session,
        started_at=_now(),
    )

    _log(sess, output_path, "start")

    print(f"\n  Output → {output_path}")
    print(f"\n  This is an interactive session. You will deploy on {competitor.upper()}")
    print(f"  from a clean account. Log each action as you take it.")
    print(HELP_TEXT)
    print(f"  When you're ready, press Enter to start the clock on Milestone 1.")
    try:
        input("  > ")
    except (EOFError, KeyboardInterrupt):
        sys.exit(0)

    # Walk milestones
    milestone_results: list[MilestoneResult] = []
    for i in range(len(MILESTONES)):
        result = _run_milestone(i, sess, output_path)
        milestone_results.append(result)

    _write_summary(sess, milestone_results, output_path)
