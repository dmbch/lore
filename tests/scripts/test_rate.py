"""The rate aggregator: run logs in, per-test outcome counts out."""

import json
from pathlib import Path

import pytest

from scripts.rate import (
    Outcome,
    artifact_layout,
    build_manifest,
    format_table,
    refuse_reused_receipts,
    tally,
)


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
    assert layout.manifest == tmp_path / "manifest.json"
    assert layout.traces == [
        tmp_path / "trace-run1.jsonl",
        tmp_path / "trace-run2.jsonl",
        tmp_path / "trace-run3.jsonl",
    ]


def _tree(tmp_path: Path) -> Path:
    """A repo-shaped tree holding one of each fingerprinted input."""
    prompts = tmp_path / "src/lore/prompts"
    prompts.mkdir(parents=True)
    (prompts / "archivist.md").write_text("the register", encoding="utf-8")
    vendors = tmp_path / "src/lore/config/vendors"
    vendors.mkdir(parents=True)
    (vendors / "acme.toml").write_text(
        '[fast]\nmodel = "acme/quick"\n\n[reasoning]\nmodel = "acme/deep"\n', encoding="utf-8"
    )
    return tmp_path


def test_manifest_fingerprints_the_inputs_that_drift(tmp_path: Path) -> None:
    manifest = build_manifest(_tree(tmp_path), runs=5, selection=["tests/e2e/test_shapes.py"])

    assert set(manifest.fingerprints) == {
        "src/lore/prompts/archivist.md",
        "src/lore/config/vendors/acme.toml",
    }
    assert manifest.runs == 5
    assert manifest.selection == ["tests/e2e/test_shapes.py"]


def test_manifest_fingerprint_moves_when_a_prompt_changes(tmp_path: Path) -> None:
    """The whole point: two receipts are comparable only if their inputs match."""
    root = _tree(tmp_path)
    before = build_manifest(root, runs=5, selection=[]).fingerprints

    (root / "src/lore/prompts/archivist.md").write_text("the register, reworded", encoding="utf-8")
    after = build_manifest(root, runs=5, selection=[]).fingerprints

    assert before["src/lore/prompts/archivist.md"] != after["src/lore/prompts/archivist.md"]
    assert before["src/lore/config/vendors/acme.toml"] == after["src/lore/config/vendors/acme.toml"]


def test_manifest_names_the_model_pin_per_vendor_role(tmp_path: Path) -> None:
    manifest = build_manifest(_tree(tmp_path), runs=5, selection=[])

    assert manifest.models == {"acme.fast": "acme/quick", "acme.reasoning": "acme/deep"}


def test_manifest_outside_a_checkout_records_no_head(tmp_path: Path) -> None:
    """A receipt from an exported tree is still a receipt; it just cannot cite a commit."""
    manifest = build_manifest(_tree(tmp_path), runs=5, selection=[])

    assert manifest.git_head is None


def test_reused_artifacts_dir_is_refused(tmp_path: Path) -> None:
    """Receipts never concatenate: a second run into the same dir would pool
    incomparable measurements under a manifest describing only the newest."""
    rate_log = tmp_path / "rate.jsonl"
    rate_log.write_text("{}\n", encoding="utf-8")

    with pytest.raises(SystemExit):
        refuse_reused_receipts(rate_log)


def test_fresh_artifacts_dir_is_accepted(tmp_path: Path) -> None:
    refuse_reused_receipts(tmp_path / "rate.jsonl")
