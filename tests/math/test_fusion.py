import itertools

from hypothesis import given
from hypothesis import strategies as st

from lore.math.fusion import _acbf_pair, fuse  # pyright: ignore[reportPrivateUsage]
from lore.math.maximize import maximize_uncertainty
from lore.math.opinion import EPSILON, VACUOUS, Opinion
from tests.math.conftest import PROP_TOL, opinion_strategy


class TestFuseHandCalculated:
    """Verify fuse() (ECBF) against hand-calculated results.

    ECBF = ACBF (Jøsang Def. 12.5) + uncertainty maximization (Eq. 3.27).
    ACBF intermediate results verified against N-ary formula from
    subjective-logic-library (joseoliveirajr) reference implementation.
    """

    # --- Edge cases ---

    def test_empty_returns_vacuous(self) -> None:
        result = fuse([])
        assert abs(result.b - 0.0) < EPSILON
        assert abs(result.d - 0.0) < EPSILON
        assert abs(result.u - 1.0) < EPSILON

    def test_single_returns_maximized(self) -> None:
        """fuse([a]) = maximize_uncertainty(a), not a itself."""
        a = Opinion(b=0.7, d=0.2, u=0.1)
        result = fuse([a])
        # P = 0.7 + 0.5*0.1 = 0.75
        # ü = 2*min(0.75, 0.25) = 0.5
        # b̈ = 0.75 - 0.25 = 0.5, d̈ = 0.0
        assert abs(result.b - 0.5) < EPSILON
        assert abs(result.d - 0.0) < EPSILON
        assert abs(result.u - 0.5) < EPSILON

    # --- Two-source ECBF (ACBF Def. 12.5 Case I + Eq. 3.27) ---

    def test_two_agreeing_opinions(self) -> None:
        """Two opinions that mostly agree: evidence accumulates.

        ACBF: κ=0.44, (0.75, 5/44, 3/22). P = 9/11.
        Maximize: ü=4/11, b̈=7/11, d̈=0.
        """
        a = Opinion(b=0.7, d=0.1, u=0.2)
        b = Opinion(b=0.6, d=0.1, u=0.3)
        result = fuse([a, b])

        assert abs(result.b - 7.0 / 11.0) < EPSILON
        assert abs(result.d - 0.0) < EPSILON
        assert abs(result.u - 4.0 / 11.0) < EPSILON

    def test_two_contradictory_cancel(self) -> None:
        """Contradicting testimonies cancel: the defining ECBF behavior.

        ACBF: symmetric, P = 0.5. Maximize: vacuous.
        'Two contradicting testimonies cancel each other out' (Jøsang §12.3.2).
        """
        a = Opinion(b=0.8, d=0.1, u=0.1)
        b = Opinion(b=0.1, d=0.8, u=0.1)
        result = fuse([a, b])

        assert abs(result.b - 0.0) < EPSILON
        assert abs(result.d - 0.0) < EPSILON
        assert abs(result.u - 1.0) < EPSILON

    def test_two_both_dogmatic(self) -> None:
        """Both dogmatic: Case II: average, then maximize.

        ACBF Case II (Eq. 12.15, γ=0.5): (0.7, 0.3, 0.0). P=0.7.
        Maximize: ü=0.6, b̈=0.4, d̈=0.0.
        """
        a = Opinion(b=0.8, d=0.2, u=0.0)
        b = Opinion(b=0.6, d=0.4, u=0.0)
        result = fuse([a, b])

        assert abs(result.b - 0.4) < EPSILON
        assert abs(result.d - 0.0) < EPSILON
        assert abs(result.u - 0.6) < EPSILON

    def test_three_all_dogmatic(self) -> None:
        """Three dogmatic opinions: N-ary equal weights (γ=1/3 each).

        ACBF Case II (Eq. 12.15): b = (0.9+0.9+0.3)/3 = 0.7, d = 0.3, u = 0.
        Maximize: P=0.7, ü=0.6, b̈=0.4, d̈=0.0.
        """
        a = Opinion(b=0.9, d=0.1, u=0.0)
        b = Opinion(b=0.9, d=0.1, u=0.0)
        c = Opinion(b=0.3, d=0.7, u=0.0)
        result = fuse([a, b, c])

        assert abs(result.b - 0.4) < EPSILON
        assert abs(result.d - 0.0) < EPSILON
        assert abs(result.u - 0.6) < EPSILON

    def test_two_one_dogmatic_dominates(self) -> None:
        """A dogmatic opinion absorbs a non-dogmatic one in ACBF.

        When u_A=0: κ=u_B, result = (b_A, d_A, 0). Dogmatic dominates.
        Then maximize: P=0.9, ü=0.2, b̈=0.8, d̈=0.0.
        """
        dogmatic = Opinion(b=0.9, d=0.1, u=0.0)
        uncertain = Opinion(b=0.3, d=0.2, u=0.5)
        result = fuse([dogmatic, uncertain])

        assert abs(result.b - 0.8) < EPSILON
        assert abs(result.d - 0.0) < EPSILON
        assert abs(result.u - 0.2) < EPSILON

    def test_three_two_dogmatic_one_nondogmatic(self) -> None:
        """Dogmatic opinions absorb non-dogmatic ones in ACBF.

        Pairwise reduce: _acbf_pair(dog1, dog2) → Case II average (0.7, 0.3, 0).
        Then _acbf_pair((0.7, 0.3, 0), nondog) → Case I with u_A=0: κ=u_B,
        so result = (b_A, d_A, 0). The non-dogmatic opinion is absorbed because
        the dogmatic intermediate has u=0, zeroing its contribution in κ.
        Effective average: avg(0.9, 0.5) = 0.7, d = 0.3, u = 0.
        Maximize: P=0.7, ü=0.6, b̈=0.4, d̈=0.0.
        """
        dog1 = Opinion(b=0.9, d=0.1, u=0.0)
        dog2 = Opinion(b=0.5, d=0.5, u=0.0)
        nondog = Opinion(b=0.1, d=0.1, u=0.8)
        result = fuse([dog1, dog2, nondog])

        assert abs(result.b - 0.4) < EPSILON
        assert abs(result.d - 0.0) < EPSILON
        assert abs(result.u - 0.6) < EPSILON

    def test_two_vacuous_returns_vacuous(self) -> None:
        result = fuse([VACUOUS, VACUOUS])
        assert abs(result.b - 0.0) < EPSILON
        assert abs(result.d - 0.0) < EPSILON
        assert abs(result.u - 1.0) < EPSILON

    def test_fuse_single_vacuous_returns_vacuous(self) -> None:
        """fuse([VACUOUS]) == VACUOUS: guards the single-opinion path.

        maximize_uncertainty(VACUOUS) is VACUOUS; a regression here would
        signal a maximize_uncertainty drift, since fuse([a]) routes through
        that operator unchanged.
        """
        result = fuse([VACUOUS])
        assert abs(result.b - VACUOUS.b) < EPSILON
        assert abs(result.d - VACUOUS.d) < EPSILON
        assert abs(result.u - VACUOUS.u) < EPSILON

    def test_fuse_mixed_dogmatic_same_direction_uses_dogmatic_subset(self) -> None:
        """Mixed dogmatic + non-dogmatic partitions to the dogmatic subset.

        Aggregatio's ``cumulativeCollectionFuse`` (Jøsang, Wang & Zhang,
        FUSION 2017, Eqs. 16-17): when ≥1 input is dogmatic and
        ≥1 is non-dogmatic, the N-ary equal-weight mean runs over the
        dogmatic subset only. Same-direction is the strict pin: the
        cancelling-pair case would coincidentally pass via uncertainty
        maximization to VACUOUS.
        """
        dog1 = Opinion(b=1.0, d=0.0, u=0.0)
        dog2 = Opinion(b=1.0, d=0.0, u=0.0)
        nondog = Opinion(b=0.3, d=0.2, u=0.5)

        full = fuse([dog1, dog2, nondog])
        subset = fuse([dog1, dog2])

        assert abs(full.b - subset.b) < EPSILON
        assert abs(full.d - subset.d) < EPSILON
        assert abs(full.u - subset.u) < EPSILON

    def test_fuse_mixed_near_dogmatic_partitions_to_dogmatic_subset(self) -> None:
        """Underflow-regime opinions participate in the dogmatic partition.

        The partition predicate is ``_u_in_underflow_regime`` (consistent
        with the all-dogmatic short-circuit), so an opinion with
        ``u = 1e-200`` counts as part of the dogmatic subset alongside a
        genuine ``u = 0`` opinion. The non-dogmatic third opinion is
        excluded from the partition.
        """
        borderline_dog = Opinion(b=0.5, d=0.5 - 1e-200, u=1e-200)
        dog = Opinion(b=0.0, d=1.0, u=0.0)
        nondog = Opinion(b=0.3, d=0.2, u=0.5)

        full = fuse([borderline_dog, dog, nondog])
        subset = fuse([borderline_dog, dog])

        assert abs(full.b - subset.b) < EPSILON
        assert abs(full.d - subset.d) < EPSILON
        assert abs(full.u - subset.u) < EPSILON

    def test_vacuous_does_not_add_info(self) -> None:
        """fuse([a, VACUOUS]) == fuse([a]): vacuous carries no evidence."""
        a = Opinion(b=0.7, d=0.2, u=0.1)
        with_vacuous = fuse([a, VACUOUS])
        without = fuse([a])

        assert abs(with_vacuous.b - without.b) < EPSILON
        assert abs(with_vacuous.d - without.d) < EPSILON
        assert abs(with_vacuous.u - without.u) < EPSILON

    # --- Three-source ECBF ---

    def test_three_source_reference_cross_check(self) -> None:
        """Cross-check against N-ary ACBF formula (subjective-logic-library).

        ACBF N-ary: dem=0.344, (28/43, 9/43, 6/43). P=31/43.
        Maximize: ü=24/43, b̈=19/43 ≈ 0.442, d̈=0.
        Pairwise reduction matches N-ary: ACBF associativity confirmed.
        """
        c1 = Opinion(b=0.1, d=0.3, u=0.6)
        c2 = Opinion(b=0.4, d=0.2, u=0.4)
        c3 = Opinion(b=0.7, d=0.1, u=0.2)
        result = fuse([c1, c2, c3])

        assert abs(result.b - 19.0 / 43.0) < 0.001
        assert abs(result.d - 0.0) < 0.001
        assert abs(result.u - 24.0 / 43.0) < 0.001

    def test_fuse_nested_pairwise_differs_from_nary(self) -> None:
        """Nested pairwise ECBF diverges from N-ary: never pre-fuse subsets.

        Uncertainty maximization between pairwise steps discards canceled
        evidence; associativity belongs to the inner ACBF only.

        Nested: ACBF(A, B): κ=0.36, (4/9, 4/9, 1/9), P=0.5; maximization
        collapses the canceled pair to VACUOUS. Fusing VACUOUS with C
        returns C maximized: P=0.6, ü=0.8, b̈=0.2, d̈=0.
        N-ary: ACBF(A, B, C): κ=13/45, (6.5/13, 5.5/13, 1/13), P=7/13.
        Maximize once: ü=12/13, b̈=1/13, d̈=0.
        """
        a = Opinion(b=0.5, d=0.3, u=0.2)
        b = Opinion(b=0.3, d=0.5, u=0.2)
        c = Opinion(b=0.5, d=0.3, u=0.2)

        nested = fuse([fuse([a, b]), c])
        nary = fuse([a, b, c])

        assert abs(nary.b - 1.0 / 13.0) < EPSILON
        assert abs(nary.d - 0.0) < EPSILON
        assert abs(nary.u - 12.0 / 13.0) < EPSILON

        assert abs(nested.b - 0.2) < EPSILON
        assert abs(nested.d - 0.0) < EPSILON
        assert abs(nested.u - 0.8) < EPSILON

        assert abs(nested.b - nary.b) > EPSILON
        assert abs(nested.u - nary.u) > EPSILON

    def test_three_with_vacuous_neutral(self) -> None:
        """Adding vacuous opinions doesn't change the result."""
        a = Opinion(b=0.7, d=0.2, u=0.1)
        result_one = fuse([a])
        result_three = fuse([a, VACUOUS, VACUOUS])

        assert abs(result_one.b - result_three.b) < EPSILON
        assert abs(result_one.d - result_three.d) < EPSILON
        assert abs(result_one.u - result_three.u) < EPSILON

    def test_all_vacuous(self) -> None:
        result = fuse([VACUOUS, VACUOUS, VACUOUS])
        assert abs(result.b - 0.0) < EPSILON
        assert abs(result.d - 0.0) < EPSILON
        assert abs(result.u - 1.0) < EPSILON

    # --- Non-idempotency (ECBF compounds duplicate evidence) ---

    def test_not_idempotent(self) -> None:
        """fuse([a, a]) ≠ fuse([a]): duplicate evidence compounds.

        This is the property that makes settlement possible.
        """
        a = Opinion(b=0.7, d=0.1, u=0.2)
        single = fuse([a])
        double = fuse([a, a])

        # Duplicate evidence drives P further from 0.5, reducing ü.
        assert double.u < single.u - EPSILON


