"""Tests for lore.adapter.mcp — FastMCP server and tool registration."""

import os
import re
from collections.abc import AsyncGenerator, Generator, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import structlog
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.tools import Tool
from pydantic import SecretStr

from lore.adapter.mcp import create_server
from lore.config import LoreSettings, load_settings
from lore.domain import ConsultLoreRequest, ConsultLoreResponse
from lore.domain.errors import StorageError
from lore.orchestrator import Orchestrator
from lore.prompts import build_system_prompt

_COMPLETE_TOML = Path(__file__).parents[1] / "fixtures" / "lore_complete.toml"


@asynccontextmanager
async def _noop_system() -> AsyncGenerator[Orchestrator]:
    yield MagicMock(spec=Orchestrator)


@pytest.fixture()
def bootstrap_env() -> Generator[None]:
    """Minimal env for bootstrap: SQLite in-memory, no OTLP, quiet logs."""
    env = {"DATABASE_URL": "sqlite:///:memory:"}
    with patch.dict(os.environ, env, clear=True):
        yield


@pytest.fixture()
def settings(bootstrap_env: None) -> LoreSettings:
    """Load settings with the complete TOML fixture."""
    return load_settings(toml_path=_COMPLETE_TOML)


@pytest.fixture()
def server(settings: LoreSettings) -> FastMCP[Orchestrator]:
    """Return a FastMCP server built from settings."""
    return create_server(settings=settings, system=_noop_system())


async def test_server_registers_tool_with_configured_name(
    server: FastMCP[Orchestrator],
) -> None:
    tools = await _list_tools(server)
    names = [t.name for t in tools]
    assert "consult" in names


def test_server_scribe_prompt_becomes_instructions(
    settings: LoreSettings,
    server: FastMCP[Orchestrator],
) -> None:
    expected = build_system_prompt(settings.prompts)
    assert server.instructions == expected


def test_server_version_defaults_to_dev_marker(
    server: FastMCP[Orchestrator],
) -> None:
    """The settings default (a source build) surfaces as serverInfo's dev marker."""
    assert server.version == "0.0.0+dev"


def test_server_reports_configured_version(settings: LoreSettings) -> None:
    """create_server surfaces settings.version as serverInfo.version."""
    versioned = create_server(
        settings=settings.model_copy(update={"version": "1.2.3"}),
        system=_noop_system(),
    )
    assert versioned.version == "1.2.3"


@pytest.fixture()
def mock_orchestrator() -> AsyncMock:
    """An AsyncMock standing in for Orchestrator.consult."""
    orch = AsyncMock(spec=Orchestrator)
    orch.consult.return_value = ConsultLoreResponse(answer="the answer")
    return orch


@pytest.fixture()
def wired_server(settings: LoreSettings, mock_orchestrator: AsyncMock) -> FastMCP[Orchestrator]:
    """Server with a mock orchestrator injected via lifespan."""

    @asynccontextmanager
    async def fake_system() -> AsyncGenerator[Orchestrator]:
        yield mock_orchestrator

    srv = create_server(settings=settings, system=fake_system())

    # Inject the mock as the lifespan result so tool handlers can access it.
    srv._lifespan_result = mock_orchestrator  # pyright: ignore[reportPrivateUsage]
    srv._lifespan_result_set = True  # pyright: ignore[reportPrivateUsage]
    return srv


async def test_read_call_delegates_to_orchestrator(
    wired_server: FastMCP[Orchestrator], mock_orchestrator: AsyncMock
) -> None:
    result = await _call_tool(wired_server, "consult", {"question": "what is lore?"})
    mock_orchestrator.consult.assert_called_once()
    call_args = mock_orchestrator.consult.call_args
    request = call_args.kwargs["request"]
    assert isinstance(request, ConsultLoreRequest)
    assert request.question == "what is lore?"
    assert request.hypothesis is None
    assert result is not None


