"""Tests for lore.config loader — vendor defaults, deep merge, validation.

Pure model-construction tests for the model-role configs live in
``tests/providers/test_config.py`` (the providers layer owns those types).
"""

import os
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from lore.config import load_settings
from lore.config.loader import (
    _load_bundled_toml,  # pyright: ignore[reportPrivateUsage]
)
from lore.config.types import DecayConfig

# Minimal valid env for most tests — DATABASE_URL is the only DSN env var.
_BASE_ENV = {"DATABASE_URL": "sqlite:///test.db"}

# Path to the test TOML fixture.
_TOML_PATH = Path(__file__).parent.parent / "fixtures" / "lore.toml"

# A nonexistent path — forces vendor detection or error.
_NO_TOML = Path(__file__).parent.parent / "fixtures" / "nonexistent.toml"

# Complete TOML with all three models + dimensions.
_COMPLETE_TOML = Path(__file__).parent.parent / "fixtures" / "lore_complete.toml"

_90_DAYS = 90 * 86400.0


# ---------------------------------------------------------------------------
# LiteLLM model parameters from TOML
# ---------------------------------------------------------------------------


def test_model_config_carries_litellm_params() -> None:
    toml_path = Path(__file__).parent.parent / "fixtures" / "lore_litellm_params.toml"
    with patch.dict(os.environ, _BASE_ENV, clear=True):
        s = load_settings(toml_path=toml_path)
        assert s.fast.temperature == 0.3
        assert s.fast.max_tokens == 4096
        assert s.reasoning.temperature is None
        assert s.reasoning.max_tokens is None


# ---------------------------------------------------------------------------
# Embedding config — dimensions from TOML
# ---------------------------------------------------------------------------


def test_embedding_config_dimensions_from_toml() -> None:
    with patch.dict(os.environ, _BASE_ENV, clear=True):
        s = load_settings(toml_path=_COMPLETE_TOML)
        assert s.embedding.dimensions == 1024


# ---------------------------------------------------------------------------
# Vendor TOML defaults
# ---------------------------------------------------------------------------


def test_vendor_toml_gemini_has_all_sections() -> None:
    defaults = _load_bundled_toml(package="lore.config.vendors", name="gemini")
    assert "embedding" in defaults
    assert "fast" in defaults
    assert "reasoning" in defaults
    assert defaults["embedding"]["model"] == "gemini/gemini-embedding-001"


def test_vendor_toml_gemini_has_task_type() -> None:
    defaults = _load_bundled_toml(package="lore.config.vendors", name="gemini")
    embedding = defaults["embedding"]
    assert isinstance(embedding.get("task_type"), dict)
    task_type = embedding["task_type"]
    assert isinstance(task_type, dict)
    assert task_type["document"] == "RETRIEVAL_DOCUMENT"


def test_vendor_toml_gemini_has_reasoning_effort() -> None:
    defaults = _load_bundled_toml(package="lore.config.vendors", name="gemini")
    assert defaults["reasoning"]["reasoning_effort"] == "high"


def test_vendor_toml_openai_has_reasoning_effort() -> None:
    defaults = _load_bundled_toml(package="lore.config.vendors", name="openai")
    assert defaults["reasoning"]["reasoning_effort"] == "high"


def test_vendor_toml_bedrock_has_reasoning_effort() -> None:
    defaults = _load_bundled_toml(package="lore.config.vendors", name="bedrock")
    assert defaults["reasoning"]["reasoning_effort"] == "high"


def test_vendor_toml_unknown_raises() -> None:
    with pytest.raises(FileNotFoundError):
        _load_bundled_toml(package="lore.config.vendors", name="unknown")


# ---------------------------------------------------------------------------
# All-or-nothing model config
# ---------------------------------------------------------------------------


def test_partial_user_toml_missing_fast_raises() -> None:
    """User provides [embedding] and [reasoning] but not [fast] — error."""
    partial = Path(__file__).parent.parent / "fixtures" / "lore_missing_fast.toml"
    with (
        patch.dict(os.environ, _BASE_ENV, clear=True),
        pytest.raises(ValidationError, match="fast"),
    ):
        load_settings(toml_path=partial)


def test_vendor_gemini_includes_task_type() -> None:
    env = {**_BASE_ENV, "GEMINI_API_KEY": "fake"}
    with patch.dict(os.environ, env, clear=True):
        s = load_settings(toml_path=_NO_TOML)
        assert s.embedding.task_type is not None
        assert s.embedding.task_type.document == "RETRIEVAL_DOCUMENT"


# ---------------------------------------------------------------------------
# Embedding task_type extraction + model reasoning_effort
# ---------------------------------------------------------------------------

_TASK_TYPE_TOML = Path(__file__).parent.parent / "fixtures" / "lore_task_type.toml"


