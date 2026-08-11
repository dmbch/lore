"""The rate-log hook: one json line per decided test outcome.

Known accepted limit: a test passing at call but failing at teardown records
two lines and inflates its denominator by one; rare, accepted for a rate
instrument.
"""

import json
from pathlib import Path
from typing import Literal

import pytest

from tests.conftest import (
    _rate_line,  # pyright: ignore[reportPrivateUsage]
    pytest_runtest_logreport,
)

NODEID = "tests/test_sample.py::test_sample"


def _report(
    *,
    when: Literal["setup", "call", "teardown"],
    outcome: Literal["passed", "failed", "skipped"],
    node: str | None = None,
) -> pytest.TestReport:
    # TestReport's **extra kwargs land as attributes, the same way xdist's
    # controller stamps `report.node` before replaying a worker's report.
    return pytest.TestReport(
        nodeid=NODEID,
        location=("tests/test_sample.py", 0, "test_sample"),
        keywords={},
        outcome=outcome,
        longrepr=None,
        when=when,
        node=node,
    )


def test_rate_line_renders_a_call_phase_outcome() -> None:
    line = _rate_line(_report(when="call", outcome="passed"))

    assert line is not None
    assert json.loads(line) == {"id": NODEID, "outcome": "passed"}


def test_rate_line_renders_a_setup_phase_skip() -> None:
    # require_gemini skips at setup; a dropped skip makes a keyless run look
    # like no run at all.
    line = _rate_line(_report(when="setup", outcome="skipped"))

    assert line is not None
    assert json.loads(line) == {"id": NODEID, "outcome": "skipped"}


def test_rate_line_ignores_a_passing_setup_phase() -> None:
    # Else every test records twice and every rate doubles its denominator.
    assert _rate_line(_report(when="setup", outcome="passed")) is None


def test_rate_line_ignores_an_xdist_controller_replay() -> None:
    # Under -n auto the worker fires this hook and the controller replays the
    # same report through it; logging both doubles every count in the table
    # while leaving the percentages plausible.
    assert _rate_line(_report(when="call", outcome="passed", node="gw0")) is None


def test_rate_log_appends_only_when_the_env_var_is_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The body ends with the var deleted, so the real hook skips this test's
    # own call report; a failure between setenv and delenv would log it to
    # the discarded tmp file instead. No line escapes either way.
    log = tmp_path / "rate.jsonl"
    report = _report(when="call", outcome="passed")

    monkeypatch.setenv("LORE_RATE_LOG", str(log))
    pytest_runtest_logreport(report)
    assert log.read_text().splitlines() == [_rate_line(report)]

    monkeypatch.delenv("LORE_RATE_LOG", raising=False)
    pytest_runtest_logreport(report)
    assert log.read_text().splitlines() == [_rate_line(report)]
