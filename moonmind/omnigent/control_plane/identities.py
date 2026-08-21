"""Provider-neutral deterministic identities for canonical Omnigent authority."""

from __future__ import annotations

import hashlib
import json


def canonical_omnigent_session_id(
    *, workflow_id: str, step_execution_id: str, agent_run_id: str
) -> str:
    authority = json.dumps(
        ["omnigent-session/v1", workflow_id, step_execution_id, agent_run_id],
        separators=(",", ":"),
    ).encode("utf-8")
    return "oms_" + hashlib.sha256(authority).hexdigest()[:40]


def canonical_omnigent_turn_attempt_id(session_id: str, ordinal: int = 1) -> str:
    authority = f"omnigent-turn/v1:{session_id}:{ordinal}".encode("utf-8")
    return "ota_" + hashlib.sha256(authority).hexdigest()[:40]


def omnigent_session_workflow_id(session_id: str) -> str:
    return f"omnigent-session:{session_id}"


__all__ = [
    "canonical_omnigent_session_id",
    "canonical_omnigent_turn_attempt_id",
    "omnigent_session_workflow_id",
]
