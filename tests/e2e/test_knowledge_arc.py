# pyright: reportPrivateUsage=false
"""Knowledge arc: full pipeline e2e test.

Four sequential consults exercise write, read, corroborate, and contradict
paths. Semantic assertions via LLM-as-judge alongside structural DB
verification of the epistemic state.

Each step uses an explicit correlation ID so attestations are traceable
end to end. Source order is execution order.

Epistemic values are derived from the formalism (docs/logic.md) with
K=1, base_rate=0.5, all oracles new (t_oracle=0.5), decay negligible.
"""

from typing import Any

import pytest

from lore.domain import TRANSFER_ORACLE
from lore.orchestrator import Orchestrator
from tests.e2e.conftest import attestations, consult, judge

pytestmark = pytest.mark.e2e


def _assert_invariants(row: dict[str, Any]) -> None:
    """Invariants that hold for every attestation in this scenario."""
    # All oracles are new. Trust is base rate.
    assert row["t_oracle"] == 0.5
    # Trust discount shrinks: |discounted| < |raw|
    assert abs(row["c_oracle_discounted"]) < abs(row["c_oracle_raw"])
    # Sign preserved through discounting
    if row["c_oracle_raw"] > 0:
        assert row["c_oracle_discounted"] > 0
    elif row["c_oracle_raw"] < 0:
        assert row["c_oracle_discounted"] < 0
    # Herd consensus in open interval (-1, 1) for K >= 1
    assert -1.0 < row["c_herd"] < 1.0


async def test_novel_write(system: Orchestrator) -> None:
    response = await consult(
        system,
        hypothesis="The speed of light in a vacuum is approximately 299,792 km/s",
        confidence=0.9,
        oracle="oracle-alpha",
        correlation_id="arc-01-novel",
    )
    verdict = await judge(
        system,
        answer=response.answer,
        criterion="The answer acknowledges a new claim about the speed of light",
    )
    assert verdict.passed, f"Novel write: {verdict.reasoning}"

    rows = await attestations(system, "arc-01-novel")
    assert len(rows) >= 1, "Expected at least one attestation after novel write"

    for row in rows:
        _assert_invariants(row)
        # Novel write on fresh hypothesis: M = 1/(1+1) = 0.5, P_eff = 0.5 * 0.5 = 0.25
        # c_oracle_discounted = P_eff * c_oracle_raw = 0.25 * c_oracle_raw
        c_raw = float(row["c_oracle_raw"])
        c_disc = float(row["c_oracle_discounted"])
        c_herd = float(row["c_herd"])
        assert abs(c_disc - 0.25 * c_raw) < 1e-10, f"P_eff=0.25: {c_disc} != 0.25*{c_raw}"
        # Single attestation: c_herd == c_oracle_discounted
        assert abs(c_herd - c_disc) < 1e-4, f"Single attestation: {c_herd} != {c_disc}"


async def test_read(system: Orchestrator) -> None:
    response = await consult(
        system,
        question="What is known about the speed of light?",
        correlation_id="arc-02-read",
    )
    verdict = await judge(
        system,
        answer=response.answer,
        criterion=(
            "The answer references or discusses a known claim about"
            " the speed of light being approximately 299,792 km/s"
        ),
    )
    assert verdict.passed, f"Read: {verdict.reasoning}"

    rows = await attestations(system, "arc-02-read")
    assert len(rows) == 0, "Read should not create attestations"


async def test_corroborate(system: Orchestrator) -> None:
    response = await consult(
        system,
        hypothesis="Light travels at roughly 3 times 10 to the 8th meters per second in vacuum",
        confidence=0.85,
        oracle="oracle-beta",
        correlation_id="arc-03-corroborate",
    )
    verdict = await judge(
        system,
        answer=response.answer,
        criterion=(
            "The answer acknowledges a claim about the speed of light"
            " and recognizes it relates to or supports an existing claim"
        ),
    )
    assert verdict.passed, f"Corroborate: {verdict.reasoning}"

    rows = await attestations(system, "arc-03-corroborate")
    assert len(rows) >= 1, "Corroboration should create attestations"

    for row in rows:
        _assert_invariants(row)
        # After corroboration, herd should remain positive (agreement)
        assert row["c_herd"] > 0, f"Herd should be positive after corroboration: {row['c_herd']}"

    # Agreement compounds: c_herd after corroboration > c_herd after novel write
    novel_rows = await attestations(system, "arc-01-novel")
    max_herd_after_novel = max(r["c_herd"] for r in novel_rows)
    max_herd_after_corroborate = max(r["c_herd"] for r in rows)
    assert max_herd_after_corroborate > max_herd_after_novel, (
        f"ECBF agreement should compound: {max_herd_after_corroborate} <= {max_herd_after_novel}"
    )


async def test_contradict(system: Orchestrator) -> None:
    response = await consult(
        system,
        hypothesis="The speed of light in a vacuum is approximately 150,000 km/s",
        confidence=0.7,
        oracle="oracle-gamma",
        correlation_id="arc-04-contradict",
    )
    verdict = await judge(
        system,
        answer=response.answer,
        criterion="The answer acknowledges a conflict or contradiction about the speed of light",
    )
    assert verdict.passed, f"Contradict: {verdict.reasoning}"

    rows = await attestations(system, "arc-04-contradict")
    assert len(rows) >= 1, "Contradiction should create attestations"

    for row in rows:
        if row["oracle_id"] != TRANSFER_ORACLE:
            _assert_invariants(row)

    # Transfer attestation should exist on the novel hypothesis
    transfer_rows = [r for r in rows if r["oracle_id"] == TRANSFER_ORACLE]
    assert len(transfer_rows) >= 1, "Contradiction should create a transfer attestation"
    for tr in transfer_rows:
        assert tr["t_oracle"] == 1.0
        assert tr["c_oracle_raw"] == tr["c_oracle_discounted"]
        assert tr["c_oracle_raw"] < 0, "Transfer carries negated herd state"


async def test_contradict_transfer_bounds_trust(system: Orchestrator) -> None:
    """Contrarian oracle's trust stays near base rate: no self-referential boost."""
    # Gamma makes another write to force a fresh trust computation
    await consult(
        system,
        hypothesis="The speed of light measurement requires precise instruments",
        confidence=0.6,
        oracle="oracle-gamma",
        correlation_id="arc-05-gamma-trust",
    )

    rows = await attestations(system, "arc-05-gamma-trust")
    assert len(rows) >= 1, "Gamma's second write should produce attestations"

    # Gamma's trust should be bounded near base rate (0.5), not self-boosted
    for row in rows:
        if row["oracle_id"] == "oracle-gamma":
            assert row["t_oracle"] <= 0.55, (
                f"Contrarian trust should be near base rate, got {row['t_oracle']}"
            )
