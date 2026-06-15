"""Tests for lore.config section configs.

Covers Decay, Trust, Limits, Retrieval, Server, Prompts, Postgres, Sqlite.
"""

import os
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from lore.config import load_settings
from lore.config.loader import (
    _resolve_prompts,  # pyright: ignore[reportPrivateUsage]
)
from lore.config.types import (
    DecayConfig,
    TrustConfig,
)

# Minimal valid env for most tests — DATABASE_URL is the only DSN env var.
_BASE_ENV = {"DATABASE_URL": "sqlite:///test.db"}

# Path to the test TOML fixture.
_TOML_PATH = Path(__file__).parent.parent / "fixtures" / "lore.toml"

_90_DAYS = 90 * 86400.0


# ---------------------------------------------------------------------------
# DecayConfig
# ---------------------------------------------------------------------------


def test_decay_config_is_frozen() -> None:
    dc = DecayConfig(attestation=_90_DAYS, trust=_90_DAYS)
    with pytest.raises(ValidationError, match="frozen"):
        dc.attestation = 1.0  # pyright: ignore[reportAttributeAccessIssue]


def test_decay_config_zero_attestation_raises() -> None:
    with pytest.raises(ValidationError, match="must be positive"):
        DecayConfig(attestation=0, trust=_90_DAYS)


def test_decay_config_zero_trust_raises() -> None:
    with pytest.raises(ValidationError, match="must be positive"):
        DecayConfig(attestation=_90_DAYS, trust=0)


# ---------------------------------------------------------------------------
# Trust config
# ---------------------------------------------------------------------------

_TRUST_TOML_PATH = Path(__file__).parent.parent / "fixtures" / "lore_trust.toml"
_45_DAYS = 45 * 86400.0


def test_trust_config_defaults_without_toml_section() -> None:
    with patch.dict(os.environ, _BASE_ENV, clear=True):
        s = load_settings(toml_path=_TOML_PATH)
        assert s.trust.maturity == 1


def test_trust_config_from_toml() -> None:
    with patch.dict(os.environ, _BASE_ENV, clear=True):
        s = load_settings(toml_path=_TRUST_TOML_PATH)
        assert s.trust.maturity == 3


def test_trust_config_maturity_zero_is_valid_transparent_mode() -> None:
    tc = TrustConfig(maturity=0)
    assert tc.maturity == 0


def test_trust_config_maturity_negative_raises() -> None:
    with pytest.raises(ValueError, match="maturity"):
        TrustConfig(maturity=-1)


def test_trust_config_threshold_defaults_to_one_thousandth() -> None:
    tc = TrustConfig(maturity=1.0)
    assert tc.threshold == 1e-3


def test_trust_config_threshold_zero_raises() -> None:
    with pytest.raises(ValueError, match="threshold"):
        TrustConfig(maturity=1.0, threshold=0)


def test_trust_config_threshold_negative_raises() -> None:
    with pytest.raises(ValueError, match="threshold"):
        TrustConfig(maturity=1.0, threshold=-1e-3)


def test_trust_config_rejects_alignment_weight() -> None:
    """alignment_weight is no longer a config field — extra="forbid" rejects it."""
    with pytest.raises(ValidationError):
        TrustConfig(
            maturity=1.0,
            alignment_weight=0.5,  # pyright: ignore[reportCallIssue]
        )


def test_decay_attestation_independent_of_trust() -> None:
    with patch.dict(os.environ, _BASE_ENV, clear=True):
        s = load_settings(toml_path=_TRUST_TOML_PATH)
        assert s.decay.attestation != s.decay.trust


# ---------------------------------------------------------------------------
# LimitsConfig — character limits for pipeline payloads (loader integration;
# model construction tests live in tests/adapter/test_config.py)
# ---------------------------------------------------------------------------


def test_limits_bundled_defaults_match_spec() -> None:
    """Bundled lore.toml provides spec-compliant defaults for limits."""
    with patch.dict(os.environ, _BASE_ENV, clear=True):
        s = load_settings(toml_path=_TOML_PATH)
        assert s.limits.question == 1024
        assert s.limits.hypothesis == 3072
        assert s.limits.context == 4096
        assert s.limits.reasoning == 4096


# ---------------------------------------------------------------------------
# LimitsConfig — TOML integration
# ---------------------------------------------------------------------------

_LIMITS_TOML_PATH = Path(__file__).parent.parent / "fixtures" / "lore_limits.toml"


def test_limits_config_from_toml() -> None:
    with patch.dict(os.environ, _BASE_ENV, clear=True):
        s = load_settings(toml_path=_LIMITS_TOML_PATH)
        assert s.limits.question == 2048
        assert s.limits.hypothesis == 4096
        assert s.limits.context == 8192
        assert s.limits.reasoning == 8192
        assert s.retrieval.max_keywords == 20


def test_limits_config_defaults_when_toml_section_absent() -> None:
    with patch.dict(os.environ, _BASE_ENV, clear=True):
        s = load_settings(toml_path=_TOML_PATH)
        assert s.limits.question == 1024
        assert s.retrieval.max_keywords == 10


# ---------------------------------------------------------------------------
# RetrievalConfig
# ---------------------------------------------------------------------------

_RETRIEVAL_TOML_PATH = Path(__file__).parent.parent / "fixtures" / "lore_retrieval.toml"


def test_retrieval_bundled_defaults_match_spec() -> None:
    """Bundled lore.toml provides spec-compliant defaults for retrieval."""
    with patch.dict(os.environ, _BASE_ENV, clear=True):
        s = load_settings(toml_path=_TOML_PATH)
        assert s.retrieval.proximity == 0.5
        assert s.retrieval.authority == 0.5
        assert s.retrieval.limit == 10
        assert s.retrieval.fan_out == 2
        assert s.retrieval.max_keywords == 10


