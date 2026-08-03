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
from api_service.api.routers.omnigent_bridge import (
    _get_bridge_proxy,
    _get_create_embedded_facade,
    _require_bridge_enabled,
)
from api_service.auth_providers import get_current_user
from api_service.db.base import get_async_session
from api_service.db.models import (
    RecurringWorkflowDefinition,
    TemporalExecutionCanonicalRecord,
    WorkflowCheckpointBranch,
    OmnigentAgentProfile,
    OmnigentAgentProfileAuditEvent,
    OmnigentAgentProfileUsage,
    OmnigentAgentProfileVersion,
    OmnigentUpstreamAgentProjection,
    ManagedAgentProviderProfile,
    User,
)
from api_service.services.omnigent_agent_profile_service import (
    projection_identity,
    projection_readiness,
    synchronize_upstream_inventory,
)
from api_service.services.omnigent_agent_bundle_service import (
    BundleValidationError,
    publish_validated_agent_bundle,
)
from api_service.api.routers.temporal_artifacts import _get_temporal_artifact_service
from api_service.services.omnigent_agent_smoke_service import (
    DEFAULT_SMOKE_TIMEOUT_SECONDS,
    run_profile_readiness_checks,
    run_smoke_validation,
)
from moonmind.workflows.temporal.artifacts import TemporalArtifactService
from moonmind.omnigent.bridge_config import (
    HOST_PROTOCOL_MODE_EMBEDDED,
    OmnigentBridgeConfig,
)
from moonmind.omnigent.bridge_embedded import OmnigentEmbeddedHostProtocolFacade
from moonmind.omnigent.bridge_proxy import (
    OmnigentBridgeError,
    OmnigentBridgeSessionProxy,
)

router = APIRouter(prefix="/api/omnigent/agent-profiles", tags=["Omnigent Agent Profiles"])
_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{1,127}$")
_FORBIDDEN_PARTS = {
    "credential", "credentials", "secret", "secrets", "password", "token",
    "dockerfile", "hostpath", "volumename", "privileged", "dockersocket",
}
_SAFE_REFERENCE_KEYS = {
    "credentialsource", "credentialsources",
    "maxtokens",
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
        walk(self.model_dump(by_alias=True, exclude_none=True))
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

class SmokeCreate(BaseModel):
    version: int | None = Field(None, ge=1)

class BundleImportCreate(BaseModel):
    version: int | None = Field(None, ge=1)

def _assert_owner(profile: OmnigentAgentProfile, user: User) -> None:
    # Ownerless workspace profiles are bootstrap/operator-managed resources.
    # Callers have already enforced provider_profiles.write before reaching
    # lifecycle mutations.
    if profile.owner_id is None and profile.visibility == "workspace":
        return
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
            "defaultForRuntime": profile.default_for_runtime, "versions": [{"version": v.version, "digest": v.digest, "document": v.document,
            "parentVersion": v.parent_version, "clonedFromProfileId": v.cloned_from_profile_id,
            "clonedFromVersion": v.cloned_from_version, "upstreamSnapshot": v.upstream_snapshot,
            "validationResult": v.validation_result,
            "rolloutMetadata": getattr(v, "rollout_metadata", None),
            "createdAt": v.created_at} for v in versions]}

async def _load(session: AsyncSession, profile_id: str) -> tuple[OmnigentAgentProfile, list[OmnigentAgentProfileVersion]]:
    profile = await session.get(OmnigentAgentProfile, profile_id)
    if not profile: raise HTTPException(404, "agent profile not found")
    versions = list((await session.execute(select(OmnigentAgentProfileVersion).where(OmnigentAgentProfileVersion.profile_id == profile_id).order_by(OmnigentAgentProfileVersion.version.desc()))).scalars())
    return profile, versions

async def _refresh_upstream_projection(
    session: AsyncSession,
    *,
    config: OmnigentBridgeConfig,
    proxy: OmnigentBridgeSessionProxy | None,
    embedded_facade: OmnigentEmbeddedHostProtocolFacade | None,
) -> None:
    facade = (
        embedded_facade
        if config.host_protocol_mode == HOST_PROTOCOL_MODE_EMBEDDED
        else proxy
    )
    if facade is None:
        raise HTTPException(503, "Omnigent inventory bridge is unavailable")
    try:
        inventory = await facade.list_agents()
    except OmnigentBridgeError as exc:
        raise HTTPException(409, f"could not refresh upstream inventory: {exc}") from exc
    await synchronize_upstream_inventory(
        session,
        endpoint_ref="default",
        bridge_mode=str(config.host_protocol_mode),
        inventory=inventory,
    )


