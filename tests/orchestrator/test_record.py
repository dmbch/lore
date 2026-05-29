"""Recorder unit tests — transfer-row threshold sourced from settings.

The Recorder writes a consolidated transfer attestation on a novel hypothesis
when an orthogonal-novel resolution carries contradicted IDs. If the fused
magnitude falls below ``settings.trust.threshold`` — an epistemic-significance
floor decoupled from the math core's IEEE epsilon — no row is written.

These tests drive ``_compute_transfer`` by handing the Recorder an
``ArchivistOutput`` carrying a single contributes/contradicts resolution and
calling the public ``Recorder.dispatch`` entry point; the test then observes
whether a TRANSFER_ORACLE row was appended.
"""

import math
from collections.abc import Sequence

import pytest
import structlog

from lore.config import LoreSettings
from lore.domain import TRANSFER_ORACLE, ArchivistOutput, Resolution, TrustSignal
from lore.math import MathService
from lore.math.opinion import EPSILON
from lore.orchestrator.record import Recorder
from lore.repositories import AttestationRecord, HypothesisRecord, HypothesisResult
from tests.orchestrator.conftest import make_settings


class _StubHypotheses:
    """Returns a fixed record on ``store``; raises on read paths the Recorder doesn't use."""

    def __init__(self) -> None:
        self.stored: list[tuple[str, Sequence[float], int]] = []

    async def store(
        self, *, content: str, embedding: Sequence[float], created_at: int
    ) -> HypothesisRecord:
        self.stored.append((content, embedding, created_at))
        return HypothesisRecord.model_construct(
            id="aaa00000-e29b-41d4-a716-446655440000",
            content=content,
            created_at=created_at,
        )

    async def find_by_id(self, id: str) -> HypothesisRecord | None:
        raise NotImplementedError

    async def search(
        self,
        *,
        embedding: Sequence[float],
        query: str,
        weights: tuple[float, float],
        limit: int,
        fan_out: int,
    ) -> list[HypothesisResult]:
        raise NotImplementedError


class _StubAttestations:
    """In-memory append sink. ``find_*`` methods unused by the dispatched path."""

    def __init__(self) -> None:
        self.appended: list[AttestationRecord] = []

    async def append(self, record: AttestationRecord) -> None:
        self.appended.append(record)

    async def find_by_hypothesis(self, hypothesis_id: str) -> list[AttestationRecord]:
        raise NotImplementedError

    async def find_by_hypotheses(
        self, hypothesis_ids: Sequence[str]
    ) -> dict[str, list[AttestationRecord]]:
        raise NotImplementedError

    async def fetch_trust_alignments(
        self, *, oracle_id: str, t_now: int, trust_half_life: float
    ) -> list[TrustSignal]:
        raise NotImplementedError


# Fixed identifiers used across tests.
_T_NOW = 2_000_000_000
_CONTRADICTED_ID = "550e8400-e29b-41d4-a716-446655440001"
_CORRELATION_ID = "corr-1"
_ORACLE_ID = "alice@example.com"
_NOVEL_CONTENT = "novel hypothesis content"
_CONTRIBUTE_RESOLUTION = Resolution(contributes=_NOVEL_CONTENT, contradicts=[_CONTRADICTED_ID])


def _archivist_output(resolution: Resolution) -> ArchivistOutput:
    """Wrap a single resolution in an ``ArchivistOutput`` for Recorder construction."""
    return ArchivistOutput(reasoning="test", answer="test", resolutions=[resolution])


def _attestation_with_c_herd(c_herd: float) -> AttestationRecord:
    """An AttestationRecord whose ``c_herd`` is the only thing the test cares about."""
    return AttestationRecord.model_construct(
        id="660e8400-e29b-41d4-a716-446655440000",
        hypothesis_id=_CONTRADICTED_ID,
        oracle_id="oracle-other",
        correlation_id="corr-prior",
        timestamp=_T_NOW,
        t_oracle=0.5,
        c_oracle_raw=c_herd,
        c_oracle_discounted=c_herd,
        c_herd=c_herd,
        n_oracle_prior=0,
    )


