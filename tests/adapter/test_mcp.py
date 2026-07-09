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
from fastmcp.tools import Tool
from pydantic import SecretStr

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
    """Load settings with the complete TOML fixture."""
    return load_settings(toml_path=_COMPLETE_TOML)


@pytest.fixture()
def server(settings: LoreSettings) -> FastMCP[Orchestrator]:
    """Return a FastMCP server built from settings."""
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


def test_mcp_instructions_are_scribe_only(
    tmp_path: Path,
    settings: LoreSettings,
    server: FastMCP[Orchestrator],
) -> None:
    assert server.instructions == load_prompt(settings.prompts.scribe)

    narrative = tmp_path / "narrative.md"
    narrative.write_text("DOMAIN NARRATIVE MUST NOT LEAK.")
    prompts = settings.prompts.model_copy(update={"narrative": narrative})
    leaky = settings.model_copy(update={"prompts": prompts})
    srv = create_server(settings=leaky, system=_noop_system)
    instructions = srv.instructions
    assert instructions is not None
    assert "DOMAIN NARRATIVE MUST NOT LEAK." not in instructions
    assert instructions == load_prompt(leaky.prompts.scribe)


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
    """An AsyncMock standing in for Orchestrator.consult."""
    orch = AsyncMock(spec=Orchestrator)
    orch.consult.return_value = ConsultLoreResponse(answer="the answer")
    return orch


@pytest.fixture()
def wired_server(settings: LoreSettings, mock_orchestrator: AsyncMock) -> FastMCP[Orchestrator]:
    """Server whose lifespan yields the mock orchestrator."""
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
    async with Client(wired_server) as client:
        await client.call_tool("consult", {"question": "first"})
        await client.call_tool("consult", {"question": "second"})

    first, second = mock_orchestrator.consult.call_args_list
    assert first.kwargs["correlation_id"] != second.kwargs["correlation_id"]


async def test_correlation_id_uses_trace_id_when_otel_active(
    wired_server: FastMCP[Orchestrator], mock_orchestrator: AsyncMock
) -> None:
    """With a valid OTel span context, correlation_id is the active trace_id.

    One identifier across client error, APM trace lookup, and ledger PK:
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
        patch("lore.adapter.mcp.get_access_token", return_value=fake_token),
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


async def test_consult_internal_validation_error_not_mislabeled_as_client_input(
    wired_server: FastMCP[Orchestrator], mock_orchestrator: AsyncMock
) -> None:
    """An internal pydantic failure is masked, not blamed on the client's input.

    A ``ValidationError`` raised past request construction (here, from deep in
    the orchestrator) is our bug. The adapter re-raises it as a non-pydantic
    error so masking scrubs it to the uniform message, rather than fastmcp's
    dedicated pydantic arm echoing the failing value and mislabeling it as
    client-fault input.
    """
    from pydantic import BaseModel, ValidationError

    class _InternalModel(BaseModel):
        internal_secret: int

    with pytest.raises(ValidationError) as caught:
        _InternalModel.model_validate({"internal_secret": "leak-me-not"})
    mock_orchestrator.consult.side_effect = caught.value

    with pytest.raises(ToolError) as exc_info:
        await _call_tool(wired_server, "consult", {"question": "test"})
    payload = str(exc_info.value)
    # Masked: nothing of the internal model or its repr crosses the wire.
    assert "_InternalModel" not in payload
    assert "internal_secret" not in payload
    assert "input_value" not in payload
    assert "leak-me-not" not in payload
    # Not mislabeled: the client-fault arm would surface the extracted
    # pydantic message ("Input should be a valid integer ...").
    assert "valid integer" not in payload


async def test_consult_diagnostic_survives_in_fastmcp_errors_log(
    wired_server: FastMCP[Orchestrator],
    mock_orchestrator: AsyncMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Masked on the wire, the cause survives in the ``fastmcp.errors`` log.

    ``ErrorHandlingMiddleware(include_traceback=True)`` is the only log-side
    record of the cause on the ToolError path (fastmcp logs ToolError with
    ``exc_info=False``), so operators keep the diagnostic the client never sees.
    """
    mock_orchestrator.consult.side_effect = StorageError("dsn=postgresql://user:pass@host/db")
    with (
        caplog.at_level(logging.ERROR, logger="fastmcp.errors"),
        pytest.raises(ToolError),
    ):
        await _call_tool(wired_server, "consult", {"question": "test"})
    middleware_records = [r for r in caplog.records if r.name == "fastmcp.errors"]
    assert len(middleware_records) == 1
    assert "dsn=postgresql://user:pass@host/db" in middleware_records[0].getMessage()


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
        patch("lore.adapter.mcp.get_access_token", return_value=fake_token),
        pytest.raises(ToolError, match="authentication failed"),
    ):
        await _call_tool(wired_server, "consult", {"question": "who am I?"})


async def test_confidence_out_of_range_rejected_before_orchestrator(
    wired_server: FastMCP[Orchestrator], mock_orchestrator: AsyncMock
) -> None:
    """The signature's ``Field(ge=-1, le=1)`` is enforced before the tool body runs.

    The rejection mechanics and wording are fastmcp's and pydantic's; ours is
    only the constraint on the signature, so the assertion stops at "rejected,
    orchestrator never reached".
    """
    with pytest.raises(ToolError):
        await _call_tool(wired_server, "consult", {"question": "test", "confidence": 1.5})
    mock_orchestrator.consult.assert_not_called()


async def test_question_exceeds_max_length_rejected_before_orchestrator(
    wired_server: FastMCP[Orchestrator], mock_orchestrator: AsyncMock
) -> None:
    # limits.question is the configured max. Exceed it by a wide margin; the
    # enforcement and wording are fastmcp's, ours is the max_length wiring.
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


async def test_idp_sub_passes_through_verbatim_even_in_synthetic_namespace(
    wired_server: FastMCP[Orchestrator], mock_orchestrator: AsyncMock
) -> None:
    """The IdP is the identity root: whatever string it puts in ``sub`` is the
    oracle_id, ``_*`` names included. The one reserved name (``_transfer``) is
    enforced by the Recorder at the domain layer, not here.
    """
    fake_token = MagicMock()
    fake_token.claims = {"sub": "_local"}
    with patch("lore.adapter.mcp.get_access_token", return_value=fake_token):
        await _call_tool(wired_server, "consult", {"question": "who am I?"})
    assert mock_orchestrator.consult.call_args.kwargs["oracle_id"] == "_local"


async def _list_tools(server: FastMCP[Orchestrator]) -> Sequence[Tool]:
    return await server.list_tools()


async def _call_tool(server: FastMCP[Orchestrator], name: str, arguments: dict[str, object]):
    """Drive one tool call through the in-memory client.

    Each call opens a fresh client session, entering the real server
    lifespan. The system CM behind a server is single-use, so a test that
    makes multiple calls against one server must share a single ``Client``
    session instead of calling this helper twice.
    """
    async with Client(server) as client:
        return await client.call_tool(name, arguments)
