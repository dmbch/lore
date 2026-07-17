"""Tests for lore.adapter.mcp: FastMCP server and tool registration."""

import logging
import os
import re
from collections.abc import AsyncGenerator, Generator, Sequence
from contextlib import asynccontextmanager
from functools import partial
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_context
from fastmcp.tools import Tool
from mcp.types import TextResourceContents
from pydantic import SecretStr

from lore.adapter._contract import (  # pyright: ignore[reportPrivateUsage]
    CONSULT_TOOL,
    load_server_contract,
)
from lore.adapter.mcp import create_server
from lore.config import LoreSettings, load_settings
from lore.domain import ConsultLoreRequest, ConsultLoreResponse
from lore.domain.errors import StorageError
from lore.orchestrator import Orchestrator
from lore.prompts import load_prompt

_COMPLETE_TOML = Path(__file__).parents[1] / "fixtures" / "lore_complete.toml"


@asynccontextmanager
async def _noop_system(orchestrator: Orchestrator | None = None) -> AsyncGenerator[Orchestrator]:
    """System factory for tests: yields the given orchestrator, or an inert mock.

    Passed to ``create_server`` uncalled (or via ``partial``): the server
    calls it per lifespan cycle, minting a fresh CM each time.
    """
    yield orchestrator if orchestrator is not None else MagicMock(spec=Orchestrator)


@pytest.fixture()
def bootstrap_env() -> Generator[None]:
    """Minimal env for bootstrap: SQLite in-memory, no OTLP, quiet logs."""
    env = {"DATABASE_URL": "sqlite:///:memory:"}
    with patch.dict(os.environ, env, clear=True):
        yield


@pytest.fixture()
def settings(bootstrap_env: None) -> LoreSettings:
    return load_settings(toml_path=_COMPLETE_TOML)


@pytest.fixture()
def server(settings: LoreSettings) -> FastMCP[Orchestrator]:
    return create_server(settings=settings, system=_noop_system)


def test_bundled_logo_returns_png_data_uri() -> None:
    import base64

    from lore.adapter.mcp import _bundled_logo  # pyright: ignore[reportPrivateUsage]

    uri = _bundled_logo()
    assert uri.startswith("data:image/png;base64,")
    payload = uri.removeprefix("data:image/png;base64,")
    decoded = base64.b64decode(payload)
    assert decoded[:8] == b"\x89PNG\r\n\x1a\n"  # PNG magic


async def test_server_registers_tool_with_configured_name(
    server: FastMCP[Orchestrator],
) -> None:
    tools = await _list_tools(server)
    names = [t.name for t in tools]
    assert "consult" in names


async def test_server_registers_observatory_entry_tool(
    server: FastMCP[Orchestrator],
) -> None:
    """The composed server carries the observatory: ``observe`` is model-visible.

    Pins the ``add_provider`` wiring in ``create_server``, which also puts the
    observatory tools behind the server's masking and identity middleware.
    """
    tools = await _list_tools(server)
    names = [t.name for t in tools]
    assert "observe" in names


def test_instructions_teach_disbelief_via_negative_confidence(
    server: FastMCP[Orchestrator],
) -> None:
    """Disbelief-via-negative-confidence teaching lives in the ambient instructions."""
    text = (server.instructions or "").lower()
    assert "negative confidence" in text
    assert "textual negation" in text or "do not negate" in text or "not a textual" in text


def test_instructions_come_from_contract(
    server: FastMCP[Orchestrator], settings: LoreSettings
) -> None:
    """Server instructions are sourced from the contract file, not hardcoded."""
    contract = load_server_contract(settings.prompts.contract)
    assert server.instructions == contract.instructions


async def test_tool_description_comes_from_contract(
    server: FastMCP[Orchestrator], settings: LoreSettings
) -> None:
    """The consult tool description is sourced from the contract file."""
    contract = load_server_contract(settings.prompts.contract)
    consult = next(t for t in await _list_tools(server) if t.name == CONSULT_TOOL)
    assert consult.description == contract.tools.consult.description


