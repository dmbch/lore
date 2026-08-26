"""Tests for trust discounting: c_discounted = P_effective * c_raw.

Scalar shortcut of Josang's Def. 14.6, valid for uncertainty-maximized opinions.
See docs/logic.md §Trust Discounting: The Scalar Shortcut.
"""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from lore.math._confidence import to_confidence, to_opinion
from lore.math._discount import discount
from lore.math._opinion import EPSILON, Opinion

from .conftest import PROP_TOL

# --- Strategies ---

confidence_strategy = st.floats(min_value=-1.0, max_value=1.0)
p_effective_strategy = st.floats(min_value=0.0, max_value=1.0)


# --- Hand-calculated cases ---


def test_discount_p_effective_one_confidence_unchanged() -> None:
    """P_effective=1.0 is transparent: no damping."""
    result = discount(confidence=0.8, p_effective=1.0)

    assert abs(result - 0.8) < EPSILON


def test_discount_p_effective_zero_returns_vacuous() -> None:
    """P_effective=0.0 collapses any opinion to vacuous (c=0.0)."""
    result = discount(confidence=0.8, p_effective=0.0)

    assert abs(result - 0.0) < EPSILON


def test_discount_quarter_strength_positive_confidence() -> None:
    """Cold-start: M=0.5, t=0.5 → P_effective=0.25. c=0.8 → 0.2."""
    result = discount(confidence=0.8, p_effective=0.25)

    assert abs(result - 0.2) < EPSILON


def test_discount_negative_confidence_sign_preserved() -> None:
    """Negative stays negative: 0.5 * (-0.6) = -0.3."""
    result = discount(confidence=-0.6, p_effective=0.5)

    assert abs(result - (-0.3)) < EPSILON


def test_discount_zero_confidence_stays_zero() -> None:
    """Vacuous is a fixed point: P * 0.0 = 0.0 for any P."""
    result = discount(confidence=0.0, p_effective=0.75)

    assert abs(result - 0.0) < EPSILON


def test_discount_high_positive_confidence_at_half_trust() -> None:
    """Near-dogmatic opinion halved: 0.5 * 0.99 = 0.495."""
    result = discount(confidence=0.99, p_effective=0.5)

    assert abs(result - 0.495) < EPSILON


def test_discount_high_negative_confidence_at_half_trust() -> None:
    """Symmetric: 0.5 * (-0.99) = -0.495."""
    result = discount(confidence=-0.99, p_effective=0.5)

    assert abs(result - (-0.495)) < EPSILON


# --- BDU roundtrip verification (Def. 14.6) ---


def test_discount_roundtrip_positive_confidence_matches_bdu_form() -> None:
    """Scalar shortcut matches Def. 14.6 in BDU space for positive c."""
    c_raw = 0.8
    p_eff = 0.25

    scalar_result = discount(confidence=c_raw, p_effective=p_eff)

    source = to_opinion(c_raw)
    b_disc = p_eff * source.b
    d_disc = p_eff * source.d
    u_disc = 1.0 - p_eff * (1.0 - source.u)
    bdu_result = to_confidence(Opinion(b=b_disc, d=d_disc, u=u_disc))

    assert abs(scalar_result - bdu_result) < EPSILON


def test_discount_roundtrip_negative_confidence_matches_bdu_form() -> None:
    """Scalar shortcut matches Def. 14.6 in BDU space for negative c."""
    c_raw = -0.6
    p_eff = 0.5

    scalar_result = discount(confidence=c_raw, p_effective=p_eff)

    source = to_opinion(c_raw)
    b_disc = p_eff * source.b
    d_disc = p_eff * source.d
    u_disc = 1.0 - p_eff * (1.0 - source.u)
    bdu_result = to_confidence(Opinion(b=b_disc, d=d_disc, u=u_disc))

    assert abs(scalar_result - bdu_result) < EPSILON


# --- Validation ---


def test_discount_dogmatic_positive_confidence_returns_valid_result() -> None:
    """Trust discounting is the bound, not input validation: 0.5 * 1.0 = 0.5."""
    result = discount(confidence=1.0, p_effective=0.5)

    assert abs(result - 0.5) < EPSILON


