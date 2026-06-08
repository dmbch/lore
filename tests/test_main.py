"""Tests for lore.__main__ — composition root."""

import os
from collections.abc import AsyncGenerator, Callable, Generator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import structlog

from lore.config import LoreSettings
from lore.repositories import RepositoryPool

_COMPLETE_TOML = Path(__file__).parent / "fixtures" / "lore_complete.toml"

# The opt-in ``reset_telemetry`` fixture from ``tests/conftest.py`` is used as a
# module-scope autouse here: every test in this file calls ``configure()``
# (directly or via ``bootstrap``), and ``configure()`` calls
# ``configure_telemetry()`` whose once-only guard would otherwise trip on the
# second test in the run.
pytestmark = pytest.mark.usefixtures("reset_telemetry")


@pytest.fixture()
def bootstrap_env() -> Generator[None]:
    """Minimal env for bootstrap: SQLite in-memory, no OTLP, quiet logs."""
    env = {"DATABASE_URL": "sqlite:///:memory:"}
    with patch.dict(os.environ, env, clear=True):
        yield


async def test_setup_and_bootstrap_complete_without_error(tmp_path: Path) -> None:
    """setup() scopes the pool; bootstrap() wires the orchestrator over it."""
    from lore.__main__ import bootstrap, setup
    from lore.config import load_settings

    db_path = tmp_path / "test.db"
    env = {"DATABASE_URL": f"sqlite:///{db_path}"}
    with patch.dict(os.environ, env, clear=True):
        settings = load_settings(toml_path=_COMPLETE_TOML)

    async with setup(settings) as pool, bootstrap(settings, pool):
        pass


def test_configure_loads_settings_and_wires_telemetry(bootstrap_env: None) -> None:
    """configure() loads TOML settings and wires telemetry against API-proxy providers
    when run outside ``opentelemetry-instrument``.

    The positive bare-launch assertion is that the module-level ``start_span``
    yields a non-recording span (``is_valid`` is ``False``): that's the documented
    contract when the auto-config wrapper did not install an SDK ``TracerProvider``.
    The wiring is still exercisable end-to-end — ``start_span`` enters and exits,
    module-level structlog loggers emit — but recording/exporting is the SDK's
    job, owned by the wrapper.
    """
    from lore.__main__ import configure
    from lore.telemetry import start_span

    settings = configure(toml_path=_COMPLETE_TOML)
    assert settings.dsn == "sqlite:///:memory:"
    log = structlog.get_logger(__name__)
    with start_span("test") as span:
        assert not span.get_span_context().is_valid
        log.info("inside span")


def test_main_runs_amain_via_asyncio_run() -> None:
    """main() is the thin sync wrapper around asyncio.run(amain())."""
    from lore.__main__ import main

    # ``new=MagicMock(...)`` overrides the autouse AsyncMock for an
    # ``async def`` symbol so ``amain()`` returns the sentinel directly
    # rather than wrapping it in a coroutine — the contract we want to
    # pin is "main passes amain() into asyncio.run", regardless of how
    # the coroutine is constructed.
    sentinel = MagicMock(name="amain_coroutine")
    with (
        patch("lore.__main__.amain", new=MagicMock(return_value=sentinel)) as mock_amain,
        patch("lore.__main__.asyncio.run") as mock_run,
    ):
        main()
    mock_amain.assert_called_once_with()
    mock_run.assert_called_once_with(sentinel)


def _fake_setup_cm(
    pool: RepositoryPool,
) -> Callable[[LoreSettings], AbstractAsyncContextManager[RepositoryPool]]:
    """Return a ``setup``-shaped async CM yielding ``pool``."""

    @asynccontextmanager
    async def fake_setup(_settings: LoreSettings) -> AsyncGenerator[RepositoryPool]:
        yield pool

    return fake_setup


