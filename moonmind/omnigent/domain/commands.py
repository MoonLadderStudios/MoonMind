"""Domain commands: side effects an application use case asks an adapter to run.

Commands are pure descriptions of intended side effects. The domain and
application layers *produce* commands; adapters *execute* them. Keeping commands
as data (not method calls) means reconciliation can be tested by asserting on the
commands it emits without any infrastructure.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Command:
    """Base marker for domain commands."""


@dataclass(frozen=True, slots=True)
class CreateProviderSession(Command):
    """Create a provider session for a MoonMind-owned bridge session."""

    bridge_session_id: str
    first_message_digest: str | None = None


@dataclass(frozen=True, slots=True)
class DeliverFirstMessage(Command):
    """Deliver the durable first message to a provider-bound session."""

    bridge_session_id: str
    first_message_digest: str


@dataclass(frozen=True, slots=True)
class RecordTerminalStatus(Command):
    """Persist a terminal normalized status and its failure class."""

    bridge_session_id: str
    status: str


@dataclass(frozen=True, slots=True)
class ReleaseHost(Command):
    """Release the host/lease bound to a session after terminal evidence."""

    bridge_session_id: str


__all__ = [
    "Command",
    "CreateProviderSession",
    "DeliverFirstMessage",
    "RecordTerminalStatus",
    "ReleaseHost",
]
