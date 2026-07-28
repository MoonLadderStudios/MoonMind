"""Persistent Omnigent agent-profile API (MoonLadderStudios/MoonMind#3517)."""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.api.routers.provider_profiles import _require_provider_profile_permission
from api_service.auth_providers import get_current_user
from api_service.db.base import get_async_session
from api_service.db.models import (
    OmnigentAgentProfile,
    OmnigentAgentProfileAuditEvent,
    OmnigentAgentProfileUsage,
    OmnigentAgentProfileVersion,
    OmnigentUpstreamAgentProjection,
    ManagedAgentProviderProfile,
    TemporalArtifact,
    User,
)
from api_service.services.omnigent_agent_profile_service import projection_identity

router = APIRouter(prefix="/api/omnigent/agent-profiles", tags=["Omnigent Agent Profiles"])
_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{1,127}$")
_FORBIDDEN_PARTS = {
    "credential", "credentials", "secret", "secrets", "password", "token",
    "dockerfile", "hostpath", "volumename", "privileged", "dockersocket",
}
_SAFE_REFERENCE_KEYS = {
    "credentialsource", "credentialsources",
}
_CONSUMER_TYPES = Literal["workflow", "schedule", "checkpoint", "remediation", "smoke"]

class AgentSource(BaseModel):
    upstream_id: str | None = Field(None, alias="upstreamId", max_length=255)
    upstream_version: str | None = Field(None, alias="upstreamVersion", max_length=255)
    bundle_artifact_ref: str | None = Field(None, alias="bundleArtifactRef", max_length=1024)
    bundle_digest: str | None = Field(None, alias="bundleDigest", pattern=r"^sha256:[0-9a-f]{64}$")
    @model_validator(mode="after")
    def exactly_one_source(self) -> "AgentSource":
        if bool(self.upstream_id) == bool(self.bundle_artifact_ref):
            raise ValueError("exactly one stable upstreamId or bundleArtifactRef is required")
        if self.bundle_artifact_ref and not self.bundle_digest:
            raise ValueError("bundleDigest is required for an artifact-backed bundle")
        return self

