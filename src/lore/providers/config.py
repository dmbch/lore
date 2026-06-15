"""Model-role config types — frozen Pydantic models.

Pass-through containers by design: Lore types only the fields it consumes
itself; every other key round-trips via ``model_dump`` and flows to LiteLLM
unchanged (see ``docs/architecture.md`` §LLM Providers).
"""

from pydantic import BaseModel, ConfigDict, field_validator


class TaskTypeConfig(BaseModel):
    """Vendor-specific embedding task types. Unset keys are omitted from the LiteLLM call."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    document: str | None = None
    question: str | None = None
    verification: str | None = None


class EmbeddingModelConfig(BaseModel):
    """Embedding model config. `extra='allow'` so unrecognised keys round-trip to LiteLLM."""

    model_config = ConfigDict(frozen=True, strict=True, extra="allow")

    model: str
    dimensions: int | None = None
    task_type: TaskTypeConfig | None = None

    @field_validator("dimensions")
    @classmethod
    def _validate_dimensions(cls, v: int | None) -> int | None:
        if v is not None and v <= 0:
            msg = f"dimensions must be > 0, got {v}"
            raise ValueError(msg)
        return v


class ModelConfig(BaseModel):
    """Completion model config. `extra='allow'` so unrecognised keys round-trip to LiteLLM."""

    model_config = ConfigDict(frozen=True, strict=True, extra="allow")

    model: str
    temperature: float | None = None
    max_tokens: int | None = None
    reasoning_effort: str | None = None
