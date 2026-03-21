"""Managed runtime strategy registry.

Provides ``RUNTIME_STRATEGIES`` — a dict mapping ``runtime_id`` strings to
:class:`ManagedRuntimeStrategy` instances — and a convenience lookup helper.
"""

from __future__ import annotations

from moonmind.workflows.temporal.runtime.strategies.base import (
    ManagedRuntimeStrategy,
)
from moonmind.workflows.temporal.runtime.strategies.gemini_cli import (
    GeminiCliStrategy,
)

__all__ = [
    "ManagedRuntimeStrategy",
    "GeminiCliStrategy",
    "RUNTIME_STRATEGIES",
    "get_strategy",
]

# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

RUNTIME_STRATEGIES: dict[str, ManagedRuntimeStrategy] = {
    "gemini_cli": GeminiCliStrategy(),
}
"""Strategy instances keyed by canonical ``runtime_id``.

Phase 1 registers only ``gemini_cli``.  Subsequent phases will add
``codex_cli``, ``cursor_cli``, and ``claude_code``.
"""


def get_strategy(runtime_id: str) -> ManagedRuntimeStrategy | None:
    """Look up a registered strategy by *runtime_id*.

    Returns ``None`` when no strategy is registered — callers should
    fall through to legacy branching in that case.
    """
    return RUNTIME_STRATEGIES.get(runtime_id)
