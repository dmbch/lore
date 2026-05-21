"""Epistemic Cumulative Belief Fusion (ECBF) — Jøsang 2016, Def. 12.6.

Combines independent observers' opinions into a single fused opinion.
ECBF is Aleatory Cumulative Belief Fusion (ACBF, Def. 12.5) followed by
uncertainty maximization (Eq. 3.27, see ``maximize.py``). Agreement
compounds — corroborating sources drive uncertainty down — and
contradictions cancel, so an even split returns toward ignorance rather
than feigning certainty. Vacuous opinions are the neutral element for
information content: fusing in vacuous changes nothing.
"""

import math
from collections.abc import Sequence
from functools import reduce

from lore.math.maximize import maximize_uncertainty
from lore.math.opinion import VACUOUS, Opinion

_LOG2_MIN_POSITIVE = math.log2(math.nextafter(0.0, 1.0))  # ≈ -1074


def _u_in_underflow_regime(u: float) -> bool:
    """True iff ``u * u`` underflows to zero in IEEE-754 doubles.

    Algebraic — sum of log₂ exponents vs. the smallest representable positive
    double. Independent of FTZ/DAZ FP-environment flags and fast-math contexts
    that flush subnormals platform-specifically.
    """
    return u == 0.0 or 2 * math.log2(u) < _LOG2_MIN_POSITIVE


def _dogmatic_average(a: Opinion, b: Opinion) -> Opinion:
    """Equal-weight average for dogmatic or functionally-dogmatic pairs.

    Eq. 12.15 with γ_A = γ_B = 0.5. Used for genuine both-dogmatic (u=0)
    and as the underflow fallback when u_A * u_B rounds to zero in IEEE 754.
    """
    return Opinion(
        b=0.5 * a.b + 0.5 * b.b,
        d=0.5 * a.d + 0.5 * b.d,
        u=0.0,
    )


def _acbf_pair(a: Opinion, b: Opinion) -> Opinion:
    """Pairwise Aleatory Cumulative Belief Fusion (Jøsang 2016 Def. 12.5).

    Accumulates evidence from two independent sources. Commutative and
    associative — N-ary ACBF can be computed by pairwise reduction.

    Case I (at least one non-dogmatic): Eq. 12.14.
    Case II (both dogmatic, u=0): Eq. 12.15 with γ_A = γ_B = 0.5.

    The Case II gate uses an exact ``u == 0.0`` check, not ``Opinion.EPSILON``:
    Eq. 12.14 is well-defined for any ``u > 0``, however small. Case II is
    the limit case where ``u = 0`` causes division by zero in κ. Near-zero
    ``u`` belongs in Case I — the asymmetry with ``Opinion.is_dogmatic``
    (which uses ``EPSILON`` for boundary classification) is intentional.

    WARNING: This function contains an underflow guard that falls back to
    dogmatic averaging when both inputs are near-dogmatic and their
    uncertainty product underflows to zero in IEEE 754 (e.g. u ≈ 1e-162).
    The fallback produces u=0.0 — a dogmatic intermediate that is only
    correct within the ECBF pipeline, where uncertainty maximization
    (step 2) restores u > 0 from the preserved projected probability P.
    Standalone callers must apply maximize_uncertainty to the result if
    near-dogmatic inputs are possible.
    """
    # Case II: both dogmatic — equal-weight average. The ``== 0.0`` check
    # is intentional; see docstring above.
    if a.u == 0.0 and b.u == 0.0:
        return _dogmatic_average(a, b)

    # Case I: at least one non-dogmatic.
    # κ = u_A + u_B − u_A·u_B (always > 0 when at least one u > 0).
    kappa = a.u + b.u - a.u * b.u
    u_product = a.u * b.u

    # Underflow guard: when both inputs have u > 0 but u_A * u_B underflows
    # to 0.0 in IEEE 754 (e.g. u = 1e-162, product < 2^-1074), fall back to
    # the dogmatic averaging case. Any u small enough to underflow when
    # squared is also too small to affect P (ULP of 1.0 ≈ 2.2e-16), so
    # these inputs are functionally dogmatic and the averaging fallback
    # produces the correct result. For opposite-direction inputs where the
    # average has P ∈ (0, 1), uncertainty maximization (ECBF step 2)
    # restores u > 0.
    # The a.u > 0 and b.u > 0 check distinguishes underflow from the genuine
    # one-dogmatic case where u_product = 0 because one input has u = 0.
    # ``_u_in_underflow_regime`` is algebraic (compares log₂ exponents to
    # the minimum-positive-double exponent), so the routing decision is
    # independent of FTZ/DAZ FP-environment flags that would otherwise
    # let ``u_product == 0.0`` fire at different ``u`` per platform.
    if a.u > 0.0 and b.u > 0.0 and _u_in_underflow_regime(a.u) and _u_in_underflow_regime(b.u):
        return _dogmatic_average(a, b)

    return Opinion(
        b=(a.b * b.u + b.b * a.u) / kappa,
        d=(a.d * b.u + b.d * a.u) / kappa,
        u=u_product / kappa,
    )


