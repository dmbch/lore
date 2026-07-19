"""Shared pydantic vocabulary: annotated field types for config models.

Config models live with the layer that owns them, so anything they share
needs a home every layer may import. This is it: a leaf module importing
nothing from ``lore`` (a shared type under ``lore.config`` would cycle,
since that package's barrel imports the section models back out of the
layers). Scope guard: pydantic field types and model bases only, never a
junk drawer. The strict frozen model base and positivity types join
``Duration`` here when that sweep lands (see TODO.md).
"""

import re
from typing import Annotated

from pydantic import BeforeValidator, ValidationInfo

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


def _parse_duration(value: str | float, info: ValidationInfo) -> float:
    """Parse a duration string or bare seconds; errors name the field."""
    field = info.field_name or "duration"
    if isinstance(value, int | float):
        seconds = float(value)
    else:
        match = _DURATION_RE.match(value.strip())
        if not match:
            msg = (
                f"invalid {field}: {value!r}"
                " (expected e.g. '1y', '3M', '90d', '24h', '60m', '3600s')"
            )
            raise ValueError(msg)
        seconds = float(match.group(1)) * _UNITS[match.group(2)]
    if seconds <= 0:
        msg = f"{field} must be positive, got {value!r}"
        raise ValueError(msg)
    return seconds


# Positive seconds as float, accepting duration strings ("1y", "3M", "90d",
# "24h", "60m", "3600s") or bare numbers. The BeforeValidator runs ahead of
# strict-mode coercion, so string inputs are legal even under strict=True.
Duration = Annotated[float, BeforeValidator(_parse_duration)]
