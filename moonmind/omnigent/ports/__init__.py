"""Omnigent ports: narrow protocols for side-effect and repository boundaries.

Each module defines one focused protocol (or a small cluster of tightly related
ones) rather than a single all-purpose ``OmnigentClient``/store. Application use
cases depend only on these protocols; adapters implement them. Ports depend only
on the domain layer and the Python standard library — never on FastAPI,
SQLAlchemy, Temporal, HTTP clients, Docker, or settings.
"""

from moonmind.omnigent.ports.sessions import (
    SessionRecord,
    SessionRepository,
    SessionRevisionConflict,
)

__all__ = [
    "SessionRecord",
    "SessionRepository",
    "SessionRevisionConflict",
]
