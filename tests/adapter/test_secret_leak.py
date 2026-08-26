"""Regression test: OIDC client_secret never leaks from the ``create_server`` adapter surface.

Scope is intentionally narrow. This test pins two contracts on
``create_server`` while it parses settings, wires the lifespan, and
constructs ``OIDCProxy``:

- **Positive:** ``OIDCProxy`` must receive ``client_secret`` as a constructor
  kwarg. Secrets must reach the auth path; the leak we forbid is downstream
  in telemetry.
- **Negative:** the sentinel ``client_secret`` must not appear in any
  structlog event or span attribute emitted during construction.

Both scans carry a positive control: construction may legitimately emit
nothing, so an empty capture would pass on any secret. A marker event and a
marker span attribute prove the captures are armed before the scans run.

``OIDCProxy`` is patched with ``MagicMock`` so the real discovery flow never
executes. That path's logging is FastMCP's contract, not Lore's, and
exercising it would require a fake OIDC server (``respx`` / ``pytest-httpx``)
for marginal coverage on third-party code.

The test installs an SDK ``TracerProvider`` as the global tracer provider with
an ``InMemorySpanExporter`` attached, mirroring what ``opentelemetry-instrument``
does at process start in production. Then calls ``configure_telemetry()`` so the
facade and structlog stack bind to that recording provider. It drives
``create_server`` in HTTP mode with a sentinel client_secret. Downstream
OIDCProxy logging is out of scope.
"""

import os
from collections.abc import AsyncGenerator, Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import structlog
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic import SecretStr

from lore import telemetry as telemetry_module
from lore.adapter import OidcConfig
from lore.adapter._mcp import create_server
from lore.config import load_settings
from lore.orchestrator import Orchestrator

_COMPLETE_TOML = Path(__file__).parents[1] / "fixtures" / "lore_complete.toml"
_SENTINEL_SECRET = "SENTINEL-SECRET-XYZ-12345"
_SENTINEL_CLIENT_ID = "SENTINEL-CLIENT-ID-67890"
_SENTINEL_BASE_URL = "https://lore.sentinel.example.com"
_CONTROL_MARKER = "CONTROL-MARKER-ARMED-54321"


@asynccontextmanager
async def _noop_system() -> AsyncGenerator[Orchestrator]:
    yield MagicMock(spec=Orchestrator)


@pytest.fixture
def bootstrap_env() -> Iterator[None]:
    """Minimal env for settings load: SQLite, predictable LOG_LEVEL."""
    env = {"DATABASE_URL": "sqlite:///:memory:", "LOG_LEVEL": "DEBUG"}
    with patch.dict(os.environ, env, clear=True):
        yield


@pytest.fixture
def captured_spans(
    reset_telemetry: None,
    bootstrap_env: None,
) -> Iterator[InMemorySpanExporter]:
    """Install a recording ``TracerProvider``; configure telemetry against it.

    Mirrors what ``opentelemetry-instrument`` does at process start: an SDK
    ``TracerProvider`` with an ``InMemorySpanExporter`` attached is patched
    onto ``otel_trace.get_tracer_provider`` for the test's duration. We patch
    rather than calling ``set_tracer_provider`` because the OTel API's global
    setter is ``Once()``: it cannot be reassigned across tests, so patching
    the lookup is the only way to keep tests isolated.

    Suite-wide invariant: no test in this run may call
    ``otel_trace.set_tracer_provider(...)``. If one does, the once-lock pins
    the installed SDK provider for the rest of the process and every later
    test sees it through ``get_tracer_provider()`` regardless of teardown.

    structlog events are captured by ``structlog.testing.capture_logs`` in
    the test itself, not by pytest's stream fixtures: the configured
    PrintLogger writes past both the sys-level (``capsys``) and descriptor
    level (``capfd``) buffers, so either would scan an empty string.
    """
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))

    with patch("lore.telemetry.otel_trace.get_tracer_provider", return_value=provider):
        telemetry_module.configure_telemetry()
        yield exporter


