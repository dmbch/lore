"""FastMCP adapter: tool registration and lifespan."""

import importlib.resources
from base64 import b64encode
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import TYPE_CHECKING, Annotated, cast
from uuid import uuid4

import structlog
from fastmcp import Context, FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.auth.oidc_proxy import OIDCProxy
from fastmcp.server.middleware.error_handling import ErrorHandlingMiddleware
from mcp.types import Icon
from opentelemetry import trace as otel_trace
from pydantic import Field, ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from lore.adapter.middleware import OracleIdentityMiddleware
from lore.domain import ConsultLoreRequest, ConsultLoreResponse
from lore.domain.errors import StorageError
from lore.orchestrator import Orchestrator
from lore.prompts import load_prompt

if TYPE_CHECKING:
    from lore.config import LoreSettings

log = structlog.get_logger(__name__)

_TOOL_NAME = "consult"


# Parameter descriptions: MCP tool schema guidance for the client LLM.
_PARAM_DESCRIPTIONS = {
    "question": (
        "What do you want to know? Searches the shared knowledge base."
        " Can be combined with a hypothesis to both ask and contribute."
    ),
    "context": (
        "Why are you asking: the problem being solved, the decision being faced."
        " Improves retrieval and resolution quality."
    ),
    "hypothesis": (
        "A claim to contribute."
        " Requires a confidence scalar. Lore classifies its relationship"
        " to existing knowledge."
    ),
    "reasoning": ("The logical chain behind the hypothesis. Strengthens resolution quality."),
    "confidence": (
        "Directional confidence in [-1, 1]. Positive = belief,"
        " negative = disbelief, 0 = genuine uncertainty. Rough calibration:"
        " 0.9 certain, 0.6 fairly sure, 0.3 suspect, 0 no idea,"
        " -0.5 doubt, -0.8 strongly disbelieve. Err toward center."
        " Required when hypothesis is present; omit when the user has no view."
    ),
}


def _bundled_logo() -> str:
    """Encode the bundled Lore logo as a data URI for the OIDC consent screen."""
    data = importlib.resources.files("lore.adapter.assets").joinpath("logo.png").read_bytes()
    return "data:image/png;base64," + b64encode(data).decode("ascii")


def _build_auth(settings: LoreSettings) -> OIDCProxy | None:
    if settings.oidc is None or settings.base_url is None:
        return None
    return OIDCProxy(
        config_url=settings.oidc.discovery_url,
        client_id=settings.oidc.client_id,
        # Unwrap the SecretStr exactly once, at the OIDC client boundary.
        # Every other code path sees the masked repr.
        client_secret=settings.oidc.client_secret.get_secret_value(),
        base_url=settings.base_url,
        # Hardcoded: 'sub' is only guaranteed when 'openid' is requested.
        required_scopes=["openid"],
        verify_id_token=settings.auth.verify_id_token,
        # Forwarded verbatim from OIDC_URL query (e.g. Google's `hd=` workspace restriction).
        extra_authorize_params=settings.oidc.extra_authorize_params,
    )


def create_server(
    *,
    settings: LoreSettings,
    system: Callable[[], AbstractAsyncContextManager[Orchestrator]],
    health_probe: Callable[[], Awaitable[None]] | None = None,
) -> FastMCP[Orchestrator]:
    """Build a FastMCP server wired to a Lore system via lifespan.

    ``system`` is a factory, not a CM instance: FastMCP's lifespan is
    ref-counted and re-enterable (the in-memory transport cycles it per
    client session), so each cycle must open a fresh system scope. A
    single-use ``@asynccontextmanager`` product would die on the second
    cycle with ``RuntimeError("generator didn't yield")``.

    ``health_probe`` is the readiness probe the ``/ready`` route awaits.
    The composition root composes it (``repositories.make_probe(pool)``)
    so the adapter never imports the repository layer directly. When
    ``None``, ``/ready`` fails closed with 503: a composition that
    forgot the probe must not vouch for readiness.
    """

    @asynccontextmanager
    async def lifespan(_server: FastMCP[Orchestrator]) -> AsyncGenerator[Orchestrator]:
        async with system() as orchestrator:
            yield orchestrator

    instructions = load_prompt(settings.prompts.scribe)
    auth = _build_auth(settings)
    server: FastMCP[Orchestrator] = FastMCP(
        name=settings.server.name,
        version=settings.version,
        instructions=instructions,
        icons=[Icon(src=settings.server.icon_url or _bundled_logo())],
        lifespan=lifespan,
        auth=auth,
        # Hardcoded posture, deliberately overriding the FASTMCP_* env default:
        # every unhandled exception scrubs to a uniform client message. Pinned by
        # the leak test.
        mask_error_details=True,
    )

    _register_tools(server=server, settings=settings)
    _register_healthchecks(server=server, health_probe=health_probe)

    # transform_errors=False is load-bearing: True would rewrap tool errors as
    # protocol McpError and change tool-error semantics. include_traceback=True
    # preserves the __cause__ chain in the fastmcp.errors log, the only log-side
    # record on the ToolError path (fastmcp logs ToolError with exc_info=False).
    server.add_middleware(ErrorHandlingMiddleware(transform_errors=False, include_traceback=True))

    # Identity is cross-cutting: every tool call resolves its oracle here,
    # never in a tool body.
    server.add_middleware(OracleIdentityMiddleware())

    return server


