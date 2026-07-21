"""Epistemic hyperparameters: frozen config owned by the math layer."""

from lore._pydantic import ConfigModel, Duration, NonNegativeFiniteFloat, PositiveFiniteFloat


class EpistemicsConfig(ConfigModel):
    """The four epistemic hyperparameters (IDEA.md, The Hyperparameters).

    `maturity_k` is the half-saturation constant K. It also governs the
    adaptive blend between write-time and read-time alignment in oracle
    trust (see docs/logic.md).

    `transfer_threshold` is the epistemic-significance floor for the
    consolidated transfer attestation: fused magnitudes below this value
    do not produce a row. Decoupled from ``Opinion.EPSILON`` (math-core
    IEEE noise floor) so that operators can tune what counts as
    "informationally meaningful" independently of float precision.
    """

    attestation_half_life: Duration
    trust_half_life: Duration
    maturity_k: NonNegativeFiniteFloat
    transfer_threshold: PositiveFiniteFloat = 1e-3
