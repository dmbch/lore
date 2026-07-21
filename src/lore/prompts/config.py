"""Prompts config type: frozen Pydantic model.

Owned by the prompts layer, which also handles loading and assembly. The loader
resolves bundled defaults (``bundled:name``) into concrete paths before this
model is constructed.
"""

from pathlib import Path

from lore._pydantic import ConfigModel


class PromptsConfig(ConfigModel):
    """Resolved prompt paths. Bundled defaults (`bundled:name`) are resolved by the loader."""

    narrative: Path | None = None
    glossary: Path | None = None
    scribe: Path
    interpreter: Path
    archivist: Path
    contract: Path
