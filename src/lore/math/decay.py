"""Temporal decay toward vacuous opinion.

Unattested knowledge drifts back toward ignorance. Belief and disbelief
decay exponentially at rate λ; uncertainty fills the gap:

    b(t) = b₀ · e^(−λt)
    d(t) = d₀ · e^(−λt)
    u(t) = 1 − (1 − u₀) · e^(−λt)

At t=0 the opinion is unchanged. As t→∞ it approaches vacuous. The b/d
ratio is preserved, decay erodes conviction, not direction.

Decay is calculated at read time, never stored. λ is a global rate from
the config. Half-life: t_½ = ln(2)/λ.

Note on symbols: throughout this module, ``λ`` (``lambda_``) is the
continuous rate constant, units of inverse time: ``λ = ln(2) / t_½``.
Jøsang & Ismail 2002, Eq. 12 defines decay on evidence counters using
``R_{τ+n} = λ_retention^n · R_τ``, where ``λ_retention ∈ [0, 1]`` is a
per-step retention factor, a different quantity that shares the symbol
in the literature. Lore decays opinions directly because hypothesis
state is always computed from the ledger at read time, so there are no
persistent evidence counters to decay.
"""

import math

from lore.math.opinion import Opinion


def decay(*, opinion: Opinion, lambda_: float, t: float) -> Opinion:
    """Apply temporal decay to an opinion.

    Args:
        opinion: The opinion at its last attestation time.
        lambda_: Decay rate. Must be finite and ≥ 0. Higher values mean
            faster decay.
        t: Elapsed time since last attestation. Must be finite and ≥ 0.

    Returns:
        The decayed opinion at time t.

    Raises:
        ValueError: If lambda_ or t is non-finite (NaN/Inf) or negative.
    """
    if not math.isfinite(lambda_) or lambda_ < 0.0:
        msg = f"lambda_ must be finite and non-negative, got {lambda_}"
        raise ValueError(msg)
    if not math.isfinite(t) or t < 0.0:
        msg = f"t must be finite and non-negative, got {t}"
        raise ValueError(msg)

    factor = math.exp(-lambda_ * t)

    # Each component stays in ``[0, 1]`` algebraically: ``b * factor`` and
    # ``d * factor`` are non-negative for non-negative inputs and
    # ``factor in [0, 1]``; ``u = 1 - (1 - u₀) * factor`` is bounded
    # below by ``u₀`` and above by ``1``. Floating-point rounding inside
    # the EPSILON window is absorbed by the clamp in ``Opinion.__new__``.
    return Opinion(
        b=opinion.b * factor,
        d=opinion.d * factor,
        u=1.0 - (1.0 - opinion.u) * factor,
    )