def test_oidc_client_secret_does_not_leak_to_logs_or_spans(
    captured_spans: InMemorySpanExporter,
) -> None:
    """Booting create_server in HTTP mode must not write the client_secret anywhere observable.

    Observable surfaces under test: every structlog event and every
    attribute on every span emitted during ``create_server``. The DB-bound
    ``OidcConfig.client_secret`` field is exempt: secrets must reach the
    OIDCProxy constructor; what we forbid is them being written to telemetry.
    """
    span_exporter = captured_spans

    # Settings without OIDC, then patched to inject the sentinel credentials.
    base_settings = load_settings(toml_path=_COMPLETE_TOML)
    settings = base_settings.model_copy(
        update={
            "oidc": OidcConfig(
                discovery_url="https://auth.sentinel.example.com/.well-known/openid-configuration",
                client_id=_SENTINEL_CLIENT_ID,
                client_secret=SecretStr(_SENTINEL_SECRET),
            ),
            "base_url": _SENTINEL_BASE_URL,
        }
    )

    # Patch OIDCProxy so we don't make real discovery calls. We still want to
    # observe what create_server writes to telemetry around the construction.
    with (
        structlog.testing.capture_logs() as captured_logs,
        patch("lore.adapter._mcp.OIDCProxy") as mock_proxy,
    ):
        mock_proxy.return_value = MagicMock()
        server = create_server(settings=settings, system=_noop_system)
        # Positive control: both scans below run over surfaces construction
        # may legitimately leave empty, so an empty capture proves nothing.
        # Prove the capture is armed with a marker the scans would catch if
        # it were the secret.
        with telemetry_module.start_span("leak.control", marker=_CONTROL_MARKER):
            structlog.get_logger(__name__).info("leak.control", marker=_CONTROL_MARKER)

    # OIDCProxy must have received the raw secret. The leak we forbid is in
    # telemetry, not the auth path itself. The adapter unwraps the SecretStr
    # exactly once at the OIDCProxy boundary.
    mock_proxy.assert_called_once()
    assert mock_proxy.call_args.kwargs["client_secret"] == _SENTINEL_SECRET
    assert server.auth is mock_proxy.return_value

    # structlog's own capture, not capsys/capfd: the configured PrintLogger
    # writes past both pytest stream buffers, so scanning either reads empty
    # and passes on any secret. Event dicts are the surface that matters.
    # capture_logs drops the contextvar merge, so a secret bound through
    # start_span's context reaches the span scan below, not this one.
    log_output = "\n".join(str(event) for event in captured_logs)
    assert _CONTROL_MARKER in log_output, (
        "log capture is not wired: the control event never arrived, so a"
        f" leaked secret would not have either:\n{log_output}"
    )
    assert _SENTINEL_SECRET not in log_output, f"client_secret leaked to log output:\n{log_output}"

    finished_spans = span_exporter.get_finished_spans()
    control_attributes = [
        value
        for span in finished_spans
        for value in (span.attributes or {}).values()
        if str(value) == _CONTROL_MARKER
    ]
    assert control_attributes, (
        "span capture is not wired: the control span never reached the exporter,"
        " so the attribute scan below would pass on any secret"
    )
    for span in finished_spans:
        if span.attributes is None:
            continue
        for key, value in span.attributes.items():
            rendered = str(value)
            assert _SENTINEL_SECRET not in rendered, (
                f"client_secret leaked to span attribute {span.name!r}.{key!r}: {rendered!r}"
            )


def test_oidc_config_repr_and_model_dump_mask_client_secret() -> None:
    """``repr`` and ``model_dump`` paths render the secret as ``'**********'``.

    Pinned via ``pydantic.SecretStr``: closes the structural leak surface
    a future debug log of ``settings`` or a stray ``model_dump()`` would
    otherwise open. Callers that genuinely need the value must call
    ``.get_secret_value()`` explicitly.
    """
    cfg = OidcConfig(
        discovery_url="https://auth.sentinel.example.com/.well-known/openid-configuration",
        client_id=_SENTINEL_CLIENT_ID,
        client_secret=SecretStr(_SENTINEL_SECRET),
    )
    assert _SENTINEL_SECRET not in repr(cfg)
    assert "**********" in repr(cfg)
    dumped = cfg.model_dump()
    # ``model_dump`` returns the SecretStr instance, whose str() is masked.
    assert _SENTINEL_SECRET not in str(dumped["client_secret"])
    assert "**********" in str(dumped["client_secret"])
    # ``model_dump_json`` likewise renders the masked form.
    assert _SENTINEL_SECRET not in cfg.model_dump_json()
    # The unwrap escape hatch still works.
    assert cfg.client_secret.get_secret_value() == _SENTINEL_SECRET
