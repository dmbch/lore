"""Tests for lore.telemetry — configure_telemetry + module-level start_span."""

import logging
import os
from collections.abc import Iterator
from unittest.mock import patch

import pytest
import structlog
from opentelemetry import metrics as otel_metrics
from opentelemetry import trace as otel_trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from lore import telemetry

# Every test in this module configures telemetry from scratch; bind the
# conftest-provided opt-in reset as module-wide autouse.
pytestmark = pytest.mark.usefixtures("reset_telemetry")


@pytest.fixture
def isolated_configure() -> Iterator[None]:
    """Clear env so configure_telemetry() runs with predictable state."""
    with patch.dict(os.environ, {}, clear=True):
        yield


# ---------------------------------------------------------------------------
# configure_telemetry() — provider non-replacement
# ---------------------------------------------------------------------------


def test_configure_telemetry_does_not_replace_global_tracer_provider(
    isolated_configure: None,
) -> None:
    """configure_telemetry() never swaps the global TracerProvider.

    Locks in the auto-config contract: whatever ``opentelemetry-instrument``
    installed (or the API proxy when bare) stays installed. The lazy
    ``_get_tracer()`` resolves through that provider at first use.
    """
    before = otel_trace.get_tracer_provider()
    telemetry.configure_telemetry()
    after = otel_trace.get_tracer_provider()
    assert after is before


def test_configure_telemetry_does_not_replace_global_meter_provider(
    isolated_configure: None,
) -> None:
    """configure_telemetry() never swaps the global MeterProvider."""
    before = otel_metrics.get_meter_provider()
    telemetry.configure_telemetry()
    after = otel_metrics.get_meter_provider()
    assert after is before


def test_configure_telemetry_no_op_when_run_outside_wrapper(
    isolated_configure: None, capsys: pytest.CaptureFixture[str]
) -> None:
    """Without opentelemetry-instrument, the API proxies stand in — no exception, logs flow.

    The documented bare-launch shape (``python -m lore`` without the wrapper):
    spans are non-recording, but ``start_span`` is still usable and structlog
    still writes through.
    """
    telemetry.configure_telemetry()
    log = structlog.get_logger("test.bare")
    with telemetry.start_span("smoke"):
        log.info("hi from no-op")
    assert "hi from no-op" in capsys.readouterr().err


def test_configure_telemetry_double_call_raises(isolated_configure: None) -> None:
    """Calling configure_telemetry() twice raises RuntimeError."""
    telemetry.configure_telemetry()
    with pytest.raises(RuntimeError, match="exactly once"):
        telemetry.configure_telemetry()


def test_configure_telemetry_invalid_log_level_raises(
    isolated_configure: None,
) -> None:
    """An unrecognized LOG_LEVEL raises ValueError."""
    with (
        patch.dict(os.environ, {"LOG_LEVEL": "POTATO"}),
        pytest.raises(ValueError, match="invalid LOG_LEVEL"),
    ):
        telemetry.configure_telemetry()


# ---------------------------------------------------------------------------
# LOG_LEVEL filtering
# ---------------------------------------------------------------------------


def test_log_level_warning_suppresses_info(
    isolated_configure: None, capsys: pytest.CaptureFixture[str]
) -> None:
    """LOG_LEVEL=WARNING suppresses info() from both structlog and stdlib."""
    with patch.dict(os.environ, {"LOG_LEVEL": "WARNING"}):
        telemetry.configure_telemetry()

    log = structlog.get_logger("test.suppress")
    log.info("should be suppressed")
    log.warning("should appear")
    logging.getLogger("test.stdlib").info("stdlib suppressed")
    logging.getLogger("test.stdlib").warning("stdlib visible")

    output = capsys.readouterr().err
    assert "should be suppressed" not in output
    assert "stdlib suppressed" not in output
    assert "should appear" in output
    assert "stdlib visible" in output


# ---------------------------------------------------------------------------
# FastMCP / LiteLLM logger reroute — and the OTEL_LOG_LEVEL gate
# ---------------------------------------------------------------------------


