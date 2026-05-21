"""Tests for resolve_dimensions — bootstrap dimension resolution."""

from unittest.mock import patch

import pytest

from lore.domain import InferenceError
from lore.providers.bootstrap import resolve_dimensions

# ---------------------------------------------------------------------------
# Configured passthrough
# ---------------------------------------------------------------------------


class TestConfiguredPassthrough:
    def test_resolve_dimensions_configured_returns_configured_value(self) -> None:
        result = resolve_dimensions(model="any-model", configured=1536)

        assert result == 1536


# ---------------------------------------------------------------------------
# Resolved from model info
# ---------------------------------------------------------------------------


class TestResolvedFromModelInfo:
    def test_resolve_dimensions_none_configured_returns_model_vector_size(self) -> None:
        with patch("lore.providers.bootstrap.litellm.get_model_info") as mock_get_model_info:
            mock_get_model_info.return_value = {"output_vector_size": 768}

            result = resolve_dimensions(model="text-embedding-3-small", configured=None)

            assert result == 768

    def test_resolve_dimensions_none_configured_calls_get_model_info(self) -> None:
        with patch("lore.providers.bootstrap.litellm.get_model_info") as mock_get_model_info:
            mock_get_model_info.return_value = {"output_vector_size": 1536}

            resolve_dimensions(model="gemini/gemini-embedding-001", configured=None)

            mock_get_model_info.assert_called_once_with("gemini/gemini-embedding-001")


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------


class TestModelUnknown:
    def test_resolve_dimensions_model_unknown_raises_inference_error(self) -> None:
        with patch("lore.providers.bootstrap.litellm.get_model_info") as mock_get_model_info:
            mock_get_model_info.side_effect = ValueError("model not found")

            with pytest.raises(InferenceError):
                resolve_dimensions(model="nonexistent-model", configured=None)

    def test_resolve_dimensions_bare_exception_raises_inference_error(self) -> None:
        # LiteLLM raises a bare Exception for unmapped models, not
        # ValueError/KeyError — the wrapper must surface a typed domain error.
        with patch("lore.providers.bootstrap.litellm.get_model_info") as mock_get_model_info:
            mock_get_model_info.side_effect = Exception("Model X isn't mapped yet")

            with pytest.raises(InferenceError):
                resolve_dimensions(model="unmapped-model", configured=None)


class TestSizeMissing:
    def test_resolve_dimensions_size_missing_raises_inference_error(self) -> None:
        with patch("lore.providers.bootstrap.litellm.get_model_info") as mock_get_model_info:
            mock_get_model_info.return_value = {}

            with pytest.raises(InferenceError):
                resolve_dimensions(model="text-embedding-3-small", configured=None)


class TestSizeNone:
    def test_resolve_dimensions_size_none_raises_inference_error(self) -> None:
        with patch("lore.providers.bootstrap.litellm.get_model_info") as mock_get_model_info:
            mock_get_model_info.return_value = {"output_vector_size": None}

            with pytest.raises(InferenceError):
                resolve_dimensions(model="text-embedding-3-small", configured=None)


class TestSizeInvalid:
    def test_resolve_dimensions_size_zero_raises_inference_error(self) -> None:
        with patch("lore.providers.bootstrap.litellm.get_model_info") as mock_get_model_info:
            mock_get_model_info.return_value = {"output_vector_size": 0}

            with pytest.raises(InferenceError):
                resolve_dimensions(model="text-embedding-3-small", configured=None)

    def test_resolve_dimensions_size_negative_raises_inference_error(self) -> None:
        with patch("lore.providers.bootstrap.litellm.get_model_info") as mock_get_model_info:
            mock_get_model_info.return_value = {"output_vector_size": -256}

            with pytest.raises(InferenceError):
                resolve_dimensions(model="text-embedding-3-small", configured=None)
