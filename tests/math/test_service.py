"""Tests for the math service: orchestrator-facing API.

The math service wraps the internal math primitives (Opinion, decay, fusion,
trust discounting, maturity) and exposes them through scalar confidences and
timestamps. Opinion never crosses this boundary.
"""

import math
import os
from pathlib import Path
from unittest.mock import patch

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st
from pydantic import ValidationError

from lore.config import load_settings
from lore.domain import AttestationComputed, EvidenceInput, TrustSignal
from lore.math.opinion import EPSILON
from lore.math.service import MathService, build_math
from tests.math.conftest import PROP_TOL
from tests.repositories.conftest import NO_DECAY_TRUST_HL as _NO_DECAY_HL

# Fixture whose epistemics all differ from the shipped defaults, so build_math
# wiring assertions can tell a threaded setting from a hardcoded default.
_TRUST_TOML = Path(__file__).parent.parent / "fixtures" / "lore_trust.toml"
_BASE_ENV = {"DATABASE_URL": "sqlite:///test.db"}

# --- Strategies ---

confidence_strategy = st.floats(min_value=-1.0, max_value=1.0)
# Discounted values are always non-dogmatic: P_effective < 1 for K >= 1.
# Bound of 0.99 models realistic pipeline output: even with high trust
# (t_oracle ≈ 0.9) and mature hypotheses (M ≈ 0.9), P_effective ≈ 0.81.
discounted_strategy = st.floats(min_value=-0.99, max_value=0.99)
half_life_strategy = st.floats(min_value=0.5, max_value=1000.0)

# Half-life values for hand-calculated tests where exact values matter.
_FAST_DECAY_HL = math.log(2) / 0.1  # ~7s half-life, λ ≈ 0.1
_SLOW_DECAY_HL = math.log(2) / 0.01  # ~69s half-life, λ ≈ 0.01


def _make_existing(c_oracle_discounted: float, timestamp: int) -> EvidenceInput:
    """Build a minimal EvidenceInput for use as an existing ledger entry."""
    return EvidenceInput(c_oracle_discounted=c_oracle_discounted, timestamp=timestamp)


def _evidence(*, t_now: int, **refs: float) -> dict[str, list[EvidenceInput]]:
    """Herd evidence whose recomputed reference at ``t_now`` equals each given
    scalar: one other-oracle row at ``t_now`` fuses to itself (lossless
    scalar-opinion bijection, zero decay). Keys are hypothesis ids."""
    return {hid: [EvidenceInput(c_oracle_discounted=c, timestamp=t_now)] for hid, c in refs.items()}


# --- prepare_attestation: hand-calculated ---
class TestPrepareAttestationHandCalculated:
    def test_first_attestation_cold_start(self) -> None:
        """No existing attestations, cold-start trust (t_oracle=0.5, K=1).

        n_oracle_prior=0 → N_O=1, M=1/2=0.5
        P_effective = 0.5 * 0.5 = 0.25
        c_oracle_discounted = 0.25 * 0.7 = 0.175
        discounted opinion = (0.175, 0, 0.825)
        herd = fuse([(0.175, 0, 0.825)]) = (0.175, 0, 0.825)
        c_herd = 0.175
        """
        svc = MathService(c_half_life=_FAST_DECAY_HL, maturity_k=1, t_half_life=_NO_DECAY_HL)
        result = svc.prepare_attestation(
            confidence=0.7,
            existing=[],
            t_now=1000,
            t_oracle=0.5,
            n_oracle_prior=0,
        )

        assert isinstance(result, AttestationComputed)
        assert abs(result.t_oracle - 0.5) < EPSILON
        assert abs(result.c_oracle_raw - 0.7) < EPSILON
        assert abs(result.c_oracle_discounted - 0.175) < EPSILON
        assert abs(result.c_herd - 0.175) < EPSILON

    def test_full_trust_transparent_discount(self) -> None:
        """Full trust (t_oracle=1.0) with K=0 means P_effective=1.0.

        M = 1/1 = 1.0 (K=0), P_effective = 1.0 * 1.0 = 1.0
        c_oracle_discounted = 1.0 * 0.8 = 0.8
        Existing: c_discounted=0.6 at t=900. No decay.
        ACBF[(0.6,0,0.4), (0.8,0,0.2)]:
          κ = 0.4 + 0.2 - 0.08 = 0.52
          b = (0.6·0.2 + 0.8·0.4) / 0.52 = 0.44/0.52 = 11/13
          d = 0, u = 0.08/0.52 = 2/13
        Maximize: already min(b,d)=0. c_herd = 11/13.
        """
        existing = [_make_existing(0.6, timestamp=900)]
        svc = MathService(c_half_life=_NO_DECAY_HL, maturity_k=0, t_half_life=_NO_DECAY_HL)
        result = svc.prepare_attestation(
            confidence=0.8,
            existing=existing,
            t_now=1000,
            t_oracle=1.0,
            n_oracle_prior=1,
        )

        assert abs(result.c_oracle_raw - 0.8) < EPSILON
        assert abs(result.c_oracle_discounted - 0.8) < EPSILON
        assert abs(result.c_herd - 11.0 / 13.0) < EPSILON

    def test_k_zero_dogmatic_input_produces_dogmatic_herd(self) -> None:
        """K=0 is an explicit deployer opt-in to transparent maturity.

        With K=0, M=1.0 always. With perfect trust t_oracle=1.0,
        P_effective=1.0 and discount is transparent. A dogmatic input
        (c=1.0) passes through unmodified → dogmatic herd.

        This is the known safety boundary: K >= 1 (default) prevents this
        by ensuring P_effective < 1.
        """
        svc = MathService(c_half_life=_NO_DECAY_HL, maturity_k=0, t_half_life=_NO_DECAY_HL)
        result = svc.prepare_attestation(
            confidence=1.0,
            existing=[],
            t_now=1000,
            t_oracle=1.0,
            n_oracle_prior=0,
        )

        assert abs(result.c_oracle_raw - 1.0) < EPSILON
        assert abs(result.c_oracle_discounted - 1.0) < EPSILON
        assert abs(result.c_herd - 1.0) < EPSILON

    def test_contradiction_cancels(self) -> None:
        """Equal and opposite discounted opinions cancel: herd returns to vacuous.

        K=0, t_oracle=1.0 → P_effective=1.0, discount transparent.
        Existing: c_discounted=0.7. New: c=-0.7 → c_discounted=-0.7.
        ACBF[(0.7,0,0.3), (0,0.7,0.3)]:
          κ = 0.51, b = 7/17, d = 7/17, u = 3/17
        Maximize: P=0.5, ü=1.0 → vacuous. c_herd = 0.0.
        """
        existing = [_make_existing(0.7, timestamp=1000)]
        svc = MathService(c_half_life=_NO_DECAY_HL, maturity_k=0, t_half_life=_NO_DECAY_HL)
        result = svc.prepare_attestation(
            confidence=-0.7,
            existing=existing,
            t_now=1000,
            t_oracle=1.0,
            n_oracle_prior=1,
        )

        assert abs(result.c_oracle_discounted - (-0.7)) < EPSILON
        assert abs(result.c_herd - 0.0) < EPSILON

    def test_vacuous_input(self) -> None:
        """Oracle expresses ignorance (c=0). Discount preserves vacuous."""
        svc = MathService(c_half_life=_FAST_DECAY_HL, maturity_k=1, t_half_life=_NO_DECAY_HL)
        result = svc.prepare_attestation(
            confidence=0.0,
            existing=[],
            t_now=1000,
            t_oracle=0.5,
            n_oracle_prior=0,
        )

        assert abs(result.c_oracle_discounted - 0.0) < EPSILON
        assert abs(result.c_herd - 0.0) < EPSILON

    def test_cold_start_default_p_effective(self) -> None:
        """Cold start (t_oracle=0.5, K=1, n_oracle_prior=0) → P_eff=0.25."""
        svc = MathService(c_half_life=_NO_DECAY_HL, maturity_k=1, t_half_life=_NO_DECAY_HL)
        result = svc.prepare_attestation(
            confidence=0.8,
            existing=[],
            t_now=1000,
            t_oracle=0.5,
            n_oracle_prior=0,
        )

        # P_effective = 0.5 * 0.5 = 0.25, c_discounted = 0.25 * 0.8 = 0.2
        assert abs(result.c_oracle_discounted - 0.2) < EPSILON
        assert abs(result.c_herd - 0.2) < EPSILON

    def test_zero_trust_discounts_to_vacuous(self) -> None:
        """t_oracle=0.0 is a valid boundary value: accepted, weightless.

        The trust scan can return exactly 0.0 (all-misaligned history), so
        prepare_attestation must admit it. P_effective = M * 0.0 = 0.0 and
        the discount collapses the opinion to vacuous: no epistemic weight.
        """
        svc = MathService(c_half_life=_FAST_DECAY_HL, t_half_life=_NO_DECAY_HL)
        result = svc.prepare_attestation(
            confidence=0.8,
            existing=[],
            t_now=1000,
            t_oracle=0.0,
            n_oracle_prior=0,
        )

        assert abs(result.c_oracle_discounted - 0.0) < EPSILON
        assert abs(result.c_herd - 0.0) < EPSILON


