from hypothesis import given

from lore.math.maximize import maximize_uncertainty
from lore.math.opinion import EPSILON, VACUOUS, Opinion
from tests.math.conftest import PROP_TOL, opinion_strategy


class TestMaximizeHandCalculated:
    """Verify maximize_uncertainty against hand-calculated results.

    Formula (Jøsang Eq. 3.27, specialized to binomial with a=0.5):
      P = b + 0.5 * u
      ü = 2 * min(P, 1 - P)
      b̈ = P - 0.5 * ü
      d̈ = (1 - P) - 0.5 * ü
    """

    def test_belief_leaning(self) -> None:
        """Opinion leaning toward belief: disbelief absorbed into uncertainty."""
        # P = 0.8 + 0.5*0.1 = 0.85
        # ü = 2*min(0.85, 0.15) = 0.3
        # b̈ = 0.85 - 0.15 = 0.7, d̈ = 0.15 - 0.15 = 0.0
        result = maximize_uncertainty(Opinion(b=0.8, d=0.1, u=0.1))
        assert abs(result.b - 0.7) < EPSILON
        assert abs(result.d - 0.0) < EPSILON
        assert abs(result.u - 0.3) < EPSILON

    def test_disbelief_leaning(self) -> None:
        """Opinion leaning toward disbelief: belief absorbed into uncertainty."""
        # P = 0.1 + 0.5*0.1 = 0.15
        # ü = 2*min(0.15, 0.85) = 0.3
        # b̈ = 0.15 - 0.15 = 0.0, d̈ = 0.85 - 0.15 = 0.7
        result = maximize_uncertainty(Opinion(b=0.1, d=0.8, u=0.1))
        assert abs(result.b - 0.0) < EPSILON
        assert abs(result.d - 0.7) < EPSILON
        assert abs(result.u - 0.3) < EPSILON

    def test_vacuous_unchanged(self) -> None:
        """Vacuous opinion is already maximally uncertain."""
        result = maximize_uncertainty(VACUOUS)
        assert abs(result.b - 0.0) < EPSILON
        assert abs(result.d - 0.0) < EPSILON
        assert abs(result.u - 1.0) < EPSILON

    def test_absolute_true_unchanged(self) -> None:
        """Absolute TRUE (b=1) cannot be uncertainty-maximized."""
        result = maximize_uncertainty(Opinion(b=1.0, d=0.0, u=0.0))
        assert abs(result.b - 1.0) < EPSILON
        assert abs(result.d - 0.0) < EPSILON
        assert abs(result.u - 0.0) < EPSILON

    def test_absolute_false_unchanged(self) -> None:
        """Absolute FALSE (d=1) cannot be uncertainty-maximized."""
        result = maximize_uncertainty(Opinion(b=0.0, d=1.0, u=0.0))
        assert abs(result.b - 0.0) < EPSILON
        assert abs(result.d - 1.0) < EPSILON
        assert abs(result.u - 0.0) < EPSILON

    def test_already_maximized(self) -> None:
        """Opinion with d=0 and b+u=1 is already on the simplex boundary."""
        result = maximize_uncertainty(Opinion(b=0.5, d=0.0, u=0.5))
        assert abs(result.b - 0.5) < EPSILON
        assert abs(result.d - 0.0) < EPSILON
        assert abs(result.u - 0.5) < EPSILON

    def test_balanced_becomes_vacuous(self) -> None:
        """When P = 0.5 exactly, all mass converts to uncertainty."""
        # P = 0.45 + 0.5*0.1 = 0.5
        # ü = 2*min(0.5, 0.5) = 1.0
        result = maximize_uncertainty(Opinion(b=0.45, d=0.45, u=0.1))
        assert abs(result.b - 0.0) < EPSILON
        assert abs(result.d - 0.0) < EPSILON
        assert abs(result.u - 1.0) < EPSILON

    def test_dogmatic_belief_heavy(self) -> None:
        """Dogmatic opinion with b > d: disbelief absorbed."""
        # P = 0.7 + 0 = 0.7
        # ü = 2*min(0.7, 0.3) = 0.6
        # b̈ = 0.7 - 0.3 = 0.4, d̈ = 0.3 - 0.3 = 0.0
        result = maximize_uncertainty(Opinion(b=0.7, d=0.3, u=0.0))
        assert abs(result.b - 0.4) < EPSILON
        assert abs(result.d - 0.0) < EPSILON
        assert abs(result.u - 0.6) < EPSILON


class TestMaximizePropertyBased:
    @given(opinion=opinion_strategy)
    def test_preserves_projected_probability(self, opinion: Opinion) -> None:
        """P(maximize(a)) == P(a): the defining property."""
        result = maximize_uncertainty(opinion)
        assert abs(result.projected_probability - opinion.projected_probability) < PROP_TOL

    @given(opinion=opinion_strategy)
    def test_preserves_bdu_sum(self, opinion: Opinion) -> None:
        result = maximize_uncertainty(opinion)
        assert abs(result.b + result.d + result.u - 1.0) < PROP_TOL

    @given(opinion=opinion_strategy)
    def test_min_of_b_d_near_zero(self, opinion: Opinion) -> None:
        """Uncertainty-maximized binomial opinions have min(b, d) ≈ 0."""
        result = maximize_uncertainty(opinion)
        assert min(result.b, result.d) < PROP_TOL

    @given(opinion=opinion_strategy)
    def test_uncertainty_does_not_decrease(self, opinion: Opinion) -> None:
        """Uncertainty maximization can only increase (or preserve) u."""
        result = maximize_uncertainty(opinion)
        assert result.u >= opinion.u - PROP_TOL

    @given(opinion=opinion_strategy)
    def test_idempotent(self, opinion: Opinion) -> None:
        """maximize(maximize(a)) == maximize(a)."""
        once = maximize_uncertainty(opinion)
        twice = maximize_uncertainty(once)
        assert abs(twice.b - once.b) < PROP_TOL
        assert abs(twice.d - once.d) < PROP_TOL
        assert abs(twice.u - once.u) < PROP_TOL
