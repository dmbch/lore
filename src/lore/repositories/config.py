"""Repository-layer config models: pool tunables, FTS specs, retrieval weights.

These are owned by the repository layer (Protocols-live-with-their-layer) and
composed into ``LoreSettings`` by ``lore.config``. The two module-level regexes
close the SQL-injection surface for ``fulltext_config`` at config load: the
values are substituted into single-quoted migration SQL at apply time, so they
must be trusted by construction.
"""

import re
from typing import Self

from pydantic import NonNegativeInt, PositiveInt, field_validator, model_validator

from lore._pydantic import ConfigModel, Duration, PositiveFiniteFloat, UnitInterval

_PG_FULLTEXT_CONFIG_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_SQLITE_FULLTEXT_CONFIG_RE = re.compile(r"^[a-z][a-z0-9_ ]*$")


class CacheConfig(ConfigModel):
    """Operational ``_cache`` maintenance knobs.

    ``sweep_interval`` is the period of the lifespan-owned expiry sweep.
    fastmcp stamps ``_cache`` TTLs from five minutes (OAuth auth codes)
    to a year (refresh tokens), with 24-hour session state in between, so
    no tick rate is ever tight; the hourly default bounds dead rows to an
    hour of churn. Duration string (``"1h"``, ``"30m"``) or bare seconds.
    """

    sweep_interval: Duration = 3600.0


class RetrievalConfig(ConfigModel):
    proximity: UnitInterval
    authority: UnitInterval
    limit: PositiveInt
    fan_out: PositiveInt
    max_keywords: PositiveInt

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


class PostgresConfig(ConfigModel):
    """Postgres pool tunables. `fulltext_config` is any installed `regconfig`."""

    min_size: PositiveInt
    max_size: PositiveInt
    timeout: PositiveFiniteFloat
    max_waiting: NonNegativeInt
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

    @model_validator(mode="after")
    def _validate_size_ordering(self) -> Self:
        if self.max_size < self.min_size:
            msg = f"max_size ({self.max_size}) must be >= min_size ({self.min_size})"
            raise ValueError(msg)
        return self


class SqliteConfig(ConfigModel):
    """SQLite tunables. `fulltext_config` is an FTS5 `tokenize=` spec; use
    `unicode61` for non-English deployments.
    """

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