async def test_observe_description_comes_from_contract(
    server: FastMCP[Orchestrator], settings: LoreSettings
) -> None:
    """The observe tool description is sourced from the contract file."""
    contract = load_server_contract(settings.prompts.contract)
    observe = next(t for t in await _list_tools(server) if t.name == "observe")
    assert observe.description == contract.tools.observe.description


async def test_mcp_serves_consult_prompt(
    server: FastMCP[Orchestrator], settings: LoreSettings
) -> None:
    """The Scribe persona is served as the ``consult`` MCP prompt (/mcp__lore__consult)."""
    from fastmcp.prompts import PromptResult
    from mcp.types import TextContent

    prompts = await server.list_prompts()
    assert "consult" in [p.name for p in prompts]

    result = await server.render_prompt("consult")
    assert isinstance(result, PromptResult)
    content = result.messages[0].content
    assert isinstance(content, TextContent)
    assert content.text == load_prompt(settings.prompts.scribe)


async def test_consult_prompt_takes_no_required_arguments(
    server: FastMCP[Orchestrator],
) -> None:
    """The bare /mcp__lore__consult invocation must work: no required arguments."""
    prompts = await server.list_prompts()
    consult = next(p for p in prompts if p.name == "consult")
    assert all(not arg.required for arg in (consult.arguments or []))


def test_server_version_defaults_to_dev_marker(
    server: FastMCP[Orchestrator],
) -> None:
    """The settings default (a source build) surfaces as serverInfo's dev marker."""
    assert server.version == "0.0.0+dev"


def test_server_reports_configured_version(settings: LoreSettings) -> None:
    versioned = create_server(
        settings=settings.model_copy(update={"version": "1.2.3"}),
        system=_noop_system,
    )
    assert versioned.version == "1.2.3"


def test_create_server_uses_configured_icon_url(settings: LoreSettings) -> None:
    configured = settings.model_copy(
        update={
            "server": settings.server.model_copy(
                update={"icon_url": "https://example.com/lore.png"}
            )
        }
    )
    server = create_server(settings=configured, system=_noop_system)
    assert server.icons is not None
    assert len(server.icons) == 1
    assert server.icons[0].src == "https://example.com/lore.png"


def test_create_server_falls_back_to_bundled_logo(settings: LoreSettings) -> None:
    from lore.adapter.mcp import _bundled_logo  # pyright: ignore[reportPrivateUsage]

    # settings has icon_url=None by default (fixture uses base TOML).
    assert settings.server.icon_url is None  # sanity-check the fixture state
    server = create_server(settings=settings, system=_noop_system)
    assert server.icons is not None
    assert len(server.icons) == 1
    assert server.icons[0].src == _bundled_logo()


@pytest.fixture()
def mock_orchestrator() -> AsyncMock:
    orch = AsyncMock(spec=Orchestrator)
    orch.consult.return_value = ConsultLoreResponse(answer="the answer")
    return orch


@pytest.fixture()
def wired_server(settings: LoreSettings, mock_orchestrator: AsyncMock) -> FastMCP[Orchestrator]:
    return create_server(settings=settings, system=partial(_noop_system, mock_orchestrator))


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
    with patch("lore.adapter.middleware.get_access_token", return_value=fake_token):
        await _call_tool(wired_server, "consult", {"question": "who am I?"})
    call_args = mock_orchestrator.consult.call_args
    oracle_id = call_args.kwargs["oracle_id"]
    assert oracle_id == "oracle-42"


