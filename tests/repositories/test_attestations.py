"""Tests for AttestationsRepository Protocol behavior."""

import uuid
from collections.abc import Awaitable, Callable

import pytest

from lore.domain import IntegrityViolation, StorageError
from lore.repositories import AttestationRecord
from lore.repositories.protocols import (
    AttestationsRepository,
    HypothesisRepository,
    RequestRepository,
)
from lore.repositories.records import generate_id
from tests.repositories.conftest import seed_hypothesis, seed_request


class TestAppend:
    async def test_append_generates_attestation_id(
        self,
        hypothesis_repo: HypothesisRepository,
        attestations_repo: AttestationsRepository,
        request_repo: RequestRepository,
    ) -> None:
        h_id = await seed_hypothesis(hypothesis_repo)
        await seed_request(request_repo, correlation_id="00000000-0000-0000-0000-000000000c01")
        await attestations_repo.append(
            AttestationRecord(
                id=generate_id(),
                hypothesis_id=h_id,
                oracle_id="sub:oracle-1",
                correlation_id="00000000-0000-0000-0000-000000000c01",
                timestamp=1000,
                t_oracle=0.5,
                c_oracle_raw=0.5,
                c_oracle_discounted=0.25,
                c_herd=0.4,
                n_oracle_prior=0,
            )
        )
        found = await attestations_repo.find_by_hypothesis(h_id)
        assert len(found) == 1
        uuid.UUID(found[0].id)  # valid UUID — doesn't raise

    async def test_append_and_find_by_hypothesis(
        self,
        hypothesis_repo: HypothesisRepository,
        attestations_repo: AttestationsRepository,
        request_repo: RequestRepository,
    ) -> None:
        h_id = await seed_hypothesis(hypothesis_repo)
        await seed_request(request_repo, correlation_id="00000000-0000-0000-0000-000000000c01")
        await attestations_repo.append(
            AttestationRecord(
                id=generate_id(),
                hypothesis_id=h_id,
                oracle_id="sub:oracle-1",
                correlation_id="00000000-0000-0000-0000-000000000c01",
                timestamp=1000,
                t_oracle=0.5,
                c_oracle_raw=0.5,
                c_oracle_discounted=0.25,
                c_herd=0.4,
                n_oracle_prior=0,
            )
        )
        found = await attestations_repo.find_by_hypothesis(h_id)
        assert len(found) == 1
        assert found[0].hypothesis_id == h_id
        assert found[0].oracle_id == "sub:oracle-1"
        assert found[0].correlation_id == "00000000-0000-0000-0000-000000000c01"
        assert found[0].timestamp == 1000
        assert found[0].t_oracle == 0.5
        assert found[0].c_oracle_raw == 0.5
        assert found[0].c_oracle_discounted == 0.25
        assert found[0].c_herd == 0.4

    async def test_append_multiple_attestations(
        self,
        hypothesis_repo: HypothesisRepository,
        attestations_repo: AttestationsRepository,
        request_repo: RequestRepository,
    ) -> None:
        h_id = await seed_hypothesis(hypothesis_repo)
        await seed_request(request_repo, correlation_id="00000000-0000-0000-0000-000000000c01")
        await attestations_repo.append(
            AttestationRecord(
                id=generate_id(),
                hypothesis_id=h_id,
                oracle_id="sub:oracle-1",
                correlation_id="00000000-0000-0000-0000-000000000c01",
                timestamp=1000,
                t_oracle=0.5,
                c_oracle_raw=0.3,
                c_oracle_discounted=0.15,
                c_herd=0.3,
                n_oracle_prior=0,
            )
        )
        await attestations_repo.append(
            AttestationRecord(
                id=generate_id(),
                hypothesis_id=h_id,
                oracle_id="sub:oracle-1",
                correlation_id="00000000-0000-0000-0000-000000000c01",
                timestamp=2000,
                t_oracle=0.5,
                c_oracle_raw=0.7,
                c_oracle_discounted=0.35,
                c_herd=0.5,
                n_oracle_prior=0,
            )
        )
        found = await attestations_repo.find_by_hypothesis(h_id)
        assert len(found) == 2

    async def test_append_with_missing_hypothesis_raises(
        self,
        attestations_repo: AttestationsRepository,
        request_repo: RequestRepository,
    ) -> None:
        await seed_request(request_repo, correlation_id="00000000-0000-0000-0000-000000000c01")
        with pytest.raises(IntegrityViolation):
            await attestations_repo.append(
                AttestationRecord(
                    id=generate_id(),
                    hypothesis_id="00000000-0000-0000-0000-000000000001",
                    oracle_id="sub:oracle-1",
                    correlation_id="00000000-0000-0000-0000-000000000c01",
                    timestamp=1000,
                    t_oracle=0.5,
                    c_oracle_raw=0.5,
                    c_oracle_discounted=0.25,
                    c_herd=0.4,
                    n_oracle_prior=0,
                )
            )


