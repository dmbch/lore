"""Tests for adapter-layer config models.

Covers Auth, Server, Oidc, Limits — pure construction and validation. The
section→field loader mapping for these types lives in tests/config/test_sections.py.
"""

import pytest
from pydantic import SecretStr, ValidationError

from lore.adapter import AuthConfig, LimitsConfig, OidcConfig, ServerConfig

# ---------------------------------------------------------------------------
# AuthConfig
# ---------------------------------------------------------------------------


def test_auth_config_defaults() -> None:
    ac = AuthConfig()
    assert ac.required is False
    assert ac.verify_id_token is True


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


def test_server_config_icon_url_defaults_to_none() -> None:
    sc = ServerConfig()
    assert sc.icon_url is None


def test_server_config_rejects_legacy_auth_keys() -> None:
    """auth_required and verify_id_token moved to [auth] — extra="forbid" rejects them."""
    with pytest.raises(ValidationError):
        ServerConfig(auth_required=False)  # pyright: ignore[reportCallIssue]
    with pytest.raises(ValidationError):
        ServerConfig(verify_id_token=True)  # pyright: ignore[reportCallIssue]


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
