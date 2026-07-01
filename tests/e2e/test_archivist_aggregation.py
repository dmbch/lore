# pyright: reportPrivateUsage=false
"""Live-LLM regression for resolution shape invariants.

Three probe scenarios from the F-WP audit:

1. Cross-resolution disjointness: composite paraphrasing one seeded hypothesis
   produces at most one positive attestation on it from this consult.
2. Consolidated transfer: novel contradicting two seeded hypotheses produces
   exactly one transfer row plus at most one negative attestation per seed.
3. Notes channel: deliberately ambiguous composite produces a non-empty
   `notes` field, observable via the `consult.notes` log event.

Marked @pytest.mark.e2e, skipped without GEMINI_API_KEY (autouse fixture in
tests/e2e/conftest.py).
"""

import os
from collections.abc import AsyncGenerator
from typing import Any

import pytest
import structlog
from structlog.typing import EventDict

from lore.__main__ import bootstrap, setup
from lore.config import load_settings
from lore.domain import TRANSFER_ORACLE
from lore.orchestrator import Orchestrator
from tests.e2e.conftest import attestations, consult

pytestmark = pytest.mark.e2e


@pytest.fixture
async def captured_system(
    tmp_path_factory: pytest.TempPathFactory,
) -> AsyncGenerator[tuple[Orchestrator, list[EventDict]]]:
    """Per-test orchestrator with structlog event capture for inspecting consult.notes."""
    dsn = f"sqlite:///{tmp_path_factory.mktemp('lore') / 'lore.db'}"
    os.environ.setdefault("DATABASE_URL", dsn)
    settings = load_settings()
    settings = settings.model_copy(update={"dsn": dsn})
    async with setup(settings) as pool, bootstrap(settings, pool) as orchestrator:
        with structlog.testing.capture_logs() as cap:
            yield orchestrator, cap


async def _seed(
    system: Orchestrator,
    correlation_id: str,
    hypothesis: str,
    confidence: float,
    *,
    oracle: str = "oracle-seeder",
) -> str:
    """Seed a hypothesis, return its DB id."""
    await consult(
        system,
        hypothesis=hypothesis,
        confidence=confidence,
        oracle=oracle,
        correlation_id=correlation_id,
    )
    rows = await attestations(system, correlation_id)
    # Single new hypothesis → single oracle attestation on it.
    [seed_id] = {r["hypothesis_id"] for r in rows if r["oracle_id"] == oracle}
    return str(seed_id)


def _by_seed(rows: list[dict[str, Any]], seed_id: str, *, oracle: str) -> list[dict[str, Any]]:
    return [r for r in rows if r["hypothesis_id"] == seed_id and r["oracle_id"] == oracle]


async def test_paraphrase_aggregation_yields_at_most_one_positive_per_seed(
    system: Orchestrator,
) -> None:
    seed_id = await _seed(
        system,
        "agg-scen1-seed",
        "Database B is built on PostgreSQL",
        0.7,
    )

    composite_corr_id = "agg-scen1-composite"
    await consult(
        system,
        hypothesis=(
            "Database B uses PostgreSQL as its storage engine, "
            "Database B is backed by PostgreSQL, and "
            "PostgreSQL powers Database B."
        ),
        confidence=0.6,
        oracle="oracle-prober",
        correlation_id=composite_corr_id,
    )

    rows = await attestations(system, composite_corr_id)
    seed_atts = _by_seed(rows, seed_id, oracle="oracle-prober")
    positive = [r for r in seed_atts if float(r["c_oracle_raw"]) > 0]
    assert len(positive) <= 1, (
        f"Cross-resolution disjointness: at most one positive attestation on the "
        f"seeded hypothesis from this consult, got {len(positive)}: {positive}"
    )


async def test_multi_contradict_writes_consolidated_transfer(
    system: Orchestrator,
) -> None:
    seed_a = await _seed(
        system,
        "agg-scen2-seed-a",
        "All planets in the solar system orbit clockwise as seen from the north pole",
        0.7,
        oracle="oracle-seeder-a",
    )
    seed_b = await _seed(
        system,
        "agg-scen2-seed-b",
        "Mars's orbit is clockwise as seen from above the northern celestial hemisphere",
        0.7,
        oracle="oracle-seeder-b",
    )

    novel_corr_id = "agg-scen2-novel"
    await consult(
        system,
        hypothesis=(
            "Planets in the solar system orbit counter-clockwise as seen from "
            "above the northern celestial hemisphere."
        ),
        confidence=0.85,
        oracle="oracle-prober",
        correlation_id=novel_corr_id,
    )

    rows = await attestations(system, novel_corr_id)

    # Exactly one consolidated transfer row.
    transfers = [r for r in rows if r["oracle_id"] == TRANSFER_ORACLE]
    assert len(transfers) == 1, (
        f"Expected one consolidated transfer row, got {len(transfers)}: {transfers}"
    )
    [transfer] = transfers
    assert float(transfer["t_oracle"]) == 1.0
    assert float(transfer["c_oracle_raw"]) == float(transfer["c_oracle_discounted"])
    assert float(transfer["c_oracle_raw"]) < 0  # negated positive herd state

    # At most one negative attestation per seed from the prober.
    for seed_id in (seed_a, seed_b):
        seed_atts = _by_seed(rows, seed_id, oracle="oracle-prober")
        negative = [r for r in seed_atts if float(r["c_oracle_raw"]) < 0]
        assert len(negative) <= 1, (
            f"At most one negative attestation per seed, got {len(negative)} on {seed_id}"
        )


async def test_ambiguous_composite_emits_consult_notes_log(
    captured_system: tuple[Orchestrator, list[EventDict]],
) -> None:
    system, cap = captured_system

    await _seed(
        system,
        "agg-scen3-seed-1",
        "The HTTP service uses gRPC for internal RPC calls",
        0.7,
        oracle="oracle-seeder-1",
    )
    await _seed(
        system,
        "agg-scen3-seed-2",
        "Internal RPC traffic in the HTTP service is gRPC over HTTP/2",
        0.7,
        oracle="oracle-seeder-2",
    )

    await consult(
        system,
        hypothesis=(
            "The HTTP service handles internal calls — these are RPC, "
            "they may run on gRPC, and the transport may or may not be HTTP/2."
        ),
        confidence=0.4,
        oracle="oracle-prober",
        correlation_id="agg-scen3-ambiguous",
    )

    notes_events = [e for e in cap if e.get("event") == "consult.notes"]
    assert notes_events, f"Expected `consult.notes` log event for ambiguous input. Events:\n{cap}"
