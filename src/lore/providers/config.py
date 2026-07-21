"""Model-role config types: frozen Pydantic models.

Pass-through containers by design: Lore types only the fields it consumes
itself; every other key round-trips via ``model_dump`` and flows to LiteLLM
unchanged (see ``docs/architecture.md`` §LLM Providers).
"""

from pydantic import ConfigDict, PositiveInt

from lore._pydantic import ConfigModel


class TaskTypeConfig(ConfigModel):
    """Vendor-specific embedding task types. Unset keys are omitted from the LiteLLM call."""

    document: str | None = None
    question: str | None = None
    verification: str | None = None


class EmbeddingModelConfig(ConfigModel):
    """Embedding model config. `extra='allow'` so unrecognised keys round-trip to LiteLLM."""

    model_config = ConfigDict(extra="allow")

    model: str
    dimensions: PositiveInt | None = None
    task_type: TaskTypeConfig | None = None


class ModelConfig(ConfigModel):
    """Completion model config. `extra='allow'` so unrecognised keys round-trip to LiteLLM."""

    model_config = ConfigDict(extra="allow")

    model: str
    temperature: float | None = None
    max_tokens: int | None = None
    reasoning_effort: str | None = None