def fuse(opinions: Sequence[Opinion]) -> Opinion:
    """Epistemic Cumulative Belief Fusion (ECBF) for N opinions.

    Combines multiple observers' epistemic opinions into a single fused
    opinion. ECBF is ACBF followed by uncertainty maximization
    (Jøsang 2016 Def. 12.6).

    Step 1: Aleatory Cumulative Fusion (Def. 12.5) — accumulates evidence
            from independent sources via pairwise reduction (Case I) or
            equal-weight N-ary average (Case II, all dogmatic, Eq. 12.15).
    Step 2: Uncertainty maximization (Eq. 3.27) — pushes uncertainty to
            its epistemic maximum while preserving projected probability P.

    Properties (over non-dogmatic inputs): commutative, associative,
    non-idempotent. Once any input is in the underflow regime the algebra
    branches into the equal-weight N-ary mean (Eq. 12.15), which is
    commutative but not associative with the Case I formula.
    VACUOUS is the neutral element for information content:
    fuse([a, VACUOUS]) == fuse([a]).
    """
    n = len(opinions)
    if n == 0:
        return VACUOUS
    if n == 1:
        return maximize_uncertainty(opinions[0])

    # Mixed-dogmatic partition (Jøsang Eq. 12.15 reading per Aggregatio):
    # when ≥1 opinion is in the underflow regime and ≥1 is not, the N-ary
    # equal-weight mean runs over the dogmatic subset only — the non-
    # dogmatic minority is ignored. Reference:
    # ``references/src/Aggregatio/.../SubjectiveOpinion.java`` cumulative
    # fusion partitioning logic. Recursing on the dogmatic subset routes
    # cleanly through the all-dogmatic short-circuit below.
    dog_count = sum(1 for o in opinions if _u_in_underflow_regime(o.u))
    if 0 < dog_count < n:
        return fuse([o for o in opinions if _u_in_underflow_regime(o.u)])

    # Case II: all (functionally) dogmatic — N-ary equal-weight average
    # (Eq. 12.15). Pairwise reduction with fixed γ=0.5 over-weights later
    # opinions: for A(0.9), B(0.7), C(0.3), pairwise gives
    # avg(avg(A,B), C) = avg(0.8, 0.3) = 0.55, but equal weights give
    # (0.9+0.7+0.3)/3 = 0.633. The N-ary formula requires γ_i = 1/N
    # for each source (Eq. 12.15).
    #
    # "Functionally dogmatic" extends ``u == 0.0`` to "u so small that
    # ``u * u`` underflows in IEEE 754" — for those inputs, ``_acbf_pair``
    # falls back to a pairwise γ=0.5 average (the underflow guard in
    # ``_acbf_pair``), and chained pairwise reduction over three or more
    # such opinions emits an intermediate ``u = 0`` that lets later steps
    # take Case I, producing an order-dependent and incorrect result
    # (regression guard in ``tests/math/test_fusion_properties.py``).
    # Detecting all-near-dogmatic up front via ``_u_in_underflow_regime``
    # (algebraic, so independent of FTZ/DAZ FP-environment flags) routes
    # through the structural N-ary mean, which the underflow guard
    # intends to mirror.
    if dog_count == n:
        acbf_result = Opinion(
            b=sum(o.b for o in opinions) / n,
            d=sum(o.d for o in opinions) / n,
            u=0.0,
        )
    else:
        acbf_result = reduce(_acbf_pair, opinions)

    return maximize_uncertainty(acbf_result)
