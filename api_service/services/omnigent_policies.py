"""Persistent immutable Omnigent policy lifecycle service."""

from __future__ import annotations

from datetime import UTC, datetime
import os
import platform
import re
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.db.models import (
    OmnigentBridgeSession,
    OmnigentOAuthHostBindingRecord,
    OmnigentPolicy,
    OmnigentPolicyEvent,
    OmnigentPolicyVersion,
)
from moonmind.config.container_backend_settings import (
    ContainerBackendConfigError,
    resolve_container_backend_settings,
)
from moonmind.omnigent.policies import (
    PolicyDocument,
    PolicyState,
    compile_policy_snapshot,
    document_digest,
    normalize_document,
)


class PolicyConflict(ValueError):
    pass


class PolicyNotFound(LookupError):
    pass


_BRIDGE_TERMINAL_STATES = frozenset({"completed", "failed", "canceled", "timed_out"})


def _split_policy_ref(policy_ref: str) -> tuple[str, int]:
    policy_id, separator, version_text = policy_ref.rpartition("@")
    if not separator or not policy_id:
        raise PolicyConflict(f"invalid policy version reference: {policy_ref!r}")
    try:
        version = int(version_text)
    except ValueError as exc:
        raise PolicyConflict(f"invalid policy version reference: {policy_ref!r}") from exc
    if version < 1:
        raise PolicyConflict(f"invalid policy version reference: {policy_ref!r}")
    return policy_id, version


