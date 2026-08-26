"""Observatory backend-tool tests: the app-scoped ``frontier`` tool.

Two contracts:
- ``frontier`` is a backend tool: the UI calls it, the model never sees it in
  ``list_tools``. Registration is real; visibility is app-only.
- Tool bodies carry no error handling of their own: an unhandled exception
  rides to fastmcp's dispatch, where ``mask_error_details=True`` collapses it
  to the uniform client message and logs the full diagnostic natively.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from prefab_ui.components import DataTable

from lore.adapter._observatory import build_observatory
from lore.adapter._observatory.tools import (
    _frontier,  # pyright: ignore[reportPrivateUsage]
    _observe,  # pyright: ignore[reportPrivateUsage]
)
from lore.domain import FrontierEntry, StorageError
from lore.orchestrator import Orchestrator

# Stand-in for the contract's observe blurb: injected into build_observatory the
# way create_server threads contract.tools.observe.description at runtime.
_DESCRIPTION = "Show the herd's uncertainty frontier: what to explore next."


def _entry(content: str = "a claim") -> FrontierEntry:
    return FrontierEntry(
        id="aaa00001-e29b-41d4-a716-446655440000",
        content=content,
        c_herd=0.0,
        uncertainty=1.0,
        oracle_count=0,
        last_attested=None,
    )


def _ctx_with(orchestrator: object) -> MagicMock:
    ctx = MagicMock()
    ctx.lifespan_context = orchestrator
    return ctx


async def test_frontier_tool_is_registered_but_not_model_visible() -> None:
    app = build_observatory(description=_DESCRIPTION)
    server: FastMCP[object] = FastMCP("test")
    server.add_provider(app)

    model_tools = [t.name for t in await server.list_tools()]
    app_tools = [t.name for t in await app._list_tools()]  # pyright: ignore[reportPrivateUsage]

    assert "frontier" not in model_tools
    assert "frontier" in app_tools


async def test_frontier_tool_returns_entries() -> None:
    orchestrator = AsyncMock(spec=Orchestrator)
    orchestrator.frontier.return_value = [_entry("first"), _entry("second")]

    result = await _frontier(_ctx_with(orchestrator))

    orchestrator.frontier.assert_awaited_once()
    assert [e.content for e in result] == ["first", "second"]


async def test_frontier_tool_propagates_internal_errors() -> None:
    """No tool-side scrub: the raw error rides to fastmcp's masking layer."""
    orchestrator = AsyncMock(spec=Orchestrator)
    orchestrator.frontier.side_effect = StorageError("disk full at /var/lib/postgres")

    with pytest.raises(StorageError):
        await _frontier(_ctx_with(orchestrator))


async def test_observe_masks_internal_errors_on_the_wire() -> None:
    """Dispatch through a masking server scrubs the payload, fastmcp-style.

    ``create_server`` pins ``mask_error_details=True``; this test mirrors that
    posture to pin what a client sees when the orchestrator fails: fastmcp's
    uniform message, never the DSN or the exception class.
    """
    orchestrator = AsyncMock(spec=Orchestrator)
    orchestrator.frontier.side_effect = StorageError("dsn=postgresql://user:pass@host/db")
    server: FastMCP[object] = FastMCP("test", mask_error_details=True)
    server.add_provider(build_observatory(description=_DESCRIPTION))
    server._lifespan_result = orchestrator  # pyright: ignore[reportPrivateUsage]
    server._lifespan_result_set = True  # pyright: ignore[reportPrivateUsage]

    with pytest.raises(ToolError) as exc_info:
        await server.call_tool("observe", {})

    payload = str(exc_info.value)
    assert "postgresql://user:pass@host/db" not in payload
    assert "StorageError" not in payload


def _observe_ctx(*, entries: list[FrontierEntry]) -> MagicMock:
    orchestrator = AsyncMock(spec=Orchestrator)
    orchestrator.frontier.return_value = entries
    ctx = MagicMock()
    ctx.lifespan_context = orchestrator
    return ctx


async def test_observe_tool_is_model_visible() -> None:
    server: FastMCP[object] = FastMCP("test")
    server.add_provider(build_observatory(description=_DESCRIPTION))

    names = [t.name for t in await server.list_tools()]

    assert "observe" in names


async def test_observe_tool_description_addresses_the_model() -> None:
    """The Scribe reads a curated when-to-call description, not the docstring."""
    server: FastMCP[object] = FastMCP("test")
    server.add_provider(build_observatory(description=_DESCRIPTION))

    observe = next(t for t in await server.list_tools() if t.name == "observe")

    assert "uncertainty frontier" in (observe.description or "")
    assert "Model-visible entry point" not in (observe.description or "")


async def test_observe_returns_datatable_component() -> None:
    """Unconditional: the idiomatic ``@app.ui`` shape, no capability gate."""
    ctx = _observe_ctx(entries=[_entry("h")])

    result = await _observe(ctx)

    assert isinstance(result, DataTable)


async def test_observe_call_tool_carries_prefab_structured_content() -> None:
    """End-to-end dispatch: the wire carries the prefab envelope with the
    DataTable and the entry content, ready for a host's renderer iframe."""
    orchestrator = AsyncMock(spec=Orchestrator)
    orchestrator.frontier.return_value = [_entry("desktop claim")]
    server: FastMCP[object] = FastMCP("test")
    server.add_provider(build_observatory(description=_DESCRIPTION))
    server._lifespan_result = orchestrator  # pyright: ignore[reportPrivateUsage]
    server._lifespan_result_set = True  # pyright: ignore[reportPrivateUsage]

    result = await server.call_tool("observe", {})

    structured = result.structured_content
    assert structured is not None
    assert "$prefab" in structured
    payload = str(structured)
    assert "DataTable" in payload
    assert "desktop claim" in payload
