"""Config types — frozen Pydantic models."""

import re
from pathlib import Path
from typing import Self

from pydantic import BaseModel, ConfigDict, SecretStr, field_validator, model_validator

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


def _parse_half_life(value: str | int | float) -> float:
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


class TaskTypeConfig(BaseModel):
    """Vendor-specific embedding task types. Unset keys are omitted from the LiteLLM call."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    document: str | None = None
    question: str | None = None
    verification: str | None = None


class EmbeddingModelConfig(BaseModel):
    """Embedding model config. `extra='allow'` so unrecognised keys round-trip to LiteLLM."""

    model_config = ConfigDict(frozen=True, strict=True, extra="allow")

    model: str
    dimensions: int | None = None
    task_type: TaskTypeConfig | None = None

    @field_validator("dimensions")
    @classmethod
    def _validate_dimensions(cls, v: int | None) -> int | None:
        if v is not None and v <= 0:
            msg = f"dimensions must be > 0, got {v}"
            raise ValueError(msg)
        return v


class ModelConfig(BaseModel):
    """Completion model config. `extra='allow'` so unrecognised keys round-trip to LiteLLM."""

    model_config = ConfigDict(frozen=True, strict=True, extra="allow")

    model: str
    temperature: float | None = None
    max_tokens: int | None = None
    reasoning_effort: str | None = None


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
    def _validate_half_life(cls, v: str | int | float) -> float:
        return _parse_half_life(v)


class LimitsConfig(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    question: int
    hypothesis: int
    context: int
    reasoning: int
    answer: int

    @field_validator("question", "hypothesis", "context", "reasoning", "answer")
    @classmethod
    def _validate_positive(cls, v: int) -> int:
        if v <= 0:
            msg = f"must be > 0, got {v}"
            raise ValueError(msg)
        return v


class RetrievalConfig(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    proximity: float
    authority: float
    limit: int
    fan_out: int
    max_keywords: int

    @field_validator("proximity", "authority")
    @classmethod
    def _validate_weight(cls, v: float) -> float:
        if not 0 <= v <= 1:
            msg = f"must be in [0, 1], got {v}"
            raise ValueError(msg)
        return v

    @field_validator("limit", "fan_out", "max_keywords")
    @classmethod
    def _validate_positive(cls, v: int) -> int:
        if v <= 0:
            msg = f"must be > 0, got {v}"
            raise ValueError(msg)
        return v


_PG_FULLTEXT_CONFIG_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_SQLITE_FULLTEXT_CONFIG_RE = re.compile(r"^[a-z][a-z0-9_ ]*$")


class PostgresConfig(BaseModel):
    """Postgres pool tunables. `fulltext_config` is any installed `regconfig`."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    min_size: int
    max_size: int
    getconn_timeout: float
    max_waiting: int
    fulltext_config: str = "english"

    @field_validator("fulltext_config")
    @classmethod
    def _validate_fulltext_config(cls, v: str) -> str:
        # Substituted into single-quoted SQL at migration time; strict regex
        # closes the injection surface at config load.
        if not _PG_FULLTEXT_CONFIG_RE.match(v):
            msg = (
                f"fulltext_config {v!r} must match {_PG_FULLTEXT_CONFIG_RE.pattern} —"
                " a plain Postgres identifier (e.g. 'english', 'german', 'simple')"
            )
            raise ValueError(msg)
        return v

    @field_validator("min_size", "max_size")
    @classmethod
    def _validate_positive_size(cls, v: int) -> int:
        if v <= 0:
            msg = f"must be > 0, got {v}"
            raise ValueError(msg)
        return v

    @field_validator("getconn_timeout")
    @classmethod
    def _validate_timeout(cls, v: float) -> float:
        if v <= 0:
            msg = f"getconn_timeout must be > 0, got {v}"
            raise ValueError(msg)
        return v

    @field_validator("max_waiting")
    @classmethod
    def _validate_max_waiting(cls, v: int) -> int:
        if v < 0:
            msg = f"max_waiting must be >= 0, got {v}"
            raise ValueError(msg)
        return v

    @model_validator(mode="after")
    def _validate_size_ordering(self) -> Self:
        if self.max_size < self.min_size:
            msg = f"max_size ({self.max_size}) must be >= min_size ({self.min_size})"
            raise ValueError(msg)
        return self


class SqliteConfig(BaseModel):
    """SQLite tunables. `fulltext_config` is an FTS5 `tokenize=` spec; use
    `unicode61` for non-English deployments.
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    fulltext_config: str = "porter unicode61"

    @field_validator("fulltext_config")
    @classmethod
    def _validate_fulltext_config(cls, v: str) -> str:
        # Substituted into single-quoted FTS5 DDL at migration time; strict
        # regex closes the injection surface at config load.
        if not _SQLITE_FULLTEXT_CONFIG_RE.match(v):
            msg = (
                f"fulltext_config {v!r} must match {_SQLITE_FULLTEXT_CONFIG_RE.pattern} —"
                " a FTS5 tokenize spec (e.g. 'porter unicode61', 'unicode61')"
            )
            raise ValueError(msg)
        return v


class ServerConfig(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    name: str = "Lore"
    auth_required: bool = False


class PromptsConfig(BaseModel):
    """Resolved prompt paths. Bundled defaults (`bundled:name`) are resolved by the loader."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    narrative: Path | None = None
    glossary: Path | None = None
    scribe: Path
    consult: Path
    interpreter: Path
    archivist: Path


class LoreSettings(BaseModel):
    """The single config object passed to bootstrap."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

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
