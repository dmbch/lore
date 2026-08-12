# pyright: reportPrivateUsage=false
"""Live-LLM regression for resolution shape invariants.

Three probe scenarios from the F-WP audit:

1. Cross-resolution disjointness: composite paraphrasing one seeded hypothesis
   produces at most one positive attestation on it from this consult.
2. Consolidated transfer: novel contradicting two seeded hypotheses produces
   exactly one transfer row plus at most one negative attestation per seed.
3. Notes channel: deliberately ambiguous composite produces a non-empty
   `notes` field, observable via the `consult.notes` log event.

Temporal probes for the archivist. A `contradicts` writes disbelief (the claim
is false, not old) and decay already prices age, so two claims conflict only
when they cannot both be true of the world. Same-session fixtures always read
`last_attested == today`; cases 7 and 8 backdate the seed's ledger rows to
decouple the two.

4. Two claims at different reference times do not contradict: an `as of 2010`
   reading and an `as of 2025` reading are both true, so no disbelief lands on
   the older.
5. Two claims at the same reference time can contradict: undated present-tense
   claims that state incompatible values collide.
6. Complexity tier: one paragraph-scale consult mixing a dated historical event
   and a differently-referenced value update writes no false contradiction on
   either seed.
7. Aging does not immunize: an undated standing claim seeded 90 days back is
   still contradicted by an incompatible present claim.
8. Aging does not indict: an old `as of <date>` snapshot is not contradicted by
   a newer reading.

Seeds arrange in the golden archive (tests/e2e/corpus.py, rebuilt via
`mise run golden-rebuild`); probes stay live.

Marked @pytest.mark.e2e, skipped without GEMINI_API_KEY (autouse fixture in
tests/e2e/conftest.py).
"""

from collections.abc import AsyncGenerator
from typing import Any

import pytest
import pytest_asyncio
import structlog
from structlog.typing import EventDict

from lore.domain import TRANSFER_ORACLE
from lore.orchestrator import Orchestrator
from tests.e2e.conftest import (
    _bootstrap,
    age_attestations,
    attestations,
    consult,
    golden_seed_id,
)
from tests.e2e.fixtures.golden import golden_copy

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio(loop_scope="session")]


@pytest_asyncio.fixture(loop_scope="session")
async def captured_system(
    tmp_path_factory: pytest.TempPathFactory,
) -> AsyncGenerator[tuple[Orchestrator, list[EventDict]]]:
    """Per-test golden-archive copy with structlog capture for inspecting consult.notes."""
    dsn = golden_copy(tmp_path_factory.mktemp("lore"))
    async for orchestrator in _bootstrap(dsn):
        with structlog.testing.capture_logs() as cap:
            yield orchestrator, cap


def _by_seed(
    rows: list[dict[str, Any]], hypothesis_id: str, *, oracle: str
) -> list[dict[str, Any]]:
    return [r for r in rows if r["hypothesis_id"] == hypothesis_id and r["oracle_id"] == oracle]


async def test_paraphrase_aggregation_yields_at_most_one_positive_per_seed(
    golden_system: Orchestrator,
) -> None:
    seed = await golden_seed_id(golden_system, "agg-scen1-seed")

    composite_corr_id = "agg-scen1-composite"
    await consult(
        golden_system,
        hypothesis=(
            "Database B uses PostgreSQL as its storage engine, "
            "Database B is backed by PostgreSQL, and "
            "PostgreSQL powers Database B."
        ),
        confidence=0.6,
        oracle="oracle-prober",
        correlation_id=composite_corr_id,
    )

    rows = await attestations(golden_system, composite_corr_id)
    seed_atts = _by_seed(rows, seed, oracle="oracle-prober")
    positive = [r for r in seed_atts if float(r["c_oracle_raw"]) > 0]
    assert len(positive) <= 1, (
        f"Cross-resolution disjointness: at most one positive attestation on the "
        f"seeded hypothesis from this consult, got {len(positive)}: {positive}"
    )