async def test_amain_wires_live_pool_probe(bootstrap_env: None) -> None:
    """amain() builds the probe via make_probe(pool) and passes it to create_server.

    Pins the live-pool contract: the probe and the orchestrator share the
    same pool, so ``/ready`` answers the question ``consult`` would face.
    """
    from lore.__main__ import amain

    fake_pool = MagicMock(name="pool", spec=RepositoryPool)
    fake_probe = AsyncMock(name="probe")
    mock_server = MagicMock()
    with (
        patch("lore.__main__.configure", return_value=MagicMock()),
        patch("lore.__main__.setup", new=_fake_setup_cm(fake_pool)),
        patch("lore.__main__.make_probe", return_value=fake_probe) as mock_make_probe,
        patch("lore.__main__.bootstrap", return_value=MagicMock()),
        patch("lore.__main__.create_server", return_value=mock_server) as mock_create,
        patch("lore.__main__.serve", new=AsyncMock()) as mock_serve,
    ):
        await amain()

    mock_make_probe.assert_called_once_with(fake_pool)
    assert mock_create.call_args.kwargs["health_probe"] is fake_probe
    mock_serve.assert_awaited_once_with(mock_server)


async def test_setup_closes_pool_when_caller_raises(bootstrap_env: None) -> None:
    """``setup`` calls ``pool.close()`` when the scope body raises.

    Mock-level: verifies ``setup`` invokes the close. See
    ``test_setup_releases_real_pool_when_caller_raises`` for the
    behavioural variant that confirms the underlying connection is gone.
    """
    from lore.__main__ import setup

    fake_pool = MagicMock(name="pool", spec=RepositoryPool)
    fake_pool.close = AsyncMock()
    with (
        patch("lore.__main__.run_migrations"),
        patch("lore.__main__.check_health"),
        patch("lore.__main__.connect", new=AsyncMock(return_value=fake_pool)),
        pytest.raises(RuntimeError, match="caller boom"),
    ):
        async with setup(MagicMock()):
            raise RuntimeError("caller boom")
    fake_pool.close.assert_awaited_once()


async def test_setup_releases_real_pool_when_caller_raises(tmp_path: Path) -> None:
    """Behavioural variant of the mock test: real SQLite pool, real release.

    Opens a real pool via ``setup``, raises inside the scope, then
    confirms the underlying aiosqlite connection has been closed.
    """
    from lore.__main__ import setup
    from lore.config import load_settings
    from lore.repositories.sqlite.pool import SqlitePool

    db_path = tmp_path / "test.db"
    env = {"DATABASE_URL": f"sqlite:///{db_path}"}
    with patch.dict(os.environ, env, clear=True):
        settings = load_settings(toml_path=_COMPLETE_TOML)

    pool_ref: list[SqlitePool] = []
    with pytest.raises(RuntimeError, match="caller boom"):
        async with setup(settings) as pool:
            pool_ref.append(cast("SqlitePool", pool))
            raise RuntimeError("caller boom")

    # The underlying aiosqlite connection is closed; a fresh session()
    # attempt fails the SELECT 1 guard with StorageError.
    from lore.domain import StorageError

    session_cm = pool_ref[0].session()
    with pytest.raises(StorageError):
        await session_cm.__aenter__()


async def test_amain_closes_pool_when_server_raises(bootstrap_env: None) -> None:
    """If server.run_async raises, ``setup``'s CM still closes the pool.

    The scope-bound CM contract is what keeps the pool from leaking on a
    server crash. Pin it explicitly so a future refactor that re-arranges
    the cleanup path breaks loudly.
    """
    from lore.__main__ import amain

    fake_pool = MagicMock(name="pool", spec=RepositoryPool)
    fake_pool.close = AsyncMock()
    mock_server = MagicMock()
    with (
        patch("lore.__main__.configure", return_value=MagicMock()),
        patch("lore.__main__.run_migrations"),
        patch("lore.__main__.check_health"),
        patch("lore.__main__.connect", new=AsyncMock(return_value=fake_pool)),
        patch("lore.__main__.make_probe", return_value=AsyncMock()),
        patch("lore.__main__.bootstrap", return_value=MagicMock()),
        patch("lore.__main__.create_server", return_value=mock_server),
        patch("lore.__main__.serve", new=AsyncMock(side_effect=RuntimeError("server crash"))),
        pytest.raises(RuntimeError, match="server crash"),
    ):
        await amain()

    fake_pool.close.assert_awaited_once()


# ---------------------------------------------------------------------------
# Auth opt-in: [server] auth_required = true requires OIDC at boot
# ---------------------------------------------------------------------------


def _toml_with_auth_required(tmp_path: Path) -> Path:
    """Write a complete TOML fixture with auth_required = true."""
    base = _COMPLETE_TOML.read_text()
    new = base.replace("auth_required = false", "auth_required = true", 1)
    if new == base:
        raise RuntimeError(
            f"{_COMPLETE_TOML.name} must contain 'auth_required = false' for this helper"
        )
    toml_file = tmp_path / "lore.toml"
    toml_file.write_text(new)
    return toml_file


