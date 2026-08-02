"""Adapters wrap a runtime's session-memory store behind one tiny async contract.

Contract:
    adapter = AdapterCls(tmpdir)
    await adapter.add(items)          # append conversation items
    await adapter.get_all() -> list   # full visible history
    await adapter.close()             # close the store

Both openai-agents session flavors expose async add/get; SQLiteSession's
close() is sync and is wrapped in asyncio.to_thread so concurrent close is a
real thread-level race. Adding a runtime = one adapter class here.

An adapter may declare `available()` -> bool when it needs a dependency that
is not in requirements.txt. Unavailable adapters are skipped by `--adapter
all` and refused loudly when named explicitly — never silently reported as a
clean run.
"""

from __future__ import annotations

import asyncio
import importlib.util
import os


def _msg(text: str) -> dict:
    return {"role": "user", "content": text}


class SQLiteSessionAdapter:
    """openai-agents SQLiteSession (sync sqlite3 via asyncio.to_thread)."""

    name = "sqlite"

    def __init__(self, tmpdir: str):
        from agents.memory import SQLiteSession

        self.db_path = os.path.join(tmpdir, "sync.db")
        self.session = SQLiteSession("bench", self.db_path)

    async def add(self, items):
        await self.session.add_items(items)

    async def get_all(self):
        return await self.session.get_items()

    async def close(self):
        await asyncio.to_thread(self.session.close)


class AsyncSQLiteSessionAdapter:
    """openai-agents AsyncSQLiteSession (aiosqlite)."""

    name = "async-sqlite"

    def __init__(self, tmpdir: str):
        from agents.extensions.memory import AsyncSQLiteSession

        self.db_path = os.path.join(tmpdir, "async.db")
        self.session = AsyncSQLiteSession("bench", self.db_path)

    async def add(self, items):
        await self.session.add_items(items)

    async def get_all(self):
        return await self.session.get_items()

    async def close(self):
        await self.session.close()


class AdvancedSQLiteSessionAdapter:
    """openai-agents AdvancedSQLiteSession (subclass of SQLiteSession with
    conversation-branching and usage-tracking tables on top).

    Included because it inherits the sync close() path while adding its own
    write path: a benchmark claim about SQLiteSession says nothing about a
    subclass that writes to more tables per add().
    """

    name = "advanced-sqlite"

    def __init__(self, tmpdir: str):
        from agents.extensions.memory import AdvancedSQLiteSession

        self.db_path = os.path.join(tmpdir, "advanced.db")
        self.session = AdvancedSQLiteSession(
            session_id="bench", db_path=self.db_path, create_tables=True
        )

    async def add(self, items):
        await self.session.add_items(items)

    async def get_all(self):
        return await self.session.get_items()

    async def close(self):
        await asyncio.to_thread(self.session.close)


class SQLAlchemySessionAdapter:
    """openai-agents SQLAlchemySession, on a SQLAlchemy async engine
    (sqlite+aiosqlite driver here; the same class fronts Postgres/MySQL).

    IMPORTANT — this backend has NO close(), and the `Session` protocol does
    not define one: of the four backends measured here, two expose a close()
    and two do not. `close()` below maps to `engine.dispose()` purely so the
    harness can release resources between trials.

    That mapping is NOT a close contract, so `native_close = False` and the
    close-semantics checks (ARIB-CONC-002/003) report `not_applicable` rather
    than a verdict. SQLAlchemy documents `dispose()` as replacing the
    connection pool, with the engine remaining usable afterwards — so a write
    that succeeds after dispose() is documented behaviour, not a silent
    resurrection. Reporting it as a violation would be manufacturing a finding
    against a promise the library never made (external review, 2026-08-02).

    The measurable fact stays, and it is about the protocol, not this class:
    swapping session backends "drop-in" also swaps whether *closed* exists.
    """

    name = "sqlalchemy"
    native_close = False

    @staticmethod
    def available() -> bool:
        return importlib.util.find_spec("sqlalchemy") is not None

    def __init__(self, tmpdir: str):
        from agents.extensions.memory import SQLAlchemySession

        self.db_path = os.path.join(tmpdir, "sqlalchemy.db")
        self.session = SQLAlchemySession.from_url(
            "bench", url=f"sqlite+aiosqlite:///{self.db_path}", create_tables=True
        )

    async def add(self, items):
        await self.session.add_items(items)

    async def get_all(self):
        return await self.session.get_items()

    async def close(self):
        await self.session.engine.dispose()


ADAPTERS = {
    a.name: a
    for a in (
        SQLiteSessionAdapter,
        AsyncSQLiteSessionAdapter,
        AdvancedSQLiteSessionAdapter,
        SQLAlchemySessionAdapter,
    )
}


def is_available(adapter_cls) -> bool:
    """An adapter is available unless it says otherwise. Fake stores injected
    by selftest.py never declare availability and must stay runnable."""
    checker = getattr(adapter_cls, "available", None)
    return bool(checker()) if callable(checker) else True


def runtime_object(adapter):
    """The thing actually under test: the runtime's own session object when the
    adapter wraps one, otherwise the adapter itself (selftest's fake stores are
    their own runtime). Used to verify a `native_close = False` claim against
    the real object instead of taking the adapter's word for it."""
    return getattr(adapter, "session", adapter)


def has_native_close(adapter_cls) -> bool:
    """Does the runtime itself define a close()? Default yes; a backend whose
    close() the harness had to invent must set `native_close = False` so the
    close-semantics checks abstain instead of inventing a verdict."""
    return bool(getattr(adapter_cls, "native_close", True))
