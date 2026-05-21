"""Tests for scalar confidence ↔ opinion mapping.

The interface currency is a scalar c ∈ [-1, 1]. Positive values express
belief, negative values express disbelief, zero expresses ignorance. The forward
mapping (c → Opinion) produces uncertainty-maximized opinions by construction.
The inverse mapping (Opinion → c) projects through P: c = b − d.

The mapping function is pure math — it maps scalars to opinions across the full
[-1, 1] domain. Trust discounting (P_effective < 1 for K >= 1) is the pipeline
policy that prevents dogmatic opinions from reaching ECBF, not input validation.

See docs/logic.md, "Scalar Confidence Mapping."
"""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from lore.math.confidence import to_confidence, to_opinion
from lore.math.fusion import fuse
from lore.math.opinion import EPSILON, VACUOUS, Opinion

from .conftest import PROP_TOL, opinion_strategy

# Strategy: scalars across the full mathematical domain [-1, 1].
confidence_strategy = st.floats(min_value=-1.0, max_value=1.0)


# --- Forward mapping: v → Opinion ---
class TestToOpinionHandCalculated:
    def test_positive_v_produces_belief_and_uncertainty(self) -> None:
        result = to_opinion(0.8)
        assert abs(result.b - 0.8) < EPSILON
        assert abs(result.d - 0.0) < EPSILON
        assert abs(result.u - 0.2) < EPSILON

    def test_negative_v_produces_disbelief_and_uncertainty(self) -> None:
        result = to_opinion(-0.6)
        assert abs(result.b - 0.0) < EPSILON
        assert abs(result.d - 0.6) < EPSILON
        assert abs(result.u - 0.4) < EPSILON

    def test_zero_produces_vacuous(self) -> None:
        result = to_opinion(0.0)
        assert abs(result.b - 0.0) < EPSILON
        assert abs(result.d - 0.0) < EPSILON
        assert abs(result.u - 1.0) < EPSILON

    def test_boundary_positive(self) -> None:
        result = to_opinion(0.99)
        assert abs(result.b - 0.99) < EPSILON
        assert abs(result.d - 0.0) < EPSILON
        assert abs(result.u - 0.01) < EPSILON

    def test_boundary_negative(self) -> None:
        result = to_opinion(-0.99)
        assert abs(result.b - 0.0) < EPSILON
        assert abs(result.d - 0.99) < EPSILON
        assert abs(result.u - 0.01) < EPSILON

    def test_near_dogmatic_positive(self) -> None:
        result = to_opinion(0.995)
        assert abs(result.b - 0.995) < EPSILON
        assert abs(result.d - 0.0) < EPSILON
        assert abs(result.u - 0.005) < EPSILON

    def test_near_dogmatic_negative(self) -> None:
        result = to_opinion(-0.995)
        assert abs(result.b - 0.0) < EPSILON
        assert abs(result.d - 0.995) < EPSILON
        assert abs(result.u - 0.005) < EPSILON


class TestToOpinionDogmaticBoundary:
    """c = +/-1.0 produces dogmatic opinions — the mathematical boundary.

    Trust discounting (P_effective < 1 for K >= 1) is the pipeline policy
    that prevents dogmatic opinions from reaching ECBF. The mapping function
    itself is pure math over [-1, 1].
    """

    def test_to_opinion_one_produces_dogmatic_belief(self) -> None:
        result = to_opinion(1.0)
        assert abs(result.b - 1.0) < EPSILON
        assert abs(result.d - 0.0) < EPSILON
        assert abs(result.u - 0.0) < EPSILON

    def test_to_opinion_negative_one_produces_dogmatic_disbelief(self) -> None:
        result = to_opinion(-1.0)
        assert abs(result.b - 0.0) < EPSILON
        assert abs(result.d - 1.0) < EPSILON
        assert abs(result.u - 0.0) < EPSILON


class TestToOpinionOutOfRange:
    def test_v_above_one_raises(self) -> None:
        with pytest.raises(ValueError):
            to_opinion(1.5)

    def test_v_below_negative_one_raises(self) -> None:
        with pytest.raises(ValueError):
            to_opinion(-1.5)

    def test_nan_raises(self) -> None:
        with pytest.raises(ValueError):
            to_opinion(float("nan"))

    def test_positive_inf_raises(self) -> None:
        with pytest.raises(ValueError):
            to_opinion(float("inf"))

    def test_negative_inf_raises(self) -> None:
        with pytest.raises(ValueError):
            to_opinion(float("-inf"))


# --- Inverse mapping: Opinion → v ---
class TestToConfidence:
    def test_roundtrip_positive(self) -> None:
        v = to_confidence(to_opinion(0.8))
        assert abs(v - 0.8) < EPSILON

    def test_roundtrip_negative(self) -> None:
        v = to_confidence(to_opinion(-0.6))
        assert abs(v - (-0.6)) < EPSILON

    def test_roundtrip_vacuous(self) -> None:
        v = to_confidence(to_opinion(0.0))
        assert abs(v - 0.0) < EPSILON

    def test_from_vacuous_opinion(self) -> None:
        v = to_confidence(VACUOUS)
        assert abs(v - 0.0) < EPSILON

    def test_from_ecbf_output(self) -> None:
        """Fused opinion → scalar preserves direction."""
        a = Opinion(b=0.7, d=0.1, u=0.2)
        b = Opinion(b=0.6, d=0.1, u=0.3)
        fused = fuse([a, b])
        v = to_confidence(fused)
        # Fused result has b > 0, d = 0 → v must be positive.
        assert v > 0.0
        # Roundtrip through to_opinion recovers the same projected probability.
        recovered = to_opinion(v)
        assert abs(recovered.projected_probability - fused.projected_probability) < EPSILON

    def test_near_dogmatic_opinion_returns_exact_confidence(self) -> None:
        """Trust discounting is the binding constraint, not output clamping.

        An opinion with b=0.995 should produce c=0.995 — the inverse
        mapping is c = b − d with no clamping. The pipeline (P_effective < 1
        for K >= 1) prevents dogmatic opinions from reaching ECBF.
        """
        almost_dogmatic = Opinion(b=0.995, d=0.0, u=0.005)
        v = to_confidence(almost_dogmatic)
        assert abs(v - 0.995) < EPSILON


# --- Property-based tests ---
@given(v=confidence_strategy)
def test_bdu_sum_is_one(v: float) -> None:
    opinion = to_opinion(v)
    assert abs(opinion.b + opinion.d + opinion.u - 1.0) < PROP_TOL


@given(v=confidence_strategy)
def test_output_is_uncertainty_maximized(v: float) -> None:
    """min(b, d) = 0 — always on the simplex boundary."""
    opinion = to_opinion(v)
    assert min(opinion.b, opinion.d) < PROP_TOL


@given(v=confidence_strategy)
def test_projected_probability_is_affine(v: float) -> None:
    """P = 0.5 + 0.5v."""
    opinion = to_opinion(v)
    expected_p = 0.5 + 0.5 * v
    assert abs(opinion.projected_probability - expected_p) < PROP_TOL


@given(v=confidence_strategy)
def test_roundtrip(v: float) -> None:
    """to_confidence(to_opinion(v)) = v for all valid v."""
    recovered = to_confidence(to_opinion(v))
    assert abs(recovered - v) < PROP_TOL


@given(opinion=opinion_strategy)
def test_to_confidence_range_property(opinion: Opinion) -> None:
    """to_confidence output ∈ [-1, 1] for all valid opinions."""
    v = to_confidence(opinion)
    assert -1.0 - PROP_TOL <= v <= 1.0 + PROP_TOL