def test_configure_telemetry_reroutes_fastmcp_logger(isolated_configure: None) -> None:
    """The ``fastmcp`` logger is reset to bare-stdlib defaults so records propagate to root.

    FastMCP attaches a ``RichHandler`` and sets ``propagate = False`` at import. The
    reroute reverses that so the root structlog handler sees the records.
    """
    telemetry.configure_telemetry()
    fastmcp_logger = logging.getLogger("fastmcp")
    assert fastmcp_logger.handlers == []
    assert fastmcp_logger.propagate is True
    assert fastmcp_logger.level == logging.NOTSET


@pytest.mark.parametrize("name", ["LiteLLM", "LiteLLM Proxy", "LiteLLM Router"])
def test_configure_telemetry_reroutes_litellm_loggers(isolated_configure: None, name: str) -> None:
    """LiteLLM attaches a ``StreamHandler`` to three loggers at import. All three reroute."""
    telemetry.configure_telemetry()
    litellm_logger = logging.getLogger(name)
    assert litellm_logger.handlers == []
    assert litellm_logger.propagate is True
    assert litellm_logger.level == logging.NOTSET


def test_fastmcp_logger_records_flow_to_root_renderer(
    isolated_configure: None, capsys: pytest.CaptureFixture[str]
) -> None:
    """End-to-end: a record emitted on the fastmcp logger renders through structlog."""
    telemetry.configure_telemetry()
    logging.getLogger("fastmcp").info("from fastmcp")
    assert "from fastmcp" in capsys.readouterr().err


def test_configure_telemetry_mirrors_log_level_to_otel(isolated_configure: None) -> None:
    """LOG_LEVEL flows through to OTEL_LOG_LEVEL so the OTel SDK shares the gate."""
    with patch.dict(os.environ, {"LOG_LEVEL": "DEBUG"}):
        telemetry.configure_telemetry()
        assert os.environ["OTEL_LOG_LEVEL"] == "DEBUG"


def test_configure_telemetry_preserves_explicit_otel_log_level(
    isolated_configure: None,
) -> None:
    """An explicit OTEL_LOG_LEVEL wins over LOG_LEVEL — operator override stands."""
    with patch.dict(os.environ, {"LOG_LEVEL": "DEBUG", "OTEL_LOG_LEVEL": "ERROR"}):
        telemetry.configure_telemetry()
        assert os.environ["OTEL_LOG_LEVEL"] == "ERROR"


def test_configure_telemetry_defaults_litellm_to_request_span_mode(
    isolated_configure: None,
) -> None:
    """litellm records LLM calls on its own child spans, not Lore's ended stage spans."""
    telemetry.configure_telemetry()
    assert os.environ["USE_OTEL_LITELLM_REQUEST_SPAN"] == "true"


def test_configure_telemetry_preserves_explicit_litellm_span_mode(
    isolated_configure: None,
) -> None:
    """An explicit USE_OTEL_LITELLM_REQUEST_SPAN wins — operator override stands."""
    with patch.dict(os.environ, {"USE_OTEL_LITELLM_REQUEST_SPAN": "false"}):
        telemetry.configure_telemetry()
        assert os.environ["USE_OTEL_LITELLM_REQUEST_SPAN"] == "false"


# ---------------------------------------------------------------------------
# trace_id injection (requires an SDK TracerProvider — the wrapper case)
# ---------------------------------------------------------------------------


def test_trace_id_injected_into_log_events_when_sdk_is_installed(
    isolated_configure: None, capsys: pytest.CaptureFixture[str]
) -> None:
    """With an SDK provider installed (the wrapper case), trace_id flows into logs.

    Mirrors what ``opentelemetry-instrument`` does at process start. We install
    an SDK ``TracerProvider`` *before* ``configure_telemetry()`` so the
    module-level tracer is bound to a recording context, and
    ``_add_trace_context`` has a valid ``SpanContext`` to inject.
    """
    span_exporter = InMemorySpanExporter()
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(SimpleSpanProcessor(span_exporter))
    with patch("lore.telemetry.otel_trace.get_tracer_provider", return_value=tracer_provider):
        telemetry.configure_telemetry()
        log = structlog.get_logger("test.trace")
        with telemetry.start_span("traced_op"):
            log.info("inside span")

    output = capsys.readouterr().err
    assert "trace_id" in output
    assert "span_id" in output


