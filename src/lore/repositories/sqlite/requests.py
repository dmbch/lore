"""SQLite implementation of RequestRepository."""

import sqlite3

import aiosqlite

from lore.domain import StorageError
from lore.repositories.records import RequestRecord


class SqliteRequestRepository:
    """Structured request store backed by SQLite."""

    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def store(self, record: RequestRecord) -> None:
        """Persist a structured request record."""
        try:
            await self._conn.execute(
                "INSERT INTO requests"
                " (id, oracle_id, timestamp, question, context, hypothesis, reasoning, confidence)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    record.id,
                    record.oracle_id,
                    record.timestamp,
                    record.question,
                    record.context,
                    record.hypothesis,
                    record.reasoning,
                    record.confidence,
                ),
            )
        except (sqlite3.Error, ValueError) as e:
            raise StorageError(str(e)) from e