async def test_write_call_delegates_to_orchestrator(
    wired_server: FastMCP[Orchestrator], mock_orchestrator: AsyncMock
) -> None:
    result = await _call_tool(
        wired_server,
        "consult",
        {
            "question": "what happened?",
            "hypothesis": "service X crashed",
            "confidence": 0.8,
            "context": "investigating outage",
            "reasoning": "logs show OOM",
        },
    )
    mock_orchestrator.consult.assert_called_once()
    call_args = mock_orchestrator.consult.call_args
    request = call_args.kwargs["request"]
    assert isinstance(request, ConsultLoreRequest)
    assert request.question == "what happened?"
    assert request.hypothesis == "service X crashed"
    assert request.confidence == 0.8
    assert request.context == "investigating outage"
    assert request.reasoning == "logs show OOM"
    assert result is not None


async def test_tool_uses_default_oracle_id_without_auth(
    wired_server: FastMCP[Orchestrator], mock_orchestrator: AsyncMock
) -> None:
    """Without an access token (stdio mode), oracle_id is the synthetic ``_local``."""
    await _call_tool(wired_server, "consult", {"question": "who am I?"})
    call_args = mock_orchestrator.consult.call_args
    oracle_id = call_args.kwargs["oracle_id"]
    assert oracle_id == "_local"


async def test_tool_extracts_oracle_id_from_access_token(
    wired_server: FastMCP[Orchestrator], mock_orchestrator: AsyncMock
) -> None:
    """When an access token is present, oracle_id comes from the 'sub' claim."""
    fake_token = MagicMock()
    fake_token.claims = {"sub": "oracle-42"}
    with patch("lore.adapter.mcp.get_access_token", return_value=fake_token):
        await _call_tool(wired_server, "consult", {"question": "who am I?"})
    call_args = mock_orchestrator.consult.call_args
    oracle_id = call_args.kwargs["oracle_id"]
    assert oracle_id == "oracle-42"


async def test_correlation_id_distinct_per_consult(
    wired_server: FastMCP[Orchestrator], mock_orchestrator: AsyncMock
) -> None:
    """Two consult calls must produce distinct correlation_ids.

    Either path delivers this: FastMCP starts a fresh tool-call trace per
    invocation, and the no-SDK uuid4 fallback also produces distinct hex
    strings. MCP's session-scoped monotonic request_id is never used as
    the correlation_id.
    """
    await _call_tool(wired_server, "consult", {"question": "first"})
    await _call_tool(wired_server, "consult", {"question": "second"})

    first, second = mock_orchestrator.consult.call_args_list
    assert first.kwargs["correlation_id"] != second.kwargs["correlation_id"]


async def test_correlation_id_uses_trace_id_when_otel_active(
    wired_server: FastMCP[Orchestrator], mock_orchestrator: AsyncMock
) -> None:
    """With a valid OTel span context, correlation_id is the active trace_id.

    One identifier across client error, APM trace lookup, and ledger PK —
    same value the structlog trace-context processor injects into every
    log event, so no extra log-line bytes.
    """
    from opentelemetry.trace import NonRecordingSpan, SpanContext, TraceFlags

    ctx = SpanContext(
        trace_id=0x0123456789ABCDEF0123456789ABCDEF,
        span_id=0xFEDCBA9876543210,
        is_remote=False,
        trace_flags=TraceFlags(0x01),
    )
    with patch("lore.adapter.mcp.otel_trace.get_current_span", return_value=NonRecordingSpan(ctx)):
        await _call_tool(wired_server, "consult", {"question": "what?"})

    call_args = mock_orchestrator.consult.call_args
    assert call_args.kwargs["correlation_id"] == "0123456789abcdef0123456789abcdef"


async def test_correlation_id_falls_back_to_uuid_hex_without_otel_sdk(
    wired_server: FastMCP[Orchestrator], mock_orchestrator: AsyncMock
) -> None:
    """Without an SDK TracerProvider the active span context is invalid; mint a uuid4 hex."""
    await _call_tool(wired_server, "consult", {"question": "what?"})

    correlation_id = mock_orchestrator.consult.call_args.kwargs["correlation_id"]
    assert re.fullmatch(r"[0-9a-f]{32}", correlation_id) is not None