_DEFAULT_THRESHOLD = 1e-3


def _settings_with_threshold(threshold: float = _DEFAULT_THRESHOLD) -> LoreSettings:
    """A LoreSettings whose ``trust.threshold`` is the only field tests care about."""
    base = make_settings()
    return base.model_copy(update={"trust": base.trust.model_copy(update={"threshold": threshold})})


def _make_recorder(
    *,
    c_herd_of_contradicted: float,
    oracle_id: str = "oracle-1",
    resolution: Resolution = _CONTRIBUTE_RESOLUTION,
    threshold: float = _DEFAULT_THRESHOLD,
) -> tuple[Recorder, _StubAttestations]:
    """Build a Recorder wrapping a single-resolution ``ArchivistOutput``.

    The default resolution contributes a novel hypothesis contradicting one
    seeded ID. Decay is neutralised by setting the latest attestation's
    ``timestamp == t_now`` and using a long half-life; ECBF of a single
    uncertainty-maximized opinion is identity. So the fused result equals the
    seeded ``c_herd`` exactly, and ``c_transfer = -c_herd``.
    """
    hypotheses = _StubHypotheses()
    attestations = _StubAttestations()
    math_service = MathService(c_half_life=86400.0, maturity_k=1.0, t_half_life=86400.0)
    attestation_map = {_CONTRADICTED_ID: [_attestation_with_c_herd(c_herd_of_contradicted)]}
    recorder = Recorder(
        hypotheses=hypotheses,
        attestations=attestations,
        math=math_service,
        reasoned=_archivist_output(resolution),
        attestation_map=attestation_map,
        novel_embeddings={_NOVEL_CONTENT: [0.1, 0.2, 0.3]},
        oracle_id=oracle_id,
        correlation_id=_CORRELATION_ID,
        confidence=0.7,
        t_oracle=0.5,
        t_now=_T_NOW,
        settings=_settings_with_threshold(threshold),
    )
    return recorder, attestations


def _transfer_rows(attestations: _StubAttestations) -> list[AttestationRecord]:
    return [a for a in attestations.appended if a.oracle_id == TRANSFER_ORACLE]


class TestComputeTransferReturnsNoneAtExactlyZero:
    """A fused-result of exactly zero writes no TRANSFER_ORACLE row."""

    async def test_compute_transfer_returns_none_at_exactly_zero(self) -> None:
        recorder, attestations = _make_recorder(c_herd_of_contradicted=0.0)

        await recorder.dispatch()

        assert _transfer_rows(attestations) == []


class TestComputeTransferLogsSkipWhenFusedRoundsToZero:
    """The skip path emits a ``recorder.transfer_skipped`` INFO event.

    Symmetric counterpart to the ``recorder.attestation`` DEBUG event the
    non-skip branch already emits — operators need to see when contradictions
    cancel and no transfer row lands.
    """

    async def test_compute_transfer_logs_skip_when_fused_rounds_to_zero(self) -> None:
        recorder, _ = _make_recorder(c_herd_of_contradicted=0.0)

        with structlog.testing.capture_logs() as cap:
            await recorder.dispatch()

        skip_events = [e for e in cap if e.get("event") == "recorder.transfer_skipped"]
        assert len(skip_events) == 1
        event = skip_events[0]
        assert event["contradicts"] == [_CONTRADICTED_ID]
        assert "c_transfer" in event


class TestComputeTransferReturnsNoneBelowThreshold:
    """A fused-result whose magnitude is below ``settings.trust.threshold`` writes no row.

    Engineered scalar: ``c_herd = 1e-4`` — five orders of magnitude above
    ``Opinion.EPSILON`` (math-core noise floor) but an order below the
    epistemic-significance threshold ``1e-3``. Proves the threshold (not
    EPSILON) is what gates the transfer row.
    """

    async def test_compute_transfer_returns_none_below_threshold(self) -> None:
        c_herd = 1e-4
        assert c_herd > EPSILON, "engineered value must exceed math-core noise floor"
        assert c_herd < _DEFAULT_THRESHOLD, "engineered value must fall below threshold"
        recorder, attestations = _make_recorder(c_herd_of_contradicted=c_herd)

        await recorder.dispatch()

        assert _transfer_rows(attestations) == []


