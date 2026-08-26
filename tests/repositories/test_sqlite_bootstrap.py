"""Tests for the SQLite sync bootstrap helper: WAL journal mode.

The async runtime ``connect()`` already sets ``PRAGMA journal_mode=WAL``;
the sync ``_connect()`` used by ``run_migrations()`` and ``check_health()``
must do the same so the very first interaction with a fresh DB file runs
under WAL rather than the default rollback journal. Closes audit S1.5.
"""

import tempfile
from pathlib import Path

from lore.repositories._sqlite.bootstrap import _connect  # pyright: ignore[reportPrivateUsage]


class TestConnectJournalMode:
    def test_connect_enables_wal_journal_mode(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = Path(f.name)
        try:
            conn = _connect(f"sqlite:///{db_path}")
            try:
                mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            finally:
                conn.close()
            assert mode == "wal"
        finally:
            db_path.unlink(missing_ok=True)
