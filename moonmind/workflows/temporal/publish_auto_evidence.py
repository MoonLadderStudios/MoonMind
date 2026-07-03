"""Validation helpers for agent-owned auto publish evidence."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping


SCHEMA_VERSION = "moonmind.publish.auto.v1"
ALLOWED_STATUSES = frozenset({"verified", "no_op_verified", "blocked", "failed"})
ALLOWED_ACTIONS = frozenset(
    {"none", "commit", "push", "merge", "commit_and_push", "push_and_merge"}
)


class AutoPublishEvidenceError(ValueError):
    """Raised when auto publish evidence is missing, malformed, or unproven."""


@dataclass(frozen=True)
class AutoPublishEvidence:
    schema_version: str
    mode: str
    owner: str
    skill_id: str
    status: str
    action: str
    repository: str
    branch: str
    local_head: str | None
    remote_branch_head: str | None
    remote_verified: bool
    pushed: bool
    merged: bool
    pr_url: str | None
    blocked_reason: str | None
    verification_commands: tuple[str, ...]

    @property
    def finish_code(self) -> str:
        if self.status == "no_op_verified":
            return "NO_COMMIT"
        if self.merged:
            return "PUBLISHED_PR"
        if self.pushed:
            return "PUBLISHED_BRANCH"
        return "FAILED"


def _text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _required_text(payload: Mapping[str, Any], key: str) -> str:
    value = _text(payload.get(key))
    if value is None:
        raise AutoPublishEvidenceError(f"auto publish evidence missing {key}")
    return value


def _bool(payload: Mapping[str, Any], key: str) -> bool:
    value = payload.get(key)
    if not isinstance(value, bool):
        raise AutoPublishEvidenceError(f"auto publish evidence {key} must be boolean")
    return value


def _commands(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise AutoPublishEvidenceError(
            "auto publish evidence verificationCommands must be a list"
        )
    return tuple(str(item).strip() for item in value if str(item).strip())


def parse_auto_publish_evidence(raw: bytes | str | Mapping[str, Any]) -> AutoPublishEvidence:
    if isinstance(raw, Mapping):
        payload = dict(raw)
    else:
        try:
            payload = json.loads(raw.decode() if isinstance(raw, bytes) else raw)
        except Exception as exc:  # noqa: BLE001 - callers need a stable domain error
            raise AutoPublishEvidenceError(
                "auto publish evidence must be a valid JSON object"
            ) from exc
        if not isinstance(payload, dict):
            raise AutoPublishEvidenceError(
                "auto publish evidence must be a valid JSON object"
            )

    schema_version = _required_text(payload, "schemaVersion")
    if schema_version != SCHEMA_VERSION:
        raise AutoPublishEvidenceError("unsupported auto publish evidence schemaVersion")
    mode = _required_text(payload, "mode")
    if mode != "auto":
        raise AutoPublishEvidenceError("auto publish evidence mode must be auto")
    owner = _required_text(payload, "owner")
    if owner != "agent":
        raise AutoPublishEvidenceError("auto publish evidence owner must be agent")
    status = _required_text(payload, "status")
    if status not in ALLOWED_STATUSES:
        raise AutoPublishEvidenceError(f"unsupported auto publish status: {status}")
    action = _required_text(payload, "action")
    if action not in ALLOWED_ACTIONS:
        raise AutoPublishEvidenceError(f"unsupported auto publish action: {action}")

    evidence = AutoPublishEvidence(
        schema_version=schema_version,
        mode=mode,
        owner=owner,
        skill_id=_required_text(payload, "skillId"),
        status=status,
        action=action,
        repository=_required_text(payload, "repository"),
        branch=_required_text(payload, "branch"),
        local_head=_text(payload.get("localHead")),
        remote_branch_head=_text(payload.get("remoteBranchHead")),
        remote_verified=_bool(payload, "remoteVerified"),
        pushed=_bool(payload, "pushed"),
        merged=_bool(payload, "merged"),
        pr_url=_text(payload.get("prUrl")),
        blocked_reason=_text(payload.get("blockedReason")),
        verification_commands=_commands(payload.get("verificationCommands")),
    )
    _validate_proof(evidence)
    return evidence


def _validate_exact_remote_head(evidence: AutoPublishEvidence) -> None:
    if not evidence.local_head or not evidence.remote_branch_head:
        raise AutoPublishEvidenceError(
            "auto publish evidence requires localHead and remoteBranchHead"
        )
    if evidence.local_head != evidence.remote_branch_head:
        raise AutoPublishEvidenceError("localHead must match remoteBranchHead")
    if not evidence.remote_verified:
        raise AutoPublishEvidenceError("auto publish evidence requires remoteVerified")


def _validate_proof(evidence: AutoPublishEvidence) -> None:
    if evidence.status in {"verified", "no_op_verified"}:
        if not evidence.verification_commands:
            raise AutoPublishEvidenceError(
                "verified auto publish evidence requires verificationCommands"
            )
    if evidence.status == "blocked":
        if not evidence.blocked_reason:
            raise AutoPublishEvidenceError(
                "blocked auto publish evidence requires blockedReason"
            )
        return
    if evidence.status == "failed":
        return
    if evidence.status == "no_op_verified":
        if evidence.action != "none":
            raise AutoPublishEvidenceError(
                "no_op_verified auto publish evidence action must be none"
            )
        _validate_exact_remote_head(evidence)
        return
    if evidence.status == "verified":
        if evidence.merged:
            if not evidence.pr_url:
                raise AutoPublishEvidenceError(
                    "verified merge evidence requires prUrl"
                )
            return
        if evidence.pushed:
            _validate_exact_remote_head(evidence)
            return
        raise AutoPublishEvidenceError("verified evidence must prove push or merge")
