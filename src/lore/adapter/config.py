"""Adapter-layer config models: server identity, OIDC credentials, payload limits.

Owned by the adapter layer (Protocols-live-with-their-layer) and composed into
``LoreSettings`` by ``lore.config``.
"""

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


class OidcConfig(BaseModel):
    """Parsed OIDC credentials. `client_secret` is `SecretStr` so accidental
    serialisation prints `'**********'`; callers unwrap via `.get_secret_value()`.
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    discovery_url: str
    client_id: str
    client_secret: SecretStr
    extra_authorize_params: dict[str, str] = Field(default_factory=dict)


class LimitsConfig(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    question: int
    hypothesis: int
    context: int
    reasoning: int

    @field_validator("question", "hypothesis", "context", "reasoning")
    @classmethod
    def _validate_positive(cls, v: int) -> int:
        if v <= 0:
            msg = f"must be > 0, got {v}"
            raise ValueError(msg)
        return v


class AuthConfig(BaseModel):
    """Authentication policy. ``required`` is the operator-controlled fail-fast:
    when true, bootstrap refuses to start without OIDC. ``verify_id_token`` toggles
    ID-token signature verification on the OIDC client.
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    required: bool = False
    verify_id_token: bool = True


class ServerConfig(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    name: str = "Lore"
    icon_url: str | None = None
