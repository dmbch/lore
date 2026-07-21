"""Adapter-layer config models: server identity, OIDC credentials, payload limits.

Owned by the adapter layer (Protocols-live-with-their-layer) and composed into
``LoreSettings`` by ``lore.config``.
"""

from pydantic import Field, PositiveInt, SecretStr

from lore._pydantic import ConfigModel


class OidcConfig(ConfigModel):
    """Parsed OIDC credentials. `client_secret` is `SecretStr` so accidental
    serialisation prints `'**********'`; callers unwrap via `.get_secret_value()`.
    """

    discovery_url: str
    client_id: str
    client_secret: SecretStr
    extra_authorize_params: dict[str, str] = Field(default_factory=dict)


class LimitsConfig(ConfigModel):
    question: PositiveInt
    hypothesis: PositiveInt
    context: PositiveInt
    reasoning: PositiveInt


class AuthConfig(ConfigModel):
    """Authentication policy. ``required`` is the operator-controlled fail-fast:
    when true, bootstrap refuses to start without OIDC. ``verify_id_token`` toggles
    ID-token signature verification on the OIDC client.
    """

    required: bool = False
    verify_id_token: bool = True


class ServerConfig(ConfigModel):
    name: str = "Lore"
    icon_url: str | None = None
