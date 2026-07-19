"""Tests for lore.config section configs: loader integration.

Covers the Epistemics, Limits, Retrieval, Cache, Server, Prompts, Postgres,
and Sqlite sections as they map through ``load_settings``. Pure
model-construction tests for ``EpistemicsConfig`` live in
``tests/math/test_config.py`` (the math layer owns that type); ``CacheConfig``
construction tests live in ``tests/repositories/test_config.py``.
"""

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from lore.config import ConfigurationError, load_settings
from lore.config.loader import (
    _resolve_prompts,  # pyright: ignore[reportPrivateUsage]
)

# Minimal valid env for most tests. DATABASE_URL is the only DSN env var.
_BASE_ENV = {"DATABASE_URL": "sqlite:///test.db"}

# Path to the test TOML fixture.
_TOML_PATH = Path(__file__).parent.parent / "fixtures" / "lore.toml"


# ---------------------------------------------------------------------------
# Epistemics section: loader integration (model construction tests live in
# tests/math/test_config.py)
# ---------------------------------------------------------------------------

_EPISTEMICS_TOML_PATH = Path(__file__).parent.parent / "fixtures" / "lore_trust.toml"


def test_epistemics_maturity_defaults_without_toml_section() -> None:
    with patch.dict(os.environ, _BASE_ENV, clear=True):
        s = load_settings(toml_path=_TOML_PATH)
        assert s.epistemics.maturity_k == 1


def test_epistemics_maturity_from_toml() -> None:
    with patch.dict(os.environ, _BASE_ENV, clear=True):
        s = load_settings(toml_path=_EPISTEMICS_TOML_PATH)
        assert s.epistemics.maturity_k == 3


def test_epistemics_attestation_half_life_independent_of_trust() -> None:
    with patch.dict(os.environ, _BASE_ENV, clear=True):
        s = load_settings(toml_path=_EPISTEMICS_TOML_PATH)
        assert s.epistemics.attestation_half_life != s.epistemics.trust_half_life


# ---------------------------------------------------------------------------
# LimitsConfig: character limits for pipeline payloads (loader integration;
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
# LimitsConfig: TOML integration
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


def test_cache_bundled_default_is_hourly() -> None:
    with patch.dict(os.environ, _BASE_ENV, clear=True):
        s = load_settings(toml_path=_TOML_PATH)
        assert s.cache.sweep_interval == 3600.0


def test_cache_sweep_interval_from_toml(tmp_path: Path) -> None:
    toml_file = tmp_path / "cache.toml"
    toml_file.write_text(
        '[cache]\nsweep_interval = "30m"\n'
        '[embedding]\nmodel = "test/e"\n[fast]\nmodel = "test/f"\n[reasoning]\nmodel = "test/r"\n'
    )
    with patch.dict(os.environ, _BASE_ENV, clear=True):
        s = load_settings(toml_path=toml_file)
        assert s.cache.sweep_interval == 1800.0


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
        pytest.raises(ConfigurationError, match=r"sum to 1\.0"),
    ):
        load_settings(toml_path=toml_file)


# ---------------------------------------------------------------------------
# AuthConfig: loader integration (model construction tests live in
# tests/adapter/test_config.py)
# ---------------------------------------------------------------------------


def test_auth_config_required_round_trips_from_toml(tmp_path: Path) -> None:
    # ``required = true`` is only a valid load state with OIDC configured.
    # The cross-section validator refuses ``required`` without ``oidc``.
    toml_file = tmp_path / "auth.toml"
    toml_file.write_text(
        "[auth]\nrequired = true\n"
        '[embedding]\nmodel = "test/e"\n[fast]\nmodel = "test/f"\n[reasoning]\nmodel = "test/r"\n'
    )
    env = {
        **_BASE_ENV,
        "OIDC_URL": "oidc://client:secret@auth.example.com/.well-known/openid-configuration",
        "BASE_URL": "https://lore.example.com",
    }
    with patch.dict(os.environ, env, clear=True):
        s = load_settings(toml_path=toml_file)
        assert s.auth.required is True


# ---------------------------------------------------------------------------
# PromptsConfig: loader resolution (model construction tests live in
# tests/prompts/test_prompts.py, alongside the layer that owns the model)
# ---------------------------------------------------------------------------


def test_load_settings_resolves_bundled_prompts() -> None:
    with patch.dict(os.environ, _BASE_ENV, clear=True):
        s = load_settings(toml_path=_TOML_PATH)
    for name in ("scribe", "interpreter", "archivist"):
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
# PostgresConfig: pool sizing and timeouts
# ---------------------------------------------------------------------------

_POSTGRES_TOML_PATH = Path(__file__).parent.parent / "fixtures" / "lore_postgres.toml"


def test_postgres_config_bundled_defaults_match_locked_positions() -> None:
    """Bundled lore.toml provides the locked default pool tunables."""
    with patch.dict(os.environ, _BASE_ENV, clear=True):
        s = load_settings(toml_path=_TOML_PATH)
        assert s.postgres.min_size == 1
        assert s.postgres.max_size == 20
        assert s.postgres.timeout == 10.0
        assert s.postgres.max_waiting == 50


def test_postgres_config_from_toml_overrides_defaults() -> None:
    with patch.dict(os.environ, _BASE_ENV, clear=True):
        s = load_settings(toml_path=_POSTGRES_TOML_PATH)
        assert s.postgres.min_size == 2
        assert s.postgres.max_size == 50
        assert s.postgres.timeout == 5.0
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
        pytest.raises(ConfigurationError, match=r"postgres\.num_workers"),
    ):
        load_settings(toml_path=toml_file)


def test_postgres_config_fulltext_config_default_english() -> None:
    with patch.dict(os.environ, _BASE_ENV, clear=True):
        s = load_settings(toml_path=_TOML_PATH)
        assert s.postgres.fulltext_config == "english"


# ---------------------------------------------------------------------------
# SqliteConfig: FTS5 tokenize spec
# ---------------------------------------------------------------------------


def test_sqlite_config_fulltext_config_default_porter_unicode61() -> None:
    with patch.dict(os.environ, _BASE_ENV, clear=True):
        s = load_settings(toml_path=_TOML_PATH)
        assert s.sqlite.fulltext_config == "porter unicode61"
