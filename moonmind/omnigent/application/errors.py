"""Application-layer errors for the Omnigent control-plane use cases.

Source issue: MoonLadderStudios/MoonMind#3711
([Omnigent control plane 10/11]).

These errors express use-case policy outcomes (an unknown or terminal session, an
exhausted attempt budget) in canonical domain terms. They must not carry
infrastructure detail, provider-native vocabulary, or credentials; the use cases
raise them so callers branch on a stable type instead of parsing repository
messages.
"""

from __future__ import annotations


class OmnigentApplicationError(RuntimeError):
    """Base class for Omnigent application-layer use-case errors."""


class SessionNotFoundError(OmnigentApplicationError):
    """Raised when a use case targets a session that has no canonical record."""

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        super().__init__(f"No canonical session record for {session_id!r}")


class SessionTerminalError(OmnigentApplicationError):
    """Raised when a use case would open new work on a terminal session.

    A session that has recorded its canonical terminal cannot accept a new turn
    attempt; the caller must start a fresh session instead of reviving a settled
    one.
    """

    def __init__(self, session_id: str, terminal_state: str) -> None:
        self.session_id = session_id
        self.terminal_state = terminal_state
        super().__init__(
            f"Session {session_id!r} is terminal ({terminal_state!r}); "
            "it cannot admit a new turn attempt"
        )


class MaxTurnAttemptsExceededError(OmnigentApplicationError):
    """Raised when admitting another attempt would exceed the intent budget.

    The immutable intent bounds how many turn attempts a session may consume
    (``CompiledSessionIntent.max_turn_attempts``); a fresh idempotency key beyond
    that budget fails closed rather than silently opening an unbounded attempt.
    """

    def __init__(self, session_id: str, *, attempts: int, limit: int) -> None:
        self.session_id = session_id
        self.attempts = attempts
        self.limit = limit
        super().__init__(
            f"Session {session_id!r} already has {attempts} turn attempt(s); "
            f"intent budget is {limit}"
        )


__all__ = [
    "OmnigentApplicationError",
    "SessionNotFoundError",
    "SessionTerminalError",
    "MaxTurnAttemptsExceededError",
]
