"""Tests for adapter-layer config models.

Covers Server, Oidc, Limits — pure construction and validation. The
section→field loader mapping for these types lives in tests/config/test_sections.py.
"""

import pytest
from pydantic import SecretStr, ValidationError

from lore.adapter import LimitsConfig, OidcConfig, ServerConfig

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
