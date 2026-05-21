"""Provider Protocols — structural subtyping contracts for inference.

Protocols live alongside the layer they abstract. Implementations just
match the shape — no inheritance required.

See docs/architecture.md: "LLM Providers define and own their Protocols."
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Literal, NamedTuple, Protocol

from pydantic import BaseModel

TaskTypeKey = Literal["document", "question", "verification"]


class Embedder(Protocol):
    """Embed text into a vector space."""

    async def embed(
        self, text: str, *, task_type_key: TaskTypeKey | None = None
    ) -> list[float]: ...


class Completer(Protocol):
    """Structured LLM completion via Pydantic response models."""

    async def complete[T: BaseModel](
        self, *, response_model: type[T], system: str, user: str
    ) -> T: ...


class Providers(NamedTuple):
    """Bundle of all provider Protocols. Mirrors Repositories in the repo layer."""

    embedder: Embedder
    interpreter: Completer
    archivist: Completer

    @asynccontextmanager
    async def session(self) -> AsyncGenerator[Providers]:
        """Per-request scope with memoized embedder."""
        from lore.providers._cache import CachedEmbedder

        yield Providers(
            embedder=CachedEmbedder(self.embedder),
            interpreter=self.interpreter,
            archivist=self.archivist,
        )
