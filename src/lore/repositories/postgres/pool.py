"""PostgreSQL connection pool — RepositoryPool implementation.

PostgreSQL concurrency model: psycopg_pool.AsyncConnectionPool. Each scope
checks out a connection on entry and returns it on exit. ``transaction()``
delegates to psycopg's native transaction CM, which already handles
best-effort rollback for any exception class (rollback errors are logged
and suppressed; the original exception always propagates).
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any  # Any: psycopg's row factory type parameter; default (tuple) is untyped

import psycopg
import psycopg.errors
from psycopg_pool import AsyncConnectionPool

from lore.domain import RetryableTransactionError, StorageError
from lore.repositories.config import PostgresConfig
from lore.repositories.postgres.attestations import PostgresAttestationsRepository
from lore.repositories.postgres.connection import create_pool
from lore.repositories.postgres.hypotheses import PostgresHypothesisRepository
from lore.repositories.postgres.requests import PostgresRequestRepository
from lore.repositories.protocols import Repositories, RepositoryPool


class PostgresPool:
    """Connection pool. Fully concurrent."""

    def __init__(
        self,
        *,
        pool: AsyncConnectionPool[psycopg.AsyncConnection[Any]],
        fulltext_config: str,
    ) -> None:
        self._pool = pool
        self._fulltext_config = fulltext_config

    @classmethod
    async def create(cls, *, dsn: str, config: PostgresConfig) -> PostgresPool:
        pool = await create_pool(dsn=dsn, config=config)
        return cls(pool=pool, fulltext_config=config.fulltext_config)

    def _bundle(self, raw: psycopg.AsyncConnection[Any]) -> Repositories:
        return Repositories(
            hypotheses=PostgresHypothesisRepository(
                conn=raw, fulltext_config=self._fulltext_config
            ),
            attestations=PostgresAttestationsRepository(raw),
            requests=PostgresRequestRepository(raw),
        )

    async def _acquire(self) -> psycopg.AsyncConnection[Any]:
        # psycopg_pool.PoolTimeout subclasses psycopg.OperationalError → psycopg.Error.
        # OSError covers connection-establishment failures (the pool may open a new
        # backend connection on demand). Mirrors factory.connect()'s catch set.
        try:
            return await self._pool.getconn()
        except (psycopg.Error, OSError) as e:
            raise StorageError(str(e)) from e

    @asynccontextmanager
    async def session(self) -> AsyncGenerator[Repositories]:
        raw = await self._acquire()
        try:
            yield self._bundle(raw)
        finally:
            await self._pool.putconn(raw)

    @asynccontextmanager
    async def transaction(self) -> AsyncGenerator[Repositories]:
        # SERIALIZABLE is set once per connection in ``_configure_connection``;
        # every transaction here inherits it. The orchestrator catches
        # RetryableTransactionError and re-runs on a fresh snapshot — see
        # docs/architecture.md, write path.
        raw = await self._acquire()
        try:
            try:
                async with raw.transaction():
                    yield self._bundle(raw)
            except psycopg.errors.SerializationFailure as e:
                raise RetryableTransactionError(str(e)) from e
            except psycopg.Error as e:
                raise StorageError(str(e)) from e
        finally:
            await self._pool.putconn(raw)

    async def close(self) -> None:
        await self._pool.close()


# Static Protocol verification — catches signature drift at type-check time.
_pool_check: type[RepositoryPool] = PostgresPool
