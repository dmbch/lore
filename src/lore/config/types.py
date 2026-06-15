"""Config types — frozen Pydantic models."""

import re

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from lore.prompts.config import PromptsConfig
from lore.providers.config import EmbeddingModelConfig, ModelConfig
from lore.repositories.config import PostgresConfig, RetrievalConfig, SqliteConfig

_DURATION_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*([yMdhms]?)$")
_UNITS: dict[str, int] = {
    "y": 31536000,
    "M": 2592000,
    "d": 86400,
    "h": 3600,
    "m": 60,
    "s": 1,
    "": 1,
}


def _parse_half_life(value: str | float) -> float:
    """Parse a duration string (`"1y"`, `"90d"`, `"24h"`, ...) or bare seconds."""
    if isinstance(value, int | float):
        seconds = float(value)
    else:
        match = _DURATION_RE.match(value.strip())
        if not match:
            msg = (
                f"invalid half_life: {value!r}"
                " (expected e.g. '1y', '3M', '90d', '24h', '60m', '3600s')"
            )
            raise ValueError(msg)
        seconds = float(match.group(1)) * _UNITS[match.group(2)]
    if seconds <= 0:
        msg = f"half_life must be positive, got {value!r}"
        raise ValueError(msg)
    return seconds


class OidcConfig(BaseModel):
    """Parsed OIDC credentials. `client_secret` is `SecretStr` so accidental
    serialisation prints `'**********'`; callers unwrap via `.get_secret_value()`.
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    discovery_url: str
    client_id: str
    client_secret: SecretStr
    extra_authorize_params: dict[str, str] = Field(default_factory=dict)


class TrustConfig(BaseModel):
    """Trust grading: `maturity` is the half-saturation constant K. The alignment-blend
    weight is derived adaptively from per-attestation maturity (see docs/logic.md).

    `threshold` is the epistemic-significance floor for the consolidated transfer
    attestation written by ``Recorder._compute_transfer`` — fused magnitudes below
    this value do not produce a row. Decoupled from ``Opinion.EPSILON`` (math-core
    IEEE noise floor) so that operators can tune what counts as "informationally
    meaningful" independently of float precision.
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    maturity: float
    threshold: float = 1e-3

    @field_validator("maturity")
    @classmethod
    def _validate_maturity(cls, v: float) -> float:
        if v < 0:
            msg = f"maturity must be >= 0, got {v}"
            raise ValueError(msg)
        return v

    @field_validator("threshold")
    @classmethod
    def _validate_threshold(cls, v: float) -> float:
        if v <= 0:
            msg = f"threshold must be > 0, got {v}"
            raise ValueError(msg)
        return v


class DecayConfig(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    attestation: float
    trust: float

    @field_validator("attestation", "trust", mode="before")
    @classmethod
    def _validate_half_life(cls, v: str | float) -> float:
        return _parse_half_life(v)


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


class ServerConfig(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    name: str = "Lore"
    auth_required: bool = False
    icon_url: str | None = None
    verify_id_token: bool = True


class LoreSettings(BaseModel):
    """The single config object passed to bootstrap."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    # serverInfo.version. Never empty: FastMCP falls back to fastmcp.__version__
    # on a falsy value, leaking the framework version to clients. The loader fills
    # this from LORE_VERSION (baked into published images); source builds keep the
    # dev marker.
    version: str = "0.0.0+dev"
    dsn: str
    oidc: OidcConfig | None
    base_url: str | None = None

    decay: DecayConfig
    embedding: EmbeddingModelConfig
    fast: ModelConfig
    reasoning: ModelConfig
    trust: TrustConfig
    limits: LimitsConfig
    retrieval: RetrievalConfig
    server: ServerConfig = ServerConfig()
    postgres: PostgresConfig
    sqlite: SqliteConfig = SqliteConfig()
    prompts: PromptsConfig
