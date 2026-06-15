"""Tests for connect() and the pool's scope-bound context managers.

Behavioral coverage of ``session()`` / ``transaction()`` lifecycle
(round-trip, release, commit, rollback, error wrapping) is parametrized
over both backends via the conftest ``pool`` fixture.
"""

import asyncio
import re
import sqlite3
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

import psycopg
import pytest

from lore.domain import StorageError
from lore.repositories import RepositoryPool, check_health, connect, make_probe, run_migrations
from lore.repositories.postgres.hypotheses import PostgresHypothesisRepository
from lore.repositories.postgres.pool import PostgresPool
from lore.repositories.protocols import Repositories
from lore.repositories.records import RequestRecord
from lore.repositories.sqlite.pool import SqlitePool
from tests.repositories.conftest import SCHEMA_DIM, make_settings

_EMBEDDING = [1.0 / SCHEMA_DIM] * SCHEMA_DIM


async def _store_request(repos: Repositories, correlation_id: str) -> str:
    await repos.requests.store(
        RequestRecord(id=correlation_id, oracle_id="sub:factory-test", timestamp=0)
    )
    return correlation_id


class TestPoolScopes:
    """``session()`` and ``transaction()`` scope-bound CMs over both backends."""

    async def test_session_yields_functional_repos(self, pool: RepositoryPool) -> None:
        async with pool.session() as repos:
            cid = await _store_request(repos, "00000000-0000-0000-0000-00000000fac7")
            # Round-trip via PK uniqueness — re-storing the same id raises,
            # proving the first write persisted within the session.
            with pytest.raises(StorageError):
                await _store_request(repos, cid)

    async def test_session_releases_resources_on_clean_exit(self, pool: RepositoryPool) -> None:
        async with pool.session() as repos:
            await _store_request(repos, "00000000-0000-0000-0000-00000000fac8")
        # A fresh session sees the prior session's autocommitted row (PK
        # duplicate raises) and is itself a working repo (new id succeeds).
        async with pool.session() as repos2:
            with pytest.raises(StorageError):
                await _store_request(repos2, "00000000-0000-0000-0000-00000000fac8")
            await _store_request(repos2, "00000000-0000-0000-0000-00000000fac9")

    async def test_session_releases_resources_on_exception(self, pool: RepositoryPool) -> None:
        with pytest.raises(RuntimeError, match="session-boom"):
            async with pool.session() as repos:
                await _store_request(repos, "00000000-0000-0000-0000-00000000faca")
                msg = "session-boom"
                raise RuntimeError(msg)
        # Connection released even though the body raised: a fresh session
        # operates normally on a new id.
        async with pool.session() as repos2:
            await _store_request(repos2, "00000000-0000-0000-0000-00000000facb")

    async def test_transaction_commits_on_clean_exit(self, pool: RepositoryPool) -> None:
        async with pool.transaction() as repos:
            hyp = await repos.hypotheses.store(
                content="tx commit", created_at=1000, embedding=_EMBEDDING
            )
            committed_id = hyp.id
        async with pool.session() as repos2:
            found = await repos2.hypotheses.find_by_id(committed_id)
            assert found is not None
            assert found.content == "tx commit"

    async def test_transaction_rolls_back_on_exception(self, pool: RepositoryPool) -> None:
        rolled_back_id: str | None = None
        with pytest.raises(RuntimeError, match="tx-boom"):
            async with pool.transaction() as repos:
                hyp = await repos.hypotheses.store(
                    content="tx rollback", created_at=1000, embedding=_EMBEDDING
                )
                rolled_back_id = hyp.id
                msg = "tx-boom"
                raise RuntimeError(msg)
        assert rolled_back_id is not None
        async with pool.session() as repos2:
            assert await repos2.hypotheses.find_by_id(rolled_back_id) is None

    async def test_transaction_releases_resources_on_clean_exit(self, pool: RepositoryPool) -> None:
        async with pool.transaction() as repos:
            await repos.hypotheses.store(
                content="tx reuse 1", created_at=1000, embedding=_EMBEDDING
            )
        async with pool.transaction() as repos2:
            hyp = await repos2.hypotheses.store(
                content="tx reuse 2", created_at=1000, embedding=_EMBEDDING
            )
            assert hyp.content == "tx reuse 2"

    async def test_transaction_releases_resources_on_exception(self, pool: RepositoryPool) -> None:
        with pytest.raises(RuntimeError, match="tx-release-boom"):
            async with pool.transaction() as repos:
                await repos.hypotheses.store(
                    content="tx release boom", created_at=1000, embedding=_EMBEDDING
                )
                msg = "tx-release-boom"
                raise RuntimeError(msg)
        async with pool.transaction() as repos2:
            hyp = await repos2.hypotheses.store(
                content="tx post-release-boom", created_at=1000, embedding=_EMBEDDING
            )
            assert hyp.content == "tx post-release-boom"