class TestComputeTransferWritesAboveThreshold:
    """A fused-result with magnitude clearly above ``settings.trust.threshold`` writes one row.

    Engineered scalar: ``c_herd = 0.1`` — two orders of magnitude above the
    default threshold so floating-point drift inside ECBF + maximize_uncertainty
    cannot push the value across the gate. The transfer scalar is ``-c_herd``;
    one TRANSFER_ORACLE row is appended on the novel.
    """

    async def test_compute_transfer_writes_above_threshold(self) -> None:
        c_herd = 0.1
        recorder, attestations = _make_recorder(c_herd_of_contradicted=c_herd)

        await recorder.dispatch()

        transfer = _transfer_rows(attestations)
        assert len(transfer) == 1
        # ECBF of a single uncertainty-maximized opinion at t_now == timestamp
        # is identity up to FP rounding inside maximize_uncertainty.
        assert math.isclose(transfer[0].c_herd, -c_herd, abs_tol=EPSILON)


class TestComputeTransferAtThresholdWritesRow:
    """A fused magnitude that meets the threshold writes a transfer row.

    The inline check is strict ``<``, so ``abs(c_transfer) == threshold``
    must produce a row. Engineered ``c_herd = threshold + 1e-6`` clears ECBF
    round-trip noise (bounded by ``Opinion.EPSILON = 1e-9``) while staying
    close enough to the gate that a future widening of the noise window
    would fail this test before silently flipping the inequality direction.
    """

    async def test_compute_transfer_at_threshold_writes_row(self) -> None:
        c_herd = _DEFAULT_THRESHOLD + 1e-6
        recorder, attestations = _make_recorder(c_herd_of_contradicted=c_herd)

        await recorder.dispatch()

        assert len(_transfer_rows(attestations)) == 1


class TestComputeTransferBalancedContradictionsSkipsTransfer:
    """Near-balanced contradicted herds (fused magnitude ≪ threshold) write no row.

    Engineers two contradicted hypotheses with herd positions that nearly
    cancel (``c_herd_a = +0.30``, ``c_herd_b = -0.300001``). The ECBF magnitude
    sits many orders above ``Opinion.EPSILON`` but below the epistemic
    threshold ``1e-3``; the transfer attestation must be skipped.

    Without the threshold, this would land a near-zero ``c_herd`` row on the
    novel that the math would then propagate through subsequent fusions —
    spurious epistemic content from algebraic noise.
    """

    async def test_compute_transfer_balanced_contradictions_skips_transfer(self) -> None:
        c_herd_a = 0.30
        c_herd_b = -0.300001
        h_a = "550e8400-e29b-41d4-a716-446655440101"
        h_b = "550e8400-e29b-41d4-a716-446655440102"

        attestation_map: dict[str, list[AttestationRecord]] = {
            h_a: [
                AttestationRecord.model_construct(
                    id="660e8400-e29b-41d4-a716-446655440101",
                    hypothesis_id=h_a,
                    oracle_id="oracle-other-a",
                    correlation_id="corr-prior-a",
                    timestamp=_T_NOW,
                    t_oracle=0.5,
                    c_oracle_raw=c_herd_a,
                    c_oracle_discounted=c_herd_a,
                    c_herd=c_herd_a,
                    n_oracle_prior=0,
                )
            ],
            h_b: [
                AttestationRecord.model_construct(
                    id="660e8400-e29b-41d4-a716-446655440102",
                    hypothesis_id=h_b,
                    oracle_id="oracle-other-b",
                    correlation_id="corr-prior-b",
                    timestamp=_T_NOW,
                    t_oracle=0.5,
                    c_oracle_raw=c_herd_b,
                    c_oracle_discounted=c_herd_b,
                    c_herd=c_herd_b,
                    n_oracle_prior=0,
                )
            ],
        }

        # c_herd_a and c_herd_b are engineered to fuse into a magnitude that
        # sits well above ``Opinion.EPSILON`` (math-core noise floor) but well
        # below ``_DEFAULT_THRESHOLD`` (epistemic-significance floor). The test
        # spec is the no-row behaviour; the band is a property of the inputs
        # the test author engineers, not a runtime invariant to check.

        hypotheses = _StubHypotheses()
        attestations = _StubAttestations()
        recorder = Recorder(
            hypotheses=hypotheses,
            attestations=attestations,
            math=MathService(c_half_life=86400.0, maturity_k=1.0, t_half_life=86400.0),
            reasoned=_archivist_output(
                Resolution(contributes=_NOVEL_CONTENT, contradicts=[h_a, h_b])
            ),
            attestation_map=attestation_map,
            novel_embeddings={_NOVEL_CONTENT: [0.1, 0.2, 0.3]},
            oracle_id="oracle-1",
            correlation_id=_CORRELATION_ID,
            confidence=0.7,
            t_oracle=0.5,
            t_now=_T_NOW,
            settings=_settings_with_threshold(),
        )

        await recorder.dispatch()

        assert _transfer_rows(attestations) == []


