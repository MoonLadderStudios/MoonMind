"""Canonical, deterministic Omnigent policy document contracts.

MoonLadderStudios/MoonMind#3515. Policy documents contain portable references,
never credentials, Docker options, or raw host paths.
"""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Any, Literal, Mapping, Sequence

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


def validate_policy_document(
    document: PolicyDocument | Mapping[str, Any],
    *,
    capabilities: Mapping[str, Sequence[str]] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate cross-boundary authority against declared deployment capabilities.

    Capability collections are explicit inputs so validation is deterministic and
    can run before credential resolution or host mutation. An omitted collection
    means that capability is not deployment-constrained; an explicitly empty
    collection supports nothing and therefore fails closed.
    """

    payload = normalize_document(document)
    diagnostics: list[dict[str, str]] = []

    def add(code: str, path: str, message: str) -> None:
        diagnostics.append({"code": code, "path": path, "message": message})

    required = (
        ("host", "mode", "OMNIGENT_HOST_MODE_UNAVAILABLE"),
        ("host", "backendRef", "OMNIGENT_BACKEND_UNAVAILABLE"),
        ("host", "serverImageRef", "OMNIGENT_INVALID_IMAGE_REF"),
        ("host", "hostImageRef", "OMNIGENT_INVALID_IMAGE_REF"),
        ("network", "egressProfileRef", "OMNIGENT_ENFORCED_EGRESS_MISSING"),
        ("workspace", "allowedClasses", "OMNIGENT_WORKSPACE_CLASS_UNSUPPORTED"),
        ("providerProfile", "compatibleProviders", "OMNIGENT_PROVIDER_INCOMPATIBLE"),
        ("capture", "required", "OMNIGENT_CAPTURE_AUTHORITY_MISSING"),
    )
    for section, field, code in required:
        if payload.get(section, {}).get(field) in (None, "", [], False):
            add(code, f"{section}.{field}", "Required policy authority is missing.")

    for field in ("serverImageRef", "hostImageRef"):
        ref = str(payload["host"].get(field) or "")
        if ref and "@sha256:" not in ref and not ref.startswith("image-ref:"):
            add("OMNIGENT_INVALID_IMAGE_REF", f"host.{field}", "Image authority must be immutable or an explicit bootstrap image reference.")

    resources = payload["resources"]
    for field in ("cpuMillis", "memoryMiB", "processes", "timeoutSeconds", "temporaryStorageMiB", "concurrency"):
        value = resources.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            add("OMNIGENT_RESOURCE_LIMIT_INVALID", f"resources.{field}", "Resource limits must be positive integers.")

    remediation = payload["remediation"]
    actions = remediation.get("actions", [])
    tiers = remediation.get("riskTiers", {})
    if any(action not in tiers for action in actions):
        add("OMNIGENT_REMEDIATION_RISK_TIER_MISSING", "remediation.riskTiers", "Every remediation action requires an explicit risk tier.")
    if remediation.get("autonomous") and not remediation.get("locks"):
        add("OMNIGENT_AUTONOMOUS_REMEDIATION_LOCK_REQUIRED", "remediation.locks", "Autonomous remediation requires durable locking.")

    if payload["checkpoint"].get("branch") and not payload["checkpoint"].get("capture"):
        add("OMNIGENT_CHECKPOINT_CAPTURE_REQUIRED", "checkpoint.capture", "Checkpoint branching requires checkpoint capture.")
    if payload["rag"].get("fallback") != "deny" and not payload["rag"].get("credentialRef"):
        add("OMNIGENT_RAG_CREDENTIAL_REQUIRED", "rag.credentialRef", "Non-deny retrieval fallback requires an explicit credential reference.")

    capability_paths = {
        "hostModes": ("host", "mode", "OMNIGENT_HOST_MODE_UNAVAILABLE"),
        "backends": ("host", "backendRef", "OMNIGENT_BACKEND_UNAVAILABLE"),
        "architectures": ("host", "architectures", "OMNIGENT_ARCHITECTURE_UNSUPPORTED"),
        "egressProfiles": ("network", "egressProfileRef", "OMNIGENT_EGRESS_PROFILE_UNAVAILABLE"),
        "workspaceClasses": ("workspace", "allowedClasses", "OMNIGENT_WORKSPACE_CLASS_UNSUPPORTED"),
        "mountClasses": ("workspace", "mountClasses", "OMNIGENT_MOUNT_CLASS_UNSUPPORTED"),
        "providers": ("providerProfile", "compatibleProviders", "OMNIGENT_PROVIDER_INCOMPATIBLE"),
    }
    capability_summary: dict[str, Any] = {}
    for name, (section, field, code) in capability_paths.items():
        if capabilities is None or name not in capabilities:
            continue
        supported = set(capabilities[name])
        requested_value = payload[section].get(field)
        requested = requested_value if isinstance(requested_value, list) else [requested_value]
        missing = sorted(str(item) for item in requested if item not in supported)
        capability_summary[name] = {"requested": requested, "unsupported": missing}
        if missing:
            add(code, f"{section}.{field}", f"Unsupported deployment capability: {', '.join(missing)}.")

    diagnostics.sort(key=lambda item: (item["path"], item["code"]))
    valid = not diagnostics
    validation = {"valid": valid, "diagnostics": diagnostics}
    compatibility = {
        "compatible": valid,
        "diagnosticCodes": sorted({item["code"] for item in diagnostics}),
        "capabilities": capability_summary,
    }
    return validation, compatibility


def bind_action_approval(
    snapshot: Mapping[str, Any], action: str, *, expected_target_state: Mapping[str, Any]
) -> dict[str, Any]:
    """Bind an approval decision to immutable authority and target state."""

    decision = resolve_action(snapshot, action)
    return {
        **decision,
        "action": action,
        "policyRef": snapshot.get("policyRef"),
        "policyDigest": snapshot.get("policyDigest"),
        "snapshotRef": snapshot.get("snapshotRef"),
        "expectedTargetState": dict(expected_target_state),
    }


def approval_binding_is_current(
    binding: Mapping[str, Any], snapshot: Mapping[str, Any], *, current_target_state: Mapping[str, Any]
) -> bool:
    """Reject pending approvals whose policy or expected state has changed."""

    return all(
        (
            binding.get("policyRef") == snapshot.get("policyRef"),
            binding.get("policyDigest") == snapshot.get("policyDigest"),
            binding.get("snapshotRef") == snapshot.get("snapshotRef"),
            binding.get("expectedTargetState") == dict(current_target_state),
        )
    )


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
