"""Binomial Opinion type — Jøsang's (b, d, u) triple.

A Subjective Logic opinion has three components in [0, 1] satisfying
b + d + u = 1: belief and disbelief carry evidence for and against a
proposition, uncertainty represents the absence of evidence either way.
A global base rate (BASE_RATE = 0.5) projects the triple to a scalar
probability when needed.

The ``Opinion`` type enforces the invariant at construction; ``VACUOUS``
(0, 0, 1) is the neutral element for fusion. See docs/logic.md for the
formalism and Jøsang (2016), §3, for the canonical definitions.
"""

import math
from typing import NamedTuple, Self

# Floating-point comparison tolerance. Used throughout the math core for
# equality checks (b + d + u ≈ 1.0) and boundary detection (is_vacuous,
# is_dogmatic). Tight enough to catch real errors, loose enough to absorb
# IEEE 754 rounding across a few arithmetic operations.
EPSILON = 1e-9

# Prior probability assigned in the absence of evidence — Jøsang's base rate
# (a_x). Lore uses a single binary domain (true/false) with a symmetric prior:
# equally likely absent evidence. This is a system constant, not per-opinion.
# See docs/logic.md, "Base Rate as System Constant."
BASE_RATE = 0.5


class _OpinionBase(NamedTuple):
    b: float
    d: float
    u: float


class Opinion(_OpinionBase):
    """A Subjective Logic binomial opinion: (b, d, u).

    Represents an observer's belief state about a binary proposition:
      b (belief)      — evidence FOR the proposition being true
      d (disbelief)   — evidence AGAINST the proposition being true
      u (uncertainty)  — absence of evidence either way

    Invariant: b + d + u = 1.0. The total probability mass is always 1.
    All components are in [0, 1]. Immutable after construction.

    Inherits from a NamedTuple base class because Python 3.13 blocks
    __new__ overrides on direct NamedTuple subclasses. The two-class
    pattern gives us named fields, tuple unpacking, and constructor
    validation.
    """

    __slots__ = ()

    def __new__(cls, b: float, d: float, u: float) -> Self:
        for name, value in [("b", b), ("d", d), ("u", u)]:
            if not math.isfinite(value) or value < -EPSILON or value > 1.0 + EPSILON:
                msg = f"{name} must be in [0, 1], got {value}"
                raise ValueError(msg)

        # Clamp slight floating-point overshoot inside the EPSILON
        # tolerance to the canonical ``[0, 1]`` interval. This makes the
        # invariant the type promises (each component in ``[0, 1]``)
        # algebraically true on the stored value, removing latent
        # contract drift that every call site already worked around.
        # Complements the algebraic argument in ``decay.py``.
        b = max(0.0, min(1.0, b))
        d = max(0.0, min(1.0, d))
        u = max(0.0, min(1.0, u))

        bdu_sum = b + d + u
        if abs(bdu_sum - 1.0) > EPSILON:
            msg = f"b + d + u must sum to 1, got {bdu_sum}"
            raise ValueError(msg)

        return super().__new__(cls, b, d, u)

    def __repr__(self) -> str:
        return f"Opinion(b={self.b}, d={self.d}, u={self.u})"

    @property
    def projected_probability(self) -> float:
        """Expected probability that the proposition is true: P = b + a * u.

        Collapses the three-valued opinion to a single probability by
        distributing uncertainty according to the base rate. When fully
        uncertain (vacuous): P = base rate. When fully decided (dogmatic): P = b.
        """
        return self.b + BASE_RATE * self.u

    @property
    def is_vacuous(self) -> bool:
        """No evidence at all — complete ignorance (u ≈ 1)."""
        return self.u >= 1.0 - EPSILON

    @property
    def is_dogmatic(self) -> bool:
        """Zero uncertainty — fully committed to belief/disbelief (u ≈ 0)."""
        return self.u <= EPSILON


# Complete ignorance: no evidence for or against. The neutral element for
# fusion — fusing any opinion with VACUOUS returns that opinion unchanged.
VACUOUS = Opinion(b=0.0, d=0.0, u=1.0)
