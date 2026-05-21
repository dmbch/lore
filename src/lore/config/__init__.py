"""Configuration package — public API."""

from lore.config.loader import load_settings
from lore.config.types import (
    EmbeddingModelConfig,
    LoreSettings,
    ModelConfig,
    PostgresConfig,
    PromptsConfig,
)

__all__ = [
    "EmbeddingModelConfig",
    "LoreSettings",
    "ModelConfig",
    "PostgresConfig",
    "PromptsConfig",
    "load_settings",
]
