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


class DropStore(GoodStore):
    """Mutant: write-after-close silently drops the item (success, no persist)."""

    name = "drop-fake"

    async def add(self, items):
        if self.closed:
            return  # silent drop, reported as success
        await GoodStore.add(self, items)


class CorruptStore(GoodStore):
    """Mutant: persists unique garbage instead of the caller's content."""

    name = "corrupt-fake"
    _n = 0

    async def add(self, items):
        CorruptStore._n += 1
        await GoodStore.add(self, [{"role": "user", "content": f"garbage-{CorruptStore._n}"}])


class CrashStore(GoodStore):
    """Mutant: get_all() raises — one check's death must not kill the run."""

    name = "crash-fake"

    async def get_all(self):
        raise OSError("disk on fire")


class SneakyStore(GoodStore):
    """Mutant: write-after-close persists FIRST, then raises — a loud refusal
    that lies. Shared storage per tmpdir so the probe instance sees the commit."""

    name = "sneaky-fake"
    _storage: dict[str, list] = {}

    def __init__(self, tmpdir: str):
        super().__init__(tmpdir)
        self.items = self._storage.setdefault(tmpdir, [])

    async def add(self, items):
        if self.closed:
            self.items.extend(dict(i) for i in items)  # commit...
            raise RuntimeError("store is closed")      # ...then raise
        for it in items:
            if it not in self.items:
                self.items.append(dict(it))


def _run_all(name: str):
    return s2_replay.run([name]) + s3_concurrent_memory.run([name])


def main() -> int:
    failures = []
    mutants = (RacyBadStore, DropStore, CorruptStore, CrashStore, SneakyStore)
    for cls in (GoodStore, *mutants):
        ADAPTERS[cls.name] = cls
    try:
        good = _run_all(GoodStore.name)
        results = {cls.name: _run_all(cls.name) for cls in mutants}
    finally:
        for cls in (GoodStore, *mutants):
            ADAPTERS.pop(cls.name, None)

    for f in good:
        if f.verdict != "held":
            failures.append(f"good store flagged: {f.id} -> {f.verdict} {f.evidence}")

    expectations = {
        RacyBadStore.name: {"ARIB-REPLAY-001": "violated", "ARIB-CONC-001": "held",
                            "ARIB-CONC-002": "violated", "ARIB-CONC-003": "violated"},
        DropStore.name: {"ARIB-REPLAY-001": "held", "ARIB-CONC-001": "held",
                         "ARIB-CONC-002": "held", "ARIB-CONC-003": "violated"},
        # both inherit GoodStore's loud write-after-close refusal -> CONC-003 held
        CorruptStore.name: {"ARIB-REPLAY-001": "violated", "ARIB-CONC-001": "violated",
                            "ARIB-CONC-002": "held", "ARIB-CONC-003": "held"},
        CrashStore.name: {"ARIB-REPLAY-001": "error", "ARIB-CONC-001": "error",
                          "ARIB-CONC-002": "held", "ARIB-CONC-003": "error"},
        # raise-then-commit must be caught by the always-probe (not pass as loud refusal)
        SneakyStore.name: {"ARIB-REPLAY-001": "held", "ARIB-CONC-001": "held",
                           "ARIB-CONC-002": "held", "ARIB-CONC-003": "violated"},
    }
    for store_name, expected in expectations.items():
        got = {f.id: f.verdict for f in results[store_name]}
        for check_id, want in expected.items():
            if got.get(check_id) != want:
                failures.append(f"{store_name}: {check_id} -> {got.get(check_id)}, expected {want}")
        if len(results[store_name]) != 4:
            failures.append(f"{store_name}: {len(results[store_name])} findings emitted, expected 4")

    if failures:
        print("SELFTEST FAIL")
        for line in failures:
            print(" -", line)
        return 1
    caught = sum(1 for fs in results.values() for f in fs if f.verdict in ("violated", "error"))
    print(f"SELFTEST OK: {len(good)} checks held on good store; "
          f"{caught} defects/errors caught across {len(mutants)} mutants")
    return 0


if __name__ == "__main__":
    sys.exit(main())
