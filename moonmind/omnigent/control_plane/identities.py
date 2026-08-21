"""Provider-neutral deterministic identities for canonical Omnigent authority."""

from __future__ import annotations

import hashlib
import json

from .records import compute_digest


EGRESS_CLEANUP_AUTHORITY_KEY = "egress_cleanup_authority"
EGRESS_CLEANUP_AUTHORITY_VERSION = 1


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


def canonical_turn_command_key(workflow_id: str, idempotency_key: str) -> str:
    """Return the workflow-scoped durable key for one instruction delivery."""

    return "omnigent-turn-command:" + compute_digest(
        ["workflow", workflow_id, idempotency_key]
    )


def canonical_turn_claim_token(command_key: str) -> str:
    """Return the stable delivery claim token for a canonical command."""

    return "otc_" + compute_digest([command_key, "delivery"])[:40]


def canonical_followup_turn_attempt_id(session_id: str, command_key: str) -> str:
    """Return the stable attempt identity for a non-bootstrap command."""

    return "ota_" + compute_digest([session_id, command_key])[:40]


def canonical_turn_command_id(
    session_id: str, command_key: str, command_type: str
) -> str:
    """Return the stable side-effect-journal identity for one command."""

    return "ocm_" + compute_digest([session_id, command_key, command_type])[:40]


__all__ = [
    "EGRESS_CLEANUP_AUTHORITY_KEY",
    "EGRESS_CLEANUP_AUTHORITY_VERSION",
    "canonical_omnigent_session_id",
    "canonical_omnigent_turn_attempt_id",
    "canonical_followup_turn_attempt_id",
    "canonical_turn_claim_token",
    "canonical_turn_command_id",
    "canonical_turn_command_key",
    "omnigent_session_workflow_id",
]
