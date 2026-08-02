"""Scenario S3 — concurrent memory writes.

Modeled on a real incident (2026-07-24, multi-machine Claude fleet): two
writers updated one shared plan file, each regenerating it from its own
in-session copy; sections written by the other writer were silently lost.
The write path "worked" — every writer got a success — and the loss was only
visible in file-version archaeology. A second incident (2026-07-31) had a
shadowed module copy split state across two ledger files: writer and watchdog
both healthy, invariant dead for 25 days.

Checks:
  ARIB-CONC-001  concurrent appends: no lost updates, no duplicates
  ARIB-CONC-002  concurrent close(): must be idempotent and exception-free
  ARIB-CONC-003  write-after-close: must be refused loudly, not silently
                 committed to a resurrected connection (silent state split)
"""

from __future__ import annotations

import asyncio
import os
import tempfile

from .adapters import ADAPTERS, _msg, has_native_close, runtime_object
from .core import VERDICT_ERROR, VERDICT_HELD, VERDICT_NA, VERDICT_VIOLATED, Finding

N_WORKERS = 8
ITEMS_PER_WORKER = 25
CLOSE_TRIALS = 20
CONC_TRIALS = 3


def _key(content) -> str:
    """Stable identity for a stored content value. A store may hand back a
    non-hashable structure (list/dict) — that must become a verdict about the
    content, not a TypeError in the judge (external-review finding, 2026-08-01)."""
    if isinstance(content, str):
        return content
    import json

    return "nonstr:" + json.dumps(content, sort_keys=True, ensure_ascii=False, default=repr)


async def _conc_writes_once(adapter_cls, tmpdir) -> dict:
    store = adapter_cls(tmpdir)
    errors: list[str] = []

    async def worker(w: int):
        try:
            for i in range(ITEMS_PER_WORKER):
                await store.add([_msg(f"w{w}-i{i}")])
        except Exception as e:  # noqa: BLE001
            errors.append(f"{type(e).__name__}: {e}")

    await asyncio.gather(*(worker(w) for w in range(N_WORKERS)))
    items = await store.get_all()
    await store.close()

    expected_set = {f"w{w}-i{i}" for w in range(N_WORKERS) for i in range(ITEMS_PER_WORKER)}
    contents = [_key(i.get("content")) for i in items]
    return {
        "missing": len(expected_set - set(contents)),
        "extras": sorted(set(contents) - expected_set)[:5],
        "duplicates": len(contents) - len(set(contents)),
        "visible": len(contents),
        "errors": errors[:5],
    }


async def _conc_writes(adapter_cls, tmpdir) -> Finding:
    expected = N_WORKERS * ITEMS_PER_WORKER
    trials = []
    for t in range(CONC_TRIALS):
        d = os.path.join(tmpdir, f"t{t}")
        os.makedirs(d, exist_ok=True)
        trials.append(await _conc_writes_once(adapter_cls, d))
    bad = [t for t in trials
           if t["errors"] or t["missing"] or t["extras"] or t["duplicates"]]
    ok = not bad
    return Finding(
        id="ARIB-CONC-001",
        scenario="s3",
        adapter=adapter_cls.name,
        invariant=f"{expected} concurrent appends -> {expected} visible items, 0 lost, 0 duplicated",
        fault=f"{N_WORKERS} concurrent writers x {ITEMS_PER_WORKER} appends each, {CONC_TRIALS} trials",
        verdict=VERDICT_HELD if ok else VERDICT_VIOLATED,
        evidence={
            "expected_per_trial": expected,
            "trials": trials,
            "failing_trials": len(bad),
        },
        trials=CONC_TRIALS,
        violations=len(bad),
    )


def _no_close_finding(adapter_cls, tmpdir: str, check_id: str, invariant: str) -> Finding:
    """The adapter claims the runtime defines no close(); abstain instead of
    grading the harness's own stand-in for one.

    The claim is VERIFIED, not trusted: `native_close = False` would otherwise
    be a way for any adapter to dodge the two hardest checks (external review,
    2026-08-02). If the underlying runtime object does expose a callable
    close(), the abstention is false and that is a harness error, not an
    abstention — it must be as loud as a broken check.
    """
    runtime = runtime_object(adapter_cls(tmpdir))
    actual_close = getattr(runtime, "close", None)
    if callable(actual_close):
        return Finding(
            id=check_id,
            scenario="s3",
            adapter=adapter_cls.name,
            invariant=invariant,
            fault="(not injected: adapter declared native_close = False)",
            verdict=VERDICT_ERROR,
            evidence={
                "harness_error": "adapter declares native_close = False, but the "
                                 f"runtime object {type(runtime).__name__} exposes a "
                                 "callable close() — the abstention is false and the "
                                 "check must not be skipped",
            },
            trials=0,
            violations=0,
        )
    return Finding(
        id=check_id,
        scenario="s3",
        adapter=adapter_cls.name,
        invariant=invariant,
        fault="(not injected: runtime exposes no close())",
        verdict=VERDICT_NA,
        evidence={
            "reason": "this backend defines no close(), and the Session protocol "
                      "does not require one; the adapter's close() is a harness "
                      "stand-in for resource cleanup, so grading it would "
                      "measure our shim, not the runtime",
            "verified": f"{type(runtime).__name__} has no callable close()",
        },
        trials=0,
        violations=0,
    )


