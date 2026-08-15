"""The shared shell of the metered script drivers: rate, recall, recall-protocol."""

import os
import sys
import tempfile
from pathlib import Path


def require_gemini_key(*, script: str, spend: str) -> None:
    """Exit before any composition, filesystem work, or network when keyless."""
    if not os.environ.get("GEMINI_API_KEY"):
        sys.exit(f"{script}: GEMINI_API_KEY not set; {spend}")


def artifacts_root(requested: Path | None, *, prefix: str) -> Path:
    """Resolve where a run's receipts live.

    Every run is metered spend; the artifacts are the receipts and always
    persist, to a fresh tempdir unless ``requested`` places them deliberately.
    """
    if requested is None:
        return Path(tempfile.mkdtemp(prefix=prefix))
    requested.mkdir(parents=True, exist_ok=True)
    return requested
