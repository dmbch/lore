# pyright: reportPrivateUsage=false
import gzip
import os
import sqlite3
import time
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import pytest
import pytest_asyncio
from pydantic import BaseModel, ConfigDict

from lore.config import load_settings
from lore.domain import ConsultLoreRequest, ConsultLoreResponse
from lore.math import EpistemicsConfig
from lore.orchestrator import Orchestrator
from lore.repositories.sqlite.pool import SqlitePool
from tests.conftest import configure_trace_sink

# Wired at import, not via fixture: same-scope autouse fixtures do not
# instantiate in definition order, so a fixture could lose the race against
# require_gemini's session skip and a keyless run would leave no trace file.
# Import happens at collection, before any fixture. scripts/rate.py sets the
# env var per run; unset means no reconfiguration.
_TRACE_LOG = os.environ.get("LORE_TRACE_LOG")
if _TRACE_LOG is not None:
    configure_trace_sink(Path(_TRACE_LOG))


@pytest.fixture(autouse=True, scope="session")
def require_gemini() -> None:
    if not os.environ.get("GEMINI_API_KEY"):
        pytest.skip("GEMINI_API_KEY not set")


async def _bootstrap(dsn: str, **overrides: object) -> AsyncGenerator[Orchestrator]:
    from lore.server import system

    os.environ.setdefault("DATABASE_URL", dsn)
    settings = load_settings()
    settings = settings.model_copy(update={"dsn": dsn, **overrides})
    async with system(settings) as orchestrator:
        yield orchestrator


# The composition root's cache-sweep task holds the pool lock across awaits, so
# fixtures and tests must share one event loop or the first consult deadlocks on pool.session().
@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def system(tmp_path_factory: pytest.TempPathFactory) -> AsyncGenerator[Orchestrator]:
    dsn = f"sqlite:///{tmp_path_factory.mktemp('lore') / 'lore.db'}"
    async for orchestrator in _bootstrap(dsn):
        yield orchestrator


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def decay_system(
    tmp_path_factory: pytest.TempPathFactory,
) -> AsyncGenerator[Orchestrator]:
    dsn = f"sqlite:///{tmp_path_factory.mktemp('lore') / 'lore.db'}"
    async for orchestrator in _bootstrap(
        dsn,
        epistemics=EpistemicsConfig(attestation_half_life=2.0, trust_half_life=2.0, maturity_k=1.0),
    ):
        yield orchestrator


_GOLDEN_ARCHIVE = Path(__file__).parent / "fixtures" / "golden.db.gz"


def _golden_copy(target_dir: Path) -> str:
    """Decompress the golden archive into target_dir and re-base its timestamps.

    Every stored timestamp shifts by one delta (now minus the newest
    attestation), so relationships between rows are preserved while seeds
    read as attested today, matching the same-session invariant the suites
    document. Plain sqlite3 suffices: no virtual table is touched. Returns
    the dsn for the copy.
    """
    if not _GOLDEN_ARCHIVE.exists():
        msg = f"golden archive missing at {_GOLDEN_ARCHIVE}: run `mise run golden-rebuild`"
        raise FileNotFoundError(msg)
    db_path = target_dir / "lore.db"
    with gzip.open(_GOLDEN_ARCHIVE, "rb") as src:
        db_path.write_bytes(src.read())
    conn = sqlite3.connect(db_path)
    try:
        (latest,) = conn.execute("SELECT max(timestamp) FROM attestations").fetchone()
        if latest is None:
            msg = "golden archive has no attestations: run `mise run golden-rebuild`"
            raise ValueError(msg)
        delta = int(time.time()) - int(latest)
        conn.execute("UPDATE attestations SET timestamp = timestamp + ?", (delta,))
        conn.execute("UPDATE requests SET timestamp = timestamp + ?", (delta,))
        conn.execute("UPDATE hypotheses SET created_at = created_at + ?", (delta,))
        conn.commit()
    finally:
        conn.close()
    return f"sqlite:///{db_path}"


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def golden_system(tmp_path_factory: pytest.TempPathFactory) -> AsyncGenerator[Orchestrator]:
    """Pre-seeded archive from the golden fixture; see tests/e2e/corpus.py."""
    dsn = _golden_copy(tmp_path_factory.mktemp("lore"))
    async for orchestrator in _bootstrap(dsn):
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


async def golden_seed_id(
    system: Orchestrator,
    correlation_id: str,
    *,
    oracle: str = "oracle-seeder",
) -> str:
    """Recover a golden seed's hypothesis id from its ledger rows."""
    rows = await attestations(system, correlation_id)
    # Single seeded hypothesis → single oracle attestation on it.
    [hypothesis_id] = {r["hypothesis_id"] for r in rows if r["oracle_id"] == oracle}
    return str(hypothesis_id)


async def age_attestations(system: Orchestrator, correlation_id: str, *, days: int) -> None:
    """Backdate a consult's ledger rows, simulating the passage of time.

    Same-session fixtures always read ``last_attested == today``; backdating
    decouples the two so temporal behavior on aged evidence is probeable.
    """
    raw = cast("SqlitePool", system._pool)._conn
    async with cast("SqlitePool", system._pool)._lock:
        await raw.execute(
            "UPDATE attestations SET timestamp = timestamp - ? WHERE correlation_id = ?",
            (days * 86400, correlation_id),
        )


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
    """Grade on the fast role: cheap; its thinking budget keeps checklist grading steady.

    Accepted cost: interpreter suites are judged by the model under test's own weights.
    """
    completer = system._providers.interpreter
    return await completer.complete(
        system=_JUDGE_SYSTEM,
        user=f"Criterion: {criterion}\n\nAnswer: {answer}",
        response_model=Verdict,
    )
