"""Provider Protocols — structural subtyping contracts for inference."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Literal, NamedTuple, Protocol

from pydantic import BaseModel

TaskTypeKey = Literal["document", "question", "verification"]


class Embedder(Protocol):
    async def embed(
        self, text: str, *, task_type_key: TaskTypeKey | None = None
    ) -> list[float]: ...


class Completer(Protocol):
    async def complete[T: BaseModel](
        self, *, response_model: type[T], system: str, user: str
    ) -> T: ...


class Providers(NamedTuple):
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