class TestAcbfPairUnderflow:
    """Verify _acbf_pair handles IEEE 754 underflow in near-dogmatic products."""

    def test_near_dogmatic_with_representable_product_uses_case_one(self) -> None:
        """Near-dogmatic opinions with representable u_A * u_B stay in Case I.

        Two opinions with u = 1e-160 are non-dogmatic (u >> EPSILON). Their
        product 1e-320 is a subnormal double but does not underflow to zero.
        Case I handles this correctly: the result must have u > 0.
        """
        near_dogmatic = Opinion(b=1 - 1e-160, d=0, u=1e-160)

        result = _acbf_pair(near_dogmatic, near_dogmatic)

        assert result.u > 0

    def test_underflow_to_zero_falls_back_to_averaging(self) -> None:
        """u_A * u_B underflows to 0.0 in IEEE 754 for extreme near-dogmatic opinions.

        Two opinions with u = 1e-162 are non-dogmatic (u >> EPSILON), but their
        product underflows to 0.0 in double precision (below smallest subnormal
        ~5e-324). The underflow guard falls back to dogmatic averaging. The u = 0
        intermediate is corrected by ECBF step 2 (uncertainty maximization).
        """
        extreme = Opinion(b=1 - 1e-162, d=0, u=1e-162)

        result = _acbf_pair(extreme, extreme)

        # Averaging fallback produces u = 0.0, but the opinion itself is valid.
        assert abs(result.b + result.d + result.u - 1.0) < EPSILON

    def test_underflow_opposite_directions_corrected_by_ecbf(self) -> None:
        """Opposite-direction underflow: averaging gives P=0.5, ECBF returns vacuous.

        When two near-dogmatic opinions point in opposite directions, their
        u-product underflows but their average has P=0.5. Uncertainty
        maximization corrects the u=0 intermediate to u=1.0 (vacuous).

        Same-direction underflow (both b≈1.0) produces P=1.0 in float,
        functionally dogmatic, correctly stays dogmatic after ECBF.
        """
        pos = Opinion(b=1 - 1e-162, d=0, u=1e-162)
        neg = Opinion(b=0, d=1 - 1e-162, u=1e-162)

        ecbf_result = fuse([pos, neg])

        # Contradicting near-dogmatic opinions cancel, returning to vacuous.
        assert ecbf_result.u > 0.99

    def test_both_dogmatic_uses_case_two_equal_weight_average(self) -> None:
        """Case II (Eq. 12.15, γ=0.5): both inputs dogmatic → equal-weight average.

        ``fuse`` partitions mixed-dogmatic input through the N-ary mean
        before reaching ``_acbf_pair``, so Case II is unreachable through
        ``fuse``. Pinning it here keeps ``_acbf_pair`` honest as a
        standalone Jøsang Def. 12.5 implementation.
        """
        a = Opinion(b=0.8, d=0.2, u=0.0)
        b = Opinion(b=0.6, d=0.4, u=0.0)

        result = _acbf_pair(a, b)

        assert abs(result.b - 0.7) < EPSILON
        assert abs(result.d - 0.3) < EPSILON
        assert abs(result.u - 0.0) < EPSILON


