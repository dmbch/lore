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
from key_value.aio.wrappers.encryption.fernet import FernetEncryptionWrapper
from mcp.types import Icon
from opentelemetry import trace as otel_trace
from pydantic import Field, ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from lore.adapter._contract import CONSULT_TOOL, ServerContract, load_server_contract
from lore.adapter.middleware import OracleIdentityMiddleware
from lore.adapter.observatory import build_observatory
from lore.domain import ConsultLoreRequest, ConsultLoreResponse
from lore.domain.errors import StorageError
from lore.orchestrator import Orchestrator
from lore.prompts import load_prompt

if TYPE_CHECKING:
    from key_value.aio.protocols.key_value import AsyncKeyValue

    from lore.adapter.config import OidcConfig
    from lore.config import LoreSettings

log = structlog.get_logger(__name__)


def _bundled_logo() -> str:
    """Encode the bundled Lore logo as a data URI for the OIDC consent screen."""
    data = importlib.resources.files("lore.adapter.assets").joinpath("logo.png").read_bytes()
    return "data:image/png;base64," + b64encode(data).decode("ascii")


def _encrypt_client_storage(store: AsyncKeyValue, *, oidc: OidcConfig) -> AsyncKeyValue:
    """Fernet-wrap the bare store for ``OAuthProxy``'s ``client_storage``.

    The adapter owns the wrapping because it owns the key material: the key
    derives (PBKDF2) from the OIDC client secret, so the secret never leaves
    the module that already unwraps it for ``OIDCProxy``.
    ``raise_on_decryption_error=False`` mirrors fastmcp's own posture: an
    undecryptable row (rotated secret, corrupted blob) is a cache miss
    forcing re-auth, not a crash.
    """
    return FernetEncryptionWrapper(
        key_value=store,
        source_material=oidc.client_secret.get_secret_value(),
        # The client_id as salt: public (salts need no secrecy, only
        # unpredictability before theft), per-deployment (precomputed
        # tables buy nothing), replica-stable, and it rotates only
        # alongside the secret, which already forces re-auth.
        salt=oidc.client_id,
        raise_on_decryption_error=False,
    )


def _build_auth(
    settings: LoreSettings, *, storage: AsyncKeyValue | None = None
) -> OIDCProxy | None:
    if settings.oidc is None or settings.base_url is None:
        return None
    return OIDCProxy(
        config_url=settings.oidc.discovery_url,
        client_id=settings.oidc.client_id,
        # Unwrap the SecretStr at the OIDC client boundary; the encryption
        # helper above reuses it as Fernet key material on the same branch.
        # Every other code path sees the masked repr.
        client_secret=settings.oidc.client_secret.get_secret_value(),
        base_url=settings.base_url,
        # Hardcoded: 'sub' is only guaranteed when 'openid' is requested.
        required_scopes=["openid"],
        verify_id_token=settings.auth.verify_id_token,
        # Forwarded verbatim from OIDC_URL query (e.g. Google's `hd=` workspace restriction).
        extra_authorize_params=settings.oidc.extra_authorize_params,
        # The wrap happens only on this branch: this guard is the one
        # "is OIDC on" decision, so the composition root never mirrors it
        # and no key derivation is spent on a server without OIDC auth.
        # On None, OAuthProxy builds its own local file-store default:
        # the explicit kwarg and an omitted one are the same upstream
        # code path.
        client_storage=(
            _encrypt_client_storage(storage, oidc=settings.oidc) if storage is not None else None
        ),
    )


