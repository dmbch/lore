"""Sync bootstrap utilities for the PostgreSQL backend.

Migration runner and health check — called before the async event loop starts.
The ``_system`` table tracks applied migrations and runtime config (e.g.
embedding model). An advisory lock prevents concurrent migration runs.
"""

import contextlib
from typing import Any, LiteralString, cast

import psycopg
import psycopg.errors
import structlog

from lore.domain import StorageError
from lore.repositories.migrate import read_migrations

_MIGRATIONS_PACKAGE = "lore.repositories.postgres.migrations"
_POSTGRES_PREFIXES = ("postgresql://", "postgres://")
_SYSTEM_DDL = "CREATE TABLE IF NOT EXISTS _system (key TEXT PRIMARY KEY, value TEXT NOT NULL)"

log = structlog.get_logger(__name__)


def is_postgres(dsn: str) -> bool:
    """Return True if *dsn* uses a ``postgresql://`` or ``postgres://`` scheme."""
    return dsn.startswith(_POSTGRES_PREFIXES)


def _connect(dsn: str) -> psycopg.Connection[tuple[Any, ...]]:
    """Open a sync PostgreSQL connection in autocommit mode."""
    return psycopg.connect(dsn, autocommit=True)


def run_migrations(*, dsn: str, **params: int | str) -> None:
    """Apply PostgreSQL migrations under an advisory lock.

    Keyword arguments are substituted into migration SQL via
    ``str.format(**params)`` — callers provide schema parameters
    (e.g. ``embedding_dim=1024``, ``fulltext_config="english"``) and
    the SQL templates reference them.

    Each migration and its tracking record are applied in a single
    transaction — if the SQL fails, the tracking row is not written,
    so the next run retries cleanly.

    String param values are validated upstream by ``factory.run_migrations``
    against an identifier-and-spaces regex, so ``str.format`` interpolation
    remains injection-safe by construction.
    """
    conn = _connect(dsn)
    try:
        conn.execute(_SYSTEM_DDL)
        conn.execute("SELECT pg_advisory_lock(hashtext('lore_migrations'))")
        try:
            row = conn.execute(
                "SELECT value FROM _system WHERE key = 'latest_migration'"
            ).fetchone()
            latest = row[0] if row else ""

            migrations = read_migrations(_MIGRATIONS_PACKAGE)
            applied = 0
            skipped = 0
            for migration in migrations:
                if migration.name <= latest:
                    skipped += 1
                    continue
                with conn.transaction():
                    # psycopg requires LiteralString for execute(); cast is safe
                    # because the SQL comes from our own migration files,
                    # formatted only with config-vetted int / str params (see
                    # factory.run_migrations for the validation contract).
                    sql = migration.sql.format(**params)
                    conn.execute(cast("LiteralString", sql))
                    conn.execute(
                        "INSERT INTO _system (key, value)"
                        " VALUES ('latest_migration', %s)"
                        " ON CONFLICT(key) DO UPDATE SET value = EXCLUDED.value",
                        (migration.name,),
                    )
                applied += 1
        finally:
            # Advisory locks are session-scoped — released on disconnect anyway.
            # Suppress errors so a dead connection doesn't mask the original
            # migration failure.
            with contextlib.suppress(psycopg.Error):
                conn.execute("SELECT pg_advisory_unlock(hashtext('lore_migrations'))")
        latest_name = migrations[-1].name if migrations else ""
        log.info("migrations.applied", applied=applied, skipped=skipped, latest=latest_name)
    finally:
        conn.close()


def check_health(
    *,
    dsn: str,
    embedding_model: str,
    embedding_dim: int,
    fulltext_config: str,
) -> None:
    """Verify embedding model + tsvector lexer stability via ``_system``.

    Requires ``run_migrations()`` to have been called first — the ``_system``
    table is created by the migration runner. On first call, records the
    config values. On subsequent calls, refuses to start if the configured
    value diverges from the stored one — the ``fulltext`` generated column
    was built under the previous regconfig and any query against the new one
    would silently return wrong rankings.
    """
    conn = _connect(dsn)
    try:
        # Atomic upsert: INSERT the model name; if the key already exists,
        # DO UPDATE SET value = _system.value is a no-op that keeps the
        # existing value unchanged. RETURNING gives us the row's value in
        # both paths (new insert or existing conflict) with no TOCTOU gap.
        # The no-op UPDATE is the cheapest way to trigger RETURNING on conflict.
        try:
            row = conn.execute(
                "INSERT INTO _system (key, value) VALUES ('embedding_model', %s)"
                " ON CONFLICT (key) DO UPDATE SET value = _system.value"
                " RETURNING value",
                (embedding_model,),
            ).fetchone()
        except psycopg.errors.UndefinedTable as e:
            msg = "run_migrations() must be called before check_health()"
            raise StorageError(msg) from e
        if row is not None and row[0] != embedding_model:
            msg = (
                f"Embedding model mismatch: database has {row[0]!r},"
                f" configured {embedding_model!r}. Rebuild the vector space."
            )
            raise StorageError(msg)
        dim_row = conn.execute(
            "INSERT INTO _system (key, value) VALUES ('embedding_dimensions', %s)"
            " ON CONFLICT (key) DO UPDATE SET value = _system.value"
            " RETURNING value",
            (str(embedding_dim),),
        ).fetchone()
        if dim_row is not None and int(dim_row[0]) != embedding_dim:
            msg = (
                f"Embedding dimensions mismatch: database has {dim_row[0]},"
                f" configured {embedding_dim}. Rebuild the vector space."
            )
            raise StorageError(msg)
        fts_row = conn.execute(
            "INSERT INTO _system (key, value) VALUES ('fulltext_config', %s)"
            " ON CONFLICT (key) DO UPDATE SET value = _system.value"
            " RETURNING value",
            (fulltext_config,),
        ).fetchone()
        if fts_row is not None and fts_row[0] != fulltext_config:
            msg = (
                f"Fulltext config mismatch: database has {fts_row[0]!r},"
                f" configured {fulltext_config!r}. Rebuild the FTS index."
            )
            raise StorageError(msg)
    finally:
        conn.close()
