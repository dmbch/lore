"""Sync bootstrap utilities for the SQLite backend.

Migration runner and health check — called before the async event loop starts.
The ``_system`` table tracks applied migrations and runtime config (e.g.
embedding model). SQLite's file-level locking serializes concurrent writers.
"""

import sqlite3

import sqlite_vec
import structlog

from lore.domain import StorageError
from lore.repositories.migrate import read_migrations

_MIGRATIONS_PACKAGE = "lore.repositories.sqlite.migrations"
_SQLITE_PREFIX = "sqlite:///"
_SYSTEM_DDL = "CREATE TABLE IF NOT EXISTS _system (key TEXT PRIMARY KEY, value TEXT NOT NULL)"

log = structlog.get_logger(__name__)


def is_sqlite(dsn: str) -> bool:
    """Return True if *dsn* uses the ``sqlite:///`` scheme."""
    return dsn.startswith(_SQLITE_PREFIX)


def strip_dsn(dsn: str) -> str:
    """Strip the ``sqlite:///`` scheme prefix, returning the file path."""
    return dsn[len(_SQLITE_PREFIX) :]


def _connect(dsn: str) -> sqlite3.Connection:
    """Open a sync SQLite connection with sqlite-vec loaded.

    Uses ``isolation_level=None`` (autocommit) so callers control
    transaction boundaries explicitly via ``BEGIN``/``COMMIT``. Enables
    WAL journal mode after extension load — the very first interaction
    with a fresh DB file (migration apply) then runs under WAL rather
    than the default rollback-journal mode, matching the async runtime
    ``connect()`` so a single DB file is never opened under two
    different journal modes within one process.
    """
    path = strip_dsn(dsn)
    if path == ":memory:":
        msg = (
            "SQLite :memory: is not supported — each connection gets a private"
            " database, so migrations and runtime would see different DBs."
            " Please use a (tmp) file path, e.g. sqlite:////tmp/lore-dev.db"
        )
        raise ValueError(msg)
    conn = sqlite3.connect(path, isolation_level=None)
    conn.enable_load_extension(True)  # noqa: FBT003 - positional-only sqlite3 API
    conn.load_extension(sqlite_vec.loadable_path())
    conn.enable_load_extension(False)  # noqa: FBT003 - positional-only sqlite3 API
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def run_migrations(*, dsn: str, **params: int | str) -> None:
    """Apply SQLite migrations with sqlite-vec loaded.

    Keyword arguments are substituted into migration SQL via
    ``str.format(**params)`` — callers provide schema parameters
    (e.g. ``embedding_dim=1024``, ``fulltext_config="porter unicode61"``)
    and the SQL templates reference them.

    Uses ``executescript()`` for multi-statement SQL execution. Virtual
    table DDL (``CREATE VIRTUAL TABLE``) auto-commits outside any
    surrounding transaction in SQLite, so migrations use ``IF NOT EXISTS``
    to make retries idempotent after partial failure.

    The tracking record is written after the migration script succeeds.
    If the script fails, the tracking row is not written, so the next
    run retries cleanly.

    String param values are validated upstream by ``factory.run_migrations``
    against an identifier-and-spaces regex, so ``str.format`` interpolation
    remains injection-safe by construction.
    """
    conn = _connect(dsn)
    try:
        conn.execute(_SYSTEM_DDL)
        row = conn.execute("SELECT value FROM _system WHERE key = 'latest_migration'").fetchone()
        latest = row[0] if row else ""

        migrations = read_migrations(_MIGRATIONS_PACKAGE)
        applied = 0
        skipped = 0
        for migration in migrations:
            if migration.name <= latest:
                skipped += 1
                continue
            sql = migration.sql.format(**params)
            conn.executescript(sql)
            conn.execute(
                "INSERT INTO _system (key, value) VALUES ('latest_migration', ?)"
                " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (migration.name,),
            )
            applied += 1
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
    """Verify embedding model + FTS5 tokenizer stability via ``_system``.

    Requires ``run_migrations()`` to have been called first — the ``_system``
    table is created by the migration runner. On first call, records the
    config values. On subsequent calls, refuses to start if the configured
    value diverges from the stored one — the FTS virtual table was built
    under the previous tokenizer and any query against the new one would
    silently return wrong results.
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
                "INSERT INTO _system (key, value) VALUES ('embedding_model', ?)"
                " ON CONFLICT(key) DO UPDATE SET value = _system.value"
                " RETURNING value",
                (embedding_model,),
            ).fetchone()
        except sqlite3.OperationalError as e:
            msg = "run_migrations() must be called before check_health()"
            raise StorageError(msg) from e
        # No explicit COMMIT needed — _connect uses isolation_level=None
        # (autocommit), so the upsert commits immediately.
        if row is not None and row[0] != embedding_model:
            msg = (
                f"Embedding model mismatch: database has {row[0]!r},"
                f" configured {embedding_model!r}. Rebuild the vector space."
            )
            raise StorageError(msg)
        dim_row = conn.execute(
            "INSERT INTO _system (key, value) VALUES ('embedding_dimensions', ?)"
            " ON CONFLICT(key) DO UPDATE SET value = _system.value"
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
            "INSERT INTO _system (key, value) VALUES ('fulltext_config', ?)"
            " ON CONFLICT(key) DO UPDATE SET value = _system.value"
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