def validate_policy(
    document: PolicyDocument,
    *,
    capabilities: dict[str, set[str]] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return stable diagnostics consumed before credentials or host mutation."""

    diagnostics: list[dict[str, str]] = []
    payload = document.model_dump(by_alias=True, mode="json")
    image_pattern = re.compile(r"^[^\s@]+@sha256:[0-9a-f]{64}$")
    for field in ("serverImageRef", "hostImageRef"):
        if not image_pattern.fullmatch(payload["host"][field]):
            diagnostics.append({
                "code": "OMNIGENT_INVALID_IMAGE_REF",
                "path": f"host.{field}",
                "message": "Runtime images must use immutable sha256 digest references.",
            })
    if not payload["capture"]["required"]:
        diagnostics.append({
            "code": "OMNIGENT_CAPTURE_AUTHORITY_MISSING",
            "path": "capture.required",
            "message": "Activation requires complete capture authority.",
        })
    if payload["network"]["egressProfileRef"] == payload["network"]["attachmentRef"]:
        diagnostics.append({
            "code": "OMNIGENT_ENFORCED_EGRESS_MISSING",
            "path": "network.egressProfileRef",
            "message": "Network attachment and enforced-egress authority must be distinct references.",
        })
    architecture = {"x86_64": "amd64", "aarch64": "arm64"}.get(
        platform.machine().lower(), platform.machine().lower()
    )
    try:
        container_backend_enabled = resolve_container_backend_settings().enabled
    except ContainerBackendConfigError:
        container_backend_enabled = False
    deployment_capabilities = {
        "hostModes": {"static_compose"} | ({"on_demand_docker"} if container_backend_enabled else set()),
        "backends": {"compose"} | ({"container-backend"} if container_backend_enabled else set()),
        "architectures": {architecture},
        "providers": {"codex"},
        "workspaceClasses": {"workflow"},
    }
    declared = capabilities or deployment_capabilities
    capability_checks = (
        ("hostModes", payload["host"]["mode"], "host.mode", "OMNIGENT_HOST_MODE_UNAVAILABLE"),
        ("backends", payload["host"]["backendRef"], "host.backendRef", "OMNIGENT_BACKEND_UNAVAILABLE"),
    )
    for capability, value, path, code in capability_checks:
        if value not in declared.get(capability, set()):
            diagnostics.append({"code": code, "path": path, "message": f"{value!r} is not available in this deployment."})
    for capability, values, path, code in (
        ("architectures", payload["host"]["architectures"], "host.architectures", "OMNIGENT_ARCHITECTURE_UNAVAILABLE"),
        ("providers", payload["providerProfile"]["compatibleProviders"], "providerProfile.compatibleProviders", "OMNIGENT_PROVIDER_PROFILE_INCOMPATIBLE"),
        ("workspaceClasses", payload["workspace"]["allowedClasses"], "workspace.allowedClasses", "OMNIGENT_WORKSPACE_CLASS_UNSUPPORTED"),
    ):
        unsupported = sorted(set(values) - declared.get(capability, set()))
        if unsupported:
            diagnostics.append({"code": code, "path": path, "message": f"Unsupported values: {', '.join(unsupported)}."})
    valid = not diagnostics
    return (
        {"valid": valid, "diagnostics": diagnostics, "validatedAt": datetime.now(UTC).isoformat()},
        {"compatible": valid, "diagnosticCodes": [item["code"] for item in diagnostics]},
    )


class OmnigentPolicyService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def list(self) -> list[tuple[OmnigentPolicy, OmnigentPolicyVersion | None]]:
        policies = (await self.session.execute(select(OmnigentPolicy).order_by(OmnigentPolicy.name))).scalars().all()
        result = []
        for policy in policies:
            version = None
            if policy.default_version is not None:
                version = await self.get_version(policy.policy_id, policy.default_version)
            else:
                version = (await self.session.execute(
                    select(OmnigentPolicyVersion)
                    .where(OmnigentPolicyVersion.policy_id == policy.policy_id)
                    .order_by(OmnigentPolicyVersion.version.desc())
                    .limit(1)
                )).scalar_one_or_none()
            result.append((policy, version))
        return result

    async def get_policy(self, policy_id: str) -> OmnigentPolicy:
        policy = await self.session.get(OmnigentPolicy, policy_id)
        if policy is None:
            raise PolicyNotFound(policy_id)
        return policy

    async def get_version(self, policy_id: str, version: int) -> OmnigentPolicyVersion:
        row = (await self.session.execute(select(OmnigentPolicyVersion).where(
            OmnigentPolicyVersion.policy_id == policy_id,
            OmnigentPolicyVersion.version == version,
        ))).scalar_one_or_none()
        if row is None:
            raise PolicyNotFound(f"{policy_id}@{version}")
        return row

    async def versions(self, policy_id: str) -> list[OmnigentPolicyVersion]:
        await self.get_policy(policy_id)
        return list((await self.session.execute(select(OmnigentPolicyVersion).where(
            OmnigentPolicyVersion.policy_id == policy_id
        ).order_by(OmnigentPolicyVersion.version.desc()))).scalars())

    async def create(self, *, policy_id: str, name: str, owner_user_id: Any, visibility: str,
                     document: PolicyDocument, actor: str, clone_source_ref: str | None = None) -> OmnigentPolicyVersion:
        if await self.session.get(OmnigentPolicy, policy_id):
            raise PolicyConflict("policy identity already exists")
        if clone_source_ref is not None:
            source_policy_id, source_version = _split_policy_ref(clone_source_ref)
            try:
                await self.get_version(source_policy_id, source_version)
            except PolicyNotFound as exc:
                raise PolicyConflict(
                    f"clone source does not exist: {clone_source_ref}"
                ) from exc
        policy = OmnigentPolicy(policy_id=policy_id, name=name, owner_user_id=owner_user_id, visibility=visibility)
        self.session.add(policy)
        row = self._version(policy_id, 1, document, actor, clone_source_ref=clone_source_ref)
        self.session.add(row)
        self._event(policy_id, 1, "version_created", actor, {
            "cloneSourceRef": clone_source_ref, "digest": row.digest,
        })
        try:
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            raise PolicyConflict("policy identity or name already exists") from exc
        return row

    async def new_version(self, *, policy_id: str, document: PolicyDocument, actor: str,
                          expected_parent_ref: str) -> OmnigentPolicyVersion:
        # Serialize allocation on the stable identity. The unique constraint is
        # the final authority on engines where row locking is unavailable.
        policy = (await self.session.execute(
            select(OmnigentPolicy)
            .where(OmnigentPolicy.policy_id == policy_id)
            .with_for_update()
        )).scalar_one_or_none()
        if policy is None:
            raise PolicyNotFound(policy_id)
        latest = (await self.session.execute(select(func.max(OmnigentPolicyVersion.version)).where(
            OmnigentPolicyVersion.policy_id == policy_id
        ))).scalar_one()
        if expected_parent_ref != f"{policy_id}@{latest}":
            raise PolicyConflict("stale policy version; reload before editing")
        row = self._version(policy_id, latest + 1, document, actor, parent_ref=expected_parent_ref)
        self.session.add(row)
        self._event(policy_id, row.version, "version_created", actor, {
            "parentRef": expected_parent_ref, "digest": row.digest,
        })
        try:
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            raise PolicyConflict("concurrent policy edit; reload before editing") from exc
        return row

    async def transition(self, *, policy_id: str, version: int, state: PolicyState, actor: str,
                         make_default: bool = False) -> OmnigentPolicyVersion:
        policy = (await self.session.execute(
            select(OmnigentPolicy)
            .where(OmnigentPolicy.policy_id == policy_id)
            .with_for_update()
        )).scalar_one_or_none()
        if policy is None:
            raise PolicyNotFound(policy_id)
        row = (await self.session.execute(
            select(OmnigentPolicyVersion)
            .where(
                OmnigentPolicyVersion.policy_id == policy_id,
                OmnigentPolicyVersion.version == version,
            )
            .with_for_update()
        )).scalar_one_or_none()
        if row is None:
            raise PolicyNotFound(f"{policy_id}@{version}")
        allowed = {
            PolicyState.DRAFT: {PolicyState.ACTIVE, PolicyState.DISABLED},
            PolicyState.ACTIVE: {PolicyState.DISABLED, PolicyState.DEPRECATED, PolicyState.SUPERSEDED},
            PolicyState.DEPRECATED: {PolicyState.ACTIVE, PolicyState.DISABLED},
            PolicyState.DISABLED: {PolicyState.ACTIVE},
            PolicyState.SUPERSEDED: {PolicyState.ACTIVE},
        }
        current = PolicyState(row.state)
        if state != current and state not in allowed[current]:
            raise PolicyConflict(f"invalid policy transition: {current.value} -> {state.value}")
        if state == PolicyState.ACTIVE and not row.validation_json.get("valid"):
            raise PolicyConflict("invalid policy cannot be activated")
        if (
            state in {PolicyState.DISABLED, PolicyState.DEPRECATED, PolicyState.SUPERSEDED}
            and policy.default_version == version
        ):
            raise PolicyConflict(
                "default policy version cannot be made unavailable; switch the default first"
            )
        if state in {PolicyState.DISABLED, PolicyState.DEPRECATED, PolicyState.SUPERSEDED}:
            bound = (await self.session.execute(
                select(OmnigentOAuthHostBindingRecord.binding_ref).where(
                    OmnigentOAuthHostBindingRecord.launch_policy_ref
                    == f"{policy_id}@{version}"
                ).limit(1)
            )).scalar_one_or_none()
            if bound is not None:
                raise PolicyConflict(
                    "policy version is bound to an active host profile and cannot be made unavailable"
                )
            bridge_rows = list((await self.session.execute(
                select(OmnigentBridgeSession)
            )).scalars())
            policy_ref = f"{policy_id}@{version}"
            active_bridge = next((
                bridge.bridge_session_id
                for bridge in bridge_rows
                if str(bridge.status).lower() not in _BRIDGE_TERMINAL_STATES
                and isinstance(bridge.effective_launch_snapshot_json, dict)
                and isinstance(
                    bridge.effective_launch_snapshot_json.get("policyAuthority"),
                    dict,
                )
                and bridge.effective_launch_snapshot_json["policyAuthority"].get(
                    "policyRef"
                ) == policy_ref
            ), None)
            if active_bridge is not None:
                raise PolicyConflict(
                    "policy version is bound to an active bridge session and cannot be made unavailable"
                )
        now = datetime.now(UTC)
        row.state = state.value
        if state == PolicyState.ACTIVE:
            row.activated_by, row.activated_at = actor, now
            if policy.default_version not in (None, version) and not row.supersedes_ref:
                row.supersedes_ref = f"{policy_id}@{policy.default_version}"
        if state == PolicyState.DISABLED:
            row.disabled_by, row.disabled_at = actor, now
        if make_default:
            if state != PolicyState.ACTIVE:
                raise PolicyConflict("only an active version can be the default")
            previous_default = policy.default_version
            policy.default_version = version
            self._event(policy_id, version, "default_changed", actor, {
                "previousVersion": previous_default,
                "newVersion": version,
            })
        self._event(policy_id, version, "lifecycle_transition", actor, {
            "from": current.value, "to": state.value, "makeDefault": make_default,
        })
        await self.session.commit()
        return row

    async def audit(self, policy_id: str) -> list[OmnigentPolicyEvent]:
        await self.get_policy(policy_id)
        return list((await self.session.execute(
            select(OmnigentPolicyEvent)
            .where(OmnigentPolicyEvent.policy_id == policy_id)
            .order_by(OmnigentPolicyEvent.created_at, OmnigentPolicyEvent.event_id)
        )).scalars())

    async def usage(self, policy_id: str, version: int) -> dict[str, Any]:
        """Return persisted dependents and lifecycle impact for one immutable version."""

        policy = await self.get_policy(policy_id)
        row = await self.get_version(policy_id, version)
        policy_ref = f"{policy_id}@{version}"
        host_bindings = list((await self.session.execute(
            select(OmnigentOAuthHostBindingRecord.binding_ref).where(
                OmnigentOAuthHostBindingRecord.launch_policy_ref == policy_ref
            ).order_by(OmnigentOAuthHostBindingRecord.binding_ref)
        )).scalars())
        # Bridge sessions keep the complete immutable authority in their launch
        # snapshot. Resolve dependents from persisted evidence instead of mutable
        # current defaults so historical usage remains inspectable after rollout.
        bridge_rows = list((await self.session.execute(
            select(OmnigentBridgeSession).order_by(
                OmnigentBridgeSession.bridge_session_id
            )
        )).scalars())
        bridge_sessions: list[str] = []
        active_bridge_sessions: list[str] = []
        workflow_ids: set[str] = set()
        provider_profile_ids = {
            str(value)
            for value in (await self.session.execute(
                select(OmnigentOAuthHostBindingRecord.provider_profile_id).where(
                    OmnigentOAuthHostBindingRecord.launch_policy_ref == policy_ref
                )
            )).scalars()
        }
        for bridge in bridge_rows:
            launch = bridge.effective_launch_snapshot_json
            if not isinstance(launch, dict):
                continue
            authority = launch.get("policyAuthority")
            if not isinstance(authority, dict) or authority.get("policyRef") != policy_ref:
                continue
            bridge_sessions.append(bridge.bridge_session_id)
            workflow_ids.add(bridge.moonmind_workflow_id)
            if bridge.provider_profile_id:
                provider_profile_ids.add(bridge.provider_profile_id)
            if str(bridge.status).lower() not in _BRIDGE_TERMINAL_STATES:
                active_bridge_sessions.append(bridge.bridge_session_id)

        is_default = policy.default_version == version
        blockers = []
        if is_default:
            blockers.append("Switch the policy default before disabling or deprecating this version.")
        if host_bindings:
            blockers.append("Move dependent host profiles before disabling or deprecating this version.")
        if active_bridge_sessions:
            blockers.append("Wait for dependent bridge sessions to finish before disabling or deprecating this version.")
        return {
            "policyRef": policy_ref,
            "state": row.state,
            "default": is_default,
            "dependents": {
                "hostBindings": host_bindings,
                "hostBindingCount": len(host_bindings),
                "providerProfiles": sorted(provider_profile_ids),
                "providerProfileCount": len(provider_profile_ids),
                "workflows": sorted(workflow_ids),
                "workflowCount": len(workflow_ids),
                "bridgeSessions": bridge_sessions,
                "bridgeSessionCount": len(bridge_sessions),
                "activeBridgeSessions": active_bridge_sessions,
                "activeBridgeSessionCount": len(active_bridge_sessions),
            },
            "activationImpact": {
                "willSwitchDefault": not is_default,
                "compatible": bool(row.compatibility_json.get("compatible")),
                "diagnostics": row.validation_json.get("diagnostics", []),
            },
            "unavailabilityBlockers": blockers,
        }

    async def snapshot(self, policy_id: str, version: int) -> dict[str, Any]:
        row = await self.get_version(policy_id, version)
        return compile_policy_snapshot(policy_id=policy_id, version=version, document=row.document_json, validation=row.validation_json)

    async def resolve_runtime_snapshot(self, policy_ref: str) -> dict[str, Any]:
        """Resolve exact, active runtime authority before any external side effect."""

        policy_id, version = _split_policy_ref(policy_ref)
        row = await self.get_version(policy_id, version)
        if row.state != PolicyState.ACTIVE.value:
            raise PolicyConflict(f"runtime policy is not active: {policy_ref}")
        if not row.validation_json.get("valid"):
            raise PolicyConflict(f"runtime policy is not valid: {policy_ref}")
        snapshot = compile_policy_snapshot(
            policy_id=policy_id,
            version=version,
            document=row.document_json,
            validation=row.validation_json,
        )
        if snapshot["policyDigest"] != row.digest:
            raise PolicyConflict(f"runtime policy digest conflict: {policy_ref}")
        return snapshot

    @staticmethod
    def _version(policy_id: str, version: int, document: PolicyDocument, actor: str, **lineage: Any) -> OmnigentPolicyVersion:
        normalized = normalize_document(document)
        validation, compatibility = validate_policy(document)
        return OmnigentPolicyVersion(
            id=uuid4(), policy_id=policy_id, version=version, state="draft",
            document_json=normalized, digest=document_digest(normalized),
            created_by=actor, validation_json=validation,
            compatibility_json=compatibility, rollout_json=normalized["rollout"], **lineage,
        )

    def _event(
        self, policy_id: str, version: int | None, event_type: str,
        actor: str, detail: dict[str, Any],
    ) -> None:
        self.session.add(OmnigentPolicyEvent(
            policy_id=policy_id, version=version, event_type=event_type,
            actor=actor, detail_json=detail,
        ))


def bootstrap_document(*, host_mode: str, execution_profile_ref: str) -> PolicyDocument:
    """Project legacy built-ins into an explicit, reviewable bootstrap policy."""

    image_pattern = re.compile(r"^[^\s@]+@sha256:[0-9a-f]{64}$")
    server_image = os.getenv("OMNIGENT_IMAGE_REF", "").strip()
    host_image = os.getenv("OMNIGENT_HOST_IMAGE_REF", "").strip()
    architecture = {"x86_64": "amd64", "aarch64": "arm64"}.get(
        platform.machine().lower(), platform.machine().lower()
    )
    return PolicyDocument.model_validate({
        "schemaVersion": 1,
        "endpoint": {"ref": "default", "bridgeModes": ["embedded", "proxy"]},
        "execution": {"profileRef": execution_profile_ref, "harness": "codex-native", "agentIdentities": ["codex"]},
        "host": {"mode": host_mode, "backendRef": "compose" if host_mode == "static_compose" else "container-backend",
                 "architectures": [architecture],
                 "serverImageRef": server_image if image_pattern.fullmatch(server_image) else "image-ref:omnigent-server",
                 "hostImageRef": host_image if image_pattern.fullmatch(host_image) else "image-ref:omnigent-codex-host"},
        "resources": {"cpuMillis": 2000, "memoryMiB": 4096, "processes": 256, "timeoutSeconds": 5400,
                      "temporaryStorageMiB": 256, "concurrency": 1},
        "network": {"attachmentRef": "local-network", "egressProfileRef": "enforced-default"},
        "workspace": {"allowedClasses": ["workflow"], "repositoryMutation": True,
                      "mountClasses": ["workspace", "oauth_home", "omnigent_state", "skills_tools", "artifacts", "cache"],
                      "runtimeUid": 1000, "runtimeGid": 1000},
        "providerProfile": {"compatibleProviders": ["codex"], "queueWhenBusy": True},
        "session": {"create": True, "firstMessage": "required", "continuation": True, "interruption": True,
                    "cancellation": True, "cleanup": "drain" if host_mode == "static_compose" else "remove"},
        "capture": {"required": True, "artifactClasses": ["events", "snapshots", "workspace"], "maxLogBytes": 10000000, "redaction": "required"},
        "checkpoint": {"capture": True, "resume": True, "branch": True, "publication": "approval", "promotion": "verified"},
        "remediation": {"actions": ["retry", "checkpoint_branch"], "riskTiers": {"retry": "low", "checkpoint_branch": "medium"},
                        "locks": True, "maxActions": 3, "autonomous": False},
        "rag": {"initialScope": "workflow", "followupScope": "session", "collectionRefs": ["workflow-default"],
                "tokenBudget": 8000, "fallback": "deny", "credentialRef": "retrieval-profile"},
        "approvals": {"actions": {"read": {"decision": "allow"}, "publish": {"decision": "approval_required",
                      "approvalClass": "publication", "reviewerRule": "workflow-owner"}, "host_mutation": {"decision": "deny"}}},
        "retention": {"days": 30, "deletion": "after-expiry"},
        "rollout": {"cohort": "bootstrap", "gate": "deployment-ready", "diagnostics": True},
    })


async def seed_bootstrap_policies(session: AsyncSession) -> list[str]:
    """Idempotently persist the three pre-existing built-in authorities."""

    service = OmnigentPolicyService(session)
    seeded: list[str] = []
    for policy_id, name, host_mode, profile_ref in (
        ("omnigent-codex", "Omnigent Codex execution", "static_compose", "omnigent-codex@1"),
        ("codex-static", "Codex static host", "static_compose", "omnigent-codex@1"),
        ("codex-on-demand", "Codex on-demand host", "on_demand_docker", "omnigent-codex@1"),
    ):
        document = bootstrap_document(host_mode=host_mode, execution_profile_ref=profile_ref)
        policy = await session.get(OmnigentPolicy, policy_id)
        if policy is None:
            row = await service.create(policy_id=policy_id, name=name, owner_user_id=None, visibility="deployment",
                                       document=document, actor="bootstrap")
        else:
            row = await service.get_version(policy_id, 1)
            if row.state != PolicyState.DRAFT.value or row.validation_json.get("valid"):
                continue
            normalized = normalize_document(document)
            validation, compatibility = validate_policy(document)
            if not validation.get("valid"):
                continue
            row.document_json = normalized
            row.digest = document_digest(normalized)
            row.validation_json = validation
            row.compatibility_json = compatibility
            row.rollout_json = normalized["rollout"]
            service._event(policy_id, 1, "bootstrap_repaired", "bootstrap", {
                "digest": row.digest,
            })
        row.env_fallback_used = False
        await session.commit()
        if row.validation_json.get("valid"):
            await service.transition(
                policy_id=policy_id,
                version=row.version,
                state=PolicyState.ACTIVE,
                actor="bootstrap",
                make_default=True,
            )
        seeded.append(policy_id)
    return seeded