class TestSqliteTransactionErrors:
    """SQLite transaction error paths — BEGIN, COMMIT, and ROLLBACK failures."""

    async def test_begin_failure_wraps_as_storage_error(self, sqlite_dsn_session: str) -> None:
        p = await connect(make_settings(dsn=sqlite_dsn_session))
        sqlite_pool = cast("SqlitePool", p)
        # Sabotage the raw connection so BEGIN fails.
        await sqlite_pool._conn.close()  # pyright: ignore[reportPrivateUsage]
        with pytest.raises(StorageError):
            async with p.transaction():
                pass  # pragma: no cover
        await p.close()

    async def test_commit_failure_wraps_as_storage_error(self, sqlite_dsn_session: str) -> None:
        p = await connect(make_settings(dsn=sqlite_dsn_session))
        sqlite_pool = cast("SqlitePool", p)
        with (
            patch.object(
                sqlite_pool._conn,  # pyright: ignore[reportPrivateUsage]
                "commit",
                new=AsyncMock(side_effect=sqlite3.OperationalError("disk I/O error")),
            ),
            pytest.raises(StorageError, match="disk I/O error"),
        ):
            async with p.transaction() as repos:
                await repos.hypotheses.store(
                    content="commit fail", created_at=1000, embedding=_EMBEDDING
                )
        await p.close()

    async def test_rollback_failure_preserves_original_exception(
        self, sqlite_dsn_session: str
    ) -> None:
        """When rollback fails, the original body exception still propagates."""
        p = await connect(make_settings(dsn=sqlite_dsn_session))
        sqlite_pool = cast("SqlitePool", p)
        with (
            patch.object(
                sqlite_pool._conn,  # pyright: ignore[reportPrivateUsage]
                "rollback",
                new=AsyncMock(side_effect=sqlite3.OperationalError("disk gone")),
            ),
            pytest.raises(RuntimeError, match="original"),
        ):
            async with p.transaction():
                msg = "original"
                raise RuntimeError(msg)
        await p.close()


class TestSqliteSessionValidation:
    """``SqlitePool.session()`` validates the connection on entry.

    Symmetric to the Postgres pool's ``check_connection`` callback on borrow:
    a broken connection raises ``StorageError`` before any caller code runs,
    so the readiness probe's semantics don't diverge across backends.
    """

    async def test_session_raises_storage_error_when_validation_fails(
        self, sqlite_dsn_session: str
    ) -> None:
        """A closed underlying connection trips the ``SELECT 1`` guard."""
        p = await connect(make_settings(dsn=sqlite_dsn_session))
        sqlite_pool = cast("SqlitePool", p)
        # Sabotage the raw connection so the validating SELECT 1 fails.
        await sqlite_pool._conn.close()  # pyright: ignore[reportPrivateUsage]
        with pytest.raises(StorageError):
            async with p.session():
                pass  # pragma: no cover — guard raises before body runs
        await p.close()


class TestPostgresTransactionErrors:
    """PostgreSQL transaction error wrapping."""

    async def test_psycopg_error_on_acquire_wraps_as_storage_error(self, pg_dsn: str) -> None:
        p = await connect(make_settings(dsn=pg_dsn))
        pg_pool = cast("PostgresPool", p)
        # Sabotage every conn the inner pool hands out so the transaction CM fails.
        with (
            patch.object(
                pg_pool._pool,  # pyright: ignore[reportPrivateUsage]
                "getconn",
                side_effect=psycopg.OperationalError("connection broken"),
            ),
            pytest.raises(StorageError, match="connection broken"),
        ):
            async with p.transaction():
                pass  # pragma: no cover
        await p.close()

    async def test_rollback_failure_preserves_original_exception(self, pg_dsn: str) -> None:
        """When rollback fails, the original body exception still propagates.

        psycopg's native transaction CM handles this: rollback errors are
        suppressed, the original exception always re-raises.
        """
        p = await connect(make_settings(dsn=pg_dsn))
        with pytest.raises(RuntimeError, match="original"):
            async with p.transaction() as repos:
                # Close the underlying conn so rollback will fail. The
                # repository carries the active conn; closing it makes the
                # implicit ROLLBACK psycopg issues on exception fail.
                pg_repo = cast("PostgresHypothesisRepository", repos.hypotheses)
                await pg_repo._conn.close()  # pyright: ignore[reportPrivateUsage]
                msg = "original"
                raise RuntimeError(msg)
        await p.close()


class TestConnectFailure:
    """connect() failures are wrapped in StorageError."""

    async def test_postgres_pool_failure_wraps_as_storage_error(self, pg_dsn: str) -> None:
        with (
            patch(
                "lore.repositories.postgres.pool.create_pool",
                side_effect=OSError("connection refused"),
            ),
            pytest.raises(StorageError, match="connection refused"),
        ):
            await connect(make_settings(dsn=pg_dsn))

    async def test_sqlite_connect_failure_wraps_as_storage_error(self) -> None:
        with (
            patch(
                "lore.repositories.sqlite.pool.sqlite_connect",
                side_effect=sqlite3.OperationalError("unable to open database file"),
            ),
            pytest.raises(StorageError, match="unable to open database file"),
        ):
            await connect(make_settings(dsn="sqlite:///any-path"))

    async def test_unsupported_dsn_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="Unsupported DSN"):
            await connect(make_settings(dsn="mysql://localhost/db"))