# --- prepare_attestation: property-based ---
class TestPrepareAttestationPropertyBased:
    @given(c=confidence_strategy, half_life=half_life_strategy)
    def test_c_herd_in_range(self, c: float, half_life: float) -> None:
        """Herd consensus is strictly non-dogmatic: ECBF with discounted inputs."""
        svc = MathService(c_half_life=half_life, t_half_life=_NO_DECAY_HL)
        result = svc.prepare_attestation(
            confidence=c,
            existing=[],
            t_now=1000,
            t_oracle=0.5,
            n_oracle_prior=0,
        )
        assert -1.0 < result.c_herd < 1.0

    @given(c=confidence_strategy, half_life=half_life_strategy)
    def test_discount_never_amplifies(self, c: float, half_life: float) -> None:
        """Discounted magnitude never exceeds raw magnitude."""
        svc = MathService(c_half_life=half_life, t_half_life=_NO_DECAY_HL)
        result = svc.prepare_attestation(
            confidence=c,
            existing=[],
            t_now=1000,
            t_oracle=0.5,
            n_oracle_prior=0,
        )
        assert abs(result.c_oracle_discounted) <= abs(result.c_oracle_raw) + PROP_TOL

    @given(c=confidence_strategy, half_life=half_life_strategy)
    def test_discount_preserves_sign(self, c: float, half_life: float) -> None:
        """Discounted confidence has the same sign as raw confidence."""
        assume(abs(c) > 0.01)
        svc = MathService(c_half_life=half_life, t_half_life=_NO_DECAY_HL)
        result = svc.prepare_attestation(
            confidence=c,
            existing=[],
            t_now=1000,
            t_oracle=0.5,
            n_oracle_prior=0,
        )
        if result.c_oracle_discounted != 0.0:
            assert (result.c_oracle_discounted > 0) == (result.c_oracle_raw > 0)

    @given(
        c=confidence_strategy,
        half_life=half_life_strategy,
        existing_cs=st.lists(discounted_strategy, min_size=1, max_size=5),
    )
    def test_c_herd_in_range_with_existing(
        self, c: float, half_life: float, existing_cs: list[float]
    ) -> None:
        """Herd consensus stays strictly non-dogmatic with prior attestations."""
        existing = [_make_existing(ec, timestamp=500 + i) for i, ec in enumerate(existing_cs)]
        svc = MathService(c_half_life=half_life, t_half_life=_NO_DECAY_HL)
        result = svc.prepare_attestation(
            confidence=c,
            existing=existing,
            t_now=1000,
            t_oracle=0.5,
            n_oracle_prior=len(existing_cs),
        )
        assert -1.0 < result.c_herd < 1.0

    @given(c=confidence_strategy, half_life=half_life_strategy)
    def test_preserves_oracle_trust_for_ledger(self, c: float, half_life: float) -> None:
        """Oracle trust is preserved verbatim: the ledger records what the system saw."""
        svc = MathService(c_half_life=half_life, t_half_life=_NO_DECAY_HL)
        result = svc.prepare_attestation(
            confidence=c,
            existing=[],
            t_now=1000,
            t_oracle=0.75,
            n_oracle_prior=0,
        )
        assert abs(result.t_oracle - 0.75) < EPSILON

    @given(c=confidence_strategy, half_life=half_life_strategy)
    def test_preserves_raw_confidence_for_audit(self, c: float, half_life: float) -> None:
        """Raw confidence is preserved verbatim: the ledger is the audit trail."""
        svc = MathService(c_half_life=half_life, t_half_life=_NO_DECAY_HL)
        result = svc.prepare_attestation(
            confidence=c,
            existing=[],
            t_now=1000,
            t_oracle=0.5,
            n_oracle_prior=0,
        )
        assert abs(result.c_oracle_raw - c) < EPSILON


# --- compute_confidence ---
class TestComputeConfidenceHandCalculated:
    def test_empty_returns_zero(self) -> None:
        """No attestations → vacuous → c=0.0."""
        svc = MathService(c_half_life=_FAST_DECAY_HL, t_half_life=_NO_DECAY_HL)
        result = svc.compute_confidence(attestations=[], t_now=1000)
        assert abs(result) < EPSILON

    def test_single_undecayed(self) -> None:
        """Single attestation at t_now: no decay, returns oracle's scalar."""
        att = _make_existing(0.7, timestamp=1000)
        svc = MathService(c_half_life=_FAST_DECAY_HL, t_half_life=_NO_DECAY_HL)
        result = svc.compute_confidence(attestations=[att], t_now=1000)
        assert abs(result - 0.7) < EPSILON

    def test_decay_reduces_magnitude(self) -> None:
        """Older attestation decays toward zero."""
        att = _make_existing(0.8, timestamp=0)
        svc = MathService(c_half_life=_SLOW_DECAY_HL, t_half_life=_NO_DECAY_HL)
        fresh = svc.compute_confidence(attestations=[att], t_now=0)
        stale = svc.compute_confidence(attestations=[att], t_now=10)
        assert abs(fresh - 0.8) < EPSILON
        assert 0.0 < stale < fresh

    def test_future_attestation_treated_as_undecayed(self) -> None:
        """An attestation with timestamp after t_now is clamped to zero elapsed time.

        Clock skew or replayed attestations must not amplify belief: the decay
        function would invert (e^(+lambda*t)) without the clamp.
        """
        att = _make_existing(0.8, timestamp=2000)
        svc = MathService(c_half_life=_SLOW_DECAY_HL, t_half_life=_NO_DECAY_HL)
        result = svc.compute_confidence(attestations=[att], t_now=1000)
        undecayed = svc.compute_confidence(attestations=[att], t_now=2000)
        assert abs(result - undecayed) < EPSILON


class TestComputeConfidencePropertyBased:
    """Mathematical guarantees for compute_confidence.

    These tests use discounted confidence values (non-dogmatic by construction:
    trust discounting ensures |c_oracle_discounted| < 1 for K >= 1).
    """

    @given(c=discounted_strategy, half_life=half_life_strategy)
    def test_result_in_range(self, c: float, half_life: float) -> None:
        """Output is strictly non-dogmatic: ECBF with non-dogmatic inputs."""
        att = _make_existing(c, timestamp=1000)
        svc = MathService(c_half_life=half_life, t_half_life=_NO_DECAY_HL)
        result = svc.compute_confidence(attestations=[att], t_now=1000)
        assert -1.0 < result < 1.0

    @given(
        c=discounted_strategy,
        half_life=st.floats(min_value=0.5, max_value=100.0),
        dt=st.integers(min_value=1, max_value=10000),
    )
    def test_decay_monotonically_reduces_magnitude(
        self, c: float, half_life: float, dt: int
    ) -> None:
        """Magnitude of confidence decreases with time."""
        assume(abs(c) > 0.01)
        att = _make_existing(c, timestamp=0)
        svc = MathService(c_half_life=half_life, t_half_life=_NO_DECAY_HL)
        fresh = svc.compute_confidence(attestations=[att], t_now=0)
        stale = svc.compute_confidence(attestations=[att], t_now=dt)
        assert abs(stale) <= abs(fresh) + PROP_TOL


