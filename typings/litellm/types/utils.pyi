# Minimal stub overlay for the LiteLLM response shapes Lore consumes.
#
# Runtime ``Embedding.embedding`` is ``Union[list, str]`` because LiteLLM
# also returns base64-encoded embeddings on demand; Lore never opts in,
# so the float-list contract is the one we commit to here. Anything else
# the runtime types offer is re-exported via the package's ``py.typed``
# marker; only the fields Lore touches are tightened.

from typing import Literal

from pydantic import BaseModel

class Embedding(BaseModel):
    embedding: list[float]
    index: int
    object: Literal["embedding"]

class EmbeddingResponse(BaseModel):
    model: str | None
    data: list[Embedding]
    object: Literal["list"]