class TestMemoryDsnRejected:
    """SQLite :memory: is rejected at bootstrap — each connection gets a private DB."""

    def test_run_migrations_rejects_memory_dsn(self) -> None:
        with pytest.raises(ValueError, match=re.escape("Please use a (tmp) file path")):
            run_migrations(
                settings=make_settings(dsn="sqlite:///:memory:"), embedding_dim=SCHEMA_DIM
            )


class TestUnsupportedDsnRouting:
    """run_migrations / check_health refuse unknown DSN schemes."""

    def test_run_migrations_rejects_unsupported_dsn(self) -> None:
        with pytest.raises(ValueError, match="Unsupported DSN"):
            run_migrations(
                settings=make_settings(dsn="mysql://localhost/lore"),
                embedding_dim=SCHEMA_DIM,
            )

    def test_check_health_rejects_unsupported_dsn(self) -> None:
        with pytest.raises(ValueError, match="Unsupported DSN"):
            check_health(
                settings=make_settings(dsn="mysql://localhost/lore"),
                embedding_dim=SCHEMA_DIM,
            )


class TestMakeProbe:
    """``make_probe(pool)`` returns a closure that acquires-and-releases on the live pool.

    The probe is the readiness signal ``/ready`` exposes to load balancers.
    Its contract: succeed quietly when the pool gives us a working connection,
    raise ``StorageError`` when it can't (timeout, pool failure, anything).
    """

    async def test_probe_succeeds_against_live_pool(self, pool: RepositoryPool) -> None:
        """A healthy pool yields a connection within the timeout."""
        probe = make_probe(pool)
        await probe()  # no raise

    async def test_probe_raises_storage_error_on_pool_session_failure(self) -> None:
        """When ``pool.session()`` raises ``StorageError``, the probe propagates it.

        ``pool.session()`` already wraps backend errors to ``StorageError``
        (see ``_acquire`` in PostgresPool); ``make_probe`` doesn't need to
        re-wrap. Pin that ``make_probe`` does not swallow or remap the error.
        """

        @asynccontextmanager
        async def _failing_session() -> AsyncGenerator[None]:
            raise StorageError("pool exhausted")
            yield  # pragma: no cover — unreachable; keeps Pyright happy on AsyncGenerator type

        fake_pool = MagicMock()
        fake_pool.session = _failing_session

        probe = make_probe(cast(RepositoryPool, fake_pool))
        with pytest.raises(StorageError, match="pool exhausted"):
            await probe()

    async def test_probe_raises_storage_error_on_timeout(self) -> None:
        """A pool that never yields within the timeout produces a scrubbed StorageError.

        The probe's job is to bound how long readiness checking can hang.
        ``asyncio.timeout`` translates a stuck ``getconn`` into ``TimeoutError``;
        ``make_probe`` rewraps as ``StorageError`` so ``/ready`` sees one
        consistent error class.
        """

        @asynccontextmanager
        async def _slow_session() -> AsyncGenerator[None]:
            await asyncio.sleep(10)
            yield  # pragma: no cover

        fake_pool = MagicMock()
        fake_pool.session = _slow_session

        # Use a small timeout so the test is fast.
        probe = make_probe(cast(RepositoryPool, fake_pool), timeout=0.05)
        with pytest.raises(StorageError, match="timed out"):
            await probe()


class TestPostgresPoolCheckCallback:
    """The Postgres pool validates connections on borrow via ``check_connection``.

    The check callback is what lets the live-pool readiness probe trust
    ``pool.session()`` without an additional roundtrip — every borrow,
    probe or otherwise, returns a connection the pool has just verified.
    """

    async def test_check_connection_is_wired_on_pool_creation(self) -> None:
        """``create_pool`` sets ``check=AsyncConnectionPool.check_connection``.

        Pin the configuration by inspecting the pool's ``_check`` attribute
        after construction — patching only ``open`` so ``create_pool`` runs
        without needing a reachable database. The behavioral cost (one
        ``SELECT 1`` per borrow) is documented in the architecture; the
        wire-up is what we want to catch in CI.
        """
        from psycopg_pool import AsyncConnectionPool

        from lore.repositories import PostgresConfig
        from lore.repositories.postgres.connection import create_pool

        config = PostgresConfig(min_size=1, max_size=2, timeout=1.0, max_waiting=0)
        with patch.object(AsyncConnectionPool, "open", new=AsyncMock()):
            pool = await create_pool(dsn="postgresql://x:y@host/db", config=config)
        try:
            # psycopg_pool stores the borrow-time check callback on the
            # private ``_check`` attribute (line 84 of pool_async.py).
            assert pool._check is AsyncConnectionPool.check_connection  # pyright: ignore[reportPrivateUsage]
        finally:
            with patch.object(AsyncConnectionPool, "close", new=AsyncMock()):
                await pool.close()
