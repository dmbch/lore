"""The rate aggregator: run logs in, per-test outcome counts out."""

import json
from pathlib import Path

from scripts.rate import Outcome, artifact_layout, format_table, tally


def _log(tmp_path: Path, *, lines: list[dict[str, str]]) -> Path:
    log = tmp_path / "rate.jsonl"
    log.write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")
    return log


def test_tally_counts_outcomes_per_test(tmp_path: Path) -> None:
    log = _log(
        tmp_path,
        lines=[
            {"id": "steady", "outcome": "passed"},
            {"id": "flaky", "outcome": "passed"},
            {"id": "steady", "outcome": "passed"},
            {"id": "flaky", "outcome": "failed"},
            {"id": "flaky", "outcome": "skipped"},
        ],
    )

    assert set(tally(log)) == {
        Outcome(test="steady", passed=2, failed=0, skipped=0),
        Outcome(test="flaky", passed=1, failed=1, skipped=1),
    }


def test_format_table_puts_the_worst_rate_first() -> None:
    table = format_table(
        [
            Outcome(test="steady", passed=10, failed=0, skipped=0),
            Outcome(test="flaky", passed=3, failed=7, skipped=0),
        ]
    )

    rows = table.splitlines()
    assert "flaky" in rows[0]
    assert "steady" in rows[1]


def test_format_table_renders_a_zero_attempt_test() -> None:
    # The exact log a keyless run produces: every test skipped at setup, and
    # the naive rate key would divide by zero.
    table = format_table([Outcome(test="keyless", passed=0, failed=0, skipped=2)])

    assert "0/0" in table
    assert "skipped 2" in table


def test_artifact_layout_names_rate_log_and_per_run_traces(tmp_path: Path) -> None:
    layout = artifact_layout(tmp_path, runs=3)

    assert layout.rate_log == tmp_path / "rate.jsonl"
    assert layout.traces == [
        tmp_path / "trace-run1.jsonl",
        tmp_path / "trace-run2.jsonl",
        tmp_path / "trace-run3.jsonl",
    ]