class TestNOraclePriorRoundTrip:
    """``n_oracle_prior`` is a stored column written at attestation time.

    The Recorder computes ``n_oracle_prior`` from a snapshot of the
    attestation map and passes the result to ``append``. These tests
    exercise the plumbing without involving the Recorder.
    """

    async def test_append_and_find_by_hypothesis_round_trips_n_oracle_prior(
        self,
        hypothesis_repo: HypothesisRepository,
        attestations_repo: AttestationsRepository,
        request_repo: RequestRepository,
    ) -> None:
        h_id = await seed_hypothesis(hypothesis_repo)
        await seed_request(request_repo, correlation_id="00000000-0000-0000-0000-000000000c01")
        await attestations_repo.append(
            AttestationRecord(
                id=generate_id(),
                hypothesis_id=h_id,
                oracle_id="sub:oracle-1",
                correlation_id="00000000-0000-0000-0000-000000000c01",
                timestamp=1000,
                t_oracle=0.5,
                c_oracle_raw=0.5,
                c_oracle_discounted=0.25,
                c_herd=0.4,
                n_oracle_prior=3,
            )
        )
        found = await attestations_repo.find_by_hypothesis(h_id)
        assert len(found) == 1
        assert found[0].n_oracle_prior == 3

    async def test_append_and_find_by_hypothesis_accepts_zero_n_oracle_prior(
        self,
        hypothesis_repo: HypothesisRepository,
        attestations_repo: AttestationsRepository,
        request_repo: RequestRepository,
    ) -> None:
        """First-attestation boundary: ``n_oracle_prior = 0`` round-trips."""
        h_id = await seed_hypothesis(hypothesis_repo)
        await seed_request(request_repo, correlation_id="00000000-0000-0000-0000-000000000c01")
        await attestations_repo.append(
            AttestationRecord(
                id=generate_id(),
                hypothesis_id=h_id,
                oracle_id="sub:oracle-1",
                correlation_id="00000000-0000-0000-0000-000000000c01",
                timestamp=1000,
                t_oracle=0.5,
                c_oracle_raw=0.5,
                c_oracle_discounted=0.25,
                c_herd=0.4,
                n_oracle_prior=0,
            )
        )
        found = await attestations_repo.find_by_hypothesis(h_id)
        assert len(found) == 1
        assert found[0].n_oracle_prior == 0


class TestFindByHypothesis:
    async def test_find_by_hypothesis_returns_ordered_by_timestamp(
        self,
        hypothesis_repo: HypothesisRepository,
        attestations_repo: AttestationsRepository,
        request_repo: RequestRepository,
    ) -> None:
        h_id = await seed_hypothesis(hypothesis_repo)
        await seed_request(request_repo, correlation_id="00000000-0000-0000-0000-000000000c01")
        # Insert out of order
        for ts in (3000, 1000, 2000):
            await attestations_repo.append(
                AttestationRecord(
                    id=generate_id(),
                    hypothesis_id=h_id,
                    oracle_id="sub:oracle-1",
                    correlation_id="00000000-0000-0000-0000-000000000c01",
                    timestamp=ts,
                    t_oracle=0.5,
                    c_oracle_raw=0.5,
                    c_oracle_discounted=0.25,
                    c_herd=0.4,
                    n_oracle_prior=0,
                )
            )
        found = await attestations_repo.find_by_hypothesis(h_id)
        timestamps = [r.timestamp for r in found]
        assert timestamps == [1000, 2000, 3000]

    async def test_find_by_hypothesis_missing_returns_empty(
        self, attestations_repo: AttestationsRepository
    ) -> None:
        result = await attestations_repo.find_by_hypothesis("00000000-0000-0000-0000-000000000000")
        assert result == []


