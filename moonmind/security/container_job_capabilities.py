"""Stateless, session-scoped authorization for managed container jobs."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Any, Literal

from moonmind.schemas.container_job_models import OwnerIdentity

_AUDIENCE = "moonmind-container-jobs"
_VERSION = 1


class ContainerJobCapabilityError(ValueError):
    """Raised when a managed-session capability is invalid or expired."""


@dataclass(frozen=True, slots=True)
class ContainerJobSessionCapability:
    """Verified identity and scope carried by one managed session."""

    owner: OwnerIdentity
    agent_run_id: str
    session_id: str
    runtime_id: str
    source_kind: Literal["managed_session", "omnigent"]
    workspace_kind: Literal["managed_runtime", "sandbox"]
    workspace_id: str
    workspace_relative_path: str
    expires_at: int


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    try:
        return base64.urlsafe_b64decode(value + padding)
    except (ValueError, TypeError) as exc:
        raise ContainerJobCapabilityError("invalid container-job capability") from exc


def _required_text(value: Any, *, field: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ContainerJobCapabilityError(
            f"container-job capability {field} must not be empty"
        )
    return normalized


def mint_container_job_session_capability(
    *,
    secret: str,
    owner: OwnerIdentity,
    agent_run_id: str,
    session_id: str,
    runtime_id: str,
    source_kind: Literal["managed_session", "omnigent"] = "managed_session",
    workspace_kind: Literal["managed_runtime", "sandbox"] = "managed_runtime",
    workspace_id: str | None = None,
    workspace_relative_path: str = "repo",
    lifetime_seconds: int,
    now: int | None = None,
) -> str:
    """Mint a bearer capability bound to one session and authorized owner."""

    signing_secret = _required_text(secret, field="signing secret")
    if lifetime_seconds < 1:
        raise ContainerJobCapabilityError(
            "container-job capability lifetime must be positive"
        )
    if source_kind not in {"managed_session", "omnigent"}:
        raise ContainerJobCapabilityError(
            "unsupported container-job capability source kind"
        )
    if workspace_kind not in {"managed_runtime", "sandbox"}:
        raise ContainerJobCapabilityError(
            "unsupported container-job capability workspace kind"
        )
    issued_at = int(time.time() if now is None else now)
    normalized_workspace_id = _required_text(
        workspace_id or agent_run_id,
        field="workspaceId",
    )
    payload = {
        "aud": _AUDIENCE,
        "v": _VERSION,
        "owner": owner.model_dump(mode="json", by_alias=True),
        "agentRunId": _required_text(agent_run_id, field="agentRunId"),
        "sessionId": _required_text(session_id, field="sessionId"),
        "runtimeId": _required_text(runtime_id, field="runtimeId"),
        "sourceKind": source_kind,
        "workspaceKind": workspace_kind,
        "workspaceId": normalized_workspace_id,
        "workspaceRelativePath": _required_text(
            workspace_relative_path,
            field="workspaceRelativePath",
        ),
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


def verify_container_job_session_capability(
    token: str,
    *,
    secret: str,
    now: int | None = None,
) -> ContainerJobSessionCapability:
    """Verify a capability and return only its bounded authorization claims."""

    signing_secret = _required_text(secret, field="signing secret")
    normalized_token = _required_text(token, field="token")
    encoded_payload, separator, encoded_signature = normalized_token.partition(".")
    if not separator or not encoded_payload or not encoded_signature:
        raise ContainerJobCapabilityError("invalid container-job capability")
    expected_signature = hmac.new(
        signing_secret.encode("utf-8"),
        encoded_payload.encode("ascii"),
        hashlib.sha256,
    ).digest()
    if not hmac.compare_digest(_decode(encoded_signature), expected_signature):
        raise ContainerJobCapabilityError("invalid container-job capability")
    try:
        payload = json.loads(_decode(encoded_payload))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContainerJobCapabilityError("invalid container-job capability") from exc
    if not isinstance(payload, dict):
        raise ContainerJobCapabilityError("invalid container-job capability")
    if payload.get("aud") != _AUDIENCE or payload.get("v") != _VERSION:
        raise ContainerJobCapabilityError("unsupported container-job capability")
    try:
        expires_at = int(payload["exp"])
        owner = OwnerIdentity.model_validate(payload["owner"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ContainerJobCapabilityError("invalid container-job capability") from exc
    current_time = int(time.time() if now is None else now)
    if expires_at <= current_time:
        raise ContainerJobCapabilityError("expired container-job capability")
    source_kind = payload.get("sourceKind") or "managed_session"
    workspace_kind = payload.get("workspaceKind") or "managed_runtime"
    if source_kind not in {"managed_session", "omnigent"}:
        raise ContainerJobCapabilityError("invalid container-job capability")
    if workspace_kind not in {"managed_runtime", "sandbox"}:
        raise ContainerJobCapabilityError("invalid container-job capability")
    return ContainerJobSessionCapability(
        owner=owner,
        agent_run_id=_required_text(payload.get("agentRunId"), field="agentRunId"),
        session_id=_required_text(payload.get("sessionId"), field="sessionId"),
        runtime_id=_required_text(payload.get("runtimeId"), field="runtimeId"),
        # Tokens minted before sandbox-scoped Omnigent container jobs existed
        # remain valid for their original managed-runtime authority until their
        # already-bounded expiry. New tokens always carry the fields explicitly.
        source_kind=source_kind,
        workspace_kind=workspace_kind,
        workspace_id=_required_text(
            payload.get("workspaceId") or payload.get("agentRunId"),
            field="workspaceId",
        ),
        workspace_relative_path=_required_text(
            payload.get("workspaceRelativePath") or "repo",
            field="workspaceRelativePath",
        ),
        expires_at=expires_at,
    )


__all__ = [
    "ContainerJobCapabilityError",
    "ContainerJobSessionCapability",
    "mint_container_job_session_capability",
    "verify_container_job_session_capability",
]
