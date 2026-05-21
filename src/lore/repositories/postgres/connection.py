"""PostgreSQL connection helper — build the AsyncConnectionPool with pgvector registered.

The pool ``PostgresPool`` owns the ``psycopg_pool.AsyncConnectionPool`` and
exposes ``session()`` / ``transaction()``. This module exposes only the
``create_pool`` helper that constructs the inner pool with the pgvector
configure callback wired in.
"""

from typing import Any

import psycopg
from pgvector.psycopg import register_vector_async
from psycopg import IsolationLevel
from psycopg_pool import AsyncConnectionPool

from lore.config import PostgresConfig


async def _configure_connection(conn: psycopg.AsyncConnection[Any]) -> None:
    """Called by the pool for each new connection.

    Registers pgvector and pins the isolation level to SERIALIZABLE so every
    transaction the connection opens is safe against write-skew. The
    autocommit ``session()`` scope is unaffected — isolation level is
    irrelevant when each statement is its own transaction.
    """
    await register_vector_async(conn)
    await conn.set_isolation_level(IsolationLevel.SERIALIZABLE)


async def create_pool(
    *, dsn: str, config: PostgresConfig
) -> AsyncConnectionPool[psycopg.AsyncConnection[Any]]:
    """Create a connection pool with pgvector registered.

    The ``configure`` callback registers pgvector on every new connection
    the pool creates. Schema is applied by ``run_migrations()`` in the
    factory before this function is called.
    """
    pool: AsyncConnectionPool[psycopg.AsyncConnection[Any]] = AsyncConnectionPool(
        dsn,
        min_size=config.min_size,
        max_size=config.max_size,
        timeout=config.getconn_timeout,
        max_waiting=config.max_waiting,
        open=False,
        kwargs={"autocommit": True},
        configure=_configure_connection,
        # Validate connections on borrow. The ``check`` callback runs once
        # per ``getconn()``; on failure psycopg_pool discards the connection
        # and tries another, so callers see only working connections. This
        # is what lets the readiness probe trust ``pool.session()`` without
        # an extra roundtrip — silent half-closes after a network partition
        # are caught at borrow time. Cost: one ``SELECT 1`` per borrow on
        # every consult call (negligible for Lore's traffic shape).
        check=AsyncConnectionPool.check_connection,
    )
    await pool.open(wait=True)
    return pool
