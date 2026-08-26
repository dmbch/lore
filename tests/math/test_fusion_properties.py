"""Property tests for the ECBF underflow fallback path.

Audit S3.1: ``_acbf_pair``'s underflow guard at ``_fusion.py:61-62``
falls back to a γ=0.5 dogmatic average when ``u_A * u_B`` underflows
to zero in IEEE 754. ``fuse`` uses ``functools.reduce`` for the
non-all-dogmatic case, so for a borderline mix like
``u = [1e-200, 1e-200, 0.5]`` pairwise reduction can land on this
fallback for the first pair, emit ``u = 0`` as an intermediate, then
take Case I on the third pair. Order-dependent: the result is not the
N-ary 1/3-weight average that the docstring promises for the all-
dogmatic case.

The fix is the property test as a regression guard. Production tuning
keeps ``K >= 1`` so the inputs to fusion stay comfortably away from the
underflow knee, but we want to catch any future change that drives an
opinion to ``u ≈ 1e-200`` and lets the bug surface.

The property: pairwise reduce vs. an explicit N-ary mean produce equal
projected probabilities post-``maximize_uncertainty``. Any ECBF
intermediate with ``u = 0`` from the underflow fallback is corrected by
step 2 (uncertainty maximization), so we compare the final
projected probability: that's what every downstream consumer sees.
"""

from hypothesis import given, settings
from hypothesis import strategies as st

from lore.math._fusion import fuse
from lore.math._maximize import maximize_uncertainty
from lore.math._opinion import Opinion


def _non_dogmatic_opinion(b_share: float, d_share: float, u: float) -> Opinion:
    """Build an opinion with ``u >= 1e-6``: well above the underflow regime.

    ``b_share`` and ``d_share`` partition the remaining mass ``1 - u``; their
    relative magnitudes set the b/d split.
    """
    remaining = 1.0 - u
    total = b_share + d_share
    if total == 0.0:
        return Opinion(b=remaining / 2.0, d=remaining / 2.0, u=u)
    return Opinion(b=remaining * (b_share / total), d=remaining * (d_share / total), u=u)


_non_dogmatic_strategy = st.builds(
    _non_dogmatic_opinion,
    b_share=st.floats(min_value=0.0, max_value=1.0),
    d_share=st.floats(min_value=0.0, max_value=1.0),
    # u in [1e-6, 1.0]: comfortably above the underflow knee at ``u * u``
    # (the underflow regime starts around ``u ≈ 1e-162``).
    u=st.floats(min_value=1e-6, max_value=1.0),
)


# Borderline-dogmatic opinions: ``u`` near zero (so ``u * u`` underflows
# in IEEE 754 once we get past 2^-1074), with the rest of the mass split
# between ``b`` and ``d``. Crank ``u`` low enough to actually trigger
# the underflow guard, not just sit on the regular Case I path.
def _borderline_opinion(b_share: float, near_dogmatic_u: float) -> Opinion:
    u = near_dogmatic_u
    remaining = 1.0 - u
    return Opinion(b=remaining * b_share, d=remaining * (1.0 - b_share), u=u)


_borderline_strategy = st.builds(
    _borderline_opinion,
    b_share=st.floats(min_value=0.0, max_value=1.0),
    # 1e-200 is well past the IEEE 754 subnormal underflow knee for u*u.
    near_dogmatic_u=st.floats(min_value=1e-300, max_value=1e-200),
)


# A handful of mid-uncertainty opinions to mix in with the borderline ones.
# The audit's failure case is specifically a mix. Sample on the 2-simplex via
# Dirichlet-style normalisation, biased toward ``u >= 0.4`` so we exercise
# the "Case I lands here" branch alongside the borderline near-dogmatics.
def _mid_opinion(b: float, d: float, u_extra: float) -> Opinion:
    u = 0.4 + 0.6 * u_extra
    remainder = 1.0 - u
    total_bd = b + d
    # Subnormal ``total_bd`` triggers a multiply-then-divide ordering bug
    # (``(remainder * d) / total_bd`` underflows the intermediate, then
    # rounds the division to 1.0 of any subnormal). Treat anything below
    # the lower normal range as effectively zero: uniformly split.
    if total_bd < 1e-300:
        return Opinion(b=remainder / 2.0, d=remainder / 2.0, u=u)
    return Opinion(
        b=remainder * (b / total_bd),
        d=remainder * (d / total_bd),
        u=u,
    )


