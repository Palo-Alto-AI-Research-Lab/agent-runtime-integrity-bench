#!/usr/bin/env python3
"""agent-runtime-integrity-bench — run integrity scenarios against real agent runtimes.

Usage:
    python run_bench.py                          # all scenarios, all adapters
    python run_bench.py --scenario s3            # one scenario
    python run_bench.py --adapter async-sqlite   # one adapter
    python run_bench.py --json results/run.json  # persist machine-readable report

Exit codes: 0 all invariants held · 1 violations found and reported · 4 harness error.
"""

from __future__ import annotations

import argparse
import datetime
import sys

from bench import core, s2_replay, s3_concurrent_memory
from bench.adapters import ADAPTERS

SCENARIOS = {
    "s2": s2_replay.run,
    "s3": s3_concurrent_memory.run,
}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--scenario", choices=[*SCENARIOS, "all"], default="all")
    ap.add_argument("--adapter", choices=[*ADAPTERS, "all"], default="all")
    ap.add_argument("--json", dest="json_path", default=None)
    ap.add_argument("--run-date", default=None,
                    help="override run date stamp (default: today UTC)")
    args = ap.parse_args()

    scenarios = list(SCENARIOS) if args.scenario == "all" else [args.scenario]
    adapters = list(ADAPTERS) if args.adapter == "all" else [args.adapter]
    run_date = args.run_date or datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")

    findings = []
    for s in scenarios:
        findings.extend(SCENARIOS[s](adapters))
    return core.emit(findings, args.json_path, run_date)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except BaseException:  # crash-guard: harness death must exit 4, not 1
        import traceback

        traceback.print_exc()
        sys.exit(4)
