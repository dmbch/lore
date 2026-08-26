"""Hypothesis state computation: decay each attestation, then ECBF.

The epistemic state of a hypothesis at time t is:

    ω_H(t) = ECBF( decay(ω₁, λ, t−t₁), ..., decay(ωₙ, λ, t−tₙ) )

Each attestation decays individually by its age. The decayed opinions are
fused with ECBF. Empty attestation list → vacuous.

See docs/logic.md, "Hypothesis State Computation."
"""

from hypothesis import assume, given
from hypothesis import strategies as st

from lore.math._decay import decay
from lore.math._fusion import fuse
from lore.math._hypothesis import OpinionAtTime, compute_hypothesis_state
from lore.math._opinion import EPSILON, Opinion
from tests.math.conftest import PROP_TOL, opinion_strategy


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------
def test_empty_attestations_returns_vacuous() -> None:
    """No attestations → vacuous. Pure ignorance."""
    result = compute_hypothesis_state(attestations=[], lambda_=0.1, t_now=1000)
    assert abs(result.b - 0.0) < EPSILON
    assert abs(result.d - 0.0) < EPSILON
    assert abs(result.u - 1.0) < EPSILON


def test_single_fresh_attestation_returns_maximized() -> None:
    """One attestation at dt=0 → uncertainty-maximized form of that opinion.

    Opinion: (0.7, 0.2, 0.1), P = 0.75.
    Maximize: ü = 0.5, b̈ = 0.5, d̈ = 0.0.
    """
    opinion = Opinion(b=0.7, d=0.2, u=0.1)
    result = compute_hypothesis_state(
        attestations=[OpinionAtTime(opinion, 100)], lambda_=0.1, t_now=100
    )
    assert abs(result.b - 0.5) < EPSILON
    assert abs(result.d - 0.0) < EPSILON
    assert abs(result.u - 0.5) < EPSILON


# ---------------------------------------------------------------------------
# Staleness
# ---------------------------------------------------------------------------
def test_fresh_attestation_dominates_stale() -> None:
    """A recent attestation has more influence than a stale one.

    Two opinions: one fresh (dt=0, undecayed) and one stale (dt=100, heavily
    decayed with λ=0.1). The stale one's evidence is mostly eroded, so the
    fused result should lean toward the fresh opinion's direction.
    """
    belief = Opinion(b=0.8, d=0.1, u=0.1)
    disbelief = Opinion(b=0.1, d=0.8, u=0.1)

    # disbelief is stale (t=900), belief is fresh (t=1000)
    result = compute_hypothesis_state(
        attestations=[OpinionAtTime(disbelief, 900), OpinionAtTime(belief, 1000)],
        lambda_=0.1,
        t_now=1000,
    )
    # Fresh belief should dominate → projected probability > 0.5
    assert result.projected_probability > 0.5


def test_all_stale_approaches_vacuous() -> None:
    """All attestations old → near vacuous.

    λ=1.0, all attestations at t=0, t_now=100. e^(-100) ≈ 0.
    """
    opinions = [
        OpinionAtTime(Opinion(b=0.8, d=0.1, u=0.1), 0),
        OpinionAtTime(Opinion(b=0.1, d=0.7, u=0.2), 0),
    ]
    result = compute_hypothesis_state(attestations=opinions, lambda_=1.0, t_now=100)
    assert result.u > 1.0 - EPSILON


# ---------------------------------------------------------------------------
# Hand-calculated: manual decay + fuse cross-check
# ---------------------------------------------------------------------------
def test_agrees_with_manual_decay_then_fuse() -> None:
    """Hand-compute decay + ECBF and verify compute_hypothesis_state matches.

    ω₁ = (0.6, 0.3, 0.1) at t=90, ω₂ = (0.4, 0.1, 0.5) at t=95.
    t_now = 100, λ = 0.1.

    Δt₁ = 10, Δt₂ = 5.
    Decay each, then fuse. Result must match compute_hypothesis_state.
    """
    o1 = Opinion(b=0.6, d=0.3, u=0.1)
    o2 = Opinion(b=0.4, d=0.1, u=0.5)

    decayed_1 = decay(opinion=o1, lambda_=0.1, t=10.0)
    decayed_2 = decay(opinion=o2, lambda_=0.1, t=5.0)
    expected = fuse([decayed_1, decayed_2])

    result = compute_hypothesis_state(
        attestations=[OpinionAtTime(o1, 90), OpinionAtTime(o2, 95)], lambda_=0.1, t_now=100
    )
    assert abs(result.b - expected.b) < EPSILON
    assert abs(result.d - expected.d) < EPSILON
    assert abs(result.u - expected.u) < EPSILON


