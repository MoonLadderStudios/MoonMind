"""Unified typed decision boundary for live reattach / cold restore / branching.

MoonLadderStudios/MoonMind#3707 §5 requires one typed contract for all
continuation admission outcomes:

- live_reattach: current runtime authority can resume the turn
- cold_restore: artifact-backed checkpoint/workspace evidence is sufficient
- branch_required: immutable authority changed and a new canonical session is
  required (policy-gated branch)
- new_session_required: prior session is not safely reusable; start fresh
- resume_unavailable: no safe continuation path exists

Live reattach requires current runtime authority. Cold restore uses
artifact-backed evidence and must not depend on a destroyed host-local path.
"""

from __future__ import annotations

from enum import Enum


class ResumeDecision(str, Enum):
    LIVE_REATTACH = "live_reattach"
    COLD_RESTORE = "cold_restore"
    BRANCH_REQUIRED = "branch_required"
    NEW_SESSION_REQUIRED = "new_session_required"
    RESUME_UNAVAILABLE = "resume_unavailable"


RESUME_DECISIONS: frozenset[str] = frozenset(item.value for item in ResumeDecision)

RESUME_DECISION_VERSION = 1


def ensure_valid_resume_decision(value: str) -> str:
    raw = str(value or "").strip()
    if raw not in RESUME_DECISIONS:
        raise ValueError(f"unknown resume decision {value!r}; expected one of {sorted(RESUME_DECISIONS)}")
    return raw


__all__ = [
    "RESUME_DECISION_VERSION",
    "RESUME_DECISIONS",
    "ResumeDecision",
    "ensure_valid_resume_decision",
]