async def test_multi_contradict_writes_consolidated_transfer(
    golden_system: Orchestrator,
) -> None:
    seed_a = await golden_seed_id(golden_system, "agg-scen2-seed-a", oracle="oracle-seeder-a")
    seed_b = await golden_seed_id(golden_system, "agg-scen2-seed-b", oracle="oracle-seeder-b")

    novel_corr_id = "agg-scen2-novel"
    await consult(
        golden_system,
        hypothesis=(
            "Planets in the solar system orbit counter-clockwise as seen from "
            "above the northern celestial hemisphere."
        ),
        confidence=0.85,
        oracle="oracle-prober",
        correlation_id=novel_corr_id,
    )

    rows = await attestations(golden_system, novel_corr_id)

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
    for seed in (seed_a, seed_b):
        seed_atts = _by_seed(rows, seed, oracle="oracle-prober")
        negative = [r for r in seed_atts if float(r["c_oracle_raw"]) < 0]
        assert len(negative) <= 1, (
            f"At most one negative attestation per seed, got {len(negative)} on {seed}"
        )


async def test_ambiguous_composite_emits_consult_notes_log(
    captured_system: tuple[Orchestrator, list[EventDict]],
) -> None:
    system, cap = captured_system

    # The two gRPC seeds ship in the golden archive; only the probe is live.
    await consult(
        system,
        hypothesis=(
            "The HTTP service handles internal calls: these are RPC, "
            "they may run on gRPC, and the transport may or may not be HTTP/2."
        ),
        confidence=0.4,
        oracle="oracle-prober",
        correlation_id="agg-scen3-ambiguous",
    )

    notes_events = [e for e in cap if e.get("event") == "consult.notes"]
    assert notes_events, f"Expected `consult.notes` log event for ambiguous input. Events:\n{cap}"


async def test_claims_at_different_reference_times_do_not_contradict(
    golden_system: Orchestrator,
) -> None:
    # Two explicit `as of <date>` readings are anchored at different reference times.
    # The wage was 7 dollars in 2010 and 15 dollars in 2025: both true, so the newer
    # reading is orthogonal-novel and no disbelief lands on the older. Writing disbelief
    # would assert the 2010 reading was false, which it was not; decay carries its age.
    seed = await golden_seed_id(golden_system, "reftime-diff-seed")

    await consult(
        golden_system,
        hypothesis="As of 2025, the national minimum wage is 15 dollars per hour.",
        confidence=0.8,
        oracle="oracle-prober",
        correlation_id="reftime-diff-probe",
    )

    rows = await attestations(golden_system, "reftime-diff-probe")
    seed_atts = _by_seed(rows, seed, oracle="oracle-prober")
    negative = [r for r in seed_atts if float(r["c_oracle_raw"]) < 0]
    assert not negative, (
        "Claims at different reference times do not contradict: the 2010 and 2025 wage "
        f"readings are both true. Expected no disbelief on the older, got {negative}"
    )


async def test_claims_at_the_same_reference_time_can_contradict(
    golden_system: Orchestrator,
) -> None:
    # Two undated present-tense claims, both anchored now, state incompatible values for
    # one thing. A city has one tallest building; the two candidates cannot both be it at
    # the same reference time, so the newer reading contradicts the older.
    seed = await golden_seed_id(golden_system, "reftime-same-seed")

    await consult(
        golden_system,
        hypothesis="The tallest building in Harborview is the Solstice Tower.",
        confidence=0.8,
        oracle="oracle-prober",
        correlation_id="reftime-same-probe",
    )

    rows = await attestations(golden_system, "reftime-same-probe")
    seed_atts = _by_seed(rows, seed, oracle="oracle-prober")
    negative = [r for r in seed_atts if float(r["c_oracle_raw"]) < 0]
    assert negative, (
        "Claims at the same reference time can contradict: two names for the current "
        "tallest building are mutually exclusive. Expected a disbelief attestation on the "
        "seed, got none."
    )