def _bridge_refresh(
    session: AsyncSession,
    *,
    config: OmnigentBridgeConfig,
    proxy: OmnigentBridgeSessionProxy | None,
    embedded_facade: OmnigentEmbeddedHostProtocolFacade | None,
):
    """Bind the upstream-projection refresh to the readiness-check core."""

    async def _refresh() -> None:
        await _refresh_upstream_projection(
            session, config=config, proxy=proxy, embedded_facade=embedded_facade
        )

    return _refresh


def _artifact_bundle_reader(
    artifact_service: TemporalArtifactService, current_user: User
):
    """Bind artifact-boundary bundle reads to the readiness-check core."""

    async def _read(artifact_id: str) -> bytes:
        _stored_artifact, bundle_bytes = await artifact_service.read(
            artifact_id=artifact_id, principal=str(current_user.id)
        )
        return bundle_bytes

    return _read


@router.get("")
async def list_profiles(session: AsyncSession = Depends(get_async_session), current_user: User = Depends(get_current_user())) -> list[dict[str, Any]]:
    _require_provider_profile_permission(current_user, "provider_profiles.read")
    profiles = list((await session.execute(select(OmnigentAgentProfile).order_by(OmnigentAgentProfile.display_name))).scalars())
    visible_profiles = [
        profile for profile in profiles
        if profile.visibility != "private" or profile.owner_id == current_user.id
    ]
    visible_ids = [profile.profile_id for profile in visible_profiles]
    versions_by_profile: dict[str, list[OmnigentAgentProfileVersion]] = {
        profile_id: [] for profile_id in visible_ids
    }
    if visible_ids:
        versions = list((await session.execute(
            select(OmnigentAgentProfileVersion)
            .where(OmnigentAgentProfileVersion.profile_id.in_(visible_ids))
            .order_by(
                OmnigentAgentProfileVersion.profile_id,
                OmnigentAgentProfileVersion.version.desc(),
            )
        )).scalars())
        for version in versions:
            versions_by_profile[version.profile_id].append(version)
    return [
        _response(profile, versions_by_profile[profile.profile_id])
        for profile in visible_profiles
    ]

@router.post("", status_code=status.HTTP_201_CREATED)
async def create_profile(body: ProfileCreate, session: AsyncSession = Depends(get_async_session), current_user: User = Depends(get_current_user())) -> dict[str, Any]:
    _require_provider_profile_permission(current_user, "provider_profiles.write")
    if not _ID.fullmatch(body.profile_id): raise HTTPException(422, "profileId must be a stable lowercase identifier")
    if await session.get(OmnigentAgentProfile, body.profile_id): raise HTTPException(409, "agent profile already exists")
    document = _normalized(body.document)
    profile = OmnigentAgentProfile(profile_id=body.profile_id, display_name=body.display_name, description=body.description, owner_id=current_user.id, visibility=body.visibility)
    version = OmnigentAgentProfileVersion(profile_id=body.profile_id, version=1, digest=_digest(document), document=document, created_by=current_user.id)
    session.add_all([profile, version, _audit(body.profile_id, "created", current_user, version=1)])
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(409, "agent profile already exists") from exc
    await session.refresh(version)
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
    latest_number = int((await session.scalar(
        select(func.max(OmnigentAgentProfileVersion.version)).where(
            OmnigentAgentProfileVersion.profile_id == profile_id
        )
    )) or 0)
    number = latest_number + 1
    version = OmnigentAgentProfileVersion(profile_id=profile_id, version=number, digest=digest, document=document, parent_version=latest_number or None, created_by=current_user.id)
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
async def validate_profile(
    profile_id: str,
    body: ValidateCreate,
    session: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(get_current_user()),
    artifact_service: TemporalArtifactService = Depends(_get_temporal_artifact_service),
    bridge_config: OmnigentBridgeConfig = Depends(_require_bridge_enabled),
    bridge_proxy: OmnigentBridgeSessionProxy | None = Depends(_get_bridge_proxy),
    embedded_facade: OmnigentEmbeddedHostProtocolFacade | None = Depends(
        _get_create_embedded_facade
    ),
) -> dict[str, Any]:
    """Perform bounded, credential-free readiness validation before activation."""
    _require_provider_profile_permission(current_user, "provider_profiles.write")
    profile, versions = await _load(session, profile_id)
    _assert_owner(profile, current_user)
    target_number = body.version or profile.active_version or (versions[0].version if versions else None)
    target = next((v for v in versions if v.version == target_number), None)
    if target is None:
        raise HTTPException(404, "profile version not found")
    outcome = await run_profile_readiness_checks(
        session,
        document=target.document,
        refresh_upstream=_bridge_refresh(
            session,
            config=bridge_config,
            proxy=bridge_proxy,
            embedded_facade=embedded_facade,
        ),
        read_bundle_bytes=_artifact_bundle_reader(artifact_service, current_user),
    )
    target.upstream_snapshot = outcome.upstream_snapshot
    target.validation_result = {
        "schemaVersion": "moonmind.omnigent-agent-profile-validation.v1",
        "ready": outcome.ready, "checks": outcome.checks,
    }
    session.add(_audit(profile_id, "validated", current_user, version=target.version,
                       metadata={"ready": outcome.ready}))
    await session.commit()
    return target.validation_result

