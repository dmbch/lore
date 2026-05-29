"""Shared fixtures for repository tests.

Backend-parametrized — tests run against SQLite (always) and PostgreSQL
(via testcontainers or ``LORE_TEST_POSTGRES_DSN``). Tests import
Protocol-typed fixtures, not implementations. Only this file knows about
concrete backends.

Design:
- Session-scoped DSN fixtures handle migration (once) and cleanup.
- Per-test ``backend`` fixture handles isolation (DELETE/TRUNCATE) and
  pool lifecycle. The fixture binds repos to a fixture-owned raw connection
  so tests can mix Protocol-typed reads/writes with raw-SQL infrastructure
  (truncation, sabotage, post-hoc reads bypassing the layer).
- ``pool`` fixture exposes the pool itself for tests that need to drive
  their own scopes (``pool.session()`` / ``pool.transaction()``).
"""

import contextlib
import os
import tempfile
from collections.abc import AsyncGenerator, Awaitable, Callable, Iterator
from typing import Any, NamedTuple, cast

import aiosqlite
import psycopg
import pytest
from testcontainers.postgres import PostgresContainer

from lore.config import LoreSettings, PostgresConfig
from lore.config.types import SqliteConfig
from lore.repositories import (
    AttestationRecord,
    AttestationsRepository,
    HypothesisRepository,
    Repositories,
    RepositoryPool,
    RequestRecord,
    RequestRepository,
    connect,
    records,
    run_migrations,
)
from lore.repositories.postgres.attestations import PostgresAttestationsRepository
from lore.repositories.postgres.hypotheses import PostgresHypothesisRepository
from lore.repositories.postgres.pool import PostgresPool
from lore.repositories.postgres.requests import PostgresRequestRepository
from lore.repositories.sqlite.attestations import SqliteAttestationsRepository
from lore.repositories.sqlite.hypotheses import SqliteHypothesisRepository
from lore.repositories.sqlite.pool import SqlitePool
from lore.repositories.sqlite.requests import SqliteRequestRepository
from tests.repositories._orchestrator_fixtures import make_settings as _make_settings

# Test PostgresConfig — defaults from PLAN.md locked positions, used wherever
# a repository test instantiates the pool.
TEST_POSTGRES_CONFIG: PostgresConfig = PostgresConfig(
    min_size=1, max_size=20, getconn_timeout=10.0, max_waiting=50
)

# Schema dimension for test migrations. All test embedding vectors must match.
SCHEMA_DIM: int = 1024

# Schema-bound FTS configs for tests. Default to the production defaults so
# the test schema mirrors what an operator gets out of the box.
PG_FULLTEXT_CONFIG: str = "english"
SQLITE_FULLTEXT_CONFIG: str = "porter unicode61"

# Module-level singleton for SqliteConfig (avoids the function-call-in-default
# antipattern flagged by ruff B008).
TEST_SQLITE_CONFIG: SqliteConfig = SqliteConfig(fulltext_config=SQLITE_FULLTEXT_CONFIG)


def make_settings(
    *,
    dsn: str,
    postgres: PostgresConfig = TEST_POSTGRES_CONFIG,
    sqlite: SqliteConfig = TEST_SQLITE_CONFIG,
    embedding_model: str = "test/embedding-model",
) -> LoreSettings:
    """Minimal LoreSettings for tests that exercise the factory."""
    return _make_settings(
        dsn=dsn, embedding_model=embedding_model, postgres=postgres, sqlite=sqlite
    )


# No-decay half-life safe for both pure math and SQL (int(5 * inf) overflows).
NO_DECAY_TRUST_HL: float = 1e12

# Local float-comparison tolerance — avoids cross-package private import.
EPSILON: float = 1e-9

# All domain tables, ordered for DELETE (children before parents).
# attestations → requests (via correlation_id FK) and → hypotheses (via
# hypothesis_id FK), so attestations must be deleted first.
_DOMAIN_TABLES = ("attestations", "requests", "hypotheses")
# SQLite virtual tables with their own storage — not cleaned by DELETE FROM hypotheses.
_SQLITE_VIRTUAL_TABLES = ("vec_hypotheses", "fts_hypotheses")


class BackendFixture(NamedTuple):
    """Pool, repos, and the raw connection bound to those repos."""

    pool: RepositoryPool
    hypotheses: HypothesisRepository
    attestations: AttestationsRepository
    requests: RequestRepository
    raw_conn: aiosqlite.Connection | psycopg.AsyncConnection[Any]


# --- Session-scoped DSN fixtures ---


