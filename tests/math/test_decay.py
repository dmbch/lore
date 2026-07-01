"""Tests for temporal decay toward vacuous opinion.

Unattested knowledge drifts back toward ignorance. Belief and disbelief
decay exponentially at rate λ; uncertainty fills the gap. At t=0 the
opinion is unchanged; as t→∞ it approaches vacuous. The b/d ratio is
preserved: decay erodes conviction, not direction.

Custom formula: Jøsang Ch. 16.2.2 decays evidence counters, not
opinions. Lore decays opinions directly (see decay.py module docstring).
Cross-checked against reference implementation single-step erosion.
"""

import math

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

from lore.math.decay import decay
from lore.math.opinion import EPSILON, VACUOUS, Opinion

from .conftest import PROP_TOL, opinion_strategy


# --- Boundary cases ---
def test_decay_at_zero_time_returns_original() -> None:
    """e^0 = 1: no time elapsed, opinion unchanged."""
    opinion = Opinion(b=0.6, d=0.3, u=0.1)
    result = decay(opinion=opinion, lambda_=0.1, t=0.0)
    assert abs(result.b - opinion.b) < EPSILON
    assert abs(result.d - opinion.d) < EPSILON
    assert abs(result.u - opinion.u) < EPSILON


def test_decay_at_large_time_approaches_vacuous() -> None:
    """e^(-λt) → 0 as t → ∞: opinion becomes vacuous."""
    opinion = Opinion(b=0.9, d=0.05, u=0.05)
    result = decay(opinion=opinion, lambda_=1.0, t=100.0)
    assert abs(result.b - 0.0) < EPSILON
    assert abs(result.d - 0.0) < EPSILON
    assert abs(result.u - 1.0) < EPSILON


def test_decay_vacuous_opinion_stays_vacuous() -> None:
    """Decaying a vacuous opinion is a no-op: nothing to erode."""
    result = decay(opinion=VACUOUS, lambda_=0.5, t=10.0)
    assert abs(result.b - 0.0) < EPSILON
    assert abs(result.d - 0.0) < EPSILON
    assert abs(result.u - 1.0) < EPSILON


def test_decay_with_zero_lambda_returns_original() -> None:
    """λ=0 means no decay: knowledge persists forever."""
    opinion = Opinion(b=0.7, d=0.2, u=0.1)
    result = decay(opinion=opinion, lambda_=0.0, t=1000.0)
    assert abs(result.b - opinion.b) < EPSILON
    assert abs(result.d - opinion.d) < EPSILON
    assert abs(result.u - opinion.u) < EPSILON


# --- Hand-calculated cases ---
def test_decay_hand_calculated() -> None:
    """Verify against manual calculation.

    Opinion: (0.6, 0.3, 0.1), λ=0.1, t=5
    e^(-0.5) ≈ 0.60653065971
    b(5) = 0.6 · 0.60653065971 ≈ 0.36391839583
    d(5) = 0.3 · 0.60653065971 ≈ 0.18195919791
    u(5) = 1 − 0.9 · 0.60653065971 ≈ 0.45412240626
    """
    opinion = Opinion(b=0.6, d=0.3, u=0.1)
    result = decay(opinion=opinion, lambda_=0.1, t=5.0)
    factor = math.exp(-0.5)
    assert abs(result.b - 0.6 * factor) < EPSILON
    assert abs(result.d - 0.3 * factor) < EPSILON
    assert abs(result.u - (1.0 - 0.9 * factor)) < EPSILON


def test_decay_half_life_equivalence() -> None:
    """At t = ln(2)/λ, belief and disbelief are halved.

    This is the half-life property. λ=1.0, t=ln(2) ≈ 0.693.
    e^(-ln(2)) = 0.5, so b and d are exactly halved.
    """
    opinion = Opinion(b=0.8, d=0.1, u=0.1)
    t_half = math.log(2.0)
    result = decay(opinion=opinion, lambda_=1.0, t=t_half)
    assert abs(result.b - 0.4) < EPSILON
    assert abs(result.d - 0.05) < EPSILON
    assert abs(result.u - 0.55) < EPSILON


def test_decay_dogmatic_opinion() -> None:
    """Dogmatic opinion (u=0) decays toward vacuous like any other.

    Opinion: (0.7, 0.3, 0.0), λ=0.5, t=2
    e^(-1.0) ≈ 0.36787944117
    b(2) = 0.7 · 0.36787944117 ≈ 0.25751560882
    d(2) = 0.3 · 0.36787944117 ≈ 0.11036383235
    u(2) = 1 − 1.0 · 0.36787944117 ≈ 0.63212055883
    """
    opinion = Opinion(b=0.7, d=0.3, u=0.0)
    result = decay(opinion=opinion, lambda_=0.5, t=2.0)
    factor = math.exp(-1.0)
    assert abs(result.b - 0.7 * factor) < EPSILON
    assert abs(result.d - 0.3 * factor) < EPSILON
    assert abs(result.u - (1.0 - 1.0 * factor)) < EPSILON


