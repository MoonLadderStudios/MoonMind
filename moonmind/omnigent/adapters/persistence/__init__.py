"""In-memory persistence adapters for the Omnigent control-plane ports.

Source issue: MoonLadderStudios/MoonMind#3711.
"""

from __future__ import annotations

from .memory import (
    InMemoryDecisionRepository,
    InMemoryObservationRepository,
)

__all__ = [
    "InMemoryDecisionRepository",
    "InMemoryObservationRepository",
]
