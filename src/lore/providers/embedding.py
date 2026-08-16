"""LiteLLM embedding provider: vendor-neutral text embedding."""

from typing import Any

import litellm
import openai
from litellm.types.utils import EmbeddingResponse

from lore.domain import InferenceError
from lore.providers.config import EmbeddingModelConfig
from lore.providers.protocols import TaskTypeKey


async def _call_litellm_embedding(
    *,
    model: str,
    input: list[str],
    **extra: Any,  # noqa: ANN401 - LiteLLM kwarg passthrough
) -> EmbeddingResponse:
    """Typed-boundary wrapper over ``litellm.aembedding``.

    Lore types the required positional surface (``model``, ``input``) and
    admits arbitrary pass-through via ``**extra: Any``: LiteLLM's kwarg
    surface is open and Lore commits to forwarding extras unchanged (see
    ``EmbeddingModelConfig`` and ``docs/architecture.md`` §LLM Providers).
    The return is ``EmbeddingResponse``; the precise shape Lore consumes
    (``data: list[Embedding]``, ``Embedding.embedding: list[float]``)
    comes from the local stub overlay at ``typings/litellm``: LiteLLM
    ships ``py.typed`` but its runtime annotations on these fields are
    loose. Tests monkey-patch the module's ``litellm`` symbol via
    per-call attribute lookup.
    """
    return await litellm.aembedding(model=model, input=input, **extra)


class EmbeddingProvider:
    def __init__(self, config: EmbeddingModelConfig) -> None:
        self._config = config

    async def embed_many(
        self, texts: list[str], *, task_type_key: TaskTypeKey | None = None
    ) -> list[list[float]]:
        if not texts:
            return []
        # Pass-through extras: every TOML key beyond what Lore acts on
        # itself flows to LiteLLM via ``model_dump``. ``model`` is bound as
        # a positional kwarg below; ``task_type`` is resolved per-call from
        # the semantic ``task_type_key``, not from the config sub-table.
        extra: dict[str, Any] = self._config.model_dump(
            exclude={"model", "task_type"}, exclude_none=True
        )
        if task_type_key is not None:
            vendor_string = self._resolve_task_type(task_type_key)
            if vendor_string is not None:
                extra["task_type"] = vendor_string

        try:
            response = await _call_litellm_embedding(model=self._config.model, input=texts, **extra)
        except openai.OpenAIError as e:
            raise InferenceError(str(e)) from e

        # LiteLLM's gemini batch path hardcodes index=0 on every entry;
        # unpack positionally (response order is request order). A count
        # mismatch would misalign vectors against texts downstream, and a
        # wrong vector stored on a hypothesis is permanent: fail loud.
        if len(response.data) != len(texts):
            msg = f"embedding response carries {len(response.data)} entries for {len(texts)} inputs"
            raise InferenceError(msg)
        return [d.embedding for d in response.data]

    def _resolve_task_type(self, key: TaskTypeKey) -> str | None:
        # The key is never None here: the sole caller resolves only when it
        # holds one. A `key is None` arm would be covered and unfailable.
        if self._config.task_type is None:
            return None
        mapping: dict[TaskTypeKey, str | None] = {
            "document": self._config.task_type.document,
            "question": self._config.task_type.question,
            "verification": self._config.task_type.verification,
        }
        return mapping[key]
