"""Tests for RequestRepository Protocol behavior."""

from collections.abc import Awaitable, Callable
from typing import Any

import aiosqlite
import psycopg
import pytest

from lore.domain import StorageError
from lore.repositories.protocols import (
    AttestationsRepository,
    HypothesisRepository,
    RequestRepository,
)
from lore.repositories.records import AttestationRecord, RequestRecord, generate_id
from tests.repositories.conftest import BackendFixture, seed_hypothesis


def _make_request(
    *,
    id: str = "00000000-0000-0000-0000-000000000001",
    oracle_id: str = "sub:oracle-A",
    timestamp: int = 1000,
    question: str | None = "what is X?",
    context: str | None = "investigating Y",
    hypothesis: str | None = "service X uses gRPC",
    reasoning: str | None = "grep showed gRPC imports",
    confidence: float | None = 0.75,
) -> RequestRecord:
    return RequestRecord(
        id=id,
        oracle_id=oracle_id,
        timestamp=timestamp,
        question=question,
        context=context,
        hypothesis=hypothesis,
        reasoning=reasoning,
        confidence=confidence,
    )


_SELECT_ALL = (
    "SELECT id, oracle_id, timestamp, question, context, hypothesis, reasoning, confidence"
    " FROM requests WHERE id = ?"
)
_SELECT_ALL_PG = _SELECT_ALL.replace("?", "%s")


async def _fetch_row(
    raw_conn: aiosqlite.Connection | psycopg.AsyncConnection[Any], id: str
) -> tuple[Any, ...] | None:
    if isinstance(raw_conn, aiosqlite.Connection):
        cursor = await raw_conn.execute(_SELECT_ALL, (id,))
        row = await cursor.fetchone()
        return tuple(row) if row is not None else None
    cur = await raw_conn.execute(_SELECT_ALL_PG, (id,))
    row = await cur.fetchone()
    if row is None:
        return None
    # Postgres returns ``uuid.UUID`` for the UUID-typed ``id`` column;
    # SQLite stores it as TEXT and returns a plain string. Normalise the
    # backend-specific type so test assertions can compare against the
    # input string directly.
    normalised = (str(row[0]), *row[1:])
    return normalised


class TestStore:
    async def test_store_persists_all_fields(
        self,
        request_repo: RequestRepository,
        backend: BackendFixture,
    ) -> None:
        record = _make_request(id="00000000-0000-0000-0000-000000000a4a")
        await request_repo.store(record)

        row = await _fetch_row(backend.raw_conn, "00000000-0000-0000-0000-000000000a4a")
        assert row is not None
        assert row[0] == "00000000-0000-0000-0000-000000000a4a"
        assert row[1] == "sub:oracle-A"
        assert row[2] == 1000
        assert row[3] == "what is X?"
        assert row[4] == "investigating Y"
        assert row[5] == "service X uses gRPC"
        assert row[6] == "grep showed gRPC imports"
        assert row[7] == 0.75

    async def test_store_persists_vacuous_confidence_as_zero(
        self,
        request_repo: RequestRepository,
        backend: BackendFixture,
    ) -> None:
        """confidence=0.0 is a scalar, not absence: must round-trip as 0.0."""
        await request_repo.store(
            _make_request(id="00000000-0000-0000-0000-000000bacacd", confidence=0.0)
        )

        row = await _fetch_row(backend.raw_conn, "00000000-0000-0000-0000-000000bacacd")
        assert row is not None
        assert row[7] == 0.0
        assert row[7] is not None

    async def test_store_persists_sparse_fields(
        self,
        request_repo: RequestRepository,
        backend: BackendFixture,
    ) -> None:
        await request_repo.store(
            _make_request(
                id="00000000-0000-0000-0000-000000050050",
                context=None,
                hypothesis=None,
                reasoning=None,
                confidence=None,
            )
        )

        row = await _fetch_row(backend.raw_conn, "00000000-0000-0000-0000-000000050050")
        assert row is not None
        assert row[3] == "what is X?"
        assert row[4] is None
        assert row[5] is None
        assert row[6] is None
        assert row[7] is None

    async def test_store_duplicate_id_raises(self, request_repo: RequestRepository) -> None:
        await request_repo.store(_make_request(id="00000000-0000-0000-0000-0000000d9999"))
        with pytest.raises(StorageError):
            await request_repo.store(_make_request(id="00000000-0000-0000-0000-0000000d9999"))


class TestAttestationForeignKey:
    async def test_attestation_fk_rejects_unknown_correlation_id(
        self,
        hypothesis_repo: HypothesisRepository,
        attestations_repo: AttestationsRepository,
    ) -> None:
        h_id = await seed_hypothesis(hypothesis_repo)
        with pytest.raises(StorageError):
            await attestations_repo.append(
                AttestationRecord(
                    id=generate_id(),
                    hypothesis_id=h_id,
                    oracle_id="sub:oracle-A",
                    correlation_id="unseeded-correlation-id",
                    timestamp=1000,
                    t_oracle=0.5,
                    c_oracle_raw=0.5,
                    c_oracle_discounted=0.25,
                    c_herd=0.4,
                    n_oracle_prior=0,
                )
            )

    async def test_attestation_fk_allows_multiple_attestations_per_request(
        self,
        request_repo: RequestRepository,
        hypothesis_repo: HypothesisRepository,
        attestations_repo: AttestationsRepository,
    ) -> None:
        await request_repo.store(_make_request(id="00000000-0000-0000-0000-0000000000fb"))
        h_id = await seed_hypothesis(hypothesis_repo)
        for ts in (1000, 2000, 3000):
            await attestations_repo.append(
                AttestationRecord(
                    id=generate_id(),
                    hypothesis_id=h_id,
                    oracle_id="sub:oracle-A",
                    correlation_id="00000000-0000-0000-0000-0000000000fb",
                    timestamp=ts,
                    t_oracle=0.5,
                    c_oracle_raw=0.5,
                    c_oracle_discounted=0.25,
                    c_herd=0.4,
                    n_oracle_prior=0,
                )
            )
        found = await attestations_repo.find_by_hypothesis(h_id)
        assert len(found) == 3
        assert all(a.correlation_id == "00000000-0000-0000-0000-0000000000fb" for a in found)


class TestStorageError:
    async def test_store_on_closed_connection_raises(
        self,
        sabotage_connection: Callable[[], Awaitable[None]],
        request_repo: RequestRepository,
    ) -> None:
        await sabotage_connection()
        with pytest.raises(StorageError):
            await request_repo.store(_make_request(id="req-after-sabotage"))
