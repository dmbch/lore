"""The recall scoring core: per-expected ranks in, aggregates and a table out.

The live driver's pure settings helpers are tested here too; the paid path
itself stays untested, like rate.py's main.
"""

import os
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from lore.config import LoreSettings, load_settings
from scripts.recall import (
    ExpectedEntry,
    QueryScore,
    ReceiptRow,
    artifact_layout,
    collapse_outcomes,
    format_table,
    interlopers,
    lane_variants,
    mean_reciprocal_rank,
    measured_depth,
    rank_of,
    recall_at_depth,
    resolve_expected,
    total_interlopers,
    with_interpreter_prompt,
)
from tests.e2e.queries import LabeledQuery

_COMPLETE_TOML = Path(__file__).parent.parent / "fixtures" / "lore_complete.toml"


def _settings() -> LoreSettings:
    # Keyless: no vendor detection, models come from the complete fixture.
    with patch.dict(os.environ, {"DATABASE_URL": "sqlite:///test.db"}, clear=True):
        return load_settings(toml_path=_COMPLETE_TOML)


def _outcome(
    hypothesis_id: str,
    *,
    composite: int | None = None,
    proximity: int | None = None,
    authority: int | None = None,
) -> ExpectedEntry:
    return ExpectedEntry(
        correlation_id=f"corr-{hypothesis_id}",
        hypothesis_id=hypothesis_id,
        composite=composite,
        proximity=proximity,
        authority=authority,
    )


def test_rank_of_is_one_based_position_or_none() -> None:
    results = ("hyp-a", "hyp-b", "hyp-c")

    assert rank_of("hyp-a", results=results) == 1
    assert rank_of("hyp-c", results=results) == 3
    assert rank_of("hyp-x", results=results) is None


def test_recall_counts_expected_hypotheses_that_reached_the_pool() -> None:
    scores = [
        QueryScore(
            query_id="both-found",
            outcomes=(_outcome("hyp-a", composite=1), _outcome("hyp-b", composite=4)),
        ),
        QueryScore(
            query_id="one-missed",
            outcomes=(_outcome("hyp-c", composite=2), _outcome("hyp-d")),
        ),
    ]

    assert recall_at_depth(scores, depth=10) == 0.75


def test_mrr_uses_the_best_ranked_expected_per_query() -> None:
    # Textbook MRR (Voorhees 1999): only the first relevant result counts,
    # so the miss beside a rank-2 hit changes nothing within the query.
    scores = [
        QueryScore(
            query_id="hit-and-miss",
            outcomes=(_outcome("hyp-a", composite=4), _outcome("hyp-b", composite=2)),
        ),
    ]

    assert mean_reciprocal_rank(scores) == 0.5


def test_mrr_treats_a_query_with_nothing_found_as_zero() -> None:
    scores = [
        QueryScore(query_id="found-first", outcomes=(_outcome("hyp-a", composite=1),)),
        QueryScore(query_id="all-missed", outcomes=(_outcome("hyp-b"),)),
    ]

    assert mean_reciprocal_rank(scores) == 0.5


def test_aggregates_over_no_outcomes_read_as_zero() -> None:
    # An eval that scored nothing must read as found-nothing, not as perfect.
    no_scores: list[QueryScore] = []
    no_outcomes = [QueryScore(query_id="scored-nothing", outcomes=())]

    assert recall_at_depth(no_scores, depth=10) == 0.0
    assert mean_reciprocal_rank(no_scores) == 0.0
    assert recall_at_depth(no_outcomes, depth=10) == 0.0
    assert mean_reciprocal_rank(no_outcomes) == 0.0


def test_measured_depth_squeezes_the_pool_until_the_archive_can_overflow_it() -> None:
    # At or below the limit the archive fills every pool, so recall there is
    # 1.000 by construction; the eval measures at a depth something can fall
    # out of instead. Past the limit the configured pool is the real one.
    assert measured_depth(archive_size=10, limit=10) == 3
    assert measured_depth(archive_size=2, limit=10) == 1
    assert measured_depth(archive_size=40, limit=10) == 10


