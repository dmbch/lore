"""Tests for ``LoreCacheStore``: fastmcp state storage over the repository layer.

The store is constructed before the repository pool exists (server wiring
runs at construction time, the pool connects inside the lifespan), so it
reads the pool through a mutable ``PoolCell`` that the lifespan fills.
SQLite-only on purpose: the repository layer underneath is already
two-backend tested in ``tests/repositories/test_cache.py``. The store is
deliberately bare; the Fernet wrapping of the OAuth lane is adapter-owned
and tested in ``tests/adapter/test_oauth_storage.py``.
"""

import sqlite3
import time
from collections.abc import AsyncGenerator
from contextlib import AbstractAsyncContextManager, closing
from pathlib import Path

import pytest
from key_value.aio._utils.managed_entry import ManagedEntry
from key_value.aio.protocols.key_value import AsyncKeyValueProtocol

from lore.domain import StorageError
from lore.repositories import (
    LoreCacheStore,
    PoolCell,
    Repositories,
    RepositoryPool,
    connect,
    run_migrations,
    sweep_expired_cache,
)
from tests.repositories._orchestrator_fixtures import make_settings

# Schema dimension for the throwaway test database. The cache store never
# touches vector tables; any valid dimension works.
_SCHEMA_DIM = 8


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "lore.db"


@pytest.fixture
async def sqlite_pool(db_path: Path) -> AsyncGenerator[RepositoryPool]:
    settings = make_settings(dsn=f"sqlite:///{db_path}")
    run_migrations(settings=settings, embedding_dim=_SCHEMA_DIM)
    pool = await connect(settings)
    yield pool
    await pool.close()


@pytest.fixture
def store(sqlite_pool: RepositoryPool) -> LoreCacheStore:
    return LoreCacheStore(pool_cell=PoolCell(pool=sqlite_pool))


class TestPoolCell:
    def test_pool_cell_starts_empty(self) -> None:
        assert PoolCell().pool is None

    async def test_store_before_pool_connected_raises_runtime_error(self) -> None:
        detached = LoreCacheStore(pool_cell=PoolCell())

        with pytest.raises(RuntimeError, match="before the repository pool"):
            await detached.get("client-abc", collection="mcp-clients")


class TestRoundTrip:
    async def test_store_put_then_get_round_trips_value(self, store: LoreCacheStore) -> None:
        value = {"client_id": "abc", "scopes": ["openid"], "active": True}

        await store.put("client-abc", value, collection="mcp-clients")

        assert await store.get("client-abc", collection="mcp-clients") == value

    async def test_store_get_missing_key_returns_none(self, store: LoreCacheStore) -> None:
        assert await store.get("absent", collection="mcp-clients") is None

    async def test_store_delete_removes_key(self, store: LoreCacheStore) -> None:
        await store.put("client-abc", {"client_id": "abc"}, collection="mcp-clients")

        assert await store.delete("client-abc", collection="mcp-clients") is True
        assert await store.get("client-abc", collection="mcp-clients") is None


class TestExpiry:
    async def test_store_expired_entry_reads_back_as_none(
        self, store: LoreCacheStore, sqlite_pool: RepositoryPool
    ) -> None:
        """A row whose ``expires_at`` has passed is a miss, not a value.

        Seeded through the repository because the public ``put`` rejects
        non-positive TTLs. Reading back through the public ``get`` drives
        the epoch-int to aware-datetime conversion: a naive datetime here
        would raise on the aware comparison inside ``is_expired``.
        """
        async with sqlite_pool.session() as repos:
            await repos.cache.put_entry(
                collection="mcp-clients",
                key="stale",
                value='{"token": "stale"}',
                created_at=0,
                expires_at=1,
            )

        assert await store.get("stale", collection="mcp-clients") is None

    async def test_store_put_with_ttl_persists_epoch_second_expiry(
        self, store: LoreCacheStore, sqlite_pool: RepositoryPool
    ) -> None:
        """The stored row carries epoch seconds, not milliseconds or naive-local."""
        before = int(time.time())
        await store.put("client-abc", {"token": "fresh"}, collection="mcp-clients", ttl=3600)
        after = int(time.time())

        async with sqlite_pool.session() as repos:
            entry = await repos.cache.get_entry(collection="mcp-clients", key="client-abc")

        assert entry is not None
        assert before <= entry.created_at <= after
        assert entry.expires_at is not None
        assert before + 3600 <= entry.expires_at <= after + 3600

    async def test_put_managed_entry_without_created_at_stamps_now(
        self, store: LoreCacheStore, sqlite_pool: RepositoryPool
    ) -> None:
        """An entry arriving without ``created_at`` is stamped at write time.

        Unreachable through the public API (``put`` always stamps), but the
        base-class contract allows it and the column is NOT NULL.
        """
        before = int(time.time())
        await store._put_managed_entry(  # pyright: ignore[reportPrivateUsage]
            collection="mcp-clients",
            key="unstamped",
            managed_entry=ManagedEntry(value={"token": "t"}),
        )
        after = int(time.time())

        async with sqlite_pool.session() as repos:
            entry = await repos.cache.get_entry(collection="mcp-clients", key="unstamped")

        assert entry is not None
        assert before <= entry.created_at <= after
        assert entry.expires_at is None