async def test_oracle_identity_does_not_outlive_its_tool_call(
    wired_server: FastMCP[Orchestrator],
) -> None:
    """The identity stash is request-scoped: a later request in the same
    session must not see the previous call's identity. The probe is a
    resource read, which no tool middleware guards. fastmcp's session-store
    default (24h TTL) would leak the identity here; this pins the
    ``serializable=False`` scoping contract across fastmcp upgrades.
    """

    async def probe_oracle_id() -> str:
        return repr(await get_context().get_state("oracle_id"))

    wired_server.resource("state://oracle-id")(probe_oracle_id)

    fake_token = MagicMock()
    fake_token.claims = {"sub": "oracle-42"}
    async with Client(wired_server) as client:
        with patch("lore.adapter.middleware.get_access_token", return_value=fake_token):
            await client.call_tool("consult", {"question": "who am I?"})
        contents = await client.read_resource("state://oracle-id")

    assert isinstance(contents, list)
    [content] = contents
    assert isinstance(content, TextResourceContents)
    assert content.text == "None"


async def test_correlation_id_distinct_per_consult(
    wired_server: FastMCP[Orchestrator], mock_orchestrator: AsyncMock
) -> None:
    """Two consult calls must produce distinct correlation_ids.

    Either path delivers this: FastMCP starts a fresh tool-call trace per
    invocation, and the no-SDK uuid4 fallback also produces distinct hex
    strings. MCP's session-scoped monotonic request_id is never used as
    the correlation_id.
    """
    async with Client(wired_server) as client:
        await client.call_tool("consult", {"question": "first"})
        await client.call_tool("consult", {"question": "second"})

    first, second = mock_orchestrator.consult.call_args_list
    assert first.kwargs["correlation_id"] != second.kwargs["correlation_id"]


async def test_correlation_id_uses_trace_id_when_otel_active(
    wired_server: FastMCP[Orchestrator], mock_orchestrator: AsyncMock
) -> None:
    """With a valid OTel span context, correlation_id is the active trace_id.

    One identifier across APM trace lookup and ledger PK, never client-facing:
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


async def test_consult_masks_internal_errors(
    wired_server: FastMCP[Orchestrator], mock_orchestrator: AsyncMock
) -> None:
    """An internal error is masked on the wire: no detail, no exception class.

    ``mask_error_details=True`` collapses every unhandled exception to fastmcp's
    uniform message, so one test pins the posture for all domain error classes
    (StorageError, InferenceError, ArchivistResolutionError all walk the same
    branch). The DSN is the most adversarial payload: neither it nor the class
    name may reach the client.
    """
    mock_orchestrator.consult.side_effect = StorageError("dsn=postgresql://user:pass@host/db")
    with pytest.raises(ToolError) as exc_info:
        await _call_tool(wired_server, "consult", {"question": "test"})
    payload = str(exc_info.value)
    assert "postgresql://user:pass@host/db" not in payload
    assert "StorageError" not in payload


async def test_consult_auth_failure_is_constant_message(
    wired_server: FastMCP[Orchestrator],
) -> None:
    """A token without a usable ``sub`` fails with one constant message.

    The wire message is a constant, so nothing token-derived can leak
    through its shape.
    """
    fake_token = MagicMock()
    fake_token.claims = {"aud": "some-audience"}
    with (
        patch("lore.adapter.middleware.get_access_token", return_value=fake_token),
        pytest.raises(ToolError) as exc_info,
    ):
        await _call_tool(wired_server, "consult", {"question": "who am I?"})
    assert str(exc_info.value) == "authentication failed: access token has no usable 'sub' claim"


@pytest.mark.parametrize(
    ("arguments", "rule"),
    [
        ({}, "consult requires a question, a hypothesis, or both"),
        ({"hypothesis": "a claim"}, "consult with a hypothesis also requires a confidence scalar"),
        (
            {"question": "what?", "confidence": 0.5},
            "consult with a confidence scalar also requires a hypothesis",
        ),
    ],
)
async def test_consult_rejects_invalid_input_with_the_violated_rule(
    wired_server: FastMCP[Orchestrator],
    mock_orchestrator: AsyncMock,
    arguments: dict[str, object],
    rule: str,
) -> None:
    """A cross-field rule violation puts exactly the violated rule on the wire.

    The IDEA.md contract sentences, verbatim from the domain validator: specific
    enough for the Scribe to self-correct (fastmcp's posture for client-fault
    input), constant so nothing of the payload echoes back. Exact equality is
    the no-echo guarantee: pydantic's ``str()`` would carry ``input_value=``.
    The orchestrator is never reached.
    """
    with pytest.raises(ToolError) as exc_info:
        await _call_tool(wired_server, "consult", arguments)
    assert str(exc_info.value) == rule
    mock_orchestrator.consult.assert_not_called()


async def test_consult_diagnostic_survives_in_fastmcp_log(
    wired_server: FastMCP[Orchestrator],
    mock_orchestrator: AsyncMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Masked on the wire, the cause survives in fastmcp's native log.

    On the masked path fastmcp calls ``logger.exception`` before scrubbing,
    so operators keep the full diagnostic under ``fastmcp.server.server``
    with no middleware in between.
    """
    mock_orchestrator.consult.side_effect = StorageError("dsn=postgresql://user:pass@host/db")
    with (
        caplog.at_level(logging.ERROR, logger="fastmcp.server.server"),
        pytest.raises(ToolError) as exc_info,
    ):
        await _call_tool(wired_server, "consult", {"question": "test"})
    assert "dsn=postgresql://user:pass@host/db" not in str(exc_info.value)
    native_records = [
        r for r in caplog.records if r.name == "fastmcp.server.server" and r.exc_info is not None
    ]
    assert len(native_records) == 1
    logged_exc_info = native_records[0].exc_info
    assert logged_exc_info is not None
    _, cause, _ = logged_exc_info
    assert isinstance(cause, StorageError)
    assert "dsn=postgresql://user:pass@host/db" in str(cause)


