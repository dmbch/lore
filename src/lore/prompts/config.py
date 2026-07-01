"""Prompts config type: frozen Pydantic model.

Owned by the prompts layer, which also handles loading and assembly. The loader
resolves bundled defaults (``bundled:name``) into concrete paths before this
model is constructed.
"""

from pathlib import Path

from pydantic import BaseModel, ConfigDict


class PromptsConfig(BaseModel):
    """Resolved prompt paths. Bundled defaults (`bundled:name`) are resolved by the loader."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    narrative: Path | None = None
    glossary: Path | None = None
    scribe: Path
    consult: Path
    interpreter: Path
    archivist: Path
