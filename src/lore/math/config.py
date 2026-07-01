"""Epistemic hyperparameters: frozen config owned by the math layer."""

import re

from pydantic import BaseModel, ConfigDict, field_validator

_DURATION_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*([yMdhms]?)$")
_UNITS: dict[str, int] = {
    "y": 31536000,
    "M": 2592000,
    "d": 86400,
    "h": 3600,
    "m": 60,
    "s": 1,
    "": 1,
}


def _parse_half_life(value: str | float) -> float:
    """Parse a duration string (`"1y"`, `"90d"`, `"24h"`, ...) or bare seconds."""
    if isinstance(value, int | float):
        seconds = float(value)
    else:
        match = _DURATION_RE.match(value.strip())
        if not match:
            msg = (
                f"invalid half_life: {value!r}"
                " (expected e.g. '1y', '3M', '90d', '24h', '60m', '3600s')"
            )
            raise ValueError(msg)
        seconds = float(match.group(1)) * _UNITS[match.group(2)]
    if seconds <= 0:
        msg = f"half_life must be positive, got {value!r}"
        raise ValueError(msg)
    return seconds


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

    attestation_half_life: float
    trust_half_life: float
    maturity_k: float
    transfer_threshold: float = 1e-3

    @field_validator("attestation_half_life", "trust_half_life", mode="before")
    @classmethod
    def _validate_half_life(cls, v: str | float) -> float:
        return _parse_half_life(v)

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
