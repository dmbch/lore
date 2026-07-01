"""Hypothesis state computation — decay each attestation, then ECBF.

The epistemic state of a hypothesis at any time t is:

    ω_H(t) = ECBF( decay(ω₁, λ, t−t₁), ..., decay(ωₙ, λ, t−tₙ) )

Each attestation decays individually by its age before fusion. Fresh evidence
naturally dominates stale evidence. Empty attestation list → vacuous.

See docs/logic.md, "Hypothesis State Computation."
"""

from collections.abc import Sequence
from typing import NamedTuple

from lore.math.decay import decay
from lore.math.fusion import fuse
from lore.math.opinion import VACUOUS, Opinion


class OpinionAtTime(NamedTuple):
    opinion: Opinion
    timestamp: int


def compute_hypothesis_state(
    *,
    attestations: Sequence[OpinionAtTime],
    lambda_: float,
    t_now: int,
) -> Opinion:
    """Compute the current epistemic state of a hypothesis.

    Args:
        attestations: Opinion-timestamp pairs from the ledger.
        lambda_: Global decay rate (≥ 0).
        t_now: Current time in integer seconds.

    Returns:
        The fused, uncertainty-maximized opinion at t_now.
    """
    if not attestations:
        return VACUOUS

    decayed = [
        decay(opinion=a.opinion, lambda_=lambda_, t=float(max(0, t_now - a.timestamp)))
        for a in attestations
    ]
    return fuse(decayed)
