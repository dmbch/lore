"""Tests for lore.config loader: load_settings, vendor detection, OIDC parsing."""

import os
from pathlib import Path
from unittest.mock import patch

import pytest
import structlog
from pydantic import ValidationError

from lore.adapter import OidcConfig
from lore.config import load_settings, redact_dsn
from lore.config.loader import discover_toml, parse_oidc_url

# Minimal valid env for most tests. DATABASE_URL is the only DSN env var.
_BASE_ENV = {"DATABASE_URL": "sqlite:///test.db"}

# Path to the test TOML fixture.
_TOML_PATH = Path(__file__).parent.parent / "fixtures" / "lore.toml"

# A nonexistent path: forces vendor detection or error.
_NO_TOML = Path(__file__).parent.parent / "fixtures" / "nonexistent.toml"

_30_DAYS = 30 * 86400.0
_90_DAYS = 90 * 86400.0


# ---------------------------------------------------------------------------
# DSN detection: DATABASE_URL only, scheme drives backend dispatch
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
# Version: LORE_VERSION baked into published images; dev marker otherwise
# ---------------------------------------------------------------------------


def test_lore_version_env_sets_version() -> None:
    env = {**_BASE_ENV, "LORE_VERSION": "1.2.3"}
    with patch.dict(os.environ, env, clear=True):
        s = load_settings(toml_path=_TOML_PATH)
        assert s.version == "1.2.3"


def test_lore_version_unset_keeps_dev_marker() -> None:
    with patch.dict(os.environ, _BASE_ENV, clear=True):
        s = load_settings(toml_path=_TOML_PATH)
        assert s.version == "0.0.0+dev"


def test_lore_version_empty_keeps_dev_marker() -> None:
    """Source builds inherit the Dockerfile's empty ARG default (LORE_VERSION="")."""
    env = {**_BASE_ENV, "LORE_VERSION": ""}
    with patch.dict(os.environ, env, clear=True):
        s = load_settings(toml_path=_TOML_PATH)
        assert s.version == "0.0.0+dev"


# ---------------------------------------------------------------------------
# BASE_URL / OIDC_URL pairing: must be both or neither
# ---------------------------------------------------------------------------

_OIDC_URL = "oidc://client:secret@auth.example.com/.well-known/openid-configuration"


def test_base_url_without_oidc_url_raises() -> None:
    env = {**_BASE_ENV, "BASE_URL": "https://lore.example.com"}
    with (
        patch.dict(os.environ, env, clear=True),
        pytest.raises(ValidationError, match="BASE_URL requires OIDC_URL"),
    ):
        load_settings(toml_path=_TOML_PATH)


def test_oidc_url_without_base_url_raises() -> None:
    env = {**_BASE_ENV, "OIDC_URL": _OIDC_URL}
    with (
        patch.dict(os.environ, env, clear=True),
        pytest.raises(ValidationError, match="OIDC_URL requires BASE_URL"),
    ):
        load_settings(toml_path=_TOML_PATH)


def test_settings_reject_auth_required_without_oidc(tmp_path: Path) -> None:
    """``[auth] required = true`` with no OIDC_URL fails inside ``load_settings``.

    The cross-section invariant lives on ``LoreSettings`` now, so the refusal
    fires during ``model_validate`` rather than in ``__main__.configure``.
    """
    toml_file = tmp_path / "auth_required.toml"
    toml_file.write_text(
        "[auth]\nrequired = true\n"
        '[embedding]\nmodel = "test/e"\n[fast]\nmodel = "test/f"\n[reasoning]\nmodel = "test/r"\n'
    )
    with (
        patch.dict(os.environ, _BASE_ENV, clear=True),
        pytest.raises(ValidationError, match=r"\[auth\] required = true requires OIDC_URL"),
    ):
        load_settings(toml_path=toml_file)


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
# TOML discovery: ./lore.toml then /etc/lore.toml
# ---------------------------------------------------------------------------