def test_configure_refuses_when_auth_required_and_oidc_missing(tmp_path: Path) -> None:
    """auth_required=true with no OIDC_URL is a fail-fast at boot."""
    from lore.__main__ import configure

    toml_file = _toml_with_auth_required(tmp_path)
    env = {"DATABASE_URL": "sqlite:///:memory:"}
    with (
        patch.dict(os.environ, env, clear=True),
        pytest.raises(ValueError, match="auth_required"),
    ):
        configure(toml_path=toml_file)


def test_configure_boots_when_auth_required_with_oidc(tmp_path: Path) -> None:
    """auth_required=true with OIDC_URL + BASE_URL boots normally."""
    from lore.__main__ import configure

    toml_file = _toml_with_auth_required(tmp_path)
    env = {
        "DATABASE_URL": "sqlite:///:memory:",
        "OIDC_URL": "oidc://client:secret@auth.example.com/.well-known/openid-configuration",
        "BASE_URL": "https://lore.example.com",
    }
    with patch.dict(os.environ, env, clear=True):
        settings = configure(toml_path=toml_file)
        assert settings.oidc is not None
        assert settings.server.auth_required is True


def test_configure_default_boots_without_oidc() -> None:
    """Default auth_required=false: no OIDC, any FASTMCP_HOST — boots without complaint."""
    from lore.__main__ import configure

    env = {
        "DATABASE_URL": "sqlite:///:memory:",
        "FASTMCP_HOST": "0.0.0.0",
    }
    with patch.dict(os.environ, env, clear=True):
        settings = configure(toml_path=_COMPLETE_TOML)
        assert settings.server.auth_required is False
        assert settings.oidc is None


def test_configure_oidc_without_base_url_fails_in_loader_not_configure(tmp_path: Path) -> None:
    """OIDC ↔ BASE_URL pairing is owned by load_settings, not the configure-level check.

    Pins the layering: configure's auth_required check only enforces ``oidc is None``,
    relying on the loader to have already rejected the partial pair. A future refactor
    that weakens the loader pairing must also re-examine the configure check.
    """
    from lore.__main__ import configure

    toml_file = _toml_with_auth_required(tmp_path)
    env = {
        "DATABASE_URL": "sqlite:///:memory:",
        "OIDC_URL": "oidc://client:secret@auth.example.com/.well-known/openid-configuration",
        # BASE_URL deliberately absent.
    }
    with (
        patch.dict(os.environ, env, clear=True),
        pytest.raises(ValueError, match="OIDC_URL requires BASE_URL"),
    ):
        configure(toml_path=toml_file)


# ---------------------------------------------------------------------------
# Telemetry initializes before settings load, so transport-mode startup logs
# reach the configured stderr renderer instead of being silently dropped.
# ---------------------------------------------------------------------------


def test_configure_emits_bootstrap_env_log_through_structlog(
    bootstrap_env: None, capsys: pytest.CaptureFixture[str]
) -> None:
    """The bootstrap.env diagnostic event reaches the configured structlog renderer.

    The product invariant: ``configure()`` calls ``configure_telemetry()``
    first, so the ``bootstrap.env`` log emitted by ``load_settings``
    routes through the renderer the wrapper installed instead of being
    dropped at the stdlib root's default WARNING level.
    """
    from lore.__main__ import configure

    configure(toml_path=_COMPLETE_TOML)

    rendered = capsys.readouterr().err
    assert "bootstrap.env" in rendered


def test_configure_initializes_telemetry_before_settings_load(bootstrap_env: None) -> None:
    """Pin the invariant: configure_telemetry runs before load_settings, so
    settings-time logs route through the configured structlog wrapper.
    """
    from lore.__main__ import configure

    call_order: list[str] = []

    def record_telemetry() -> None:
        call_order.append("configure_telemetry")

    def record_settings(*, toml_path: Path | None = None) -> object:
        call_order.append("load_settings")
        from lore.config import load_settings

        return load_settings(toml_path=toml_path)

    with (
        patch("lore.__main__.configure_telemetry", side_effect=record_telemetry),
        patch("lore.__main__.load_settings", side_effect=record_settings),
    ):
        configure(toml_path=_COMPLETE_TOML)

    assert call_order == ["configure_telemetry", "load_settings"]
