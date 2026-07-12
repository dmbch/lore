"""Tests for lore.adapter.middleware: oracle identity resolution."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastmcp.exceptions import ToolError

from lore.adapter.middleware import OracleIdentityMiddleware
from lore.domain import LOCAL_ORACLE

_AUTH_FAILED = "authentication failed: access token has no usable 'sub' claim"


def _context() -> MagicMock:
    """A MiddlewareContext stand-in with an async-state fastmcp Context."""
    context = MagicMock()
    context.fastmcp_context = MagicMock()
    context.fastmcp_context.set_state = AsyncMock()
    return context


def _token(claims: dict[str, object]) -> MagicMock:
    token = MagicMock()
    token.claims = claims
    return token


async def test_token_sub_becomes_oracle_id() -> None:
    context = _context()
    call_next = AsyncMock(return_value="downstream result")
    with patch(
        "lore.adapter.middleware.get_access_token",
        return_value=_token({"sub": "oracle-42"}),
    ):
        result = await OracleIdentityMiddleware().on_call_tool(context, call_next)
    context.fastmcp_context.set_state.assert_awaited_once_with("oracle_id", "oracle-42")
    call_next.assert_awaited_once_with(context)
    assert result == "downstream result"


async def test_no_token_falls_back_to_local_oracle() -> None:
    """Unauthenticated (stdio mode) resolves to the trusted local synthetic."""
    context = _context()
    call_next = AsyncMock()
    with patch("lore.adapter.middleware.get_access_token", return_value=None):
        await OracleIdentityMiddleware().on_call_tool(context, call_next)
    context.fastmcp_context.set_state.assert_awaited_once_with("oracle_id", LOCAL_ORACLE)
    call_next.assert_awaited_once_with(context)


@pytest.mark.parametrize(
    "claims",
    [
        pytest.param({"aud": "some-audience"}, id="missing-sub"),
        pytest.param({"sub": 12345}, id="non-string-sub"),
        pytest.param({"sub": ""}, id="empty-sub"),
        pytest.param({"sub": "_local"}, id="synthetic-local"),
        pytest.param({"sub": "_transfer"}, id="synthetic-transfer"),
    ],
)
async def test_unusable_sub_rejected_with_constant_message(claims: dict[str, object]) -> None:
    """Every rejection carries one constant message: nothing token-derived
    can leak through its shape. The tool is never reached."""
    context = _context()
    call_next = AsyncMock()
    with (
        patch("lore.adapter.middleware.get_access_token", return_value=_token(claims)),
        pytest.raises(ToolError) as exc_info,
    ):
        await OracleIdentityMiddleware().on_call_tool(context, call_next)
    assert str(exc_info.value) == _AUTH_FAILED
    call_next.assert_not_awaited()
    context.fastmcp_context.set_state.assert_not_awaited()


async def test_missing_fastmcp_context_proceeds_without_state() -> None:
    """fastmcp types ``fastmcp_context`` as ``Context | None``: with no
    Context there is nowhere to stash, so the middleware proceeds and the
    tool-side narrow fails as a masked internal error."""
    context = MagicMock()
    context.fastmcp_context = None
    call_next = AsyncMock(return_value="downstream result")
    with patch(
        "lore.adapter.middleware.get_access_token",
        return_value=_token({"sub": "oracle-42"}),
    ):
        result = await OracleIdentityMiddleware().on_call_tool(context, call_next)
    call_next.assert_awaited_once_with(context)
    assert result == "downstream result"
