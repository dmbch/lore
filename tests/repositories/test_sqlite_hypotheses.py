"""SQLite-specific hypothesis storage tests.

Atomicity is provided by the orchestrator's outer ``pool.transaction()``,
not by an inner SAVEPOINT in ``store()``. This test locks in the contract:
a forced Python-level failure between the three INSERTs leaves no
hypothesis row visible after the parent transaction rolls back.
"""

from collections.abc import AsyncGenerator

import pytest

from lore.domain import StorageError
from lore.repositories import RepositoryPool, connect
from lore.repositories.sqlite.pool import SqlitePool
from tests.repositories.conftest import SCHEMA_DIM, make_settings


@pytest.fixture
async def sqlite_pool(sqlite_dsn_session: str) -> AsyncGenerator[SqlitePool]:
    """Open a SQLite-only pool. Truncate before yielding."""
    pool: RepositoryPool = await connect(make_settings(dsn=sqlite_dsn_session))
    assert isinstance(pool, SqlitePool)
    # Truncate domain + virtual tables. The session-scoped DSN is shared.
    raw = pool._conn  # pyright: ignore[reportPrivateUsage]
    for table in ("attestations", "requests", "fts_hypotheses", "vec_hypotheses", "hypotheses"):
        await raw.execute(f"DELETE FROM {table}")  # table names are compile-time constants
    yield pool
    await pool.close()


class TestStoreFailureRollsBackViaOuterTransaction:
    """Atomicity via parent transaction is the contract: SAVEPOINT was cosmetic."""

    async def test_store_failure_in_vec_insert_rolls_back_hypothesis_row_via_outer_transaction(
        self, sqlite_pool: SqlitePool, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Force the second INSERT to fail; assert no row visible after rollback.

        ``sqlite_vec.serialize_float32`` is called inline as the embedding
        argument to the vec_hypotheses INSERT, after the hypotheses INSERT
        has already run. Patching the module-level reference to raise simulates
        a mid-store Python-level failure. The store wraps the ``ValueError``
        as ``StorageError``; ``pool.transaction()`` catches it and rolls back
        the whole BEGIN.
        """

        def _boom(_: object) -> bytes:
            msg = "injected serialization failure"
            raise ValueError(msg)

        monkeypatch.setattr(
            "lore.repositories.sqlite.hypotheses.sqlite_vec.serialize_float32", _boom
        )

        with pytest.raises(StorageError, match="injected serialization failure"):
            async with sqlite_pool.transaction() as repos:
                await repos.hypotheses.store(
                    content="doomed claim",
                    embedding=[1.0 / SCHEMA_DIM] * SCHEMA_DIM,
                    created_at=1000,
                )

        # Lift the patch: query the raw table to prove no row survived.
        monkeypatch.undo()
        raw = sqlite_pool._conn  # pyright: ignore[reportPrivateUsage]
        cursor = await raw.execute(
            "SELECT COUNT(*) FROM hypotheses WHERE content = ?", ("doomed claim",)
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row[0] == 0
