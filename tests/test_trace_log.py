"""The trace sink: structlog debug events as JSON lines, contextvars merged."""

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
import structlog

from tests.conftest import configure_trace_sink

# Every test reconfigures structlog through the sink helper; bind the
# conftest-provided opt-in reset as module-wide autouse. Mandatory here: the
# helper never sets _telemetry._configured, so the enforce_telemetry_reset
# tripwire cannot catch a leak.
pytestmark = pytest.mark.usefixtures("reset_telemetry")


@pytest.fixture
def trace_log(tmp_path: Path) -> Iterator[Path]:
    log = tmp_path / "trace.jsonl"
    sink = configure_trace_sink(log)
    yield log
    sink.close()


def test_trace_sink_writes_debug_events_with_bound_context(trace_log: Path) -> None:
    with structlog.contextvars.bound_contextvars(correlation_id="c-1"):
        structlog.get_logger().debug("consult.interpret.result")

    (line,) = trace_log.read_text().splitlines()
    entry = json.loads(line)
    assert entry["event"] == "consult.interpret.result"
    assert entry["correlation_id"] == "c-1"


def test_trace_sink_appends_two_events_as_two_lines(trace_log: Path) -> None:
    structlog.get_logger().debug("first")
    structlog.get_logger().debug("second")

    lines = trace_log.read_text().splitlines()
    assert [json.loads(line)["event"] for line in lines] == ["first", "second"]
