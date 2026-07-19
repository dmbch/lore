"""Tests for lore.server: the fastmcp-run composition root."""

import os
from pathlib import Path
from unittest.mock import patch

import pytest
from fastmcp import Client, FastMCP
from starlette.testclient import TestClient

from lore.config import LoreSettings, load_settings
from lore.domain import StorageError
from lore.repositories import RepositoryPool

_COMPLETE_TOML = Path(__file__).parent / "fixtures" / "lore_complete.toml"

# Apply the opt-in ``reset_telemetry`` fixture (tests/conftest.py) to every
# test in this module: the factory tests call ``server()``, whose
# ``configure_telemetry()`` once-only guard would otherwise trip on the
# second test in the run.
pytestmark = pytest.mark.usefixtures("reset_telemetry")


def _settings_for(db_path: Path) -> LoreSettings:
    env = {"DATABASE_URL": f"sqlite:///{db_path}"}
    with patch.dict(os.environ, env, clear=True):
        return load_settings(toml_path=_COMPLETE_TOML)


async def test_system_yields_wired_orchestrator(tmp_path: Path) -> None:
    from lore.orchestrator import Orchestrator
    from lore.server import system

    settings = _settings_for(tmp_path / "test.db")
    async with system(settings) as orchestrator:
        assert isinstance(orchestrator, Orchestrator)


async def test_ready_check_passes_inside_scope_and_raises_after(tmp_path: Path) -> None:
    """``_check_ready`` passes while the pool is live and raises after exit,
    so ``/ready`` answers 200 only inside the lifespan scope.
    """
    from lore.repositories import PoolCell
    from lore.server import _check_ready, system  # pyright: ignore[reportPrivateUsage]

    settings = _settings_for(tmp_path / "test.db")
    pool_cell = PoolCell()
    async with system(settings, pool_cell=pool_cell):
        await _check_ready(pool_cell)

    with pytest.raises(StorageError):
        await _check_ready(pool_cell)


async def test_ready_check_before_start_raises_storage_error() -> None:
    """A fresh cell answers not-ready: the /ready-503 shape before startup."""
    from lore.repositories import PoolCell
    from lore.server import _check_ready  # pyright: ignore[reportPrivateUsage]

    with pytest.raises(StorageError):
        await _check_ready(PoolCell())


async def test_system_fills_pool_cell_inside_scope(tmp_path: Path) -> None:
    """``pool_cell.pool`` is live inside the lifespan and cleared after exit,
    so OAuth token storage never reaches a dead pool.
    """
    from lore.repositories import PoolCell
    from lore.server import system

    settings = _settings_for(tmp_path / "test.db")
    pool_cell = PoolCell()
    async with system(settings, pool_cell=pool_cell):
        assert pool_cell.pool is not None

    assert pool_cell.pool is None


async def test_system_clears_pool_cell_on_caller_exception(tmp_path: Path) -> None:
    """The cell is cleared on the exception exit, mirroring the probe cell."""
    from lore.repositories import PoolCell
    from lore.server import system

    settings = _settings_for(tmp_path / "test.db")
    pool_cell = PoolCell()
    with pytest.raises(RuntimeError, match="caller boom"):
        async with system(settings, pool_cell=pool_cell):
            assert pool_cell.pool is not None
            raise RuntimeError("caller boom")

    assert pool_cell.pool is None


async def test_system_closes_pool_when_caller_raises(tmp_path: Path) -> None:
    """Behavioural port of the old setup test: real SQLite pool, caller
    raises inside the scope, the pool is released and the cell cleared.
    """
    from lore.repositories import PoolCell
    from lore.repositories import connect as real_connect
    from lore.server import _check_ready, system  # pyright: ignore[reportPrivateUsage]

    settings = _settings_for(tmp_path / "test.db")
    pool_cell = PoolCell()

    pool_ref: list[RepositoryPool] = []

    async def capturing_connect(settings: LoreSettings) -> RepositoryPool:
        pool = await real_connect(settings)
        pool_ref.append(pool)
        return pool

    with (
        patch("lore.server.connect", new=capturing_connect),
        pytest.raises(RuntimeError, match="caller boom"),
    ):
        async with system(settings, pool_cell=pool_cell):
            raise RuntimeError("caller boom")

    # The underlying aiosqlite connection is closed; a fresh session()
    # attempt fails the SELECT 1 guard with StorageError.
    session_cm = pool_ref[0].session()
    with pytest.raises(StorageError):
        await session_cm.__aenter__()

    # The cell is cleared on the exception exit: /ready answers 503 again.
    with pytest.raises(StorageError):
        await _check_ready(pool_cell)


