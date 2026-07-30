"""Rebuild the committed golden archive from the seed corpus.

Manual task: `mise run golden-rebuild`. Requires a live GEMINI_API_KEY;
each seed in `tests/e2e/corpus.py` runs one real consult through the full
pipeline (real embeddings, real ledger math). The result is VACUUMed,
gzipped deterministically, and written to `tests/e2e/fixtures/golden.db.gz`
under a hard size budget. Raw and compressed byte counts go in the commit
body.
"""

import asyncio
import gzip
import os
import sqlite3
import tempfile
from pathlib import Path

from lore.config import load_settings
from lore.domain import ConsultLoreRequest
from lore.server import system
from tests.e2e.conftest import attestations
from tests.e2e.corpus import SEEDS

GOLDEN_MAX_COMPRESSED_BYTES = 1_048_576
GOLDEN_PATH = Path(__file__).parent / "golden.db.gz"


async def _build(db_path: Path) -> None:
    """Seed a fresh archive at db_path, one live consult per corpus record."""
    dsn = f"sqlite:///{db_path}"
    os.environ.setdefault("DATABASE_URL", dsn)
    settings = load_settings()
    settings = settings.model_copy(update={"dsn": dsn})
    # A stray env override on the rebuild machine would bake into the
    # committed fixture invisibly; print what actually applied so the
    # commit body can carry it.
    print(
        "effective config: "
        f"embedding={settings.embedding.model} "
        f"fast={settings.fast.model} "
        f"reasoning={settings.reasoning.model} "
        f"attestation_half_life={settings.epistemics.attestation_half_life} "
        f"trust_half_life={settings.epistemics.trust_half_life} "
        f"maturity_k={settings.epistemics.maturity_k}"
    )
    async with system(settings) as orchestrator:
        for seed in SEEDS:
            await orchestrator.consult(
                oracle_id=seed.oracle,
                request=ConsultLoreRequest(hypothesis=seed.hypothesis, confidence=seed.confidence),
                correlation_id=seed.correlation_id,
            )
        for seed in SEEDS:
            rows = await attestations(orchestrator, seed.correlation_id)
            ids = {row["hypothesis_id"] for row in rows if row["oracle_id"] == seed.oracle}
            if len(ids) != 1:
                msg = (
                    f"seed {seed.correlation_id!r} resolved to {len(ids)} hypothesis ids, "
                    "expected exactly one"
                )
                raise SystemExit(msg)


def _vacuum(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    try:
        # The swap-in rebase shifts only ledger-side timestamps; a populated
        # _cache would carry unshifted ones. The build path never touches
        # OAuth/session storage; keep that assumption loud.
        (cached,) = conn.execute("SELECT count(*) FROM _cache").fetchone()
        if cached:
            msg = f"_cache holds {cached} rows; the golden rebase assumes it is empty"
            raise SystemExit(msg)
        conn.execute("VACUUM")
    finally:
        conn.close()


def _compress(db_path: Path) -> tuple[int, int]:
    """Gzip db_path to GOLDEN_PATH with mtime=0: identical DB bytes, identical artifact."""
    raw = db_path.read_bytes()
    with GOLDEN_PATH.open("wb") as out, gzip.GzipFile(fileobj=out, mode="wb", mtime=0) as gz:
        gz.write(raw)
    return len(raw), GOLDEN_PATH.stat().st_size


def main() -> None:
    if not os.environ.get("GEMINI_API_KEY"):
        msg = "GEMINI_API_KEY is not set; the rebuild seeds through live consults"
        raise SystemExit(msg)
    GOLDEN_PATH.unlink(missing_ok=True)
    with tempfile.TemporaryDirectory() as scratch:
        db_path = Path(scratch) / "lore.db"
        asyncio.run(_build(db_path))
        _vacuum(db_path)
        raw_size, compressed_size = _compress(db_path)
    if compressed_size > GOLDEN_MAX_COMPRESSED_BYTES:
        GOLDEN_PATH.unlink()
        msg = (
            f"golden archive exceeds the size budget: {compressed_size} compressed bytes "
            f"({raw_size} raw) > {GOLDEN_MAX_COMPRESSED_BYTES}. Growing past the budget "
            "requires raising GOLDEN_MAX_COMPRESSED_BYTES in a reviewed commit."
        )
        raise SystemExit(msg)
    print(f"golden archive: {raw_size} raw bytes, {compressed_size} compressed bytes")


if __name__ == "__main__":
    main()