_mid_strategy = st.builds(
    _mid_opinion,
    # Exclude subnormals: they give ``b + d`` weird rounding behaviour
    # below the normal range that's irrelevant to the underflow guard
    # under test.
    b=st.floats(min_value=0.0, max_value=1.0).filter(lambda x: x == 0.0 or x >= 1e-300),
    d=st.floats(min_value=0.0, max_value=1.0).filter(lambda x: x == 0.0 or x >= 1e-300),
    u_extra=st.floats(min_value=0.0, max_value=1.0),
)


def _n_ary_mean(opinions: list[Opinion]) -> Opinion:
    """Explicit equal-weight N-ary average: the all-dogmatic Case II formula."""
    n = len(opinions)
    return Opinion(
        b=sum(o.b for o in opinions) / n,
        d=sum(o.d for o in opinions) / n,
        u=0.0,
    )


class TestAcbfPairwiseVsNAryEquivalence:
    """The underflow fallback's intermediate ``u = 0`` is corrected by step 2.

    No matter what order pairwise reduction produces ``u = 0`` for, the
    final projected probability after ``maximize_uncertainty`` matches
    what a direct N-ary all-dogmatic mean would have produced, because
    both paths preserve ``P`` and ECBF step 2 maps ``P`` to a unique
    uncertainty-maximised opinion.
    """

    @given(opinions=st.lists(_borderline_strategy, min_size=2, max_size=8))
    @settings(max_examples=100, deadline=None)
    def test_all_borderline_dogmatic_match_n_ary_via_projected_probability(
        self, opinions: list[Opinion]
    ) -> None:
        # The pairwise path may go through the underflow guard; the
        # N-ary all-dogmatic path is structural. Both are passed through
        # ECBF (which applies maximize_uncertainty) and we compare P.
        pairwise = fuse(opinions)
        n_ary = maximize_uncertainty(_n_ary_mean(opinions))
        assert abs(pairwise.projected_probability - n_ary.projected_probability) < 1e-6

    @given(
        borderline=st.lists(_borderline_strategy, min_size=1, max_size=4),
        mid=st.lists(_mid_strategy, min_size=1, max_size=4),
    )
    @settings(max_examples=100, deadline=None)
    def test_mixed_borderline_and_mid_remains_in_unit_interval(
        self, borderline: list[Opinion], mid: list[Opinion]
    ) -> None:
        # The exact case from S3.1: ``u = [1e-200, 1e-200, 0.5]`` and
        # similar shapes. Order-independence is too strong to assert
        # directly (the audit acknowledges the path is order-dependent
        # before maximize_uncertainty), but ``fuse`` must always return
        # a valid opinion with ``P in [0, 1]``.
        result = fuse([*borderline, *mid])
        assert 0.0 <= result.projected_probability <= 1.0
        # Sum-to-1 invariant: should be enforced by Opinion construction
        # in any case, but the property scan covers ranges the
        # parameterized tests don't.
        assert abs(result.b + result.d + result.u - 1.0) < 1e-6


