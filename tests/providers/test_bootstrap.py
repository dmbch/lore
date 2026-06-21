"""Tests for resolve_dimensions — bootstrap dimension resolution."""

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from lore.config import LoreSettings, load_settings
from lore.domain import InferenceError
from lore.providers import EmbeddingModelConfig
from lore.providers.bootstrap import resolve_dimensions

# Complete TOML with all three model roles — a valid base for model_copy.
_COMPLETE_TOML = Path(__file__).parent.parent / "fixtures" / "lore_complete.toml"

# Minimal valid env for load_settings — DATABASE_URL is the only required DSN.
_BASE_ENV = {"DATABASE_URL": "sqlite:///test.db"}


def _settings(*, model: str, dimensions: int | None) -> LoreSettings:
    """Valid base settings with only the embedding role varied.

    ``model_copy(update=...)`` deliberately bypasses validators — the base
    loaded from the complete fixture is already valid, and we only swap the
    embedding role to carry the per-test ``model``/``dimensions``.
    """
    with patch.dict(os.environ, _BASE_ENV, clear=True):
        base = load_settings(toml_path=_COMPLETE_TOML)
    return base.model_copy(
        update={"embedding": EmbeddingModelConfig(model=model, dimensions=dimensions)}
    )


# ---------------------------------------------------------------------------
# Configured passthrough
# ---------------------------------------------------------------------------


class TestConfiguredPassthrough:
    def test_resolve_dimensions_configured_returns_configured_value(self) -> None:
        result = resolve_dimensions(_settings(model="any-model", dimensions=1536))

        assert result == 1536

    def test_resolve_dimensions_configured_does_not_call_get_model_info(self) -> None:
        with patch("lore.providers.bootstrap.litellm.get_model_info") as mock_get_model_info:
            resolve_dimensions(_settings(model="any-model", dimensions=1536))

            mock_get_model_info.assert_not_called()


# ---------------------------------------------------------------------------
# Resolved from model info
# ---------------------------------------------------------------------------


class TestResolvedFromModelInfo:
    def test_resolve_dimensions_none_configured_returns_model_vector_size(self) -> None:
        with patch("lore.providers.bootstrap.litellm.get_model_info") as mock_get_model_info:
            mock_get_model_info.return_value = {"output_vector_size": 768}

            result = resolve_dimensions(_settings(model="text-embedding-3-small", dimensions=None))

            assert result == 768

    def test_resolve_dimensions_none_configured_calls_get_model_info(self) -> None:
        with patch("lore.providers.bootstrap.litellm.get_model_info") as mock_get_model_info:
            mock_get_model_info.return_value = {"output_vector_size": 1536}

            resolve_dimensions(_settings(model="gemini/gemini-embedding-001", dimensions=None))

            mock_get_model_info.assert_called_once_with("gemini/gemini-embedding-001")


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------


class TestModelUnknown:
    def test_resolve_dimensions_model_unknown_raises_inference_error(self) -> None:
        with patch("lore.providers.bootstrap.litellm.get_model_info") as mock_get_model_info:
            mock_get_model_info.side_effect = ValueError("model not found")

            with pytest.raises(InferenceError):
                resolve_dimensions(_settings(model="nonexistent-model", dimensions=None))

    def test_resolve_dimensions_bare_exception_raises_inference_error(self) -> None:
        # LiteLLM raises a bare Exception for unmapped models, not
        # ValueError/KeyError — the wrapper must surface a typed domain error.
        with patch("lore.providers.bootstrap.litellm.get_model_info") as mock_get_model_info:
            mock_get_model_info.side_effect = Exception("Model X isn't mapped yet")

            with pytest.raises(InferenceError):
                resolve_dimensions(_settings(model="unmapped-model", dimensions=None))


class TestSizeMissing:
    def test_resolve_dimensions_size_missing_raises_inference_error(self) -> None:
        with patch("lore.providers.bootstrap.litellm.get_model_info") as mock_get_model_info:
            mock_get_model_info.return_value = {}

            with pytest.raises(InferenceError):
                resolve_dimensions(_settings(model="text-embedding-3-small", dimensions=None))


class TestSizeNone:
    def test_resolve_dimensions_size_none_raises_inference_error(self) -> None:
        with patch("lore.providers.bootstrap.litellm.get_model_info") as mock_get_model_info:
            mock_get_model_info.return_value = {"output_vector_size": None}

            with pytest.raises(InferenceError):
                resolve_dimensions(_settings(model="text-embedding-3-small", dimensions=None))


class TestSizeInvalid:
    def test_resolve_dimensions_size_zero_raises_inference_error(self) -> None:
        with patch("lore.providers.bootstrap.litellm.get_model_info") as mock_get_model_info:
            mock_get_model_info.return_value = {"output_vector_size": 0}

            with pytest.raises(InferenceError):
                resolve_dimensions(_settings(model="text-embedding-3-small", dimensions=None))

    def test_resolve_dimensions_size_negative_raises_inference_error(self) -> None:
        with patch("lore.providers.bootstrap.litellm.get_model_info") as mock_get_model_info:
            mock_get_model_info.return_value = {"output_vector_size": -256}

            with pytest.raises(InferenceError):
                resolve_dimensions(_settings(model="text-embedding-3-small", dimensions=None))
