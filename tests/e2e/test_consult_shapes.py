"""Client-shaped consult probes the corpus lacked (audit SCR-12).

Four shapes: negative confidence on a paraphrase, negative confidence on a
novel, deixis grounded from `context` through the full consult path, and a
mixed-certainty compound under one scalar.

The Archivist never sees `confidence`. The sign on each ledger row is the
orchestrator's Stage 4 rule: `corroborates` and `contributes` write +c, each
`contradicts` entry writes -c. These probes pin that orchestrator behavior
downstream of a stochastic resolution.

Measurement protocol: the suite is stochastic; pin claims at k >= 5 runs
(docs/testing.md). Marked @pytest.mark.e2e, skipped without GEMINI_API_KEY
(autouse fixture in tests/e2e/conftest.py).
"""

import pytest

from lore.domain import TRANSFER_ORACLE
from lore.orchestrator import Orchestrator
from tests.e2e.conftest import attestations, consult, golden_seed_id

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio(loop_scope="session")]


async def test_negative_confidence_paraphrase_writes_disbelief_on_the_corroborated_seed(
    golden_system: Orchestrator,
) -> None:
    # "I doubt this" aimed at an existing claim: the paraphrase corroborates a
    # seed, and Stage 4 writes +c with c = -0.6, so the row carries the disbelief
    # unmodified. scen3 seeds a near-paraphrase pair and either is a defensible
    # target; the probe pins the sign rule, not target discrimination.
    seeds = {
        await golden_seed_id(golden_system, "agg-scen3-seed-1", oracle="oracle-seeder-1"),
        await golden_seed_id(golden_system, "agg-scen3-seed-2", oracle="oracle-seeder-2"),
    }

    oracle = "oracle-shape-neg-para"
    corr_id = "shapes-neg-paraphrase"
    await consult(
        golden_system,
        hypothesis="The HTTP service's internal RPC calls go over gRPC.",
        confidence=-0.6,
        oracle=oracle,
        correlation_id=corr_id,
    )

    rows = await attestations(golden_system, corr_id)
    seed_rows = [r for r in rows if r["hypothesis_id"] in seeds and r["oracle_id"] == oracle]
    assert [float(r["c_oracle_raw"]) for r in seed_rows] == [-0.6], (
        f"A paraphrase at c=-0.6 writes one row of raw disbelief on whichever "
        f"scen3 seed it corroborates, got {seed_rows} among {rows}"
    )


async def test_negative_confidence_novel_is_stored_with_its_disbelief(
    golden_system: Orchestrator,
) -> None:
    # IDEA.md's "I doubt this, but": a claim its only attester disbelieves still
    # enters the archive, carrying that disbelief. Geology is a held-out domain,
    # so the novel resolves against an empty neighborhood and no transfer lands.
    corr_id = "shapes-neg-novel"
    await consult(
        golden_system,
        hypothesis="The Brindle Ridge escarpment is composed primarily of Jurassic limestone.",
        confidence=-0.4,
        oracle="oracle-shape-neg-novel",
        correlation_id=corr_id,
    )

    rows = await attestations(golden_system, corr_id)
    oracle_rows = [r for r in rows if r["oracle_id"] != TRANSFER_ORACLE]
    assert len(oracle_rows) == 1, (
        f"A neighborless novel yields exactly one non-transfer row, got {oracle_rows}"
    )
    [row] = oracle_rows
    assert float(row["c_oracle_raw"]) == -0.4, (
        f"The novel stores the oracle's raw disbelief unmodified, got {row}"
    )
    assert float(row["c_herd"]) < 0, (
        f"A herd of one disbeliever lands negative, got c_herd={row['c_herd']}"
    )


async def test_deictic_consult_grounds_from_context_and_corroborates_the_seed(
    golden_system: Orchestrator,
) -> None:
    # The stage-level twins call interpret() directly; this is the consult-level
    # case: the referent named only in `context` must survive interpret through
    # record and land the paraphrase on the Database B seed.
    seed = await golden_seed_id(golden_system, "agg-scen1-seed")

    oracle = "oracle-shape-deixis"
    corr_id = "shapes-deixis"
    await consult(
        golden_system,
        hypothesis="It uses PostgreSQL as its storage engine.",
        context="Reviewing the storage layer of Database B.",
        confidence=0.8,
        oracle=oracle,
        correlation_id=corr_id,
    )

    rows = await attestations(golden_system, corr_id)
    seed_rows = [r for r in rows if r["hypothesis_id"] == seed and r["oracle_id"] == oracle]
    positive = [r for r in seed_rows if float(r["c_oracle_raw"]) > 0]
    assert positive, (
        f"Deixis grounded from context corroborates the Database B seed: expected a "
        f"positive row on it, got {rows}"
    )


async def test_mixed_certainty_compound_applies_one_scalar_to_every_atom(
    golden_system: Orchestrator,
) -> None:
    # One consult, one scalar. However the Interpreter splits the compound and
    # however each atom resolves, the oracle's 0.7 reaches every ledger row at
    # full magnitude; hedging lives in the text, never in a per-clause
    # confidence.
    corr_id = "shapes-compound"
    await consult(
        golden_system,
        hypothesis=(
            "Traditional balsamic vinegar from Modena is aged in wooden casks "
            "for at least twelve years, and the cask wood species may also "
            "shape its final flavor profile."
        ),
        confidence=0.7,
        oracle="oracle-shape-compound",
        correlation_id=corr_id,
    )

    rows = await attestations(golden_system, corr_id)
    oracle_rows = [r for r in rows if r["oracle_id"] != TRANSFER_ORACLE]
    assert oracle_rows, "the consult must write at least one oracle row"
    magnitudes = {abs(float(r["c_oracle_raw"])) for r in oracle_rows}
    assert magnitudes == {0.7}, (
        f"One scalar reaches every atom at full magnitude, got {oracle_rows}"
    )
