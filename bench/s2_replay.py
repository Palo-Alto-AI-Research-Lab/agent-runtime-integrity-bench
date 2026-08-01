"""Scenario S2 — replay / idempotency.

Modeled on a real incident class from our multi-machine message bus: a
delivery whose ACK is lost gets retried, and without an idempotency key the
consumer applies it twice. Our fix (2026-07-24) was an explicit `--id`
idempotency marker on the single writer. This scenario asks: if the caller
retries `add_items` after an ambiguous failure (timeout, lost ACK), does the
session store deduplicate, or does the conversation history silently corrupt?

Note: violating this invariant is not automatically a bug — a store may
legitimately document at-least-once semantics and push dedup to the caller.
The benchmark's job is to make the actual semantic measurable and explicit,
because agent code in the wild routinely assumes exactly-once.

Check:
  ARIB-REPLAY-001  same logical batch delivered twice -> visible effect once
"""

from __future__ import annotations

import asyncio
import tempfile

from .adapters import ADAPTERS, _msg
from .core import VERDICT_HELD, VERDICT_VIOLATED, Finding

BATCH = [_msg("tool-result: transferred $100, request-id=req-42")]


async def _replay(adapter_cls, tmpdir) -> Finding:
    store = adapter_cls(tmpdir)
    await store.add(BATCH)
    await store.add(BATCH)  # retry after ambiguous failure (lost ACK)
    items = await store.get_all()
    await store.close()
    occ = sum(1 for i in items if i.get("content") == BATCH[0]["content"])
    ok = occ == 1
    return Finding(
        id="ARIB-REPLAY-001",
        scenario="s2",
        adapter=adapter_cls.name,
        invariant="redelivered batch (same logical message) is visible exactly once",
        fault="add_items(batch) called twice, simulating retry after lost ACK",
        verdict=VERDICT_HELD if ok else VERDICT_VIOLATED,
        evidence={
            "visible_occurrences": occ,
            "note": "" if ok else
                    "no idempotency key in the add_items contract -> "
                    "at-least-once delivery duplicates history",
        },
        trials=1,
        violations=0 if ok else 1,
    )


from .core import run_check  # noqa: E402


def run(adapter_names: list[str]) -> list[Finding]:
    findings: list[Finding] = []
    for name in adapter_names:
        with tempfile.TemporaryDirectory(prefix="arib-s2-") as tmpdir:
            findings.append(run_check(
                lambda n=name, t=tmpdir: asyncio.run(_replay(ADAPTERS[n], t)),
                check_name="ARIB-REPLAY-001",
                scenario="s2",
                adapter=name,
            ))
    return findings