def test_settings_from_toml() -> None:
    with patch.dict(os.environ, _BASE_ENV, clear=True):
        s = load_settings(toml_path=_TOML_PATH)
        assert s.epistemics.attestation_half_life == _30_DAYS
        assert s.embedding.model == "test/embedding-model"
        assert s.fast.model == "test/fast-model"
        assert s.reasoning.model == "test/reasoning-model"


def test_half_life_default_without_toml() -> None:
    env = {**_BASE_ENV, "GEMINI_API_KEY": "fake-key"}
    with patch.dict(os.environ, env, clear=True):
        s = load_settings(toml_path=_NO_TOML)
        assert s.epistemics.attestation_half_life == _90_DAYS


def test_settings_expose_epistemics_section() -> None:
    """``load_settings`` maps ``[epistemics]`` onto ``settings.epistemics``."""
    with patch.dict(os.environ, _BASE_ENV, clear=True):
        s = load_settings(toml_path=_TOML_PATH)
        assert s.epistemics.attestation_half_life == _30_DAYS
        assert s.epistemics.trust_half_life == _90_DAYS
        assert s.epistemics.maturity_k == 1.0
        assert s.epistemics.transfer_threshold == 1e-3


def test_settings_reject_legacy_decay_section(tmp_path: Path) -> None:
    """The pre-rename ``[decay]`` section is now an unknown key (extra='forbid')."""
    toml_file = tmp_path / "legacy_decay.toml"
    toml_file.write_text(
        '[decay]\nattestation = "30d"\ntrust = "90d"\n'
        '[embedding]\nmodel = "test/e"\n[fast]\nmodel = "test/f"\n[reasoning]\nmodel = "test/r"\n'
    )
    with (
        patch.dict(os.environ, _BASE_ENV, clear=True),
        pytest.raises(ValidationError, match="extra_forbidden"),
    ):
        load_settings(toml_path=toml_file)


def test_settings_reject_legacy_trust_section(tmp_path: Path) -> None:
    """The pre-rename ``[trust]`` section is now an unknown key (extra='forbid')."""
    toml_file = tmp_path / "legacy_trust.toml"
    toml_file.write_text(
        "[trust]\nmaturity = 1.0\n"
        '[embedding]\nmodel = "test/e"\n[fast]\nmodel = "test/f"\n[reasoning]\nmodel = "test/r"\n'
    )
    with (
        patch.dict(os.environ, _BASE_ENV, clear=True),
        pytest.raises(ValidationError, match="extra_forbidden"),
    ):
        load_settings(toml_path=toml_file)


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
        assert s.epistemics.attestation_half_life == _90_DAYS
        assert "gemini/" in s.embedding.model


# ---------------------------------------------------------------------------
# Env does NOT override TOML-only fields
# ---------------------------------------------------------------------------


def test_env_does_not_override_half_life() -> None:
    env = {**_BASE_ENV, "LORE_HALF_LIFE": "0.05"}
    with patch.dict(os.environ, env, clear=True):
        s = load_settings(toml_path=_TOML_PATH)
        # half_life comes from TOML, not env.
        assert s.epistemics.attestation_half_life == _30_DAYS


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
    """Lexical order: bedrock < gemini. Bedrock wins when both keys are set."""
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

    Regression of the old ``extra='forbid'`` behavior: vendor-specific
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


def test_parse_oidc_url_discovery_url_excludes_query_and_fragment() -> None:
    url = "oidc://id:secret@host/path?foo=bar#frag"
    result = parse_oidc_url(url)
    assert result.discovery_url == "https://host/path"


def test_parse_oidc_url_extracts_query_into_extra_authorize_params() -> None:
    """OIDC_URL query becomes upstream authorize params: verbatim, no denylist."""
    url = "oidc://id:secret@host/path?hd=example.com&prompt=consent"
    result = parse_oidc_url(url)
    assert result.extra_authorize_params == {"hd": "example.com", "prompt": "consent"}


def test_parse_oidc_url_keeps_blank_query_values() -> None:
    """`?prompt=` reaches the IdP as an empty value, not dropped silently at parse time."""
    url = "oidc://id:secret@host/path?prompt="
    result = parse_oidc_url(url)
    assert result.extra_authorize_params == {"prompt": ""}


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
# DSN redaction: operator-facing diagnostic, never leaks creds
# ---------------------------------------------------------------------------