def test_recall_at_depth_counts_only_hypotheses_inside_the_pool() -> None:
    scores = [
        QueryScore(
            query_id="one-deep-one-shallow",
            outcomes=(_outcome("hyp-a", composite=1), _outcome("hyp-b", composite=7)),
        ),
        QueryScore(query_id="missed", outcomes=(_outcome("hyp-c"),)),
    ]

    assert recall_at_depth(scores, depth=10) == pytest.approx(2 / 3)
    assert recall_at_depth(scores, depth=3) == pytest.approx(1 / 3)
    assert recall_at_depth([], depth=3) == 0.0


def test_interlopers_count_non_expected_hypotheses_outranking_the_expected() -> None:
    # The live metric on a census archive: retrieval degrades by crowding
    # before it drops anything from the pool.
    clean = QueryScore(
        query_id="clean",
        outcomes=(_outcome("hyp-a", composite=1), _outcome("hyp-b", composite=2)),
    )
    crowded = QueryScore(
        query_id="crowded",
        outcomes=(_outcome("hyp-a", composite=1), _outcome("hyp-b", composite=5)),
    )
    missed = QueryScore(query_id="missed", outcomes=(_outcome("hyp-c"),))

    assert interlopers(clean) == 0
    assert interlopers(crowded) == 3
    assert interlopers(missed) is None
    assert total_interlopers([clean, crowded, missed]) == 3


def test_format_table_sorts_misses_first_then_the_most_crowded() -> None:
    table = format_table(
        [
            QueryScore(query_id="clean", outcomes=(_outcome("hyp-a", composite=1),)),
            QueryScore(query_id="crowded", outcomes=(_outcome("hyp-b", composite=4),)),
            QueryScore(query_id="missed", outcomes=(_outcome("hyp-c"),)),
        ]
    )

    rows = table.splitlines()
    assert "missed" in rows[0]
    assert rows[0].startswith("miss")
    assert "crowded" in rows[1]
    assert rows[1].startswith("+3")
    assert "clean" in rows[2]
    assert rows[2].startswith("+0")


def test_collapsed_correlation_ids_count_their_hypothesis_once() -> None:
    # Paraphrase collapse: the golden rebuild may store two seeds as one
    # hypothesis; the recall denominator must count it once, under a label
    # that names both seeds.
    query = LabeledQuery(
        id="collapse",
        question="q",
        hypothesis=None,
        context=None,
        expected=("corr-a", "corr-b", "corr-c"),
    )
    resolved = {"corr-a": ("hyp-1",), "corr-b": ("hyp-1",), "corr-c": ("hyp-2",)}

    outcomes = collapse_outcomes(
        query=query,
        resolved=resolved,
        composite=("hyp-1", "hyp-2"),
        proximity=("hyp-2",),
        authority=("hyp-1",),
    )

    assert len(outcomes) == 2
    collapsed, single = outcomes
    assert collapsed.correlation_id == "corr-a+corr-b"
    assert collapsed.hypothesis_id == "hyp-1"
    assert (collapsed.composite, collapsed.proximity, collapsed.authority) == (1, None, 1)
    assert single.correlation_id == "corr-c"
    assert (single.composite, single.proximity, single.authority) == (2, 1, None)


def test_a_split_seed_scores_by_its_best_ranked_hypothesis() -> None:
    # Decomposition split: a rebuild may store one seed as several atoms.
    # The seed counts once in the denominator and is found if any member is,
    # mirroring MRR's best-rank semantics.
    query = LabeledQuery(
        id="split",
        question="q",
        hypothesis=None,
        context=None,
        expected=("corr-a",),
    )
    resolved = {"corr-a": ("hyp-1", "hyp-2")}

    outcomes = collapse_outcomes(
        query=query,
        resolved=resolved,
        composite=("hyp-2", "hyp-1"),
        proximity=("hyp-1",),
        authority=(),
    )

    assert len(outcomes) == 1
    (outcome,) = outcomes
    assert outcome.correlation_id == "corr-a"
    assert outcome.hypothesis_id == "hyp-1+hyp-2"
    assert (outcome.composite, outcome.proximity, outcome.authority) == (1, 1, None)


def _attestation_db(tmp_path: Path, *, rows: list[tuple[str, str, str]]) -> Path:
    db_path = tmp_path / "golden.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "CREATE TABLE attestations (hypothesis_id TEXT, correlation_id TEXT, oracle_id TEXT)"
        )
        conn.executemany("INSERT INTO attestations VALUES (?, ?, ?)", rows)
        conn.commit()
    finally:
        conn.close()
    return db_path


