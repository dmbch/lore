"""Observatory tools: the app-scoped backend behind the ``observe`` entry point.

``frontier`` is a backend tool (``visibility=["app"]``): the UI calls it via
CallTool, the model never sees it. Error posture is fastmcp's own: the server
runs with ``mask_error_details=True``, so an unhandled exception is logged in
full natively and reaches the client as the uniform masked message. No auth
reads here: a read path consumes no oracle identity.

Presentation stays out of this file. ``render.py`` owns the prefab Component,
keeping the tool thin and orchestrator-facing.
"""

from typing import cast

from fastmcp import Context
from fastmcp.apps import FastMCPApp
from prefab_ui.components.base import Component

from lore.adapter.observatory.render import render_frontier
from lore.domain import FrontierEntry
from lore.orchestrator import Orchestrator

_APP_NAME = "Observatory"


async def _frontier(ctx: Context) -> list[FrontierEntry]:
    """Return the current uncertainty frontier. App-scoped: the UI calls this."""
    orchestrator = cast(Orchestrator, ctx.lifespan_context)
    return await orchestrator.frontier()


async def _observe(ctx: Context) -> Component:
    """Model-visible entry point: the frontier as a prefab DataTable.

    Returned unconditionally, the idiomatic ``@app.ui`` shape: UI-capable
    hosts render it; text-only clients see fastmcp's placeholder block.
    """
    entries = await _frontier(ctx)
    return render_frontier(entries)


def build_observatory(*, description: str) -> FastMCPApp:
    """Build the Observatory app: the ``observe`` entry point over app-scoped tools.

    ``description`` is the model-facing ``observe`` blurb, sourced from the MCP
    contract so all client-facing prose has one home.
    """
    app = FastMCPApp(_APP_NAME)
    app.tool(name="frontier")(_frontier)
    app.ui(name="observe", description=description)(_observe)
    return app
