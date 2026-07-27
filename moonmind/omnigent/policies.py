"""Canonical, deterministic Omnigent policy document contracts.

MoonLadderStudios/MoonMind#3515. Policy documents contain portable references,
never credentials, Docker options, or raw host paths.
"""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PolicyState(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    DISABLED = "disabled"
    DEPRECATED = "deprecated"
    SUPERSEDED = "superseded"


class PolicyDocument(BaseModel):
    """Complete authority consumed at all Omnigent enforcement boundaries."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = Field(1, alias="schemaVersion")
    endpoint: dict[str, Any]
    execution: dict[str, Any]
    host: dict[str, Any]
    resources: dict[str, Any]
    network: dict[str, Any]
    workspace: dict[str, Any]
    provider_profile: dict[str, Any] = Field(alias="providerProfile")
    session: dict[str, Any]
    capture: dict[str, Any]
    checkpoint: dict[str, Any]
    remediation: dict[str, Any]
    rag: dict[str, Any]
    approvals: dict[str, Any]
    retention: dict[str, Any]
    rollout: dict[str, Any]

    @model_validator(mode="after")
    def reject_ambient_authority(self) -> "PolicyDocument":
        forbidden_keys = ("password", "token", "secretbody", "credentialbody")

        def inspect(value: object, path: str = "") -> None:
            if isinstance(value, Mapping):
                for key, item in value.items():
                    lowered = str(key).lower().replace("_", "")
                    if any(marker in lowered for marker in forbidden_keys):
                        raise ValueError(f"{path}{key} must be a reference, not secret material")
                    inspect(item, f"{path}{key}.")
            elif isinstance(value, list):
                for item in value:
                    inspect(item, path)
            elif isinstance(value, str):
                if "docker.sock" in value.lower() or value.startswith(("/", "~", ".")):
                    raise ValueError(f"{path[:-1]} contains a forbidden raw machine path")

        inspect(self.model_dump(by_alias=True, mode="json"))
        return self


def normalize_document(document: PolicyDocument | Mapping[str, Any]) -> dict[str, Any]:
    parsed = document if isinstance(document, PolicyDocument) else PolicyDocument.model_validate(document)
    return parsed.model_dump(by_alias=True, mode="json", exclude_none=False)


def document_digest(document: PolicyDocument | Mapping[str, Any]) -> str:
    canonical = json.dumps(normalize_document(document), sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()


def compile_policy_snapshot(
    *,
    policy_id: str,
    version: int,
    document: PolicyDocument | Mapping[str, Any],
    validation: Mapping[str, Any],
) -> dict[str, Any]:
    normalized = normalize_document(document)
    digest = document_digest(normalized)
    payload = {
        "schemaVersion": 1,
        "policyId": policy_id,
        "policyVersion": version,
        "policyRef": f"{policy_id}@{version}",
        "policyDigest": digest,
        "validation": dict(validation),
        "boundaries": normalized,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    payload["snapshotRef"] = "omnigent-policy:sha256:" + hashlib.sha256(canonical.encode()).hexdigest()
    return payload


def resolve_action(snapshot: Mapping[str, Any], action: str) -> dict[str, str]:
    """Resolve action authority without consulting mutable current policy state."""

    rules = snapshot.get("boundaries", {}).get("approvals", {}).get("actions", {})
    rule = rules.get(action, {"decision": "deny", "reason": "action is not allowlisted"})
    decision = rule.get("decision")
    if decision not in {"allow", "approval_required", "deny"}:
        decision = "deny"
    result = {"decision": decision, "reason": str(rule.get("reason", decision))}
    if decision == "approval_required":
        approval_class = rule.get("approvalClass")
        reviewer_rule = rule.get("reviewerRule")
        if not approval_class or not reviewer_rule:
            return {"decision": "deny", "reason": "approval rule is incomplete"}
        result.update(approvalClass=str(approval_class), reviewerRule=str(reviewer_rule))
    return result
