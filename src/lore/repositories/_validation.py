"""Shared validation for repository methods.

Pure validation logic used by both SQLite and PostgreSQL backends.
No I/O, no domain logic — just parameter contracts.
"""

import math
from collections.abc import Sequence


def validate_embedding(embedding: Sequence[float]) -> None:
    """Validate embedding() input. Raises ValueError on invalid input.

    All elements must be finite; at least one must be non-zero. Cosine
    distance on a zero-magnitude vector is NaN — undefined direction —
    so a no-direction vector is not a meaningful embedding.
    """
    if not all(math.isfinite(x) for x in embedding):
        msg = "embedding components must be finite"
        raise ValueError(msg)
    if not any(embedding):
        msg = "embedding must have non-zero magnitude"
        raise ValueError(msg)


def validate_search_params(*, weights: tuple[float, float], limit: int, fan_out: int) -> None:
    """Validate search() parameters. Raises ValueError on invalid input.

    Both backends must enforce identical constraints — this function is
    the single source of truth for the Protocol contract:
    "weights must sum to 1.0 (±0.001 tolerance). limit and fan_out must
    be >= 1."
    """
    if limit < 1:
        msg = f"limit must be >= 1, got {limit}"
        raise ValueError(msg)
    if fan_out < 1:
        msg = f"fan_out must be >= 1, got {fan_out}"
        raise ValueError(msg)
    if any(w < 0 for w in weights):
        msg = f"weights must be non-negative, got {weights}"
        raise ValueError(msg)
    w_sum = weights[0] + weights[1]
    if abs(w_sum - 1.0) > 0.001:
        msg = f"weights must sum to 1.0, got {w_sum}"
        raise ValueError(msg)