def test_single_decayed_attestation() -> None:
    """Single stale attestation = maximize(decay(ω)).

    ω = (0.8, 0.1, 0.1), λ=0.5, Δt=2. e^(-1) ≈ 0.3679.
    Decayed: b≈0.2943, d≈0.0368, u≈0.6690.
    P ≈ 0.2943 + 0.5·0.6690 ≈ 0.6288.
    Maximize: ü ≈ 0.7425, b̈ ≈ 0.2575, d̈ = 0.
    """
    opinion = Opinion(b=0.8, d=0.1, u=0.1)
    decayed = decay(opinion=opinion, lambda_=0.5, t=2.0)
    expected = fuse([decayed])

    result = compute_hypothesis_state(
        attestations=[OpinionAtTime(opinion, 8)], lambda_=0.5, t_now=10
    )
    assert abs(result.b - expected.b) < EPSILON
    assert abs(result.d - expected.d) < EPSILON
    assert abs(result.u - expected.u) < EPSILON


# ---------------------------------------------------------------------------
# Zero lambda (no decay)
# ---------------------------------------------------------------------------
def test_contradicting_attestations_yield_high_uncertainty() -> None:
    """Evenly opposed attestations cancel: the hypothesis returns to ignorance.

    Two fresh attestations with symmetric confidence (one believes, one disbelieves)
    fuse to near-vacuous. This is the hypothesis-level analog of ECBF's
    contradiction cancellation (Jøsang §12.3.2).
    """
    belief = Opinion(b=0.8, d=0.0, u=0.2)
    disbelief = Opinion(b=0.0, d=0.8, u=0.2)
    t = 100

    result = compute_hypothesis_state(
        attestations=[OpinionAtTime(belief, t), OpinionAtTime(disbelief, t)],
        lambda_=0.0,
        t_now=t,
    )
    assert result.u > 0.99
    assert abs(result.b) < EPSILON
    assert abs(result.d) < EPSILON


def test_zero_lambda_no_decay() -> None:
    """λ=0 → no decay. Equivalent to fusing undecayed opinions."""
    o1 = Opinion(b=0.7, d=0.1, u=0.2)
    o2 = Opinion(b=0.6, d=0.1, u=0.3)
    expected = fuse([o1, o2])

    result = compute_hypothesis_state(
        attestations=[OpinionAtTime(o1, 0), OpinionAtTime(o2, 500)],
        lambda_=0.0,
        t_now=1000,
    )
    assert abs(result.b - expected.b) < EPSILON
    assert abs(result.d - expected.d) < EPSILON
    assert abs(result.u - expected.u) < EPSILON


# ---------------------------------------------------------------------------
# Property-based tests
# ---------------------------------------------------------------------------

# Strategy: attestation as OpinionAtTime with timestamp in [0, 1000].
attestation_strategy = st.tuples(
    opinion_strategy,
    st.integers(min_value=0, max_value=1000),
).map(lambda t: OpinionAtTime(t[0], t[1]))


@given(
    attestations=st.lists(attestation_strategy, min_size=0, max_size=5),
    lambda_=st.floats(min_value=0.0, max_value=1.0),
)
def test_output_bdu_sum_is_one(
    attestations: list[OpinionAtTime],
    lambda_: float,
) -> None:
    """b + d + u = 1 for all inputs."""
    result = compute_hypothesis_state(attestations=attestations, lambda_=lambda_, t_now=1001)
    assert abs(result.b + result.d + result.u - 1.0) < PROP_TOL


@given(
    attestations=st.lists(attestation_strategy, min_size=0, max_size=5),
    lambda_=st.floats(min_value=0.0, max_value=1.0),
)
def test_output_is_uncertainty_maximized(
    attestations: list[OpinionAtTime],
    lambda_: float,
) -> None:
    """ECBF output always has min(b, d) ≈ 0."""
    result = compute_hypothesis_state(attestations=attestations, lambda_=lambda_, t_now=1001)
    assert min(result.b, result.d) < PROP_TOL


@given(
    opinion=opinion_strategy,
    lambda_=st.floats(min_value=0.01, max_value=0.5),
)
def test_corroborating_attestation_reduces_uncertainty(
    opinion: Opinion,
    lambda_: float,
) -> None:
    """Adding a corroborating (identical) attestation reduces uncertainty.

    fuse([a, a]) has less uncertainty than fuse([a]): ECBF is non-idempotent.
    Both attestations are fresh (dt=0) to isolate the fusion effect from decay.
    """
    assume(not opinion.is_vacuous)
    single = compute_hypothesis_state(
        attestations=[OpinionAtTime(opinion, 100)], lambda_=lambda_, t_now=100
    )
    double = compute_hypothesis_state(
        attestations=[OpinionAtTime(opinion, 100), OpinionAtTime(opinion, 100)],
        lambda_=lambda_,
        t_now=100,
    )
    assert double.u <= single.u + PROP_TOL


@given(
    attestations=st.lists(attestation_strategy, min_size=0, max_size=5),
    lambda_=st.floats(min_value=0.0, max_value=1.0),
)
def test_output_components_in_unit_interval(
    attestations: list[OpinionAtTime],
    lambda_: float,
) -> None:
    """All output components ∈ [0, 1]."""
    result = compute_hypothesis_state(attestations=attestations, lambda_=lambda_, t_now=1001)
    assert -PROP_TOL <= result.b <= 1.0 + PROP_TOL
    assert -PROP_TOL <= result.d <= 1.0 + PROP_TOL
    assert -PROP_TOL <= result.u <= 1.0 + PROP_TOL