class TestCorruptValue:
    async def test_get_undecodable_value_raises_storage_error(
        self, store: LoreCacheStore, sqlite_pool: RepositoryPool
    ) -> None:
        """A ``value`` column that is not a JSON object is corrupt storage.

        The repositories translate validator-failing rows to StorageError;
        an unparseable value column must take the same lane one layer up,
        not escape as the key-value library's exception class.
        """
        async with sqlite_pool.session() as repos:
            await repos.cache.put_entry(
                collection="mcp-clients",
                key="garbled",
                value="not json",
                created_at=0,
                expires_at=None,
            )

        with pytest.raises(StorageError, match="corrupt _cache row"):
            await store.get("garbled", collection="mcp-clients")


class TestProtocolConformance:
    def test_store_conforms_to_async_key_value_protocol(self) -> None:
        # ``AsyncKeyValue`` (fastmcp's client_storage type) only re-labels
        # AsyncKeyValueProtocol and is not itself @runtime_checkable, so the
        # isinstance check targets the runtime-checkable base.
        candidate: object = LoreCacheStore(pool_cell=PoolCell())

        assert isinstance(candidate, AsyncKeyValueProtocol)


class TestBulkDefaults:
    async def test_store_get_many_put_many_work_via_base_store_defaults(
        self, store: LoreCacheStore
    ) -> None:
        """The inherited bulk methods drive the three primitives correctly."""
        await store.put_many(
            ["k1", "k2"],
            [{"a": 1}, {"b": 2}],
            collection="mcp-clients",
        )

        assert await store.get_many(["k1", "k2", "absent"], collection="mcp-clients") == [
            {"a": 1},
            {"b": 2},
            None,
        ]


class TestSweepMechanism:
    async def test_sweep_survives_storage_errors(self, db_path: Path) -> None:
        """A failed sweep logs and returns: the caller's lifespan must
        never die over it.
        """
        settings = make_settings(dsn=f"sqlite:///{db_path}")
        run_migrations(settings=settings, embedding_dim=_SCHEMA_DIM)
        pool = await connect(settings)
        await pool.close()

        await sweep_expired_cache(pool)

    async def test_sweep_survives_unexpected_errors(self) -> None:
        """The broad catch is the contract, not just the StorageError arm.

        A bug raising past ``sweep_expired_cache`` would kill the caller's
        sweep loop unobserved, then resurface from the lifespan's ``await
        sweep`` inside ``finally``, skipping cell clearing and pool close.
        """

        class ExplodingPool:
            def session(self) -> AbstractAsyncContextManager[Repositories]:
                raise NotImplementedError

            def transaction(self) -> AbstractAsyncContextManager[Repositories]:
                msg = "sweep bug"
                raise RuntimeError(msg)

            async def close(self) -> None:
                raise NotImplementedError

        await sweep_expired_cache(ExplodingPool())


class TestSessionStatePosture:
    async def test_session_value_is_plaintext_in_db(
        self, store: LoreCacheStore, db_path: Path
    ) -> None:
        """Pins the posture: the bare store (fastmcp's session_state_store)
        writes plaintext. Session state is operational app state in the
        same trust domain as the domain tables, not credentials; encryption
        is reserved for the adapter-wrapped OAuth lane.
        """
        await store.put(
            "session-1:filters", {"view": "plaintext-canary-visible"}, collection="fastmcp_state"
        )

        with closing(sqlite3.connect(db_path)) as conn:
            row = conn.execute(
                "SELECT value FROM _cache WHERE collection = ? AND key = ?",
                ("fastmcp_state", "session-1:filters"),
            ).fetchone()

        assert row is not None
        assert "plaintext-canary-visible" in row[0]