class TestRecorderOracleIdFlow:
    """The Recorder writes ``oracle_id`` to both the ledger and telemetry events.

    No fingerprint indirection: operators who need ``oracle_id`` redacted from
    telemetry configure their OTel collector's ``attributes`` processor at the
    export boundary. The synthetic ``_transfer`` identity survives unchanged.
    """

    async def test_recorder_attestation_event_uses_oracle_id(self) -> None:
        recorder, _ = _make_recorder(
            c_herd_of_contradicted=0.0,
            oracle_id=_ORACLE_ID,
            resolution=Resolution(corroborates="550e8400-e29b-41d4-a716-446655440000"),
        )

        with structlog.testing.capture_logs() as cap:
            await recorder.dispatch()

        attestation_events = [e for e in cap if e.get("event") == "recorder.attestation"]
        assert attestation_events, "expected at least one recorder.attestation event"
        for event in attestation_events:
            assert event["oracle_id"] == _ORACLE_ID

    async def test_recorder_attestation_keeps_transfer_oracle_synthetic_id(self) -> None:
        recorder, _ = _make_recorder(c_herd_of_contradicted=0.1, oracle_id=_ORACLE_ID)

        with structlog.testing.capture_logs() as cap:
            await recorder.dispatch()

        attestation_events = [e for e in cap if e.get("event") == "recorder.attestation"]
        oracle_ids = {e["oracle_id"] for e in attestation_events}
        # The transfer event carries the literal ``_transfer`` synthetic id.
        assert TRANSFER_ORACLE in oracle_ids
        # The oracle attestation event uses the supplied oracle_id directly.
        assert _ORACLE_ID in oracle_ids

    async def test_recorder_writes_oracle_id_to_ledger(self) -> None:
        recorder, attestations = _make_recorder(
            c_herd_of_contradicted=0.1,
            oracle_id=_ORACLE_ID,
        )

        await recorder.dispatch()

        # The ledger stores the oracle_id; transfer rows keep the synthetic id.
        non_synthetic = [a for a in attestations.appended if a.oracle_id != TRANSFER_ORACLE]
        assert non_synthetic, "expected at least one oracle attestation"
        for a in non_synthetic:
            assert a.oracle_id == _ORACLE_ID


class TestRecorderRejectsTransferOracleSynthetic:
    """The ``_transfer`` synthetic is reserved for ``_compute_transfer``.

    Accepting it as the principal ``oracle_id`` would write full-credibility
    oracle attestations under the transfer synthetic, bypassing trust
    discounting. The adapter refuses IdP-claimed ``_*`` namespace values; this
    is the corresponding domain-layer enforcement so the invariant survives a
    future caller path that bypasses the adapter (a direct test, an internal
    job, an as-yet-unwritten admin tool).
    """

    def test_recorder_constructor_rejects_transfer_oracle(self) -> None:
        with pytest.raises(ValueError, match=r"reserved for _compute_transfer"):
            _make_recorder(c_herd_of_contradicted=0.0, oracle_id=TRANSFER_ORACLE)


