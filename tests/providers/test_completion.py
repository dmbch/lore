"""Tests for CompletionProvider: Instructor-backed structured completion provider."""

from collections.abc import Generator
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import openai
import pytest
from instructor.core.exceptions import (
    IncompleteOutputException,
    InstructorRetryException,
)
from pydantic import BaseModel, ValidationError

from lore.domain import InferenceError
from lore.providers import ModelConfig
from lore.providers.completion import CompletionProvider


class _DummyResponse(BaseModel):
    answer: str


class _CompletionHarness:
    """Pre-wired CompletionProvider with accessible mocks."""

    def __init__(self, mock_instructor: MagicMock, mock_create: AsyncMock) -> None:
        self.mock_instructor = mock_instructor
        self.mock_create = mock_create


@contextmanager
def _make_provider(
    config: ModelConfig, mock_create: AsyncMock
) -> Generator[tuple[CompletionProvider, _CompletionHarness]]:
    """Build a CompletionProvider with patched Instructor and wired mock_create."""
    mock_client = MagicMock()
    mock_client.chat.completions.create = mock_create
    with patch("lore.providers.completion.instructor") as mock_instructor:
        mock_instructor.from_provider.return_value = mock_client
        provider = CompletionProvider(config)
        yield provider, _CompletionHarness(mock_instructor, mock_create)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestCompleteHappyPath:
    async def test_complete_returns_validated_pydantic_model(self) -> None:
        config = ModelConfig(model="gpt-4.1-mini")
        expected = _DummyResponse(answer="hello")
        mock_create = AsyncMock(return_value=expected)

        with _make_provider(config, mock_create) as (provider, _):
            result = await provider.complete(
                response_model=_DummyResponse,
                system="You are helpful.",
                user="Say hello.",
            )

        assert result == expected

    async def test_complete_passes_model_to_create(self) -> None:
        config = ModelConfig(model="gpt-4.1-mini")
        mock_create = AsyncMock(return_value=_DummyResponse(answer="ok"))

        with _make_provider(config, mock_create) as (provider, harness):
            await provider.complete(
                response_model=_DummyResponse,
                system="sys",
                user="usr",
            )

        assert harness.mock_create.call_args.kwargs["model"] == "gpt-4.1-mini"

    async def test_complete_constructs_messages_from_system_and_user(self) -> None:
        config = ModelConfig(model="gpt-4.1-mini")
        mock_create = AsyncMock(return_value=_DummyResponse(answer="ok"))

        with _make_provider(config, mock_create) as (provider, harness):
            await provider.complete(
                response_model=_DummyResponse,
                system="sys prompt",
                user="usr prompt",
            )

        call_kwargs = harness.mock_create.call_args.kwargs
        assert call_kwargs["messages"] == [
            {"role": "system", "content": "sys prompt"},
            {"role": "user", "content": "usr prompt"},
        ]

    async def test_complete_passes_response_model_to_create(self) -> None:
        config = ModelConfig(model="gpt-4.1-mini")
        mock_create = AsyncMock(return_value=_DummyResponse(answer="ok"))

        with _make_provider(config, mock_create) as (provider, harness):
            await provider.complete(
                response_model=_DummyResponse,
                system="sys",
                user="usr",
            )

        call_kwargs = harness.mock_create.call_args.kwargs
        assert call_kwargs["response_model"] is _DummyResponse


# ---------------------------------------------------------------------------
# Parameter passthrough
# ---------------------------------------------------------------------------


class TestCompleteParameterPassthrough:
    async def test_complete_with_temperature_passes_to_create(self) -> None:
        config = ModelConfig(model="gpt-4.1-mini", temperature=0.7)
        mock_create = AsyncMock(return_value=_DummyResponse(answer="ok"))

        with _make_provider(config, mock_create) as (provider, harness):
            await provider.complete(
                response_model=_DummyResponse,
                system="sys",
                user="usr",
            )

        assert harness.mock_create.call_args.kwargs["temperature"] == 0.7

    async def test_complete_with_max_tokens_passes_to_create(self) -> None:
        config = ModelConfig(model="gpt-4.1-mini", max_tokens=1024)
        mock_create = AsyncMock(return_value=_DummyResponse(answer="ok"))

        with _make_provider(config, mock_create) as (provider, harness):
            await provider.complete(
                response_model=_DummyResponse,
                system="sys",
                user="usr",
            )

        assert harness.mock_create.call_args.kwargs["max_tokens"] == 1024

    async def test_complete_with_reasoning_effort_passes_to_create(self) -> None:
        config = ModelConfig(model="o4-mini", reasoning_effort="medium")
        mock_create = AsyncMock(return_value=_DummyResponse(answer="ok"))

        with _make_provider(config, mock_create) as (provider, harness):
            await provider.complete(
                response_model=_DummyResponse,
                system="sys",
                user="usr",
            )

        assert harness.mock_create.call_args.kwargs["reasoning_effort"] == "medium"


# ---------------------------------------------------------------------------
# Omitted optionals
# ---------------------------------------------------------------------------


