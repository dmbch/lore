"""Tests for lore.config loader — load_settings, vendor detection, OIDC parsing."""

import os
from pathlib import Path
from unittest.mock import patch

import pytest
import structlog
from pydantic import ValidationError

from lore.config import load_settings
from lore.config.loader import discover_toml, parse_oidc_url
from lore.config.types import DecayConfig, OidcConfig

# Minimal valid env for most tests — DATABASE_URL is the only DSN env var.
_BASE_ENV = {"DATABASE_URL": "sqlite:///test.db"}

# Path to the test TOML fixture.
_TOML_PATH = Path(__file__).parent.parent / "fixtures" / "lore.toml"

# A nonexistent path — forces vendor detection or error.
_NO_TOML = Path(__file__).parent.parent / "fixtures" / "nonexistent.toml"

_30_DAYS = 30 * 86400.0
_90_DAYS = 90 * 86400.0


# ---------------------------------------------------------------------------
# DSN detection — DATABASE_URL only, scheme drives backend dispatch
# ---------------------------------------------------------------------------


def test_settings_no_dsn_env_raises() -> None:
    with (
        patch.dict(os.environ, {}, clear=True),
        pytest.raises(ValueError, match="DATABASE_URL"),
    ):
        load_settings(toml_path=_TOML_PATH)


def test_settings_database_url_postgres_scheme() -> None:
    env = {"DATABASE_URL": "postgresql://localhost/lore"}
    with patch.dict(os.environ, env, clear=True):
        s = load_settings(toml_path=_TOML_PATH)
        assert s.dsn == "postgresql://localhost/lore"


def test_settings_database_url_sqlite_scheme() -> None:
    env = {"DATABASE_URL": "sqlite:///x.db"}
    with patch.dict(os.environ, env, clear=True):
        s = load_settings(toml_path=_TOML_PATH)
        assert s.dsn == "sqlite:///x.db"


def test_settings_empty_dsn_raises() -> None:
    env = {"DATABASE_URL": "  "}
    with (
        patch.dict(os.environ, env, clear=True),
        pytest.raises(ValueError, match="DATABASE_URL"),
    ):
        load_settings(toml_path=_TOML_PATH)


# ---------------------------------------------------------------------------
# BASE_URL / OIDC_URL pairing — must be both or neither
# ---------------------------------------------------------------------------

_OIDC_URL = "oidc://client:secret@auth.example.com/.well-known/openid-configuration"


def test_base_url_without_oidc_url_raises() -> None:
    env = {**_BASE_ENV, "BASE_URL": "https://lore.example.com"}
    with (
        patch.dict(os.environ, env, clear=True),
        pytest.raises(ValueError, match="BASE_URL requires OIDC_URL"),
    ):
        load_settings(toml_path=_TOML_PATH)


def test_oidc_url_without_base_url_raises() -> None:
    env = {**_BASE_ENV, "OIDC_URL": _OIDC_URL}
    with (
        patch.dict(os.environ, env, clear=True),
        pytest.raises(ValueError, match="OIDC_URL requires BASE_URL"),
    ):
        load_settings(toml_path=_TOML_PATH)


def test_both_set_returns_http_config() -> None:
    env = {**_BASE_ENV, "BASE_URL": "https://lore.example.com", "OIDC_URL": _OIDC_URL}
    with patch.dict(os.environ, env, clear=True):
        s = load_settings(toml_path=_TOML_PATH)
        assert s.base_url == "https://lore.example.com"
        assert s.oidc is not None
        assert s.oidc.client_id == "client"


def test_neither_set_returns_stdio_config() -> None:
    with patch.dict(os.environ, _BASE_ENV, clear=True):
        s = load_settings(toml_path=_TOML_PATH)
        assert s.base_url is None
        assert s.oidc is None


# ---------------------------------------------------------------------------
# TOML discovery — ./lore.toml then /etc/lore.toml
# ---------------------------------------------------------------------------


def test_settings_from_toml() -> None:
    with patch.dict(os.environ, _BASE_ENV, clear=True):
        s = load_settings(toml_path=_TOML_PATH)
        assert s.decay.attestation == _30_DAYS
        assert s.embedding.model == "test/embedding-model"
        assert s.fast.model == "test/fast-model"
        assert s.reasoning.model == "test/reasoning-model"


