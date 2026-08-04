# agent-runtime-integrity-bench

[![selftest](https://github.com/Palo-Alto-AI-Research-Lab/agent-runtime-integrity-bench/actions/workflows/selftest.yml/badge.svg)](https://github.com/Palo-Alto-AI-Research-Lab/agent-runtime-integrity-bench/actions/workflows/selftest.yml)
[![license: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

Integrity scenarios for agent runtimes, distilled from real incidents in a production
multi-machine agent fleet — replayed as deterministic, one-command checks against real SDKs.
The scenarios are `bench/s2_replay.py` and `bench/s3_concurrent_memory.py`; every published
verdict is a dated JSON file under `results/`.

Most agent-runtime failures we have lived through were **silent**: every component reported
success while an invariant was dead. A benchmark that only measures task success cannot see
this class, so this one injects the fault and checks the invariant directly (`bench/core.py`).

## Why these scenarios (the incidents behind them)

Each scenario is modeled on a dated incident from our own fleet — 4+ machines running
Claude-based agents with shared file/DB state, a message bus, and human-approval gates:

| Scenario | Our incident | Date |
|---|---|---|
| **S3 concurrent memory writes** | Two writers updated one shared plan file; each regenerated it from its in-session copy, silently discarding the other's sections. Every write returned success; the loss was only visible in file-version archaeology. A related incident: a shadowed module copy split state across two ledger files — writer and watchdog both "healthy," invariant dead for 25 days. | 2026-07-24, 2026-07-31 |
| **S2 replay / idempotency** | A bus delivery whose ACK was lost got retried and applied twice. Fix was an explicit idempotency key on the single writer — the store itself offered none. | 2026-07-24 |

Six more scenarios from the same incident log are planned but **not built** — crash before
state-commit, timeout after partial progress, citation provenance, history tampering,
consensus divergence, human-approval timeout.
We add a scenario only when we can pair it with a real incident and a real runtime to test,
so `bench/` currently holds two.

## Current checks

| ID | Invariant | Fault injected |
|---|---|---|
| ARIB-CONC-001 | N concurrent appends → N visible items, 0 lost, 0 duplicated | 8 concurrent writers × 25 appends |
| ARIB-CONC-002 | `close()` is idempotent under concurrency | 2 concurrent `close()`, 20 trials |
| ARIB-CONC-003 | write-after-close is refused loudly, never silently committed or dropped | `add_items()` after `close()` |
| ARIB-REPLAY-001 | redelivered batch is visible exactly once | same batch delivered twice (retry after lost ACK) |

A violated invariant is not automatically a bug in the runtime: a store may legitimately
document at-least-once semantics and push dedup to the caller.
The benchmark's job is to make the actual semantics **measurable and explicit** — see the
verdict vocabulary in `bench/core.py` — because agent code in the wild routinely assumes
exactly-once and assumes closed means closed.

## Results — openai-agents 0.19.2 (2026-08-02, Python 3.12.13, macOS)

Four session backends from `openai/openai-agents-python`, all four fronting the same
`Session` protocol and documented as drop-in replacements for each other.
Raw report: [`results/2026-08-02-openai-agents-0.19.2.json`](results/2026-08-02-openai-agents-0.19.2.json) — 16 findings, 8 held, 6 violated, 2 not applicable.
Earlier two-adapter runs are kept in `results/` as dated evidence about that day's scope, not rewritten.

The same run on CI (Ubuntu, glibc 2.39, same versions) produced [an identical report](results/2026-08-02-linux-openai-agents-0.19.2.json).
All 16 verdicts and all violation counts match the macOS run, including the 20/20 close-race — diff the two files in `results/` yourself.
These are not one-machine results.

| Check | SQLiteSession | AsyncSQLiteSession | AdvancedSQLiteSession | SQLAlchemySession |
|---|---|---|---|---|
| ARIB-CONC-001 concurrent appends | ✅ held | ✅ held | ✅ held | ✅ held |
| ARIB-CONC-002 concurrent close | ✅ held | ❌ violated — `AttributeError: 'NoneType' object has no attribute 'close'` in **20/20 trials** | ✅ held | ⚪ n/a ⁽*⁾ |
| ARIB-CONC-003 write-after-close | ✅ held (`RuntimeError: SQLiteSession is closed`) | ❌ violated — write silently committed to a resurrected connection; the leaked connection then outlives the event loop | ✅ held (inherits the loud refusal) | ⚪ n/a ⁽*⁾ |
| ARIB-REPLAY-001 replay dedup | ❌ violated (2 copies visible) | ❌ violated (2 copies visible) | ❌ violated (2 copies visible) | ❌ violated (2 copies visible) |

⁽*⁾ **`SQLAlchemySession` has no `close()`** — and neither does the `Session` protocol: two of these four drop-in-compatible backends expose one, two do not (`bench/adapters.py`).
The harness gives that adapter a stand-in `close()` (`engine.dispose()`) only to release resources between trials, and the close-semantics checks **abstain** rather than grade it.
SQLAlchemy documents `dispose()` as replacing the pool with the engine still usable, so a write that succeeds afterwards is documented behaviour, not silent resurrection.
Grading it would have produced a red cell against a promise the library never made. The first external review of this change caught exactly that, and the abstention is now enforced by the `NoCloseStore` mutant in `selftest.py`.

The measurable fact survives the correction, and it is about the protocol rather than any one class: an application swapping session backends "drop-in" also swaps whether *closed* exists at all (`bench/adapters.py`).

Replay is the one result that is unanimous: **no backend deduplicates**, and none claims to — `add_items()` has no idempotency key anywhere in the protocol.
Exactly-once is the caller's job in all four (`bench/s2_replay.py`), which is worth knowing before a retried tool-call writes a transfer into history twice.

The two `AsyncSQLiteSession` findings independently reproduce [openai/openai-agents-python#3983](https://github.com/openai/openai-agents-python/issues/3983) (reported by @hsusul).
`close()` checks `self._connection is None` outside the lock without re-checking inside, and the class has no `_closed` flag — so a post-close write re-initializes a fresh connection instead of raising.
The sync `SQLiteSession` holds both invariants, which shows the fix shape already exists in the same codebase; both verdicts are in [`results/2026-08-02-openai-agents-0.19.2.json`](results/2026-08-02-openai-agents-0.19.2.json).

ARIB-CONC-003 is the one we care most about: **silent state resurrection is the same failure class as our 25-day ledger split of 2026-07-31** — the caller believes the store is closed and finished, while the runtime keeps writing somewhere the caller no longer watches (`bench/s3_concurrent_memory.py`).

## Quickstart

```
git clone https://github.com/Palo-Alto-AI-Research-Lab/agent-runtime-integrity-bench
cd agent-runtime-integrity-bench
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt   # 3 of 4 backends
.venv/bin/python run_bench.py
```

Three minutes, no API key, no network, no model call — the scenarios in `bench/` exercise session storage, not an LLM.

`requirements-full.txt` adds SQLAlchemy and is what CI installs and what the published results were measured with.
With only the core install (`requirements.txt`), the `sqlalchemy` adapter is **named on stderr as skipped and listed in the report under `skipped_adapters`**, and the rest still run; asking for it by name exits 4 instead (`run_bench.py`).
A backend that did not run must never read as a backend that passed — including to whatever reads the JSON later.

Options: `--scenario s2|s3`, `--adapter sqlite|async-sqlite|advanced-sqlite|sqlalchemy`,
`--json results/run.json`, `--run-date YYYY-MM-DD`.

Verdicts are `held`, `violated`, `error` — and `not_applicable`, for a contract the runtime never claimed (`bench/core.py`).
Abstaining is a first-class outcome here: it is recorded in the report and printed in the summary, never dropped, and a report in which *every* check abstained exits 4 rather than 0, because nothing was measured (`run_bench.py`).
An adapter cannot use it to dodge grading either — the "this runtime has no close()" claim is checked against the runtime object, and a false claim is a harness error, not an abstention (`LyingNoCloseStore` in `selftest.py`).

Exit codes are part of the contract (`run_bench.py`): `0` all invariants held · `1` violations found and reported · `4` the harness itself failed.
A dead harness must never look like a clean run — that, too, is a lesson from a production watchdog that reported green while its subject was gone.

The harness proves it can tell good from bad before you trust it, via `selftest.py`:

```
.venv/bin/python selftest.py
```

It runs every check against a correct in-memory store, where everything must hold, and then against seven mutants in `selftest.py` carrying the defect classes above:

- `RacyBadStore` — the close-race and no dedup;
- `DropStore` — write-after-close silently dropped;
- `CorruptStore` — persists garbage instead of the caller's content;
- `CrashStore` — a dying check must produce an `error` finding, not kill the run;
- `SneakyStore` — a "loud refusal" that persists anyway must not pass, so `selftest.py` probes the storage even when the write raises;
- `NoCloseStore` — a runtime with no `close()` at all: the close checks must abstain, not invent a verdict;
- `LyingNoCloseStore` — one that *lies* about having no `close()`: the abstention must be refused as a harness error.

The judge must get all seven right. The report-layer guards — empty report and all-abstained report both exit 4 — are asserted directly in `selftest.py`, since no CLI invocation can reach them.

Known oracle limits, kept honest rather than hidden (`bench/s3_concurrent_memory.py`): ARIB-CONC-002 asserts "no exception under concurrent close" and does not yet verify post-close resource state.
Its interleaving also relies on the runtime yielding inside `close()` — a close that never awaits would serialize and pass this check while still being unsafe under preemptive scheduling.

## Adding a runtime

One adapter class in [`bench/adapters.py`](bench/adapters.py) with three async methods — `add(items)`, `get_all()`, `close()` — plus an optional `available()` if it needs a dependency the core install doesn't carry.
If the runtime has no `close()` of its own, set `native_close = False` in `bench/adapters.py`: the close checks will abstain instead of grading a stand-in the harness invented.

Planned next: `pydantic-ai` message history and the `modelcontextprotocol/python-sdk` session layer — neither is built yet.
The remaining openai-agents backends (`RedisSession`, `MongoDBSession`, `DaprSession`, `EncryptedSession`) need a live service or key and are deliberately not stubbed in `bench/adapters.py`, because a mocked backend would produce a verdict about the mock.

## Provenance & disclosure

Built by [Palo Alto AI Research Lab](https://github.com/Palo-Alto-AI-Research-Lab).
Code written with an AI agent (Claude); a human verified: the checks were run
live on the versions stated, the AsyncSQLiteSession source was read to confirm
the mechanism (check-outside-lock, missing `_closed` flag), the self-test
mutant fails and the real runs are reproducible (3 consecutive identical
verdict sets). Since 2026-08-02 the checks also run on CI: the self-test on
Python 3.10/3.12/3.13 and the full benchmark on Ubuntu, which reproduced all
16 macOS verdicts exactly. Still not verified: Windows, free-threaded builds,
and alternative event loops (uvloop) — the 20/20 close-race determinism relies
on the await-inside-the-lock suspension point and has been measured on stock
asyncio only, on two platforms. Known selftest gap: mutants cover wrong
verdicts, not hangs (a store deadlocking on SQLite busy_timeout would stall
the run rather than fail it) — a per-check wall-clock timeout is on the
roadmap.

**2026-08-02 additions** (`advanced-sqlite` and `sqlalchemy` adapters, CI,
this results file) were produced by the agent without a human in the loop, and
are labelled that way on purpose. What backs them: every verdict comes from a
live run on the versions stated in the report; the run was repeated and the
verdict set was identical; the dependency-missing paths were exercised in a
clean environment without SQLAlchemy installed (skipped-by-name → exit 1 with
the skip recorded in the report, named explicitly → exit 4); the self-test
covers all seven mutants in `selftest.py`. They also went through an external code review before
publication, which rejected the first version of the `SQLAlchemySession`
result as a manufactured finding — that objection is why those two cells read
`n/a` above instead of red. What does **not** back them: a second pair of
*human* eyes. Treat the two new adapters as reproducible-and-machine-reviewed
until that happens.

License: MIT.

## Roadmap

**Now — [v0.1.0](https://github.com/Palo-Alto-AI-Research-Lab/agent-runtime-integrity-bench/releases/tag/v0.1.0).**
Two scenarios, four invariants, four adapters against `openai-agents` 0.19.2; every published
verdict is a dated JSON file under `results/`, reproduced identically on macOS and on CI Ubuntu.

**Next**, in the order we would take them:

- **Six more scenarios from the same incident log** — crash before state-commit, timeout after
  partial progress, citation provenance, history tampering, consensus divergence, human-approval
  timeout. The rule that keeps this honest: a scenario ships only when we can pair it with a real
  incident *and* a real runtime to test it against, which is why `bench/` holds two today.
- **A second runtime family.** Everything measured so far is one SDK. A benchmark that has only
  ever been pointed at one library has not yet been shown to measure the library rather than us.
- **Human eyes on the two newest adapters.** `AdvancedSQLiteSession` and `SQLAlchemySession` are
  reproducible-and-machine-reviewed; the README says so per claim, and that is a gap, not a style.
- **Platforms we do not cover:** Windows, free-threaded builds, uvloop.
  `RedisSession` / `MongoDBSession` / `DaprSession` / `EncryptedSession` need a live service and
  are deliberately *not* stubbed — a mock would only produce a verdict about the mock.

Every noticeable change ships as a new release, and a result about someone else's library is a
noticeable change: the
[release feed](https://github.com/Palo-Alto-AI-Research-Lab/agent-runtime-integrity-bench/releases)
is where the scope of what we have actually measured is recorded.

## AI contributors

This project is built by a human + AI team, and the git log says so: Claude
writes most of the code, Codex and Grok review it, Gemini feeds the research.
Each is credited on a commit **only if its output changed that commit's
content** — no decorative credits. Lab-wide policy, one source for every repo:
[AI-CONTRIBUTORS.md](https://github.com/Palo-Alto-AI-Research-Lab/.github/blob/main/AI-CONTRIBUTORS.md).
