"""PostgreSQL implementation of CacheRepository."""

from typing import Any, NoReturn

import psycopg
import psycopg.errors
from psycopg.rows import dict_row
from pydantic import ValidationError

from lore.domain import StorageError
from lore.repositories._postgres._errors import translate
from lore.repositories._records import CacheEntry


def _translate_session_statement(e: psycopg.Error) -> NoReturn:
    """Statement-level translation for the autocommit ``session()`` methods.

    ``translate`` re-raises ``SerializationFailure`` for ``transaction()``'s
    retry contract, but no converting scope sits above an autocommit
    statement, and the connection is pinned SERIALIZABLE even there: the
    sweep's DELETE contending with an upsert on the same row raises 40001
    mid-statement. A one-shot statement has no retry context to preserve,
    so the raw psycopg class must not cross the repository boundary; it
    degrades to ``StorageError`` like any other driver fault.
    """
    if isinstance(e, psycopg.errors.SerializationFailure):
        raise StorageError(str(e)) from e
    translate(e)


class PostgresCacheRepository:
    """Operational key-value cache backed by PostgreSQL."""

    def __init__(self, conn: psycopg.AsyncConnection[Any]) -> None:
        self._conn = conn

    async def get_entry(self, *, collection: str, key: str) -> CacheEntry | None:
        try:
            cur = self._conn.cursor(row_factory=dict_row)
            await cur.execute(
                "SELECT collection, key, value, created_at, expires_at"
                " FROM _cache WHERE collection = %s AND key = %s",
                (collection, key),
            )
            row = await cur.fetchone()
        except psycopg.Error as e:
            _translate_session_statement(e)
        if row is None:
            return None
        # Validating constructor, not model_construct(): the table has no
        # CHECK constraints, so the record validators are the only guard.
        # A row that fails them is corrupt storage, hence StorageError.
        try:
            return CacheEntry(
                collection=row["collection"],
                key=row["key"],
                value=row["value"],
                created_at=row["created_at"],
                expires_at=row["expires_at"],
            )
        except ValidationError as e:
            msg = f"corrupt _cache row for ({collection!r}, {key!r})"
            raise StorageError(msg) from e

    async def put_entry(
        self,
        *,
        collection: str,
        key: str,
        value: str,
        created_at: int,
        expires_at: int | None,
    ) -> None:
        # Validate before the write, mirroring the read path; a failure here
        # is a caller bug, so the ValidationError propagates untranslated.
        entry = CacheEntry(
            collection=collection,
            key=key,
            value=value,
            created_at=created_at,
            expires_at=expires_at,
        )
        try:
            await self._conn.execute(
                "INSERT INTO _cache (collection, key, value, created_at, expires_at)"
                " VALUES (%s, %s, %s, %s, %s)"
                " ON CONFLICT(collection, key) DO UPDATE SET value = EXCLUDED.value,"
                " created_at = EXCLUDED.created_at, expires_at = EXCLUDED.expires_at",
                (entry.collection, entry.key, entry.value, entry.created_at, entry.expires_at),
            )
        except psycopg.Error as e:
            _translate_session_statement(e)

    async def delete_entry(self, *, collection: str, key: str) -> bool:
        try:
            cur = await self._conn.execute(
                "DELETE FROM _cache WHERE collection = %s AND key = %s",
                (collection, key),
            )
        except psycopg.Error as e:
            _translate_session_statement(e)
        return cur.rowcount > 0

    async def delete_expired(self, *, now: int) -> int:
        # Transaction-scoped advisory try-lock: when several replicas sweep
        # on the same schedule, one does the work and the rest skip. Released
        # automatically at COMMIT/ROLLBACK, so it cannot leak on a pooled
        # connection; outside a transaction it is a per-statement no-op and
        # the sweep simply runs unguarded.
        try:
            cur = await self._conn.execute(
                "SELECT pg_try_advisory_xact_lock(hashtext('lore_cache_sweep'))"
            )
            row = await cur.fetchone()
            if row is None or not row[0]:
                return 0
            cur = await self._conn.execute(
                "DELETE FROM _cache WHERE expires_at IS NOT NULL AND expires_at < %s",
                (now,),
            )
        except psycopg.Error as e:
            translate(e)
        return cur.rowcount
