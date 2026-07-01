"""Tests for EmbeddingProvider: LiteLLM-backed embedding provider."""

from unittest.mock import AsyncMock, patch

import openai
import pytest
from litellm.types.utils import Embedding, EmbeddingResponse

from lore.domain import InferenceError
from lore.providers import EmbeddingModelConfig, TaskTypeConfig
from lore.providers.embedding import EmbeddingProvider


def _make_embedding_response(embedding: list[float]) -> EmbeddingResponse:
    """Build a real EmbeddingResponse to mimic the LiteLLM contract."""
    return EmbeddingResponse(
        model="test-model",
        data=[Embedding(embedding=embedding, index=0, object="embedding")],
        object="list",
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestEmbedHappyPath:
    async def test_embed_returns_embedding_vector(self) -> None:
        config = EmbeddingModelConfig(model="text-embedding-3-small")
        provider = EmbeddingProvider(config)
        expected = [0.1, 0.2, 0.3]

        with patch("lore.providers.embedding.litellm") as mock_litellm:
            mock_litellm.aembedding = AsyncMock(
                return_value=_make_embedding_response(expected),
            )
            result = await provider.embed("hello world")

        assert result == expected

    async def test_embed_passes_model_to_litellm(self) -> None:
        config = EmbeddingModelConfig(model="text-embedding-3-small")
        provider = EmbeddingProvider(config)

        with patch("lore.providers.embedding.litellm") as mock_litellm:
            mock_litellm.aembedding = AsyncMock(
                return_value=_make_embedding_response([0.1]),
            )
            await provider.embed("test")

        mock_litellm.aembedding.assert_called_once()
        call_kwargs = mock_litellm.aembedding.call_args
        assert call_kwargs.kwargs["model"] == "text-embedding-3-small"
        assert call_kwargs.kwargs["input"] == ["test"]


# ---------------------------------------------------------------------------
# Dimensions passthrough
# ---------------------------------------------------------------------------


class TestEmbedDimensions:
    async def test_embed_with_dimensions_passes_to_litellm(self) -> None:
        config = EmbeddingModelConfig(model="text-embedding-3-small", dimensions=256)
        provider = EmbeddingProvider(config)

        with patch("lore.providers.embedding.litellm") as mock_litellm:
            mock_litellm.aembedding = AsyncMock(
                return_value=_make_embedding_response([0.1, 0.2]),
            )
            await provider.embed("test")

        call_kwargs = mock_litellm.aembedding.call_args.kwargs
        assert call_kwargs["dimensions"] == 256


# ---------------------------------------------------------------------------
# Task type resolution
# ---------------------------------------------------------------------------


class TestEmbedTaskType:
    async def test_embed_with_task_type_key_resolves_vendor_string(self) -> None:
        task_type = TaskTypeConfig(document="RETRIEVAL_DOCUMENT")
        config = EmbeddingModelConfig(
            model="gemini/gemini-embedding-001",
            task_type=task_type,
        )
        provider = EmbeddingProvider(config)

        with patch("lore.providers.embedding.litellm") as mock_litellm:
            mock_litellm.aembedding = AsyncMock(
                return_value=_make_embedding_response([0.1]),
            )
            await provider.embed("test", task_type_key="document")

        call_kwargs = mock_litellm.aembedding.call_args.kwargs
        assert call_kwargs["task_type"] == "RETRIEVAL_DOCUMENT"

    async def test_embed_with_question_task_type_resolves_correctly(self) -> None:
        task_type = TaskTypeConfig(question="QUESTION_ANSWERING")
        config = EmbeddingModelConfig(
            model="gemini/gemini-embedding-001",
            task_type=task_type,
        )
        provider = EmbeddingProvider(config)

        with patch("lore.providers.embedding.litellm") as mock_litellm:
            mock_litellm.aembedding = AsyncMock(
                return_value=_make_embedding_response([0.1]),
            )
            await provider.embed("test", task_type_key="question")

        call_kwargs = mock_litellm.aembedding.call_args.kwargs
        assert call_kwargs["task_type"] == "QUESTION_ANSWERING"

    async def test_embed_with_none_task_type_value_omits_kwarg(self) -> None:
        task_type = TaskTypeConfig(verification=None)
        config = EmbeddingModelConfig(
            model="gemini/gemini-embedding-001",
            task_type=task_type,
        )
        provider = EmbeddingProvider(config)

        with patch("lore.providers.embedding.litellm") as mock_litellm:
            mock_litellm.aembedding = AsyncMock(
                return_value=_make_embedding_response([0.1]),
            )
            await provider.embed("test", task_type_key="verification")

        call_kwargs = mock_litellm.aembedding.call_args.kwargs
        assert "task_type" not in call_kwargs


# ---------------------------------------------------------------------------
# Omitted optionals
# ---------------------------------------------------------------------------


class TestEmbedOmittedOptionals:
    async def test_embed_without_dimensions_omits_kwarg(self) -> None:
        config = EmbeddingModelConfig(model="text-embedding-3-small")
        provider = EmbeddingProvider(config)

        with patch("lore.providers.embedding.litellm") as mock_litellm:
            mock_litellm.aembedding = AsyncMock(
                return_value=_make_embedding_response([0.1]),
            )
            await provider.embed("test")

        call_kwargs = mock_litellm.aembedding.call_args.kwargs
        assert "dimensions" not in call_kwargs

    async def test_embed_without_task_type_config_omits_kwarg(self) -> None:
        config = EmbeddingModelConfig(model="text-embedding-3-small")
        provider = EmbeddingProvider(config)

        with patch("lore.providers.embedding.litellm") as mock_litellm:
            mock_litellm.aembedding = AsyncMock(
                return_value=_make_embedding_response([0.1]),
            )
            await provider.embed("test")

        call_kwargs = mock_litellm.aembedding.call_args.kwargs
        assert "task_type" not in call_kwargs

    async def test_embed_with_task_type_key_but_no_config_omits_kwarg(self) -> None:
        config = EmbeddingModelConfig(model="text-embedding-3-small")
        provider = EmbeddingProvider(config)

        with patch("lore.providers.embedding.litellm") as mock_litellm:
            mock_litellm.aembedding = AsyncMock(
                return_value=_make_embedding_response([0.1]),
            )
            await provider.embed("test", task_type_key="document")

        call_kwargs = mock_litellm.aembedding.call_args.kwargs
        assert "task_type" not in call_kwargs


# ---------------------------------------------------------------------------
# Error mapping
# ---------------------------------------------------------------------------


class TestEmbedErrorMapping:
    async def test_embed_maps_openai_error_to_inference_error(self) -> None:
        config = EmbeddingModelConfig(model="text-embedding-3-small")
        provider = EmbeddingProvider(config)

        with patch("lore.providers.embedding.litellm") as mock_litellm:
            mock_litellm.aembedding = AsyncMock(
                side_effect=openai.OpenAIError("rate limited"),
            )
            with pytest.raises(InferenceError, match="rate limited"):
                await provider.embed("test")

    async def test_embed_preserves_original_error_as_cause(self) -> None:
        config = EmbeddingModelConfig(model="text-embedding-3-small")
        provider = EmbeddingProvider(config)
        original = openai.OpenAIError("timeout")

        with patch("lore.providers.embedding.litellm") as mock_litellm:
            mock_litellm.aembedding = AsyncMock(side_effect=original)
            with pytest.raises(InferenceError) as exc_info:
                await provider.embed("test")

        assert exc_info.value.__cause__ is original


# ---------------------------------------------------------------------------
# Pass-through extras (B6 / S2.6)
# ---------------------------------------------------------------------------


class TestEmbedPassThroughExtras:
    """``EmbeddingModelConfig`` is a pass-through container for LiteLLM kwargs.

    Vendor-specific keys beyond what Lore types itself flow into the
    LiteLLM call unchanged. The provider does not enumerate them. That is
    the design commitment (see ``docs/architecture.md`` §LLM Providers).
    """

    async def test_embed_extra_config_field_passes_through_to_litellm_kwargs(self) -> None:
        config = EmbeddingModelConfig.model_validate(
            {"model": "text-embedding-3-small", "vendor_specific_knob": "deep"}
        )
        provider = EmbeddingProvider(config)

        with patch("lore.providers.embedding.litellm") as mock_litellm:
            mock_litellm.aembedding = AsyncMock(
                return_value=_make_embedding_response([0.1]),
            )
            await provider.embed("test")

        call_kwargs = mock_litellm.aembedding.call_args.kwargs
        assert call_kwargs["vendor_specific_knob"] == "deep"

    async def test_embed_typed_field_is_not_duplicated_in_extra(self) -> None:
        """The typed ``model`` is bound positionally; it must not also appear via extras."""
        config = EmbeddingModelConfig(model="text-embedding-3-small", dimensions=256)
        provider = EmbeddingProvider(config)

        with patch("lore.providers.embedding.litellm") as mock_litellm:
            mock_litellm.aembedding = AsyncMock(
                return_value=_make_embedding_response([0.1]),
            )
            await provider.embed("test")

        call_args = mock_litellm.aembedding.call_args
        # ``model`` is the only allowed positional/keyword binding for the
        # field. We forbid double-binding (TypeError on real call).
        all_kwargs = list(call_args.kwargs.keys())
        assert all_kwargs.count("model") == 1
