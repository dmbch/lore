"""Tests for AttestationsRepository Protocol behavior."""

import math
import uuid
from collections.abc import Awaitable, Callable

import pytest

from lore.domain import TRANSFER_ORACLE, EvidenceInput, IntegrityViolation, StorageError
from lore.repositories import AttestationRecord, DecayWindow
from lore.repositories.protocols import (
    AttestationsRepository,
    HypothesisRepository,
    RequestRepository,
)
from lore.repositories.records import generate_id
from tests.repositories.conftest import NO_DECAY_TRUST_HL as _NO_DECAY_HL
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
        uuid.UUID(found[0].id)  # valid UUID: doesn't raise

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
        assert len(result[h1].rows) == 1
        assert len(result[h2].rows) == 1
        assert result[h1].rows[0].hypothesis_id == h1
        assert result[h2].rows[0].hypothesis_id == h2
        # Unwindowed: aggregates match the returned rows.
        assert result[h1].oracle_count == 1
        assert result[h1].last_attested == 1000

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
        assert len(result[h1].rows) == 1
        assert result[missing].rows == []
        assert result[missing].oracle_count == 0
        assert result[missing].last_attested is None

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
        timestamps = [r.timestamp for r in result[h_id].rows]
        assert timestamps == [1000, 2000, 3000]

    async def test_find_by_hypotheses_windows_rows_but_keeps_exact_aggregates(
        self,
        hypothesis_repo: HypothesisRepository,
        attestations_repo: AttestationsRepository,
        request_repo: RequestRepository,
    ) -> None:
        """Stale rows leave the fused view; the summary stays full-history.

        Window = [t_now - 5*half_life, t_now] = [5000, 10000]: the ts=100
        row is out, the ts=9000 row is in. oracle_count still counts the
        oracle whose only row aged out; last_attested sees the whole
        ledger.
        """
        h_id = await seed_hypothesis(hypothesis_repo)
        await seed_request(request_repo, correlation_id="00000000-0000-0000-0000-000000000c01")
        for ts, oracle_id in ((100, "sub:oracle-1"), (9000, "sub:oracle-2")):
            await attestations_repo.append(
                AttestationRecord(
                    id=generate_id(),
                    hypothesis_id=h_id,
                    oracle_id=oracle_id,
                    correlation_id="00000000-0000-0000-0000-000000000c01",
                    timestamp=ts,
                    t_oracle=0.5,
                    c_oracle_raw=0.5,
                    c_oracle_discounted=0.25,
                    c_herd=0.4,
                    n_oracle_prior=0,
                )
            )
        result = await attestations_repo.find_by_hypotheses(
            [h_id], window=DecayWindow(t_now=10_000, half_life=1000.0)
        )
        view = result[h_id]
        assert [r.timestamp for r in view.rows] == [9000]
        assert view.oracle_count == 2
        assert view.last_attested == 9000

    async def test_find_by_hypotheses_all_stale_view_still_reports_history(
        self,
        hypothesis_repo: HypothesisRepository,
        attestations_repo: AttestationsRepository,
        request_repo: RequestRepository,
    ) -> None:
        """A hypothesis whose whole ledger aged out is stale, not unattested.

        Empty windowed rows with oracle_count = 1 and a real
        last_attested is the "stale since" signal; a never-attested
        hypothesis reports count 0 and last_attested None.
        """
        h_id = await seed_hypothesis(hypothesis_repo)
        await seed_request(request_repo, correlation_id="00000000-0000-0000-0000-000000000c01")
        await attestations_repo.append(
            AttestationRecord(
                id=generate_id(),
                hypothesis_id=h_id,
                oracle_id="sub:oracle-1",
                correlation_id="00000000-0000-0000-0000-000000000c01",
                timestamp=100,
                t_oracle=0.5,
                c_oracle_raw=0.5,
                c_oracle_discounted=0.25,
                c_herd=0.4,
                n_oracle_prior=0,
            )
        )
        never = "00000000-0000-0000-0000-000000000000"
        result = await attestations_repo.find_by_hypotheses(
            [h_id, never], window=DecayWindow(t_now=10_000, half_life=1000.0)
        )
        assert result[h_id].rows == []
        assert result[h_id].oracle_count == 1
        assert result[h_id].last_attested == 100
        assert result[never].rows == []
        assert result[never].oracle_count == 0
        assert result[never].last_attested is None

    async def test_find_by_hypotheses_infinite_window_includes_ancient_rows(
        self,
        hypothesis_repo: HypothesisRepository,
        attestations_repo: AttestationsRepository,
        request_repo: RequestRepository,
    ) -> None:
        """half_life=inf collapses the window's lower bound to zero."""
        h_id = await seed_hypothesis(hypothesis_repo)
        await seed_request(request_repo, correlation_id="00000000-0000-0000-0000-000000000c01")
        await attestations_repo.append(
            AttestationRecord(
                id=generate_id(),
                hypothesis_id=h_id,
                oracle_id="sub:oracle-1",
                correlation_id="00000000-0000-0000-0000-000000000c01",
                timestamp=1,
                t_oracle=0.5,
                c_oracle_raw=0.5,
                c_oracle_discounted=0.25,
                c_herd=0.4,
                n_oracle_prior=0,
            )
        )
        result = await attestations_repo.find_by_hypotheses(
            [h_id], window=DecayWindow(t_now=10_000, half_life=math.inf)
        )
        assert [r.timestamp for r in result[h_id].rows] == [1]

    async def test_oracle_count_collapses_repeat_attestations(
        self,
        hypothesis_repo: HypothesisRepository,
        attestations_repo: AttestationsRepository,
        request_repo: RequestRepository,
    ) -> None:
        """One oracle attesting twice is one examiner, not two.

        Repetition is fusion's business; the count reports scrutiny.
        """
        h_id = await seed_hypothesis(hypothesis_repo)
        await seed_request(request_repo, correlation_id="00000000-0000-0000-0000-000000000c01")
        for ts in (1000, 2000):
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
        assert result[h_id].oracle_count == 1

    async def test_oracle_count_counts_transfer_carrier_once(
        self,
        hypothesis_repo: HypothesisRepository,
        attestations_repo: AttestationsRepository,
        request_repo: RequestRepository,
    ) -> None:
        """The synthetic transfer carrier counts as one distinct voice.

        One counting policy everywhere: maturity's ``n_oracle_prior``
        and the witness rule already admit the carrier (docs/logic.md:
        formally another oracle), and its evidence on the belief is
        real, so the read model admits it too. One oracle row plus one
        transfer row is two; DISTINCT would still collapse repeats.
        """
        h_id = await seed_hypothesis(hypothesis_repo)
        await seed_request(request_repo, correlation_id="00000000-0000-0000-0000-000000000c01")
        for ts, oracle_id in ((1000, "sub:oracle-1"), (2000, TRANSFER_ORACLE)):
            await attestations_repo.append(
                AttestationRecord(
                    id=generate_id(),
                    hypothesis_id=h_id,
                    oracle_id=oracle_id,
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
        assert result[h_id].oracle_count == 2
        assert result[h_id].last_attested == 2000

    async def test_oracle_count_counts_distinct_oracles(
        self,
        hypothesis_repo: HypothesisRepository,
        attestations_repo: AttestationsRepository,
        request_repo: RequestRepository,
    ) -> None:
        """Two oracles across three rows: two examiners.

        The repeat row keeps this discriminating against a raw row count.
        """
        h_id = await seed_hypothesis(hypothesis_repo)
        await seed_request(request_repo, correlation_id="00000000-0000-0000-0000-000000000c01")
        rows = ((1000, "sub:oracle-1"), (2000, "sub:oracle-2"), (3000, "sub:oracle-1"))
        for ts, oracle_id in rows:
            await attestations_repo.append(
                AttestationRecord(
                    id=generate_id(),
                    hypothesis_id=h_id,
                    oracle_id=oracle_id,
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
        assert result[h_id].oracle_count == 2


async def _append_evidence_row(
    repo: AttestationsRepository,
    *,
    hypothesis_id: str,
    oracle_id: str,
    timestamp: int,
    c_oracle_discounted: float = 0.25,
    record_id: str | None = None,
) -> None:
    """Append a row with the fields ``fetch_herd_evidence`` projects.

    Requires the parent request row for correlation
    ``00000000-0000-0000-0000-000000000c01`` (see :func:`seed_request`).
    """
    await repo.append(
        AttestationRecord(
            id=record_id or generate_id(),
            hypothesis_id=hypothesis_id,
            oracle_id=oracle_id,
            correlation_id="00000000-0000-0000-0000-000000000c01",
            timestamp=timestamp,
            t_oracle=0.5,
            c_oracle_raw=0.5,
            c_oracle_discounted=c_oracle_discounted,
            c_herd=0.4,
            n_oracle_prior=0,
        )
    )


class TestFetchHerdEvidence:
    """Others-only fusion evidence per hypothesis, for the trust witness rule."""

    async def test_fetch_herd_evidence_excludes_oracle_rows(
        self,
        hypothesis_repo: HypothesisRepository,
        attestations_repo: AttestationsRepository,
        request_repo: RequestRepository,
    ) -> None:
        h_id = await seed_hypothesis(hypothesis_repo)
        await seed_request(request_repo, correlation_id="00000000-0000-0000-0000-000000000c01")
        await _append_evidence_row(
            attestations_repo,
            hypothesis_id=h_id,
            oracle_id="sub:oracle-X",
            timestamp=100,
            c_oracle_discounted=0.125,
        )
        await _append_evidence_row(
            attestations_repo,
            hypothesis_id=h_id,
            oracle_id="sub:oracle-Y",
            timestamp=200,
            c_oracle_discounted=0.25,
        )
        result = await attestations_repo.fetch_herd_evidence(
            [h_id],
            exclude_oracle="sub:oracle-X",
            window=DecayWindow(t_now=1000, half_life=_NO_DECAY_HL),
        )
        assert result[h_id] == [EvidenceInput(c_oracle_discounted=0.25, timestamp=200)]

    async def test_fetch_herd_evidence_includes_transfer_rows(
        self,
        hypothesis_repo: HypothesisRepository,
        attestations_repo: AttestationsRepository,
        request_repo: RequestRepository,
    ) -> None:
        """The synthetic ``_transfer`` oracle is an ordinary includable oracle."""
        h_id = await seed_hypothesis(hypothesis_repo)
        await seed_request(request_repo, correlation_id="00000000-0000-0000-0000-000000000c01")
        await _append_evidence_row(
            attestations_repo,
            hypothesis_id=h_id,
            oracle_id=TRANSFER_ORACLE,
            timestamp=100,
            c_oracle_discounted=-0.5,
        )
        result = await attestations_repo.fetch_herd_evidence(
            [h_id],
            exclude_oracle="sub:oracle-X",
            window=DecayWindow(t_now=1000, half_life=_NO_DECAY_HL),
        )
        assert result[h_id] == [EvidenceInput(c_oracle_discounted=-0.5, timestamp=100)]

    async def test_fetch_herd_evidence_applies_decay_window(
        self,
        hypothesis_repo: HypothesisRepository,
        attestations_repo: AttestationsRepository,
        request_repo: RequestRepository,
    ) -> None:
        """Window is [t_now - 5 * half_life, t_now]; infinite half-life keeps all history."""
        h_id = await seed_hypothesis(hypothesis_repo)
        await seed_request(request_repo, correlation_id="00000000-0000-0000-0000-000000000c01")
        for ts in (100, 600, 1500):  # stale, in-window, future
            await _append_evidence_row(
                attestations_repo,
                hypothesis_id=h_id,
                oracle_id="sub:oracle-Y",
                timestamp=ts,
            )
        windowed = await attestations_repo.fetch_herd_evidence(
            [h_id],
            exclude_oracle="sub:oracle-X",
            window=DecayWindow(t_now=1000, half_life=100.0),
        )
        assert [row.timestamp for row in windowed[h_id]] == [600]
        unwindowed = await attestations_repo.fetch_herd_evidence(
            [h_id],
            exclude_oracle="sub:oracle-X",
            window=DecayWindow(t_now=1000, half_life=float("inf")),
        )
        assert [row.timestamp for row in unwindowed[h_id]] == [100, 600]

    async def test_fetch_herd_evidence_empty_for_unwitnessed(
        self,
        hypothesis_repo: HypothesisRepository,
        attestations_repo: AttestationsRepository,
        request_repo: RequestRepository,
    ) -> None:
        """Solo and unattested hypotheses map to empty lists, keys present."""
        h_solo = await seed_hypothesis(hypothesis_repo)
        unattested = "00000000-0000-0000-0000-000000000000"
        await seed_request(request_repo, correlation_id="00000000-0000-0000-0000-000000000c01")
        await _append_evidence_row(
            attestations_repo,
            hypothesis_id=h_solo,
            oracle_id="sub:oracle-X",
            timestamp=100,
        )
        result = await attestations_repo.fetch_herd_evidence(
            [h_solo, unattested],
            exclude_oracle="sub:oracle-X",
            window=DecayWindow(t_now=1000, half_life=_NO_DECAY_HL),
        )
        assert result == {h_solo: [], unattested: []}

    async def test_fetch_herd_evidence_empty_input_returns_empty_dict(
        self, attestations_repo: AttestationsRepository
    ) -> None:
        result = await attestations_repo.fetch_herd_evidence(
            [],
            exclude_oracle="sub:oracle-X",
            window=DecayWindow(t_now=1000, half_life=_NO_DECAY_HL),
        )
        assert result == {}

    async def test_fetch_herd_evidence_orders_by_timestamp_then_id(
        self,
        hypothesis_repo: HypothesisRepository,
        attestations_repo: AttestationsRepository,
        request_repo: RequestRepository,
    ) -> None:
        """Equal timestamps tie-break on id; insertion order does not leak through."""
        h_id = await seed_hypothesis(hypothesis_repo)
        await seed_request(request_repo, correlation_id="00000000-0000-0000-0000-000000000c01")
        await _append_evidence_row(
            attestations_repo,
            hypothesis_id=h_id,
            oracle_id="sub:oracle-Y",
            timestamp=200,
            c_oracle_discounted=0.25,
            record_id="00000000-0000-0000-0000-00000000000b",
        )
        await _append_evidence_row(
            attestations_repo,
            hypothesis_id=h_id,
            oracle_id="sub:oracle-Y",
            timestamp=200,
            c_oracle_discounted=0.125,
            record_id="00000000-0000-0000-0000-00000000000a",
        )
        await _append_evidence_row(
            attestations_repo,
            hypothesis_id=h_id,
            oracle_id="sub:oracle-Y",
            timestamp=100,
            c_oracle_discounted=0.0625,
        )
        result = await attestations_repo.fetch_herd_evidence(
            [h_id],
            exclude_oracle="sub:oracle-X",
            window=DecayWindow(t_now=1000, half_life=_NO_DECAY_HL),
        )
        assert result[h_id] == [
            EvidenceInput(c_oracle_discounted=0.0625, timestamp=100),
            EvidenceInput(c_oracle_discounted=0.125, timestamp=200),
            EvidenceInput(c_oracle_discounted=0.25, timestamp=200),
        ]


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

    async def test_fetch_herd_evidence_raises(
        self,
        sabotage_connection: Callable[[], Awaitable[None]],
        attestations_repo: AttestationsRepository,
    ) -> None:
        await sabotage_connection()
        with pytest.raises(StorageError):
            await attestations_repo.fetch_herd_evidence(
                ["00000000-0000-0000-0000-000000000000"],
                exclude_oracle="sub:oracle-A",
                window=DecayWindow(t_now=1000, half_life=1e12),
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
        """t_oracle is bounded to [0, 1]: negative trust is non-sensical."""
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
    that violates an in-process invariant (e.g. negative timestamp: no DB
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
        # Use a negative timestamp: passes column NOT NULL, has no CHECK,
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