def test_server_with_oidc_configures_auth(settings: LoreSettings) -> None:
    sentinel = MagicMock()
    with patch("lore.adapter.mcp._build_auth", return_value=sentinel):
        oidc_server = create_server(settings=settings, system=_noop_system)
    assert oidc_server.auth is sentinel


def test_server_without_oidc_has_no_auth(server: FastMCP[Orchestrator]) -> None:
    assert server.auth is None


def test_build_auth_returns_none_without_oidc(settings: LoreSettings) -> None:
    from lore.adapter.mcp import _build_auth  # pyright: ignore[reportPrivateUsage]

    assert _build_auth(settings) is None


def test_build_auth_returns_none_without_base_url(settings: LoreSettings) -> None:
    from lore.adapter import OidcConfig
    from lore.adapter.mcp import _build_auth  # pyright: ignore[reportPrivateUsage]

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


def test_build_auth_forwards_oidc_credentials_to_proxy(settings: LoreSettings) -> None:
    """OidcConfig + base_url flow into OIDCProxy as the four core construction kwargs.

    Asserts kwargs individually so future kwarg additions don't drift this test
    structurally. Per-kwarg tests below cover the trust-grading additions
    (`required_scopes`, `verify_id_token`, `extra_authorize_params`).
    """
    from lore.adapter import OidcConfig
    from lore.adapter.mcp import _build_auth  # pyright: ignore[reportPrivateUsage]

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
    kwargs = mock_proxy.call_args.kwargs
    assert kwargs["config_url"] == "https://auth.example.com/.well-known/openid-configuration"
    assert kwargs["client_id"] == "test-client"
    assert kwargs["client_secret"] == "test-secret"
    assert kwargs["base_url"] == "https://lore.example.com"
    assert result is mock_proxy.return_value


def test_build_auth_passes_openid_required_scope(settings: LoreSettings) -> None:
    """openid is hardcoded at the OIDCProxy boundary: the minimum OIDC guarantees an id_token."""
    from lore.adapter import OidcConfig
    from lore.adapter.mcp import _build_auth  # pyright: ignore[reportPrivateUsage]

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
        _build_auth(oidc_settings)
    assert mock_proxy.call_args.kwargs["required_scopes"] == ["openid"]


