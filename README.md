# agent-runtime-integrity-bench

Integrity scenarios for agent runtimes, distilled from real incidents in a
production multi-machine agent fleet — replayed as deterministic, one-command
checks against real SDKs.

Most agent-runtime failures we have lived through were **silent**: every
component reported success while an invariant was dead. A benchmark that only
measures task success can't see this class. This one injects the fault and
checks the invariant directly.

## Why these scenarios (the incidents behind them)

Each scenario is modeled on a dated incident from our own fleet (4+ machines
running Claude-based agents with shared file/DB state, a message bus, and
human-approval gates):

| Scenario | Our incident | Date |
|---|---|---|
| **S3 concurrent memory writes** | Two writers updated one shared plan file; each regenerated it from its in-session copy, silently discarding the other's sections. Every write returned success; the loss was only visible in file-version archaeology. A related incident: a shadowed module copy split state across two ledger files — writer and watchdog both "healthy," invariant dead for 25 days. | 2026-07-24, 2026-07-31 |
| **S2 replay / idempotency** | A bus delivery whose ACK was lost got retried and applied twice. Fix was an explicit idempotency key on the single writer — the store itself offered none. | 2026-07-24 |

Six more scenarios from the same incident log (crash before state-commit,
timeout after partial progress, citation provenance, history tampering,
consensus divergence, human-approval timeout) are planned; we add a scenario
only when we can pair it with a real incident and a real runtime to test.

## Current checks

| ID | Invariant | Fault injected |
|---|---|---|
| ARIB-CONC-001 | N concurrent appends → N visible items, 0 lost, 0 duplicated | 8 concurrent writers × 25 appends |
| ARIB-CONC-002 | `close()` is idempotent under concurrency | 2 concurrent `close()`, 20 trials |
| ARIB-CONC-003 | write-after-close is refused loudly, never silently committed or dropped | `add_items()` after `close()` |
| ARIB-REPLAY-001 | redelivered batch is visible exactly once | same batch delivered twice (retry after lost ACK) |

A violated invariant is not automatically a bug in the runtime — a store may
legitimately document at-least-once semantics and push dedup to the caller.
The benchmark's job is to make the actual semantics **measurable and
explicit**, because agent code in the wild routinely assumes exactly-once and
assumes closed means closed.

## Results — openai-agents 0.19.2 (2026-07-31, Python 3.12.13, macOS)

Adapters: `SQLiteSession` (sync sqlite3) and `AsyncSQLiteSession` (aiosqlite)
from `openai/openai-agents-python`. Raw report:
[`results/2026-07-31-openai-agents-0.19.2.json`](results/2026-07-31-openai-agents-0.19.2.json).

| Check | SQLiteSession | AsyncSQLiteSession |
|---|---|---|
| ARIB-CONC-001 concurrent appends | ✅ held | ✅ held |
| ARIB-CONC-002 concurrent close | ✅ held | ❌ violated — `AttributeError: 'NoneType' object has no attribute 'close'` in **20/20 trials** |
| ARIB-CONC-003 write-after-close | ✅ held (`RuntimeError: SQLiteSession is closed`) | ❌ violated — write silently committed to a resurrected connection; the leaked connection then outlives the event loop |
| ARIB-REPLAY-001 replay dedup | ❌ violated (2 copies visible) | ❌ violated (2 copies visible) |

The two `AsyncSQLiteSession` findings independently reproduce
[openai/openai-agents-python#3983](https://github.com/openai/openai-agents-python/issues/3983)
(reported by @hsusul): `close()` checks `self._connection is None` outside the
lock without re-checking inside, and the class has no `_closed` flag — so a
post-close write re-initializes a fresh connection instead of raising. The
sync `SQLiteSession` holds both invariants, which shows the fix shape already
exists in the same codebase.

ARIB-CONC-003 is the one we care most about: **silent state resurrection is
the same failure class as our 25-day ledger split** — the caller believes the
store is closed/finished, the runtime keeps writing somewhere the caller no
longer watches.

## Run it

```
python3.12 -m venv .venv
.venv/bin/pip install openai-agents aiosqlite
.venv/bin/python run_bench.py
```

Options: `--scenario s2|s3`, `--adapter sqlite|async-sqlite`,
`--json results/run.json`.

Exit codes are part of the contract: `0` all invariants held · `1` violations
found and reported · `4` the harness itself failed. A dead harness must never
look like a clean run (that, too, is a lesson from a production watchdog that
reported green while its subject was gone).

The harness proves it can tell good from bad before you trust it:

```
.venv/bin/python selftest.py
```

runs every check against a correct in-memory store (everything must hold) and
against five mutants carrying the defect classes above — close-race, silent
resurrection, silent drop, corrupted content, a crashing store (a dying check
must produce an `error` finding, not kill the run), and a raise-then-commit
store (a "loud refusal" that persists anyway must not pass — the storage is
probed even when the write raises). The judge must catch all of them.

Known oracle limits (kept honest rather than hidden): ARIB-CONC-002 asserts
"no exception under concurrent close" — it does not yet verify post-close
resource state, and its interleaving relies on the runtime yielding inside
`close()`; a close that never awaits would serialize and pass this check
while still being unsafe under preemptive scheduling.

## Adding a runtime

One adapter class in [`bench/adapters.py`](bench/adapters.py) with three async
methods: `add(items)`, `get_all()`, `close()`. Planned next:
`pydantic-ai` message history and `modelcontextprotocol/python-sdk` session
layer.

## Provenance & disclosure

Built by [Palo Alto AI Research Lab](https://github.com/Palo-Alto-AI-Research-Lab).
Code written with an AI agent (Claude); a human verified: the checks were run
live on the versions stated, the AsyncSQLiteSession source was read to confirm
the mechanism (check-outside-lock, missing `_closed` flag), the self-test
mutant fails and the real runs are reproducible (3 consecutive identical
verdict sets). Not verified: behavior on Windows, on Python ≠ 3.12, under free-threaded
builds, or on alternative event loops (uvloop) — the 20/20 close-race
determinism relies on the await-inside-the-lock suspension point and was
measured on stock asyncio only. Known selftest gap: mutants cover wrong
verdicts, not hangs (a store deadlocking on SQLite busy_timeout would stall
the run rather than fail it) — a per-check wall-clock timeout is on the
roadmap.

License: MIT.
