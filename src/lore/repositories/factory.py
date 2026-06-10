"""Repository factory — settings to RepositoryPool.

Bootstrap infrastructure, not a layer. ``connect()`` opens the backend
named by ``settings.dsn`` and returns a RepositoryPool; the pool creates
per-request repo bundles. ``run_migrations()`` and ``check_health()`` are
sync bootstrap utilities that run before the async event loop starts.
``make_probe()`` returns a readiness-probe closure over a live pool.

All four entry points take ``LoreSettings`` whole (except ``make_probe``,
which takes a pool) and read the backend choice from ``settings.dsn``.
Backend-specific logic lives in ``postgres/bootstrap.py`` and
``sqlite/bootstrap.py``; this module is the only routing surface.
"""

import asyncio
import sqlite3
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

import psycopg

from lore.domain import StorageError
from lore.repositories.postgres import PostgresPool, postgres_bootstrap
from lore.repositories.postgres.bootstrap import is_postgres
from lore.repositories.protocols import RepositoryPool
from lore.repositories.sqlite import SqlitePool, sqlite_bootstrap
from lore.repositories.sqlite.bootstrap import is_sqlite

if TYPE_CHECKING:
    from lore.config import LoreSettings


def _unsupported_dsn(dsn: str) -> ValueError:
    """Build a ValueError for an unrecognized DSN scheme."""
    msg = (
        f"Unsupported DSN: {dsn!r}. Expected 'postgresql://...',"
        " 'postgres://...', or 'sqlite:///<path>'."
    )
    return ValueError(msg)


def _fulltext_config(settings: LoreSettings) -> str:
    """Pick the backend-appropriate fulltext config."""
    if is_postgres(settings.dsn):
        return settings.postgres.fulltext_config
    if is_sqlite(settings.dsn):
        return settings.sqlite.fulltext_config
    raise _unsupported_dsn(settings.dsn)


def run_migrations(*, settings: LoreSettings, embedding_dim: int) -> None:
    """Apply migrations for the backend identified by ``settings.dsn``.

    Reads the backend-appropriate ``fulltext_config`` from ``settings.postgres``
    or ``settings.sqlite``. ``embedding_dim`` is supplied separately because it
    is resolved at bootstrap from LiteLLM model info (a cross-layer fact that
    belongs in the composition root, not in TOML).

    Both placeholder values are substituted into migration SQL templates via
    ``str.format()``. Pydantic validation upstream confines them to safe
    shapes: ``fulltext_config`` to a strict identifier regex, the integer to
    ``int`` under ``strict=True``. The migration runner trusts those
    guarantees.

    Sync — runs at bootstrap before the async event loop starts.
    """
    params: dict[str, int | str] = {
        "embedding_dim": embedding_dim,
        "fulltext_config": _fulltext_config(settings),
    }
    if is_postgres(settings.dsn):
        postgres_bootstrap.run_migrations(dsn=settings.dsn, **params)
    else:
        sqlite_bootstrap.run_migrations(dsn=settings.dsn, **params)


def check_health(*, settings: LoreSettings, embedding_dim: int) -> None:
    """Verify DB availability and schema-bound config stability via ``_system``.

    On first call, stores ``embedding_model``, ``embedding_dim``, and
    ``fulltext_config``. On subsequent calls, refuses to start if any of
    these differ from the recorded value — the vector space and FTS index
    are bound to those choices at schema creation, and silent drift would
    produce wrong retrieval results.

    Sync — runs at bootstrap before the async event loop starts.
    """
    fulltext_config = _fulltext_config(settings)
    if is_postgres(settings.dsn):
        postgres_bootstrap.check_health(
            dsn=settings.dsn,
            embedding_model=settings.embedding.model,
            embedding_dim=embedding_dim,
            fulltext_config=fulltext_config,
        )
    else:
        sqlite_bootstrap.check_health(
            dsn=settings.dsn,
            embedding_model=settings.embedding.model,
            embedding_dim=embedding_dim,
            fulltext_config=fulltext_config,
        )


def make_probe(
    pool: RepositoryPool,
    *,
    timeout: float = 5.0,
) -> Callable[[], Awaitable[None]]:
    """Build a readiness probe over ``pool``.

    The probe acquires a connection from ``pool`` via ``session()`` and
    immediately releases it. For Postgres, the pool's ``check`` callback
    validates the connection on borrow — so the probe answers "can
    consult get a working connection right now?" without an extra
    roundtrip. For SQLite, the probe verifies that a per-connection lock
    against the database file can be acquired.

    The acquire-release cycle is bounded by ``timeout``; pool failures
    (``pool.session()`` already raises ``StorageError``) propagate as-is,
    and timeouts translate to ``StorageError`` so ``/ready`` sees one
    consistent error class.
    """

    async def probe() -> None:
        try:
            async with asyncio.timeout(timeout), pool.session():
                pass
        except TimeoutError as e:
            msg = f"readiness probe timed out after {timeout}s"
            raise StorageError(msg) from e

    return probe


async def connect(settings: LoreSettings) -> RepositoryPool:
    """Open a connection pool for the backend identified by ``settings.dsn``.

    Callers must run ``run_migrations(settings=..., embedding_dim=...)`` before
    the first ``connect()`` call — migrations are a one-time bootstrap step,
    not a per-pool concern.

    For PostgreSQL DSNs the pool is sized from ``settings.postgres``; for
    SQLite the field is ignored.

    Usage::

        run_migrations(settings=settings, embedding_dim=dim)
        check_health(settings=settings, embedding_dim=dim)
        pool = await connect(settings)
        async with pool.session() as repos:
            await repos.requests.store(record)
        async with pool.transaction() as repos:
            await repos.attestations.append(...)
        await pool.close()
    """
    if is_postgres(settings.dsn):
        try:
            return await PostgresPool.create(dsn=settings.dsn, config=settings.postgres)
        except (psycopg.Error, OSError) as e:
            raise StorageError(str(e)) from e
    if is_sqlite(settings.dsn):
        try:
            return await SqlitePool.create(settings.dsn)
        except (sqlite3.Error, OSError) as e:
            raise StorageError(str(e)) from e
    raise _unsupported_dsn(settings.dsn)