async def test_storage_error_scrubs_to_generic_tool_error_with_correlation_id(
    wired_server: FastMCP[Orchestrator], mock_orchestrator: AsyncMock
) -> None:
    """StorageError from orchestrator surfaces as a generic ToolError + correlation_id."""
    from fastmcp.exceptions import ToolError

    from lore.domain.errors import StorageError

    mock_orchestrator.consult.side_effect = StorageError("disk full at /var/lib/postgres")
    with pytest.raises(ToolError, match=r"internal error \(correlation_id=") as exc_info:
        await _call_tool(wired_server, "consult", {"question": "test"})
    payload = str(exc_info.value)
    assert "disk full" not in payload
    assert "/var/lib/postgres" not in payload
    assert isinstance(exc_info.value.__cause__, StorageError)


async def test_storage_error_does_not_leak_constraint_name_to_client(
    wired_server: FastMCP[Orchestrator], mock_orchestrator: AsyncMock
) -> None:
    """Schema and constraint names from psycopg/sqlite must not reach the client."""
    from fastmcp.exceptions import ToolError

    from lore.domain.errors import StorageError

    mock_orchestrator.consult.side_effect = StorageError(
        'duplicate key value violates unique constraint "hypotheses_pkey"'
    )
    with pytest.raises(ToolError) as exc_info:
        await _call_tool(wired_server, "consult", {"question": "test"})
    payload = str(exc_info.value)
    assert "hypotheses_pkey" not in payload
    assert "constraint" not in payload
    assert "duplicate" not in payload


async def test_inference_error_scrubs_to_generic_tool_error_with_correlation_id(
    wired_server: FastMCP[Orchestrator], mock_orchestrator: AsyncMock
) -> None:
    """InferenceError from orchestrator surfaces as a generic ToolError + correlation_id."""
    from fastmcp.exceptions import ToolError

    from lore.domain.errors import InferenceError

    mock_orchestrator.consult.side_effect = InferenceError(
        "openai.RateLimitError: api.openai.com/v1/chat returned 429"
    )
    with pytest.raises(ToolError, match=r"internal error \(correlation_id=") as exc_info:
        await _call_tool(wired_server, "consult", {"question": "test"})
    payload = str(exc_info.value)
    assert "openai" not in payload
    assert "api.openai.com" not in payload
    assert "RateLimitError" not in payload
    assert isinstance(exc_info.value.__cause__, InferenceError)


def test_server_with_oidc_configures_auth(settings: LoreSettings) -> None:
    """When OidcConfig is present, the server has an auth provider."""
    sentinel = MagicMock()
    with patch("lore.adapter.mcp._build_auth", return_value=sentinel):
        oidc_server = create_server(settings=settings, system=_noop_system())
    assert oidc_server.auth is sentinel


def test_server_without_oidc_has_no_auth(server: FastMCP[Orchestrator]) -> None:
    """Without OidcConfig, the server has no auth (stdio mode)."""
    assert server.auth is None


def test_build_auth_returns_none_without_oidc(settings: LoreSettings) -> None:
    """No OIDC config means no auth provider."""
    from lore.adapter.mcp import _build_auth  # pyright: ignore[reportPrivateUsage]

    assert _build_auth(settings) is None


def test_build_auth_returns_none_without_base_url(settings: LoreSettings) -> None:
    """OIDC config without base_url means no auth provider."""
    from lore.adapter.mcp import _build_auth  # pyright: ignore[reportPrivateUsage]
    from lore.config.types import OidcConfig

    oidc_settings = settings.model_copy(
        update={
            "oidc": OidcConfig(
                discovery_url="https://auth.example.com/.well-known/openid-configuration",
                client_id="test-client",
                client_secret=SecretStr("test-secret"),
            ),
        }
    )
    assert _build_auth(oidc_settings) is None


