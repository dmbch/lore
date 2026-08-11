"""Shared test fixtures.

Tests that reconfigure ``configure_telemetry()`` opt into
``reset_telemetry`` to reach into private globals and unwind them.
"""

import os

# fastmcp's settings singleton snapshots env at ``import fastmcp``, so the
# off-switch must land before any import that could pull it. pytest loads this
# conftest before test modules, so this is the suite's process-wide guarantee.
# A hard assign, not setdefault: the suite pins the propagate-to-structlog
# posture, and an ambient FASTMCP_LOG_ENABLED=true from a dev shell must not
# flip it. All imports sit below this line by design (see the E402 per-file
# ignore).
os.environ["FASTMCP_LOG_ENABLED"] = "false"

import json
import logging
from collections.abc import Iterator
from pathlib import Path

import litellm
import pytest
import structlog

import lore.telemetry as _telemetry


def _rate_line(report: pytest.TestReport) -> str | None:
    # Under xdist the controller replays every worker report through this hook
    # and stamps `report.node` on the way (dsession.py); the worker already
    # logged it, so rendering the replay would double every count.
    if getattr(report, "node", None) is not None:
        return None
    # The call phase decides the outcome; setup and teardown matter only when
    # they fail or skip (require_gemini skips at setup). Rendering passing
    # non-call phases would triple-count every test.
    if report.when != "call" and report.outcome == "passed":
        return None
    return json.dumps({"id": report.nodeid, "outcome": report.outcome})


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    """Append the outcome to the run log scripts/rate.py aggregates.

    Inert unless LORE_RATE_LOG names the log file. Append mode by design:
    all k runs of a rate measurement share one file.
    """
    log = os.environ.get("LORE_RATE_LOG")
    if log is None:
        return
    line = _rate_line(report)
    if line is None:
        return
    with Path(log).open("a", encoding="utf-8") as sink:
        sink.write(line + "\n")


def configure_trace_sink(log: Path) -> None:
    """Point structlog at a JSONL trace file, debug level, contextvars merged.

    Test-lane only; production telemetry (``configure_telemetry``) is
    untouched. Tests calling this must opt into ``reset_telemetry``: the
    helper never sets ``_telemetry._configured``, so the autouse tripwire
    cannot catch the structlog leak.
    """
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.DEBUG),
        logger_factory=structlog.WriteLoggerFactory(file=log.open("a", encoding="utf-8")),
    )


def _reset_telemetry_state() -> None:
    # Note: the LiteLLM trio ``configure_telemetry`` reroutes (LiteLLM,
    # LiteLLM Proxy, LiteLLM Router) is not restored. Those loggers hold the
    # bare-stdlib state the reroute leaves them in. No current test depends on
    # the pre-reroute state across the boundary; a future test that does will
    # need to capture and restore those loggers itself.
    _telemetry._configured = False  # pyright: ignore[reportPrivateUsage]
    structlog.reset_defaults()
    structlog.contextvars.clear_contextvars()
    litellm.callbacks = []
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(logging.WARNING)


@pytest.fixture
def reset_telemetry() -> Iterator[None]:
    """Opt-in reset for tests that call ``configure_telemetry()`` themselves."""
    _reset_telemetry_state()
    yield
    _reset_telemetry_state()


@pytest.fixture(autouse=True)
def enforce_telemetry_reset() -> Iterator[None]:
    """Catch tests that configure telemetry without opting into ``reset_telemetry``.

    Autouse fixtures tear down after explicit ones, so this observes state
    after ``reset_telemetry`` (if requested) has cleared it. A live
    ``_configured`` here means a test mutated globals without opting in.
    Fail loudly rather than letting the next test inherit the leak.
    """
    yield
    if _telemetry._configured:  # pyright: ignore[reportPrivateUsage]
        _reset_telemetry_state()
        msg = "test mutated lore.telemetry._configured without using the reset_telemetry fixture"
        raise AssertionError(msg)
