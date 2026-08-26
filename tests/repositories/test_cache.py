"""Tests for CacheRepository Protocol behavior.

Upsert semantics are the point: a second ``put_entry`` on the same
``(collection, key)`` overwrites in place, unlike ``RequestRepository.store``
(insert-only, duplicate raises). See ``test_requests.py::test_store_duplicate_id_raises``
for the contrast.
"""

from collections.abc import Awaitable, Callable
from typing import Any, NoReturn, cast

import aiosqlite
import psycopg
import psycopg.errors
import pytest
from pydantic import ValidationError

from lore.domain import StorageError
from lore.repositories import connect
from lore.repositories._postgres.cache import PostgresCacheRepository
from lore.repositories._protocols import CacheRepository, RepositoryPool
from lore.repositories._records import CacheEntry
from tests.repositories._orchestrator_fixtures import make_settings
from tests.repositories.conftest import BackendFixture


class TestPutGet:
    async def test_put_then_get_returns_stored_value(self, cache_repo: CacheRepository) -> None:
        await cache_repo.put_entry(
            collection="mcp-clients",
            key="client-abc",
            value='{"client_id": "abc"}',
            created_at=1000,
            expires_at=2000,
        )

        entry = await cache_repo.get_entry(collection="mcp-clients", key="client-abc")

        assert entry == CacheEntry(
            collection="mcp-clients",
            key="client-abc",
            value='{"client_id": "abc"}',
            created_at=1000,
            expires_at=2000,
        )

    async def test_get_missing_key_returns_none(self, cache_repo: CacheRepository) -> None:
        assert await cache_repo.get_entry(collection="mcp-clients", key="absent") is None

    async def test_put_upserts_existing_key(self, cache_repo: CacheRepository) -> None:
        await cache_repo.put_entry(
            collection="mcp-clients",
            key="client-abc",
            value='{"token": "old"}',
            created_at=1000,
            expires_at=2000,
        )
        await cache_repo.put_entry(
            collection="mcp-clients",
            key="client-abc",
            value='{"token": "refreshed"}',
            created_at=3000,
            expires_at=None,
        )

        entry = await cache_repo.get_entry(collection="mcp-clients", key="client-abc")

        assert entry == CacheEntry(
            collection="mcp-clients",
            key="client-abc",
            value='{"token": "refreshed"}',
            created_at=3000,
            expires_at=None,
        )


class TestCollectionIsolation:
    """fastmcp runs several logical stores over this one table (client
    registrations, upstream tokens, auth codes, ...), isolated solely by
    the ``collection`` column. The primary key protects ``put_entry``;
    these pin ``get_entry`` and ``delete_entry`` to the same lane.
    """

    async def test_same_key_in_two_collections_stays_isolated(
        self, cache_repo: CacheRepository
    ) -> None:
        await cache_repo.put_entry(
            collection="mcp-clients",
            key="shared-key",
            value='{"tenant": "clients"}',
            created_at=1000,
            expires_at=None,
        )
        await cache_repo.put_entry(
            collection="upstream-tokens",
            key="shared-key",
            value='{"tenant": "tokens"}',
            created_at=2000,
            expires_at=None,
        )

        clients = await cache_repo.get_entry(collection="mcp-clients", key="shared-key")
        tokens = await cache_repo.get_entry(collection="upstream-tokens", key="shared-key")

        assert clients is not None
        assert clients.value == '{"tenant": "clients"}'
        assert tokens is not None
        assert tokens.value == '{"tenant": "tokens"}'

    async def test_delete_in_one_collection_leaves_the_other_readable(
        self, cache_repo: CacheRepository
    ) -> None:
        await cache_repo.put_entry(
            collection="mcp-clients",
            key="shared-key",
            value='{"tenant": "clients"}',
            created_at=1000,
            expires_at=None,
        )
        await cache_repo.put_entry(
            collection="upstream-tokens",
            key="shared-key",
            value='{"tenant": "tokens"}',
            created_at=2000,
            expires_at=None,
        )

        deleted = await cache_repo.delete_entry(collection="mcp-clients", key="shared-key")

        assert deleted is True
        assert await cache_repo.get_entry(collection="mcp-clients", key="shared-key") is None
        survivor = await cache_repo.get_entry(collection="upstream-tokens", key="shared-key")
        assert survivor is not None
        assert survivor.value == '{"tenant": "tokens"}'


