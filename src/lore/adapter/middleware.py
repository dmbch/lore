"""Oracle identity resolution for every MCP tool call."""

import mcp.types as mt
from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_access_token
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools import ToolResult

from lore.domain import LOCAL_ORACLE

# One constant for every rejection: nothing token-derived can leak through
# the message shape.
_AUTH_FAILED = "authentication failed: access token has no usable 'sub' claim"


class OracleIdentityMiddleware(Middleware):
    """Resolve the calling oracle's identity before any tool body runs.

    A token's ``sub`` claim becomes the ``oracle_id``; no token is the
    trusted local fallback (stdio mode). Missing, mistyped, empty, and
    synthetic-namespace (``_*``) subs are rejected: an IdP-issued
    ``_local`` would silently merge with the unauthenticated local oracle,
    and ``_transfer`` would write full-credibility attestations. The
    resolved identity travels to the tool via request state.
    """

    # Library-imposed hook signature: positional context and call_next.
    async def on_call_tool(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        call_next: CallNext[mt.CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        token = get_access_token()
        if token is None:
            oracle_id = LOCAL_ORACLE
        else:
            sub = token.claims.get("sub")
            if not isinstance(sub, str) or not sub or sub.startswith("_"):
                raise ToolError(_AUTH_FAILED)
            oracle_id = sub
        # fastmcp types fastmcp_context as Context | None. With no Context
        # there is nowhere to stash; proceed and let the tool-side narrow
        # fail as a masked internal error.
        if context.fastmcp_context is not None:
            await context.fastmcp_context.set_state("oracle_id", oracle_id)
        return await call_next(context)
