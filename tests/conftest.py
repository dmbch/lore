"""Shared test fixtures.

Tests that reconfigure ``configure_telemetry()`` opt into
``reset_telemetry`` to reach into private globals and unwind them.
"""

import os

# fastmcp's settings singleton snapshots env at ``import fastmcp``, so the
# off-switch must land before any import that could pull it. pytest loads this
# conftest before test modules, and ``uv run pytest`` runs without the mise
# env, so this is the suite's process-wide guarantee. All imports sit below
# this line by design (see the E402 per-file ignore).
os.environ.setdefault("FASTMCP_LOG_ENABLED", "false")

import logging
from collections.abc import Iterator

import litellm
import pytest
import structlog

import lore.telemetry as _telemetry


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
