import pytest
from hypothesis import given

from lore.math.opinion import BASE_RATE, EPSILON, VACUOUS, Opinion
from tests.math.conftest import opinion_strategy


# --- System constant ---
class TestBaseRate:
    def test_base_rate_is_half(self) -> None:
        assert BASE_RATE == 0.5


# --- Construction ---
class TestConstruction:
    def test_valid_opinion(self) -> None:
        o = Opinion(b=0.3, d=0.2, u=0.5)
        assert o.b == 0.3
        assert o.d == 0.2
        assert o.u == 0.5

    def test_sum_not_one_raises(self) -> None:
        with pytest.raises(ValueError, match="must sum to 1"):
            Opinion(b=0.3, d=0.3, u=0.3)

    def test_negative_belief_raises(self) -> None:
        with pytest.raises(ValueError, match="must be in"):
            Opinion(b=-0.1, d=0.1, u=1.0)

    def test_negative_disbelief_raises(self) -> None:
        with pytest.raises(ValueError, match="must be in"):
            Opinion(b=0.1, d=-0.1, u=1.0)

    def test_negative_uncertainty_raises(self) -> None:
        with pytest.raises(ValueError, match="must be in"):
            Opinion(b=0.5, d=0.6, u=-0.1)

    def test_belief_above_one_raises(self) -> None:
        with pytest.raises(ValueError, match="must be in"):
            Opinion(b=1.1, d=0.0, u=-0.1)

    def test_nan_component_raises(self) -> None:
        with pytest.raises(ValueError, match="must be in"):
            Opinion(b=float("nan"), d=0.0, u=0.0)

    def test_inf_component_raises(self) -> None:
        with pytest.raises(ValueError, match="must be in"):
            Opinion(b=float("inf"), d=0.0, u=0.0)

    def test_epsilon_tolerance_accepts_near_one(self) -> None:
        # Construct values whose sum is 1.0 + EPSILON/2 — clearly inside
        # the EPSILON tolerance window in Opinion.__new__ but well above
        # any IEEE-754 rounding noise. Verified: b + d + u == 1.0000000005.
        b = 0.3
        d = 0.2
        u = 0.5 + EPSILON / 2
        o = Opinion(b=b, d=d, u=u)
        assert o.b == b
        assert o.d == d
        assert o.u == u

    def test_sum_outside_tolerance_raises(self) -> None:
        # Sum = 1.0 + 2 * EPSILON, strictly outside the tolerance window.
        # Pairs with test_epsilon_tolerance_accepts_near_one to nail down
        # both sides of the EPSILON boundary.
        with pytest.raises(ValueError, match="must sum to 1"):
            Opinion(b=0.3, d=0.2, u=0.5 + 2 * EPSILON)


# --- Projected probability ---
class TestProjectedProbability:
    def test_basic(self) -> None:
        o = Opinion(b=0.3, d=0.2, u=0.5)
        # P = 0.3 + 0.5 * 0.5 = 0.55
        assert abs(o.projected_probability - 0.55) < EPSILON

    def test_vacuous_equals_base_rate(self) -> None:
        o = Opinion(b=0.0, d=0.0, u=1.0)
        # P = 0 + 0.5 * 1.0 = 0.5 — no evidence, fall back to prior
        assert abs(o.projected_probability - BASE_RATE) < EPSILON

    def test_dogmatic_equals_belief(self) -> None:
        o = Opinion(b=0.7, d=0.3, u=0.0)
        # P = 0.7 + 0.5 * 0 = 0.7 — no uncertainty, belief IS the probability
        assert abs(o.projected_probability - 0.7) < EPSILON


# --- Properties ---
class TestProperties:
    def test_vacuous_opinion(self) -> None:
        o = Opinion(b=0.0, d=0.0, u=1.0)
        assert o.is_vacuous
        assert not o.is_dogmatic

    def test_dogmatic_opinion(self) -> None:
        o = Opinion(b=0.6, d=0.4, u=0.0)
        assert o.is_dogmatic
        assert not o.is_vacuous

    def test_neither_vacuous_nor_dogmatic(self) -> None:
        o = Opinion(b=0.3, d=0.2, u=0.5)
        assert not o.is_vacuous
        assert not o.is_dogmatic


# --- VACUOUS constant ---
class TestVacuous:
    def test_vacuous_constant(self) -> None:
        assert VACUOUS.b == 0.0
        assert VACUOUS.d == 0.0
        assert VACUOUS.u == 1.0

    def test_vacuous_is_vacuous(self) -> None:
        assert VACUOUS.is_vacuous

    def test_vacuous_projected_probability(self) -> None:
        assert abs(VACUOUS.projected_probability - BASE_RATE) < EPSILON


# --- Tuple semantics ---
class TestTupleSemantics:
    def test_opinion_unpacking_yields_bdu_components(self) -> None:
        opinion = Opinion(b=0.5, d=0.3, u=0.2)

        b, d, u = opinion

        assert abs(b - 0.5) < EPSILON
        assert abs(d - 0.3) < EPSILON
        assert abs(u - 0.2) < EPSILON

    def test_opinion_repr_shows_named_components(self) -> None:
        opinion = Opinion(b=0.5, d=0.3, u=0.2)

        r = repr(opinion)
        assert "Opinion" in r
        assert "0.5" in r
        assert "0.3" in r
        assert "0.2" in r


# --- Property-based tests ---
class TestPropertyBased:
    def test_opinion_is_immutable(self) -> None:
        opinion = Opinion(b=0.5, d=0.3, u=0.2)
        with pytest.raises(AttributeError):
            opinion.b = 0.9  # pyright: ignore[reportAttributeAccessIssue]

    @given(opinion=opinion_strategy)
    def test_projected_probability_in_unit_interval(self, opinion: Opinion) -> None:
        p = opinion.projected_probability
        assert -EPSILON <= p <= 1.0 + EPSILON

    @given(opinion=opinion_strategy)
    def test_bdu_sum_is_one(self, opinion: Opinion) -> None:
        assert abs(opinion.b + opinion.d + opinion.u - 1.0) < EPSILON


class TestOpinionClampsOnConstruction:
    """Constructor clamps slight floating-point overshoot to ``[0, 1]``.

    Each component is admitted in ``[-EPSILON, 1 + EPSILON]`` (cheap
    rounding tolerance for callers like ``decay`` and
    ``maximize_uncertainty``) and then clamped to the canonical interval
    before the invariant assertion. The audit (S3.5) flagged that the
    pre-clamp value was being stored verbatim; downstream consumers
    could see a fractionally-negative ``b``.
    """

    def test_opinion_with_negative_epsilon_input_clamps_to_zero(self) -> None:
        # Slightly negative ``b`` (within EPSILON), ``d`` adjusted to keep
        # the sum at 1.0 within tolerance.
        o = Opinion(b=-1e-12, d=0.6, u=0.4)
        assert o.b == 0.0
        assert o.d == 0.6
        assert o.u == 0.4

    def test_opinion_with_one_plus_epsilon_input_clamps_to_one(self) -> None:
        o = Opinion(b=1.0 + 1e-12, d=0.0, u=0.0)
        assert o.b == 1.0
        assert o.d == 0.0
        assert o.u == 0.0

    def test_opinion_outside_epsilon_tolerance_still_raises(self) -> None:
        # Beyond EPSILON — the bounds check fires before the clamp.
        with pytest.raises(ValueError, match="must be in"):
            Opinion(b=-1.0, d=0.0, u=2.0)
