"""Managed runtime strategy registry.

Provides ``RUNTIME_STRATEGIES`` — a dict mapping ``runtime_id`` strings to
:class:`ManagedRuntimeStrategy` instances — and a convenience lookup helper.
"""

from __future__ import annotations

from moonmind.workflows.temporal.runtime.strategies.base import (
    ManagedRuntimeStrategy,
)
from moonmind.workflows.temporal.runtime.strategies.claude_code import (
    ClaudeCodeStrategy,
)
from moonmind.workflows.temporal.runtime.strategies.codex_cli import (
    CodexCliStrategy,
)
from moonmind.workflows.temporal.runtime.strategies.cursor_cli import (
    CursorCliStrategy,
)
from moonmind.workflows.temporal.runtime.strategies.gemini_cli import (
    GeminiCliStrategy,
)

__all__ = [
    "ManagedRuntimeStrategy",
    "ClaudeCodeStrategy",
    "CodexCliStrategy",
    "CursorCliStrategy",
    "GeminiCliStrategy",
    "RUNTIME_STRATEGIES",
    "get_strategy",
]

# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

RUNTIME_STRATEGIES: dict[str, ManagedRuntimeStrategy] = {
    "claude_code": ClaudeCodeStrategy(),
    "codex_cli": CodexCliStrategy(),
    "cursor_cli": CursorCliStrategy(),
    "gemini_cli": GeminiCliStrategy(),
}
"""Strategy instances keyed by canonical ``runtime_id``.

All four managed runtimes are registered.  The launcher and adapter
delegate fully to strategies — no ``if/elif`` branching remains.
"""


def get_strategy(runtime_id: str) -> ManagedRuntimeStrategy | None:
    """Look up a registered strategy by *runtime_id*.

    Returns ``None`` when no strategy is registered — callers should
    fall through to generic handling in that case.
    """
    return RUNTIME_STRATEGIES.get(runtime_id)