class TestFindByHypotheses:
    async def test_find_by_hypotheses_returns_grouped_results(
        self,
        hypothesis_repo: HypothesisRepository,
        attestations_repo: AttestationsRepository,
        request_repo: RequestRepository,
    ) -> None:
        h1 = await seed_hypothesis(hypothesis_repo)
        h2 = await seed_hypothesis(hypothesis_repo)
        await seed_request(request_repo, correlation_id="00000000-0000-0000-0000-000000000c01")
        await seed_request(request_repo, correlation_id="00000000-0000-0000-0000-000000000c02")
        await attestations_repo.append(
            AttestationRecord(
                id=generate_id(),
                hypothesis_id=h1,
                oracle_id="sub:oracle-1",
                correlation_id="00000000-0000-0000-0000-000000000c01",
                timestamp=1000,
                t_oracle=0.5,
                c_oracle_raw=0.5,
                c_oracle_discounted=0.25,
                c_herd=0.4,
                n_oracle_prior=0,
            )
        )
        await attestations_repo.append(
            AttestationRecord(
                id=generate_id(),
                hypothesis_id=h2,
                oracle_id="sub:oracle-2",
                correlation_id="00000000-0000-0000-0000-000000000c02",
                timestamp=2000,
                t_oracle=0.6,
                c_oracle_raw=0.7,
                c_oracle_discounted=0.42,
                c_herd=0.6,
                n_oracle_prior=0,
            )
        )
        result = await attestations_repo.find_by_hypotheses([h1, h2])
        assert len(result[h1]) == 1
        assert len(result[h2]) == 1
        assert result[h1][0].hypothesis_id == h1
        assert result[h2][0].hypothesis_id == h2

    async def test_find_by_hypotheses_missing_ids_have_empty_lists(
        self,
        hypothesis_repo: HypothesisRepository,
        attestations_repo: AttestationsRepository,
        request_repo: RequestRepository,
    ) -> None:
        h1 = await seed_hypothesis(hypothesis_repo)
        missing = "00000000-0000-0000-0000-000000000000"
        await seed_request(request_repo, correlation_id="00000000-0000-0000-0000-000000000c01")
        await attestations_repo.append(
            AttestationRecord(
                id=generate_id(),
                hypothesis_id=h1,
                oracle_id="sub:oracle-1",
                correlation_id="00000000-0000-0000-0000-000000000c01",
                timestamp=1000,
                t_oracle=0.5,
                c_oracle_raw=0.5,
                c_oracle_discounted=0.25,
                c_herd=0.4,
                n_oracle_prior=0,
            )
        )
        result = await attestations_repo.find_by_hypotheses([h1, missing])
        assert len(result[h1]) == 1
        assert result[missing] == []

    async def test_find_by_hypotheses_empty_input_returns_empty_dict(
        self, attestations_repo: AttestationsRepository
    ) -> None:
        result = await attestations_repo.find_by_hypotheses([])
        assert result == {}

    async def test_find_by_hypotheses_preserves_ordering(
        self,
        hypothesis_repo: HypothesisRepository,
        attestations_repo: AttestationsRepository,
        request_repo: RequestRepository,
    ) -> None:
        h_id = await seed_hypothesis(hypothesis_repo)
        await seed_request(request_repo, correlation_id="00000000-0000-0000-0000-000000000c01")
        for ts in (3000, 1000, 2000):
            await attestations_repo.append(
                AttestationRecord(
                    id=generate_id(),
                    hypothesis_id=h_id,
                    oracle_id="sub:oracle-1",
                    correlation_id="00000000-0000-0000-0000-000000000c01",
                    timestamp=ts,
                    t_oracle=0.5,
                    c_oracle_raw=0.5,
                    c_oracle_discounted=0.25,
                    c_herd=0.4,
                    n_oracle_prior=0,
                )
            )
        result = await attestations_repo.find_by_hypotheses([h_id])
        timestamps = [r.timestamp for r in result[h_id]]
        assert timestamps == [1000, 2000, 3000]