@pytest.mark.parametrize("verify", [True, False])
def test_build_auth_forwards_verify_id_token_from_auth_section(
    settings: LoreSettings, *, verify: bool
) -> None:
    from lore.adapter import OidcConfig
    from lore.adapter.mcp import _build_auth  # pyright: ignore[reportPrivateUsage]

    oidc_settings = settings.model_copy(
        update={
            "oidc": OidcConfig(
                discovery_url="https://auth.example.com/.well-known/openid-configuration",
                client_id="test-client",
                client_secret=SecretStr("test-secret"),
            ),
            "base_url": "https://lore.example.com",
            "auth": settings.auth.model_copy(update={"verify_id_token": verify}),
        }
    )
    with patch("lore.adapter.mcp.OIDCProxy") as mock_proxy:
        _build_auth(oidc_settings)
    assert mock_proxy.call_args.kwargs["verify_id_token"] is verify


def test_build_auth_forwards_extra_authorize_params_from_settings(
    settings: LoreSettings,
) -> None:
    """Non-empty extra_authorize_params flows through verbatim (e.g. Google's hd=)."""
    from lore.adapter import OidcConfig
    from lore.adapter.mcp import _build_auth  # pyright: ignore[reportPrivateUsage]

    oidc_settings = settings.model_copy(
        update={
            "oidc": OidcConfig(
                discovery_url="https://auth.example.com/.well-known/openid-configuration",
                client_id="test-client",
                client_secret=SecretStr("test-secret"),
                extra_authorize_params={"hd": "example.com"},
            ),
            "base_url": "https://lore.example.com",
        }
    )
    with patch("lore.adapter.mcp.OIDCProxy") as mock_proxy:
        _build_auth(oidc_settings)
    assert mock_proxy.call_args.kwargs["extra_authorize_params"] == {"hd": "example.com"}


async def test_token_with_non_string_sub_claim_raises_tool_error(
    wired_server: FastMCP[Orchestrator],
) -> None:
    """The second trigger of the type-boundary check (the first, a missing
    ``sub``, is pinned by ``test_consult_auth_failure_is_constant_message``).
    """
    fake_token = MagicMock()
    fake_token.claims = {"sub": 12345}
    with (
        patch("lore.adapter.middleware.get_access_token", return_value=fake_token),
        pytest.raises(ToolError, match="authentication failed"),
    ):
        await _call_tool(wired_server, "consult", {"question": "who am I?"})


async def test_confidence_out_of_range_rejected_before_orchestrator(
    wired_server: FastMCP[Orchestrator], mock_orchestrator: AsyncMock
) -> None:
    """The signature's ``Field(ge=-1, le=1)`` is enforced before the tool body runs.

    The rejection mechanics and wording are fastmcp's and pydantic's; ours is
    only the constraint on the signature, so the assertion stops at "rejected,
    orchestrator never reached". fastmcp echoes the client's own out-of-range
    input back as client-fault guidance: its designed behavior, not a leak,
    and deliberately not asserted here.
    """
    with pytest.raises(ToolError):
        await _call_tool(wired_server, "consult", {"question": "test", "confidence": 1.5})
    mock_orchestrator.consult.assert_not_called()


async def test_question_exceeds_max_length_rejected_before_orchestrator(
    wired_server: FastMCP[Orchestrator], mock_orchestrator: AsyncMock
) -> None:
    # limits.question is the configured max. Exceed it by a wide margin; the
    # enforcement and wording are fastmcp's, ours is the max_length wiring.
    # fastmcp echoes the client's own oversized input as client-fault guidance:
    # designed behavior, not a leak, deliberately not asserted.
    with pytest.raises(ToolError):
        await _call_tool(wired_server, "consult", {"question": "x" * 1_000_000})
    mock_orchestrator.consult.assert_not_called()


