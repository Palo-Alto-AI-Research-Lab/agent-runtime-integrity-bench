"""Adapters wrap a runtime's session-memory store behind one tiny async contract.

Contract:
    adapter = AdapterCls(tmpdir)
    await adapter.add(items)          # append conversation items
    await adapter.get_all() -> list   # full visible history
    await adapter.close()             # close the store

Both openai-agents session flavors expose async add/get; SQLiteSession's
close() is sync and is wrapped in asyncio.to_thread so concurrent close is a
real thread-level race. Adding a runtime = one adapter class here.
"""

from __future__ import annotations

import asyncio
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


ADAPTERS = {a.name: a for a in (SQLiteSessionAdapter, AsyncSQLiteSessionAdapter)}