class TestUnderflowRegimeRouting:
    """Lock in that ``fuse`` routes by the algebraic underflow predicate.

    Audit S2.8: the IEEE-754 ``u * u == 0.0`` proxy at ``_fusion.py:111``
    is platform-dependent: FTZ/DAZ FP-environment flags and fast-math
    contexts can flush subnormals so that the predicate fires at
    different ``u`` than the algebra predicts. These tests pin the
    routing decisions to observable outputs so the algebraic helper
    cannot drift away from the canonical IEEE-754 semantics.
    """

    def test_fuse_routes_through_underflow_regime_when_all_u_subnormal_squared(
        self,
    ) -> None:
        # Three asymmetric opinions, all with ``u = 1e-200``, well past
        # the IEEE-754 underflow knee for ``u * u`` (2^-1074). With
        # asymmetric ``(b, d)`` the N-ary equal-weight mean (Case II,
        # ``_fusion.py:112``) and the chained pairwise reduction produce
        # observably different projected probabilities, so the chosen
        # path is visible in the output.
        u = 1e-200
        opinions = [
            Opinion(b=0.9, d=1.0 - 0.9 - u, u=u),
            Opinion(b=0.7, d=1.0 - 0.7 - u, u=u),
            Opinion(b=0.1, d=1.0 - 0.1 - u, u=u),
        ]
        # N-ary equal-weight mean: b̄ = (0.9 + 0.7 + 0.1) / 3 ≈ 0.5667;
        # ``maximize_uncertainty`` preserves ``P`` so the post-fuse
        # projected probability lands at the structural mean. A broken
        # router that drove these through chained pairwise reduction
        # would land near 0.8 instead (the pairwise underflow guard at
        # ``_fusion.py:61`` emits an intermediate ``u = 0`` after the
        # first pair, and the third opinion's Case I formula then
        # weights the first-pair belief at full strength, see audit
        # S3.1).
        expected_p = (0.9 + 0.7 + 0.1) / 3
        result = fuse(opinions)
        assert abs(result.projected_probability - expected_p) < 1e-9

    def test_fuse_uses_standard_acbf_at_moderate_u(self) -> None:
        # ``u = 1e-10`` sits comfortably above the underflow knee
        # (``u * u = 1e-20`` is a normal IEEE-754 double). It is also
        # below ``Opinion.EPSILON`` (``1e-9``), so a rejected proposal
        # to gate the all-dogmatic route on ``Opinion.is_dogmatic``
        # would misroute these inputs through the equal-weight mean.
        # Mix one near-dogmatic opinion with one moderate-``u`` opinion
        # so the two routes give visibly different projected
        # probabilities.
        near_dogmatic = Opinion(b=0.9, d=1.0 - 0.9 - 1e-10, u=1e-10)
        moderate = Opinion(b=0.1, d=0.4, u=0.5)
        # Pairwise ACBF Case I (Eq. 12.14): the near-dogmatic
        # opinion's tiny ``u`` weights ``moderate``'s belief mass
        # negligibly while ``moderate``'s ``u = 0.5`` carries
        # ``near_dogmatic``'s belief mass at full strength. ``P``
        # collapses toward the near-dogmatic opinion's belief
        # ``b = 0.9``.
        result = fuse([near_dogmatic, moderate])
        assert abs(result.projected_probability - 0.9) < 1e-6

    def test_fuse_mixed_underflow_and_moderate_u_partitions_to_underflow(self) -> None:
        # The S3.1 mix: one borderline-dogmatic and one moderate-``u``
        # opinion. ``fuse`` partitions on the underflow predicate before
        # routing: with ``0 < dog_count < n`` the non-dogmatic minority
        # is dropped and the result is the N-ary mean over the
        # dogmatic subset only (here, just the borderline).
        u_underflow = 1e-200
        borderline = Opinion(b=0.8, d=1.0 - 0.8 - u_underflow, u=u_underflow)
        moderate = Opinion(b=0.2, d=0.3, u=0.5)
        # Partition: dogmatic subset = [borderline]; recurses into the
        # single-input path, which is ``maximize_uncertainty(borderline)``
        # Projected probability is preserved at ``P ≈ 0.8``. A broken
        # router that took pairwise Case I across the mix would also
        # land near 0.8 here (the moderate ``u`` would carry the
        # borderline belief at full strength), so this test pins the
        # partition by code path; the equal-weight-mean misroute
        # (``P = 0.5``) is the failure mode it discriminates against.
        result = fuse([borderline, moderate])
        assert abs(result.projected_probability - 0.8) < 1e-6


class TestNonDogmaticUndogmatism:
    """The K≥1 default-pipeline guarantee: non-dogmatic inputs stay non-dogmatic.

    ``docs/logic.md`` (§ACBF Properties, §Hypothesis Maturity) commits that
    the production pipeline never produces a dogmatic fused opinion when no
    input is dogmatic. ECBF preserves uncertainty: Case I's ``u`` formula
    yields ``u > 0`` whenever every input has ``u > 0`` and ``maximize_
    uncertainty`` cannot shrink ``u`` below the lifted projected probability.
    """

    @given(opinions=st.lists(_non_dogmatic_strategy, min_size=2, max_size=10))
    @settings(max_examples=100, deadline=None)
    def test_fuse_preserves_uncertainty_when_all_inputs_non_dogmatic(
        self, opinions: list[Opinion]
    ) -> None:
        result = fuse(opinions)
        assert result.u > 0.0
