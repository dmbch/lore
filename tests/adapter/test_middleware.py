"""Tests for lore.adapter._middleware: oracle identity resolution."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import structlog
from fastmcp.exceptions import ToolError

from lore.adapter._middleware import OracleIdentityMiddleware
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
        "lore.adapter._middleware.get_access_token",
        return_value=_token({"sub": "oracle-42"}),
    ):
        result = await OracleIdentityMiddleware().on_call_tool(context, call_next)
    context.fastmcp_context.set_state.assert_awaited_once_with(
        "oracle_id", "oracle-42", serializable=False
    )
    call_next.assert_awaited_once_with(context)
    assert result == "downstream result"


async def test_no_token_falls_back_to_local_oracle() -> None:
    """Unauthenticated (stdio mode) resolves to the trusted local synthetic."""
    context = _context()
    call_next = AsyncMock()
    with patch("lore.adapter._middleware.get_access_token", return_value=None):
        await OracleIdentityMiddleware().on_call_tool(context, call_next)
    context.fastmcp_context.set_state.assert_awaited_once_with(
        "oracle_id", LOCAL_ORACLE, serializable=False
    )
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
        patch("lore.adapter._middleware.get_access_token", return_value=_token(claims)),
        pytest.raises(ToolError) as exc_info,
    ):
        await OracleIdentityMiddleware().on_call_tool(context, call_next)
    assert str(exc_info.value) == _AUTH_FAILED
    call_next.assert_not_awaited()
    context.fastmcp_context.set_state.assert_not_awaited()


async def test_rejected_sub_leaves_operator_log_record() -> None:
    """A rejection raised here propagates before fastmcp's log arms fire, so
    the middleware's own warning is the only server-side trace of the probe.
    The wire stays constant; the diagnostic lives in the log."""
    context = _context()
    context.message.name = "consult"
    call_next = AsyncMock()
    with (
        patch(
            "lore.adapter._middleware.get_access_token", return_value=_token({"sub": "_transfer"})
        ),
        structlog.testing.capture_logs() as cap,
        pytest.raises(ToolError),
    ):
        await OracleIdentityMiddleware().on_call_tool(context, call_next)
    rejections = [e for e in cap if e["event"] == "auth.rejected"]
    assert len(rejections) == 1
    assert rejections[0]["tool"] == "consult"
    assert rejections[0]["sub"] == "'_transfer'"
