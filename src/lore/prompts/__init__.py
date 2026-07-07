"""Prompts module: load and assemble actor system prompts."""

from collections.abc import Callable, Mapping
from pathlib import Path

from lore.prompts.config import PromptsConfig
from lore.prompts.parser import parse

__all__ = [
    "PromptsConfig",
    "build_core_prompt",
    "load_contract",
    "load_prompt",
]


def load_prompt(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def load_contract[T](path: Path, *, build: Callable[[Mapping[str, object]], T]) -> T:
    """Load a sectioned markdown file and build a typed value from its structure.

    ``build`` receives the parsed sections as a nested, title-keyed mapping and
    returns the typed value, raising if the structure does not fit. A Pydantic
    ``model_validate`` (or a small factory around one) is the natural ``build``.
    """
    return build(parse(load_prompt(path)))


def build_core_prompt(config: PromptsConfig, *, base: Path) -> str:
    parts: list[str] = []
    if config.narrative is not None:
        parts.append(load_prompt(config.narrative))
    if config.glossary is not None:
        parts.append(load_prompt(config.glossary))
    parts.append(load_prompt(base))
    return "\n\n".join(parts)