# --- MathService validation ---
def test_prepare_attestation_rejects_negative_t_oracle() -> None:
    """Negative trust is not a valid alignment probability."""
    svc = MathService(c_half_life=_FAST_DECAY_HL, t_half_life=_NO_DECAY_HL)
    with pytest.raises(ValueError, match="t_oracle must be in"):
        svc.prepare_attestation(
            confidence=0.5, existing=[], t_now=1000, t_oracle=-0.1, n_oracle_prior=0
        )


def test_prepare_attestation_rejects_t_oracle_above_one() -> None:
    """Trust above 1.0 is not a valid alignment probability."""
    svc = MathService(c_half_life=_FAST_DECAY_HL, t_half_life=_NO_DECAY_HL)
    with pytest.raises(ValueError, match="t_oracle must be in"):
        svc.prepare_attestation(
            confidence=0.5, existing=[], t_now=1000, t_oracle=1.01, n_oracle_prior=0
        )


def test_prepare_attestation_rejects_negative_n_oracle_prior() -> None:
    """Negative oracle count is nonsensical."""
    svc = MathService(c_half_life=_FAST_DECAY_HL, t_half_life=_NO_DECAY_HL)
    with pytest.raises(ValueError, match="n_oracle_prior must be non-negative"):
        svc.prepare_attestation(
            confidence=0.5, existing=[], t_now=1000, t_oracle=0.5, n_oracle_prior=-1
        )


# --- TrustSignal construction ---
def test_oracle_alignment_snapshot_requires_n_oracle_prior() -> None:
    """n_oracle_prior is required for adaptive w computation."""
    with pytest.raises(ValidationError):
        TrustSignal(  # pyright: ignore[reportCallIssue]
            hypothesis_id="h1", c_oracle_raw=0.5, timestamp=100, c_herd_prior=0.0
        )


def test_oracle_alignment_snapshot_rejects_negative_n_oracle_prior() -> None:
    """Distinct oracle count cannot be negative."""
    with pytest.raises(ValidationError):
        TrustSignal(
            hypothesis_id="h1",
            c_oracle_raw=0.5,
            timestamp=100,
            c_herd_prior=0.0,
            n_oracle_prior=-1,
        )