def test_half_life_default_without_toml() -> None:
    env = {**_BASE_ENV, "GEMINI_API_KEY": "fake-key"}
    with patch.dict(os.environ, env, clear=True):
        s = load_settings(toml_path=_NO_TOML)
        assert s.decay.attestation == _90_DAYS


def test_discover_toml_first_candidate_wins(tmp_path: Path) -> None:
    first = tmp_path / "first.toml"
    second = tmp_path / "second.toml"
    first.write_text("[embedding]\nmodel = 'first'\n")
    second.write_text("[embedding]\nmodel = 'second'\n")
    assert discover_toml(candidates=(first, second)) == first


def test_discover_toml_skips_missing(tmp_path: Path) -> None:
    missing = tmp_path / "missing.toml"
    present = tmp_path / "present.toml"
    present.write_text("[embedding]\nmodel = 'found'\n")
    assert discover_toml(candidates=(missing, present)) == present


def test_discover_toml_none_when_all_missing(tmp_path: Path) -> None:
    assert discover_toml(candidates=(tmp_path / "a.toml", tmp_path / "b.toml")) is None


def test_toml_discovered_end_to_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """load_settings() discovers ./lore.toml from the working directory."""
    toml_file = tmp_path / "lore.toml"
    toml_file.write_text(
        '[embedding]\nmodel = "test/e"\n[fast]\nmodel = "test/f"\n[reasoning]\nmodel = "test/r"\n'
    )
    monkeypatch.chdir(tmp_path)
    env = {**_BASE_ENV}
    with patch.dict(os.environ, env, clear=True):
        s = load_settings()
        assert s.embedding.model == "test/e"


