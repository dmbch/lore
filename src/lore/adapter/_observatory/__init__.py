"""Observatory: MCP-native exploration surface, a subpackage of the adapter.

Same server, lifespan, and auth as ``consult``. ``tools.py`` holds the entry and
app-scoped tools; ``render.py`` holds presentation.
"""

from lore.adapter._observatory.tools import build_observatory

__all__ = ["build_observatory"]
