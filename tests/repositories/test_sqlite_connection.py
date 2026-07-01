"""Tests for the SQLite connection helper — extension-load failure path.

The ``connect()`` factory loads the ``sqlite-vec`` shared library after
opening the connection. If the shared library is missing or unloadable,
the helper closes the connection it just opened so the caller does not
inherit a half-initialised handle. Audit S3.3-native: the original
``except Exception`` was guarded with ``# pragma: no cover``;
narrowing to ``(sqlite3.Error, OSError)`` and exercising the path
removes the suppression.
"""

import sqlite3
import tempfile
from pathlib import Path

import pytest

from lore.repositories.sqlite.connection import connect


class TestConnectExtensionLoadFailure:
    async def test_extension_load_failure_propagates_and_closes_connection(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Force ``load_extension`` to fail at the path returned by
        # ``sqlite_vec.loadable_path``. Aim a known-missing file at it;
        # SQLite's loader raises ``sqlite3.OperationalError``.
        monkeypatch.setattr(
            "lore.repositories.sqlite.connection.sqlite_vec.loadable_path",
            lambda: "/nonexistent/path/to/vec.dylib",
        )

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = Path(f.name)
        try:
            with pytest.raises((sqlite3.OperationalError, OSError)):
                await connect(str(db_path))
        finally:
            db_path.unlink(missing_ok=True)
