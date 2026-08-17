"""Acceptance evidence schema.

The typed shape of terminal acceptance evidence: the canonical terminal status,
its failure class (if any), and the durable refs proving completion. Evidence
records the outcome; it does not compute or override lifecycle authority.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from moonmind.schemas.agent_runtime_models import FailureClass


@dataclass(frozen=True, slots=True)
class AcceptanceRecord:
    bridge_session_id: str
    terminal_status: str
    failure_class: FailureClass | None = None
    artifact_refs: Sequence[str] = field(default_factory=tuple)

    @property
    def succeeded(self) -> bool:
        return self.terminal_status == "completed" and self.failure_class is None


__all__ = ["AcceptanceRecord"]
