"""Bootstrap utilities for the provider layer.

Dimension resolution for embedding models — sync, runs before migrations.
"""

import litellm

from lore.domain import InferenceError


def resolve_dimensions(*, model: str, configured: int | None) -> int:
    if configured is not None:
        return configured

    try:
        info = litellm.get_model_info(model)
    except Exception as e:
        # LiteLLM raises a bare Exception for unmapped models ("Model {model}
        # isn't mapped yet..."), not ValueError/KeyError. Widening the clause
        # keeps the documented contract: every bootstrap failure surfaces as
        # a typed domain error.
        msg = f"cannot resolve dimensions for model {model!r}"
        raise InferenceError(msg) from e

    size = info.get("output_vector_size")
    if not isinstance(size, int) or size <= 0:
        msg = f"model {model!r} has no valid output_vector_size: {size!r}"
        raise InferenceError(msg)

    return size
