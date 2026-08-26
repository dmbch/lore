"""SQLite connection pool: RepositoryPool implementation.

SQLite concurrency model: single connection + asyncio.Lock. Each scope
acquires the lock on entry and releases on exit. ``transaction()`` issues
explicit BEGIN / COMMIT / ROLLBACK around the yield, mirroring psycopg's
native transaction CM behavior. ``session()`` runs autocommit: each
statement commits independently because ``aiosqlite.connect`` was opened
with ``isolation_level=None``.
"""

import asyncio
import contextlib
import sqlite3
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import aiosqlite

from lore.domain import StorageError
from lore.repositories._protocols import Repositories, RepositoryPool
from lore.repositories._sqlite.attestations import SqliteAttestationsRepository
from lore.repositories._sqlite.bootstrap import strip_dsn
from lore.repositories._sqlite.cache import SqliteCacheRepository
from lore.repositories._sqlite.connection import connect as sqlite_connect
from lore.repositories._sqlite.hypotheses import SqliteHypothesisRepository
from lore.repositories._sqlite.requests import SqliteRequestRepository


class SqlitePool:
    """Single connection + asyncio.Lock. Serializes concurrent requests."""

    def __init__(self, *, conn: aiosqlite.Connection, lock: asyncio.Lock) -> None:
        self._conn = conn
        self._lock = lock

    @classmethod
    async def create(cls, dsn: str) -> SqlitePool:
        conn = await sqlite_connect(strip_dsn(dsn))
        return cls(conn=conn, lock=asyncio.Lock())

    def _bundle(self) -> Repositories:
        return Repositories(
            hypotheses=SqliteHypothesisRepository(self._conn),
            attestations=SqliteAttestationsRepository(self._conn),
            requests=SqliteRequestRepository(self._conn),
            cache=SqliteCacheRepository(self._conn),
        )

    @asynccontextmanager
    async def session(self) -> AsyncGenerator[Repositories]:
        async with self._lock:
            # SELECT 1 on entry mirrors the Postgres ``check_connection``
            # callback at the probe's borrow site: both backends yield a
            # verified-alive connection. ``transaction()`` relies on
            # ``BEGIN``'s implicit roundtrip instead. ``ValueError`` covers
            # closed-connection driver state.
            try:
                await self._conn.execute("SELECT 1")
            except (sqlite3.Error, ValueError) as e:
                raise StorageError(str(e)) from e
            yield self._bundle()

    @asynccontextmanager
    async def transaction(self) -> AsyncGenerator[Repositories]:
        async with self._lock:
            try:
                await self._conn.execute("BEGIN")
            except (sqlite3.Error, ValueError) as e:
                raise StorageError(str(e)) from e
            try:
                yield self._bundle()
                try:
                    await self._conn.commit()
                except (sqlite3.Error, ValueError) as e:
                    raise StorageError(str(e)) from e
            except sqlite3.Error as e:
                # Body-level DB errors map to the domain StorageError so callers
                # handle one exception class regardless of backend (parity with
                # PostgresPool.transaction, which catches psycopg.Error only).
                # ValueError is intentionally not caught here: it's reserved
                # for the BEGIN/commit arms above as a defense against driver-
                # state errors (e.g. closed connection on commit), not body
                # code paths. Non-DB exceptions propagate as their original
                # class via the BaseException arm below.
                with contextlib.suppress(sqlite3.Error, ValueError):
                    await self._conn.rollback()
                raise StorageError(str(e)) from e
            except BaseException:
                with contextlib.suppress(sqlite3.Error, ValueError):
                    await self._conn.rollback()
                raise

    async def close(self) -> None:
        await self._conn.close()


# Static Protocol verification: catches signature drift at type-check time.
_pool_check: type[RepositoryPool] = SqlitePool