async def test_question_only_reaches_orchestrator(
    wired_server: FastMCP[Orchestrator], mock_orchestrator: AsyncMock
) -> None:
    await _call_tool(wired_server, "consult", {"question": "what?"})
    mock_orchestrator.consult.assert_called_once()


async def test_hypothesis_with_zero_confidence_reaches_orchestrator(
    wired_server: FastMCP[Orchestrator], mock_orchestrator: AsyncMock
) -> None:
    """confidence=0.0 is the genuine vacuous state: accepted, not absent."""
    await _call_tool(wired_server, "consult", {"hypothesis": "a claim", "confidence": 0.0})
    mock_orchestrator.consult.assert_called_once()
    request = mock_orchestrator.consult.call_args.kwargs["request"]
    assert request.confidence == 0.0
    assert request.hypothesis == "a claim"


@pytest.mark.parametrize("sub", ["_local", "_transfer"])
async def test_synthetic_namespace_sub_rejected(
    wired_server: FastMCP[Orchestrator], mock_orchestrator: AsyncMock, sub: str
) -> None:
    """An IdP-issued ``_*`` sub is refused before the orchestrator runs.

    The synthetic namespace is reserved: a token's ``_local`` would silently
    merge with the unauthenticated local oracle, and ``_transfer`` would write
    full-credibility attestations. The identity middleware closes both at the
    boundary; the Recorder's ``_transfer`` guard stays as defense-in-depth.
    """
    fake_token = MagicMock()
    fake_token.claims = {"sub": sub}
    with (
        patch("lore.adapter.middleware.get_access_token", return_value=fake_token),
        pytest.raises(ToolError) as exc_info,
    ):
        await _call_tool(wired_server, "consult", {"question": "who am I?"})
    assert str(exc_info.value) == "authentication failed: access token has no usable 'sub' claim"
    mock_orchestrator.consult.assert_not_called()


async def test_empty_sub_rejected(
    wired_server: FastMCP[Orchestrator], mock_orchestrator: AsyncMock
) -> None:
    """An empty ``sub`` is refused with the same constant message, never a
    masked 500 downstream."""
    fake_token = MagicMock()
    fake_token.claims = {"sub": ""}
    with (
        patch("lore.adapter.middleware.get_access_token", return_value=fake_token),
        pytest.raises(ToolError) as exc_info,
    ):
        await _call_tool(wired_server, "consult", {"question": "who am I?"})
    assert str(exc_info.value) == "authentication failed: access token has no usable 'sub' claim"
    mock_orchestrator.consult.assert_not_called()


async def test_consult_without_identity_state_fails_masked(
    settings: LoreSettings, mock_orchestrator: AsyncMock
) -> None:
    """A consult with no resolved identity is an internal bug: masked, no write.

    Simulates a mis-wired composition by swapping the identity middleware for
    the inert base ``Middleware``, so no ``oracle_id`` lands in request state.
    The tool-side narrow must fail before the orchestrator, and the wire must
    carry only fastmcp's masked message, never the internal detail.
    """
    from fastmcp.server.middleware import Middleware

    with patch("lore.adapter.mcp.OracleIdentityMiddleware", return_value=Middleware()):
        srv = create_server(settings=settings, system=partial(_noop_system, mock_orchestrator))
    with pytest.raises(ToolError) as exc_info:
        await _call_tool(srv, "consult", {"question": "who am I?"})
    assert "oracle identity missing" not in str(exc_info.value)
    mock_orchestrator.consult.assert_not_called()


async def _list_tools(server: FastMCP[Orchestrator]) -> Sequence[Tool]:
    return await server.list_tools()


async def _call_tool(server: FastMCP[Orchestrator], name: str, arguments: dict[str, object]):
    """Drive one tool call through the in-memory client.

    Each call opens a fresh client session, entering the real server
    lifespan; the server mints a fresh system scope per cycle, so calling
    this helper repeatedly against one server is fine.
    """
    async with Client(server) as client:
        return await client.call_tool(name, arguments)
