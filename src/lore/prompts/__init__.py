"""Prompts module: load and assemble actor system prompts."""

from pathlib import Path

from lore.prompts.config import PromptsConfig

__all__ = ["PromptsConfig", "build_core_prompt", "load_prompt"]


def load_prompt(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def build_core_prompt(config: PromptsConfig, *, base: Path) -> str:
    parts: list[str] = []
    if config.narrative is not None:
        parts.append(load_prompt(config.narrative))
    if config.glossary is not None:
        parts.append(load_prompt(config.glossary))
    parts.append(load_prompt(base))
    return "\n\n".join(parts)
