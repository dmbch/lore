"""The recall-protocol comparison core: two receipt sets in, rank movements out."""

from pathlib import Path

import pytest

from scripts.recall import ExpectedEntry, ReceiptRow
from scripts.recall_protocol import (
    EntryMovement,
    LanePair,
    candidate_dirs,
    compare_receipts,
    format_movements,
    format_noise,
    noise_floor,
    protocol_layout,
    regressed,
)


def _entry(
    correlation_id: str,
    *,
    hypothesis_id: str = "hyp-1",
    composite: int | None = None,
    proximity: int | None = None,
    authority: int | None = None,
) -> ExpectedEntry:
    return ExpectedEntry(
        correlation_id=correlation_id,
        hypothesis_id=hypothesis_id,
        composite=composite,
        proximity=proximity,
        authority=authority,
    )


def _row(query_id: str, *, expected: tuple[ExpectedEntry, ...]) -> ReceiptRow:
    return ReceiptRow(
        query_id=query_id, keywords=(), propositions=(), expected=expected, composite_results=()
    )


def test_regressed_flags_lost_and_slid_ranks() -> None:
    assert regressed(LanePair(old=1, new=None))
    assert regressed(LanePair(old=1, new=2))
    assert not regressed(LanePair(old=None, new=3))
    assert not regressed(LanePair(old=2, new=2))
    assert not regressed(LanePair(old=3, new=1))
    assert not regressed(LanePair(old=None, new=None))


def test_compare_pairs_lane_ranks_by_query_and_hypothesis() -> None:
    old = [_row("q1", expected=(_entry("corr-a", composite=1, proximity=2, authority=3),))]
    candidate = [_row("q1", expected=(_entry("corr-a", composite=2, proximity=2, authority=None),))]

    movements = compare_receipts(old=old, candidate=candidate)

    assert movements == [
        EntryMovement(
            query_id="q1",
            correlation_id="corr-a",
            hypothesis_id="hyp-1",
            composite=LanePair(old=1, new=2),
            proximity=LanePair(old=2, new=2),
            authority=LanePair(old=3, new=None),
        )
    ]


def test_compare_rejects_mismatched_receipt_sets() -> None:
    # A partial join would read entries only one run saw as measurements.
    old = [_row("q1", expected=(_entry("corr-a", composite=1),))]
    candidate = [_row("q2", expected=(_entry("corr-a", composite=1),))]

    with pytest.raises(ValueError, match="disagree"):
        compare_receipts(old=old, candidate=candidate)


def test_compare_rejects_a_receipt_that_mixes_runs() -> None:
    # A rerun that concatenated into an old recall.jsonl must fail loud, not
    # join last-wins into a clean-looking measurement.
    row = _row("q1", expected=(_entry("corr-a", composite=1),))
    clean = [_row("q1", expected=(_entry("corr-a", composite=2),))]

    with pytest.raises(ValueError, match="mixes runs"):
        compare_receipts(old=[row, row], candidate=clean)


def test_noise_floor_keeps_only_cells_that_move_between_repeats() -> None:
    steady = [_row("q1", expected=(_entry("corr-a", composite=1, proximity=2, authority=3),))]
    moved = [_row("q1", expected=(_entry("corr-a", composite=2, proximity=2, authority=3),))]

    assert noise_floor([]) == []
    assert noise_floor([steady]) == []
    assert noise_floor([steady, steady]) == []
    noise = noise_floor([steady, steady, moved])
    assert len(noise) == 1
    assert noise[0].composite == LanePair(old=1, new=2)
    assert noise[0].proximity == LanePair(old=2, new=2)


def test_format_noise_counts_unstable_cells_and_flags_the_floor() -> None:
    quiet = format_noise([], runs=2)
    loud = format_noise(
        [
            EntryMovement(
                query_id="q1",
                correlation_id="corr-a",
                hypothesis_id="hyp-1",
                composite=LanePair(old=1, new=2),
                proximity=LanePair(old=2, new=2),
                authority=LanePair(old=3, new=None),
            )
        ],
        runs=3,
    )

    assert quiet == "noise: 0 unstable cells across 2 candidate runs"
    assert "noise: 2 unstable cells across 3 candidate runs" in loud
    assert "not evidence" in loud
    assert "c 1>2" in loud
    assert "a 3>-" in loud


def test_format_movements_sorts_regressions_first() -> None:
    improved = EntryMovement(
        query_id="a-improved",
        correlation_id="corr-i",
        hypothesis_id="hyp-i",
        composite=LanePair(old=3, new=1),
        proximity=LanePair(old=1, new=1),
        authority=LanePair(old=None, new=2),
    )
    slipped = EntryMovement(
        query_id="z-regressed",
        correlation_id="corr-r",
        hypothesis_id="hyp-r",
        composite=LanePair(old=1, new=1),
        proximity=LanePair(old=1, new=1),
        authority=LanePair(old=2, new=None),
    )

    table = format_movements([improved, slipped])

    rows = table.splitlines()
    assert "z-regressed" in rows[0]
    assert "a 2>-" in rows[0]
    assert "regressed" in rows[0]
    assert "a-improved" in rows[1]
    assert rows[-1] == "1/2 entries regressed"


def test_protocol_layout_names_the_run_dirs(tmp_path: Path) -> None:
    layout = protocol_layout(tmp_path)

    assert layout.candidate_dir == tmp_path / "candidate"
    assert layout.old_dir == tmp_path / "old"
    assert layout.old_prompt == tmp_path / "interpreter-old.md"


def test_candidate_dirs_number_the_repeat_runs(tmp_path: Path) -> None:
    layout = protocol_layout(tmp_path)

    assert candidate_dirs(layout, k=1) == [tmp_path / "candidate"]
    assert candidate_dirs(layout, k=3) == [
        tmp_path / "candidate",
        tmp_path / "candidate-2",
        tmp_path / "candidate-3",
    ]
