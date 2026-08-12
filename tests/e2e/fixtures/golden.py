"""Golden-archive access for suites and tooling: decompress a copy, re-base its clock."""

import gzip
import sqlite3
import time
from pathlib import Path

GOLDEN_ARCHIVE = Path(__file__).parent / "golden.db.gz"


def golden_copy(target_dir: Path) -> str:
    """Decompress the golden archive into target_dir and re-base its timestamps.

    Every stored timestamp shifts by one delta (now minus the newest
    attestation), so relationships between rows are preserved while seeds
    read as attested today, matching the same-session invariant the suites
    document. Plain sqlite3 suffices: no virtual table is touched. Returns
    the dsn for the copy.
    """
    if not GOLDEN_ARCHIVE.exists():
        msg = f"golden archive missing at {GOLDEN_ARCHIVE}: run `mise run golden-rebuild`"
        raise FileNotFoundError(msg)
    db_path = target_dir / "lore.db"
    with gzip.open(GOLDEN_ARCHIVE, "rb") as src:
        db_path.write_bytes(src.read())
    conn = sqlite3.connect(db_path)
    try:
        (latest,) = conn.execute("SELECT max(timestamp) FROM attestations").fetchone()
        if latest is None:
            msg = "golden archive has no attestations: run `mise run golden-rebuild`"
            raise ValueError(msg)
        delta = int(time.time()) - int(latest)
        conn.execute("UPDATE attestations SET timestamp = timestamp + ?", (delta,))
        conn.execute("UPDATE requests SET timestamp = timestamp + ?", (delta,))
        conn.execute("UPDATE hypotheses SET created_at = created_at + ?", (delta,))
        conn.commit()
    finally:
        conn.close()
    return f"sqlite:///{db_path}"