def test_trace_id_absent_without_active_span(
    isolated_configure: None, capsys: pytest.CaptureFixture[str]
) -> None:
    """Log events outside a span do not carry trace_id."""
    telemetry.configure_telemetry()
    log = structlog.get_logger("test.notrace")
    log.info("no span")

    assert "trace_id" not in capsys.readouterr().err


# ---------------------------------------------------------------------------
# logger + stdlib bridge
# ---------------------------------------------------------------------------


def test_logger_with_bound_context_appears_in_output(
    isolated_configure: None, capsys: pytest.CaptureFixture[str]
) -> None:
    """Bound context keys appear in log output."""
    telemetry.configure_telemetry()
    log = structlog.get_logger("test.bind").bind(component="test_comp")
    log.info("bound message")
    output = capsys.readouterr().err
    assert "component" in output
    assert "test_comp" in output


def test_stdlib_bridge_with_info_flows_through_renderer(
    isolated_configure: None, capsys: pytest.CaptureFixture[str]
) -> None:
    """stdlib logging routes through structlog to stderr."""
    telemetry.configure_telemetry()

    logging.getLogger("test.bridge").info("bridge test message")

    captured = capsys.readouterr()
    assert "bridge test message" in captured.err
    assert captured.out == ""


def test_native_structlog_records_carry_logger_name(
    isolated_configure: None, capsys: pytest.CaptureFixture[str]
) -> None:
    """Native structlog records render the emitter name, same as bridged stdlib ones.

    Pairs symmetrically with ``test_stdlib_bridge_with_info_flows_through_renderer``:
    both record kinds carry a ``logger`` key, so JSON consumers can group by emitter
    regardless of who produced the record.
    """
    telemetry.configure_telemetry()
    structlog.get_logger("test.named").info("hello")
    output = capsys.readouterr().err
    assert "logger" in output
    assert "test.named" in output


def test_json_renderer_exc_info_emits_structured_traceback(
    isolated_configure: None, capsys: pytest.CaptureFixture[str]
) -> None:
    """In JSON mode, ``log.error(..., exc_info=True)`` surfaces a structured exception.

    Four call sites in ``adapter/mcp.py`` rely on this contract. ``dict_tracebacks``
    produces a JSON-queryable list of frames (exc_type, exc_value, frames) — log
    aggregators filter by exception class natively, rather than grepping a wall-
    of-text string traceback.
    """
    telemetry.configure_telemetry()
    log = structlog.get_logger("test.exc")
    try:
        msg = "deliberate"
        raise RuntimeError(msg)
    except RuntimeError:
        log.error("boom", exc_info=True)
    output = capsys.readouterr().err
    assert "exception" in output
    assert "RuntimeError" in output
    assert "deliberate" in output


def test_console_renderer_exc_info_does_not_crash_formatter(
    isolated_configure: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """In TTY mode, ``log.error(..., exc_info=True)`` must not blow up the formatter.

    ``ConsoleRenderer`` writes ``"\\n" + exc`` (string concat) for the rendered
    traceback. ``dict_tracebacks`` would replace ``exc_info`` with a list, raising
    ``TypeError`` on the concat and losing the log line to stdlib's "Logging
    error" fallback. The split is to keep the dict-tracebacks step on the JSON
    branch only; this test pins the no-crash invariant for the dev path.
    """
    import io

    class FakeTTY(io.StringIO):
        def isatty(self) -> bool:
            return True

    fake = FakeTTY()
    monkeypatch.setattr("sys.stderr", fake)
    telemetry.configure_telemetry()

    log = structlog.get_logger("test.tty_exc")
    try:
        msg = "deliberate"
        raise RuntimeError(msg)
    except RuntimeError:
        log.error("boom", exc_info=True)

    output = fake.getvalue()
    assert "boom" in output
    assert "RuntimeError" in output
    assert "Logging error" not in output  # stdlib fallback when formatter raises


# ---------------------------------------------------------------------------
# stdout safety (MCP transport)
# ---------------------------------------------------------------------------


def test_stderr_mode_with_all_loggers_does_not_touch_stdout(
    isolated_configure: None, capsys: pytest.CaptureFixture[str]
) -> None:
    """Logs land on stderr only — stdout stays clean for the MCP transport."""
    telemetry.configure_telemetry()

    log = structlog.get_logger("test.stdout")
    log.info("stderr only")
    logging.getLogger("stdlib.test").info("also stderr")

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "stderr only" in captured.err
    assert "also stderr" in captured.err


# ---------------------------------------------------------------------------
# Module-level start_span() — span creation, context binding, attributes
# ---------------------------------------------------------------------------
#
# start_span() resolves its tracer through otel_trace.get_tracer("lore") on
# every call. Tests assert on span content by patching the otel_trace getter
# in lore.telemetry's namespace; the patch routes the call through a local
# SDK TracerProvider with an in-memory exporter.


def _patched_sdk_provider() -> tuple[InMemorySpanExporter, TracerProvider]:
    span_exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(span_exporter))
    return span_exporter, provider


