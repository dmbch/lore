"""Tests for lore.__main__: the ``python -m lore`` image entry point."""

import os
import runpy
from unittest.mock import MagicMock

import fastmcp
import pytest


def test_python_m_lore_builds_and_runs_the_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    """``python -m lore`` calls the ``server()`` factory and runs the result.

    Driven through ``runpy`` with ``run_name="__main__"`` so the module's
    ``if __name__ == "__main__"`` guard fires, exercising the real entry path.
    ``server`` is patched to an inert factory so ``run()`` never blocks on the
    event loop.
    """
    built = MagicMock()
    monkeypatch.setattr("lore.server.server", lambda: built)
    monkeypatch.setattr(fastmcp.settings, "transport", "stdio")

    runpy.run_module("lore.__main__", run_name="__main__")

    built.run.assert_called_once_with()


def test_main_defaults_fastmcp_log_enabled_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """The entry point owns the log posture: with no operator env, ``main()``
    lands ``FASTMCP_LOG_ENABLED=false``. The setdefault-before-import ordering
    is not observable in-process (the suite imports fastmcp long before this
    test); the load-bearing-order comment in ``lore.__main__`` carries it.
    """
    from lore.__main__ import main

    monkeypatch.setattr("lore.server.server", lambda: MagicMock())
    monkeypatch.delenv("FASTMCP_LOG_ENABLED", raising=False)

    main()

    assert os.environ["FASTMCP_LOG_ENABLED"] == "false"


def test_main_respects_preset_fastmcp_log_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """setdefault, not overwrite: an operator's explicit value survives."""
    from lore.__main__ import main

    monkeypatch.setattr("lore.server.server", lambda: MagicMock())
    monkeypatch.setenv("FASTMCP_LOG_ENABLED", "true")

    main()

    assert os.environ["FASTMCP_LOG_ENABLED"] == "true"


def test_main_http_run_unhooks_uvicorn_dictconfig(monkeypatch: pytest.MonkeyPatch) -> None:
    """On http transport, ``run()`` gets ``uvicorn_config={"log_config": None}``
    so uvicorn's records propagate into the root structlog pipeline instead of
    its own plaintext handlers.
    """
    from lore.__main__ import main

    built = MagicMock()
    monkeypatch.setattr("lore.server.server", lambda: built)
    monkeypatch.setattr(fastmcp.settings, "transport", "http")

    main()

    built.run.assert_called_once_with(uvicorn_config={"log_config": None})


def test_main_stdio_run_passes_no_uvicorn_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """The stdio dispatch rejects the uvicorn kwarg; ``main()`` must not pass it."""
    from lore.__main__ import main

    built = MagicMock()
    monkeypatch.setattr("lore.server.server", lambda: built)
    monkeypatch.setattr(fastmcp.settings, "transport", "stdio")

    main()

    built.run.assert_called_once_with()
