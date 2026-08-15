"""The shared driver shell: keyless guard and artifacts-root resolution."""

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts._metered import artifacts_root, require_gemini_key


def test_requested_artifacts_dir_is_created_and_returned(tmp_path: Path) -> None:
    requested = tmp_path / "nested" / "artifacts"

    assert artifacts_root(requested, prefix="lore-test-") == requested
    assert requested.is_dir()


def test_absent_artifacts_dir_falls_back_to_a_fresh_tempdir() -> None:
    root = artifacts_root(None, prefix="lore-test-")

    assert root.is_dir()
    assert root.name.startswith("lore-test-")


def test_keyless_run_exits_before_any_spend() -> None:
    with (
        patch.dict(os.environ, {}, clear=True),
        pytest.raises(SystemExit, match="probe: GEMINI_API_KEY not set; live calls"),
    ):
        require_gemini_key(script="probe", spend="live calls")


def test_keyed_run_passes_the_guard() -> None:
    with patch.dict(os.environ, {"GEMINI_API_KEY": "key"}):
        require_gemini_key(script="probe", spend="live calls")
