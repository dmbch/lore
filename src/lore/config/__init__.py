"""Configuration package — public API."""

from lore.config.loader import load_settings, redact_dsn
from lore.config.types import (
    LoreSettings,
    PostgresConfig,
    PromptsConfig,
)

__all__ = [
    "LoreSettings",
    "PostgresConfig",
    "PromptsConfig",
    "load_settings",
    "redact_dsn",
]
