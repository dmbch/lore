"""Scalar confidence ↔ opinion mapping.

The forward mapping accepts c ∈ [-1, 1] (the full mathematical domain) and
produces uncertainty-maximized opinions by construction. Trust discounting
(P_effective < 1 for K >= 1) is the pipeline policy that prevents dogmatic
opinions from reaching ECBF, not input validation.

    c > 0:  ω = (c, 0, 1 − c)
    c < 0:  ω = (0, |c|, 1 − |c|)
    c = 0:  ω = (0, 0, 1)         (vacuous)

Inverse: c = b − d.

See docs/logic.md, "Scalar Confidence Mapping."
"""

import math

from lore.math.opinion import VACUOUS, Opinion


def to_opinion(c: float) -> Opinion:
    """Map a scalar confidence to an uncertainty-maximized opinion.

    Args:
        c: Directional confidence in [-1, 1]. Positive = belief,
           negative = disbelief, zero = ignorance. The full mathematical
           domain: c = 1.0 produces Opinion(1, 0, 0) (dogmatic belief),
           c = -1.0 produces Opinion(0, 1, 0) (dogmatic disbelief).

    Raises:
        ValueError: If c is non-finite or outside [-1, 1].
    """
    if not math.isfinite(c) or c < -1.0 or c > 1.0:
        msg = f"confidence must be in [-1, 1], got {c}"
        raise ValueError(msg)

    if c > 0.0:
        return Opinion(b=c, d=0.0, u=1.0 - c)
    if c < 0.0:
        return Opinion(b=0.0, d=-c, u=1.0 + c)
    return VACUOUS


def to_confidence(opinion: Opinion) -> float:
    """Map an opinion back to a scalar confidence.

    c = 2P − 1 = b − d.

    Lossless for uncertainty-maximized opinions (min(b, d) = 0).
    """
    return opinion.b - opinion.d