async def test_paragraph_scale_mixed_references_avoid_false_contradiction(
    golden_system: Orchestrator,
) -> None:
    # Complexity tier: one paragraph-scale consult mixing a dated historical event with a
    # differently-referenced value update. The 1789 event and the 2015 tax reading are
    # both anchored in the past; the prober's 2018 reading is a different reference again.
    # No two of these share a reference time, so no false contradiction lands on either
    # seed. The expensive error is over-contradicting; this case tests restraint.
    event_id = await golden_seed_id(golden_system, "reftime-mixed-event", oracle="oracle-seeder-e")
    value_id = await golden_seed_id(golden_system, "reftime-mixed-value", oracle="oracle-seeder-v")

    await consult(
        golden_system,
        hypothesis=(
            "Building on the framework the Bill of Rights established when it was ratified "
            "in 1789, tax policy has since changed: as of 2018 the federal corporate tax "
            "rate is 21 percent, down from the earlier 35 percent."
        ),
        confidence=0.75,
        oracle="oracle-prober",
        correlation_id="reftime-mixed-probe",
    )

    rows = await attestations(golden_system, "reftime-mixed-probe")

    event_neg = [
        r for r in _by_seed(rows, event_id, oracle="oracle-prober") if float(r["c_oracle_raw"]) < 0
    ]
    assert not event_neg, (
        "The 1789 ratification event shares no reference time with a present tax reading: "
        f"expected no disbelief on the historical event, got {event_neg}"
    )

    value_neg = [
        r for r in _by_seed(rows, value_id, oracle="oracle-prober") if float(r["c_oracle_raw"]) < 0
    ]
    assert not value_neg, (
        "The 2015 and 2018 tax readings are at different reference times, both true: "
        f"expected no disbelief on the 2015 reading, got {value_neg}"
    )


async def test_aged_standing_claim_is_still_contradicted(
    golden_system: Orchestrator,
) -> None:
    # An undated standing claim seeded 90 days back: `last_attested` falls well
    # before `today`, but a single-valued attribute still cannot hold two values,
    # so an incompatible present claim contradicts it. Being untouched makes a
    # claim stale, not immune; this guards against reading age as a wall.
    seed = await golden_seed_id(golden_system, "aged-standing-seed")
    await age_attestations(golden_system, "aged-standing-seed", days=90)

    await consult(
        golden_system,
        hypothesis="The language of instruction at the Valletta maritime academy is Italian.",
        confidence=0.8,
        oracle="oracle-prober",
        correlation_id="aged-standing-probe",
    )

    rows = await attestations(golden_system, "aged-standing-probe")
    seed_atts = _by_seed(rows, seed, oracle="oracle-prober")
    negative = [r for r in seed_atts if float(r["c_oracle_raw"]) < 0]
    assert negative, (
        "An aged standing claim is still contradicted: one academy has one language "
        "of instruction, however long ago the herd last touched the older reading. "
        "Expected a disbelief attestation on the seed, got none."
    )


async def test_aged_dated_snapshot_is_not_contradicted(
    golden_system: Orchestrator,
) -> None:
    # The control for case 7: an old dated snapshot aged the same 90 days. The
    # two readings date themselves to different years, so both are true and the
    # newer one enters as novel; age must not tip the call toward disbelief.
    seed = await golden_seed_id(golden_system, "aged-dated-seed")
    await age_attestations(golden_system, "aged-dated-seed", days=90)

    await consult(
        golden_system,
        hypothesis="As of 2026, Cedarbrook Health employs 2,300 nurses.",
        confidence=0.8,
        oracle="oracle-prober",
        correlation_id="aged-dated-probe",
    )

    rows = await attestations(golden_system, "aged-dated-probe")
    seed_atts = _by_seed(rows, seed, oracle="oracle-prober")
    negative = [r for r in seed_atts if float(r["c_oracle_raw"]) < 0]
    assert not negative, (
        "An aged dated snapshot is not contradicted: the 2023 and 2026 headcounts "
        f"are both true of their own years. Expected no disbelief on the seed, got {negative}"
    )