def test_retrieval_config_from_toml() -> None:
    with patch.dict(os.environ, _BASE_ENV, clear=True):
        s = load_settings(toml_path=_RETRIEVAL_TOML_PATH)
        assert s.retrieval.proximity == 0.7
        assert s.retrieval.authority == 0.3
        assert s.retrieval.limit == 20
        assert s.retrieval.fan_out == 3
        assert s.retrieval.max_keywords == 10


def test_load_settings_rejects_partial_weight_override(tmp_path: Path) -> None:
    """A user TOML overriding only one lane weight must fail at load, not first consult.

    Deep merge would otherwise silently combine the user's `proximity = 0.7`
    with the bundled default `authority = 0.5`.
    """
    toml_file = tmp_path / "partial_retrieval.toml"
    toml_file.write_text("[retrieval]\nproximity = 0.7\n")
    env = {**_BASE_ENV, "GEMINI_API_KEY": "fake-key"}
    with (
        patch.dict(os.environ, env, clear=True),
        pytest.raises(ValidationError, match=r"sum to 1\.0"),
    ):
        load_settings(toml_path=toml_file)


# ---------------------------------------------------------------------------
# ServerConfig — loader integration (model construction tests live in
# tests/adapter/test_config.py)
# ---------------------------------------------------------------------------


def test_server_config_auth_required_round_trips_from_toml(tmp_path: Path) -> None:
    toml_file = tmp_path / "auth.toml"
    toml_file.write_text(
        "[server]\nauth_required = true\n"
        '[embedding]\nmodel = "test/e"\n[fast]\nmodel = "test/f"\n[reasoning]\nmodel = "test/r"\n'
    )
    with patch.dict(os.environ, _BASE_ENV, clear=True):
        s = load_settings(toml_path=toml_file)
        assert s.server.auth_required is True


# ---------------------------------------------------------------------------
# PromptsConfig — loader resolution (model construction tests live in
# tests/prompts/test_prompts.py, alongside the layer that owns the model)
# ---------------------------------------------------------------------------


def test_load_settings_resolves_bundled_prompts() -> None:
    with patch.dict(os.environ, _BASE_ENV, clear=True):
        s = load_settings(toml_path=_TOML_PATH)
    for name in ("scribe", "consult", "interpreter", "archivist"):
        path = getattr(s.prompts, name)
        assert isinstance(path, Path)
        assert path.name == f"{name}.md"


def test_load_settings_user_toml_overrides_prompt_path(tmp_path: Path) -> None:
    custom = tmp_path / "my_scribe.md"
    custom.write_text("custom")
    toml_file = tmp_path / "lore.toml"
    toml_content = _TOML_PATH.read_text() + f'\n[prompts]\nscribe = "{custom}"\n'
    toml_file.write_text(toml_content)
    with patch.dict(os.environ, _BASE_ENV, clear=True):
        s = load_settings(toml_path=toml_file)
    assert s.prompts.scribe == custom


def test_resolve_prompts_passes_non_string_values_through() -> None:
    result = _resolve_prompts({"narrative": None})
    assert result == {"narrative": None}


# ---------------------------------------------------------------------------
# PostgresConfig — pool sizing and timeouts
# ---------------------------------------------------------------------------

_POSTGRES_TOML_PATH = Path(__file__).parent.parent / "fixtures" / "lore_postgres.toml"


def test_postgres_config_bundled_defaults_match_locked_positions() -> None:
    """Bundled lore.toml provides the locked default pool tunables."""
    with patch.dict(os.environ, _BASE_ENV, clear=True):
        s = load_settings(toml_path=_TOML_PATH)
        assert s.postgres.min_size == 1
        assert s.postgres.max_size == 20
        assert s.postgres.getconn_timeout == 10.0
        assert s.postgres.max_waiting == 50


def test_postgres_config_from_toml_overrides_defaults() -> None:
    with patch.dict(os.environ, _BASE_ENV, clear=True):
        s = load_settings(toml_path=_POSTGRES_TOML_PATH)
        assert s.postgres.min_size == 2
        assert s.postgres.max_size == 50
        assert s.postgres.getconn_timeout == 5.0
        assert s.postgres.max_waiting == 100


def test_postgres_config_unknown_key_in_toml_raises(tmp_path: Path) -> None:
    """An unknown key under [postgres] is rejected by extra='forbid'."""
    toml_file = tmp_path / "bad_postgres.toml"
    toml_file.write_text(
        '[embedding]\nmodel = "test/e"\n[fast]\nmodel = "test/f"\n'
        '[reasoning]\nmodel = "test/r"\n'
        "[postgres]\nnum_workers = 4\n"
    )
    with (
        patch.dict(os.environ, _BASE_ENV, clear=True),
        pytest.raises(ValidationError, match="extra_forbidden"),
    ):
        load_settings(toml_path=toml_file)


def test_postgres_config_fulltext_config_default_english() -> None:
    with patch.dict(os.environ, _BASE_ENV, clear=True):
        s = load_settings(toml_path=_TOML_PATH)
        assert s.postgres.fulltext_config == "english"


# ---------------------------------------------------------------------------
# SqliteConfig — FTS5 tokenize spec
# ---------------------------------------------------------------------------


def test_sqlite_config_fulltext_config_default_porter_unicode61() -> None:
    with patch.dict(os.environ, _BASE_ENV, clear=True):
        s = load_settings(toml_path=_TOML_PATH)
        assert s.sqlite.fulltext_config == "porter unicode61"