def test_redact_dsn_postgres_strips_user_password() -> None:
    """Postgres credentials never reach the diagnostic log."""
    redacted = redact_dsn("postgresql://alice:s3cret@db.internal:5432/lore")
    assert "alice" not in redacted
    assert "s3cret" not in redacted
    assert redacted == "postgresql://db.internal:5432/lore"


def test_redact_dsn_postgres_without_port_round_trips() -> None:
    """Hostname-only Postgres DSNs survive without an injected port."""
    assert redact_dsn("postgresql://db.internal/lore") == "postgresql://db.internal/lore"


def test_redact_dsn_sqlite_preserves_absolute_path() -> None:
    """The four-slash SQLite absolute-path form round-trips unchanged."""
    assert redact_dsn("sqlite:////tmp/lore-dev.db") == "sqlite:////tmp/lore-dev.db"


def test_redact_dsn_drops_query_and_fragment() -> None:
    """Defensive: any DSN query/fragment is stripped (operators never log them)."""
    redacted = redact_dsn("postgresql://db.internal/lore?sslmode=require#fragment")
    assert redacted == "postgresql://db.internal/lore"


# ---------------------------------------------------------------------------
# bootstrap.env: the diagnostic dump
# ---------------------------------------------------------------------------


def test_load_settings_emits_bootstrap_env_with_redacted_dsn() -> None:
    """The diagnostic log carries the credential-free DSN, never the raw user:pass."""
    env = {"DATABASE_URL": "postgresql://alice:s3cret@db.internal:5432/lore"}
    with (
        structlog.testing.capture_logs() as cap,
        patch.dict(os.environ, env, clear=True),
    ):
        load_settings(toml_path=_TOML_PATH)
    events = [e for e in cap if e["event"] == "bootstrap.env"]
    assert len(events) == 1
    assert events[0]["database_url"] == "postgresql://db.internal:5432/lore"
    assert "alice" not in events[0]["database_url"]
    assert "s3cret" not in events[0]["database_url"]


def test_load_settings_bootstrap_env_surfaces_oidc_discovery_url_only() -> None:
    """``oidc_url`` carries the credential-free discovery URL, not the raw OIDC_URL."""
    env = {**_BASE_ENV, "BASE_URL": "https://lore.example.com", "OIDC_URL": _OIDC_URL}
    with (
        structlog.testing.capture_logs() as cap,
        patch.dict(os.environ, env, clear=True),
    ):
        load_settings(toml_path=_TOML_PATH)
    event = next(e for e in cap if e["event"] == "bootstrap.env")
    oidc_url = event["oidc_url"]
    assert oidc_url is not None
    assert "client" not in oidc_url  # client_id from _OIDC_URL stays out
    assert "secret" not in oidc_url  # client_secret too
    assert oidc_url.startswith("https://")


def test_load_settings_bootstrap_env_does_not_invent_fastmcp_defaults() -> None:
    """FastMCP owns its env-var defaults; unset reads as None, not our guess."""
    with (
        structlog.testing.capture_logs() as cap,
        patch.dict(os.environ, _BASE_ENV, clear=True),
    ):
        load_settings(toml_path=_TOML_PATH)
    event = next(e for e in cap if e["event"] == "bootstrap.env")
    assert event["fastmcp_transport"] is None
    assert event["fastmcp_host"] is None
    assert event["fastmcp_port"] is None


# ---------------------------------------------------------------------------
# Half-life through the loader (grammar tests live in tests/math/test_config.py)
# ---------------------------------------------------------------------------


def test_half_life_duration_string_in_toml() -> None:
    toml_path = Path(__file__).parent.parent / "fixtures" / "lore_halflife.toml"
    with patch.dict(os.environ, _BASE_ENV, clear=True):
        s = load_settings(toml_path=toml_path)
        assert s.epistemics.attestation_half_life == _90_DAYS