class TestStorageError:
    async def test_append_raises(
        self,
        sabotage_connection: Callable[[], Awaitable[None]],
        attestations_repo: AttestationsRepository,
    ) -> None:
        await sabotage_connection()
        with pytest.raises(StorageError):
            await attestations_repo.append(
                AttestationRecord(
                    id=generate_id(),
                    hypothesis_id="00000000-0000-0000-0000-000000000001",
                    oracle_id="sub:oracle-1",
                    correlation_id="00000000-0000-0000-0000-000000000c01",
                    timestamp=1000,
                    t_oracle=0.5,
                    c_oracle_raw=0.5,
                    c_oracle_discounted=0.25,
                    c_herd=0.4,
                    n_oracle_prior=0,
                )
            )

    async def test_find_by_hypothesis_raises(
        self,
        sabotage_connection: Callable[[], Awaitable[None]],
        attestations_repo: AttestationsRepository,
    ) -> None:
        await sabotage_connection()
        with pytest.raises(StorageError):
            await attestations_repo.find_by_hypothesis("00000000-0000-0000-0000-000000000000")

    async def test_find_by_hypotheses_raises(
        self,
        sabotage_connection: Callable[[], Awaitable[None]],
        attestations_repo: AttestationsRepository,
    ) -> None:
        await sabotage_connection()
        with pytest.raises(StorageError):
            await attestations_repo.find_by_hypotheses(["00000000-0000-0000-0000-000000000000"])

    async def test_fetch_trust_alignments_raises(
        self,
        sabotage_connection: Callable[[], Awaitable[None]],
        attestations_repo: AttestationsRepository,
    ) -> None:
        await sabotage_connection()
        with pytest.raises(StorageError):
            await attestations_repo.fetch_trust_alignments(
                oracle_id="sub:oracle-A",
                t_now=1000,
                trust_half_life=1e12,
            )


class TestCheckConstraints:
    """Database-level CHECK constraints reject out-of-range trust columns.

    Belt-and-braces: the repository write path now also enforces these via
    typed attestations, but a CHECK constraint catches direct SQL writes
    and any future write path that bypasses the constructor.
    """

    async def test_append_with_c_herd_above_one_raises_storage_error(
        self,
        hypothesis_repo: HypothesisRepository,
        attestations_repo: AttestationsRepository,
        request_repo: RequestRepository,
    ) -> None:
        h_id = await seed_hypothesis(hypothesis_repo)
        await seed_request(request_repo, correlation_id="00000000-0000-0000-0000-000000000c01")
        # The out-of-bounds value violates AttestationRecord validation, so
        # build the record via ``model_construct`` to exercise the DB CHECK
        # constraint directly.
        with pytest.raises(StorageError):
            await attestations_repo.append(
                AttestationRecord.model_construct(
                    id=generate_id(),
                    hypothesis_id=h_id,
                    oracle_id="sub:oracle-1",
                    correlation_id="00000000-0000-0000-0000-000000000c01",
                    timestamp=1000,
                    t_oracle=0.5,
                    c_oracle_raw=0.5,
                    c_oracle_discounted=0.25,
                    c_herd=1.5,  # out of [-1, 1]
                    n_oracle_prior=0,
                )
            )

    async def test_append_with_c_oracle_raw_below_minus_one_raises_storage_error(
        self,
        hypothesis_repo: HypothesisRepository,
        attestations_repo: AttestationsRepository,
        request_repo: RequestRepository,
    ) -> None:
        h_id = await seed_hypothesis(hypothesis_repo)
        await seed_request(request_repo, correlation_id="00000000-0000-0000-0000-000000000c01")
        with pytest.raises(StorageError):
            await attestations_repo.append(
                AttestationRecord.model_construct(
                    id=generate_id(),
                    hypothesis_id=h_id,
                    oracle_id="sub:oracle-1",
                    correlation_id="00000000-0000-0000-0000-000000000c01",
                    timestamp=1000,
                    t_oracle=0.5,
                    c_oracle_raw=-1.5,  # out of [-1, 1]
                    c_oracle_discounted=0.25,
                    c_herd=0.4,
                    n_oracle_prior=0,
                )
            )

    async def test_append_with_c_oracle_discounted_above_one_raises_storage_error(
        self,
        hypothesis_repo: HypothesisRepository,
        attestations_repo: AttestationsRepository,
        request_repo: RequestRepository,
    ) -> None:
        h_id = await seed_hypothesis(hypothesis_repo)
        await seed_request(request_repo, correlation_id="00000000-0000-0000-0000-000000000c01")
        with pytest.raises(StorageError):
            await attestations_repo.append(
                AttestationRecord.model_construct(
                    id=generate_id(),
                    hypothesis_id=h_id,
                    oracle_id="sub:oracle-1",
                    correlation_id="00000000-0000-0000-0000-000000000c01",
                    timestamp=1000,
                    t_oracle=0.5,
                    c_oracle_raw=0.5,
                    c_oracle_discounted=1.1,  # out of [-1, 1]
                    c_herd=0.4,
                    n_oracle_prior=0,
                )
            )

    async def test_append_with_t_oracle_negative_raises_storage_error(
        self,
        hypothesis_repo: HypothesisRepository,
        attestations_repo: AttestationsRepository,
        request_repo: RequestRepository,
    ) -> None:
        """t_oracle is bounded to [0, 1] — negative trust is non-sensical."""
        h_id = await seed_hypothesis(hypothesis_repo)
        await seed_request(request_repo, correlation_id="00000000-0000-0000-0000-000000000c01")
        with pytest.raises(StorageError):
            await attestations_repo.append(
                AttestationRecord.model_construct(
                    id=generate_id(),
                    hypothesis_id=h_id,
                    oracle_id="sub:oracle-1",
                    correlation_id="00000000-0000-0000-0000-000000000c01",
                    timestamp=1000,
                    t_oracle=-0.1,  # out of [0, 1]
                    c_oracle_raw=0.5,
                    c_oracle_discounted=0.25,
                    c_herd=0.4,
                    n_oracle_prior=0,
                )
            )

    async def test_append_with_t_oracle_above_one_raises_storage_error(
        self,
        hypothesis_repo: HypothesisRepository,
        attestations_repo: AttestationsRepository,
        request_repo: RequestRepository,
    ) -> None:
        h_id = await seed_hypothesis(hypothesis_repo)
        await seed_request(request_repo, correlation_id="00000000-0000-0000-0000-000000000c01")
        with pytest.raises(StorageError):
            await attestations_repo.append(
                AttestationRecord.model_construct(
                    id=generate_id(),
                    hypothesis_id=h_id,
                    oracle_id="sub:oracle-1",
                    correlation_id="00000000-0000-0000-0000-000000000c01",
                    timestamp=1000,
                    t_oracle=1.5,  # out of [0, 1]
                    c_oracle_raw=0.5,
                    c_oracle_discounted=0.25,
                    c_herd=0.4,
                    n_oracle_prior=0,
                )
            )