class TestPoolBundle:
    async def test_cache_queryable_through_pool_session(self, pool: RepositoryPool) -> None:
        """The pool's own bundle carries _cache: the production access path."""
        async with pool.session() as repos:
            await repos.cache.put_entry(
                collection="mcp-clients",
                key="client-abc",
                value='{"client_id": "abc"}',
                created_at=1000,
                expires_at=None,
            )
            entry = await repos.cache.get_entry(collection="mcp-clients", key="client-abc")

        assert entry is not None
        assert entry.value == '{"client_id": "abc"}'


class TestDelete:
    async def test_delete_existing_key_returns_true_and_removes_row(
        self, cache_repo: CacheRepository
    ) -> None:
        await cache_repo.put_entry(
            collection="mcp-clients",
            key="client-abc",
            value='{"client_id": "abc"}',
            created_at=1000,
            expires_at=None,
        )

        deleted = await cache_repo.delete_entry(collection="mcp-clients", key="client-abc")
        assert deleted is True
        assert await cache_repo.get_entry(collection="mcp-clients", key="client-abc") is None

    async def test_delete_missing_key_returns_false(self, cache_repo: CacheRepository) -> None:
        assert await cache_repo.delete_entry(collection="mcp-clients", key="absent") is False


class TestBoundaryValidation:
    """The table has no CHECK constraints; the CacheEntry validators are
    the only guard, applied on both sides of the boundary. A bad write is a
    caller bug (ValidationError); a bad stored row is corrupt storage
    (StorageError).
    """

    async def test_put_empty_collection_raises_validation_error(
        self, cache_repo: CacheRepository
    ) -> None:
        with pytest.raises(ValidationError, match="collection"):
            await cache_repo.put_entry(
                collection="",
                key="k",
                value="{}",
                created_at=1000,
                expires_at=None,
            )

    async def test_put_negative_created_at_raises_validation_error(
        self, cache_repo: CacheRepository
    ) -> None:
        with pytest.raises(ValidationError, match="created_at"):
            await cache_repo.put_entry(
                collection="mcp-clients",
                key="k",
                value="{}",
                created_at=-1,
                expires_at=None,
            )

    async def test_get_corrupt_row_raises_storage_error(
        self, backend: BackendFixture, cache_repo: CacheRepository
    ) -> None:
        """A row that bypassed put_entry and fails validation is corrupt storage."""
        await _seed_corrupt_row(backend.raw_conn)

        with pytest.raises(StorageError, match="corrupt _cache row"):
            await cache_repo.get_entry(collection="mcp-clients", key="corrupt")


async def _seed_corrupt_row(
    raw_conn: aiosqlite.Connection | psycopg.AsyncConnection[Any],
) -> None:
    """Insert a row put_entry would reject: negative created_at."""
    params = ("mcp-clients", "corrupt", "{}", -1)
    if isinstance(raw_conn, aiosqlite.Connection):
        await raw_conn.execute(
            "INSERT INTO _cache (collection, key, value, created_at) VALUES (?, ?, ?, ?)",
            params,
        )
        return
    await raw_conn.execute(
        "INSERT INTO _cache (collection, key, value, created_at) VALUES (%s, %s, %s, %s)",
        params,
    )


class TestSweep:
    async def test_delete_expired_removes_only_expired_rows(
        self, cache_repo: CacheRepository
    ) -> None:
        """Expired rows go; live rows and NULL-expiry rows (client
        registrations, which persist by design) stay.
        """
        await cache_repo.put_entry(
            collection="mcp-oauth-transactions",
            key="stale",
            value="{}",
            created_at=0,
            expires_at=100,
        )
        await cache_repo.put_entry(
            collection="mcp-oauth-transactions",
            key="live",
            value="{}",
            created_at=0,
            expires_at=10_000,
        )
        await cache_repo.put_entry(
            collection="mcp-oauth-proxy-clients",
            key="registration",
            value="{}",
            created_at=0,
            expires_at=None,
        )

        deleted = await cache_repo.delete_expired(now=5_000)

        assert deleted == 1
        stale = await cache_repo.get_entry(collection="mcp-oauth-transactions", key="stale")
        assert stale is None
        live = await cache_repo.get_entry(collection="mcp-oauth-transactions", key="live")
        assert live is not None
        eternal = await cache_repo.get_entry(
            collection="mcp-oauth-proxy-clients", key="registration"
        )
        assert eternal is not None

    async def test_delete_expired_on_clean_table_returns_zero(
        self, cache_repo: CacheRepository
    ) -> None:
        assert await cache_repo.delete_expired(now=5_000) == 0

    async def test_delete_expired_on_closed_connection_raises(
        self,
        sabotage_connection: Callable[[], Awaitable[None]],
        cache_repo: CacheRepository,
    ) -> None:
        await sabotage_connection()
        with pytest.raises(StorageError):
            await cache_repo.delete_expired(now=5_000)


