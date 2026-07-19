"""Epistemic hyperparameters: frozen config owned by the math layer."""

from pydantic import BaseModel, ConfigDict, field_validator

from lore._pydantic import Duration


class EpistemicsConfig(BaseModel):
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

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    attestation_half_life: Duration
    trust_half_life: Duration
    maturity_k: float
    transfer_threshold: float = 1e-3

    @field_validator("maturity_k")
    @classmethod
    def _validate_maturity_k(cls, v: float) -> float:
        if v < 0:
            msg = f"maturity_k must be >= 0, got {v}"
            raise ValueError(msg)
        return v

    @field_validator("transfer_threshold")
    @classmethod
    def _validate_transfer_threshold(cls, v: float) -> float:
        if v <= 0:
            msg = f"transfer_threshold must be > 0, got {v}"
            raise ValueError(msg)
        return v
