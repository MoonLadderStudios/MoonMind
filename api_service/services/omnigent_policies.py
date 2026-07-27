"""Persistent immutable Omnigent policy lifecycle service."""

from __future__ import annotations

from datetime import UTC, datetime
import os
import re
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.db.models import OmnigentPolicy, OmnigentPolicyVersion
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


def validate_policy(document: PolicyDocument) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return stable diagnostics consumed before credentials or host mutation."""

    diagnostics: list[dict[str, str]] = []
    required_checks = (
        ("host", "mode", "OMNIGENT_HOST_MODE_UNAVAILABLE"),
        ("host", "serverImageRef", "OMNIGENT_INVALID_IMAGE_REF"),
        ("network", "egressProfileRef", "OMNIGENT_ENFORCED_EGRESS_MISSING"),
        ("workspace", "allowedClasses", "OMNIGENT_WORKSPACE_CLASS_UNSUPPORTED"),
        ("capture", "required", "OMNIGENT_CAPTURE_AUTHORITY_MISSING"),
    )
    payload = document.model_dump(by_alias=True, mode="json")
    for section, field, code in required_checks:
        if payload.get(section, {}).get(field) in (None, "", [], False):
            diagnostics.append({"code": code, "path": f"{section}.{field}", "message": "Required policy authority is missing."})
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
        policy = OmnigentPolicy(policy_id=policy_id, name=name, owner_user_id=owner_user_id, visibility=visibility)
        self.session.add(policy)
        row = self._version(policy_id, 1, document, actor, clone_source_ref=clone_source_ref)
        self.session.add(row)
        await self.session.commit()
        return row

    async def new_version(self, *, policy_id: str, document: PolicyDocument, actor: str,
                          expected_parent_ref: str) -> OmnigentPolicyVersion:
        await self.get_policy(policy_id)
        latest = (await self.session.execute(select(func.max(OmnigentPolicyVersion.version)).where(
            OmnigentPolicyVersion.policy_id == policy_id
        ))).scalar_one()
        if expected_parent_ref != f"{policy_id}@{latest}":
            raise PolicyConflict("stale policy version; reload before editing")
        row = self._version(policy_id, latest + 1, document, actor, parent_ref=expected_parent_ref)
        self.session.add(row)
        await self.session.commit()
        return row

    async def transition(self, *, policy_id: str, version: int, state: PolicyState, actor: str,
                         make_default: bool = False) -> OmnigentPolicyVersion:
        policy = await self.get_policy(policy_id)
        row = await self.get_version(policy_id, version)
        if state == PolicyState.ACTIVE and not row.validation_json.get("valid"):
            raise PolicyConflict("invalid policy cannot be activated")
        now = datetime.now(UTC)
        row.state = state.value
        if state == PolicyState.ACTIVE:
            row.activated_by, row.activated_at = actor, now
        if state == PolicyState.DISABLED:
            row.disabled_by, row.disabled_at = actor, now
        if make_default:
            if state != PolicyState.ACTIVE:
                raise PolicyConflict("only an active version can be the default")
            policy.default_version = version
        await self.session.commit()
        return row

    async def snapshot(self, policy_id: str, version: int) -> dict[str, Any]:
        row = await self.get_version(policy_id, version)
        return compile_policy_snapshot(policy_id=policy_id, version=version, document=row.document_json, validation=row.validation_json)

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


def bootstrap_document(*, host_mode: str, execution_profile_ref: str) -> PolicyDocument:
    """Project legacy built-ins into an explicit, reviewable bootstrap policy."""

    image_pattern = re.compile(r"^[^\s@]+@sha256:[0-9a-f]{64}$")
    server_image = os.getenv("OMNIGENT_IMAGE_REF", "").strip()
    host_image = os.getenv("OMNIGENT_HOST_IMAGE_REF", "").strip()
    return PolicyDocument.model_validate({
        "schemaVersion": 1,
        "endpoint": {"ref": "default", "bridgeModes": ["embedded", "proxy"]},
        "execution": {"profileRef": execution_profile_ref, "harness": "codex-native", "agentIdentities": ["codex"]},
        "host": {"mode": host_mode, "backendRef": "compose" if host_mode == "static_compose" else "container-backend",
                 "architectures": ["amd64", "arm64"],
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
        if await session.get(OmnigentPolicy, policy_id):
            continue
        document = bootstrap_document(host_mode=host_mode, execution_profile_ref=profile_ref)
        row = await service.create(policy_id=policy_id, name=name, owner_user_id=None, visibility="deployment",
                                   document=document, actor="bootstrap")
        row.env_fallback_used = any(
            os.getenv(variable, "").strip() == document.host.get(field)
            for variable, field in (
                ("OMNIGENT_IMAGE_REF", "serverImageRef"),
                ("OMNIGENT_HOST_IMAGE_REF", "hostImageRef"),
            )
        )
        await session.commit()
        seeded.append(policy_id)
    return seeded
