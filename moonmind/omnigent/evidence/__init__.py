"""Omnigent evidence: durable artifact-backed evidence and acceptance schemas.

The evidence layer owns the event journal, harvesting, diagnostics, and
acceptance schemas. It records and projects evidence; it does NOT decide session
lifecycle authority. Evidence may depend on the domain layer and the artifact
port, never on FastAPI, SQLAlchemy, or provider clients.
"""

from moonmind.omnigent.evidence.acceptance import AcceptanceRecord
from moonmind.omnigent.evidence.diagnostics import Diagnostic
from moonmind.omnigent.evidence.event_journal import EventJournal

__all__ = ["AcceptanceRecord", "Diagnostic", "EventJournal"]
