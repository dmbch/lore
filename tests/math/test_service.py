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

# Complete TOML with all required sections: a valid base for build_math.
_COMPLETE_TOML = Path(__file__).parent.parent / "fixtures" / "lore_complete.toml"
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
            hypothesis_id="h1", c_oracle_raw=0.5, timestamp=100, c_herd_prior=0.0, c_herd_now=0.5
        )


def test_oracle_alignment_snapshot_rejects_negative_n_oracle_prior() -> None:
    """Distinct oracle count cannot be negative."""
    with pytest.raises(ValidationError):
        TrustSignal(
            hypothesis_id="h1",
            c_oracle_raw=0.5,
            timestamp=100,
            c_herd_prior=0.0,
            c_herd_now=0.5,
            n_oracle_prior=-1,
        )


# --- compute_oracle_trust (method) ---
class TestComputeOracleTrust:
    """Tests for oracle trust computation via MathService.

    Formula (see docs/logic.md, Oracle Trust section):
      M_write_i        = N_O / (N_O + K), N_O = n_oracle_prior + 1
      align_write_i    = 1 - 0.5 * |c_oracle_raw - c_herd_prior|
      align_read_i     = 1 - 0.5 * |c_oracle_raw - c_herd_now|
      align_i          = M_write · align_write + (1 - M_write) · align_read
      info_i           = 1 - |c_herd_prior|
      conviction_i     = |c_oracle_raw|
      signal_i         = conviction · info
      effective_align  = signal · align + (1 - signal) · 0.5  (Def. 14.6)
      weight_i         = exp(-λ_trust · Δt)
      t_oracle         = Σ(effective_align · conviction · weight)
                       / Σ(conviction · weight)
    """

    def test_empty_rows_returns_base_rate(self) -> None:
        """Cold start: no history → base rate trust (0.5)."""
        svc = MathService(c_half_life=100.0, t_half_life=_NO_DECAY_HL)
        result = svc.compute_oracle_trust(rows=[], t_now=1000)
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
                c_herd_now=0.8,
                n_oracle_prior=0,
            )
        ]
        result = svc.compute_oracle_trust(rows=rows, t_now=1000)
        assert abs(result - 0.74) < EPSILON

    def test_adaptive_w_fresh_hypothesis_blends_equally(self) -> None:
        """Fresh hypothesis (n_oracle_prior=0, K=1) → M_write=0.5.

        Write-time and read-time signals get equal weight.
          c_oracle_raw=0.5, c_herd_prior=0.5, c_herd_now=-0.5
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
                c_herd_now=-0.5,
                n_oracle_prior=0,
            )
        ]
        result = svc.compute_oracle_trust(rows=rows, t_now=1000)
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
                c_herd_now=-0.5,
                n_oracle_prior=9,
            )
        ]
        result = svc.compute_oracle_trust(rows=rows, t_now=1000)
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
                c_herd_now=0.9,
                n_oracle_prior=5,
            )
        ]
        result = svc.compute_oracle_trust(rows=rows, t_now=1000)
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
                c_herd_now=0.5,
                n_oracle_prior=0,
            )
        ]
        result = svc.compute_oracle_trust(rows=rows, t_now=1000)
        assert abs(result - 0.6925) < EPSILON

    def test_conviction_calibrates_alignment_signal(self) -> None:
        """A perfectly aligned row moves trust only as far as its conviction.

        K=inf is the pure read-time limit: M_write = 1/(1+inf) = 0, so
        align = align_read exactly. (At any finite K the write leg mixes
        in and align=1 with info=1 is unreachable for nonzero conviction.)
        With c_herd_now = c_oracle_raw the row is perfectly aligned and
        the herd was vacuous at write time:

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
                c_herd_now=0.2,
                n_oracle_prior=0,
            )
        ]
        result = svc.compute_oracle_trust(rows=rows, t_now=1000)
        assert abs(result - 0.6) < EPSILON

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
                c_herd_now=1.0,
                n_oracle_prior=4,
            )
        ]
        result = svc.compute_oracle_trust(rows=rows, t_now=1000)
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
                c_herd_now=0.8,
                n_oracle_prior=0,
            )
        ]
        result = svc.compute_oracle_trust(rows=rows, t_now=1000)
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
                c_herd_now=0.6,
                n_oracle_prior=0,
            )
        ]
        result = svc.compute_oracle_trust(rows=rows, t_now=0)
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
                c_herd_now=0.30,
                n_oracle_prior=0,
            ),
            TrustSignal(
                hypothesis_id="h2",
                c_oracle_raw=-0.7,
                timestamp=0,
                c_herd_prior=0.50,
                c_herd_now=0.55,
                n_oracle_prior=2,
            ),
        ]
        result = svc.compute_oracle_trust(rows=rows, t_now=0)
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
                c_herd_now=0.55,
                n_oracle_prior=4,
            ),
            TrustSignal(
                hypothesis_id="h2",
                c_oracle_raw=0.50,
                timestamp=0,
                c_herd_prior=0.30,
                c_herd_now=0.45,
                n_oracle_prior=9,
            ),
        ]
        result = svc.compute_oracle_trust(rows=rows, t_now=0)
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
                c_herd_now=0.6,
                n_oracle_prior=0,
            )
        ]
        result = svc.compute_oracle_trust(rows=rows, t_now=0)
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
                c_herd_now=1.0,
                n_oracle_prior=3,
            ),
            TrustSignal(
                hypothesis_id="h2",
                c_oracle_raw=-1.0,
                timestamp=1000,
                c_herd_prior=-1.0,
                c_herd_now=-1.0,
                n_oracle_prior=7,
            ),
        ]
        result = svc.compute_oracle_trust(rows=rows, t_now=1000)
        assert abs(result - 0.5) < EPSILON

    def test_all_vacuous_history_returns_base_rate(self) -> None:
        """All rows have c_oracle_raw=0.0 → conviction=0 → denominator=0 → 0.5."""
        svc = MathService(c_half_life=100.0, t_half_life=_NO_DECAY_HL)
        rows = [
            TrustSignal(
                hypothesis_id="h1",
                c_oracle_raw=0.0,
                timestamp=900,
                c_herd_prior=0.0,
                c_herd_now=0.0,
                n_oracle_prior=0,
            ),
            TrustSignal(
                hypothesis_id="h2",
                c_oracle_raw=0.0,
                timestamp=1000,
                c_herd_prior=0.0,
                c_herd_now=0.0,
                n_oracle_prior=2,
            ),
        ]
        result = svc.compute_oracle_trust(rows=rows, t_now=1000)
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
                c_herd_now=0.8,
                n_oracle_prior=0,
            )
        ]
        result = svc.compute_oracle_trust(rows=rows, t_now=1000)
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
                c_herd_now=0.4,
                n_oracle_prior=0,
            ),
            TrustSignal(
                hypothesis_id="h2",
                c_oracle_raw=0.7,
                timestamp=1000,
                c_herd_prior=0.0,
                c_herd_now=0.5,
                n_oracle_prior=0,
            ),
        ]
        result = svc.compute_oracle_trust(rows=rows, t_now=1000)
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
                c_herd_now=0.8,
                n_oracle_prior=0,
            ),
            TrustSignal(
                hypothesis_id="h2",
                c_oracle_raw=0.5,
                timestamp=1000,
                c_herd_prior=0.5,
                c_herd_now=-0.5,
                n_oracle_prior=0,
            ),
        ]
        result = svc.compute_oracle_trust(rows=rows, t_now=1000)
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
                c_herd_now=0.8,
                n_oracle_prior=0,
            ),
            TrustSignal(
                hypothesis_id="h2",
                c_oracle_raw=0.5,
                timestamp=9000,
                c_herd_prior=0.5,
                c_herd_now=-0.5,
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

        result = svc.compute_oracle_trust(rows=rows, t_now=t_now)
        assert abs(result - expected) < EPSILON

    def test_compute_oracle_trust_asymmetric_prior_hand_calc(self) -> None:
        """Hand-calculated trust under an asymmetric, mid-confidence prior.

        Pins the §4.8 alignment identity ``1 - PD(to_opinion(c_a),
        to_opinion(c_b)) = 1 - 0.5·|c_a − c_b|`` to a worked example:

          c_oracle_raw=0.4, c_herd_prior=0.1, c_herd_now=0.6
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
                c_herd_now=0.6,
                n_oracle_prior=0,
            )
        ]
        result = svc.compute_oracle_trust(rows=rows, t_now=1000)
        assert abs(result - 0.635) < EPSILON

    def test_compute_oracle_trust_is_row_order_invariant(self) -> None:
        """t_oracle is bit-exact under row reordering.

        ``math.fsum`` is Shewchuk-exact and order-independent. The witness
        list mixes convictions across ~12 orders of magnitude so naive
        ``+=`` accumulation would round differently across orderings.
        Reversed and shuffled permutations each pin the contract.
        """
        svc = MathService(c_half_life=100.0, t_half_life=_NO_DECAY_HL)

        def row(c_oracle_raw: float, c_herd_prior: float, c_herd_now: float) -> TrustSignal:
            return TrustSignal(
                hypothesis_id="h1",
                c_oracle_raw=c_oracle_raw,
                timestamp=1000,
                c_herd_prior=c_herd_prior,
                c_herd_now=c_herd_now,
                n_oracle_prior=0,
            )

        rows = [
            row(0.99, 0.10, 0.20),
            row(1e-12, 0.50, -0.50),
            row(0.80, 0.00, 0.60),
            row(1e-10, -0.30, 0.30),
            row(0.50, 0.20, 0.40),
            row(1e-14, 0.00, 0.10),
            row(0.70, -0.10, 0.50),
            row(1e-8, 0.40, -0.40),
        ]

        baseline = svc.compute_oracle_trust(rows=rows, t_now=1000)
        assert svc.compute_oracle_trust(rows=list(reversed(rows)), t_now=1000) == baseline
        # An explicit interleave: small/large magnitudes alternated so a
        # naive left-fold lands on a different intermediate than baseline
        # or reverse.
        shuffled = [rows[1], rows[0], rows[5], rows[2], rows[7], rows[4], rows[3], rows[6]]
        assert svc.compute_oracle_trust(rows=shuffled, t_now=1000) == baseline

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
                c_herd_now=0.8,
                n_oracle_prior=0,
            ),
            TrustSignal(
                hypothesis_id="h2",
                c_oracle_raw=0.8,
                timestamp=999_999,
                c_herd_prior=0.0,
                c_herd_now=0.8,
                n_oracle_prior=0,
            ),
        ]
        result = svc.compute_oracle_trust(rows=rows, t_now=1_000_000)
        assert abs(result - 0.74) < EPSILON


# --- build_math factory ---
def test_build_math_wires_epistemics() -> None:
    """The factory maps EpistemicsConfig half-lives and K onto the service.

    MathService stores rate constants privately (λ = ln2 / half_life); the
    test reaches them to confirm the four epistemic hyperparameters reached
    the engine intact.
    """
    with patch.dict(os.environ, _BASE_ENV, clear=True):
        settings = load_settings(toml_path=_COMPLETE_TOML)

    svc = build_math(settings)

    assert isinstance(svc, MathService)
    assert svc._maturity_k == settings.epistemics.maturity_k  # pyright: ignore[reportPrivateUsage]
    assert svc._lambda == math.log(2) / settings.epistemics.attestation_half_life  # pyright: ignore[reportPrivateUsage]
    assert svc._trust_lambda == math.log(2) / settings.epistemics.trust_half_life  # pyright: ignore[reportPrivateUsage]
