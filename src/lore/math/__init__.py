"""Math service: the public API for Lore's Subjective Logic engine.

Consumers import from this package: ``from lore.math import MathService``.
Internal modules (_opinion, _fusion, _decay, etc.) are implementation details.
"""

from lore.math._service import MathService, build_math
from lore.math.config import EpistemicsConfig

__all__ = ["EpistemicsConfig", "MathService", "build_math"]
