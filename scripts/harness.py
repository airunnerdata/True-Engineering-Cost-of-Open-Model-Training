#!/usr/bin/env python3
"""
Surf Report — Crusoe Benchmark Harness v2

Usage:
    python harness.py --test <1-4> --competitor <aws|nebius|crusoe> [options]

Tests:
  1  Managed Orchestration — CMK + Command Center vs. native tooling  (H100)
  2  Managed Kubernetes Provisioning Speed + GPU Utilization           (H100)
  3  Goodput Stability Test — 48-hour FSDP training run               (B300)
  4  Day in the Life Deployment Race — EFI + time-to-first-step       (H100)
"""

import argparse
import sys
from datetime import datetime, timezone

from tests import test_01, test_02, test_03, test_04

TESTS = {
    1: test_01,
    2: test_02,
    3: test_03,
    4: test_04,
}

TEST_LABELS = {
    1: "Managed Orchestration — CMK + Command Center vs. Native Tooling",
    2: "Managed Kubernetes Provisioning Speed & GPU Utilization",
    3: "The Goodput Stability Test (48-hour FSDP)",
    4: "The Day in the Life Deployment Race",
}

TEST_HARDWARE = {
    1: "H100  (A100/H100 Phase 1 validation)",
    2: "H100  (A100/H100 Phase 1 validation)",
    3: "B300  (H100 Phase 1 validation)",
    4: "H100",
}

VALID_COMPETITORS = {"aws", "nebius", "crusoe"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="harness.py",
        description="Surf Report — Crusoe Benchmark Test Harness v2",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Tests:
  1  Managed Orchestration      (H100, aws|nebius|crusoe)
  2  K8s Provisioning + MFU     (H100, aws|nebius|crusoe)
  3  Goodput — 48hr FSDP        (B300, aws|nebius|crusoe)
  4  Day in the Life / EFI      (H100, aws|nebius|crusoe)

Examples:
  python harness.py --test 4 --competitor aws
  python harness.py --test 4 --competitor crusoe
  python harness.py --test 3 --competitor nebius --nodes 8 --kubeconfig ~/.kube/nebius
  python harness.py --test 2 --competitor crusoe --nodes 4 --kubeconfig ~/.kube/cmk
  python harness.py --test 1 --competitor aws --dry-run
        """,
    )
    parser.add_argument(
        "--test", "-t",
        type=int,
        choices=range(1, 5),
        required=True,
        metavar="TEST",
        help="Test number to run (1–4)",
    )
    parser.add_argument(
        "--competitor", "-c",
        type=str,
        choices=sorted(VALID_COMPETITORS),
        required=True,
        metavar="COMPETITOR",
        help="Competitor platform: aws | nebius | crusoe",
    )
    parser.add_argument(
        "--nodes",
        type=int,
        default=2,
        metavar="N",
        help="Number of GPU nodes in the test cluster (default: 2). Tests 1–3.",
    )
    parser.add_argument(
        "--kubeconfig",
        type=str,
        default=None,
        metavar="PATH",
        help="Path to kubeconfig for the test cluster. Tests 1–3.",
    )
    parser.add_argument(
        "--session",
        type=str,
        default=None,
        metavar="LABEL",
        help="Session label for grouping results (default: auto-generated timestamp).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would run without executing.",
    )
    return parser.parse_args()


def make_session(competitor: str) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{competitor}_{ts}"


def banner(test: int, competitor: str, session: str, nodes: int) -> None:
    width = 62
    print("=" * width)
    print("  Surf Report — Crusoe Benchmark Harness")
    print(f"  Started  : {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC")
    print(f"  Test     : {test} — {TEST_LABELS[test]}")
    print(f"  Against  : {competitor.upper()}")
    print(f"  Hardware : {TEST_HARDWARE[test]}")
    print(f"  Nodes    : {nodes}")
    print(f"  Session  : {session}")
    print("=" * width)


def main() -> None:
    args = parse_args()
    session = args.session or make_session(args.competitor)
    banner(args.test, args.competitor, session, args.nodes)
    print()

    if args.dry_run:
        print(f"  [dry-run] Would execute: test_{args.test:02d}.run()")
        print(f"  [dry-run] competitor={args.competitor}  session={session}  nodes={args.nodes}")
        if args.kubeconfig:
            print(f"  [dry-run] kubeconfig={args.kubeconfig}")
        print()
        return

    TESTS[args.test].run(
        competitor=args.competitor,
        session=session,
        nodes=args.nodes,
        kubeconfig=args.kubeconfig,
    )
    print()
    print("Done.")


if __name__ == "__main__":
    main()
