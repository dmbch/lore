# pyright: reportPrivateUsage=false
import os
from collections.abc import AsyncGenerator
from typing import Any, cast
from uuid import uuid4

import pytest
from pydantic import BaseModel, ConfigDict

from lore.config import load_settings
from lore.domain import ConsultLoreRequest, ConsultLoreResponse
from lore.math import EpistemicsConfig
from lore.orchestrator import Orchestrator
from lore.repositories.sqlite.pool import SqlitePool


@pytest.fixture(autouse=True, scope="session")
def require_gemini() -> None:
    if not os.environ.get("GEMINI_API_KEY"):
        pytest.skip("GEMINI_API_KEY not set")


async def _bootstrap(dsn: str, **overrides: object) -> AsyncGenerator[Orchestrator]:
    from lore.__main__ import bootstrap, setup

    os.environ.setdefault("DATABASE_URL", dsn)
    settings = load_settings()
    settings = settings.model_copy(update={"dsn": dsn, **overrides})
    async with setup(settings) as pool, bootstrap(settings, pool) as orchestrator:
        yield orchestrator


@pytest.fixture(scope="session")
async def system(tmp_path_factory: pytest.TempPathFactory) -> AsyncGenerator[Orchestrator]:
    dsn = f"sqlite:///{tmp_path_factory.mktemp('lore') / 'lore.db'}"
    async for orchestrator in _bootstrap(dsn):
        yield orchestrator


@pytest.fixture(scope="session")
async def decay_system(
    tmp_path_factory: pytest.TempPathFactory,
) -> AsyncGenerator[Orchestrator]:
    dsn = f"sqlite:///{tmp_path_factory.mktemp('lore') / 'lore.db'}"
    async for orchestrator in _bootstrap(
        dsn,
        epistemics=EpistemicsConfig(attestation_half_life=2.0, trust_half_life=2.0, maturity_k=1.0),
    ):
        yield orchestrator


async def attestations(
    system: Orchestrator,
    correlation_id: str,
) -> list[dict[str, Any]]:
    """Fetch attestations for a specific consult call."""
    raw = cast("SqlitePool", system._pool)._conn
    async with cast("SqlitePool", system._pool)._lock:
        cursor = await raw.execute(
            "SELECT * FROM attestations WHERE correlation_id = ?",
            (correlation_id,),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def consult(
    system: Orchestrator,
    *,
    question: str | None = None,
    hypothesis: str | None = None,
    confidence: float | None = None,
    context: str | None = None,
    reasoning: str | None = None,
    oracle: str = "e2e",
    correlation_id: str | None = None,
) -> ConsultLoreResponse:
    request = ConsultLoreRequest(
        question=question,
        hypothesis=hypothesis,
        confidence=confidence,
        context=context,
        reasoning=reasoning,
    )
    return await system.consult(
        oracle_id=oracle,
        request=request,
        correlation_id=correlation_id or str(uuid4()),
    )


class Verdict(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)

    reasoning: str
    passed: bool


_JUDGE_SYSTEM = (
    "You are a test judge. Does the answer meet the criterion?"
    " Ignore verbosity, formatting, and phrasing: only semantic content matters."
    "\n\nThink step by step in the reasoning field, then set passed."
)


async def judge(
    system: Orchestrator,
    *,
    answer: str,
    criterion: str,
) -> Verdict:
    return await system._providers.interpreter.complete(
        system=_JUDGE_SYSTEM,
        user=f"Criterion: {criterion}\n\nAnswer: {answer}",
        response_model=Verdict,
    )
