# AGENTS.md — working in this repo

Written for AI coding agents, and equally readable by a human contributor. Short on purpose.

## What this repo is

A benchmark that injects a fault into a real agent runtime and then checks an **invariant**, not a
task-success score. Every scenario here is modelled on a dated incident from a production
multi-machine agent fleet — the failures it looks for are ones that already happened, silently,
while every component reported success.

That constraint is the repo's whole editorial line: **a scenario without a real incident behind it
does not go in.** "This could theoretically break" is a good issue and a bad benchmark.

## Stack and layout

- **Python 3.12, stdlib + the runtime under test.** `requirements.txt` pins only what an adapter
  needs.
- `run_bench.py` — the entry point. `--scenario`, `--adapter`, `--json`, `--run-date`.
- `bench/core.py` — finding model and report emission.
- `bench/adapters.py` — one adapter per runtime under test; `ADAPTERS` is the registry.
- `bench/s2_replay.py`, `bench/s3_concurrent_memory.py` — the scenarios.
- `selftest.py` — runs both scenarios against a correct fake store and a deliberately broken one.
- `results/` — dated raw reports. Append, never rewrite: an old result is evidence about an old
  version, not a stale file.

## How to verify a change

```bash
python selftest.py     # must exit 0: the harness still tells good from bad
python run_bench.py    # exit 0 = all invariants held, 1 = violations found, 4 = harness error
```

`selftest.py` first, always. **A benchmark that cannot fail its own mutant is not measuring
anything** — if you add a check, add the mutant that makes it fail, or the check is decoration.

Paste both outputs in the PR. If you added or changed a scenario, also commit its `--json` report
into `results/` with the real date, the runtime version, and the platform.

## Conventions

- **Exit codes are the interface:** `0` held, `1` violations found and reported, `4` harness died.
  `run_bench.py` carries a crash-guard for exactly this reason — a harness that dies must not be
  indistinguishable from a harness that found bugs. Keep that property in anything you add.
- Check IDs are stable and namespaced (`ARIB-CONC-001`). Never renumber an existing one; a
  published result refers to it.
- A violated invariant is **not automatically a bug in the runtime.** A store may document
  at-least-once semantics. Report what the semantics *are*; do not editorialise them into a defect.
- Deterministic runs. No wall-clock dependence, no network, no ordering assumptions beyond what the
  scenario states.

## Boundaries — what needs a human

- **Adding a scenario.** It needs a named, dated incident and a real runtime to test against. Open
  an issue with the incident first; the code is the easy part.
- **Publishing a result about someone else's project.** These findings name third-party libraries
  and versions. Numbers must come from a run you can reproduce, on a version you state — never
  from memory, an estimate, or a previous run "that should still hold".
- **Editing an existing file under `results/`.** Don't. Add a new dated one.

## The deal

Your copyright stays yours, there is no CLA, and issues labelled `accepted` are free to take —
comment "claiming this". Full terms:
[CONTRIBUTING.md](https://github.com/Palo-Alto-AI-Research-Lab/.github/blob/main/CONTRIBUTING.md).

If an AI wrote your change, say so in the PR and confirm you ran it. Welcome here — we do it daily.
Unread generated code is the one thing that gets closed on sight.
