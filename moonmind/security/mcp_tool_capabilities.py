"""Stateless, purpose-scoped authorization for managed MCP tool callers.

Managed agent runtimes call the general MCP tool surface for trusted Jira
operations. They are machines, not the operator: each launch receives a
capability bound to one workflow step and one tool purpose, signed with the
same deployment secret as the container-job and fan-out capabilities. It never
grants operator access and is useful only for that purpose's tools.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Literal

_AUDIENCE = "moonmind-mcp-tools"
_VERSION = 1

JIRA_TOOLS_PURPOSE = "jira"
# Purpose (the matching authored required capability) -> tool-name prefix.
MCP_TOOL_PURPOSE_PREFIXES: dict[str, str] = {JIRA_TOOLS_PURPOSE: "jira."}
McpToolSourceKind = Literal["managed_process", "managed_session", "omnigent"]
_SOURCE_KINDS = frozenset({"managed_process", "managed_session", "omnigent"})


class McpToolCapabilityError(ValueError):
    """Raised when an MCP tool capability is invalid or expired."""


@dataclass(frozen=True, slots=True)
class McpToolCapability:
    """Verified authority to call one purpose's tools for one workflow step."""

    purpose: str
    workflow_id: str
    agent_run_id: str
    step_id: str | None
    session_id: str
    runtime_id: str
    source_kind: McpToolSourceKind
    expires_at: int

    def allows_tool(self, tool: str) -> bool:
        prefix = MCP_TOOL_PURPOSE_PREFIXES.get(self.purpose)
        return bool(prefix) and str(tool or "").startswith(prefix)


def requires_jira_tools(required_capabilities: Iterable[str] | None) -> bool:
    """Whether a step's authored capabilities include trusted Jira tools."""

    return JIRA_TOOLS_PURPOSE in {
        str(value or "").strip().lower() for value in (required_capabilities or ())
    }


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode(value: str) -> bytes:
    try:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (ValueError, TypeError) as exc:
        raise McpToolCapabilityError("invalid MCP tool capability") from exc


def _required_text(value: Any, *, field: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise McpToolCapabilityError(f"MCP tool capability {field} must not be empty")
    return normalized


def _signature(secret: str, encoded_payload: str) -> bytes:
    return hmac.new(
        _required_text(secret, field="signing secret").encode("utf-8"),
        encoded_payload.encode("ascii"),
        hashlib.sha256,
    ).digest()


def mint_mcp_tool_capability(
    *,
    secret: str,
    purpose: str,
    workflow_id: str,
    agent_run_id: str,
    session_id: str,
    runtime_id: str,
    source_kind: McpToolSourceKind,
    lifetime_seconds: int,
    step_id: str | None = None,
    now: int | None = None,
) -> str:
    """Mint a bearer useful only for ``purpose`` tools within one workflow."""

    if purpose not in MCP_TOOL_PURPOSE_PREFIXES:
        raise McpToolCapabilityError("unsupported MCP tool capability purpose")
    if source_kind not in _SOURCE_KINDS:
        raise McpToolCapabilityError("unsupported MCP tool capability source kind")
    if lifetime_seconds < 1:
        raise McpToolCapabilityError("MCP tool capability lifetime must be positive")
    issued_at = int(time.time() if now is None else now)
    payload = {
        "aud": _AUDIENCE,
        "v": _VERSION,
        "purpose": purpose,
        "workflowId": _required_text(workflow_id, field="workflowId"),
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
    return f"{encoded_payload}.{_encode(_signature(secret, encoded_payload))}"


def verify_mcp_tool_capability(
    token: str,
    *,
    secret: str,
    now: int | None = None,
) -> McpToolCapability:
    """Verify ``token`` and return only its bounded purpose and binding."""

    encoded_payload, separator, encoded_signature = _required_text(
        token, field="token"
    ).partition(".")
    if not separator or not encoded_payload or not encoded_signature:
        raise McpToolCapabilityError("invalid MCP tool capability")
    if not hmac.compare_digest(
        _decode(encoded_signature), _signature(secret, encoded_payload)
    ):
        raise McpToolCapabilityError("invalid MCP tool capability")
    try:
        payload = json.loads(_decode(encoded_payload))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise McpToolCapabilityError("invalid MCP tool capability") from exc
    if not isinstance(payload, dict):
        raise McpToolCapabilityError("invalid MCP tool capability")
    if payload.get("aud") != _AUDIENCE or payload.get("v") != _VERSION:
        raise McpToolCapabilityError("unsupported MCP tool capability")
    try:
        expires_at = int(payload["exp"])
    except (KeyError, TypeError, ValueError) as exc:
        raise McpToolCapabilityError("invalid MCP tool capability") from exc
    if expires_at <= int(time.time() if now is None else now):
        raise McpToolCapabilityError("expired MCP tool capability")
    purpose = payload.get("purpose")
    source_kind = payload.get("sourceKind")
    if purpose not in MCP_TOOL_PURPOSE_PREFIXES or source_kind not in _SOURCE_KINDS:
        raise McpToolCapabilityError("invalid MCP tool capability")
    return McpToolCapability(
        purpose=purpose,
        workflow_id=_required_text(payload.get("workflowId"), field="workflowId"),
        agent_run_id=_required_text(payload.get("agentRunId"), field="agentRunId"),
        step_id=str(payload.get("stepId") or "").strip() or None,
        session_id=_required_text(payload.get("sessionId"), field="sessionId"),
        runtime_id=_required_text(payload.get("runtimeId"), field="runtimeId"),
        source_kind=source_kind,
        expires_at=expires_at,
    )


__all__ = [
    "JIRA_TOOLS_PURPOSE",
    "MCP_TOOL_PURPOSE_PREFIXES",
    "McpToolCapability",
    "McpToolCapabilityError",
    "McpToolSourceKind",
    "mint_mcp_tool_capability",
    "requires_jira_tools",
    "verify_mcp_tool_capability",
]
