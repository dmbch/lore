"""Provider Protocols: structural subtyping contracts for inference."""

from typing import Literal, NamedTuple, Protocol

from pydantic import BaseModel

TaskTypeKey = Literal["document", "question", "verification"]


class Embedder(Protocol):
    async def embed_many(
        self, texts: list[str], *, task_type_key: TaskTypeKey | None = None
    ) -> list[list[float]]: ...


class Completer(Protocol):
    async def complete[T: BaseModel](
        self, *, response_model: type[T], system: str, user: str
    ) -> T: ...


class Providers(NamedTuple):
    embedder: Embedder
    interpreter: Completer
    archivist: Completer