@pytest.fixture(scope="session")
def sqlite_dsn_session() -> Iterator[str]:
    """Session-scoped SQLite DSN — one temp file, one migration run."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    dsn = f"sqlite:///{path}"
    run_migrations(settings=make_settings(dsn=dsn), embedding_dim=SCHEMA_DIM)
    yield dsn
    os.unlink(path)


@pytest.fixture(scope="session")
def pg_dsn_session() -> Iterator[str]:
    """Session-scoped PostgreSQL DSN via env var or testcontainers."""
    env_dsn = os.environ.get("LORE_TEST_POSTGRES_DSN")
    if env_dsn:
        _reset_pg_schema(env_dsn)
        yield env_dsn
        return
    try:
        pg = PostgresContainer("pgvector/pgvector:pg18", driver=None)
        pg.start()
    except Exception:
        pytest.skip("PostgreSQL not available (no LORE_TEST_POSTGRES_DSN, no Docker)")
        return  # unreachable, but satisfies the generator protocol for type checkers
    try:
        dsn = pg.get_connection_url()
        _reset_pg_schema(dsn)
        yield dsn
    finally:
        pg.stop()


def drop_pg_tables(dsn: str) -> None:
    """Drop all domain and migration-tracking tables from a PostgreSQL database."""
    conn = psycopg.connect(dsn, autocommit=True)
    try:
        conn.execute("DROP TABLE IF EXISTS _system, attestations, requests, hypotheses CASCADE")
    finally:
        conn.close()


def _reset_pg_schema(dsn: str) -> None:
    """Drop all tables and reapply schema."""
    drop_pg_tables(dsn)
    run_migrations(settings=make_settings(dsn=dsn), embedding_dim=SCHEMA_DIM)


# --- Per-test pool + backend fixtures ---


_BACKEND_PARAMS = [pytest.param("sqlite", id="sqlite"), pytest.param("postgres", id="postgres")]


def _bundle_for_sqlite(raw: aiosqlite.Connection) -> Repositories:
    return Repositories(
        hypotheses=SqliteHypothesisRepository(raw),
        attestations=SqliteAttestationsRepository(raw),
        requests=SqliteRequestRepository(raw),
    )


def _bundle_for_postgres(raw: psycopg.AsyncConnection[Any]) -> Repositories:
    return Repositories(
        hypotheses=PostgresHypothesisRepository(conn=raw, fulltext_config=PG_FULLTEXT_CONFIG),
        attestations=PostgresAttestationsRepository(raw),
        requests=PostgresRequestRepository(raw),
    )


async def _truncate(backend: str, raw: aiosqlite.Connection | psycopg.AsyncConnection[Any]) -> None:
    if backend == "sqlite":
        for table in (*_SQLITE_VIRTUAL_TABLES, *_DOMAIN_TABLES):
            await raw.execute(f"DELETE FROM {table}")  # table names are compile-time constants
    else:
        await raw.execute(f"TRUNCATE {', '.join(_DOMAIN_TABLES)} CASCADE")


@pytest.fixture(params=_BACKEND_PARAMS)
async def pool(
    request: pytest.FixtureRequest, sqlite_dsn_session: str
) -> AsyncGenerator[RepositoryPool]:
    """Open a pool, truncate domain tables, yield. No session held."""
    if request.param == "sqlite":
        dsn = sqlite_dsn_session
    else:
        dsn = request.getfixturevalue("pg_dsn_session")
    p = await connect(make_settings(dsn=dsn))

    # Truncate via a transient connection that doesn't survive into the test.
    if request.param == "sqlite":
        sqlite_pool = cast("SqlitePool", p)
        await _truncate("sqlite", sqlite_pool._conn)  # pyright: ignore[reportPrivateUsage]
    else:
        pg_pool = cast("PostgresPool", p)
        truncate_conn = await pg_pool._pool.getconn()  # pyright: ignore[reportPrivateUsage]
        try:
            await _truncate("postgres", truncate_conn)
        finally:
            await pg_pool._pool.putconn(truncate_conn)  # pyright: ignore[reportPrivateUsage]

    yield p
    await p.close()


@pytest.fixture
async def backend(pool: RepositoryPool) -> AsyncGenerator[BackendFixture]:
    """Bind repos to a fixture-owned raw connection.

    Repository operations on ``backend.hypotheses`` etc. run autocommit on
    the same connection as ``backend.raw_conn`` — so post-hoc raw reads see
    fixture writes immediately. Tests that want to drive ``pool.session()`` /
    ``pool.transaction()`` use ``backend.pool`` (or the ``pool`` fixture
    directly).
    """
    if isinstance(pool, SqlitePool):
        raw: aiosqlite.Connection | psycopg.AsyncConnection[Any] = pool._conn  # pyright: ignore[reportPrivateUsage]
        repos = _bundle_for_sqlite(pool._conn)  # pyright: ignore[reportPrivateUsage]
        yield BackendFixture(
            pool=pool,
            hypotheses=repos.hypotheses,
            attestations=repos.attestations,
            requests=repos.requests,
            raw_conn=raw,
        )
        return

    pg_pool = cast("PostgresPool", pool)
    pg_raw = await pg_pool._pool.getconn()  # pyright: ignore[reportPrivateUsage]
    repos = _bundle_for_postgres(pg_raw)
    try:
        yield BackendFixture(
            pool=pool,
            hypotheses=repos.hypotheses,
            attestations=repos.attestations,
            requests=repos.requests,
            raw_conn=pg_raw,
        )
    finally:
        with contextlib.suppress(Exception):
            await pg_pool._pool.putconn(pg_raw)  # pyright: ignore[reportPrivateUsage]


# --- Convenience fixtures ---


@pytest.fixture
def pg_dsn(pg_dsn_session: str) -> str:
    """Provide the PostgreSQL DSN for tests that need it directly."""
    return pg_dsn_session


@pytest.fixture
def hypothesis_repo(backend: BackendFixture) -> HypothesisRepository:
    return backend.hypotheses


@pytest.fixture
def attestations_repo(backend: BackendFixture) -> AttestationsRepository:
    return backend.attestations


@pytest.fixture
def request_repo(backend: BackendFixture) -> RequestRepository:
    return backend.requests


@pytest.fixture
def sabotage_connection(backend: BackendFixture) -> Callable[[], Awaitable[None]]:
    """Sabotage the raw connection. Repos will raise StorageError.

    Closes the underlying database connection (not just the wrapper),
    so subsequent repo operations fail with StorageError.
    """
    raw = backend.raw_conn

    async def _sabotage() -> None:
        await raw.close()

    return _sabotage


# --- Shared test helpers ---


async def seed_hypothesis(repo: HypothesisRepository) -> str:
    """Insert a hypothesis FK target. Returns hypothesis_id."""
    h = await repo.store(content="claim", embedding=[1.0 / SCHEMA_DIM] * SCHEMA_DIM, created_at=0)
    return h.id


async def seed_request(
    repo: RequestRepository,
    *,
    correlation_id: str,
    oracle_id: str = "sub:oracle-seed",
    timestamp: int = 0,
) -> None:
    """Insert a parent request row so an attestation FK-referencing this
    ``correlation_id`` can be appended. Call from tests that use
    ``append_attestation`` or otherwise construct attestations with
    non-default correlation IDs.
    """
    await repo.store(
        RequestRecord(
            id=correlation_id,
            oracle_id=oracle_id,
            timestamp=timestamp,
        )
    )


async def append_attestation(
    repo: AttestationsRepository,
    *,
    hypothesis_id: str,
    oracle_id: str = "sub:oracle-1",
    c_oracle_discounted: float = 0.25,
    c_herd: float = 0.4,
    n_oracle_prior: int = 0,
) -> None:
    """Append an attestation with storage-valid defaults for testing.

    The parent request row must exist — see :func:`seed_request`. Defaults
    are valid per record validation, not mathematically consistent.
    Repository tests verify persistence, not algebra. ``n_oracle_prior``
    defaults to zero — the "fresh hypothesis" boundary — so trust-scan
    fixtures that care about the value must pass it explicitly.

    Hardcoded inside the helper: ``correlation_id``, ``timestamp = 1000``,
    ``t_oracle = 0.5``, ``c_oracle_raw = 0.5``. ``timestamp`` and
    ``c_oracle_raw`` are commonly varied — trust-scan tests are windowed
    over time and aligned against raw confidence — and those callers
    construct an ``AttestationRecord`` explicitly and call
    ``repo.append(record)`` directly. See ``test_oracle_trust.py`` for the
    pattern. The helper stays minimal so persistence tests (the majority,
    which only care about *that* a row landed) read as one line; trust
    tests pay verbosity in exchange for spelling out every field they
    control. The split keeps the helper under PLR0913 and out of the way
    of the math that the trust tests are actually about.
    """
    # Qualified access through ``records.generate_id`` so the monkeypatch in
    # ``test_two_backend_parity._force_fixed_id`` (which patches
    # ``lore.repositories.records.generate_id``) reaches this call site.
    await repo.append(
        AttestationRecord(
            id=records.generate_id(),
            hypothesis_id=hypothesis_id,
            oracle_id=oracle_id,
            correlation_id="00000000-0000-0000-0000-000000000099",
            timestamp=1000,
            t_oracle=0.5,
            c_oracle_raw=0.5,
            c_oracle_discounted=c_oracle_discounted,
            c_herd=c_herd,
            n_oracle_prior=n_oracle_prior,
        )
    )
