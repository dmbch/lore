"""Tests for the ``/health`` and ``/ready`` HTTP routes.

The two routes serve different probe semantics:

- ``/health`` is the liveness probe — answers "the process is responsive."
  Always 200; never touches the database.
- ``/ready`` is the readiness probe — answers "the database is reachable
  right now." 200 on success; 503 when the injected ``health_probe``
  raises ``StorageError`` (or anything else). The 503 body never echoes
  the underlying error — operator diagnostics live in structlog, not in
  the wire payload.

The probe is injected into ``create_server`` rather than imported by the
adapter; this keeps the adapter layer free of repository imports. When
``health_probe`` is omitted, ``/ready`` returns 200 unconditionally —
the right shape for stdio mode and tests that don't exercise readiness.
"""

import os
from collections.abc import AsyncGenerator, Awaitable, Callable, Generator
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastmcp import FastMCP
from starlette.testclient import TestClient

from lore.adapter.mcp import create_server
from lore.config import LoreSettings, load_settings
from lore.domain.errors import StorageError
from lore.orchestrator import Orchestrator

_COMPLETE_TOML = Path(__file__).parents[1] / "fixtures" / "lore_complete.toml"


@asynccontextmanager
async def _noop_system() -> AsyncGenerator[Orchestrator]:
    yield MagicMock(spec=Orchestrator)


@pytest.fixture()
def bootstrap_env() -> Generator[None]:
    env = {"DATABASE_URL": "sqlite:///:memory:"}
    with patch.dict(os.environ, env, clear=True):
        yield


@pytest.fixture()
def settings(bootstrap_env: None) -> LoreSettings:
    return load_settings(toml_path=_COMPLETE_TOML)


async def _ok_probe() -> None:
    """A health_probe that always succeeds."""


async def _failing_probe() -> None:
    """A health_probe that simulates an unreachable database."""
    raise StorageError("connection refused at 10.0.0.1:5432")


async def _crashing_probe() -> None:
    """A health_probe that raises something other than StorageError.

    Stands in for a bug in the probe closure, a vendor SDK leak, or any
    other unexpected failure — the catch-all branch must absorb it.
    """
    raise RuntimeError("unexpected probe bug: division by zero in pool.session")


def _server(
    settings: LoreSettings, *, health_probe: Callable[[], Awaitable[None]] | None = None
) -> FastMCP[Orchestrator]:
    return create_server(settings=settings, system=_noop_system(), health_probe=health_probe)


def _client(server: FastMCP[Orchestrator]) -> TestClient:
    return TestClient(server.http_app())


def test_health_returns_ok_unconditionally(settings: LoreSettings) -> None:
    """`/health` is a no-op liveness probe; never touches the probe callable."""
    # Even with a probe that would raise, /health stays 200 — confirming
    # the route does not call the probe.
    client = _client(_server(settings, health_probe=_failing_probe))
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ready_returns_ok_when_probe_succeeds(settings: LoreSettings) -> None:
    """`/ready` returns 200 when the injected probe completes without raising."""
    client = _client(_server(settings, health_probe=_ok_probe))
    response = client.get("/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ready_returns_ok_when_no_probe_configured(settings: LoreSettings) -> None:
    """Without a probe, ``/ready`` returns 200 — the stdio / test shape.

    Pins the contract that omitting ``health_probe`` is a valid composition
    (stdio mode has no HTTP transport; tests that don't exercise readiness
    shouldn't have to wire a stub probe).
    """
    client = _client(_server(settings))
    response = client.get("/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ready_returns_503_when_probe_raises_storage_error(settings: LoreSettings) -> None:
    """`/ready` translates `StorageError` to a 503 — load balancers pull the pod."""
    client = _client(_server(settings, health_probe=_failing_probe))
    response = client.get("/ready")
    assert response.status_code == 503
    # Full-body equality: the contract is exactly this payload, not "contains some token".
    assert response.json() == {"status": "unavailable"}


def test_ready_returns_503_when_probe_raises_unexpected_exception(
    settings: LoreSettings,
) -> None:
    """Non-StorageError exceptions also collapse to the scrubbed 503.

    The wire posture must stay uniform whether the failure was anticipated
    (StorageError → "unavailable") or surprising (RuntimeError from a bug
    in the probe closure). The full traceback lives in structlog under
    ``ready.error.internal``.
    """
    client = _client(_server(settings, health_probe=_crashing_probe))
    response = client.get("/ready")
    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}


def test_ready_503_does_not_leak_storage_error_message(settings: LoreSettings) -> None:
    """The 503 body never echoes the underlying error message back to the client.

    DSN host:port, constraint names, and other operator-only diagnostics
    must stay out of the wire payload — same scrub posture as the consult
    tool. Operators read them from structlog. Full-body equality enforces
    the contract more tightly than substring assertions on a sampled message.
    """
    client = _client(_server(settings, health_probe=_failing_probe))
    response = client.get("/ready")
    assert response.json() == {"status": "unavailable"}
