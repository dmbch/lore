"""Tests for lore.__main__: the ``python -m lore`` image entry point."""

import runpy
from unittest.mock import MagicMock

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

    runpy.run_module("lore.__main__", run_name="__main__")

    built.run.assert_called_once_with()
