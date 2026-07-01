"""Uncertainty maximization: Jøsang 2016, Eq. 3.27.

Pushes uncertainty to its theoretical maximum while preserving the
projected probability P = b + a·u. The result is an *epistemic* opinion:
for the binomial domain with a = 0.5, at least one of b or d is zero.
Used as ECBF step 2 (see ``fusion.py``) and any time a fused opinion
must be expressed in epistemic form.
"""

from lore.math.opinion import BASE_RATE, Opinion


def maximize_uncertainty(opinion: Opinion) -> Opinion:
    """Uncertainty-maximise a binomial opinion (Jøsang 2016, Section 3.5.6).

    Pushes uncertainty to its theoretical maximum while preserving the
    projected probability P = b + a·u. The result is an epistemic opinion:
    for binomial domain with a = 0.5, at least one of b or d is always zero.

    Eq. 3.27 specialized to binomial: ü = min(P/a, (1−P)/(1−a)).
    With a = 0.5 this simplifies to ü = 2·min(P, 1−P).
    """
    p = opinion.projected_probability
    u_max = min(p / BASE_RATE, (1.0 - p) / (1.0 - BASE_RATE))
    b_max = p - BASE_RATE * u_max
    d_max = (1.0 - p) - (1.0 - BASE_RATE) * u_max

    # Clamp IEEE 754 noise to [0, 1].
    return Opinion(
        b=max(0.0, min(1.0, b_max)),
        d=max(0.0, min(1.0, d_max)),
        u=max(0.0, min(1.0, u_max)),
    )