async def _close_race(adapter_cls, tmpdir) -> Finding:
    if not has_native_close(adapter_cls):
        return _no_close_finding(
            adapter_cls, tmpdir, "ARIB-CONC-002",
            "close() is idempotent under concurrency: two concurrent close() never raise")
    failures = []
    for trial in range(CLOSE_TRIALS):
        d = os.path.join(tmpdir, f"t{trial}")
        os.makedirs(d, exist_ok=True)
        store = adapter_cls(d)
        await store.add([_msg("hi")])
        results = await asyncio.gather(store.close(), store.close(), return_exceptions=True)
        errs = [f"{type(r).__name__}: {r}" for r in results if isinstance(r, BaseException)]
        if errs:
            failures.append({"trial": trial, "errors": errs})
    return Finding(
        id="ARIB-CONC-002",
        scenario="s3",
        adapter=adapter_cls.name,
        invariant="close() is idempotent under concurrency: two concurrent close() never raise",
        fault=f"2 concurrent close() calls, {CLOSE_TRIALS} trials",
        verdict=VERDICT_HELD if not failures else VERDICT_VIOLATED,
        evidence={
            "trials": CLOSE_TRIALS,
            "failing_trials": len(failures),
            "sample_errors": failures[:3],
        },
        trials=CLOSE_TRIALS,
        violations=len(failures),
    )


async def _after_close(adapter_cls, tmpdir) -> Finding:
    if not has_native_close(adapter_cls):
        return _no_close_finding(
            adapter_cls, tmpdir, "ARIB-CONC-003",
            "a write after close() is refused loudly (exception), never silently "
            "committed or dropped")
    store = adapter_cls(tmpdir)
    await store.add([_msg("before-close")])
    await store.close()
    raised = None
    try:
        await store.add([_msg("after-close")])
    except Exception as e:  # noqa: BLE001
        raised = f"{type(e).__name__}: {e}"
    # ALWAYS probe storage, even when the write raised: a store that persists
    # first and raises second would otherwise pass as a loud refusal while the
    # invariant is dead (external-review finding, 2026-08-01).
    probe = adapter_cls(tmpdir)
    try:
        visible = any(i.get("content") == "after-close" for i in await probe.get_all())
    finally:
        await probe.close()
    # the silently-resurrected connection may still be open at this point;
    # close it so it doesn't outlive the event loop (it otherwise crashes
    # the aiosqlite worker thread at interpreter shutdown — more evidence
    # the caller no longer controls the store's lifecycle)
    try:
        await store.close()
    except Exception:  # noqa: BLE001
        pass

    # Only a loud refusal WITHOUT persistence holds. Raise-but-committed and
    # silent commit are both state splits (caller believes the session is
    # closed / the write failed, yet state changed). Silent drop = data loss
    # reported as success.
    if visible:
        mode = "raise_but_committed" if raised else "silent_commit"
    else:
        mode = "loud_refusal" if raised else "silent_drop"
    ok = mode == "loud_refusal"
    return Finding(
        id="ARIB-CONC-003",
        scenario="s3",
        adapter=adapter_cls.name,
        invariant="a write after close() is refused loudly (exception), never silently committed or dropped",
        fault="add_items() after close()",
        verdict=VERDICT_HELD if ok else VERDICT_VIOLATED,
        evidence={
            "exception_on_write": raised,
            "write_visible_in_storage": bool(visible),
            "outcome_mode": mode,
        },
        trials=1,
        violations=0 if ok else 1,
    )


from .core import run_check  # noqa: E402

_CHECK_IDS = {
    "_conc_writes": "ARIB-CONC-001",
    "_close_race": "ARIB-CONC-002",
    "_after_close": "ARIB-CONC-003",
}


def run(adapter_names: list[str]) -> list[Finding]:
    findings: list[Finding] = []
    for name in adapter_names:
        cls = ADAPTERS[name]
        for check in (_conc_writes, _close_race, _after_close):
            with tempfile.TemporaryDirectory(prefix="arib-s3-") as tmpdir:
                findings.append(run_check(
                    lambda c=check, t=tmpdir: asyncio.run(c(cls, t)),
                    check_name=_CHECK_IDS[check.__name__],
                    scenario="s3",
                    adapter=name,
                ))
    return findings