def test_build_auth_constructs_oidc_proxy(settings: LoreSettings) -> None:
    """With OIDC config and base_url, constructs OIDCProxy."""
    from lore.adapter.mcp import _build_auth  # pyright: ignore[reportPrivateUsage]
    from lore.config.types import OidcConfig

    oidc_settings = settings.model_copy(
        update={
            "oidc": OidcConfig(
                discovery_url="https://auth.example.com/.well-known/openid-configuration",
                client_id="test-client",
                client_secret=SecretStr("test-secret"),
            ),
            "base_url": "https://lore.example.com",
        }
    )
    with patch("lore.adapter.mcp.OIDCProxy") as mock_proxy:
        result = _build_auth(oidc_settings)
    mock_proxy.assert_called_once_with(
        config_url="https://auth.example.com/.well-known/openid-configuration",
        client_id="test-client",
        client_secret="test-secret",
        base_url="https://lore.example.com",
    )
    assert result is mock_proxy.return_value


async def test_server_lifespan_delegates_to_system_cm(
    settings: LoreSettings,
) -> None:
    """The server lifespan wraps the system CM and yields the orchestrator."""
    sentinel = MagicMock()

    @asynccontextmanager
    async def fake_system() -> AsyncGenerator[MagicMock]:
        yield sentinel

    server = create_server(settings=settings, system=fake_system())
    lifespan = server._lifespan  # pyright: ignore[reportPrivateUsage]
    async with lifespan(server) as result:
        assert result is sentinel


async def test_token_without_sub_claim_raises_tool_error(
    wired_server: FastMCP[Orchestrator],
) -> None:
    """Access token present but missing 'sub' claim raises a scrubbed ToolError."""
    from fastmcp.exceptions import ToolError

    fake_token = MagicMock()
    fake_token.claims = {"aud": "some-audience"}
    with (
        patch("lore.adapter.mcp.get_access_token", return_value=fake_token),
        pytest.raises(ToolError, match=r"authentication failed \(correlation_id="),
    ):
        await _call_tool(wired_server, "consult", {"question": "who am I?"})


async def test_token_with_non_string_sub_claim_raises_tool_error(
    wired_server: FastMCP[Orchestrator],
) -> None:
    """A non-string 'sub' claim is rejected at the boundary with a scrubbed message."""
    from fastmcp.exceptions import ToolError

    fake_token = MagicMock()
    fake_token.claims = {"sub": 12345}
    with (
        patch("lore.adapter.mcp.get_access_token", return_value=fake_token),
        pytest.raises(ToolError, match=r"authentication failed \(correlation_id="),
    ):
        await _call_tool(wired_server, "consult", {"question": "who am I?"})


@pytest.mark.parametrize(
    ("claims", "diagnostic_fragment"),
    [
        ({"aud": "some-audience"}, "missing 'sub' claim"),
        ({"sub": 12345}, "must be a string"),
        ({"sub": ""}, "must not be empty"),
        ({"sub": "_local"}, "synthetic"),
        ({"sub": "_transfer"}, "synthetic"),
    ],
)
async def test_authentication_error_scrubs_to_constant_message_and_logs_diagnostic(
    settings: LoreSettings,
    mock_orchestrator: AsyncMock,
    claims: dict[str, object],
    diagnostic_fragment: str,
) -> None:
    """Every AuthenticationError path scrubs the wire payload and logs the diagnostic.

    Walks every code path that raises ``AuthenticationError`` inside the
    adapter's ``consult`` body. Asserts two contracts at once: the client sees
    only the constant scrubbed message (with the correlation_id), and the
    structlog event preserves the original ``str(exc)`` under
    ``error_message=`` for operators.
    """

    @asynccontextmanager
    async def fake_system() -> AsyncGenerator[Orchestrator]:
        yield mock_orchestrator

    srv = create_server(settings=settings, system=fake_system())
    srv._lifespan_result = mock_orchestrator  # pyright: ignore[reportPrivateUsage]
    srv._lifespan_result_set = True  # pyright: ignore[reportPrivateUsage]

    fake_token = MagicMock()
    fake_token.claims = claims
    with (
        structlog.testing.capture_logs() as cap,
        patch("lore.adapter.mcp.get_access_token", return_value=fake_token),
        pytest.raises(ToolError) as exc_info,
    ):
        await _call_tool(srv, "consult", {"question": "who am I?"})

    payload = str(exc_info.value)
    # (a) Wire payload is exactly the constant scrub: only the correlation_id
    # varies. No diagnostic content can leak through this shape.
    assert re.fullmatch(r"authentication failed \(correlation_id=[0-9a-f-]+\)", payload), payload

    # (b) Log event preserves the original diagnostic under error_message=.
    auth_events = [e for e in cap if e.get("event") == "consult.auth_error"]
    assert len(auth_events) == 1
    event = auth_events[0]
    assert event["error_class"] == "AuthenticationError"
    assert diagnostic_fragment in event["error_message"]


