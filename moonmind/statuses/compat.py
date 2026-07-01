"""Explicit compatibility aliases at status ingestion boundaries."""

from __future__ import annotations


WORKFLOW_STATE_COMPATIBILITY_ALIASES: dict[str, str] = {
    "no_changes": "no_commit",
}


def normalize_workflow_state_alias(raw: str) -> str:
    """Normalize legacy workflow state aliases before canonical parsing."""

    candidate = str(raw).strip().lower()
    return WORKFLOW_STATE_COMPATIBILITY_ALIASES.get(candidate, candidate)


__all__ = ["WORKFLOW_STATE_COMPATIBILITY_ALIASES", "normalize_workflow_state_alias"]