@router.post("/{profile_id}/smoke")
async def smoke_validate_profile(
    profile_id: str,
    body: SmokeCreate,
    session: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(get_current_user()),
    artifact_service: TemporalArtifactService = Depends(_get_temporal_artifact_service),
    bridge_config: OmnigentBridgeConfig = Depends(_require_bridge_enabled),
    bridge_proxy: OmnigentBridgeSessionProxy | None = Depends(_get_bridge_proxy),
    embedded_facade: OmnigentEmbeddedHostProtocolFacade | None = Depends(
        _get_create_embedded_facade
    ),
) -> dict[str, Any]:
    """Run an operator-triggered bounded smoke preflight (Sec 7).

    Reuses the shared readiness checks, adds the strongest *safe* session-start
    capability probe (bridge reachability, never a real launch), bounds the
    whole preflight by a time budget, secret-scans diagnostics, and guarantees
    release of any validation-owned lease. A pass is readiness evidence, not a
    workflow-success guarantee, so it never mutates the version's activation
    validation result.
    """
    _require_provider_profile_permission(current_user, "provider_profiles.write")
    profile, versions = await _load(session, profile_id)
    _assert_owner(profile, current_user)
    target_number = body.version or profile.active_version or (
        versions[0].version if versions else None
    )
    target = next((v for v in versions if v.version == target_number), None)
    if target is None:
        raise HTTPException(404, "profile version not found")

    facade = (
        embedded_facade
        if bridge_config.host_protocol_mode == HOST_PROTOCOL_MODE_EMBEDDED
        else bridge_proxy
    )
    diagnostics: list[str] = []

    async def _preflight():
        return await run_profile_readiness_checks(
            session,
            document=target.document,
            refresh_upstream=_bridge_refresh(
                session,
                config=bridge_config,
                proxy=bridge_proxy,
                embedded_facade=embedded_facade,
            ),
            read_bundle_bytes=_artifact_bundle_reader(artifact_service, current_user),
        )

    async def _session_start_probe() -> dict[str, Any]:
        # Strongest safe session-start capability: prove the endpoint can be
        # reached to enumerate hosts without launching a real agent session.
        if facade is None:
            return {"ready": False, "reason": "Omnigent bridge is unavailable"}
        try:
            await facade.list_hosts()
            return {"ready": True, "reason": None}
        except OmnigentBridgeError as exc:
            diagnostics.append(f"session-start probe failed: {exc}")
            return {
                "ready": False,
                "reason": "bridge endpoint is not reachable for a session-start check",
            }

    result = await run_smoke_validation(
        preflight=_preflight,
        session_start_probe=_session_start_probe,
        cleanup=None,  # reachability probe creates no durable session or lease
        diagnostics=diagnostics,
        timeout_seconds=DEFAULT_SMOKE_TIMEOUT_SECONDS,
        profile_id=profile_id,
        version=target.version,
    )
    session.add(
        _audit(
            profile_id, "smoke_validated", current_user, version=target.version,
            metadata={
                "ready": result["ready"],
                "timedOut": result["timedOut"],
                "durationMs": result["durationMs"],
                "checks": [
                    {"name": check["name"], "ready": check["ready"]}
                    for check in result["checks"]
                ],
            },
        )
    )
    await session.commit()
    return result


