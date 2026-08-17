"""Session repository port.

The narrow persistence boundary for canonical bridge session state. Every
adapter (in-memory test double, SQLAlchemy production store) must implement
identical optimistic-concurrency (revision) and fencing outcomes; the shared
contract test suite in
``tests/unit/omnigent/ports/test_session_repository_contract.py`` proves it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Protocol, runtime_checkable


class SessionRevisionConflict(RuntimeError):
    """Raised when a save is attempted against a stale expected revision.

    This is the fencing/optimistic-concurrency outcome: a writer holding an
    outdated ``expected_revision`` must not clobber a newer revision.
    """

    def __init__(self, session_id: str, expected: int, actual: int) -> None:
        super().__init__(
            f"Session {session_id!r} revision conflict: "
            f"expected {expected}, found {actual}"
        )
        self.session_id = session_id
        self.expected = expected
        self.actual = actual


@dataclass(frozen=True, slots=True)
class SessionRecord:
    """A persisted bridge session projection with its concurrency revision.

    ``revision`` starts at 1 for a freshly created record and increments on every
    successful save. Adapters must never expose partially-applied writes.
    """

    bridge_session_id: str
    status: str
    revision: int
    omnigent_session_id: str | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)


@runtime_checkable
class SessionRepository(Protocol):
    """Narrow port for canonical bridge session persistence."""

    async def get(self, bridge_session_id: str) -> SessionRecord | None:
        """Return the current record, or ``None`` if it does not exist."""
        ...

    async def create(
        self,
        bridge_session_id: str,
        *,
        status: str,
        omnigent_session_id: str | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> SessionRecord:
        """Create a new record at revision 1.

        Raises :class:`SessionRevisionConflict` if a record already exists (the
        create-vs-create fencing outcome).
        """
        ...

    async def save(
        self,
        record: SessionRecord,
        *,
        expected_revision: int,
    ) -> SessionRecord:
        """Persist ``record`` if ``expected_revision`` matches the stored one.

        Returns the stored record with an incremented revision. Raises
        :class:`SessionRevisionConflict` on a stale ``expected_revision``.
        """
        ...


__all__ = [
    "SessionRecord",
    "SessionRepository",
    "SessionRevisionConflict",
]
