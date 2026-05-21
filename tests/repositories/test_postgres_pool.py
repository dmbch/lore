"""Tests for PostgresPool error translation."""

import asyncio
from typing import Any, cast
from unittest.mock import patch

import psycopg
import pytest
from psycopg_pool import AsyncConnectionPool

from lore.config import PostgresConfig
from lore.domain import RetryableTransactionError, StorageError
from lore.repositories import AttestationRecord, RequestRecord
from lore.repositories.postgres.connection import create_pool
from lore.repositories.postgres.hypotheses import PostgresHypothesisRepository
from lore.repositories.postgres.pool import PostgresPool
from tests.repositories.conftest import SCHEMA_DIM, TEST_POSTGRES_CONFIG


class TestPostgresPoolSession:
    """``session()`` translates psycopg pool errors to StorageError."""

    async def test_pool_exhaustion_on_session_raises_storage_error(self, pg_dsn: str) -> None:
        """Pool exhaustion (PoolTimeout) surfaces as StorageError, not psycopg.Error.

        The pool is constructed without the pgvector configure callback because
        this test does not exercise vector operations — it only forces the
        getconn timeout.
        """
        raw_pool: AsyncConnectionPool[psycopg.AsyncConnection[Any]] = AsyncConnectionPool(
            pg_dsn,
            min_size=1,
            max_size=1,
            timeout=0.1,
            open=False,
            kwargs={"autocommit": True},
        )
        await raw_pool.open(wait=True)
        pool = PostgresPool(pool=raw_pool, fulltext_config="english")
        try:
            async with pool.session() as _repos:
                with pytest.raises(StorageError):
                    async with pool.session() as _repos2:
                        pass  # pragma: no cover
        finally:
            await pool.close()

    async def test_oserror_on_session_raises_storage_error(self, pg_dsn: str) -> None:
        """OSError from getconn surfaces as StorageError.

        The pool may attempt to open a backend connection on demand; a network
        blip surfaces as OSError, which must translate to a domain-level
        StorageError just like psycopg errors do.
        """
        raw_pool: AsyncConnectionPool[psycopg.AsyncConnection[Any]] = AsyncConnectionPool(
            pg_dsn,
            min_size=1,
            max_size=1,
            timeout=0.1,
            open=False,
            kwargs={"autocommit": True},
        )
        await raw_pool.open(wait=True)
        pool = PostgresPool(pool=raw_pool, fulltext_config="english")
        try:
            with (
                patch.object(raw_pool, "getconn", side_effect=OSError("connection reset")),
                pytest.raises(StorageError, match="connection reset"),
            ):
                async with pool.session() as _repos:
                    pass  # pragma: no cover
        finally:
            await pool.close()


class TestPostgresPoolIsolation:
    """pool.transaction() runs at SERIALIZABLE — the write-path concurrency contract."""

    async def test_postgres_transaction_runs_at_serializable(self, pg_dsn: str) -> None:
        config = PostgresConfig(min_size=1, max_size=1, getconn_timeout=10.0, max_waiting=50)
        pool = await PostgresPool.create(dsn=pg_dsn, config=config)
        try:
            async with pool.transaction() as repos:
                pg_repo = cast("PostgresHypothesisRepository", repos.hypotheses)
                cursor = await pg_repo._conn.execute(  # pyright: ignore[reportPrivateUsage]
                    "SHOW transaction_isolation"
                )
                row = await cursor.fetchone()
            assert row is not None
            assert row[0] == "serializable"
        finally:
            await pool.close()