class TestCompleteOmittedOptionals:
    async def test_complete_without_temperature_omits_kwarg(self) -> None:
        config = ModelConfig(model="gpt-4.1-mini")
        mock_create = AsyncMock(return_value=_DummyResponse(answer="ok"))

        with _make_provider(config, mock_create) as (provider, harness):
            await provider.complete(
                response_model=_DummyResponse,
                system="sys",
                user="usr",
            )

        assert "temperature" not in harness.mock_create.call_args.kwargs

    async def test_complete_without_max_tokens_omits_kwarg(self) -> None:
        config = ModelConfig(model="gpt-4.1-mini")
        mock_create = AsyncMock(return_value=_DummyResponse(answer="ok"))

        with _make_provider(config, mock_create) as (provider, harness):
            await provider.complete(
                response_model=_DummyResponse,
                system="sys",
                user="usr",
            )

        assert "max_tokens" not in harness.mock_create.call_args.kwargs

    async def test_complete_without_reasoning_effort_omits_kwarg(self) -> None:
        config = ModelConfig(model="gpt-4.1-mini")
        mock_create = AsyncMock(return_value=_DummyResponse(answer="ok"))

        with _make_provider(config, mock_create) as (provider, harness):
            await provider.complete(
                response_model=_DummyResponse,
                system="sys",
                user="usr",
            )

        assert "reasoning_effort" not in harness.mock_create.call_args.kwargs


# ---------------------------------------------------------------------------
# Error mapping
# ---------------------------------------------------------------------------


class TestCompleteErrorMapping:
    async def test_complete_maps_openai_error_to_inference_error(self) -> None:
        config = ModelConfig(model="gpt-4.1-mini")
        mock_create = AsyncMock(side_effect=openai.OpenAIError("rate limited"))

        with (
            _make_provider(config, mock_create) as (provider, _),
            pytest.raises(InferenceError, match="rate limited"),
        ):
            await provider.complete(
                response_model=_DummyResponse,
                system="sys",
                user="usr",
            )

    async def test_complete_preserves_original_error_as_cause(self) -> None:
        config = ModelConfig(model="gpt-4.1-mini")
        original = openai.OpenAIError("timeout")
        mock_create = AsyncMock(side_effect=original)

        with (
            _make_provider(config, mock_create) as (provider, _),
            pytest.raises(InferenceError) as exc_info,
        ):
            await provider.complete(
                response_model=_DummyResponse,
                system="sys",
                user="usr",
            )

        assert exc_info.value.__cause__ is original

    async def test_complete_maps_instructor_retry_error_to_inference_error(
        self,
    ) -> None:
        config = ModelConfig(model="gpt-4.1-mini")
        original = InstructorRetryException(
            last_completion=None, messages=[], n_attempts=3, total_usage=0
        )
        mock_create = AsyncMock(side_effect=original)

        with (
            _make_provider(config, mock_create) as (provider, _),
            pytest.raises(InferenceError) as exc_info,
        ):
            await provider.complete(
                response_model=_DummyResponse,
                system="sys",
                user="usr",
            )

        assert exc_info.value.__cause__ is original

    async def test_complete_maps_incomplete_output_error_to_inference_error(
        self,
    ) -> None:
        config = ModelConfig(model="gpt-4.1-mini")
        original = IncompleteOutputException(last_completion=None)
        mock_create = AsyncMock(side_effect=original)

        with (
            _make_provider(config, mock_create) as (provider, _),
            pytest.raises(InferenceError) as exc_info,
        ):
            await provider.complete(
                response_model=_DummyResponse,
                system="sys",
                user="usr",
            )

        assert exc_info.value.__cause__ is original

    async def test_complete_maps_pydantic_validation_error_to_inference_error(
        self,
    ) -> None:
        config = ModelConfig(model="gpt-4.1-mini")

        with pytest.raises(ValidationError) as caught:
            _DummyResponse.model_validate({})
        original = caught.value

        mock_create = AsyncMock(side_effect=original)

        with (
            _make_provider(config, mock_create) as (provider, _),
            pytest.raises(InferenceError) as exc_info,
        ):
            await provider.complete(
                response_model=_DummyResponse,
                system="sys",
                user="usr",
            )

        assert exc_info.value.__cause__ is original


# ---------------------------------------------------------------------------
# Pass-through extras (B6 / S2.6)
# ---------------------------------------------------------------------------


class TestCompletePassThroughExtras:
    """``ModelConfig`` is a pass-through container for completion kwargs.

    Vendor-specific keys beyond what Lore types itself flow into the
    Instructor + LiteLLM call unchanged. The provider does not enumerate
    them. That is the design commitment (see ``docs/architecture.md``
    §LLM Providers).
    """

    async def test_complete_extra_config_field_passes_through_to_create(self) -> None:
        config = ModelConfig.model_validate(
            {"model": "gpt-4.1-mini", "vendor_specific_knob": "deep"}
        )
        mock_create = AsyncMock(return_value=_DummyResponse(answer="hi"))
        with _make_provider(config, mock_create) as (provider, harness):
            await provider.complete(
                response_model=_DummyResponse,
                system="sys",
                user="usr",
            )
        assert harness.mock_create.call_args.kwargs["vendor_specific_knob"] == "deep"

    async def test_complete_typed_field_is_not_duplicated_in_extra(self) -> None:
        """``model`` is bound by the typed surface; it must not appear twice."""
        config = ModelConfig(model="gpt-4.1-mini", temperature=0.5)
        mock_create = AsyncMock(return_value=_DummyResponse(answer="hi"))
        with _make_provider(config, mock_create) as (provider, harness):
            await provider.complete(
                response_model=_DummyResponse,
                system="sys",
                user="usr",
            )
        kwargs = harness.mock_create.call_args.kwargs
        assert list(kwargs.keys()).count("model") == 1
