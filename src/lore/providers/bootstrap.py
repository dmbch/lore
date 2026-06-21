"""Bootstrap utilities for the provider layer.

Dimension resolution for embedding models — sync, runs before migrations.
"""

from typing import TYPE_CHECKING

import litellm

from lore.domain import InferenceError
from lore.providers.completion import CompletionProvider
from lore.providers.embedding import EmbeddingProvider
from lore.providers.protocols import Providers

if TYPE_CHECKING:
    from lore.config import LoreSettings


def build_providers(settings: LoreSettings) -> Providers:
    return Providers(
        embedder=EmbeddingProvider(settings.embedding),
        interpreter=CompletionProvider(settings.fast),
        archivist=CompletionProvider(settings.reasoning),
    )


def resolve_dimensions(settings: LoreSettings) -> int:
    configured = settings.embedding.dimensions
    model = settings.embedding.model
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
