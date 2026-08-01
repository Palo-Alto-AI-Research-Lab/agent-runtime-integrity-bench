"""Core report/verdict plumbing for agent-runtime-integrity-bench.

Stdlib only. Every check produces a Finding with a verdict:

  held     - the invariant held under fault injection
  violated - the invariant was violated (found-and-reported; this is the
             benchmark doing its job, not the benchmark failing)
  error    - the harness itself failed (never conflate with 'violated')

Exit codes of run_bench.py follow the same discipline:
  0 = all invariants held, 1 = >=1 violation found and reported,
  4 = harness crashed. Codes are never overloaded: a dead harness must not
  look like a clean run (see README, "why exit codes matter").
"""

from __future__ import annotations

import dataclasses
import json
import platform
import sys
import traceback
from typing import Any, Callable

VERDICT_HELD = "held"
VERDICT_VIOLATED = "violated"
VERDICT_ERROR = "error"


@dataclasses.dataclass
class Finding:
    id: str                      # e.g. ARIB-CONC-002
    scenario: str                # s2 | s3
    adapter: str                 # sqlite | async-sqlite
    invariant: str               # human-readable invariant statement
    fault: str                   # what fault was injected
    verdict: str                 # held | violated | error
    evidence: dict[str, Any]     # counts, exception types, traces
    trials: int = 1
    violations: int = 0

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


def run_check(fn: Callable[[], Finding], *, check_name: str, scenario: str,
              adapter: str) -> Finding:
    """Crash-guard around one check: a harness/adapter bug must surface as an
    'error' finding for THAT check, without discarding the other findings."""
    try:
        return fn()
    except BaseException:  # noqa: BLE001 - deliberate: capture check death
        return Finding(
            id=check_name,
            scenario=scenario,
            adapter=adapter,
            invariant="(check did not complete)",
            fault="(check did not complete)",
            verdict=VERDICT_ERROR,
            evidence={"harness_traceback": traceback.format_exc()},
        )


def environment() -> dict[str, str]:
    import importlib.metadata as md

    env = {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
    }
    for pkg in ("openai-agents", "aiosqlite"):
        try:
            env[pkg] = md.version(pkg)
        except md.PackageNotFoundError:
            env[pkg] = "not installed"
    return env


def emit(findings: list[Finding], json_path: str | None, run_date: str) -> int:
    # zero findings = the harness did no work; a disabled/empty run must never
    # look like a clean one (CI would count exit 0 as "all invariants held")
    if not findings:
        print("ERROR: no checks ran (empty scenario/adapter selection?)", file=sys.stderr)
        return 4
    report = {
        "bench": "agent-runtime-integrity-bench",
        "run_date": run_date,
        "environment": environment(),
        "findings": [f.to_dict() for f in findings],
    }
    text = json.dumps(report, indent=2, ensure_ascii=False)
    if json_path:
        with open(json_path, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
    print(text)

    print("\n== summary ==", file=sys.stderr)
    worst = 0
    for f in findings:
        mark = {"held": "OK  ", "violated": "VIOL", "error": "ERR "}[f.verdict]
        print(f"{mark} {f.id:<18} [{f.adapter}] {f.invariant}", file=sys.stderr)
        if f.verdict == VERDICT_VIOLATED:
            worst = max(worst, 1)
        elif f.verdict == VERDICT_ERROR:
            worst = 4
    return worst
