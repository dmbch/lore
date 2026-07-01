"""Conflict metrics: Jøsang 2016 §4.8, Def. 4.20, Eqs. 4.61–4.63.

Three metrics for comparing two opinions:

  PD: Projected Distance    (Eq. 4.61)  |P_a - P_b|
  CC: Conjunctive Certainty (Eq. 4.62)  (1 - u_a)(1 - u_b)
  DC: Degree of Conflict    (Eq. 4.63)  PD · CC

All three are implemented and verified for prior-art completeness. Only
``compute_projected_distance`` (PD) is wired into the runtime today; CC and DC
are kept as tested implementations for future use.

Verified against:
  - Jøsang (2016), Def. 4.20, Eqs. 4.61–4.63
  - uncertainty-datatypes (PyPI), sbool.py
"""

from lore.math.opinion import Opinion


def compute_projected_distance(a: Opinion, b: Opinion) -> float:
    """Eq. 4.61: binomial case: |P_a - P_b|."""
    return abs(a.projected_probability - b.projected_probability)


def compute_conjunctive_certainty(a: Opinion, b: Opinion) -> float:
    """Eq. 4.62: joint certainty: (1 - u_a)(1 - u_b)."""
    return (1.0 - a.u) * (1.0 - b.u)


def compute_degree_of_conflict(a: Opinion, b: Opinion) -> float:
    """Def. 4.20, Eq. 4.63: PD · CC.

    High DC requires both directional disagreement and evidential weight.
    """
    return compute_projected_distance(a, b) * compute_conjunctive_certainty(a, b)