def create_server(
    *,
    settings: LoreSettings,
    system: Callable[[], AbstractAsyncContextManager[Orchestrator]],
    health_probe: Callable[[], Awaitable[None]] | None = None,
    storage: AsyncKeyValue | None = None,
) -> FastMCP[Orchestrator]:
    """Build a FastMCP server wired to a Lore system via lifespan.

    ``system`` is a factory, not a CM instance: FastMCP's lifespan is
    ref-counted and re-enterable (the in-memory transport cycles it per
    client session), so each cycle must open a fresh system scope. A
    single-use ``@asynccontextmanager`` product would die on the second
    cycle with ``RuntimeError("generator didn't yield")``.

    ``health_probe`` is the readiness probe the ``/ready`` route awaits.
    The composition root composes it (``repositories.make_probe(pool)``)
    so the adapter never imports the repository layer. When ``None``,
    ``/ready`` fails closed with 503: a composition that forgot the
    probe must not vouch for readiness.

    ``storage`` is the durable key-value store behind fastmcp's
    operational state, composed by the composition root under the same
    inversion as ``health_probe``. The adapter assigns it to both fastmcp
    slots, collections keeping the lanes isolated: bare as the
    ``session_state_store`` (``ctx.get_state`` / ``set_state``:
    operational app state, not credentials, serving every topology), and
    Fernet-wrapped into OIDC auth's ``client_storage`` (client
    registrations, upstream tokens), keyed off the adapter's own OIDC
    settings, so no key derivation is spent on a server without auth and
    the composition root never touches the secret. When ``None``,
    fastmcp's defaults apply: process memory for session state, a local
    file store for OAuth state.
    """

    @asynccontextmanager
    async def lifespan(_server: FastMCP[Orchestrator]) -> AsyncGenerator[Orchestrator]:
        async with system() as orchestrator:
            yield orchestrator

    contract = load_server_contract(settings.prompts.contract)
    auth = _build_auth(settings, storage=storage)
    server: FastMCP[Orchestrator] = FastMCP(
        name=settings.server.name,
        version=settings.version,
        instructions=contract.instructions,
        icons=[Icon(src=settings.server.icon_url or _bundled_logo())],
        lifespan=lifespan,
        auth=auth,
        session_state_store=storage,
        # Hardcoded posture, deliberately overriding the FASTMCP_* env default:
        # every unhandled exception scrubs to a uniform client message. Pinned by
        # the leak test.
        mask_error_details=True,
    )

    _register_tools(server=server, settings=settings, contract=contract)
    _register_prompts(server=server, settings=settings)
    server.add_provider(build_observatory(description=contract.tools.observe.description))
    _register_healthchecks(server=server, health_probe=health_probe)

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


def _register_prompts(*, server: FastMCP[Orchestrator], settings: LoreSettings) -> None:
    """Register the ``consult`` MCP prompt: the invocable Scribe persona.

    Served as an MCP prompt so a user can fire it as a slash command
    (``/mcp__lore__consult``), landing the persona as a real conversation
    message: the strongest injection channel, where server ``instructions``
    are the weakest. The prompt and the tool deliberately share the
    client-facing ``consult`` name.
    """
    persona = load_prompt(settings.prompts.scribe)

    @server.prompt(name=CONSULT_TOOL)
    def consult() -> str:
        return persona

    # FastMCP registers via decorator side effect; silence pyright "unused".
    _ = consult


def _register_tools(
    *, server: FastMCP[Orchestrator], settings: LoreSettings, contract: ServerContract
) -> None:
    limits = settings.limits
    tool = contract.tools.consult
    fields = tool.fields

    @server.tool(name=CONSULT_TOOL, description=tool.description)
    async def consult(
        ctx: Context,
        question: Annotated[
            str | None,
            Field(max_length=limits.question, description=fields.question),
        ] = None,
        context: Annotated[
            str | None,
            Field(max_length=limits.context, description=fields.context),
        ] = None,
        hypothesis: Annotated[
            str | None,
            Field(max_length=limits.hypothesis, description=fields.hypothesis),
        ] = None,
        reasoning: Annotated[
            str | None,
            Field(max_length=limits.reasoning, description=fields.reasoning),
        ] = None,
        confidence: Annotated[
            float | None,
            Field(ge=-1, le=1, description=fields.confidence),
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