class TestTrustSignalReadValidation:
    """Malformed ledger rows surface as StorageError on the read path.

    Repositories now construct ``TrustSignal`` with full validation; a row
    that violates an in-process invariant (e.g. negative timestamp — no DB
    CHECK enforces this) is wrapped as ``StorageError`` because the source
    of the malformed data is the database.
    """

    async def test_fetch_trust_alignments_negative_timestamp_raises_storage_error(
        self,
        hypothesis_repo: HypothesisRepository,
        attestations_repo: AttestationsRepository,
        request_repo: RequestRepository,
    ) -> None:
        h_id = await seed_hypothesis(hypothesis_repo)
        await seed_request(
            request_repo,
            correlation_id="00000000-0000-0000-0000-000000000c01",
            oracle_id="sub:oracle-A",
            timestamp=0,
        )
        # Use a negative timestamp — passes column NOT NULL, has no CHECK,
        # but TrustSignal._validate_timestamp rejects it. ``model_construct``
        # bypasses ``AttestationRecord._validate_timestamp`` so the negative
        # value reaches storage and the read-side validation can be exercised.
        await attestations_repo.append(
            AttestationRecord.model_construct(
                id=generate_id(),
                hypothesis_id=h_id,
                oracle_id="sub:oracle-A",
                correlation_id="00000000-0000-0000-0000-000000000c01",
                timestamp=-1,
                t_oracle=0.5,
                c_oracle_raw=0.5,
                c_oracle_discounted=0.25,
                c_herd=0.4,
                n_oracle_prior=0,
            )
        )
        with pytest.raises(StorageError):
            await attestations_repo.fetch_trust_alignments(
                oracle_id="sub:oracle-A",
                t_now=1000,
                trust_half_life=1e12,
            )