@router.post("/{profile_id}/import-bundle")
async def import_profile_bundle(
    profile_id: str,
    body: BundleImportCreate,
    session: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(get_current_user()),
    artifact_service: TemporalArtifactService = Depends(_get_temporal_artifact_service),
    bridge_config: OmnigentBridgeConfig = Depends(_require_bridge_enabled),
    bridge_proxy: OmnigentBridgeSessionProxy | None = Depends(_get_bridge_proxy),
) -> dict[str, Any]:
    """Publish one validated immutable bundle through the authorized facade."""

    _require_provider_profile_permission(current_user, "provider_profiles.write")
    profile, versions = await _load(session, profile_id)
    _assert_owner(profile, current_user)
    target_number = body.version or profile.active_version or (
        versions[0].version if versions else None
    )
    target = next((v for v in versions if v.version == target_number), None)
    if target is None:
        raise HTTPException(404, "profile version not found")
    source = target.document.get("source", {})
    artifact_ref = str(source.get("bundleArtifactRef") or "")
    if not artifact_ref:
        raise HTTPException(409, "profile version is not artifact-backed")
    if target.document.get("endpointRef") != "default":
        raise HTTPException(409, "profile endpoint is outside the configured bridge scope")
    if (
        bridge_config.host_protocol_mode != "proxy"
        or target.document.get("bridgeMode") != "proxy"
        or bridge_proxy is None
    ):
        raise HTTPException(409, "bundle import requires the authorized proxy bridge mode")
    if not target.validation_result or target.validation_result.get("ready") is not True:
        raise HTTPException(409, "profile version must pass validation before bundle import")

    existing_import = (target.rollout_metadata or {}).get("bundleImport") or {}
    if existing_import.get("status") == "succeeded":
        return existing_import
    if existing_import.get("status") == "publishing":
        raise HTTPException(
            409,
            "bundle import is already reserved; reconcile its audit evidence before retrying",
        )
    operation_key = f"{profile_id}@{target.version}:{source['bundleDigest']}"
    reservation = {
        "schemaVersion": "moonmind.omnigent-agent-bundle-import.v1",
        "status": "publishing",
        "idempotencyKey": operation_key,
    }
    target.rollout_metadata = {
        **(target.rollout_metadata or {}),
        "bundleImport": reservation,
    }
    session.add(
        _audit(
            profile_id,
            "bundle_import_reserved",
            current_user,
            version=target.version,
            metadata=reservation,
        )
    )
    await session.commit()

    artifact_id = artifact_ref.removeprefix("artifact:")
    stored_artifact, bundle_bytes = await artifact_service.read(
        artifact_id=artifact_id, principal=str(current_user.id)
    )
    filename = f"{profile_id}-v{target.version}.bundle"
    try:
        result = await publish_validated_agent_bundle(
            data=bundle_bytes,
            content_type=str(stored_artifact.content_type or "application/octet-stream"),
            expected_digest=str(source["bundleDigest"]),
            filename=filename,
            publish=bridge_proxy.import_agent_bundle,
        )
    except (BundleValidationError, OmnigentBridgeError) as exc:
        failure = {
            "schemaVersion": "moonmind.omnigent-agent-bundle-import.v1",
            "status": "failed",
            "failureClass": (
                exc.failure_class if isinstance(exc, OmnigentBridgeError) else "validation_error"
            ),
        }
        target.rollout_metadata = {**(target.rollout_metadata or {}), "bundleImport": failure}
        session.add(_audit(profile_id, "bundle_import_failed", current_user,
                           version=target.version, metadata=failure))
        await session.commit()
        raise HTTPException(409 if isinstance(exc, BundleValidationError) else 502,
                            "bundle import failed; see profile audit evidence") from exc

    result = {**result, "idempotencyKey": operation_key}
    target.rollout_metadata = {**(target.rollout_metadata or {}), "bundleImport": result}
    session.add(_audit(profile_id, "bundle_imported", current_user,
                       version=target.version, metadata=result))
    await session.commit()
    return result

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
async def resolve_snapshot(
    profile_id: str,
    body: SnapshotCreate,
    session: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(get_current_user()),
    bridge_config: OmnigentBridgeConfig = Depends(_require_bridge_enabled),
    bridge_proxy: OmnigentBridgeSessionProxy | None = Depends(_get_bridge_proxy),
    embedded_facade: OmnigentEmbeddedHostProtocolFacade | None = Depends(
        _get_create_embedded_facade
    ),
) -> dict[str, Any]:
    """Resolve and persist the exact immutable selection at an authoring boundary."""
    _require_provider_profile_permission(current_user, "provider_profiles.write")
    profile, versions = await _load(session, profile_id)
    if profile.visibility == "private" and profile.owner_id != current_user.id:
        raise HTTPException(404, "agent profile not found")
    if not current_user.is_superuser:
        authorized = False
        if body.consumer_type in {"workflow", "remediation"}:
            consumer = await session.get(
                TemporalExecutionCanonicalRecord, body.consumer_id
            )
            authorized = consumer is not None and consumer.owner_id == str(current_user.id)
        elif body.consumer_type == "schedule":
            try:
                consumer = await session.get(RecurringWorkflowDefinition, body.consumer_id)
            except (TypeError, ValueError):
                consumer = None
            authorized = consumer is not None and consumer.owner_user_id == current_user.id
        elif body.consumer_type == "checkpoint":
            consumer = await session.get(WorkflowCheckpointBranch, body.consumer_id)
            if consumer is not None:
                workflow = await session.get(
                    TemporalExecutionCanonicalRecord, consumer.workflow_id
                )
                authorized = (
                    workflow is not None and workflow.owner_id == str(current_user.id)
                )
        elif body.consumer_type == "smoke":
            authorized = profile.owner_id == current_user.id and body.consumer_id == profile_id
        if not authorized:
            raise HTTPException(403, "consumer ownership could not be verified")
    existing = await session.scalar(select(OmnigentAgentProfileUsage).where(
        OmnigentAgentProfileUsage.consumer_type == body.consumer_type,
        OmnigentAgentProfileUsage.consumer_id == body.consumer_id,
    ))
    if existing is not None:
        if existing.profile_id != profile_id:
            raise HTTPException(409, "consumer already has an immutable profile snapshot")
        return existing.effective_snapshot
    if profile.state != "active":
        raise HTTPException(409, "agent profile is not active")
    target_number = body.version or profile.active_version
    target = next((v for v in versions if v.version == target_number), None)
    if target is None or not target.validation_result or target.validation_result.get("ready") is not True:
        raise HTTPException(409, "selected profile version is not launch ready")
    source = target.document["source"]
    projection = None
    if source.get("upstreamId"):
        await _refresh_upstream_projection(
            session,
            config=bridge_config,
            proxy=bridge_proxy,
            embedded_facade=embedded_facade,
        )
        projection_id = projection_identity(
            target.document["endpointRef"],
            source["upstreamId"],
            source.get("upstreamVersion"),
        )
        projection = await session.get(OmnigentUpstreamAgentProjection, projection_id)
        upstream_readiness = projection_readiness(
            projection,
            bridge_mode=target.document["bridgeMode"],
            harness=target.document["harness"],
            required_capabilities=target.document.get("requiredCapabilities", []),
        )
        if not upstream_readiness["ready"]:
            raise HTTPException(409, upstream_readiness["reason"])
    allowed_overrides = {"model", "capture", "rag", "publish"}
    rejected = set(body.overrides) - allowed_overrides
    if rejected:
        raise HTTPException(422, f"unsupported profile overrides: {', '.join(sorted(rejected))}")
    effective = json.loads(json.dumps(target.document))
    for key, value in body.overrides.items():
        if not isinstance(value, dict):
            raise HTTPException(422, f"{key} override must be an object")
        effective[key] = {**effective.get(key, {}), **value}
    try:
        effective = _normalized(AgentProfileDocument.model_validate(effective))
    except ValueError as exc:
        raise HTTPException(422, f"invalid profile overrides: {exc}") from exc
    requirements = effective["providerRequirements"]
    providers = list((await session.execute(select(ManagedAgentProviderProfile))).scalars())
    compatible_provider = any(
        row.enabled
        and row.runtime_id == requirements["runtimeId"]
        and row.credential_source.value == requirements["credentialSource"]
        and row.runtime_materialization_mode.value == requirements["materializationMode"]
        and (not requirements.get("providerIds") or row.provider_id in requirements["providerIds"])
        for row in providers
    )
    if not compatible_provider:
        raise HTTPException(409, "no enabled compatible Provider Profile")
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
        if existing:
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
    if usage_count or profile.state != "draft":
        raise HTTPException(409, "only unused draft profiles can be deleted")
    await session.execute(delete(OmnigentAgentProfileAuditEvent).where(
        OmnigentAgentProfileAuditEvent.profile_id == profile_id
    ))
    await session.execute(delete(OmnigentAgentProfileVersion).where(
        OmnigentAgentProfileVersion.profile_id == profile_id
    ))
    await session.delete(profile)
    await session.commit()

@router.post("/{profile_id}/{action}")
async def lifecycle(profile_id: str, action: Literal["disable", "deprecate"], session: AsyncSession = Depends(get_async_session), current_user: User = Depends(get_current_user())) -> dict[str, Any]:
    _require_provider_profile_permission(current_user, "provider_profiles.write")
    profile, versions = await _load(session, profile_id)
    _assert_owner(profile, current_user)
    profile.state = "disabled" if action == "disable" else "deprecated"
    profile.default_for_runtime = False
    session.add(_audit(profile_id, action + "d", current_user, version=profile.active_version))
    await session.commit(); return _response(profile, versions)