class TestPostgresPoolSnapshotIsolation:
    """SERIALIZABLE pins T1's snapshot at BEGIN — concurrent commits stay invisible.

    Two coroutines coordinate through ``asyncio.Event``: T1 opens a
    transaction, reads, hands the floor to T2, waits for T2's commit, reads
    again. Under READ COMMITTED, T1's second read would observe T2's row.
    Under REPEATABLE READ / SERIALIZABLE, T1's view is fixed at BEGIN. T1
    is read-only here, so serializability detection does not fire on commit.
    """

    async def test_postgres_transaction_sees_stable_snapshot_under_concurrent_write(
        self, pg_dsn: str
    ) -> None:
        pool = await PostgresPool.create(dsn=pg_dsn, config=TEST_POSTGRES_CONFIG)
        try:
            embedding = [1.0 / SCHEMA_DIM] * SCHEMA_DIM
            seed_correlation_id = "00000000-0000-0000-0000-0000000000d1"
            writer_correlation_id = "00000000-0000-0000-0000-0000000000d2"

            async with pool.session() as repos:
                hypothesis = await repos.hypotheses.store(
                    content="snapshot-isolation claim", embedding=embedding, created_at=0
                )
                await repos.requests.store(
                    RequestRecord(id=seed_correlation_id, oracle_id="sub:seed", timestamp=0)
                )
                await repos.attestations.append(
                    hypothesis_id=hypothesis.id,
                    oracle_id="sub:seed",
                    correlation_id=seed_correlation_id,
                    timestamp=1000,
                    t_oracle=0.5,
                    c_oracle_raw=0.5,
                    c_oracle_discounted=0.25,
                    c_herd=0.4,
                    n_oracle_prior=0,
                )

            t1_inside_txn = asyncio.Event()
            t2_committed = asyncio.Event()

            async def reader() -> tuple[list[AttestationRecord], list[AttestationRecord]]:
                async with pool.transaction() as repos:
                    first = await repos.attestations.find_by_hypothesis(hypothesis.id)
                    t1_inside_txn.set()
                    await t2_committed.wait()
                    second = await repos.attestations.find_by_hypothesis(hypothesis.id)
                return first, second

            async def writer() -> None:
                await t1_inside_txn.wait()
                async with pool.transaction() as repos:
                    await repos.requests.store(
                        RequestRecord(
                            id=writer_correlation_id, oracle_id="sub:writer", timestamp=2000
                        )
                    )
                    await repos.attestations.append(
                        hypothesis_id=hypothesis.id,
                        oracle_id="sub:writer",
                        correlation_id=writer_correlation_id,
                        timestamp=2000,
                        t_oracle=0.5,
                        c_oracle_raw=0.5,
                        c_oracle_discounted=0.25,
                        c_herd=0.4,
                        n_oracle_prior=0,
                    )
                t2_committed.set()

            (first, second), _ = await asyncio.gather(reader(), writer())

            assert len(first) == 1
            assert first == second

            # Confirm T2's write actually committed — proves the test isn't
            # passing because the writer failed silently.
            async with pool.session() as repos:
                after = await repos.attestations.find_by_hypothesis(hypothesis.id)
            assert len(after) == 2
        finally:
            await pool.close()


