"""Tests for lore.providers.config: model-role config models."""

import pytest
from pydantic import ValidationError

from lore.providers.config import EmbeddingModelConfig, ModelConfig, TaskTypeConfig

# ---------------------------------------------------------------------------
# Embedding config: dimensions
# ---------------------------------------------------------------------------


def test_embedding_config_accepts_explicit_dimensions() -> None:
    ec = EmbeddingModelConfig(model="test/m", dimensions=1536)
    assert ec.dimensions == 1536


def test_embedding_config_dimensions_zero_raises() -> None:
    with pytest.raises(ValidationError, match="dimensions"):
        EmbeddingModelConfig(model="test/m", dimensions=0)


def test_embedding_config_dimensions_negative_raises() -> None:
    with pytest.raises(ValidationError, match="dimensions"):
        EmbeddingModelConfig(model="test/m", dimensions=-1)


# ---------------------------------------------------------------------------
# TaskTypeConfig
# ---------------------------------------------------------------------------


def test_task_type_config_accepts_partial_fields() -> None:
    tc = TaskTypeConfig(document="RETRIEVAL_DOCUMENT")
    assert tc.document == "RETRIEVAL_DOCUMENT"
    assert tc.question is None
    assert tc.verification is None


def test_task_type_config_frozen() -> None:
    tc = TaskTypeConfig(document="X")
    with pytest.raises(ValidationError, match="frozen"):
        tc.document = "Y"  # pyright: ignore[reportAttributeAccessIssue]


def test_task_type_config_rejects_unknown_keys() -> None:
    with pytest.raises(ValidationError, match="extra"):
        TaskTypeConfig(documnet="RETRIEVAL_DOCUMENT")  # pyright: ignore[reportCallIssue]


# ---------------------------------------------------------------------------
# EmbeddingModelConfig.task_type
# ---------------------------------------------------------------------------


def test_embedding_config_accepts_task_type() -> None:
    tt = TaskTypeConfig(document="DOC", question="QA")
    ec = EmbeddingModelConfig(model="test/m", task_type=tt)
    assert ec.task_type is not None
    assert ec.task_type.document == "DOC"
    assert ec.task_type.question == "QA"


# ---------------------------------------------------------------------------
# ModelConfig.reasoning_effort
# ---------------------------------------------------------------------------


def test_model_config_accepts_reasoning_effort() -> None:
    mc = ModelConfig(model="test/m", reasoning_effort="high")
    assert mc.reasoning_effort == "high"


# ---------------------------------------------------------------------------
# Pass-through extras
# ---------------------------------------------------------------------------


def test_model_config_accepts_extras_as_pass_through() -> None:
    """ModelConfig admits arbitrary keys; they round-trip via model_dump.

    Model-role configs are pass-through containers (see
    ``docs/architecture.md`` §LLM Providers). Unknown keys are not typos:
    they are vendor-specific LiteLLM kwargs Lore commits to forwarding
    unchanged. Constructed via ``model_validate`` so the dynamic-key
    contract does not need a typed-call escape hatch.
    """
    cfg = ModelConfig.model_validate({"model": "x", "custom_vendor_knob": "y"})
    assert cfg.model_dump()["custom_vendor_knob"] == "y"


def test_embedding_model_config_round_trips_extra_kwargs_through_model_dump() -> None:
    cfg = EmbeddingModelConfig.model_validate({"model": "x", "custom": "y"})
    dumped = cfg.model_dump()
    assert dumped["model"] == "x"
    assert dumped["custom"] == "y"


def test_model_config_keeps_frozen_with_allow_override() -> None:
    """extra='allow' is a one-key config override: frozen/strict still apply.

    Guards the config-merge trap this chunk must not fall into: overriding
    a single ConfigDict key on a ConfigModel subclass must not silently
    drop the inherited frozen/strict behavior.
    """
    mc = ModelConfig(model="test/m")
    with pytest.raises(ValidationError, match="frozen"):
        mc.model = "other/m"  # pyright: ignore[reportAttributeAccessIssue]
    with pytest.raises(ValidationError):
        ModelConfig(model="test/m", max_tokens="not-an-int")  # pyright: ignore[reportArgumentType]
