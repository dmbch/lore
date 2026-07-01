"""Math service: the public API for Lore's Subjective Logic engine.

Consumers import from this package: ``from lore.math import MathService``.
Internal modules (opinion, fusion, decay, etc.) are implementation details.
"""

from lore.math.config import EpistemicsConfig
from lore.math.service import MathService, build_math

__all__ = ["EpistemicsConfig", "MathService", "build_math"]