async def test_server_factory_builds_fastmcp_instance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The no-arg factory returns a FastMCP instance whose lifespan actually
    runs: the in-memory client enters it and lists the model-facing tools.
    """
    from lore.server import server

    monkeypatch.chdir(tmp_path)
    (tmp_path / "lore.toml").write_text(_COMPLETE_TOML.read_text())
    env = {"DATABASE_URL": f"sqlite:///{tmp_path / 'test.db'}"}
    with patch.dict(os.environ, env, clear=True):
        instance = server()
        assert isinstance(instance, FastMCP)
        async with Client(instance) as client:
            tools = await client.list_tools()
        # The entire model-facing surface: app-scoped observatory tools stay
        # invisible (IDEA.md's one-tool discipline, survived as two).
        assert [tool.name for tool in tools] == ["consult", "observe"]


async def test_server_factory_survives_sequential_client_sessions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each lifespan cycle opens a fresh ``system()`` scope.

    FastMCP's lifespan manager is ref-counted and re-enterable; the
    in-memory transport cycles it per client session. A factory handing
    over a single-use CM instance would die on the second session with
    ``RuntimeError("generator didn't yield")``.
    """
    from lore.server import server

    monkeypatch.chdir(tmp_path)
    (tmp_path / "lore.toml").write_text(_COMPLETE_TOML.read_text())
    env = {"DATABASE_URL": f"sqlite:///{tmp_path / 'test.db'}"}
    with patch.dict(os.environ, env, clear=True):
        instance = server()
        for _ in range(2):
            async with Client(instance) as client:
                tools = await client.list_tools()
            assert [tool.name for tool in tools] == ["consult", "observe"]


def test_server_factory_wires_ready_probe_to_pool_lifetime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``/ready`` answers 503 before the lifespan, 200 inside, 503 after.

    Pins the factory's ``health_probe=_check_ready`` wiring: an unwired
    ``None`` probe would answer 200 unconditionally, so the pre-lifespan
    503 is the observable difference.
    """
    from lore.server import server

    monkeypatch.chdir(tmp_path)
    (tmp_path / "lore.toml").write_text(_COMPLETE_TOML.read_text())
    env = {"DATABASE_URL": f"sqlite:///{tmp_path / 'test.db'}"}
    with patch.dict(os.environ, env, clear=True):
        client = TestClient(server().http_app())
        assert client.get("/ready").status_code == 503
        with client:
            assert client.get("/ready").status_code == 200
        assert client.get("/ready").status_code == 503


def test_server_factory_wires_oauth_storage_into_auth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With OIDC configured, the factory hands OIDCProxy the Lore-backed,
    Fernet-wrapped client storage instead of fastmcp's local file default.

    ``OIDCProxy`` is patched because its constructor fetches the discovery
    document over the network; the assertion targets what the factory
    passed it. OIDC arrives via env (``OIDC_URL``/``BASE_URL``), the only
    channel ``load_settings`` reads it from.
    """
    from key_value.aio.wrappers.encryption.fernet import FernetEncryptionWrapper

    from lore.server import server

    monkeypatch.chdir(tmp_path)
    (tmp_path / "lore.toml").write_text(_COMPLETE_TOML.read_text())
    env = {
        "DATABASE_URL": "sqlite:///:memory:",
        "OIDC_URL": "oidc://test-client:test-secret@auth.example.com/authorize",
        "BASE_URL": "https://lore.example.com",
    }
    with (
        patch.dict(os.environ, env, clear=True),
        patch("lore.adapter.mcp.OIDCProxy") as mock_proxy,
    ):
        server()
    assert isinstance(mock_proxy.call_args.kwargs["client_storage"], FernetEncryptionWrapper)


def test_server_factory_wires_session_state_into_repository_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """MCP session state lands in the repository-backed store, not fastmcp's
    in-memory default: session state survives restarts in every topology,
    so no OIDC env is configured here.
    """
    from lore.repositories import LoreCacheStore
    from lore.server import server

    monkeypatch.chdir(tmp_path)
    (tmp_path / "lore.toml").write_text(_COMPLETE_TOML.read_text())
    env = {"DATABASE_URL": "sqlite:///:memory:"}
    with patch.dict(os.environ, env, clear=True):
        instance = server()
    state_storage = instance._state_storage  # pyright: ignore[reportPrivateUsage]
    assert isinstance(state_storage, LoreCacheStore)


def test_server_factory_emits_bootstrap_env_log_through_structlog(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The bootstrap.env diagnostic event reaches the configured structlog renderer.

    The factory calls ``configure_telemetry()`` before ``load_settings()``,
    so the ``bootstrap.env`` log emitted during settings load routes through
    the installed renderer instead of being dropped at the stdlib root's
    default WARNING level.
    """
    from lore.server import server

    monkeypatch.chdir(tmp_path)
    (tmp_path / "lore.toml").write_text(_COMPLETE_TOML.read_text())
    env = {"DATABASE_URL": "sqlite:///:memory:"}
    with patch.dict(os.environ, env, clear=True):
        server()

    rendered = capsys.readouterr().err
    assert "bootstrap.env" in rendered


def test_server_factory_configures_telemetry_before_settings() -> None:
    """Pin the invariant: configure_telemetry runs before load_settings, so
    the bootstrap.env log routes through the configured structlog wrapper.
    """
    from lore.server import server

    call_order: list[str] = []

    def record_telemetry() -> None:
        call_order.append("configure_telemetry")

    def record_settings() -> LoreSettings:
        call_order.append("load_settings")
        return load_settings(toml_path=_COMPLETE_TOML)

    env = {"DATABASE_URL": "sqlite:///:memory:"}
    with (
        patch.dict(os.environ, env, clear=True),
        patch("lore.server.configure_telemetry", side_effect=record_telemetry),
        patch("lore.server.load_settings", side_effect=record_settings),
    ):
        server()

    assert call_order == ["configure_telemetry", "load_settings"]