class TestFusePropertyBased:
    @given(data=st.data())
    def test_commutativity_two(self, data: st.DataObject) -> None:
        """fuse([a, b]) == fuse([b, a])."""
        a = data.draw(opinion_strategy)
        b = data.draw(opinion_strategy)

        r1 = fuse([a, b])
        r2 = fuse([b, a])
        assert abs(r1.b - r2.b) < PROP_TOL
        assert abs(r1.d - r2.d) < PROP_TOL
        assert abs(r1.u - r2.u) < PROP_TOL

    @given(data=st.data())
    def test_commutativity_three(self, data: st.DataObject) -> None:
        """Result is independent of input order for N=3.

        ACBF is commutative and associative, so pairwise left-fold in any
        permutation order produces the same result. Uncertainty maximization
        is applied once at the end.
        """
        a = data.draw(opinion_strategy)
        b = data.draw(opinion_strategy)
        c = data.draw(opinion_strategy)

        perms = list(itertools.permutations([a, b, c]))
        results = [fuse(list(p)) for p in perms]
        for r in results[1:]:
            assert abs(r.b - results[0].b) < PROP_TOL
            assert abs(r.d - results[0].d) < PROP_TOL
            assert abs(r.u - results[0].u) < PROP_TOL

    @given(a=opinion_strategy, b=opinion_strategy)
    def test_bdu_sum_is_one_two(self, a: Opinion, b: Opinion) -> None:
        result = fuse([a, b])
        assert abs(result.b + result.d + result.u - 1.0) < PROP_TOL

    @given(a=opinion_strategy, b=opinion_strategy, c=opinion_strategy)
    def test_bdu_sum_is_one_three(self, a: Opinion, b: Opinion, c: Opinion) -> None:
        result = fuse([a, b, c])
        assert abs(result.b + result.d + result.u - 1.0) < PROP_TOL

    @given(opinion=opinion_strategy)
    def test_projected_probability_in_unit_interval(self, opinion: Opinion) -> None:
        p = fuse([opinion, VACUOUS]).projected_probability
        assert -PROP_TOL <= p <= 1.0 + PROP_TOL

    @given(opinion=opinion_strategy)
    def test_vacuous_neutral_for_projected_probability(self, opinion: Opinion) -> None:
        """P(fuse([a, VACUOUS])) == P(fuse([a]))."""
        with_v = fuse([opinion, VACUOUS])
        without = fuse([opinion])
        assert abs(with_v.projected_probability - without.projected_probability) < PROP_TOL

    @given(opinion=opinion_strategy)
    def test_duplicate_does_not_increase_uncertainty(self, opinion: Opinion) -> None:
        """More evidence (even duplicated) cannot increase uncertainty."""
        single = fuse([opinion])
        double = fuse([opinion, opinion])
        assert double.u <= single.u + PROP_TOL

    @given(a=opinion_strategy, b=opinion_strategy)
    def test_output_min_bd_near_zero(self, a: Opinion, b: Opinion) -> None:
        """ECBF output is uncertainty-maximized: min(b, d) ≈ 0."""
        result = fuse([a, b])
        assert min(result.b, result.d) < PROP_TOL

    @given(opinion=opinion_strategy)
    def test_single_equals_maximize(self, opinion: Opinion) -> None:
        """fuse([a]) == maximize_uncertainty(a)."""
        result = fuse([opinion])
        expected = maximize_uncertainty(opinion)
        assert abs(result.b - expected.b) < PROP_TOL
        assert abs(result.d - expected.d) < PROP_TOL
        assert abs(result.u - expected.u) < PROP_TOL
