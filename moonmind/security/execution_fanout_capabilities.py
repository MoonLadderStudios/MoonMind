"""Stateless authorization for workflow-scoped child execution fan-out."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from collections.abc import Mapping, Sequence
from typing import Any, Literal

_AUDIENCE = "moonmind-execution-fanout"
_VERSION = 1
EXECUTION_FANOUT_REQUIRED_CAPABILITY = "execution.fanout"


class ExecutionFanoutCapabilityError(ValueError):
    """Raised when an execution fan-out capability is invalid or expired."""


def require_execution_fanout_authorization(
    required_capabilities: Sequence[str] | None,
    authorization: Mapping[str, Any] | None,
) -> bool:
    """Authorize minting from workflow-owned evidence, with a replay-only gap."""

    required = {
        str(value or "").strip().lower()
        for value in (required_capabilities or ())
    }
    if EXECUTION_FANOUT_REQUIRED_CAPABILITY not in required:
        return False
    # Already-scheduled activity payloads predate the typed authorization field
    # and retain their former launch semantics. Current workflows always write
    # an explicit authorized/denied decision.
    if authorization is None:
        return True
    if authorization.get("authorized") is True:
        return True
    raise ExecutionFanoutCapabilityError(
        "execution fan-out is not authorized by the resolved Skill policy"
    )


@dataclass(frozen=True, slots=True)
class ExecutionFanoutCapability:
    """Verified authority to create and inspect children of one workflow."""

    parent_workflow_id: str
    agent_run_id: str
    step_id: str | None
    session_id: str
    runtime_id: str
    source_kind: Literal["managed_session", "omnigent"]
    expires_at: int


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    try:
        return base64.urlsafe_b64decode(value + padding)
    except (ValueError, TypeError) as exc:
        raise ExecutionFanoutCapabilityError(
            "invalid execution fan-out capability"
        ) from exc


def _required_text(value: Any, *, field: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ExecutionFanoutCapabilityError(
            f"execution fan-out capability {field} must not be empty"
        )
    return normalized


def mint_execution_fanout_capability(
    *,
    secret: str,
    parent_workflow_id: str,
    agent_run_id: str,
    session_id: str,
    runtime_id: str,
    source_kind: Literal["managed_session", "omnigent"],
    lifetime_seconds: int,
    step_id: str | None = None,
    now: int | None = None,
) -> str:
    """Mint a bearer that is useful only for one parent's child fan-out."""

    signing_secret = _required_text(secret, field="signing secret")
    if lifetime_seconds < 1:
        raise ExecutionFanoutCapabilityError(
            "execution fan-out capability lifetime must be positive"
        )
    if source_kind not in {"managed_session", "omnigent"}:
        raise ExecutionFanoutCapabilityError(
            "unsupported execution fan-out capability source kind"
        )
    issued_at = int(time.time() if now is None else now)
    payload = {
        "aud": _AUDIENCE,
        "v": _VERSION,
        "parentWorkflowId": _required_text(
            parent_workflow_id, field="parentWorkflowId"
        ),
        "agentRunId": _required_text(agent_run_id, field="agentRunId"),
        "stepId": str(step_id or "").strip() or None,
        "sessionId": _required_text(session_id, field="sessionId"),
        "runtimeId": _required_text(runtime_id, field="runtimeId"),
        "sourceKind": source_kind,
        "iat": issued_at,
        "exp": issued_at + lifetime_seconds,
    }
    encoded_payload = _encode(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )
    signature = hmac.new(
        signing_secret.encode("utf-8"),
        encoded_payload.encode("ascii"),
        hashlib.sha256,
    ).digest()
    return f"{encoded_payload}.{_encode(signature)}"


def verify_execution_fanout_capability(
    token: str,
    *,
    secret: str,
    now: int | None = None,
) -> ExecutionFanoutCapability:
    """Verify and return the bounded fan-out claims carried by ``token``."""

    signing_secret = _required_text(secret, field="signing secret")
    normalized_token = _required_text(token, field="token")
    encoded_payload, separator, encoded_signature = normalized_token.partition(".")
    if not separator or not encoded_payload or not encoded_signature:
        raise ExecutionFanoutCapabilityError("invalid execution fan-out capability")
    expected_signature = hmac.new(
        signing_secret.encode("utf-8"),
        encoded_payload.encode("ascii"),
        hashlib.sha256,
    ).digest()
    if not hmac.compare_digest(_decode(encoded_signature), expected_signature):
        raise ExecutionFanoutCapabilityError("invalid execution fan-out capability")
    try:
        payload = json.loads(_decode(encoded_payload))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ExecutionFanoutCapabilityError(
            "invalid execution fan-out capability"
        ) from exc
    if not isinstance(payload, dict):
        raise ExecutionFanoutCapabilityError("invalid execution fan-out capability")
    if payload.get("aud") != _AUDIENCE or payload.get("v") != _VERSION:
        raise ExecutionFanoutCapabilityError("unsupported execution fan-out capability")
    try:
        expires_at = int(payload["exp"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ExecutionFanoutCapabilityError(
            "invalid execution fan-out capability"
        ) from exc
    current_time = int(time.time() if now is None else now)
    if expires_at <= current_time:
        raise ExecutionFanoutCapabilityError("expired execution fan-out capability")
    source_kind = payload.get("sourceKind")
    if source_kind not in {"managed_session", "omnigent"}:
        raise ExecutionFanoutCapabilityError("invalid execution fan-out capability")
    return ExecutionFanoutCapability(
        parent_workflow_id=_required_text(
            payload.get("parentWorkflowId"), field="parentWorkflowId"
        ),
        agent_run_id=_required_text(payload.get("agentRunId"), field="agentRunId"),
        step_id=str(payload.get("stepId") or "").strip() or None,
        session_id=_required_text(payload.get("sessionId"), field="sessionId"),
        runtime_id=_required_text(payload.get("runtimeId"), field="runtimeId"),
        source_kind=source_kind,
        expires_at=expires_at,
    )


__all__ = [
    "EXECUTION_FANOUT_REQUIRED_CAPABILITY",
    "ExecutionFanoutCapability",
    "ExecutionFanoutCapabilityError",
    "mint_execution_fanout_capability",
    "require_execution_fanout_authorization",
    "verify_execution_fanout_capability",
]
