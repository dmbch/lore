"""Tests for lore.config section configs.

Covers Decay, Trust, Limits, Retrieval, Server, Prompts, Postgres, Sqlite.
"""

import os
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import SecretStr, ValidationError

from lore.config import PostgresConfig, PromptsConfig, load_settings
from lore.config.loader import (
    _resolve_prompts,  # pyright: ignore[reportPrivateUsage]
)
from lore.config.types import (
    DecayConfig,
    LimitsConfig,
    OidcConfig,
    RetrievalConfig,
    ServerConfig,
    SqliteConfig,
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
# LimitsConfig — character limits for pipeline payloads
# ---------------------------------------------------------------------------


def test_limits_config_is_frozen() -> None:
    lc = LimitsConfig(
        question=1024,
        hypothesis=3072,
        context=4096,
        reasoning=4096,
    )
    with pytest.raises(ValidationError, match="frozen"):
        lc.question = 512  # pyright: ignore[reportAttributeAccessIssue]


def test_limits_bundled_defaults_match_spec() -> None:
    """Bundled lore.toml provides spec-compliant defaults for limits."""
    with patch.dict(os.environ, _BASE_ENV, clear=True):
        s = load_settings(toml_path=_TOML_PATH)
        assert s.limits.question == 1024
        assert s.limits.hypothesis == 3072
        assert s.limits.context == 4096
        assert s.limits.reasoning == 4096


def test_limits_config_question_zero_raises() -> None:
    with pytest.raises(ValidationError, match="question"):
        LimitsConfig(
            question=0,
            hypothesis=3072,
            context=4096,
            reasoning=4096,
        )


def test_limits_config_question_negative_raises() -> None:
    with pytest.raises(ValidationError, match="question"):
        LimitsConfig(
            question=-1,
            hypothesis=3072,
            context=4096,
            reasoning=4096,
        )


def test_limits_config_rejects_answer_key() -> None:
    """answer is no longer a config field — extra="forbid" rejects it."""
    with pytest.raises(ValidationError):
        LimitsConfig(
            question=1024,
            hypothesis=3072,
            context=4096,
            reasoning=4096,
            answer=8192,  # pyright: ignore[reportCallIssue]
        )


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


def test_retrieval_config_is_frozen() -> None:
    rc = RetrievalConfig(proximity=0.5, authority=0.5, limit=10, fan_out=2, max_keywords=10)
    with pytest.raises(ValidationError, match="frozen"):
        rc.proximity = 0.7  # pyright: ignore[reportAttributeAccessIssue]


def test_retrieval_config_weight_out_of_range_raises() -> None:
    with pytest.raises(ValidationError, match="proximity"):
        RetrievalConfig(proximity=1.5, authority=0.5, limit=10, fan_out=2, max_keywords=10)


def test_retrieval_config_limit_zero_raises() -> None:
    with pytest.raises(ValidationError, match="limit"):
        RetrievalConfig(proximity=0.5, authority=0.5, limit=0, fan_out=2, max_keywords=10)


def test_retrieval_config_max_keywords_zero_raises() -> None:
    with pytest.raises(ValidationError, match="must be > 0"):
        RetrievalConfig(proximity=0.5, authority=0.5, limit=10, fan_out=2, max_keywords=0)


def test_retrieval_config_from_toml() -> None:
    with patch.dict(os.environ, _BASE_ENV, clear=True):
        s = load_settings(toml_path=_RETRIEVAL_TOML_PATH)
        assert s.retrieval.proximity == 0.7
        assert s.retrieval.authority == 0.3
        assert s.retrieval.limit == 20
        assert s.retrieval.fan_out == 3
        assert s.retrieval.max_keywords == 10


# ---------------------------------------------------------------------------
# ServerConfig
# ---------------------------------------------------------------------------


def test_server_config_default_name() -> None:
    sc = ServerConfig()
    assert sc.name == "Lore"


def test_server_config_is_frozen() -> None:
    sc = ServerConfig()
    with pytest.raises(ValidationError, match="frozen"):
        sc.name = "Other"  # pyright: ignore[reportAttributeAccessIssue]


def test_server_config_auth_required_defaults_to_false() -> None:
    sc = ServerConfig()
    assert sc.auth_required is False


def test_server_config_icon_url_defaults_to_none() -> None:
    sc = ServerConfig()
    assert sc.icon_url is None


def test_server_config_verify_id_token_defaults_to_true() -> None:
    sc = ServerConfig()
    assert sc.verify_id_token is True


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
# OidcConfig
# ---------------------------------------------------------------------------


def test_oidc_config_extra_authorize_params_defaults_to_empty() -> None:
    oc = OidcConfig(
        discovery_url="https://auth.example.com/.well-known/openid-configuration",
        client_id="cid",
        client_secret=SecretStr("sec"),
    )
    assert oc.extra_authorize_params == {}


# ---------------------------------------------------------------------------
# PromptsConfig
# ---------------------------------------------------------------------------


def test_prompts_config_requires_bundled_paths() -> None:
    pc = PromptsConfig(
        scribe=Path("/tmp/scribe.md"),
        consult=Path("/tmp/consult.md"),
        interpreter=Path("/tmp/interpreter.md"),
        archivist=Path("/tmp/archivist.md"),
    )
    assert pc.narrative is None
    assert pc.glossary is None
    assert pc.scribe == Path("/tmp/scribe.md")
    assert pc.consult == Path("/tmp/consult.md")
    assert pc.interpreter == Path("/tmp/interpreter.md")
    assert pc.archivist == Path("/tmp/archivist.md")


def test_prompts_config_accepts_narrative_and_glossary() -> None:
    pc = PromptsConfig(
        narrative=Path("/tmp/narrative.md"),
        glossary=Path("/tmp/glossary.md"),
        scribe=Path("/tmp/scribe.md"),
        consult=Path("/tmp/consult.md"),
        interpreter=Path("/tmp/interpreter.md"),
        archivist=Path("/tmp/archivist.md"),
    )
    assert pc.narrative == Path("/tmp/narrative.md")
    assert pc.glossary == Path("/tmp/glossary.md")


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


def test_postgres_config_is_frozen() -> None:
    pc = PostgresConfig(min_size=1, max_size=20, getconn_timeout=10.0, max_waiting=50)
    with pytest.raises(ValidationError, match="frozen"):
        pc.max_size = 30  # pyright: ignore[reportAttributeAccessIssue]


def test_postgres_config_max_size_below_min_size_raises() -> None:
    with pytest.raises(ValidationError, match="max_size"):
        PostgresConfig(min_size=10, max_size=5, getconn_timeout=10.0, max_waiting=50)


def test_postgres_config_min_size_zero_raises() -> None:
    with pytest.raises(ValidationError, match="min_size"):
        PostgresConfig(min_size=0, max_size=20, getconn_timeout=10.0, max_waiting=50)


def test_postgres_config_max_size_zero_raises() -> None:
    with pytest.raises(ValidationError, match="max_size"):
        PostgresConfig(min_size=1, max_size=0, getconn_timeout=10.0, max_waiting=50)


def test_postgres_config_getconn_timeout_zero_raises() -> None:
    with pytest.raises(ValidationError, match="getconn_timeout"):
        PostgresConfig(min_size=1, max_size=20, getconn_timeout=0.0, max_waiting=50)


def test_postgres_config_getconn_timeout_negative_raises() -> None:
    with pytest.raises(ValidationError, match="getconn_timeout"):
        PostgresConfig(min_size=1, max_size=20, getconn_timeout=-1.0, max_waiting=50)


def test_postgres_config_max_waiting_negative_raises() -> None:
    with pytest.raises(ValidationError, match="max_waiting"):
        PostgresConfig(min_size=1, max_size=20, getconn_timeout=10.0, max_waiting=-1)


def test_postgres_config_max_waiting_zero_is_valid_unlimited() -> None:
    """max_waiting=0 is psycopg's unlimited-queue mode; valid but not the default."""
    pc = PostgresConfig(min_size=1, max_size=20, getconn_timeout=10.0, max_waiting=0)
    assert pc.max_waiting == 0


def test_postgres_config_rejects_unknown_keys() -> None:
    with pytest.raises(ValidationError, match="extra"):
        PostgresConfig(
            min_size=1,
            max_size=20,
            getconn_timeout=10.0,
            max_waiting=50,
            num_workers=4,  # pyright: ignore[reportCallIssue]
        )


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


@pytest.mark.parametrize("value", ["English", "; DROP TABLE", "naïve", "", "1simple"])
def test_postgres_config_fulltext_config_rejects_unsafe_values(value: str) -> None:
    with pytest.raises(ValidationError, match="fulltext_config"):
        PostgresConfig(
            min_size=1,
            max_size=20,
            getconn_timeout=10.0,
            max_waiting=50,
            fulltext_config=value,
        )


@pytest.mark.parametrize("value", ["english", "german", "french", "simple", "english_stem"])
def test_postgres_config_fulltext_config_accepts_valid_regconfigs(value: str) -> None:
    pc = PostgresConfig(
        min_size=1,
        max_size=20,
        getconn_timeout=10.0,
        max_waiting=50,
        fulltext_config=value,
    )
    assert pc.fulltext_config == value


# ---------------------------------------------------------------------------
# SqliteConfig — FTS5 tokenize spec
# ---------------------------------------------------------------------------


def test_sqlite_config_fulltext_config_default_porter_unicode61() -> None:
    with patch.dict(os.environ, _BASE_ENV, clear=True):
        s = load_settings(toml_path=_TOML_PATH)
        assert s.sqlite.fulltext_config == "porter unicode61"


@pytest.mark.parametrize("value", ["Unicode61", "porter'; DROP TABLE --", "naïve", ""])
def test_sqlite_config_fulltext_config_rejects_unsafe_values(value: str) -> None:
    with pytest.raises(ValidationError, match="fulltext_config"):
        SqliteConfig(fulltext_config=value)


@pytest.mark.parametrize(
    "value", ["unicode61", "porter unicode61", "ascii", "unicode61 remove_diacritics 1"]
)
def test_sqlite_config_fulltext_config_accepts_valid_fts5_specs(value: str) -> None:
    sc = SqliteConfig(fulltext_config=value)
    assert sc.fulltext_config == value
