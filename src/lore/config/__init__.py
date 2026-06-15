"""Configuration package — public API."""

from lore.config.loader import load_settings, redact_dsn
from lore.config.types import (
    LoreSettings,
    PromptsConfig,
)

__all__ = [
    "LoreSettings",
    "PromptsConfig",
    "load_settings",
    "redact_dsn",
]
