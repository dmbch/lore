"""Repository-layer config models: pool tunables, FTS specs, retrieval weights.

These are owned by the repository layer (Protocols-live-with-their-layer) and
composed into ``LoreSettings`` by ``lore.config``. The two module-level regexes
close the SQL-injection surface for ``fulltext_config`` at config load: the
values are substituted into single-quoted migration SQL at apply time, so they
must be trusted by construction.
"""

import re
from typing import Self

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

_PG_FULLTEXT_CONFIG_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_SQLITE_FULLTEXT_CONFIG_RE = re.compile(r"^[a-z][a-z0-9_ ]*$")


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

    @model_validator(mode="after")
    def _validate_weight_sum(self) -> Self:
        # Tolerance matches repositories/_validation.py; the Protocol-boundary
        # check stays as defense in depth; this one fails at startup instead of
        # first consult (deep-merge partial overrides are the landmine).
        if abs(self.proximity + self.authority - 1.0) > 0.001:
            msg = (
                f"retrieval weights must sum to 1.0 (±0.001):"
                f" proximity={self.proximity} + authority={self.authority}"
                f" = {self.proximity + self.authority}"
            )
            raise ValueError(msg)
        return self

    @property
    def weights(self) -> tuple[float, float]:
        """Lane weights in ``(proximity, authority)`` order: the search Protocol shape."""
        return (self.proximity, self.authority)


class PostgresConfig(BaseModel):
    """Postgres pool tunables. `fulltext_config` is any installed `regconfig`."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    min_size: int
    max_size: int
    timeout: float
    max_waiting: int
    fulltext_config: str = "english"

    @field_validator("fulltext_config")
    @classmethod
    def _validate_fulltext_config(cls, v: str) -> str:
        # Substituted into single-quoted SQL at migration time; strict regex
        # closes the injection surface at config load.
        if not _PG_FULLTEXT_CONFIG_RE.match(v):
            msg = (
                f"fulltext_config {v!r} must match {_PG_FULLTEXT_CONFIG_RE.pattern}:"
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

    @field_validator("timeout")
    @classmethod
    def _validate_timeout(cls, v: float) -> float:
        if v <= 0:
            msg = f"timeout must be > 0, got {v}"
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
                f"fulltext_config {v!r} must match {_SQLITE_FULLTEXT_CONFIG_RE.pattern}:"
                " a FTS5 tokenize spec (e.g. 'porter unicode61', 'unicode61')"
            )
            raise ValueError(msg)
        return v
