"""Canonical, deterministic Omnigent policy document contracts.

MoonLadderStudios/MoonMind#3515. Policy documents contain portable references,
never credentials, Docker options, or raw host paths.
"""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Annotated, Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PolicyState(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    DISABLED = "disabled"
    DEPRECATED = "deprecated"
    SUPERSEDED = "superseded"


Ref = Annotated[str, Field(min_length=1, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:@/-]*$")]
PositiveInt = Annotated[int, Field(gt=0)]
NonNegativeInt = Annotated[int, Field(ge=0)]


class PolicySection(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EndpointPolicy(PolicySection):
    ref: Ref
    bridge_modes: list[Literal["embedded", "proxy"]] = Field(alias="bridgeModes", min_length=1)


class ExecutionPolicy(PolicySection):
    profile_ref: Ref = Field(alias="profileRef")
    harness: Ref
    agent_identities: list[Ref] = Field(alias="agentIdentities", min_length=1)


class HostPolicy(PolicySection):
    mode: Literal["static_compose", "on_demand_docker"]
    backend_ref: Ref = Field(alias="backendRef")
    architectures: list[Literal["amd64", "arm64"]] = Field(min_length=1)
    server_image_ref: Ref = Field(alias="serverImageRef")
    host_image_ref: Ref = Field(alias="hostImageRef")


class ResourcePolicy(PolicySection):
    cpu_millis: PositiveInt = Field(alias="cpuMillis")
    memory_mib: PositiveInt = Field(alias="memoryMiB")
    processes: PositiveInt
    timeout_seconds: PositiveInt = Field(alias="timeoutSeconds")
    temporary_storage_mib: PositiveInt = Field(alias="temporaryStorageMiB")
    concurrency: PositiveInt


class NetworkPolicy(PolicySection):
    attachment_ref: Ref = Field(alias="attachmentRef")
    egress_profile_ref: Ref = Field(alias="egressProfileRef")


class WorkspacePolicy(PolicySection):
    allowed_classes: list[Ref] = Field(alias="allowedClasses", min_length=1)
    repository_mutation: bool = Field(alias="repositoryMutation")
    mount_classes: list[Literal["workspace", "oauth_home", "omnigent_state", "skills_tools", "artifacts", "cache"]] = Field(alias="mountClasses")
    runtime_uid: NonNegativeInt = Field(alias="runtimeUid")
    runtime_gid: NonNegativeInt = Field(alias="runtimeGid")
    mount_rules: list["MountRule"] = Field(default_factory=list, alias="mountRules")
    artifact_boundary_ref: Ref | None = Field(None, alias="artifactBoundaryRef")
    skill_boundary_ref: Ref | None = Field(None, alias="skillBoundaryRef")
    tool_boundary_ref: Ref | None = Field(None, alias="toolBoundaryRef")
    oauth_boundary_ref: Ref | None = Field(None, alias="oauthBoundaryRef")
    state_boundary_ref: Ref | None = Field(None, alias="stateBoundaryRef")


class MountRule(PolicySection):
    source_ref: Ref = Field(alias="sourceRef")
    target_ref: Ref = Field(alias="targetRef")
    mode: Literal["read_only", "read_write"]


class ProviderProfilePolicy(PolicySection):
    compatible_providers: list[Ref] = Field(alias="compatibleProviders", min_length=1)
    queue_when_busy: bool = Field(alias="queueWhenBusy")


class SessionPolicy(PolicySection):
    create: bool
    first_message: Literal["required", "optional", "forbidden"] = Field(alias="firstMessage")
    continuation: bool
    interruption: bool
    cancellation: bool
    cleanup: Literal["drain", "remove"]


class CapturePolicy(PolicySection):
    required: bool
    artifact_classes: list[Ref] = Field(alias="artifactClasses", min_length=1)
    max_log_bytes: PositiveInt = Field(alias="maxLogBytes")
    redaction: Literal["required", "best_effort"]
    evidence_completeness: Literal["required", "best_effort"] = Field(
        "required", alias="evidenceCompleteness"
    )


class CheckpointPolicy(PolicySection):
    capture: bool
    resume: bool
    branch: bool
    publication: Literal["deny", "approval", "allow"]
    promotion: Literal["deny", "verified", "approval"]


class RemediationPolicy(PolicySection):
    actions: list[Ref]
    risk_tiers: dict[str, Literal["low", "medium", "high", "critical"]] = Field(alias="riskTiers")
    locks: bool
    max_actions: NonNegativeInt = Field(alias="maxActions")
    autonomous: bool


class RagPolicy(PolicySection):
    initial_scope: Ref = Field(alias="initialScope")
    followup_scope: Ref = Field(alias="followupScope")
    collection_refs: list[Ref] = Field(alias="collectionRefs")
    token_budget: PositiveInt = Field(alias="tokenBudget")
    latency_budget_ms: PositiveInt = Field(3000, alias="latencyBudgetMs")
    fallback: Literal["deny", "empty"]
    credential_ref: Ref = Field(alias="credentialRef")


class ApprovalRule(PolicySection):
    decision: Literal["allow", "approval_required", "deny"]
    approval_class: Ref | None = Field(None, alias="approvalClass")
    reviewer_rule: Ref | None = Field(None, alias="reviewerRule")
    reason: str | None = None

    @model_validator(mode="after")
    def require_approval_authority(self) -> "ApprovalRule":
        if self.decision == "approval_required" and not (
            self.approval_class and self.reviewer_rule
        ):
            raise ValueError("approval_required needs approvalClass and reviewerRule")
        return self


class ApprovalPolicy(PolicySection):
    actions: dict[str, ApprovalRule]


class RetentionPolicy(PolicySection):
    days: PositiveInt
    deletion: Literal["after-expiry", "manual"]


class RolloutPolicy(PolicySection):
    cohort: Ref
    gate: Ref
    diagnostics: bool
    diagnostic_ref: Ref | None = Field(None, alias="diagnosticRef")
    deprecation_ref: Ref | None = Field(None, alias="deprecationRef")


class PolicyDocument(BaseModel):
    """Complete authority consumed at all Omnigent enforcement boundaries."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = Field(1, alias="schemaVersion")
    endpoint: EndpointPolicy
    execution: ExecutionPolicy
    host: HostPolicy
    resources: ResourcePolicy
    network: NetworkPolicy
    workspace: WorkspacePolicy
    provider_profile: ProviderProfilePolicy = Field(alias="providerProfile")
    session: SessionPolicy
    capture: CapturePolicy
    checkpoint: CheckpointPolicy
    remediation: RemediationPolicy
    rag: RagPolicy
    approvals: ApprovalPolicy
    retention: RetentionPolicy
    rollout: RolloutPolicy

    @model_validator(mode="after")
    def reject_ambient_authority(self) -> "PolicyDocument":
        forbidden_keys = {
            "password", "accesstoken", "authtoken", "refreshtoken",
            "secretbody", "credentialbody",
        }

        def inspect(value: object, path: str = "") -> None:
            if isinstance(value, Mapping):
                for key, item in value.items():
                    lowered = str(key).lower().replace("_", "")
                    if lowered in forbidden_keys:
                        raise ValueError(f"{path}{key} must be a reference, not secret material")
                    inspect(item, f"{path}{key}.")
            elif isinstance(value, list):
                for item in value:
                    inspect(item, path)
            elif isinstance(value, str):
                if "docker.sock" in value.lower() or value.startswith(("/", "~", ".")):
                    raise ValueError(f"{path[:-1]} contains a forbidden raw machine path")

        inspect(self.model_dump(by_alias=True, mode="json"))
        if self.host.mode == "static_compose" and self.session.cleanup != "drain":
            raise ValueError("static_compose requires session.cleanup=drain")
        if self.host.mode == "on_demand_docker" and self.session.cleanup != "remove":
            raise ValueError("on_demand_docker requires session.cleanup=remove")
        if self.remediation.autonomous and not self.remediation.locks:
            raise ValueError("autonomous remediation requires locks")
        missing_tiers = set(self.remediation.actions) - set(self.remediation.risk_tiers)
        if missing_tiers:
            raise ValueError(f"remediation actions lack risk tiers: {sorted(missing_tiers)}")
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


def bind_approval_request(
    snapshot: Mapping[str, Any], action: str, *, target_expected_state: str
) -> dict[str, str]:
    """Create a complete immutable approval binding or fail closed."""

    authority = resolve_action(snapshot, action)
    if authority["decision"] != "approval_required":
        raise ValueError(f"{action} does not resolve to approval_required")
    required = ("policyRef", "policyDigest", "snapshotRef")
    missing = [field for field in required if not str(snapshot.get(field) or "").strip()]
    if missing:
        raise ValueError(f"policy snapshot lacks approval authority: {', '.join(missing)}")
    if not target_expected_state.strip():
        raise ValueError("target expected state is required")
    return {
        "policyRef": str(snapshot["policyRef"]),
        "policyDigest": str(snapshot["policyDigest"]),
        "snapshotRef": str(snapshot["snapshotRef"]),
        "targetExpectedState": target_expected_state,
        "approvalClass": authority["approvalClass"],
        "reviewerRule": authority["reviewerRule"],
    }


def validate_approval_binding(
    binding: Mapping[str, Any],
    snapshot: Mapping[str, Any],
    *,
    target_current_state: str,
) -> None:
    """Reject approval work when authority or optimistic target state is stale."""

    comparisons = {
        "policyRef": snapshot.get("policyRef"),
        "policyDigest": snapshot.get("policyDigest"),
        "snapshotRef": snapshot.get("snapshotRef"),
        "targetExpectedState": target_current_state,
    }
    stale = [
        field
        for field, current in comparisons.items()
        if not current or binding.get(field) != current
    ]
    if stale:
        raise ValueError(f"stale approval binding: {', '.join(stale)}")
