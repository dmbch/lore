"""Telemetry — structlog + OTel glue.

`configure_telemetry()` is the one-shot wiring; `start_span()` is the
seam that binds the same context to a span and to structlog's
contextvars so traces and logs share identity.

SDK provider wiring is delegated to `opentelemetry-instrument`; without
the wrapper, the global providers are OTel API proxies — spans are
non-recording, logs still flow.

`LOG_LEVEL` (default `INFO`) controls stderr verbosity and is mirrored
into `FASTMCP_LOG_LEVEL` and `OTEL_LOG_LEVEL` via `setdefault` so the
same gate covers FastMCP and the OTel SDK; explicit operator values win.
"""

import logging
import os
import sys
import threading
from collections.abc import Generator
from contextlib import contextmanager

import litellm
import structlog
from opentelemetry import trace as otel_trace

_VALID_LOG_LEVELS: frozenset[str] = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})
_configure_lock = threading.Lock()
_configured = False


@contextmanager
def start_span(name: str, **context: str | float | bool) -> Generator[otel_trace.Span]:
    """Open a span and bind `context` to both span attributes and structlog contextvars."""
    with otel_trace.get_tracer("lore").start_as_current_span(name) as span:
        for k, v in context.items():
            span.set_attribute(k, v)
        with structlog.contextvars.bound_contextvars(**context):
            yield span


def _add_trace_context(
    _logger: structlog.types.WrappedLogger,
    _method: str,
    event_dict: structlog.types.EventDict,
) -> structlog.types.EventDict:
    """Inject OTel trace_id and span_id into every log event."""
    span = otel_trace.get_current_span()
    ctx = span.get_span_context()
    if ctx.is_valid:
        event_dict["trace_id"] = format(ctx.trace_id, "032x")
        event_dict["span_id"] = format(ctx.span_id, "016x")
    return event_dict


def _configure_logging(log_level: str) -> None:
    level = getattr(logging, log_level)
    renderer: structlog.types.Processor = (
        structlog.dev.ConsoleRenderer()
        if sys.stderr.isatty()
        else structlog.processors.JSONRenderer()
    )

    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        _add_trace_context,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
    ]

    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
        foreign_pre_chain=[
            *shared_processors,
            structlog.stdlib.add_logger_name,
        ],
    )
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=False,
    )


def configure_telemetry() -> None:
    """Wire structlog and the LiteLLM OTel callback. Call exactly once at startup."""
    global _configured
    with _configure_lock:
        if _configured:
            msg = "configure_telemetry() must be called exactly once"
            raise RuntimeError(msg)

        log_level = os.environ.get("LOG_LEVEL", "INFO").upper()
        if log_level not in _VALID_LOG_LEVELS:
            msg = f"invalid LOG_LEVEL: {log_level!r} (expected one of {sorted(_VALID_LOG_LEVELS)})"
            raise ValueError(msg)
        os.environ.setdefault("FASTMCP_LOG_LEVEL", log_level)
        os.environ.setdefault("OTEL_LOG_LEVEL", log_level)

        _configure_logging(log_level)
        litellm.callbacks = ["otel"]

        _configured = True