def _register_healthchecks(
    *,
    server: FastMCP[Orchestrator],
    health_probe: Callable[[], Awaitable[None]] | None,
) -> None:
    """Register ``/health`` (liveness) and ``/ready`` (readiness) routes.

    ``/health`` is a no-op 200: the load balancer learns the process is
    responsive. ``/ready`` awaits the injected probe when present;
    ``StorageError`` becomes 503 with a scrubbed body, and any other
    exception collapses to the same scrubbed 503 (full exception + stack
    in structlog under ``ready.error.internal``). When the probe is
    ``None``, ``/ready`` fails closed: 503 "unconfigured", so a
    mis-wired composition is loudly visible instead of ready forever.
    """

    # FastMCP's @custom_route signature is library-imposed: the handler
    # receives a positional Starlette Request. Kw-only does not apply.
    @server.custom_route("/health", methods=["GET"])
    async def health(_request: Request) -> Response:
        return JSONResponse({"status": "ok"})

    @server.custom_route("/ready", methods=["GET"])
    async def ready(_request: Request) -> Response:
        if health_probe is None:
            return JSONResponse({"status": "unconfigured"}, status_code=503)
        try:
            await health_probe()
        except StorageError as exc:
            # Log under structlog so operators can diagnose; wire stays scrubbed.
            log.warning("ready.unavailable", error_message=str(exc))
            return JSONResponse({"status": "unavailable"}, status_code=503)
        except Exception as exc:
            # Anything unexpected: bug in the probe closure, vendor SDK leak,
            # asyncio internals: collapses to the same scrubbed 503 so the
            # wire posture stays uniform. Full diagnostics live in the log.
            log.error(
                "ready.error.internal",
                error_class=type(exc).__name__,
                error_message=str(exc),
                exc_info=True,
            )
            return JSONResponse({"status": "unavailable"}, status_code=503)
        return JSONResponse({"status": "ok"})

    # FastMCP registers via decorator side effect; silence pyright "unused".
    _ = health
    _ = ready


def _register_tools(*, server: FastMCP[Orchestrator], settings: LoreSettings) -> None:
    limits = settings.limits
    tool_description = load_prompt(settings.prompts.consult)

    @server.tool(name=_TOOL_NAME, description=tool_description)
    async def consult(
        ctx: Context,
        question: Annotated[
            str | None,
            Field(max_length=limits.question, description=_PARAM_DESCRIPTIONS["question"]),
        ] = None,
        context: Annotated[
            str | None,
            Field(max_length=limits.context, description=_PARAM_DESCRIPTIONS["context"]),
        ] = None,
        hypothesis: Annotated[
            str | None,
            Field(max_length=limits.hypothesis, description=_PARAM_DESCRIPTIONS["hypothesis"]),
        ] = None,
        reasoning: Annotated[
            str | None,
            Field(max_length=limits.reasoning, description=_PARAM_DESCRIPTIONS["reasoning"]),
        ] = None,
        confidence: Annotated[
            float | None,
            Field(ge=-1, le=1, description=_PARAM_DESCRIPTIONS["confidence"]),
        ] = None,
    ) -> ConsultLoreResponse:
        # trace_id of the active span gives one ID across the APM trace and the
        # ledger row: the same value the structlog processor already injects into
        # every log event, so we add no extra log-line bytes. Ledger + trace
        # identity, no longer client-facing. Bare runs (no SDK TracerProvider)
        # produce a non-recording span with INVALID context; fall back to a uuid4
        # hex so the PK is still well-formed.
        span_ctx = otel_trace.get_current_span().get_span_context()
        correlation_id = f"{span_ctx.trace_id:032x}" if span_ctx.is_valid else uuid4().hex
        try:
            request = ConsultLoreRequest(
                question=question,
                context=context,
                hypothesis=hypothesis,
                reasoning=reasoning,
                confidence=confidence,
            )
        except ValidationError as exc:
            # Cross-field rules cannot live in the flat tool signature, so they
            # surface here. The wire message is exactly the violated rule: a
            # constant the domain validator wrote for the Scribe. Never str(exc):
            # pydantic's repr would echo the client's payload back.
            rules = "; ".join(err["msg"].removeprefix("Value error, ") for err in exc.errors())
            raise ToolError(rules) from exc

        # Stashed by OracleIdentityMiddleware on every tool call. get_state is
        # library-typed Any, so narrow honestly at the boundary. A miss means
        # the middleware is not registered: an internal bug, and the
        # RuntimeError takes fastmcp's masked arm.
        oracle_id = await ctx.get_state("oracle_id")
        if not isinstance(oracle_id, str):
            msg = "oracle identity missing from request state"
            raise RuntimeError(msg)

        # FastMCP types lifespan_context as dict[str, Any] but returns the
        # lifespan-yielded value directly: our Orchestrator instance. Internal
        # ValidationErrors are the orchestrator's to wrap (DomainInvariantError),
        # so nothing raw-pydantic can reach fastmcp's echo arm from here.
        orchestrator = cast(Orchestrator, ctx.lifespan_context)
        return await orchestrator.consult(
            oracle_id=oracle_id, request=request, correlation_id=correlation_id
        )

    # Keep a reference so pyright doesn't flag as unused.
    _ = consult
