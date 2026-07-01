"""Shared validation for repository methods.

Pure parameter-contract checks called by both SQLite and PostgreSQL:
one implementation keeps the two backends from diverging.
"""

import math
from collections.abc import Sequence


def validate_embedding(embedding: Sequence[float]) -> None:
    """Reject non-finite or zero-magnitude embeddings.

    Cosine distance on a zero-magnitude vector is NaN: undefined
    direction, so a no-direction vector is not a meaningful embedding.
    """
    if not all(math.isfinite(x) for x in embedding):
        msg = "embedding components must be finite"
        raise ValueError(msg)
    if not any(embedding):
        msg = "embedding must have non-zero magnitude"
        raise ValueError(msg)


def validate_search_params(*, weights: tuple[float, float], limit: int, fan_out: int) -> None:
    """Single source of truth for the search() parameter contract.

    Both backends call this so SQLite and PostgreSQL enforce identical
    constraints: weights sum to 1.0 (±0.001), limit and fan_out >= 1.
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
