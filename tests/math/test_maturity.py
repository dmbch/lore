"""Tests for hypothesis maturity: M = N_O / (N_O + K).

Saturation function over oracle diversity. The maturity factor enters
P_effective = M * t_oracle, which feeds trust discounting (Def. 14.6).
"""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from lore.math.maturity import compute_maturity
from lore.math.opinion import EPSILON

from .conftest import PROP_TOL

# --- Strategies ---

# n_oracle_prior: non-negative integer (0 = first oracle on this hypothesis)
n_oracle_prior_strategy = st.integers(min_value=0, max_value=1000)

# K: non-negative float (0 = transparent maturity, default 1)
k_strategy = st.floats(min_value=0.0, max_value=100.0)


# --- Hand-calculated cases ---


def test_maturity_first_oracle_half_strength() -> None:
    """N_O=1, K=1 → M=0.5. One phantom skeptic is always in the room."""
    result = compute_maturity(n_oracle_prior=0, k=1.0)

    assert abs(result - 0.5) < EPSILON


def test_maturity_two_oracles_two_thirds() -> None:
    """N_O=2, K=1 → M=2/3."""
    result = compute_maturity(n_oracle_prior=1, k=1.0)

    assert abs(result - 2.0 / 3.0) < EPSILON


def test_maturity_five_oracles_five_sixths() -> None:
    """N_O=5, K=1 → M=5/6."""
    result = compute_maturity(n_oracle_prior=4, k=1.0)

    assert abs(result - 5.0 / 6.0) < EPSILON


def test_maturity_ten_oracles_approaches_one() -> None:
    """N_O=10, K=1 → M=10/11 ≈ 0.91. Diminishing returns."""
    result = compute_maturity(n_oracle_prior=9, k=1.0)

    assert abs(result - 10.0 / 11.0) < EPSILON


def test_maturity_k_zero_always_transparent() -> None:
    """K=0 disables the phantom skeptic: M=1.0 regardless of oracle count."""
    assert abs(compute_maturity(n_oracle_prior=0, k=0.0) - 1.0) < EPSILON
    assert abs(compute_maturity(n_oracle_prior=1, k=0.0) - 1.0) < EPSILON
    assert abs(compute_maturity(n_oracle_prior=100, k=0.0) - 1.0) < EPSILON


def test_maturity_high_k_suppresses_early_oracles() -> None:
    """K=10 requires many oracles before maturity lifts. First oracle: M=1/11."""
    result = compute_maturity(n_oracle_prior=0, k=10.0)

    assert abs(result - 1.0 / 11.0) < EPSILON


# --- Validation ---


def test_maturity_negative_oracle_count_rejected() -> None:
    """Negative oracle count is nonsensical."""
    with pytest.raises(ValueError, match="n_oracle_prior must be non-negative"):
        compute_maturity(n_oracle_prior=-1, k=1.0)


def test_maturity_negative_k_rejected() -> None:
    """Negative K would produce M > 1, amplifying instead of damping."""
    with pytest.raises(ValueError, match="k must be non-negative"):
        compute_maturity(n_oracle_prior=0, k=-0.5)


# --- Property-based tests ---


@given(n=n_oracle_prior_strategy, k=k_strategy)
def test_maturity_always_in_zero_one_exclusive_inclusive(n: int, k: float) -> None:
    """Always positive (no deadlock) and at most 1.0."""
    result = compute_maturity(n_oracle_prior=n, k=k)

    assert result > 0.0
    assert result <= 1.0 + PROP_TOL


@given(k=k_strategy)
def test_maturity_monotonically_increasing_in_oracle_count(k: float) -> None:
    """More oracles → higher maturity, but with diminishing returns."""
    values = [compute_maturity(n_oracle_prior=n, k=k) for n in range(20)]

    for i in range(1, len(values)):
        assert values[i] >= values[i - 1] - PROP_TOL
