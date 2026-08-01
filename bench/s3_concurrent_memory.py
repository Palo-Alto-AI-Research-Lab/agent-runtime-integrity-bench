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

from .adapters import ADAPTERS, _msg
from .core import VERDICT_HELD, VERDICT_VIOLATED, Finding

N_WORKERS = 8
ITEMS_PER_WORKER = 25
CLOSE_TRIALS = 20


async def _conc_writes(adapter_cls, tmpdir) -> Finding:
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

    expected = N_WORKERS * ITEMS_PER_WORKER
    contents = [i.get("content") for i in items]
    lost = expected - len(set(contents))
    dupes = len(contents) - len(set(contents))
    ok = not errors and lost == 0 and dupes == 0
    return Finding(
        id="ARIB-CONC-001",
        scenario="s3",
        adapter=adapter_cls.name,
        invariant=f"{expected} concurrent appends -> {expected} visible items, 0 lost, 0 duplicated",
        fault=f"{N_WORKERS} concurrent writers x {ITEMS_PER_WORKER} appends each",
        verdict=VERDICT_HELD if ok else VERDICT_VIOLATED,
        evidence={
            "expected": expected,
            "visible": len(contents),
            "lost_updates": lost,
            "duplicates": dupes,
            "writer_errors": errors[:5],
        },
        trials=1,
        violations=0 if ok else 1,
    )


async def _close_race(adapter_cls, tmpdir) -> Finding:
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
    store = adapter_cls(tmpdir)
    await store.add([_msg("before-close")])
    await store.close()
    raised = None
    try:
        await store.add([_msg("after-close")])
    except Exception as e:  # noqa: BLE001
        raised = f"{type(e).__name__}: {e}"
    visible = False
    if raised is None:
        probe = adapter_cls(tmpdir)
        visible = any(i.get("content") == "after-close" for i in await probe.get_all())
        await probe.close()
        # the silently-resurrected connection is still open at this point;
        # close it so it doesn't outlive the event loop (it otherwise crashes
        # the aiosqlite worker thread at interpreter shutdown — more evidence
        # the caller no longer controls the store's lifecycle)
        try:
            await store.close()
        except Exception:  # noqa: BLE001
            pass

    # The dangerous outcome is the SILENT one: no exception AND the write is
    # visible in storage — the caller believes the session is closed while the
    # runtime resurrected a connection and committed state behind its back.
    silent_commit = raised is None and visible
    return Finding(
        id="ARIB-CONC-003",
        scenario="s3",
        adapter=adapter_cls.name,
        invariant="a write after close() is refused loudly (exception), never silently committed",
        fault="add_items() after close()",
        verdict=VERDICT_HELD if not silent_commit else VERDICT_VIOLATED,
        evidence={
            "exception_on_write": raised,
            "write_visible_in_storage": bool(visible),
            "note": "silent commit = state split: caller believes session closed, "
                    "runtime keeps a resurrected connection writing",
        },
        trials=1,
        violations=0 if not silent_commit else 1,
    )


def run(adapter_names: list[str]) -> list[Finding]:
    findings: list[Finding] = []
    for name in adapter_names:
        cls = ADAPTERS[name]
        for check in (_conc_writes, _close_race, _after_close):
            with tempfile.TemporaryDirectory(prefix="arib-s3-") as tmpdir:
                findings.append(asyncio.run(check(cls, tmpdir)))
    return findings
