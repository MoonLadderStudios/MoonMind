"""Speckit stage adapter.

Current behavior intentionally delegates to the existing direct executor to keep
backward compatibility while routing through the skills policy layer.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")


class SkillAdapterError(RuntimeError):
    """Raised when a skill adapter cannot execute its stage contract."""


def run_speckit_stage(*, execute_direct: Callable[[], T]) -> T:
    """Execute the stage via existing direct implementation."""

    return execute_direct()
