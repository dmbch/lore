"""LiteLLM completion provider — Instructor-backed structured output."""

from typing import Any

import instructor
import openai
from instructor.core.exceptions import InstructorError
from openai.types.chat import (
    ChatCompletionMessageParam,
    ChatCompletionSystemMessageParam,
    ChatCompletionUserMessageParam,
)
from pydantic import BaseModel, ValidationError

from lore.domain import InferenceError
from lore.providers.config import ModelConfig


class CompletionProvider:
    def __init__(self, config: ModelConfig) -> None:
        self._config = config
        # ``async_client=True`` returns an ``AsyncInstructor`` patched
        # client. Instructor ships ``py.typed`` so pyright resolves the
        # full ``.chat.completions.create`` chain — no Any annotation
        # needed.
        self._client = instructor.from_provider(
            f"litellm/{config.model}",
            async_client=True,
        )

    async def complete[T: BaseModel](self, *, response_model: type[T], system: str, user: str) -> T:
        messages: list[ChatCompletionMessageParam] = [
            ChatCompletionSystemMessageParam(role="system", content=system),
            ChatCompletionUserMessageParam(role="user", content=user),
        ]

        # Pass-through extras: every TOML key beyond ``model`` flows to the
        # client via ``model_dump``. ModelConfig is a pass-through container
        # by design (see ``docs/architecture.md`` §LLM Providers); typed
        # fields are typed for code-side ergonomics, not because Lore
        # interprets them — they round-trip unchanged.
        extra: dict[str, Any] = self._config.model_dump(exclude={"model"}, exclude_none=True)

        try:
            result = await self._client.chat.completions.create(
                model=self._config.model,
                response_model=response_model,
                messages=messages,
                **extra,
            )
        except (openai.OpenAIError, InstructorError, ValidationError) as e:
            raise InferenceError(str(e)) from e

        return result
