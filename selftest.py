#!/usr/bin/env python3
"""Self-test of the harness itself: prove the judge can tell good from bad.

Runs both scenarios against two fake in-process stores:
  - GoodStore: correct semantics (dedup by content, closed-flag, safe close)
    -> every check must come back 'held'
  - BadStore: naive list append, close() double-free, silent write-after-close
    -> ARIB-REPLAY-001 / ARIB-CONC-002 / ARIB-CONC-003 must come back 'violated'

A benchmark that cannot fail its own mutant is not measuring anything.
Exit 0 = harness discriminates correctly, 1 = it does not.
"""

from __future__ import annotations

import sys

from bench import s2_replay, s3_concurrent_memory
from bench.adapters import ADAPTERS


class GoodStore:
    name = "good-fake"

    def __init__(self, tmpdir: str):
        self.items: list[dict] = []
        self.closed = False

    async def add(self, items):
        if self.closed:
            raise RuntimeError("store is closed")
        for it in items:
            if it not in self.items:  # idempotent by content
                self.items.append(dict(it))

    async def get_all(self):
        return list(self.items)

    async def close(self):
        self.closed = True  # idempotent


class RacyBadStore:
    """Mutant: shared storage per tmpdir, no dedup, close() with the classic
    check-outside/deref-inside race, silent resurrection after close."""

    name = "bad-fake"
    _storage: dict[str, list] = {}  # shared across instances, like a real DB file

    def __init__(self, tmpdir: str):
        import types

        self.items = self._storage.setdefault(tmpdir, [])
        self._conn: object | None = types.SimpleNamespace(close=lambda: None)

    async def add(self, items):
        import types

        if self._conn is None:
            self._conn = types.SimpleNamespace(close=lambda: None)  # silent resurrection
        self.items.extend(dict(i) for i in items)

    async def get_all(self):
        if self._conn is None:
            self._conn = object()
        return list(self.items)

    async def close(self):
        import asyncio

        if self._conn is None:  # check outside any interleave protection...
            return
        await asyncio.sleep(0)  # ...yield, letting a concurrent close finish first
        self._conn.close  # unchecked deref: AttributeError when _conn became None
        self._conn = None


def main() -> int:
    failures = []

    ADAPTERS[GoodStore.name] = GoodStore
    ADAPTERS[RacyBadStore.name] = RacyBadStore
    try:
        good = s2_replay.run([GoodStore.name]) + s3_concurrent_memory.run([GoodStore.name])
        bad = s2_replay.run([RacyBadStore.name]) + s3_concurrent_memory.run([RacyBadStore.name])
    finally:
        ADAPTERS.pop(GoodStore.name, None)
        ADAPTERS.pop(RacyBadStore.name, None)

    for f in good:
        if f.verdict != "held":
            failures.append(f"good store flagged: {f.id} -> {f.verdict} {f.evidence}")
    expect_bad = {"ARIB-REPLAY-001", "ARIB-CONC-002", "ARIB-CONC-003"}
    for f in bad:
        want = "violated" if f.id in expect_bad else "held"
        if f.verdict != want:
            failures.append(f"bad store: {f.id} -> {f.verdict}, expected {want}")

    if failures:
        print("SELFTEST FAIL")
        for line in failures:
            print(" -", line)
        return 1
    print(f"SELFTEST OK: {len(good)} checks held on good store, "
          f"{sum(1 for f in bad if f.verdict == 'violated')} violations caught on mutant")
    return 0


if __name__ == "__main__":
    sys.exit(main())
