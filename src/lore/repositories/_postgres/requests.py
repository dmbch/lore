"""PostgreSQL implementation of RequestRepository."""

from typing import Any

import psycopg

from lore.repositories._postgres._errors import translate
from lore.repositories._records import RequestRecord


class PostgresRequestRepository:
    """Structured request store backed by PostgreSQL."""

    def __init__(self, conn: psycopg.AsyncConnection[Any]) -> None:
        self._conn = conn

    async def store(self, record: RequestRecord) -> None:
        try:
            await self._conn.execute(
                "INSERT INTO requests"
                " (id, oracle_id, timestamp, question, context, hypothesis, reasoning, confidence)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
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
        except psycopg.Error as e:
            translate(e)