async def test_delete_expired_skips_while_another_session_holds_the_sweep_lock(
    pg_dsn_session: str,
) -> None:
    """PostgreSQL only: with several replicas on the same schedule, one
    sweeps and the rest no-op. The advisory lock is transaction-scoped, so
    the rival closing its transaction reopens the sweep.
    """
    settings = make_settings(dsn=pg_dsn_session)
    pool = await connect(settings)
    try:
        async with pool.session() as repos:
            await repos.cache.put_entry(
                collection="mcp-oauth-transactions",
                key="stale",
                value="{}",
                created_at=0,
                expires_at=100,
            )

        rival = psycopg.connect(pg_dsn_session)
        try:
            rival.execute("SELECT pg_try_advisory_xact_lock(hashtext('lore_cache_sweep'))")
            async with pool.transaction() as repos:
                assert await repos.cache.delete_expired(now=5_000) == 0
        finally:
            rival.close()

        async with pool.transaction() as repos:
            assert await repos.cache.delete_expired(now=5_000) == 1
    finally:
        await pool.close()


class TestStorageError:
    async def test_get_on_closed_connection_raises(
        self,
        sabotage_connection: Callable[[], Awaitable[None]],
        cache_repo: CacheRepository,
    ) -> None:
        await sabotage_connection()
        with pytest.raises(StorageError):
            await cache_repo.get_entry(collection="mcp-clients", key="client-abc")

    async def test_put_on_closed_connection_raises(
        self,
        sabotage_connection: Callable[[], Awaitable[None]],
        cache_repo: CacheRepository,
    ) -> None:
        await sabotage_connection()
        with pytest.raises(StorageError):
            await cache_repo.put_entry(
                collection="mcp-clients",
                key="client-abc",
                value="{}",
                created_at=1000,
                expires_at=None,
            )

    async def test_delete_on_closed_connection_raises(
        self,
        sabotage_connection: Callable[[], Awaitable[None]],
        cache_repo: CacheRepository,
    ) -> None:
        await sabotage_connection()
        with pytest.raises(StorageError):
            await cache_repo.delete_entry(collection="mcp-clients", key="client-abc")


class _SerializationRacingConn:
    """Stub connection: every statement loses a serialization race.

    A genuine single-statement 40001 (the sweep's DELETE landing mid-upsert
    on the same expired row) needs sub-statement timing no test can
    schedule deterministically, so the race is simulated at the driver
    boundary.
    """

    def cursor(self, **_kwargs: object) -> _SerializationRacingConn:
        return self

    async def execute(self, *_args: object, **_kwargs: object) -> NoReturn:
        raise psycopg.errors.SerializationFailure(
            "could not serialize access due to concurrent update"
        )


class TestSerializationFailureTranslation:
    """Session-scope statements degrade 40001 to StorageError.

    ``translate`` re-raises SerializationFailure so ``transaction()`` can
    convert it to RetryableTransactionError, but the session-scope methods
    run autocommit with no converting scope above (and the connection is
    pinned SERIALIZABLE even there), so the raw psycopg class must not
    cross the repository boundary. ``delete_expired`` keeps the
    pass-through: it runs under ``transaction()`` by contract.
    """

    @pytest.fixture
    def racing_repo(self) -> PostgresCacheRepository:
        return PostgresCacheRepository(
            cast("psycopg.AsyncConnection[Any]", _SerializationRacingConn())
        )

    async def test_get_entry_serialization_failure_raises_storage_error(
        self, racing_repo: PostgresCacheRepository
    ) -> None:
        with pytest.raises(StorageError, match="serialize"):
            await racing_repo.get_entry(collection="mcp-clients", key="client-abc")

    async def test_put_entry_serialization_failure_raises_storage_error(
        self, racing_repo: PostgresCacheRepository
    ) -> None:
        with pytest.raises(StorageError, match="serialize"):
            await racing_repo.put_entry(
                collection="mcp-clients",
                key="client-abc",
                value="{}",
                created_at=1000,
                expires_at=None,
            )

    async def test_delete_entry_serialization_failure_raises_storage_error(
        self, racing_repo: PostgresCacheRepository
    ) -> None:
        with pytest.raises(StorageError, match="serialize"):
            await racing_repo.delete_entry(collection="mcp-clients", key="client-abc")
