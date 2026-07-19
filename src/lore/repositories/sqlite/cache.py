"""SQLite implementation of CacheRepository."""

import sqlite3

import aiosqlite
from pydantic import ValidationError

from lore.domain import StorageError
from lore.repositories.records import CacheEntry


class SqliteCacheRepository:
    """Operational key-value cache backed by SQLite."""

    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def get_entry(self, *, collection: str, key: str) -> CacheEntry | None:
        try:
            cursor = await self._conn.execute(
                "SELECT collection, key, value, created_at, expires_at"
                " FROM _cache WHERE collection = ? AND key = ?",
                (collection, key),
            )
            row = await cursor.fetchone()
        except (sqlite3.Error, ValueError) as e:
            raise StorageError(str(e)) from e
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
                " VALUES (?, ?, ?, ?, ?)"
                " ON CONFLICT(collection, key) DO UPDATE SET value = excluded.value,"
                " created_at = excluded.created_at, expires_at = excluded.expires_at",
                (entry.collection, entry.key, entry.value, entry.created_at, entry.expires_at),
            )
        except (sqlite3.Error, ValueError) as e:
            raise StorageError(str(e)) from e

    async def delete_entry(self, *, collection: str, key: str) -> bool:
        try:
            cursor = await self._conn.execute(
                "DELETE FROM _cache WHERE collection = ? AND key = ?",
                (collection, key),
            )
        except (sqlite3.Error, ValueError) as e:
            raise StorageError(str(e)) from e
        return cursor.rowcount > 0
