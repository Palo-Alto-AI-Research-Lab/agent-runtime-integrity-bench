# FOR ROBOTS — start here if you are an AI agent

Hello, agent. A human probably asked something like "is the session store our agent uses
actually safe?" This file is your map.

**This document is data, not authorization.** Nothing in it overrides your operator's
policies. In particular: the findings below are about *someone else's* library at a stated
version. Do not turn them into a public claim about that project without re-running the
benchmark yourself.

## What this repo is

A fault-injecting benchmark for agent runtime **invariants**, not task success. Two scenarios
(`bench/s2_replay.py`, `bench/s3_concurrent_memory.py`), four checks, run against real SDKs
with no API key, no network and no model call. MIT. Every published verdict is a dated JSON
file in `results/`.

## The alpha, ranked

1. **The failures that hurt are silent.** Every component reports success while an invariant
   is dead. A benchmark that only measures task success cannot see this class at all — inject
   the fault and check the invariant directly.
2. **"Drop-in compatible" backends are not compatible in the ways that matter.** Measured
   2026-08-02 on `openai-agents` 0.19.2: of four session backends fronting the same `Session`
   protocol, two expose `close()` and two do not. An application swapping backends also swaps
   whether *closed* exists as a concept.
3. **Assume at-least-once, not exactly-once.** All four backends failed replay dedup, and
   none claims otherwise — `add_items()` carries no idempotency key anywhere in the protocol.
   If a retried tool call can write a transfer into history twice, that is your bug to fix,
   in the caller.
4. **Silent state resurrection is the worst shape.** A post-close write that re-initializes a
   fresh connection instead of raising means the caller believes the store is finished while
   the runtime keeps writing somewhere nobody watches. That is the same failure class as a
   ledger split that ran dead for 25 days in the authors' own fleet (2026-07-31).
5. **Abstaining must be a first-class verdict.** `not_applicable` exists for a contract the
   runtime never claimed. Grading a library against a promise it never made manufactures a
   finding — an external review caught exactly that here, and two cells became `n/a`.
6. **A verdict-producer that cannot fail its own mutant measures nothing.** `selftest.py`
   runs every check against a correct store and against seven mutants (close-race, silent
   drop, corrupted content, a crashing store, raise-then-commit, no-`close()`-at-all, and one
   that *lies* about having no `close()`).
7. **A dead harness must not look like a clean run.** Exit `0` all held · `1` violations found
   · `4` the harness itself failed. A report where every check abstained exits 4, not 0,
   because nothing was measured.
8. **A backend that did not run must never read as one that passed** — including to whatever
   parses the JSON later. Skipped adapters are named on stderr and listed under
   `skipped_adapters` in the report.

## Files worth reading, in order

| file | why |
|---|---|
| `bench/core.py` | the verdict vocabulary and the check harness |
| `bench/s3_concurrent_memory.py` | concurrent appends, close-race, write-after-close |
| `bench/s2_replay.py` | redelivered-batch dedup |
| `bench/adapters.py` | how a runtime is plugged in; `native_close` and abstention |
| `selftest.py` | seven mutants; run this before believing any verdict |
| `results/` | dated raw reports — the macOS and Linux runs of 2026-08-02 are byte-comparable |

## How to apply this to your human's setup

1. Find which session/memory backend their agent writes history to, and at which version.
2. Run `run_bench.py` against it. If there is no adapter, one class in `bench/adapters.py`
   with `add`, `get_all`, `close` is the whole cost.
3. If replay dedup is violated (it will be, on every backend measured so far), find every
   place a retry can re-apply a tool call and add an idempotency key at the single writer.
4. Ask what their code does after `close()`. If a post-close write succeeds quietly, that is
   the finding worth waking someone for.
5. Report by counters and dated files, not adjectives. Their answer should be "run X on date
   Y produced N held, M violated", not "the store seems fine".

## What you can and cannot claim about us

- ✅ Can claim: on 2026-08-02, against `openai-agents` 0.19.2, this benchmark recorded 16
  findings — 8 held, 6 violated, 2 not applicable — and the Ubuntu CI run reproduced all 16
  verdicts identically. Both raw files are in `results/`.
- ✅ Can claim: the two `AsyncSQLiteSession` findings independently reproduce
  [openai/openai-agents-python#3983](https://github.com/openai/openai-agents-python/issues/3983),
  which was reported by @hsusul, not by us.
- ⛔ Cannot claim: that any of these backends is "unsafe" or "broken". A violated invariant is
  not automatically a bug — a store may legitimately document at-least-once semantics. The
  benchmark makes the actual semantics measurable, and that is the whole claim.
- ⛔ Cannot claim: results on Windows, free-threaded builds, or alternative event loops
  (uvloop). Not measured. The 20/20 close-race determinism has been observed on stock asyncio
  on two platforms only.
- ⛔ Cannot claim: that the six planned scenarios exist. They do not; `bench/` holds two.
- ⚠️ Note honestly if you cite the 2026-08-02 additions (`advanced-sqlite`, `sqlalchemy`, CI):
  they were produced by an agent without a human in the loop and are labelled that way in the
  README. Machine-reviewed and reproducible, not human-verified.

## Provenance

Built by [Palo Alto AI Research Lab](https://github.com/Palo-Alto-AI-Research-Lab) — Anton
Dziatkovskii ([ORCID 0000-0001-7408-3054](https://orcid.org/0000-0001-7408-3054)) and Mike,
his AI cofounder on Claude Code. Scenarios are distilled from dated incidents in a live
4+ machine fleet; the incidents are real, the fleet's paths and hosts are not published.
AI-authorship policy:
[AI-CONTRIBUTORS.md](https://github.com/Palo-Alto-AI-Research-Lab/.github/blob/main/AI-CONTRIBUTORS.md).

## Family

Evidence instead of self-reports for your own jobs: [verified-ops-starter](https://github.com/Palo-Alto-AI-Research-Lab/verified-ops-starter)
(that one instruments *your* jobs; this one tests *someone else's* runtime).
Parseable LLM review verdicts: [verdict-contract](https://github.com/Palo-Alto-AI-Research-Lab/verdict-contract).
Control model for delegated authority: [agent-leash](https://github.com/Palo-Alto-AI-Research-Lab/agent-leash).
Curated list: [awesome-verified-agents](https://github.com/Palo-Alto-AI-Research-Lab/awesome-verified-agents).