def test_fastmcp_exposes_lifespan_result_attribute(
    server: FastMCP[Orchestrator],
) -> None:
    """Structural guard: wired_server injects _lifespan_result directly.

    If FastMCP renames or removes this internal attribute, this test fails
    before the wired_server fixture silently stops working. Validated against
    fastmcp 2.x — check on upgrade.
    """
    assert hasattr(server, "_lifespan_result"), (
        "FastMCP no longer exposes _lifespan_result — update wired_server fixture"
    )


async def test_confidence_out_of_range_raises_validation_error(
    wired_server: FastMCP[Orchestrator],
) -> None:
    """Confidence outside [-1, 1] is rejected at the tool boundary."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="less than or equal to 1"):
        await _call_tool(wired_server, "consult", {"question": "test", "confidence": 1.5})


async def test_question_exceeds_max_length_raises_validation_error(
    wired_server: FastMCP[Orchestrator],
) -> None:
    """Question exceeding max_length is rejected at the tool boundary."""
    from pydantic import ValidationError

    # limits.question is the configured max — exceed it by a wide margin.
    with pytest.raises(ValidationError, match="string_too_long"):
        await _call_tool(wired_server, "consult", {"question": "x" * 1_000_000})


async def test_fully_empty_consult_does_not_reach_orchestrator(
    wired_server: FastMCP[Orchestrator], mock_orchestrator: AsyncMock
) -> None:
    """An empty consult is rejected by the domain validator before the orchestrator runs.

    The validator raises Pydantic ValidationError, which the adapter scrubs to
    "invalid consult input" — Pydantic's str() includes `input_value=...`.
    """
    with pytest.raises(ToolError, match=r"invalid consult input \(correlation_id="):
        await _call_tool(wired_server, "consult", {})
    mock_orchestrator.consult.assert_not_called()


async def test_hypothesis_without_confidence_does_not_reach_orchestrator(
    wired_server: FastMCP[Orchestrator], mock_orchestrator: AsyncMock
) -> None:
    """A hypothesis without a confidence scalar never reaches the orchestrator."""
    with pytest.raises(ToolError, match=r"invalid consult input \(correlation_id="):
        await _call_tool(wired_server, "consult", {"hypothesis": "a claim"})
    mock_orchestrator.consult.assert_not_called()


async def test_question_only_reaches_orchestrator(
    wired_server: FastMCP[Orchestrator], mock_orchestrator: AsyncMock
) -> None:
    """A bare question-only call is the happy-path read signal."""
    await _call_tool(wired_server, "consult", {"question": "what?"})
    mock_orchestrator.consult.assert_called_once()


async def test_hypothesis_with_zero_confidence_reaches_orchestrator(
    wired_server: FastMCP[Orchestrator], mock_orchestrator: AsyncMock
) -> None:
    """confidence=0.0 is the genuine vacuous state — accepted, not absent."""
    await _call_tool(wired_server, "consult", {"hypothesis": "a claim", "confidence": 0.0})
    mock_orchestrator.consult.assert_called_once()
    request = mock_orchestrator.consult.call_args.kwargs["request"]
    assert request.confidence == 0.0
    assert request.hypothesis == "a claim"


async def test_idp_claim_in_synthetic_namespace_raises_tool_error(
    wired_server: FastMCP[Orchestrator],
) -> None:
    """An IdP that claims ``sub='_local'`` is refused at the adapter boundary.

    The synthetic ``_*`` namespace is reserved for identities the adapter
    trusts by construction (``_local``, ``_transfer``); IdP-claimed values in
    that namespace would let an external principal impersonate a synthetic in
    the ledger and bypass trust discounting.
    """
    fake_token = MagicMock()
    fake_token.claims = {"sub": "_local"}
    with (
        patch("lore.adapter.mcp.get_access_token", return_value=fake_token),
        pytest.raises(ToolError),
    ):
        await _call_tool(wired_server, "consult", {"question": "who am I?"})


async def test_idp_claim_with_underscore_transfer_namespace_raises_tool_error(
    wired_server: FastMCP[Orchestrator],
) -> None:
    """``sub='_transfer'`` is also refused: the synthetic check is namespace-scoped."""
    fake_token = MagicMock()
    fake_token.claims = {"sub": "_transfer"}
    with (
        patch("lore.adapter.mcp.get_access_token", return_value=fake_token),
        pytest.raises(ToolError),
    ):
        await _call_tool(wired_server, "consult", {"question": "who am I?"})


async def _list_tools(server: FastMCP[Orchestrator]) -> Sequence[Tool]:
    return await server.list_tools()


async def _call_tool(
    server: FastMCP[Orchestrator], name: str, arguments: dict[str, object]
) -> object:
    return await server.call_tool(name, arguments)


async def test_archivist_resolution_error_scrubs_to_generic_tool_error(
    wired_server: FastMCP[Orchestrator], mock_orchestrator: AsyncMock
) -> None:
    """ArchivistResolutionError uses the same scrub treatment as StorageError/InferenceError."""
    from fastmcp.exceptions import ToolError

    from lore.domain import ArchivistResolutionError

    mock_orchestrator.consult.side_effect = ArchivistResolutionError(
        "corroborates id 'deadbeef-...' did not surface"
    )
    with pytest.raises(ToolError, match=r"internal error \(correlation_id=") as exc_info:
        await _call_tool(wired_server, "consult", {"question": "test"})
    payload = str(exc_info.value)
    assert "deadbeef" not in payload
    assert "corroborates" not in payload
    assert isinstance(exc_info.value.__cause__, ArchivistResolutionError)


async def test_consult_auth_error_logs_correlation_id(
    wired_server: FastMCP[Orchestrator],
) -> None:
    """AuthenticationError path binds ``correlation_id`` on the structlog event."""
    fake_token = MagicMock()
    fake_token.claims = {"aud": "some-audience"}  # missing 'sub' claim
    with (
        structlog.testing.capture_logs() as cap,
        patch("lore.adapter.mcp.get_access_token", return_value=fake_token),
        pytest.raises(ToolError, match=r"authentication failed \(correlation_id="),
    ):
        await _call_tool(wired_server, "consult", {"question": "who am I?"})

    auth_events = [e for e in cap if e.get("event") == "consult.auth_error"]
    assert len(auth_events) == 1
    assert "correlation_id" in auth_events[0]


async def test_consult_validation_error_logs_correlation_id(
    wired_server: FastMCP[Orchestrator], mock_orchestrator: AsyncMock
) -> None:
    """ValidationError path binds ``correlation_id`` on the structlog event."""
    with (
        structlog.testing.capture_logs() as cap,
        pytest.raises(ToolError, match=r"invalid consult input \(correlation_id="),
    ):
        await _call_tool(wired_server, "consult", {})  # empty payload → ValidationError
    mock_orchestrator.consult.assert_not_called()

    err_events = [e for e in cap if e.get("event") == "consult.error.validation"]
    assert len(err_events) == 1
    event = err_events[0]
    assert "correlation_id" in event
    assert event["error_class"] == "ValidationError"


async def test_consult_internal_error_logs_correlation_id(
    wired_server: FastMCP[Orchestrator], mock_orchestrator: AsyncMock
) -> None:
    """The catch-all branch binds ``correlation_id`` on the structlog event."""
    mock_orchestrator.consult.side_effect = StorageError("disk full at /var/lib/postgres")
    with (
        structlog.testing.capture_logs() as cap,
        pytest.raises(ToolError, match=r"internal error \(correlation_id="),
    ):
        await _call_tool(wired_server, "consult", {"question": "test"})

    err_events = [e for e in cap if e.get("event") == "consult.error.internal"]
    assert len(err_events) == 1
    event = err_events[0]
    assert "correlation_id" in event
    assert event["error_class"] == "StorageError"
    assert "disk full" in event["error_message"]