class TestRecorderIsDictBacked:
    """Recorder is constructed once per write transaction — __slots__ is premature."""

    def test_recorder_class_has_no_slots(self) -> None:
        assert not hasattr(Recorder, "__slots__")

    def test_recorder_instance_has_dict(self) -> None:
        recorder, _ = _make_recorder(c_herd_of_contradicted=0.0)
        assert hasattr(recorder, "__dict__")


def _attestation_for_oracle(*, hypothesis_id: str, oracle_id: str) -> AttestationRecord:
    """A storage-valid AttestationRecord whose oracle_id is the only thing that matters."""
    return AttestationRecord.model_construct(
        id="660e8400-e29b-41d4-a716-446655440000",
        hypothesis_id=hypothesis_id,
        oracle_id=oracle_id,
        correlation_id="corr-prior",
        timestamp=_T_NOW,
        t_oracle=0.5,
        c_oracle_raw=0.0,
        c_oracle_discounted=0.0,
        c_herd=0.0,
        n_oracle_prior=0,
    )


class TestRecorderPassesDistinctOracleCountToAppend:
    """The Recorder hands ``n_oracle_prior = |distinct oracles| - {self}`` to ``append()``.

    The trust scan reads this column verbatim — there is no longer a SQL
    derivation to fall back on. Round-trip tests verify the column survives
    storage; this test verifies the Recorder writes the right value in the
    first place. Together they pin both halves of the contract.
    """

    async def test_attest_existing_excludes_self_and_deduplicates_oracles(self) -> None:
        existing_id = "550e8400-e29b-41d4-a716-446655440099"
        # Four prior rows on the existing hypothesis: oracle-a twice (counts
        # once), oracle-b once, oracle-1 once. oracle-1 is the current oracle
        # and must be excluded from the count.
        existing = [
            _attestation_for_oracle(hypothesis_id=existing_id, oracle_id="oracle-a"),
            _attestation_for_oracle(hypothesis_id=existing_id, oracle_id="oracle-a"),
            _attestation_for_oracle(hypothesis_id=existing_id, oracle_id="oracle-b"),
            _attestation_for_oracle(hypothesis_id=existing_id, oracle_id="oracle-1"),
        ]

        hypotheses = _StubHypotheses()
        attestations = _StubAttestations()
        math_service = MathService(c_half_life=86400.0, maturity_k=1.0, t_half_life=86400.0)
        recorder = Recorder(
            hypotheses=hypotheses,
            attestations=attestations,
            math=math_service,
            reasoned=_archivist_output(Resolution(corroborates=existing_id)),
            attestation_map={existing_id: existing},
            novel_embeddings={},
            oracle_id="oracle-1",
            correlation_id=_CORRELATION_ID,
            confidence=0.7,
            t_oracle=0.5,
            t_now=_T_NOW,
            settings=_settings_with_threshold(),
        )

        await recorder.dispatch()

        # Distinct oracles among existing rows: {oracle-a, oracle-b, oracle-1}.
        # Excluding current (oracle-1): {oracle-a, oracle-b} → 2.
        assert len(attestations.appended) == 1
        assert attestations.appended[0].n_oracle_prior == 2

    async def test_attest_existing_returns_zero_when_no_prior_attestations(self) -> None:
        """First attester on a hypothesis: n_oracle_prior = 0, regardless of self-exclusion."""
        existing_id = "550e8400-e29b-41d4-a716-446655440098"

        hypotheses = _StubHypotheses()
        attestations = _StubAttestations()
        math_service = MathService(c_half_life=86400.0, maturity_k=1.0, t_half_life=86400.0)
        recorder = Recorder(
            hypotheses=hypotheses,
            attestations=attestations,
            math=math_service,
            reasoned=_archivist_output(Resolution(corroborates=existing_id)),
            attestation_map={existing_id: []},
            novel_embeddings={},
            oracle_id="oracle-1",
            correlation_id=_CORRELATION_ID,
            confidence=0.7,
            t_oracle=0.5,
            t_now=_T_NOW,
            settings=_settings_with_threshold(),
        )

        await recorder.dispatch()

        assert len(attestations.appended) == 1
        assert attestations.appended[0].n_oracle_prior == 0
