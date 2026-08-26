"""Trust discounting: c_discounted = P_effective * c_raw.

Scalar shortcut of Josang's Def. 14.6, valid for uncertainty-maximized opinions.
See docs/logic.md §Trust Discounting: The Scalar Shortcut.
"""

import math


def discount(*, confidence: float, p_effective: float) -> float:
    """Direction preserved, magnitude reduced."""
    if not math.isfinite(confidence) or confidence < -1.0 or confidence > 1.0:
        msg = f"confidence must be in [-1, 1], got {confidence}"
        raise ValueError(msg)
    if not math.isfinite(p_effective) or p_effective < 0.0 or p_effective > 1.0:
        msg = f"p_effective must be in [0, 1], got {p_effective}"
        raise ValueError(msg)
    return p_effective * confidence