def test_module_start_span_creates_named_span() -> None:
    """The module-level start_span() exports a span with the given name."""
    span_exporter, provider = _patched_sdk_provider()
    with (
        patch("lore.telemetry.otel_trace.get_tracer_provider", return_value=provider),
        telemetry.start_span("op"),
    ):
        pass

    spans = span_exporter.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].name == "op"


def test_module_start_span_binds_kwargs_to_log_context_and_clears_on_exit(
    isolated_configure: None, capsys: pytest.CaptureFixture[str]
) -> None:
    """Kwargs appear in log events emitted within the span and clear on exit."""
    telemetry.configure_telemetry()
    log = structlog.get_logger("test.span_ctx")

    with telemetry.start_span("op", oracle_id="user-42", correlation_id="req-abc"):
        log.info("inside span")

    inside = capsys.readouterr().err
    assert "oracle_id" in inside
    assert "user-42" in inside
    assert "correlation_id" in inside
    assert "req-abc" in inside

    log.info("after span")
    after = capsys.readouterr().err
    assert "oracle_id" not in after
    assert "correlation_id" not in after


def test_module_start_span_sets_span_attributes() -> None:
    """Module-level start_span() kwargs are set as OTel span attributes."""
    span_exporter, provider = _patched_sdk_provider()
    with (
        patch("lore.telemetry.otel_trace.get_tracer_provider", return_value=provider),
        telemetry.start_span("op", path="read", oracle_id="user-42"),
    ):
        pass

    spans = span_exporter.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].attributes is not None
    assert spans[0].attributes.get("path") == "read"
    assert spans[0].attributes.get("oracle_id") == "user-42"


def test_module_start_span_creates_child_under_active_parent() -> None:
    """Nested under start_as_current_span('parent'), the child's parent matches."""
    span_exporter, provider = _patched_sdk_provider()
    with patch("lore.telemetry.otel_trace.get_tracer_provider", return_value=provider):
        tracer = provider.get_tracer("test_parent")
        with tracer.start_as_current_span("parent"), telemetry.start_span("child_op"):
            pass

    spans = span_exporter.get_finished_spans()
    names = [s.name for s in spans]
    assert "child_op" in names
    assert "parent" in names

    child = next(s for s in spans if s.name == "child_op")
    parent = next(s for s in spans if s.name == "parent")
    child_parent_ctx = child.parent
    parent_ctx = parent.context
    assert child_parent_ctx is not None
    assert parent_ctx is not None
    assert child_parent_ctx.span_id == parent_ctx.span_id


def test_module_start_span_no_active_trace_creates_root() -> None:
    """start_span() without an active trace creates a root span."""
    span_exporter, provider = _patched_sdk_provider()
    with (
        patch("lore.telemetry.otel_trace.get_tracer_provider", return_value=provider),
        telemetry.start_span("root_op"),
    ):
        pass

    spans = span_exporter.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].name == "root_op"
    assert spans[0].parent is None
