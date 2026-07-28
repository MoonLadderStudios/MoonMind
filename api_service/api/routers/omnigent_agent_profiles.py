"""Persistent Omnigent agent-profile API (MoonLadderStudios/MoonMind#3517)."""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.api.routers.provider_profiles import _require_provider_profile_permission
from api_service.auth_providers import get_current_user
from api_service.db.base import get_async_session
from api_service.db.models import OmnigentAgentProfile, OmnigentAgentProfileVersion, User

router = APIRouter(prefix="/api/omnigent/agent-profiles", tags=["Omnigent Agent Profiles"])
_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{1,127}$")
_FORBIDDEN = {"credentials", "credential", "dockerfile", "hostPath", "host_path", "volumeName", "volume_name", "privileged"}

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

class AgentProfileDocument(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    schema_version: Literal["moonmind.omnigent-agent-profile.v1"] = Field("moonmind.omnigent-agent-profile.v1", alias="schemaVersion")
    endpoint_ref: str = Field(alias="endpointRef", min_length=1, max_length=128)
    bridge_mode: str = Field(alias="bridgeMode", min_length=1, max_length=64)
    source: AgentSource
    harness: str = Field(min_length=1, max_length=64)
    required_capabilities: list[str] = Field(default_factory=list, alias="requiredCapabilities", max_length=64)
    execution: dict[str, Any]
    provider_requirements: dict[str, Any] = Field(alias="providerRequirements")
    model: dict[str, Any] = Field(default_factory=dict)
    workspace: dict[str, Any] = Field(default_factory=dict)
    skills: list[str] = Field(default_factory=list, max_length=128)
    tools: list[str] = Field(default_factory=list, max_length=128)
    capture: dict[str, Any] = Field(default_factory=dict)
    rag: dict[str, Any] = Field(default_factory=dict)
    continuations: dict[str, Any] = Field(default_factory=dict)
    publish: dict[str, Any] = Field(default_factory=dict)
    policy_ref: str = Field(alias="policyRef", min_length=1, max_length=255)
    @model_validator(mode="after")
    def reject_authority(self) -> "AgentProfileDocument":
        def walk(value: Any) -> None:
            if isinstance(value, dict):
                for key, child in value.items():
                    if key in _FORBIDDEN:
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

def _normalized(document: AgentProfileDocument) -> dict[str, Any]:
    return document.model_dump(mode="json", by_alias=True, exclude_none=True)

def _digest(document: dict[str, Any]) -> str:
    encoded = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()

def _response(profile: OmnigentAgentProfile, versions: list[OmnigentAgentProfileVersion]) -> dict[str, Any]:
    return {"profileId": profile.profile_id, "displayName": profile.display_name, "description": profile.description,
            "visibility": profile.visibility, "state": profile.state, "activeVersion": profile.active_version,
            "default": profile.default_for_runtime, "versions": [{"version": v.version, "digest": v.digest, "document": v.document,
            "parentVersion": v.parent_version, "validationResult": v.validation_result, "createdAt": v.created_at} for v in versions]}

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
    session.add_all([profile, version]); await session.commit(); await session.refresh(version)
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
    if profile.owner_id != current_user.id and not current_user.is_superuser: raise HTTPException(403, "profile owner permission required")
    document = _normalized(body.document); digest = _digest(document)
    if any(v.digest == digest for v in versions): raise HTTPException(409, "identical immutable version already exists")
    number = (versions[0].version if versions else 0) + 1
    version = OmnigentAgentProfileVersion(profile_id=profile_id, version=number, digest=digest, document=document, parent_version=versions[0].version if versions else None, created_by=current_user.id)
    session.add(version); await session.commit(); await session.refresh(version)
    return _response(profile, [version])

@router.post("/{profile_id}/activate/{version}")
async def activate(profile_id: str, version: int, session: AsyncSession = Depends(get_async_session), current_user: User = Depends(get_current_user())) -> dict[str, Any]:
    _require_provider_profile_permission(current_user, "provider_profiles.write")
    profile, versions = await _load(session, profile_id)
    target = next((v for v in versions if v.version == version), None)
    if not target: raise HTTPException(404, "profile version not found")
    if target.validation_result and target.validation_result.get("ready") is False: raise HTTPException(409, "profile version failed validation")
    profile.active_version = version; profile.state = "active"; await session.commit()
    return _response(profile, versions)

@router.post("/{profile_id}/{action}")
async def lifecycle(profile_id: str, action: Literal["disable", "deprecate"], session: AsyncSession = Depends(get_async_session), current_user: User = Depends(get_current_user())) -> dict[str, Any]:
    _require_provider_profile_permission(current_user, "provider_profiles.write")
    profile, versions = await _load(session, profile_id); profile.state = "disabled" if action == "disable" else "deprecated"
    await session.commit(); return _response(profile, versions)
