"""Conflict metrics: Jøsang 2016 §4.8, Def. 4.20, Eqs. 4.61–4.63.

Verified against:
  - Jøsang (2016), Def. 4.20, Eqs. 4.61–4.63
  - uncertainty-datatypes (PyPI), sbool.py lines 156–163

Hand-calculated examples use BDU values from Jøsang §4.8 but with Lore's
BASE_RATE = 0.5. Jøsang's example uses a = 0.10, producing different projected
probabilities (P_B = 0.13, P_C = 0.69, PD = 0.56, DC ≈ 0.10). CC is
base-rate-independent and matches Jøsang exactly.
"""

from hypothesis import given

from lore.math._conflict import (
    compute_conjunctive_certainty,
    compute_degree_of_conflict,
    compute_projected_distance,
)
from lore.math._opinion import EPSILON, VACUOUS, Opinion
from tests.math.conftest import PROP_TOL, opinion_strategy


# ---------------------------------------------------------------------------
# Projected Distance (PD): Eq. 4.61
# ---------------------------------------------------------------------------
def test_pd_identical_opinions_is_zero() -> None:
    """PD between identical opinions is 0: no disagreement."""
    omega = Opinion(b=0.7, d=0.1, u=0.2)
    assert abs(compute_projected_distance(omega, omega)) < EPSILON


def test_pd_opposite_dogmatic_is_one() -> None:
    """PD between (1,0,0) and (0,1,0) is 1: maximal disagreement."""
    full_belief = Opinion(b=1.0, d=0.0, u=0.0)
    full_disbelief = Opinion(b=0.0, d=1.0, u=0.0)
    assert abs(compute_projected_distance(full_belief, full_disbelief) - 1.0) < EPSILON


def test_pd_hand_calculated() -> None:
    """BDU values from Jøsang §4.8, projected with Lore's a = 0.5.

    ω_B = (0.05, 0.15, 0.80) → P_B = 0.05 + 0.5·0.80 = 0.45
    ω_C = (0.68, 0.22, 0.10) → P_C = 0.68 + 0.5·0.10 = 0.73
    PD = |0.45 − 0.73| = 0.28

    (Jøsang uses a = 0.10 → P_B = 0.13, P_C = 0.69, PD = 0.56.)
    """
    omega_b = Opinion(b=0.05, d=0.15, u=0.80)
    omega_c = Opinion(b=0.68, d=0.22, u=0.10)
    assert abs(compute_projected_distance(omega_b, omega_c) - 0.28) < 1e-6


def test_pd_vacuous_pair_is_zero() -> None:
    """Two vacuous opinions have identical projections (both = base rate)."""
    assert abs(compute_projected_distance(VACUOUS, VACUOUS)) < EPSILON


# ---------------------------------------------------------------------------
# Conjunctive Certainty (CC): Eq. 4.62
# ---------------------------------------------------------------------------
def test_cc_both_dogmatic_is_one() -> None:
    """Both dogmatic → CC = (1-0)(1-0) = 1."""
    a = Opinion(b=1.0, d=0.0, u=0.0)
    b = Opinion(b=0.0, d=1.0, u=0.0)
    assert abs(compute_conjunctive_certainty(a, b) - 1.0) < EPSILON


def test_cc_one_vacuous_is_zero() -> None:
    """One vacuous → CC = (1-u_a)(1-1) = 0."""
    a = Opinion(b=0.7, d=0.1, u=0.2)
    assert abs(compute_conjunctive_certainty(a, VACUOUS)) < EPSILON


def test_cc_hand_calculated() -> None:
    """CC for §4.8 BDU values: (1−0.80)(1−0.10) = 0.18. Matches Jøsang exactly.
    CC is base-rate-independent."""
    omega_b = Opinion(b=0.05, d=0.15, u=0.80)
    omega_c = Opinion(b=0.68, d=0.22, u=0.10)
    assert abs(compute_conjunctive_certainty(omega_b, omega_c) - 0.18) < 1e-6