def test_resolve_expected_maps_each_correlation_id_to_its_hypothesis(tmp_path: Path) -> None:
    # The _transfer row shares the seed's correlation ID; the oracle filter
    # must keep it out of resolution.
    db_path = _attestation_db(
        tmp_path,
        rows=[
            ("hyp-1", "corr-a", "oracle-a"),
            ("hyp-9", "corr-a", "_transfer"),
            ("hyp-2", "corr-b", "oracle-b"),
        ],
    )

    resolved = resolve_expected(
        db_path=db_path,
        oracles={"corr-a": "oracle-a", "corr-b": "oracle-b"},
        labeled=["corr-a", "corr-b"],
    )

    assert resolved == {"corr-a": ("hyp-1",), "corr-b": ("hyp-2",)}


def test_resolve_expected_returns_every_hypothesis_of_a_split_seed(tmp_path: Path) -> None:
    # Decomposition split: the golden rebuild may store one seed as several
    # atoms; the label binds to all of them.
    db_path = _attestation_db(
        tmp_path,
        rows=[("hyp-2", "corr-split", "oracle-a"), ("hyp-1", "corr-split", "oracle-a")],
    )

    resolved = resolve_expected(
        db_path=db_path, oracles={"corr-split": "oracle-a"}, labeled=["corr-split"]
    )

    assert resolved == {"corr-split": ("hyp-1", "hyp-2")}


def test_resolve_expected_rejects_a_label_with_no_hypothesis(tmp_path: Path) -> None:
    db_path = _attestation_db(tmp_path, rows=[("hyp-1", "corr-a", "oracle-a")])

    with pytest.raises(ValueError, match="resolves to no hypothesis"):
        resolve_expected(db_path=db_path, oracles={"corr-gone": "oracle-a"}, labeled=["corr-gone"])


def test_artifact_layout_names_the_recall_log(tmp_path: Path) -> None:
    layout = artifact_layout(tmp_path)

    assert layout.recall_log == tmp_path / "recall.jsonl"


def test_receipt_row_round_trips_through_json() -> None:
    # The receipt is the contract between the recall run and the protocol
    # driver's comparison; both directions must agree on it.
    row = ReceiptRow(
        query_id="abbrev-cap-composite",
        keywords=("HTTP", "Hypertext Transfer Protocol"),
        propositions=("The HTTP service's internal RPC traffic runs on gRPC.",),
        expected=(
            ExpectedEntry(
                correlation_id="corr-a+corr-b",
                hypothesis_id="hyp-1",
                composite=1,
                proximity=None,
                authority=2,
            ),
        ),
        composite_results=("hyp-1", "hyp-2"),
    )

    assert ReceiptRow.model_validate_json(row.model_dump_json()) == row


def test_lane_settings_isolate_one_lane_and_still_sum_to_one() -> None:
    settings = _settings()

    variants = lane_variants(settings)

    assert variants.proximity_only.retrieval.weights == (1.0, 0.0)
    assert variants.authority_only.retrieval.weights == (0.0, 1.0)
    for variant in (variants.proximity_only, variants.authority_only):
        # The weights are pinned exactly above; only the rest is open here.
        # Only the weights move; the rest of retrieval and the settings stay put.
        assert variant.retrieval.limit == settings.retrieval.limit
        assert variant.retrieval.fan_out == settings.retrieval.fan_out
        assert variant.retrieval.max_keywords == settings.retrieval.max_keywords
        assert variant.dsn == settings.dsn
        assert variant.prompts == settings.prompts


def test_prompt_override_replaces_only_the_interpreter_path(tmp_path: Path) -> None:
    settings = _settings()
    candidate = tmp_path / "interpreter-candidate.md"

    overridden = with_interpreter_prompt(settings, prompt=candidate)

    assert overridden.prompts.interpreter == candidate
    assert overridden.prompts.scribe == settings.prompts.scribe
    assert overridden.prompts.archivist == settings.prompts.archivist
    assert overridden.prompts.contract == settings.prompts.contract
    assert overridden.retrieval == settings.retrieval
    assert overridden.dsn == settings.dsn