class TestPostgresPoolSerializationFailureTranslation:
    """SerializationFailure on commit translates to RetryableTransactionError.

    Two coroutines coordinate through ``asyncio.Event`` to construct a
    write-skew under PG SERIALIZABLE: both read the same attestation row,
    both write a new attestation on the same hypothesis. PostgreSQL detects
    the dependency cycle and aborts the second committer with
    SQLSTATE 40001. The pool's translation must surface this as
    ``RetryableTransactionError`` — the contract the orchestrator's retry
    loop watches for.
    """

    async def test_postgres_transaction_translates_serialization_failure_to_retryable(
        self, pg_dsn: str
    ) -> None:
        pool = await PostgresPool.create(dsn=pg_dsn, config=TEST_POSTGRES_CONFIG)
        try:
            embedding = [1.0 / SCHEMA_DIM] * SCHEMA_DIM
            seed_correlation_id = "00000000-0000-0000-0000-0000000000c1"
            t1_correlation_id = "00000000-0000-0000-0000-0000000000c2"
            t2_correlation_id = "00000000-0000-0000-0000-0000000000c3"

            async with pool.session() as repos:
                hypothesis = await repos.hypotheses.store(
                    content="serialization-conflict claim",
                    embedding=embedding,
                    created_at=0,
                )
                await repos.requests.store(
                    RequestRecord(id=seed_correlation_id, oracle_id="sub:seed", timestamp=0)
                )
                await repos.requests.store(
                    RequestRecord(id=t1_correlation_id, oracle_id="sub:t1", timestamp=1000)
                )
                await repos.requests.store(
                    RequestRecord(id=t2_correlation_id, oracle_id="sub:t2", timestamp=2000)
                )
                await repos.attestations.append(
                    hypothesis_id=hypothesis.id,
                    oracle_id="sub:seed",
                    correlation_id=seed_correlation_id,
                    timestamp=500,
                    t_oracle=0.5,
                    c_oracle_raw=0.5,
                    c_oracle_discounted=0.25,
                    c_herd=0.4,
                    n_oracle_prior=0,
                )

            t1_read_done = asyncio.Event()
            t2_committed = asyncio.Event()
            retryable_caught: list[RetryableTransactionError] = []

            async def t1() -> None:
                try:
                    async with pool.transaction() as repos:
                        # Snapshot taken here; read the hypothesis's attestations.
                        await repos.attestations.find_by_hypothesis(hypothesis.id)
                        t1_read_done.set()
                        # Wait for T2 to commit a conflicting attestation.
                        await t2_committed.wait()
                        # Now write on the same hypothesis. T1's snapshot is stale
                        # relative to T2's commit — SERIALIZABLE detects the
                        # write-skew at commit time and raises SQLSTATE 40001.
                        await repos.attestations.append(
                            hypothesis_id=hypothesis.id,
                            oracle_id="sub:t1",
                            correlation_id=t1_correlation_id,
                            timestamp=1000,
                            t_oracle=0.5,
                            c_oracle_raw=0.5,
                            c_oracle_discounted=0.25,
                            c_herd=0.4,
                            n_oracle_prior=1,
                        )
                except RetryableTransactionError as e:
                    retryable_caught.append(e)

            async def t2() -> None:
                await t1_read_done.wait()
                async with pool.transaction() as repos:
                    await repos.attestations.find_by_hypothesis(hypothesis.id)
                    await repos.attestations.append(
                        hypothesis_id=hypothesis.id,
                        oracle_id="sub:t2",
                        correlation_id=t2_correlation_id,
                        timestamp=2000,
                        t_oracle=0.5,
                        c_oracle_raw=0.5,
                        c_oracle_discounted=0.25,
                        c_herd=0.4,
                        n_oracle_prior=1,
                    )
                t2_committed.set()

            await asyncio.gather(t1(), t2())

            assert len(retryable_caught) == 1
            assert isinstance(retryable_caught[0], StorageError)

            # T2 committed; T1 aborted. The ledger holds two attestations.
            async with pool.session() as repos:
                rows = await repos.attestations.find_by_hypothesis(hypothesis.id)
            assert len(rows) == 2
            assert {r.oracle_id for r in rows} == {"sub:seed", "sub:t2"}
        finally:
            await pool.close()


class TestCreatePoolThreadsConfig:
    """create_pool() applies the configured PostgresConfig values to AsyncConnectionPool."""

    async def test_create_pool_applies_config(self, pg_dsn: str) -> None:
        config = PostgresConfig(min_size=2, max_size=7, getconn_timeout=3.5, max_waiting=11)
        pool = await create_pool(dsn=pg_dsn, config=config)
        try:
            assert pool.min_size == 2
            assert pool.max_size == 7
            assert pool.timeout == 3.5
            assert pool.max_waiting == 11
        finally:
            await pool.close()

    async def test_postgres_pool_create_threads_config_to_create_pool(self, pg_dsn: str) -> None:
        config = PostgresConfig(min_size=1, max_size=4, getconn_timeout=2.0, max_waiting=8)
        with patch(
            "lore.repositories.postgres.pool.create_pool",
            wraps=create_pool,
        ) as spy:
            pool = await PostgresPool.create(dsn=pg_dsn, config=config)
            try:
                spy.assert_called_once_with(dsn=pg_dsn, config=config)
            finally:
                await pool.close()