# --- Cross-check against reference implementation erosion ---
def test_reference_single_step_erosion_equivalence() -> None:
    """Our decay at t=1 matches the standard erosion structure.

    Standard erosion (retention-factor approach):
        f = retention factor ∈ [0, 1]
        b' = b · f
        d' = d · f
        u' = 1 - b' - d'

    Our formula with λ and t=1:
        factor = e^(-λ)
        b' = b · factor
        d' = d · factor
        u' = 1 - (1 - u) · factor

    Structurally identical: both multiply b and d by a retention factor,
    then derive u from the remainder. With retention f = e^(-λ):
    u' = 1 - b·f - d·f = 1 - (b+d)·f = 1 - (1-u)·f. ✓

    Retention=0.7 is equivalent to our λ = -ln(0.7) ≈ 0.35667, t=1.
    """
    opinion = Opinion(b=0.6, d=0.3, u=0.1)

    # Reference single-step erosion: retention=0.7
    retention = 0.7
    ref_b = opinion.b * retention  # 0.42
    ref_d = opinion.d * retention  # 0.21
    ref_u = 1.0 - ref_b - ref_d  # 0.37

    # Our equivalent: λ = -ln(retention), t=1
    lambda_ = -math.log(retention)
    result = decay(opinion=opinion, lambda_=lambda_, t=1.0)

    assert abs(result.b - ref_b) < EPSILON
    assert abs(result.d - ref_d) < EPSILON
    assert abs(result.u - ref_u) < EPSILON


# --- Error cases ---
def test_decay_negative_time_raises() -> None:
    """Negative time is nonsensical: reject it."""
    opinion = Opinion(b=0.5, d=0.3, u=0.2)
    with pytest.raises(ValueError):
        decay(opinion=opinion, lambda_=0.1, t=-1.0)


def test_decay_negative_lambda_raises() -> None:
    """Negative λ would mean anti-decay (growing certainty). Not allowed."""
    opinion = Opinion(b=0.5, d=0.3, u=0.2)
    with pytest.raises(ValueError):
        decay(opinion=opinion, lambda_=-0.1, t=1.0)


def test_decay_rejects_nan_lambda() -> None:
    """NaN λ cannot produce a meaningful decay factor: reject it."""
    opinion = Opinion(b=0.5, d=0.3, u=0.2)
    with pytest.raises(ValueError):
        decay(opinion=opinion, lambda_=float("nan"), t=1.0)


def test_decay_rejects_inf_lambda() -> None:
    """Infinite λ has no finite meaning on bounded elapsed time: reject it."""
    opinion = Opinion(b=0.5, d=0.3, u=0.2)
    with pytest.raises(ValueError):
        decay(opinion=opinion, lambda_=float("inf"), t=1.0)


def test_decay_rejects_nan_t() -> None:
    """NaN elapsed time cannot produce a meaningful decay factor: reject it."""
    opinion = Opinion(b=0.5, d=0.3, u=0.2)
    with pytest.raises(ValueError):
        decay(opinion=opinion, lambda_=0.1, t=float("nan"))


def test_decay_rejects_inf_t() -> None:
    """Infinite elapsed time has no finite meaning at read time: reject it."""
    opinion = Opinion(b=0.5, d=0.3, u=0.2)
    with pytest.raises(ValueError):
        decay(opinion=opinion, lambda_=0.1, t=float("inf"))


# --- Property-based tests ---
@given(opinion=opinion_strategy, t=st.floats(min_value=0.0, max_value=1000.0))
def test_decay_preserves_bdu_sum(opinion: Opinion, t: float) -> None:
    """Invariant: b + d + u = 1.0 after decay."""
    result = decay(opinion=opinion, lambda_=0.1, t=t)
    assert abs(result.b + result.d + result.u - 1.0) < PROP_TOL


@given(opinion=opinion_strategy, t=st.floats(min_value=0.0, max_value=1000.0))
def test_decay_belief_monotonically_decreases(opinion: Opinion, t: float) -> None:
    """Decay never increases belief or disbelief."""
    result = decay(opinion=opinion, lambda_=0.1, t=t)
    assert result.b <= opinion.b + PROP_TOL
    assert result.d <= opinion.d + PROP_TOL


@given(opinion=opinion_strategy, t=st.floats(min_value=0.0, max_value=1000.0))
def test_decay_uncertainty_monotonically_increases(opinion: Opinion, t: float) -> None:
    """Decay never decreases uncertainty."""
    result = decay(opinion=opinion, lambda_=0.1, t=t)
    assert result.u >= opinion.u - PROP_TOL


@given(opinion=opinion_strategy, t=st.floats(min_value=0.0, max_value=100.0))
def test_decay_preserves_belief_disbelief_ratio(opinion: Opinion, t: float) -> None:
    """Decay erodes conviction uniformly: the b/d ratio is preserved.

    Both b and d are multiplied by the same factor e^(-λt), so their
    ratio is invariant. Only check when both are large enough to avoid
    division-by-zero noise.
    """
    assume(opinion.b > 0.01)
    assume(opinion.d > 0.01)
    result = decay(opinion=opinion, lambda_=0.1, t=t)
    assume(result.d > PROP_TOL)
    original_ratio = opinion.b / opinion.d
    decayed_ratio = result.b / result.d
    assert abs(decayed_ratio - original_ratio) < PROP_TOL * 100