def test_discount_dogmatic_negative_confidence_returns_valid_result() -> None:
    """Trust discounting is the bound, not input validation: 0.5 * (-1.0) = -0.5."""
    result = discount(confidence=-1.0, p_effective=0.5)

    assert abs(result - (-0.5)) < EPSILON


def test_discount_p_effective_above_one_rejected() -> None:
    """P_effective > 1 would amplify instead of damping."""
    with pytest.raises(ValueError, match="p_effective must be in"):
        discount(confidence=0.5, p_effective=1.5)


def test_discount_p_effective_negative_rejected() -> None:
    """Negative P_effective would flip the sign: nonsensical."""
    with pytest.raises(ValueError, match="p_effective must be in"):
        discount(confidence=0.5, p_effective=-0.1)


def test_discount_confidence_above_one_rejected() -> None:
    """Confidence > 1 is outside the mathematical domain [-1, 1]."""
    with pytest.raises(ValueError, match="confidence must be in"):
        discount(confidence=1.5, p_effective=0.5)


def test_discount_confidence_below_negative_one_rejected() -> None:
    """Confidence < -1 is outside the mathematical domain [-1, 1]."""
    with pytest.raises(ValueError, match="confidence must be in"):
        discount(confidence=-1.5, p_effective=0.5)


def test_discount_nan_confidence_rejected() -> None:
    """NaN confidence would silently poison downstream computation."""
    with pytest.raises(ValueError, match="confidence must be in"):
        discount(confidence=float("nan"), p_effective=0.5)


def test_discount_inf_confidence_rejected() -> None:
    """Infinite confidence is not in the mathematical domain."""
    with pytest.raises(ValueError, match="confidence must be in"):
        discount(confidence=float("inf"), p_effective=0.5)


def test_discount_nan_p_effective_rejected() -> None:
    """NaN p_effective would silently poison downstream computation."""
    with pytest.raises(ValueError, match="p_effective must be in"):
        discount(confidence=0.5, p_effective=float("nan"))


def test_discount_inf_p_effective_rejected() -> None:
    """Infinite p_effective would amplify instead of damping."""
    with pytest.raises(ValueError, match="p_effective must be in"):
        discount(confidence=0.5, p_effective=float("inf"))


# --- Property-based tests ---


@given(c=confidence_strategy, p=p_effective_strategy)
def test_discount_direction_preserved(c: float, p: float) -> None:
    """Positive stays positive, negative stays negative."""
    result = discount(confidence=c, p_effective=p)

    if c > 0.0:
        assert result >= 0.0 - PROP_TOL
    elif c < 0.0:
        assert result <= 0.0 + PROP_TOL
    else:
        assert abs(result) < PROP_TOL


@given(c=confidence_strategy, p=p_effective_strategy)
def test_discount_magnitude_never_amplified(c: float, p: float) -> None:
    """Discounting pushes toward vacuous, never away from it."""
    result = discount(confidence=c, p_effective=p)

    assert abs(result) <= abs(c) + PROP_TOL


@given(c=confidence_strategy, p=p_effective_strategy)
def test_discount_result_within_valid_range(c: float, p: float) -> None:
    """Result stays within [-1, 1]: discount cannot amplify beyond input domain."""
    result = discount(confidence=c, p_effective=p)

    assert result >= -1.0 - PROP_TOL
    assert result <= 1.0 + PROP_TOL


@given(c=confidence_strategy, p=p_effective_strategy)
def test_discount_roundtrip_scalar_matches_bdu_form(c: float, p: float) -> None:
    """Scalar shortcut ≡ Def. 14.6 BDU form for all uncertainty-maximized inputs."""
    scalar_result = discount(confidence=c, p_effective=p)

    source = to_opinion(c)
    b_disc = p * source.b
    d_disc = p * source.d
    u_disc = 1.0 - p * (1.0 - source.u)
    discounted = Opinion(b=b_disc, d=d_disc, u=u_disc)
    bdu_result = to_confidence(discounted)

    assert abs(scalar_result - bdu_result) < PROP_TOL
