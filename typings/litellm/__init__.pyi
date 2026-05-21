"""Minimal stub overlay for the symbols Lore consumes from ``litellm``.

LiteLLM ships ``py.typed`` but several of its public functions are
typed with ``(...) -> Return``: pyright accepts the return but flags
the args as partially unknown. This overlay tightens just the surface
Lore touches; submodules (``litellm.types.utils``) continue to use the
runtime package's annotations.
"""

from collections.abc import Mapping
from typing import Any

from litellm.types.utils import EmbeddingResponse

callbacks: list[str]

async def aembedding(
    *,
    model: str,
    input: list[str],
    **kwargs: Any,
) -> EmbeddingResponse: ...
def get_model_info(model: str) -> Mapping[str, Any]: ...