def test_toml_no_file_uses_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When no lore.toml exists, vendor defaults apply."""
    monkeypatch.chdir(tmp_path)
    env = {**_BASE_ENV, "GEMINI_API_KEY": "fake-key"}
    with patch.dict(os.environ, env, clear=True):
        s = load_settings()
        assert s.decay.attestation == _90_DAYS
        assert "gemini/" in s.embedding.model


# ---------------------------------------------------------------------------
# Env does NOT override TOML-only fields
# ---------------------------------------------------------------------------


def test_env_does_not_override_half_life() -> None:
    env = {**_BASE_ENV, "LORE_HALF_LIFE": "0.05"}
    with patch.dict(os.environ, env, clear=True):
        s = load_settings(toml_path=_TOML_PATH)
        # half_life comes from TOML, not env.
        assert s.decay.attestation == _30_DAYS


# ---------------------------------------------------------------------------
# Vendor auto-detection
# ---------------------------------------------------------------------------


def test_vendor_detection_gemini() -> None:
    env = {**_BASE_ENV, "GEMINI_API_KEY": "fake-key"}
    with patch.dict(os.environ, env, clear=True):
        s = load_settings(toml_path=_NO_TOML)
        assert "gemini/" in s.embedding.model
        assert "gemini/" in s.fast.model
        assert "gemini/" in s.reasoning.model


def test_vendor_detection_openai() -> None:
    env = {**_BASE_ENV, "OPENAI_API_KEY": "fake-key"}
    with patch.dict(os.environ, env, clear=True):
        s = load_settings(toml_path=_NO_TOML)
        assert s.embedding.model == "text-embedding-3-small"
        assert s.fast.model == "gpt-4.1-mini"
        assert s.reasoning.model == "o4-mini"


def test_vendor_detection_bedrock() -> None:
    env = {**_BASE_ENV, "AWS_BEARER_TOKEN_BEDROCK": "fake-key"}
    with patch.dict(os.environ, env, clear=True):
        s = load_settings(toml_path=_NO_TOML)
        assert "bedrock/" in s.embedding.model


def test_vendor_detection_priority_lexical() -> None:
    """Lexical order: bedrock < gemini — bedrock wins when both keys are set."""
    env = {
        **_BASE_ENV,
        "AWS_BEARER_TOKEN_BEDROCK": "fake-aws",
        "GEMINI_API_KEY": "fake-gemini",
    }
    with patch.dict(os.environ, env, clear=True):
        s = load_settings(toml_path=_NO_TOML)
        assert "bedrock/" in s.embedding.model


def test_toml_overrides_vendor_defaults() -> None:
    env = {**_BASE_ENV, "OPENAI_API_KEY": "fake-key"}
    with patch.dict(os.environ, env, clear=True):
        s = load_settings(toml_path=_TOML_PATH)
        # TOML says test models; OpenAI key is present but TOML wins.
        assert s.embedding.model == "test/embedding-model"


def test_no_vendor_no_toml_models_raises() -> None:
    with (
        patch.dict(os.environ, _BASE_ENV, clear=True),
        pytest.raises(ValidationError, match="fast"),
    ):
        load_settings(toml_path=_NO_TOML)


def test_no_vendor_partial_toml_raises() -> None:
    partial = Path(__file__).parent.parent / "fixtures" / "lore_embedding_only.toml"
    with (
        patch.dict(os.environ, _BASE_ENV, clear=True),
        pytest.raises(ValidationError, match="fast"),
    ):
        load_settings(toml_path=partial)


def test_embedding_section_without_model_raises() -> None:
    """[embedding] with no 'model' key raises instead of a raw KeyError."""
    no_model = Path(__file__).parent.parent / "fixtures" / "lore_embedding_no_model.toml"
    with (
        patch.dict(os.environ, _BASE_ENV, clear=True),
        pytest.raises(ValidationError, match="model"),
    ):
        load_settings(toml_path=no_model)


def test_nested_toml_value_passes_through_to_model_extra() -> None:
    """Model-role configs accept unknown sub-tables as pass-through extras.

    Regression of the old ``extra='forbid'`` behavior — vendor-specific
    LiteLLM kwargs flow through ``model_dump`` without typed-field
    schema changes (see ``docs/architecture.md`` §LLM Providers).
    """
    nested = Path(__file__).parent.parent / "fixtures" / "lore_nested_value.toml"
    with patch.dict(os.environ, _BASE_ENV, clear=True):
        s = load_settings(toml_path=nested)
        # ``options`` lives in ``[fast]``; round-trip via model_dump.
        assert s.fast.model_dump()["options"] == {"stream": True}


# ---------------------------------------------------------------------------
# OIDC URL parsing
# ---------------------------------------------------------------------------


def test_oidc_url_parsing() -> None:
    url = "oidc://my-client:s3cret@auth.example.com/.well-known/openid-configuration"
    result = parse_oidc_url(url)
    assert isinstance(result, OidcConfig)
    assert result.discovery_url == "https://auth.example.com/.well-known/openid-configuration"
    assert result.client_id == "my-client"
    # SecretStr round-trip: get_secret_value() at the boundary, repr is masked.
    assert result.client_secret.get_secret_value() == "s3cret"


def test_oidc_url_with_port() -> None:
    url = "oidc://cid:sec@auth.local:8443/.well-known/openid-configuration"
    result = parse_oidc_url(url)
    assert result.discovery_url == "https://auth.local:8443/.well-known/openid-configuration"


def test_oidc_url_encoded_password() -> None:
    url = "oidc://cid:p%40ss%3Aw0rd@auth.example.com/.well-known/openid-configuration"
    result = parse_oidc_url(url)
    assert result.client_secret.get_secret_value() == "p@ss:w0rd"
    assert result.client_id == "cid"


def test_oidc_url_with_query_string_preserves_query() -> None:
    url = "oidc://cid:sec@auth.example.com/.well-known/openid-configuration?foo=bar"
    result = parse_oidc_url(url)
    assert result.discovery_url == (
        "https://auth.example.com/.well-known/openid-configuration?foo=bar"
    )


def test_oidc_url_with_fragment_preserves_fragment() -> None:
    url = "oidc://cid:sec@auth.example.com/path#frag"
    result = parse_oidc_url(url)
    assert result.discovery_url == "https://auth.example.com/path#frag"


def test_oidc_url_missing_credentials() -> None:
    with pytest.raises(ValueError, match="client_id:client_secret"):
        parse_oidc_url("oidc://auth.example.com/.well-known/openid-configuration")


def test_oidc_url_none() -> None:
    with patch.dict(os.environ, _BASE_ENV, clear=True):
        s = load_settings(toml_path=_TOML_PATH)
        assert s.oidc is None


def test_oidc_url_from_env() -> None:
    url = "oidc://cid:sec@auth.example.com/.well-known/openid-configuration"
    env = {**_BASE_ENV, "OIDC_URL": url, "BASE_URL": "https://lore.example.com"}
    with patch.dict(os.environ, env, clear=True):
        s = load_settings(toml_path=_TOML_PATH)
        assert s.oidc is not None
        assert s.oidc.client_id == "cid"


# ---------------------------------------------------------------------------
# Transport-mode logging routes through the module-level structlog logger
# ---------------------------------------------------------------------------


def test_load_settings_logs_transport_mode_stdio() -> None:
    """When OIDC is absent, the loader emits a structured stdio transport event."""
    with (
        structlog.testing.capture_logs() as cap,
        patch.dict(os.environ, _BASE_ENV, clear=True),
    ):
        load_settings(toml_path=_TOML_PATH)
    assert any(e["event"] == "transport_mode" and e["mode"] == "stdio" for e in cap)


def test_load_settings_logs_transport_mode_http() -> None:
    """When OIDC + BASE_URL are set, the loader emits the http transport event."""
    env = {**_BASE_ENV, "BASE_URL": "https://lore.example.com", "OIDC_URL": _OIDC_URL}
    with (
        structlog.testing.capture_logs() as cap,
        patch.dict(os.environ, env, clear=True),
    ):
        load_settings(toml_path=_TOML_PATH)
    assert any(
        e["event"] == "transport_mode"
        and e["mode"] == "http"
        and e["base_url"] == "https://lore.example.com"
        for e in cap
    )


# ---------------------------------------------------------------------------
# Half-life parsing (duration strings → seconds) — tested through types
# ---------------------------------------------------------------------------


def test_half_life_bare_float() -> None:
    dc = DecayConfig(attestation=86400.0, trust=_90_DAYS)
    assert dc.attestation == 86400.0


def test_half_life_bare_int() -> None:
    dc = DecayConfig(attestation=3600, trust=_90_DAYS)
    assert dc.attestation == 3600.0


def test_half_life_hours_string() -> None:
    dc = DecayConfig(attestation="24h", trust=_90_DAYS)  # pyright: ignore[reportArgumentType]
    assert dc.attestation == 86400.0


def test_half_life_days_string() -> None:
    dc = DecayConfig(attestation="90d", trust=_90_DAYS)  # pyright: ignore[reportArgumentType]
    assert dc.attestation == _90_DAYS


def test_half_life_months_string() -> None:
    dc = DecayConfig(attestation="3M", trust=_90_DAYS)  # pyright: ignore[reportArgumentType]
    assert dc.attestation == 3 * 2592000.0


def test_half_life_years_string() -> None:
    dc = DecayConfig(attestation="1y", trust=_90_DAYS)  # pyright: ignore[reportArgumentType]
    assert dc.attestation == 31536000.0


def test_half_life_fractional() -> None:
    dc = DecayConfig(attestation="1.5h", trust=_90_DAYS)  # pyright: ignore[reportArgumentType]
    assert dc.attestation == 5400.0


def test_half_life_whitespace() -> None:
    dc = DecayConfig(attestation="  90d  ", trust=_90_DAYS)  # pyright: ignore[reportArgumentType]
    assert dc.attestation == _90_DAYS


def test_half_life_invalid_unit_raises() -> None:
    with pytest.raises(ValidationError, match="invalid half_life"):
        DecayConfig(attestation="90x", trust=_90_DAYS)  # pyright: ignore[reportArgumentType]


def test_half_life_seconds_string() -> None:
    dc = DecayConfig(attestation="3600s", trust=_90_DAYS)  # pyright: ignore[reportArgumentType]
    assert dc.attestation == 3600.0


def test_half_life_minutes_string() -> None:
    dc = DecayConfig(attestation="60m", trust=_90_DAYS)  # pyright: ignore[reportArgumentType]
    assert dc.attestation == 3600.0


def test_half_life_invalid_string_raises() -> None:
    with pytest.raises(ValidationError, match="invalid half_life"):
        DecayConfig(attestation="hello", trust=_90_DAYS)  # pyright: ignore[reportArgumentType]


def test_half_life_negative_raises() -> None:
    with pytest.raises(ValidationError, match="must be positive"):
        DecayConfig(attestation=-1.0, trust=_90_DAYS)


def test_half_life_zero_raises() -> None:
    with pytest.raises(ValidationError, match="must be positive"):
        DecayConfig(attestation=0, trust=_90_DAYS)


def test_half_life_duration_string_in_toml() -> None:
    toml_path = Path(__file__).parent.parent / "fixtures" / "lore_halflife.toml"
    with patch.dict(os.environ, _BASE_ENV, clear=True):
        s = load_settings(toml_path=toml_path)
        assert s.decay.attestation == _90_DAYS
