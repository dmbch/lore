"""Tests for provider Protocols, error types, and session scope.

Dummy implementations verify Protocol shape compiles under pyright.
InferenceError mirrors StorageError in repositories — single base
exception for the provider layer.
"""

from typing import TypeVar

import pytest
from pydantic import BaseModel

from lore.domain import InferenceError
from lore.providers import Providers, TaskTypeKey
from lore.providers.protocols import Completer, Embedder

T = TypeVar("T", bound=BaseModel)


# ---------------------------------------------------------------------------
# Dummy implementations — structural subtyping verification
# ---------------------------------------------------------------------------


class _DummyEmbedding:
    """Minimal implementation satisfying Embedder shape."""

    async def embed(self, text: str, *, task_type_key: TaskTypeKey | None = None) -> list[float]:
        return [0.1, 0.2, 0.3]


class _DummyResponse(BaseModel):
    answer: str


class _DummyCompletion:
    """Minimal implementation satisfying Completer shape."""

    async def complete(self, *, response_model: type[T], system: str, user: str) -> T:
        return response_model.model_validate({"answer": "ok"})


# ---------------------------------------------------------------------------
# Embedder Protocol
# ---------------------------------------------------------------------------


class TestEmbedderProtocol:
    async def test_embed_without_task_type_returns_floats(self) -> None:
        provider: Embedder = _DummyEmbedding()
        result = await provider.embed("hello world")
        assert isinstance(result, list)
        assert all(isinstance(v, float) for v in result)

    async def test_embed_with_task_type_key_returns_floats(self) -> None:
        provider: Embedder = _DummyEmbedding()
        result = await provider.embed("hello world", task_type_key="document")
        assert isinstance(result, list)

    async def test_embed_with_none_task_type_key_returns_floats(self) -> None:
        provider: Embedder = _DummyEmbedding()
        result = await provider.embed("hello world", task_type_key=None)
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# Completer Protocol
# ---------------------------------------------------------------------------


class TestCompleterProtocol:
    async def test_complete_returns_response_model(self) -> None:
        provider: Completer = _DummyCompletion()
        result = await provider.complete(
            response_model=_DummyResponse,
            system="You are helpful.",
            user="Say ok.",
        )
        assert isinstance(result, _DummyResponse)
        assert result.answer == "ok"


# ---------------------------------------------------------------------------
# InferenceError
# ---------------------------------------------------------------------------


class TestInferenceError:
    def test_inference_error_is_exception(self) -> None:
        assert issubclass(InferenceError, Exception)

    def test_inference_error_preserves_message(self) -> None:
        error = InferenceError("model unavailable")
        assert str(error) == "model unavailable"

    def test_inference_error_raised_preserves_message(self) -> None:
        with pytest.raises(InferenceError, match="timeout"):
            raise InferenceError("timeout")


# ---------------------------------------------------------------------------
# Providers session — memoized embedding scope
# ---------------------------------------------------------------------------


class TestProvidersSession:
    """Providers.session() yields a bundle with memoized embedding."""

    async def test_session_passes_through_completion_providers(self) -> None:
        """session wraps embedder but passes interpreter and archivist through unchanged."""
        embedder = _DummyEmbedding()
        interpreter = _DummyCompletion()
        archivist = _DummyCompletion()
        providers = Providers(embedder=embedder, interpreter=interpreter, archivist=archivist)
        async with providers.session() as scoped:
            assert scoped.interpreter is interpreter
            assert scoped.archivist is archivist

    async def test_session_memoizes_embeddings(self) -> None:
        call_count = 0

        class _CountingEmbedder:
            async def embed(
                self, text: str, *, task_type_key: TaskTypeKey | None = None
            ) -> list[float]:
                nonlocal call_count
                call_count += 1
                return [0.1, 0.2, 0.3]

        providers = Providers(
            embedder=_CountingEmbedder(),
            interpreter=_DummyCompletion(),
            archivist=_DummyCompletion(),
        )
        async with providers.session() as scoped:
            v1 = await scoped.embedder.embed("hello", task_type_key="document")
            v2 = await scoped.embedder.embed("hello", task_type_key="document")
            assert v1 == v2
            assert call_count == 1

    async def test_session_different_keys_not_cached(self) -> None:
        call_count = 0

        class _CountingEmbedder:
            async def embed(
                self, text: str, *, task_type_key: TaskTypeKey | None = None
            ) -> list[float]:
                nonlocal call_count
                call_count += 1
                return [float(call_count)] * 3

        providers = Providers(
            embedder=_CountingEmbedder(),
            interpreter=_DummyCompletion(),
            archivist=_DummyCompletion(),
        )
        async with providers.session() as scoped:
            v1 = await scoped.embedder.embed("hello", task_type_key="document")
            v2 = await scoped.embedder.embed("hello", task_type_key="question")
            assert v1 != v2
            assert call_count == 2

    async def test_session_deduplicates_when_resolver_collapses_keys(self) -> None:
        call_count = 0

        class _CountingEmbedderWithResolver:
            async def embed(
                self, text: str, *, task_type_key: TaskTypeKey | None = None
            ) -> list[float]:
                nonlocal call_count
                call_count += 1
                return [0.1, 0.2, 0.3]

            def resolve_task_type(self, key: TaskTypeKey | None) -> str | None:
                return None

        providers = Providers(
            embedder=_CountingEmbedderWithResolver(),
            interpreter=_DummyCompletion(),
            archivist=_DummyCompletion(),
        )
        async with providers.session() as scoped:
            v1 = await scoped.embedder.embed("hello", task_type_key="question")
            v2 = await scoped.embedder.embed("hello", task_type_key="document")
            assert v1 == v2
            assert call_count == 1

    async def test_session_cache_scoped_to_context(self) -> None:
        call_count = 0

        class _CountingEmbedder:
            async def embed(
                self, text: str, *, task_type_key: TaskTypeKey | None = None
            ) -> list[float]:
                nonlocal call_count
                call_count += 1
                return [0.1, 0.2, 0.3]

        providers = Providers(
            embedder=_CountingEmbedder(),
            interpreter=_DummyCompletion(),
            archivist=_DummyCompletion(),
        )
        async with providers.session() as scoped:
            await scoped.embedder.embed("hello")
        assert call_count == 1

        async with providers.session() as scoped2:
            await scoped2.embedder.embed("hello")
        assert call_count == 2
