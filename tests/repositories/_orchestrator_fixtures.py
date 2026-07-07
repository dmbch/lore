"""Shared scaffolding for orchestrator-integration tests.

Three real-backend tests build an Orchestrator with stub providers and
the real repository pool: ``test_orchestrator_orphan_request``,
``test_recorder_atomicity``, ``test_recorder_aggregation``. Each needs
the same minimal ``LoreSettings``, ``MathService``, and Protocol-shaped
provider stubs. Centralizing them here keeps a future ``LoreSettings``
field from drifting across call sites.
"""

import importlib.resources
from pathlib import Path
from typing import cast

from pydantic import BaseModel

from lore.adapter import LimitsConfig
from lore.config import LoreSettings
from lore.math import EpistemicsConfig, MathService
from lore.prompts import PromptsConfig
from lore.providers import EmbeddingModelConfig, ModelConfig
from lore.repositories import PostgresConfig, RetrievalConfig, SqliteConfig

_DEFAULT_POSTGRES = PostgresConfig(min_size=1, max_size=20, timeout=10.0, max_waiting=50)
_DEFAULT_SQLITE = SqliteConfig()


class StubCompletion:
    """Fixed-response completion stub satisfying Completer Protocol."""

    def __init__(self, output: BaseModel) -> None:
        self._output = output

    async def complete[T: BaseModel](self, *, response_model: type[T], system: str, user: str) -> T:
        return cast("T", self._output)


class FixedEmbedder:
    """Returns a fixed non-zero vector: matches SCHEMA_DIM in conftest."""

    async def embed(self, text: str, *, task_type_key: str | None = None) -> list[float]:
        return [0.1] * 1024


def bundled_prompt(name: str) -> Path:
    return Path(str(importlib.resources.files("lore.prompts").joinpath(f"{name}.md")))


def make_settings(
    *,
    dsn: str = "sqlite:///:memory:",
    embedding_model: str = "test/embed",
    postgres: PostgresConfig = _DEFAULT_POSTGRES,
    sqlite: SqliteConfig = _DEFAULT_SQLITE,
) -> LoreSettings:
    return LoreSettings(
        dsn=dsn,
        oidc=None,
        epistemics=EpistemicsConfig(
            attestation_half_life=86400.0, trust_half_life=86400.0, maturity_k=1.0
        ),
        embedding=EmbeddingModelConfig(model=embedding_model, dimensions=3),
        fast=ModelConfig(model="test/fast"),
        reasoning=ModelConfig(model="test/reasoning"),
        limits=LimitsConfig(
            question=10000,
            hypothesis=10000,
            context=10000,
            reasoning=10000,
        ),
        retrieval=RetrievalConfig(
            proximity=0.5, authority=0.5, limit=10, fan_out=2, max_keywords=1000
        ),
        postgres=postgres,
        sqlite=sqlite,
        prompts=PromptsConfig(
            scribe=bundled_prompt("scribe"),
            interpreter=bundled_prompt("interpreter"),
            archivist=bundled_prompt("archivist"),
            contract=bundled_prompt("contract"),
        ),
    )


def make_math() -> MathService:
    return MathService(c_half_life=86400.0, maturity_k=1.0, t_half_life=86400.0)
