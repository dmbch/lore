"""Image entry point: ``python -m lore``.

The wheel-only container has no source tree for ``fastmcp run`` to point at,
so the image launches the server through this module. Whoever owns process
start owns the log posture: ``main()`` defaults ``FASTMCP_LOG_ENABLED`` off
before fastmcp loads, so framework records propagate to the root structlog
handler. Dev under ``fastmcp run`` cannot win that import race in lore code;
the launcher env carries the switch there (mise ``[env]``, ``.env.claude``).
"""

import os


def main() -> None:
    # Load-bearing order: fastmcp snapshots env into its settings singleton
    # at import, so the off-switch must land before the first fastmcp
    # import. setdefault keeps the operator override.
    os.environ.setdefault("FASTMCP_LOG_ENABLED", "false")

    import fastmcp
    import structlog

    from lore.config import ConfigurationError
    from lore.server import server

    try:
        srv = server()
    except ConfigurationError as exc:
        # A refusal is operator input to fix, not a crash: one structured
        # event instead of a traceback, and a nonzero exit. configure_telemetry
        # is server()'s first statement, so structlog is already routed.
        structlog.get_logger(__name__).error("bootstrap.refused", reason=str(exc))
        raise SystemExit(1) from exc
    if fastmcp.settings.transport == "stdio":
        # The stdio dispatch rejects the uvicorn kwarg.
        srv.run()
    else:
        # Unhook uvicorn's dictconfig so its records propagate into the root
        # structlog pipeline instead of uvicorn's own plaintext handlers.
        srv.run(uvicorn_config={"log_config": None})


if __name__ == "__main__":
    main()