# --- compute_oracle_trust (method) ---
class TestComputeOracleTrust:
    """Tests for oracle trust computation via MathService.

    Formula (see docs/logic.md, Oracle Trust section):
      witness rule     : rows whose hypothesis has no others-only evidence
                         leave the scan (neither numerator nor denominator)
      ref_i            = compute_confidence(herd_evidence[hypothesis_id], t_now)
      M_write_i        = N_O / (N_O + K), N_O = n_oracle_prior + 1
      align_write_i    = 1 - 0.5 * |c_oracle_raw - c_herd_prior|
      align_read_i     = 1 - 0.5 * |c_oracle_raw - ref|
      align_i          = M_write · align_write + (1 - M_write) · align_read
      info_i           = 1 - |c_herd_prior|
      conviction_i     = |c_oracle_raw|
      signal_i         = conviction · info
      effective_align  = signal · align + (1 - signal) · 0.5  (Def. 14.6)
      weight_i         = exp(-λ_trust · Δt)
      t_oracle         = Σ(effective_align · conviction · weight)
                       / Σ(conviction · weight)

    TrustSignal carries no herd-now snapshot; the read-time reference
    always comes from ``herd_evidence``, recomputed at t_now.
    """

    def test_empty_rows_returns_base_rate(self) -> None:
        """Cold start: no history → base rate trust (0.5)."""
        svc = MathService(c_half_life=100.0, t_half_life=_NO_DECAY_HL)
        result = svc.compute_oracle_trust(rows=[], herd_evidence={}, t_now=1000)
        assert abs(result - 0.5) < EPSILON

    def test_fresh_herd_perfect_read_alignment(self) -> None:
        """First attester on a vacuous herd, later vindicated by read-time.

        n_oracle_prior=0, K=1 → M_write = 0.5
        align_write = 1 - 0.5·0.8 = 0.6
        align_read  = 1 - 0.5·0.0 = 1.0
        align       = 0.5·0.6 + 0.5·1.0 = 0.8
        info        = 1 - 0 = 1.0
        signal      = 0.8·1.0 = 0.8
        effective   = 0.8·0.8 + 0.2·0.5 = 0.74
        t_oracle    = 0.74
        """
        svc = MathService(c_half_life=100.0, t_half_life=_NO_DECAY_HL)
        rows = [
            TrustSignal(
                hypothesis_id="h1",
                c_oracle_raw=0.8,
                timestamp=1000,
                c_herd_prior=0.0,
                n_oracle_prior=0,
            )
        ]
        result = svc.compute_oracle_trust(
            rows=rows, herd_evidence=_evidence(t_now=1000, h1=0.8), t_now=1000
        )
        assert abs(result - 0.74) < EPSILON

    def test_adaptive_w_fresh_hypothesis_blends_equally(self) -> None:
        """Fresh hypothesis (n_oracle_prior=0, K=1) → M_write=0.5.

        Write-time and read-time signals get equal weight.
          c_oracle_raw=0.5, c_herd_prior=0.5
        align_write = 1 - 0 = 1.0
        align_read  = 1 - 0.5·1.0 = 0.5
        align       = 0.5·1.0 + 0.5·0.5 = 0.75
        info        = 1 - 0.5 = 0.5
        signal      = 0.5·0.5 = 0.25
        effective   = 0.25·0.75 + 0.75·0.5 = 0.5625
        t_oracle    = 0.5625
        """
        svc = MathService(c_half_life=100.0, t_half_life=_NO_DECAY_HL)
        rows = [
            TrustSignal(
                hypothesis_id="h1",
                c_oracle_raw=0.5,
                timestamp=1000,
                c_herd_prior=0.5,
                n_oracle_prior=0,
            )
        ]
        result = svc.compute_oracle_trust(
            rows=rows, herd_evidence=_evidence(t_now=1000, h1=-0.5), t_now=1000
        )
        assert abs(result - 0.5625) < EPSILON

    def test_adaptive_w_mature_hypothesis_anchors_on_write_time(self) -> None:
        """Mature hypothesis (n_oracle_prior=9, K=1) → M_write=10/11.

        Same alignments as the fresh-hypothesis test, but now write-time
        (align=1.0) dominates instead of read-time (align=0.5).
          align = (10/11)·1.0 + (1/11)·0.5 = 21/22
          info  = 0.5
          signal = 0.5·0.5 = 0.25
          effective = 0.25·(21/22) + 0.75·0.5 = 21/88 + 33/88 = 54/88 = 27/44
        """
        svc = MathService(c_half_life=100.0, t_half_life=_NO_DECAY_HL)
        rows = [
            TrustSignal(
                hypothesis_id="h1",
                c_oracle_raw=0.5,
                timestamp=1000,
                c_herd_prior=0.5,
                n_oracle_prior=9,
            )
        ]
        result = svc.compute_oracle_trust(
            rows=rows, herd_evidence=_evidence(t_now=1000, h1=-0.5), t_now=1000
        )
        assert abs(result - 27.0 / 44.0) < EPSILON

    def test_info_discount_settled_herd_earns_negligible_credit(self) -> None:
        """Perfect agreement with a dogmatic herd → effective align ≈ 0.5.

        c_herd_prior=0.9 → info = 0.1, signal = 0.9·0.1 = 0.09. Even
        align=1.0 collapses toward the base rate:
        effective = 0.09·1.0 + 0.91·0.5 = 0.545.
        """
        svc = MathService(c_half_life=100.0, t_half_life=_NO_DECAY_HL)
        rows = [
            TrustSignal(
                hypothesis_id="h1",
                c_oracle_raw=0.9,
                timestamp=1000,
                c_herd_prior=0.9,
                n_oracle_prior=5,
            )
        ]
        result = svc.compute_oracle_trust(
            rows=rows, herd_evidence=_evidence(t_now=1000, h1=0.9), t_now=1000
        )
        assert abs(result - 0.545) < EPSILON

    def test_fresh_herd_calibration_reduces_to_conviction(self) -> None:
        """Fresh herd (c_herd_prior=0) → info=1 → signal = conviction alone.

        n_oracle_prior=0, K=1 → M_write=0.5
        align_write = 1 - 0.5·0.7 = 0.65
        align_read  = 1 - 0.5·0.2 = 0.9
        align       = 0.5·0.65 + 0.5·0.9 = 0.775
        info        = 1.0, signal = 0.7
        effective   = 0.7·0.775 + 0.3·0.5 = 0.6925
        """
        svc = MathService(c_half_life=100.0, t_half_life=_NO_DECAY_HL)
        rows = [
            TrustSignal(
                hypothesis_id="h1",
                c_oracle_raw=0.7,
                timestamp=1000,
                c_herd_prior=0.0,
                n_oracle_prior=0,
            )
        ]
        result = svc.compute_oracle_trust(
            rows=rows, herd_evidence=_evidence(t_now=1000, h1=0.5), t_now=1000
        )
        assert abs(result - 0.6925) < EPSILON

    def test_conviction_calibrates_alignment_signal(self) -> None:
        """A perfectly aligned row moves trust only as far as its conviction.

        K=inf is the pure read-time limit: M_write = 1/(1+inf) = 0, so
        align = align_read exactly. (At any finite K the write leg mixes
        in and align=1 with info=1 is unreachable for nonzero conviction.)
        It is an analytical device, not a configuration: ``EpistemicsConfig``
        rejects a non-finite ``maturity_k`` because inf drives M to 0 for
        every hypothesis and every write would land vacuous. ``MathService``
        is constructed directly here to reach the limit the algebra has but
        no deployment may select; the isolation is deliberate, and config
        should not learn to accept inf on this test's account.
        With the reference equal to c_oracle_raw the row is perfectly
        aligned and the herd was vacuous at write time:

          align     = align_read = 1 - 0.5·|0.2 - 0.2| = 1.0
          info      = 1 - 0 = 1.0
          signal    = 0.2·1.0 = 0.2
          effective = 0.2·1.0 + 0.8·0.5 = 0.6
          t_oracle  = 0.6 exactly

        Under info-only calibration this row would earn t = 1.0: the
        low-conviction scattershot vector. Conviction caps it at 0.6.
        """
        svc = MathService(
            c_half_life=float("inf"), t_half_life=float("inf"), maturity_k=float("inf")
        )
        rows = [
            TrustSignal(
                hypothesis_id="h1",
                c_oracle_raw=0.2,
                timestamp=1000,
                c_herd_prior=0.0,
                n_oracle_prior=0,
            )
        ]
        result = svc.compute_oracle_trust(
            rows=rows, herd_evidence=_evidence(t_now=1000, h1=0.2), t_now=1000
        )
        assert abs(result - 0.6) < EPSILON

    def test_committed_wrongness_on_uncertain_herd_scores_zero(self) -> None:
        """The full-penalty extreme of the informative-commitment table:
        signal = 1, align = 0 → effective_align = 0.

        This pins the pure function's documented contract, not a state the
        pipeline can reach. The row is analytical twice over, and the two
        devices are the reason it needs both:

          - align = 0 demands a dogmatic herd opposite the oracle
            (|c - reference| = 2). Trust discounting keeps every stored
            c_herd strictly interior at every finite K, so the ledger has
            no such reference to offer.
          - signal = 1 demands info = 1, a vacuous prior. At any finite K
            the write leg then contributes M·0.5 > 0, so align = 0 and
            signal = 1 cannot hold on the same row. Only the K = inf limit
            (M_write = 0, pure read-time) admits both, the same analytical
            device test_conviction_calibrates_alignment_signal uses and for
            the same reason: EpistemicsConfig rejects a non-finite
            maturity_k.

          align     = align_read = 1 - 0.5·|1.0 - (-1.0)| = 0.0
          info      = 1 - 0 = 1.0
          signal    = 1.0·1.0 = 1.0
          effective = 1.0·0.0 + 0.0·0.5 = 0.0

        The counterpart to test_bandwagon_theorem_survives_calibration
        below: uninformative wrongness neutralises to 0.5, informative
        wrongness is punished to the floor.
        """
        svc = MathService(
            c_half_life=float("inf"), t_half_life=float("inf"), maturity_k=float("inf")
        )
        rows = [
            TrustSignal(
                hypothesis_id="h1",
                c_oracle_raw=1.0,
                timestamp=1000,
                c_herd_prior=0.0,
                n_oracle_prior=0,
            )
        ]
        result = svc.compute_oracle_trust(
            rows=rows, herd_evidence=_evidence(t_now=1000, h1=-1.0), t_now=1000
        )
        assert abs(result) < EPSILON

    def test_bandwagon_theorem_survives_calibration(self) -> None:
        """Full conviction against a dogmatic herd still earns exactly 0.5.

        info = 1 - |c_herd_prior| = 0, so signal = conviction·info = 0
        regardless of conviction: effective_align = 0.5 by algebra, no
        fallback involved. The conviction factor does not reopen the
        bandwagon vector.
        """
        svc = MathService(c_half_life=100.0, t_half_life=_NO_DECAY_HL)
        rows = [
            TrustSignal(
                hypothesis_id="h1",
                c_oracle_raw=1.0,
                timestamp=1000,
                c_herd_prior=1.0,
                n_oracle_prior=4,
            )
        ]
        result = svc.compute_oracle_trust(
            rows=rows, herd_evidence=_evidence(t_now=1000, h1=1.0), t_now=1000
        )
        assert abs(result - 0.5) < EPSILON

    def test_full_conviction_recovers_info_only_calibration(self) -> None:
        """At conviction = 1 the composite gate reduces to info alone.

        signal = 1·info = info, so the calibrated formula reproduces the
        old info-only weighting exactly.

          n_oracle_prior=0, K=1 → M_write = 0.5
          align_write = 1 - 0.5·|1.0 - 0.4| = 0.7
          align_read  = 1 - 0.5·|1.0 - 0.8| = 0.9
          align       = 0.5·0.7 + 0.5·0.9 = 0.8
          info        = 1 - 0.4 = 0.6
          info-only:  0.6·0.8 + 0.4·0.5 = 0.68
          calibrated: signal = 1·0.6 = 0.6 → 0.6·0.8 + 0.4·0.5 = 0.68
        """
        svc = MathService(c_half_life=100.0, t_half_life=_NO_DECAY_HL)
        rows = [
            TrustSignal(
                hypothesis_id="h1",
                c_oracle_raw=1.0,
                timestamp=1000,
                c_herd_prior=0.4,
                n_oracle_prior=0,
            )
        ]
        result = svc.compute_oracle_trust(
            rows=rows, herd_evidence=_evidence(t_now=1000, h1=0.8), t_now=1000
        )
        assert abs(result - 0.68) < EPSILON

    # --- Worked-example archetypes from docs/logic.md §Oracle Trust ---

    def test_compute_oracle_trust_prophet_archetype(self) -> None:
        """docs/logic.md Example 1: the Prophet earns 0.700.

        First attester on a fresh hypothesis (n_oracle_prior=0) with high
        conviction (c=0.8); the herd later converges to 0.6. M_write=0.5,
        align = 0.5·0.6 + 0.5·0.9 = 0.75, info=1, signal=0.8,
        effective = 0.8·0.75 + 0.2·0.5 = 0.700. Single row → t = 0.700.
        """
        svc = MathService(c_half_life=float("inf"), t_half_life=float("inf"), maturity_k=1.0)
        rows = [
            TrustSignal(
                hypothesis_id="h1",
                c_oracle_raw=0.8,
                timestamp=0,
                c_herd_prior=0.0,
                n_oracle_prior=0,
            )
        ]
        result = svc.compute_oracle_trust(
            rows=rows, herd_evidence=_evidence(t_now=0, h1=0.6), t_now=0
        )
        assert abs(result - 0.700) < 1e-3

    def test_compute_oracle_trust_contrarian_archetype(self) -> None:
        """docs/logic.md Example 3: the Contrarian earns ~0.493.

        Two rows attesting against the herd, one fresh, one moderate.
        Row 1: align = 0.5·0.6 + 0.5·0.45 = 0.525, info=1, signal=0.8,
          effective = 0.8·0.525 + 0.2·0.5 = 0.52; num 0.416, den 0.8.
        Row 2: align = 0.75·0.4 + 0.25·0.375 = 0.39375, info=0.5,
          signal = 0.7·0.5 = 0.35,
          effective = 0.35·0.39375 + 0.65·0.5 = 0.4628125;
          num 0.32396875, den 0.7.
        t = 0.73996875 / 1.5 = 0.4933125 ≈ 0.493.
        """
        svc = MathService(c_half_life=float("inf"), t_half_life=float("inf"), maturity_k=1.0)
        rows = [
            TrustSignal(
                hypothesis_id="h1",
                c_oracle_raw=-0.8,
                timestamp=0,
                c_herd_prior=0.0,
                n_oracle_prior=0,
            ),
            TrustSignal(
                hypothesis_id="h2",
                c_oracle_raw=-0.7,
                timestamp=0,
                c_herd_prior=0.50,
                n_oracle_prior=2,
            ),
        ]
        result = svc.compute_oracle_trust(
            rows=rows, herd_evidence=_evidence(t_now=0, h1=0.30, h2=0.55), t_now=0
        )
        assert abs(result - 0.493) < 1e-3

    def test_compute_oracle_trust_honest_conformist_archetype(self) -> None:
        """docs/logic.md Example 4: the Honest Conformist earns ~0.6457.

        Two mature-but-fluid hypotheses, near-perfect alignment, moderate
        conviction.
        Row 1: align = (5/6)·0.9 + (1/6)·0.975 = 0.9125, info=0.6,
          signal = 0.6·0.6 = 0.36,
          effective = 0.36·0.9125 + 0.64·0.5 = 0.6485; num 0.3891, den 0.6.
        Row 2: align = (10/11)·0.9 + (1/11)·0.975 = 9.975/11 ≈ 0.90682,
          info=0.7, signal = 0.5·0.7 = 0.35,
          effective = 0.35·(9.975/11) + 0.65·0.5 ≈ 0.64239;
          num ≈ 0.32119, den 0.5.
        t ≈ 0.71029 / 1.1 ≈ 0.6457.
        """
        svc = MathService(c_half_life=float("inf"), t_half_life=float("inf"), maturity_k=1.0)
        rows = [
            TrustSignal(
                hypothesis_id="h1",
                c_oracle_raw=0.60,
                timestamp=0,
                c_herd_prior=0.40,
                n_oracle_prior=4,
            ),
            TrustSignal(
                hypothesis_id="h2",
                c_oracle_raw=0.50,
                timestamp=0,
                c_herd_prior=0.30,
                n_oracle_prior=9,
            ),
        ]
        result = svc.compute_oracle_trust(
            rows=rows, herd_evidence=_evidence(t_now=0, h1=0.55, h2=0.45), t_now=0
        )
        assert abs(result - 0.6457) < 1e-4

    def test_compute_oracle_trust_at_k_zero_collapses_to_write_time_alignment(self) -> None:
        """K=0 makes M_write=1 for any n_oracle_prior → pure write-time signal.

        Prophet inputs but K=0: align = align_write = 1 − 0.5·|0.8−0.0|
        = 0.6. info=1, signal=0.8 →
        t_oracle = 0.8·0.6 + 0.2·0.5 = 0.58.
        """
        svc = MathService(c_half_life=float("inf"), t_half_life=float("inf"), maturity_k=0.0)
        rows = [
            TrustSignal(
                hypothesis_id="h1",
                c_oracle_raw=0.8,
                timestamp=0,
                c_herd_prior=0.0,
                n_oracle_prior=0,
            )
        ]
        result = svc.compute_oracle_trust(
            rows=rows, herd_evidence=_evidence(t_now=0, h1=0.6), t_now=0
        )
        assert abs(result - 0.58) < EPSILON

    def test_pure_bandwagoner_against_dogmatic_herd_returns_base_rate(self) -> None:
        """Dogmatic prior herd → info=0 → every row discounted to 0.5.

        With c_herd_prior=±1, info=0 and effective_align=0.5 regardless
        of raw alignment. The conviction-weighted average is exactly 0.5.
        """
        svc = MathService(c_half_life=100.0, t_half_life=_NO_DECAY_HL)
        rows = [
            TrustSignal(
                hypothesis_id="h1",
                c_oracle_raw=1.0,
                timestamp=800,
                c_herd_prior=1.0,
                n_oracle_prior=3,
            ),
            TrustSignal(
                hypothesis_id="h2",
                c_oracle_raw=-1.0,
                timestamp=1000,
                c_herd_prior=-1.0,
                n_oracle_prior=7,
            ),
        ]
        result = svc.compute_oracle_trust(
            rows=rows, herd_evidence=_evidence(t_now=1000, h1=1.0, h2=-1.0), t_now=1000
        )
        assert abs(result - 0.5) < EPSILON

    def test_prophet_outearns_conformist_outearns_bandwagoner(self) -> None:
        """The identity promise holds as an ordering, not three pinned constants.

        One service (infinite half-lives, K=1), three careers. The prophet
        and conformist reuse the docs/logic.md archetype rows; the
        bandwagoner is the deployment regime: agreement with settled but
        non-dogmatic herds (c_herd_prior=0.9 → info=0.1), where the
        composite gate signal = conviction·info caps agreement credit
        near base rate even at perfect alignment.

        Prophet (≈ 0.700): c=0.8 on a fresh hypothesis (info=1), herd
        later converges to 0.6. See the prophet archetype test.

        Conformist (≈ 0.6457): moderate conviction on mature-but-fluid
        herds (info=0.6, 0.7). See the honest-conformist archetype test.

        Bandwagoner (≈ 0.5408):
        Row 1: c=0.9, prior=0.9, n_oracle_prior=5, ref=0.9:
          align=1.0, info=0.1, signal=0.09,
          effective = 0.09·1.0 + 0.91·0.5 = 0.545; num 0.4905, den 0.9.
        Row 2: c=0.8, prior=0.9, n_oracle_prior=9, ref=0.9:
          align_write = align_read = 1 - 0.5·0.1 = 0.95, signal=0.08,
          effective = 0.08·0.95 + 0.92·0.5 = 0.536; num 0.4288, den 0.8.
        t = 0.9193 / 1.7 ≈ 0.5408.

        The ordering holds because conviction·info prices the room, not
        the agreement: the prophet spoke where the herd was ignorant
        (info=1), the conformist where it was still fluid, and the
        bandwagoner's info of 0.1 collapses even perfect alignment
        toward base rate 0.5.
        """
        svc = MathService(c_half_life=float("inf"), t_half_life=float("inf"), maturity_k=1.0)

        prophet_rows = [
            TrustSignal(
                hypothesis_id="h1",
                c_oracle_raw=0.8,
                timestamp=0,
                c_herd_prior=0.0,
                n_oracle_prior=0,
            )
        ]
        t_prophet = svc.compute_oracle_trust(
            rows=prophet_rows, herd_evidence=_evidence(t_now=0, h1=0.6), t_now=0
        )

        conformist_rows = [
            TrustSignal(
                hypothesis_id="h1",
                c_oracle_raw=0.60,
                timestamp=0,
                c_herd_prior=0.40,
                n_oracle_prior=4,
            ),
            TrustSignal(
                hypothesis_id="h2",
                c_oracle_raw=0.50,
                timestamp=0,
                c_herd_prior=0.30,
                n_oracle_prior=9,
            ),
        ]
        t_conformist = svc.compute_oracle_trust(
            rows=conformist_rows, herd_evidence=_evidence(t_now=0, h1=0.55, h2=0.45), t_now=0
        )

        bandwagoner_rows = [
            TrustSignal(
                hypothesis_id="h1",
                c_oracle_raw=0.9,
                timestamp=0,
                c_herd_prior=0.9,
                n_oracle_prior=5,
            ),
            TrustSignal(
                hypothesis_id="h2",
                c_oracle_raw=0.8,
                timestamp=0,
                c_herd_prior=0.9,
                n_oracle_prior=9,
            ),
        ]
        t_bandwagoner = svc.compute_oracle_trust(
            rows=bandwagoner_rows, herd_evidence=_evidence(t_now=0, h1=0.9, h2=0.9), t_now=0
        )

        assert t_prophet > t_conformist > t_bandwagoner

    def test_trust_spent_on_bad_claim_drops_at_next_scan(self) -> None:
        """The build-then-spend arc: a committed wrong call lowers trust.

        IDEA.md's trust-exploitation bound as a scenario, not prose: an
        oracle who builds high trust and then spends it on a bad claim
        gets one high-trust attestation before the herd corrects the
        hypothesis and the next trust scan prices the wrong call in.
        One service (infinite half-lives, K=1); the arc is purely
        evidential, timing plays no role.

        Build (t_before = 0.700): two prophet-style rows, c=0.8 on fresh
        hypotheses (prior=0.0, n_oracle_prior=0), herd converges to 0.6.
        Per row: align = 0.5·0.6 + 0.5·0.9 = 0.75, info=1, signal=0.8,
          effective = 0.8·0.75 + 0.2·0.5 = 0.700; num 0.560, den 0.8.
        t_before = 1.12 / 1.6 = 0.700, well above base rate 0.5.

        Spend (t_after ≈ 0.5794): one row, c=0.9 on a fresh hypothesis,
        herd corrects to -0.8 (the committed wrong call, witnessed):
          align_write = 1 - 0.5·0.9 = 0.55,
          align_read  = 1 - 0.5·|0.9 - (-0.8)| = 0.15,
          align = 0.5·0.55 + 0.5·0.15 = 0.35, info=1, signal=0.9,
          effective = 0.9·0.35 + 0.1·0.5 = 0.365; num 0.3285, den 0.9.
        t_after = 1.4485 / 2.5 = 0.5794 < t_before.

        One bad call dents trust, it does not zero it: the vindicated
        career still holds t_after above base rate. Both are asserted:
        the drop and the floor.
        """
        svc = MathService(c_half_life=float("inf"), t_half_life=float("inf"), maturity_k=1.0)

        build_rows = [
            TrustSignal(
                hypothesis_id="h1",
                c_oracle_raw=0.8,
                timestamp=0,
                c_herd_prior=0.0,
                n_oracle_prior=0,
            ),
            TrustSignal(
                hypothesis_id="h2",
                c_oracle_raw=0.8,
                timestamp=0,
                c_herd_prior=0.0,
                n_oracle_prior=0,
            ),
        ]
        t_before = svc.compute_oracle_trust(
            rows=build_rows, herd_evidence=_evidence(t_now=0, h1=0.6, h2=0.6), t_now=0
        )

        spend_row = TrustSignal(
            hypothesis_id="h3",
            c_oracle_raw=0.9,
            timestamp=0,
            c_herd_prior=0.0,
            n_oracle_prior=0,
        )
        t_after = svc.compute_oracle_trust(
            rows=[*build_rows, spend_row],
            herd_evidence=_evidence(t_now=0, h1=0.6, h2=0.6, h3=-0.8),
            t_now=0,
        )

        assert t_before > 0.5
        assert 0.5 < t_after < t_before

    def test_all_vacuous_history_returns_base_rate(self) -> None:
        """All rows have c_oracle_raw=0.0 → conviction=0 → denominator=0 → 0.5."""
        svc = MathService(c_half_life=100.0, t_half_life=_NO_DECAY_HL)
        rows = [
            TrustSignal(
                hypothesis_id="h1",
                c_oracle_raw=0.0,
                timestamp=900,
                c_herd_prior=0.0,
                n_oracle_prior=0,
            ),
            TrustSignal(
                hypothesis_id="h2",
                c_oracle_raw=0.0,
                timestamp=1000,
                c_herd_prior=0.0,
                n_oracle_prior=2,
            ),
        ]
        result = svc.compute_oracle_trust(
            rows=rows, herd_evidence=_evidence(t_now=1000, h1=0.0, h2=0.0), t_now=1000
        )
        assert abs(result - 0.5) < EPSILON

    def test_k_zero_degeneracy_collapses_to_pure_write_time(self) -> None:
        """K=0 makes M_write=1 for all rows → adaptive w = pure write-time.

        Same row as test_fresh_herd_perfect_read_alignment but with K=0:
          align = align_write = 0.6 (not the 0.8 blend)
          info  = 1, signal = 0.8
          effective = 0.8·0.6 + 0.2·0.5 = 0.58
        """
        svc = MathService(c_half_life=100.0, maturity_k=0, t_half_life=_NO_DECAY_HL)
        rows = [
            TrustSignal(
                hypothesis_id="h1",
                c_oracle_raw=0.8,
                timestamp=1000,
                c_herd_prior=0.0,
                n_oracle_prior=0,
            )
        ]
        result = svc.compute_oracle_trust(
            rows=rows, herd_evidence=_evidence(t_now=1000, h1=0.8), t_now=1000
        )
        assert abs(result - 0.58) < EPSILON

    def test_conviction_weighting_excludes_vacuous_rows(self) -> None:
        """Vacuous rows drop out of the average (conviction=0).

        Vacuous row at t=900 contributes nothing. The single committed row
        at t=1000 (same as test_fresh_herd_calibration_reduces_to_conviction)
        gives t_oracle = 0.6925.
        """
        svc = MathService(c_half_life=100.0, t_half_life=_NO_DECAY_HL)
        rows = [
            TrustSignal(
                hypothesis_id="h1",
                c_oracle_raw=0.0,
                timestamp=900,
                c_herd_prior=0.4,
                n_oracle_prior=0,
            ),
            TrustSignal(
                hypothesis_id="h2",
                c_oracle_raw=0.7,
                timestamp=1000,
                c_herd_prior=0.0,
                n_oracle_prior=0,
            ),
        ]
        result = svc.compute_oracle_trust(
            rows=rows, herd_evidence=_evidence(t_now=1000, h1=0.4, h2=0.5), t_now=1000
        )
        assert abs(result - 0.6925) < EPSILON

    def test_mixed_history_conviction_weighted_average(self) -> None:
        """Two committed rows, different effective alignments.

        Row 1 (fresh blend, perfect read): effective = 0.74, conv = 0.8
        Row 2 (fresh blend, 0.5/0.5 signals): effective = 0.5625, conv = 0.5
        No decay:
          numerator   = 0.74·0.8 + 0.5625·0.5 = 0.592 + 0.28125 = 0.87325
          denominator = 0.8 + 0.5 = 1.3
          t_oracle    = 0.87325 / 1.3
        """
        svc = MathService(c_half_life=100.0, t_half_life=_NO_DECAY_HL)
        rows = [
            TrustSignal(
                hypothesis_id="h1",
                c_oracle_raw=0.8,
                timestamp=1000,
                c_herd_prior=0.0,
                n_oracle_prior=0,
            ),
            TrustSignal(
                hypothesis_id="h2",
                c_oracle_raw=0.5,
                timestamp=1000,
                c_herd_prior=0.5,
                n_oracle_prior=0,
            ),
        ]
        result = svc.compute_oracle_trust(
            rows=rows, herd_evidence=_evidence(t_now=1000, h1=0.8, h2=-0.5), t_now=1000
        )
        assert abs(result - (0.87325 / 1.3)) < EPSILON

    def test_decay_weights_recent_more(self) -> None:
        """Recent attestation gets higher weight than old one."""
        trust_half_life = 2000.0
        t_now = 10000
        svc = MathService(c_half_life=100.0, t_half_life=trust_half_life)
        rows = [
            TrustSignal(
                hypothesis_id="h1",
                c_oracle_raw=0.8,
                timestamp=100,
                c_herd_prior=0.0,
                n_oracle_prior=0,
            ),
            TrustSignal(
                hypothesis_id="h2",
                c_oracle_raw=0.5,
                timestamp=9000,
                c_herd_prior=0.5,
                n_oracle_prior=0,
            ),
        ]

        lambda_trust = math.log(2) / trust_half_life
        # Row 1: fresh blend (M=0.5), align=0.8, signal=0.8·1 → effective=0.74
        effective_old = 0.74
        conv_old = 0.8
        w_old = math.exp(-lambda_trust * (t_now - 100))
        # Row 2: fresh blend (M=0.5), align=0.75, signal=0.5·0.5 → effective=0.5625
        effective_new = 0.5625
        conv_new = 0.5
        w_new = math.exp(-lambda_trust * (t_now - 9000))
        expected = (effective_old * conv_old * w_old + effective_new * conv_new * w_new) / (
            conv_old * w_old + conv_new * w_new
        )

        result = svc.compute_oracle_trust(
            rows=rows, herd_evidence=_evidence(t_now=t_now, h1=0.8, h2=-0.5), t_now=t_now
        )
        assert abs(result - expected) < EPSILON

    def test_zero_age_row_bears_full_weight(self) -> None:
        """A row attested at t_now decays not at all: dt = 0, weight exactly 1.

        A single-row ratio cancels the weight, so the boundary is only
        observable in a blend: the second row sits exactly one half-life
        back (weight 0.5).

        Row 1 (zero age): fresh blend (M=0.5), align=0.8, signal=0.8·1 → effective=0.74
        Row 2 (one half-life): align=0.75, signal=0.5·0.5 → effective=0.5625
        """
        t_now = 10_000
        svc = MathService(c_half_life=100.0, t_half_life=100.0)
        rows = [
            TrustSignal(
                hypothesis_id="h1",
                c_oracle_raw=0.8,
                timestamp=t_now,
                c_herd_prior=0.0,
                n_oracle_prior=0,
            ),
            TrustSignal(
                hypothesis_id="h2",
                c_oracle_raw=0.5,
                timestamp=t_now - 100,
                c_herd_prior=0.5,
                n_oracle_prior=0,
            ),
        ]
        expected = (0.74 * 0.8 * 1.0 + 0.5625 * 0.5 * 0.5) / (0.8 * 1.0 + 0.5 * 0.5)

        result = svc.compute_oracle_trust(
            rows=rows, herd_evidence=_evidence(t_now=t_now, h1=0.8, h2=-0.5), t_now=t_now
        )
        assert abs(result - expected) < EPSILON

    def test_future_row_treated_as_zero_age(self) -> None:
        """A row timestamped after t_now clamps to zero age, not negative.

        Mirrors test_future_attestation_treated_as_undecayed on the
        attestation side: clock skew must not mint amplified weight
        (exp(+λ·dt)) for evidence from the future.
        """
        t_now = 10_000
        svc = MathService(c_half_life=100.0, t_half_life=100.0)

        def rows_with_first_timestamp(timestamp: int) -> list[TrustSignal]:
            return [
                TrustSignal(
                    hypothesis_id="h1",
                    c_oracle_raw=0.8,
                    timestamp=timestamp,
                    c_herd_prior=0.0,
                    n_oracle_prior=0,
                ),
                TrustSignal(
                    hypothesis_id="h2",
                    c_oracle_raw=0.5,
                    timestamp=t_now - 100,
                    c_herd_prior=0.5,
                    n_oracle_prior=0,
                ),
            ]

        herd = _evidence(t_now=t_now, h1=0.8, h2=-0.5)
        at_now = svc.compute_oracle_trust(
            rows=rows_with_first_timestamp(t_now), herd_evidence=herd, t_now=t_now
        )
        from_future = svc.compute_oracle_trust(
            rows=rows_with_first_timestamp(t_now + 500), herd_evidence=herd, t_now=t_now
        )
        assert from_future == at_now

    def test_compute_oracle_trust_asymmetric_prior_hand_calc(self) -> None:
        """Hand-calculated trust under an asymmetric, mid-confidence prior.

        Pins the §4.8 alignment identity ``1 - PD(to_opinion(c_a),
        to_opinion(c_b)) = 1 - 0.5·|c_a − c_b|`` to a worked example:

          c_oracle_raw=0.4, c_herd_prior=0.1
          align_write = 1 − 0.5·|0.4 − 0.1| = 0.85
          align_read  = 1 − 0.5·|0.4 − 0.6| = 0.90
          M_write     = 1/2 (n_oracle_prior=0, K=1)
          align       = 0.5·0.85 + 0.5·0.90 = 0.875
          info        = 1 − |0.1| = 0.9
          signal      = 0.4·0.9 = 0.36
          effective   = 0.36·0.875 + 0.64·0.5 = 0.315 + 0.320 = 0.635
        """
        svc = MathService(c_half_life=100.0, t_half_life=_NO_DECAY_HL)
        rows = [
            TrustSignal(
                hypothesis_id="h1",
                c_oracle_raw=0.4,
                timestamp=1000,
                c_herd_prior=0.1,
                n_oracle_prior=0,
            )
        ]
        result = svc.compute_oracle_trust(
            rows=rows, herd_evidence=_evidence(t_now=1000, h1=0.6), t_now=1000
        )
        assert abs(result - 0.635) < EPSILON

    def test_compute_oracle_trust_is_row_order_invariant(self) -> None:
        """t_oracle is bit-exact under row reordering.

        ``math.fsum`` is Shewchuk-exact and order-independent. The witness
        list mixes convictions across ~12 orders of magnitude so naive
        ``+=`` accumulation would round differently across orderings.
        Reversed and shuffled permutations each pin the contract.
        """
        svc = MathService(c_half_life=100.0, t_half_life=_NO_DECAY_HL)

        def row(i: int, c_oracle_raw: float, c_herd_prior: float) -> TrustSignal:
            return TrustSignal(
                hypothesis_id=f"h{i}",
                c_oracle_raw=c_oracle_raw,
                timestamp=1000,
                c_herd_prior=c_herd_prior,
                n_oracle_prior=0,
            )

        rows = [
            row(1, 0.99, 0.10),
            row(2, 1e-12, 0.50),
            row(3, 0.80, 0.00),
            row(4, 1e-10, -0.30),
            row(5, 0.50, 0.20),
            row(6, 1e-14, 0.00),
            row(7, 0.70, -0.10),
            row(8, 1e-8, 0.40),
        ]
        evidence = _evidence(
            t_now=1000, h1=0.20, h2=-0.50, h3=0.60, h4=0.30, h5=0.40, h6=0.10, h7=0.50, h8=-0.40
        )

        baseline = svc.compute_oracle_trust(rows=rows, herd_evidence=evidence, t_now=1000)
        assert (
            svc.compute_oracle_trust(rows=list(reversed(rows)), herd_evidence=evidence, t_now=1000)
            == baseline
        )
        # An explicit interleave: small/large magnitudes alternated so a
        # naive left-fold lands on a different intermediate than baseline
        # or reverse.
        shuffled = [rows[1], rows[0], rows[5], rows[2], rows[7], rows[4], rows[3], rows[6]]
        assert (
            svc.compute_oracle_trust(rows=shuffled, herd_evidence=evidence, t_now=1000) == baseline
        )

    def test_infinite_trust_half_life_means_no_decay(self) -> None:
        """t_half_life=inf → λ_trust=0 → all weights are 1.0.

        Two identical-by-construction rows at wildly different timestamps
        must contribute equally to the average. Each row: fresh blend,
        align=0.8, signal=0.8·1, effective=0.74, conv=0.8. Result = 0.74.
        """
        svc = MathService(c_half_life=100.0, t_half_life=float("inf"))
        rows = [
            TrustSignal(
                hypothesis_id="h1",
                c_oracle_raw=0.8,
                timestamp=100,
                c_herd_prior=0.0,
                n_oracle_prior=0,
            ),
            TrustSignal(
                hypothesis_id="h2",
                c_oracle_raw=0.8,
                timestamp=999_999,
                c_herd_prior=0.0,
                n_oracle_prior=0,
            ),
        ]
        result = svc.compute_oracle_trust(
            rows=rows, herd_evidence=_evidence(t_now=1_000_000, h1=0.8, h2=0.8), t_now=1_000_000
        )
        assert abs(result - 0.74) < EPSILON

    def test_unwitnessed_rows_leave_the_scan(self) -> None:
        """A row no other oracle has answered carries no alignment information.

        The witness rule drops it from numerator and denominator alike: an
        all-solo history is informationally identical to cold start and
        earns base rate exactly.
        """
        svc = MathService(c_half_life=100.0, t_half_life=_NO_DECAY_HL)
        rows = [
            TrustSignal(
                hypothesis_id="h1",
                c_oracle_raw=0.9,
                timestamp=1000,
                c_herd_prior=0.0,
                n_oracle_prior=0,
            )
        ]
        result = svc.compute_oracle_trust(rows=rows, herd_evidence={"h1": []}, t_now=1000)
        assert result == 0.5

    def test_solo_spam_collapses_to_base_rate(self) -> None:
        """The solo-spam attack pin: low-conviction novels, no witnesses.

        Pre-fix, the stored herd snapshot included the oracle's own row, so
        align_read = 1 on every solo hypothesis and iterated self-reference
        converged on t = (1 − 0.5c)/(1 − 0.125c) ≈ 0.923 at c = 0.2. Under
        the witness rule every unwitnessed row leaves the scan: the whole
        campaign is worth exactly base rate.
        """
        svc = MathService(c_half_life=100.0, t_half_life=_NO_DECAY_HL)
        rows = [
            TrustSignal(
                hypothesis_id=f"h{i}",
                c_oracle_raw=0.2,
                timestamp=1000 + i,
                c_herd_prior=0.0,
                n_oracle_prior=0,
            )
            for i in range(20)
        ]
        evidence: dict[str, list[EvidenceInput]] = {f"h{i}": [] for i in range(20)}
        result = svc.compute_oracle_trust(rows=rows, herd_evidence=evidence, t_now=2000)
        assert result == 0.5

    def test_reference_is_others_only_refusion(self) -> None:
        """align_read measures against the recomputed others-only herd state.

        Evidence: two other-oracle rows, c = 0.15 and 0.30 at t_now; ECBF
        fuses them to 72/191, and that recomputation is the only source
        the read-time leg has.

          ref         = 72/191 ≈ 0.376963
          align_write = 1 − 0.5·|0.5 − 0.0|     = 0.75
          align_read  = 1 − 0.5·|0.5 − 72/191|  = 358.5/382
          align       = 0.5·0.75 + 0.5·(358.5/382) = 645/764
          signal      = 0.5·1.0
          effective   = 0.5·(645/764) + 0.5·0.5 = 1027/1528 ≈ 0.672120
        """
        svc = MathService(c_half_life=100.0, t_half_life=_NO_DECAY_HL)
        t_now = 1000
        rows = [
            TrustSignal(
                hypothesis_id="h1",
                c_oracle_raw=0.5,
                timestamp=t_now,
                c_herd_prior=0.0,
                n_oracle_prior=0,
            )
        ]
        evidence = {
            "h1": [
                EvidenceInput(c_oracle_discounted=0.15, timestamp=t_now),
                EvidenceInput(c_oracle_discounted=0.30, timestamp=t_now),
            ]
        }
        result = svc.compute_oracle_trust(rows=rows, herd_evidence=evidence, t_now=t_now)
        assert abs(result - 1027 / 1528) < EPSILON

    def test_transfer_row_witnesses_a_novel(self) -> None:
        """The synthetic _transfer oracle counts as a witness.

        A contradicting novel receives a transfer prior before the oracle's
        own row; that evidence keeps the row in the scan.

          ref = −0.3 (the transfer row)
          align_write = 1 − 0.5·|0.8 − (−0.3)| = 0.45
          align_read  = 1 − 0.5·|0.8 − (−0.3)| = 0.45
          align = 0.45; info = 1 − 0.3 = 0.7; signal = 0.8·0.7 = 0.56
          effective = 0.56·0.45 + 0.44·0.5 = 0.472
        """
        svc = MathService(c_half_life=100.0, t_half_life=_NO_DECAY_HL)
        t_now = 1000
        rows = [
            TrustSignal(
                hypothesis_id="h1",
                c_oracle_raw=0.8,
                timestamp=t_now,
                c_herd_prior=-0.3,
                n_oracle_prior=0,
            )
        ]
        evidence = {"h1": [EvidenceInput(c_oracle_discounted=-0.3, timestamp=t_now)]}
        result = svc.compute_oracle_trust(rows=rows, herd_evidence=evidence, t_now=t_now)
        assert abs(result - 0.472) < EPSILON

    def test_mixed_history_averages_witnessed_rows_only(self) -> None:
        """Witnessed and unwitnessed rows in one scan: only the witnessed count.

        The h1 row scores effective = 0.74 (align_write 0.6, align_read 1.0,
        signal 0.8). The h2 row carries conviction 0.9 and catastrophic
        misalignment, but no witness: skipped, not averaged. Result equals
        the single-witnessed-row value exactly.
        """
        svc = MathService(c_half_life=100.0, t_half_life=_NO_DECAY_HL)
        t_now = 1000
        rows = [
            TrustSignal(
                hypothesis_id="h1",
                c_oracle_raw=0.8,
                timestamp=t_now,
                c_herd_prior=0.0,
                n_oracle_prior=0,
            ),
            TrustSignal(
                hypothesis_id="h2",
                c_oracle_raw=-0.9,
                timestamp=t_now,
                c_herd_prior=0.0,
                n_oracle_prior=0,
            ),
        ]
        evidence: dict[str, list[EvidenceInput]] = {
            "h1": [EvidenceInput(c_oracle_discounted=0.8, timestamp=t_now)],
            "h2": [],
        }
        result = svc.compute_oracle_trust(rows=rows, herd_evidence=evidence, t_now=t_now)
        assert abs(result - 0.74) < EPSILON


# --- build_math factory ---
def test_build_math_wires_epistemics() -> None:
    """The factory maps EpistemicsConfig half-lives and K onto the service.

    The fixture's epistemics (30d, 45d, K=3) all differ from the shipped
    defaults, and the assertions are literals (half-lives in seconds), so a
    factory that hardcodes or echoes a default cannot pass. MathService
    stores rate constants privately (λ = ln2 / half_life); the test reaches
    them to confirm the hyperparameters reached the engine intact.
    """
    with patch.dict(os.environ, _BASE_ENV, clear=True):
        settings = load_settings(toml_path=_TRUST_TOML)

    svc = build_math(settings)

    assert isinstance(svc, MathService)
    assert svc._maturity_k == 3.0  # pyright: ignore[reportPrivateUsage]
    assert svc._lambda == math.log(2) / 2_592_000  # pyright: ignore[reportPrivateUsage]
    assert svc._trust_lambda == math.log(2) / 3_888_000  # pyright: ignore[reportPrivateUsage]