# ---------------------------------------------------------------------------
# Degree of Conflict (DC): Def. 4.20, Eq. 4.63
# ---------------------------------------------------------------------------
def test_dc_equals_pd_times_cc() -> None:
    """DC = PD · CC: Def. 4.20."""
    a = Opinion(b=0.8, d=0.1, u=0.1)
    b = Opinion(b=0.1, d=0.7, u=0.2)
    pd = compute_projected_distance(a, b)
    cc = compute_conjunctive_certainty(a, b)
    dc = compute_degree_of_conflict(a, b)
    assert abs(dc - pd * cc) < EPSILON


def test_dc_hand_calculated() -> None:
    """DC for §4.8 BDU values with a = 0.5: 0.28 · 0.18 = 0.0504.

    (Jøsang uses a = 0.10 → PD = 0.56, DC = 0.56 · 0.18 ≈ 0.10.)
    """
    omega_b = Opinion(b=0.05, d=0.15, u=0.80)
    omega_c = Opinion(b=0.68, d=0.22, u=0.10)
    assert abs(compute_degree_of_conflict(omega_b, omega_c) - 0.0504) < 1e-6


def test_dc_opposite_dogmatic() -> None:
    """Opposite dogmatic opinions: PD=1, CC=1, DC=1."""
    a = Opinion(b=1.0, d=0.0, u=0.0)
    b = Opinion(b=0.0, d=1.0, u=0.0)
    assert abs(compute_degree_of_conflict(a, b) - 1.0) < EPSILON


def test_dc_identical_opinions_is_zero() -> None:
    """No directional disagreement → DC = 0 regardless of certainty."""
    a = Opinion(b=0.7, d=0.1, u=0.2)
    assert abs(compute_degree_of_conflict(a, a)) < EPSILON


def test_dc_dogmatic_vs_vacuous_is_zero() -> None:
    """Dogmatic vs vacuous → CC = 0 → DC = 0 despite high PD."""
    dogmatic = Opinion(b=1.0, d=0.0, u=0.0)
    assert abs(compute_degree_of_conflict(dogmatic, VACUOUS)) < EPSILON


# ---------------------------------------------------------------------------
# Property-based tests
# ---------------------------------------------------------------------------
@given(a=opinion_strategy, b=opinion_strategy)
def test_pd_range_property(a: Opinion, b: Opinion) -> None:
    """PD ∈ [0, 1] for all opinion pairs."""
    pd = compute_projected_distance(a, b)
    assert -PROP_TOL <= pd <= 1.0 + PROP_TOL


@given(a=opinion_strategy, b=opinion_strategy)
def test_cc_range_property(a: Opinion, b: Opinion) -> None:
    """CC ∈ [0, 1] for all opinion pairs."""
    cc = compute_conjunctive_certainty(a, b)
    assert -PROP_TOL <= cc <= 1.0 + PROP_TOL


@given(a=opinion_strategy, b=opinion_strategy)
def test_dc_range_property(a: Opinion, b: Opinion) -> None:
    """DC ∈ [0, 1] for all opinion pairs."""
    dc = compute_degree_of_conflict(a, b)
    assert -PROP_TOL <= dc <= 1.0 + PROP_TOL


@given(a=opinion_strategy, b=opinion_strategy)
def test_dc_is_symmetric_property(a: Opinion, b: Opinion) -> None:
    """DC(a, b) = DC(b, a) for all opinion pairs."""
    assert abs(compute_degree_of_conflict(a, b) - compute_degree_of_conflict(b, a)) < PROP_TOL


@given(a=opinion_strategy, b=opinion_strategy)
def test_pd_is_symmetric_property(a: Opinion, b: Opinion) -> None:
    """PD(a, b) = PD(b, a) for all opinion pairs."""
    assert abs(compute_projected_distance(a, b) - compute_projected_distance(b, a)) < PROP_TOL


@given(a=opinion_strategy, b=opinion_strategy)
def test_cc_is_symmetric_property(a: Opinion, b: Opinion) -> None:
    """CC(a, b) = CC(b, a) for all opinion pairs."""
    assert abs(compute_conjunctive_certainty(a, b) - compute_conjunctive_certainty(b, a)) < PROP_TOL