class ExecutionDefaults(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    profile_ref: str = Field(alias="defaultExecutionProfileRef", min_length=1, max_length=255)
    allowed_launch_policy_refs: list[str] = Field(
        alias="allowedLaunchPolicyRefs", min_length=1, max_length=32
    )

class ProviderRequirements(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    runtime_id: str = Field(alias="runtimeId", min_length=1, max_length=128)
    provider_ids: list[str] = Field(default_factory=list, alias="providerIds", max_length=32)
    credential_source: str = Field(alias="credentialSource", min_length=1, max_length=64)
    materialization_mode: str = Field(alias="materializationMode", min_length=1, max_length=64)

class WorkspaceDefaults(BaseModel):
    # Unknown workspace defaults are retained for forward-compatible capability
    # declarations, then inspected by the document-wide authority validator.
    model_config = ConfigDict(populate_by_name=True, extra="allow")
    mutation: Literal["read_only", "allowed", "checkpoint_branch"] = "allowed"
    required_capabilities: list[str] = Field(
        default_factory=list, alias="requiredCapabilities", max_length=32
    )

class ContinuationDefaults(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    checkpoint: bool = True
    branch: bool = True
    remediation: bool = True

class ModelDefaults(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    model: str | None = Field(None, max_length=128)
    effort: Literal["minimal", "low", "medium", "high", "xhigh"] | None = None
    settings: dict[str, str | int | float | bool] = Field(default_factory=dict)

class CaptureDefaults(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    stream: bool = True
    retention_days: int | None = Field(None, alias="retentionDays", ge=1, le=3650)
    evidence: bool = True

class RagDefaults(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    initial: dict[str, Any] = Field(default_factory=dict)
    follow_up: dict[str, Any] = Field(default_factory=dict, alias="followUp")
    max_tokens: int | None = Field(None, alias="maxTokens", ge=0, le=1_000_000)
    max_latency_ms: int | None = Field(None, alias="maxLatencyMs", ge=1, le=600_000)

class PublishDefaults(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    mode: Literal["none", "draft", "ready", "auto"] = "none"

class AgentProfileDocument(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    schema_version: Literal["moonmind.omnigent-agent-profile.v1"] = Field("moonmind.omnigent-agent-profile.v1", alias="schemaVersion")
    endpoint_ref: str = Field(alias="endpointRef", min_length=1, max_length=128)
    bridge_mode: str = Field(alias="bridgeMode", min_length=1, max_length=64)
    source: AgentSource
    harness: str = Field(min_length=1, max_length=64)
    required_capabilities: list[str] = Field(default_factory=list, alias="requiredCapabilities", max_length=64)
    execution: ExecutionDefaults
    provider_requirements: ProviderRequirements = Field(alias="providerRequirements")
    model: ModelDefaults = Field(default_factory=ModelDefaults)
    workspace: WorkspaceDefaults = Field(default_factory=WorkspaceDefaults)
    skills: list[str] = Field(default_factory=list, max_length=128)
    tools: list[str] = Field(default_factory=list, max_length=128)
    capture: CaptureDefaults = Field(default_factory=CaptureDefaults)
    rag: RagDefaults = Field(default_factory=RagDefaults)
    continuations: ContinuationDefaults = Field(default_factory=ContinuationDefaults)
    publish: PublishDefaults = Field(default_factory=PublishDefaults)
    policy_ref: str = Field(alias="policyRef", min_length=1, max_length=255)
    @model_validator(mode="after")
    def reject_authority(self) -> "AgentProfileDocument":
        def walk(value: Any) -> None:
            if isinstance(value, dict):
                for key, child in value.items():
                    normalized_key = re.sub(r"[^a-z]", "", key.lower())
                    if (
                        normalized_key not in _SAFE_REFERENCE_KEYS
                        and any(part in normalized_key for part in _FORBIDDEN_PARTS)
                    ):
                        raise ValueError(f"{key} is runtime authority and cannot be stored in a profile")
                    walk(child)
            elif isinstance(value, list):
                for child in value: walk(child)
        walk(self.model_dump(by_alias=True))
        return self

class ProfileCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    profile_id: str = Field(alias="profileId")
    display_name: str = Field(alias="displayName", min_length=1, max_length=255)
    description: str | None = None
    visibility: Literal["private", "workspace", "public"] = "private"
    document: AgentProfileDocument

class VersionCreate(BaseModel):
    document: AgentProfileDocument

class CloneCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    profile_id: str = Field(alias="profileId")
    display_name: str = Field(alias="displayName", min_length=1, max_length=255)
    version: int | None = Field(None, ge=1)

class SnapshotCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    consumer_type: _CONSUMER_TYPES = Field(alias="consumerType")
    consumer_id: str = Field(alias="consumerId", min_length=1, max_length=255)
    version: int | None = Field(None, ge=1)
    overrides: dict[str, Any] = Field(default_factory=dict)

class ValidateCreate(BaseModel):
    version: int | None = Field(None, ge=1)

def _assert_owner(profile: OmnigentAgentProfile, user: User) -> None:
    if profile.owner_id != user.id and not user.is_superuser:
        raise HTTPException(403, "profile owner permission required")

def _audit(
    profile_id: str,
    action: str,
    user: User,
    *,
    version: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> OmnigentAgentProfileAuditEvent:
    return OmnigentAgentProfileAuditEvent(
        profile_id=profile_id,
        action=action,
        version=version,
        actor_id=user.id,
        metadata_json=metadata or {},
    )

def _normalized(document: AgentProfileDocument) -> dict[str, Any]:
    return document.model_dump(mode="json", by_alias=True, exclude_none=True)

def _digest(document: dict[str, Any]) -> str:
    encoded = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()

def _response(profile: OmnigentAgentProfile, versions: list[OmnigentAgentProfileVersion]) -> dict[str, Any]:
    return {"profileId": profile.profile_id, "displayName": profile.display_name, "description": profile.description,
            "visibility": profile.visibility, "state": profile.state, "activeVersion": profile.active_version,
            "default": profile.default_for_runtime, "versions": [{"version": v.version, "digest": v.digest, "document": v.document,
            "parentVersion": v.parent_version, "clonedFromProfileId": v.cloned_from_profile_id,
            "clonedFromVersion": v.cloned_from_version, "upstreamSnapshot": v.upstream_snapshot,
            "validationResult": v.validation_result, "createdAt": v.created_at} for v in versions]}

async def _load(session: AsyncSession, profile_id: str) -> tuple[OmnigentAgentProfile, list[OmnigentAgentProfileVersion]]:
    profile = await session.get(OmnigentAgentProfile, profile_id)
    if not profile: raise HTTPException(404, "agent profile not found")
    versions = list((await session.execute(select(OmnigentAgentProfileVersion).where(OmnigentAgentProfileVersion.profile_id == profile_id).order_by(OmnigentAgentProfileVersion.version.desc()))).scalars())
    return profile, versions

@router.get("")
async def list_profiles(session: AsyncSession = Depends(get_async_session), current_user: User = Depends(get_current_user())) -> list[dict[str, Any]]:
    _require_provider_profile_permission(current_user, "provider_profiles.read")
    profiles = list((await session.execute(select(OmnigentAgentProfile).order_by(OmnigentAgentProfile.display_name))).scalars())
    return [_response(p, []) for p in profiles if p.visibility != "private" or p.owner_id == current_user.id]

@router.post("", status_code=status.HTTP_201_CREATED)
async def create_profile(body: ProfileCreate, session: AsyncSession = Depends(get_async_session), current_user: User = Depends(get_current_user())) -> dict[str, Any]:
    _require_provider_profile_permission(current_user, "provider_profiles.write")
    if not _ID.fullmatch(body.profile_id): raise HTTPException(422, "profileId must be a stable lowercase identifier")
    if await session.get(OmnigentAgentProfile, body.profile_id): raise HTTPException(409, "agent profile already exists")
    document = _normalized(body.document)
    profile = OmnigentAgentProfile(profile_id=body.profile_id, display_name=body.display_name, description=body.description, owner_id=current_user.id, visibility=body.visibility)
    version = OmnigentAgentProfileVersion(profile_id=body.profile_id, version=1, digest=_digest(document), document=document, created_by=current_user.id)
    session.add_all([profile, version, _audit(body.profile_id, "created", current_user, version=1)])
    await session.commit(); await session.refresh(version)
    return _response(profile, [version])

@router.get("/{profile_id}")
async def get_profile(profile_id: str, session: AsyncSession = Depends(get_async_session), current_user: User = Depends(get_current_user())) -> dict[str, Any]:
    _require_provider_profile_permission(current_user, "provider_profiles.read")
    profile, versions = await _load(session, profile_id)
    if profile.visibility == "private" and profile.owner_id != current_user.id: raise HTTPException(404, "agent profile not found")
    return _response(profile, versions)

@router.post("/{profile_id}/versions", status_code=status.HTTP_201_CREATED)
async def create_version(profile_id: str, body: VersionCreate, session: AsyncSession = Depends(get_async_session), current_user: User = Depends(get_current_user())) -> dict[str, Any]:
    _require_provider_profile_permission(current_user, "provider_profiles.write")
    profile, versions = await _load(session, profile_id)
    _assert_owner(profile, current_user)
    document = _normalized(body.document); digest = _digest(document)
    if any(v.digest == digest for v in versions): raise HTTPException(409, "identical immutable version already exists")
    await session.execute(
        select(OmnigentAgentProfile.profile_id)
        .where(OmnigentAgentProfile.profile_id == profile_id)
        .with_for_update()
    )
    number = int((await session.scalar(
        select(func.max(OmnigentAgentProfileVersion.version)).where(
            OmnigentAgentProfileVersion.profile_id == profile_id
        )
    )) or 0) + 1
    version = OmnigentAgentProfileVersion(profile_id=profile_id, version=number, digest=digest, document=document, parent_version=versions[0].version if versions else None, created_by=current_user.id)
    session.add_all([version, _audit(profile_id, "version_created", current_user, version=number)])
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(409, "concurrent or identical immutable version") from exc
    await session.refresh(version)
    return _response(profile, [version])

@router.post("/{profile_id}/clone", status_code=status.HTTP_201_CREATED)
async def clone_profile(profile_id: str, body: CloneCreate, session: AsyncSession = Depends(get_async_session), current_user: User = Depends(get_current_user())) -> dict[str, Any]:
    _require_provider_profile_permission(current_user, "provider_profiles.write")
    source, versions = await _load(session, profile_id)
    if source.visibility == "private" and source.owner_id != current_user.id:
        raise HTTPException(404, "agent profile not found")
    if not _ID.fullmatch(body.profile_id):
        raise HTTPException(422, "profileId must be a stable lowercase identifier")
    target = next((v for v in versions if v.version == (body.version or source.active_version)), None)
    if target is None:
        raise HTTPException(409, "source profile has no selected version")
    if await session.get(OmnigentAgentProfile, body.profile_id):
        raise HTTPException(409, "agent profile already exists")
    profile = OmnigentAgentProfile(
        profile_id=body.profile_id, display_name=body.display_name,
        description=source.description, owner_id=current_user.id, visibility="private",
    )
    version = OmnigentAgentProfileVersion(
        profile_id=body.profile_id, version=1, digest=target.digest,
        document=target.document, cloned_from_profile_id=profile_id,
        cloned_from_version=target.version, created_by=current_user.id,
    )
    session.add_all([
        profile, version,
        _audit(body.profile_id, "cloned", current_user, version=1, metadata={
            "sourceProfileId": profile_id, "sourceVersion": target.version,
        }),
    ])
    await session.commit()
    return _response(profile, [version])

@router.post("/{profile_id}/activate/{version}")
async def activate(profile_id: str, version: int, session: AsyncSession = Depends(get_async_session), current_user: User = Depends(get_current_user())) -> dict[str, Any]:
    _require_provider_profile_permission(current_user, "provider_profiles.write")
    profile, versions = await _load(session, profile_id)
    _assert_owner(profile, current_user)
    target = next((v for v in versions if v.version == version), None)
    if not target: raise HTTPException(404, "profile version not found")
    if not target.validation_result or target.validation_result.get("ready") is not True:
        raise HTTPException(409, "profile version must pass validation before activation")
    profile.active_version = version; profile.state = "active"
    session.add(_audit(profile_id, "activated", current_user, version=version))
    await session.commit()
    return _response(profile, versions)

@router.post("/{profile_id}/validate")
async def validate_profile(profile_id: str, body: ValidateCreate, session: AsyncSession = Depends(get_async_session), current_user: User = Depends(get_current_user())) -> dict[str, Any]:
    """Perform bounded, credential-free readiness validation before activation."""
    _require_provider_profile_permission(current_user, "provider_profiles.write")
    profile, versions = await _load(session, profile_id)
    _assert_owner(profile, current_user)
    target_number = body.version or profile.active_version or (versions[0].version if versions else None)
    target = next((v for v in versions if v.version == target_number), None)
    if target is None:
        raise HTTPException(404, "profile version not found")
    document = target.document
    checks: list[dict[str, Any]] = []
    source = document["source"]
    if source.get("upstreamId"):
        projection = await session.get(
            OmnigentUpstreamAgentProjection,
            projection_identity(
                document["endpointRef"], source["upstreamId"],
                source.get("upstreamVersion"),
            ),
        )
        checks.append({
            "name": "upstream_identity",
            "ready": bool(projection and projection.available and projection.compatible),
            "reason": None if projection and projection.available and projection.compatible
            else "stable upstream identity is unavailable or incompatible",
        })
        target.upstream_snapshot = projection.metadata_snapshot if projection else None
    else:
        artifact_id = source["bundleArtifactRef"].removeprefix("artifact:")
        artifact = await session.get(TemporalArtifact, artifact_id)
        expected = source["bundleDigest"].removeprefix("sha256:")
        safe_types = {
            "application/zip", "application/x-tar", "application/gzip",
            "application/vnd.moonmind.omnigent-agent-bundle+zip",
        }
        bundle_ready = bool(
            artifact and artifact.sha256 == expected
            and artifact.content_type in safe_types
            and artifact.size_bytes is not None
            and 0 < artifact.size_bytes <= 50 * 1024 * 1024
            and artifact.created_by_principal
        )
        checks.append({
            "name": "bundle_provenance", "ready": bundle_ready,
            "reason": None if bundle_ready else
            "bundle must resolve to a creator-attributed artifact with matching digest, safe media type, and bounded size",
        })
    requirements = document["providerRequirements"]
    providers = list((await session.execute(select(ManagedAgentProviderProfile).where(
        ManagedAgentProviderProfile.runtime_id == requirements["runtimeId"],
        ManagedAgentProviderProfile.enabled.is_(True),
    ))).scalars())
    compatible_provider = any(
        row.credential_source.value == requirements["credentialSource"]
        and row.runtime_materialization_mode.value == requirements["materializationMode"]
        and (not requirements.get("providerIds") or row.provider_id in requirements["providerIds"])
        for row in providers
    )
    checks.append({
        "name": "provider_profile", "ready": compatible_provider,
        "reason": None if compatible_provider else "no enabled compatible Provider Profile",
    })
    ready = all(check["ready"] for check in checks)
    target.validation_result = {
        "schemaVersion": "moonmind.omnigent-agent-profile-validation.v1",
        "ready": ready, "checks": checks,
    }
    session.add(_audit(profile_id, "validated", current_user, version=target.version,
                       metadata={"ready": ready}))
    await session.commit()
    return target.validation_result

@router.post("/{profile_id}/{action}")
async def lifecycle(profile_id: str, action: Literal["disable", "deprecate"], session: AsyncSession = Depends(get_async_session), current_user: User = Depends(get_current_user())) -> dict[str, Any]:
    _require_provider_profile_permission(current_user, "provider_profiles.write")
    profile, versions = await _load(session, profile_id)
    _assert_owner(profile, current_user)
    profile.state = "disabled" if action == "disable" else "deprecated"
    session.add(_audit(profile_id, action + "d", current_user, version=profile.active_version))
    await session.commit(); return _response(profile, versions)

@router.post("/{profile_id}/default")
async def make_default(profile_id: str, session: AsyncSession = Depends(get_async_session), current_user: User = Depends(get_current_user())) -> dict[str, Any]:
    _require_provider_profile_permission(current_user, "provider_profiles.write")
    profile, versions = await _load(session, profile_id)
    _assert_owner(profile, current_user)
    if profile.state != "active" or profile.active_version is None:
        raise HTTPException(409, "only an active validated profile can be default")
    await session.execute(update(OmnigentAgentProfile).values(default_for_runtime=False))
    profile.default_for_runtime = True
    session.add(_audit(profile_id, "made_default", current_user, version=profile.active_version))
    await session.commit()
    return _response(profile, versions)

@router.get("/{profile_id}/audit")
async def audit_history(profile_id: str, session: AsyncSession = Depends(get_async_session), current_user: User = Depends(get_current_user())) -> list[dict[str, Any]]:
    _require_provider_profile_permission(current_user, "provider_profiles.read")
    profile, _ = await _load(session, profile_id)
    if profile.visibility == "private" and profile.owner_id != current_user.id:
        raise HTTPException(404, "agent profile not found")
    rows = list((await session.execute(
        select(OmnigentAgentProfileAuditEvent)
        .where(OmnigentAgentProfileAuditEvent.profile_id == profile_id)
        .order_by(OmnigentAgentProfileAuditEvent.created_at.desc())
    )).scalars())
    return [{"action": row.action, "version": row.version, "actorId": row.actor_id,
             "metadata": row.metadata_json, "createdAt": row.created_at} for row in rows]

@router.get("/{profile_id}/usage")
async def profile_usage(profile_id: str, session: AsyncSession = Depends(get_async_session), current_user: User = Depends(get_current_user())) -> list[dict[str, Any]]:
    _require_provider_profile_permission(current_user, "provider_profiles.read")
    profile, _ = await _load(session, profile_id)
    if profile.visibility == "private" and profile.owner_id != current_user.id:
        raise HTTPException(404, "agent profile not found")
    rows = list((await session.execute(select(OmnigentAgentProfileUsage).where(
        OmnigentAgentProfileUsage.profile_id == profile_id
    ))).scalars())
    return [{"consumerType": row.consumer_type, "consumerId": row.consumer_id,
             "version": row.version, "digest": row.digest, "createdAt": row.created_at}
            for row in rows]

@router.post("/{profile_id}/snapshot", status_code=status.HTTP_201_CREATED)
async def resolve_snapshot(profile_id: str, body: SnapshotCreate, session: AsyncSession = Depends(get_async_session), current_user: User = Depends(get_current_user())) -> dict[str, Any]:
    """Resolve and persist the exact immutable selection at an authoring boundary."""
    _require_provider_profile_permission(current_user, "provider_profiles.read")
    profile, versions = await _load(session, profile_id)
    if profile.visibility == "private" and profile.owner_id != current_user.id:
        raise HTTPException(404, "agent profile not found")
    if profile.state != "active":
        raise HTTPException(409, "agent profile is not active")
    target_number = body.version or profile.active_version
    target = next((v for v in versions if v.version == target_number), None)
    if target is None or not target.validation_result or target.validation_result.get("ready") is not True:
        raise HTTPException(409, "selected profile version is not launch ready")
    source = target.document["source"]
    projection = None
    if source.get("upstreamId"):
        projection_id = projection_identity(
            target.document["endpointRef"],
            source["upstreamId"],
            source.get("upstreamVersion"),
        )
        projection = await session.get(OmnigentUpstreamAgentProjection, projection_id)
        if projection is None or not projection.available or not projection.compatible:
            raise HTTPException(409, "upstream agent is unavailable or incompatible")
    allowed_overrides = {"model", "capture", "rag", "publish"}
    rejected = set(body.overrides) - allowed_overrides
    if rejected:
        raise HTTPException(422, f"unsupported profile overrides: {', '.join(sorted(rejected))}")
    effective = json.loads(json.dumps(target.document))
    for key, value in body.overrides.items():
        if not isinstance(value, dict):
            raise HTTPException(422, f"{key} override must be an object")
        effective[key] = {**effective.get(key, {}), **value}
    snapshot = {
        "schemaVersion": "moonmind.omnigent-agent-profile-snapshot.v1",
        "profileId": profile_id, "version": target.version, "digest": target.digest,
        "document": effective, "upstreamSnapshot": (
            projection.metadata_snapshot if projection else target.upstream_snapshot
        ),
        "validationResult": target.validation_result,
    }
    usage = OmnigentAgentProfileUsage(
        consumer_type=body.consumer_type, consumer_id=body.consumer_id,
        profile_id=profile_id, version=target.version, digest=target.digest,
        effective_snapshot=snapshot,
    )
    session.add(usage)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        existing = await session.scalar(select(OmnigentAgentProfileUsage).where(
            OmnigentAgentProfileUsage.consumer_type == body.consumer_type,
            OmnigentAgentProfileUsage.consumer_id == body.consumer_id,
        ))
        if existing and existing.effective_snapshot == snapshot:
            return existing.effective_snapshot
        raise HTTPException(409, "consumer already has an immutable profile snapshot") from exc
    return snapshot

@router.delete("/{profile_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_profile(profile_id: str, session: AsyncSession = Depends(get_async_session), current_user: User = Depends(get_current_user())) -> None:
    _require_provider_profile_permission(current_user, "provider_profiles.write")
    profile, _ = await _load(session, profile_id)
    _assert_owner(profile, current_user)
    usage_count = int(await session.scalar(select(func.count()).select_from(
        OmnigentAgentProfileUsage
    ).where(OmnigentAgentProfileUsage.profile_id == profile_id)) or 0)
    if usage_count or profile.state == "active":
        raise HTTPException(409, "used or active profiles cannot be deleted; deprecate them")
    await session.execute(delete(OmnigentAgentProfileAuditEvent).where(
        OmnigentAgentProfileAuditEvent.profile_id == profile_id
    ))
    await session.execute(delete(OmnigentAgentProfileVersion).where(
        OmnigentAgentProfileVersion.profile_id == profile_id
    ))
    await session.delete(profile)
    await session.commit()