def test_embedding_task_type_from_toml() -> None:
    with patch.dict(os.environ, _BASE_ENV, clear=True):
        s = load_settings(toml_path=_TASK_TYPE_TOML)
        assert s.embedding.task_type is not None
        assert s.embedding.task_type.document == "RETRIEVAL_DOCUMENT"
        assert s.embedding.task_type.question == "QUESTION_ANSWERING"
        assert s.embedding.task_type.verification == "FACT_VERIFICATION"


def test_embedding_task_type_none_when_absent() -> None:
    with patch.dict(os.environ, _BASE_ENV, clear=True):
        s = load_settings(toml_path=_TOML_PATH)
        assert s.embedding.task_type is None


def test_build_model_missing_model_key_raises() -> None:
    """A model section without a 'model' key is rejected."""
    toml = Path(__file__).parent.parent / "fixtures" / "lore_missing_model_key.toml"
    with (
        patch.dict(os.environ, _BASE_ENV, clear=True),
        pytest.raises(ValidationError, match="model"),
    ):
        load_settings(toml_path=toml)


# ---------------------------------------------------------------------------
# Deep merge — trust partial override
# ---------------------------------------------------------------------------


def test_user_toml_trust_partial_override_merges_with_defaults() -> None:
    """User overrides one trust field — rest comes from base defaults."""
    toml_path = Path(__file__).parent.parent / "fixtures" / "lore_trust_partial.toml"
    with patch.dict(os.environ, _BASE_ENV, clear=True):
        s = load_settings(toml_path=toml_path)
        assert s.trust.maturity == 5.0


# ---------------------------------------------------------------------------
# Chunk 3: BeforeValidator + extra="forbid"
# ---------------------------------------------------------------------------


def test_decay_config_accepts_duration_string() -> None:
    dc = DecayConfig(attestation="45d", trust=_90_DAYS)  # pyright: ignore[reportArgumentType]
    assert dc.attestation == 45 * 86400.0


def test_top_level_toml_typo_raises(tmp_path: Path) -> None:
    """Unknown top-level key in TOML is caught by extra='forbid'."""
    toml_file = tmp_path / "typo.toml"
    toml_file.write_text(
        'halff_life = "90d"\n'
        '[embedding]\nmodel = "test/e"\n[fast]\nmodel = "test/f"\n[reasoning]\nmodel = "test/r"\n'
    )
    with (
        patch.dict(os.environ, _BASE_ENV, clear=True),
        pytest.raises(ValidationError, match="extra_forbidden"),
    ):
        load_settings(toml_path=toml_file)


# ---------------------------------------------------------------------------
# Chunk 6: Vendor defaults as base layer
# ---------------------------------------------------------------------------

_PARTIAL_FAST_TOML = Path(__file__).parent.parent / "fixtures" / "lore_partial_fast_override.toml"


def test_user_toml_overrides_single_vendor_model_section() -> None:
    """User provides only [fast], vendor (Gemini) fills [embedding] and [reasoning]."""
    env = {**_BASE_ENV, "GEMINI_API_KEY": "fake-key"}
    with patch.dict(os.environ, env, clear=True):
        s = load_settings(toml_path=_PARTIAL_FAST_TOML)
        assert s.fast.model == "custom/my-fast-model"
        assert s.embedding.model == "gemini/gemini-embedding-001"
        assert s.reasoning.model == "gemini/gemini-flash-latest"


def test_user_overrides_embedding_dimension_preserves_vendor_task_type(
    tmp_path: Path,
) -> None:
    """User sets [embedding] dimensions, vendor provides task_type. Deep merge preserves both."""
    toml_file = tmp_path / "override.toml"
    toml_file.write_text(
        "[decay]\n"
        'attestation = "90d"\n'
        'trust = "90d"\n\n'
        "[embedding]\n"
        'model = "gemini/gemini-embedding-001"\n'
        "dimensions = 768\n"
    )
    env = {**_BASE_ENV, "GEMINI_API_KEY": "fake-key"}
    with patch.dict(os.environ, env, clear=True):
        s = load_settings(toml_path=toml_file)
        assert s.embedding.dimensions == 768
        assert s.embedding.task_type is not None
        assert s.embedding.task_type.document == "RETRIEVAL_DOCUMENT"


def test_vendor_defaults_do_not_leak_api_key() -> None:
    """Vendor api_key is stripped during load_settings, not leaked into LoreSettings."""
    env = {**_BASE_ENV, "GEMINI_API_KEY": "fake-key"}
    with patch.dict(os.environ, env, clear=True):
        s = load_settings(toml_path=_NO_TOML)
        # If api_key leaked, model_validate with extra="forbid" would reject it.
        assert s.embedding.model == "gemini/gemini-embedding-001"
