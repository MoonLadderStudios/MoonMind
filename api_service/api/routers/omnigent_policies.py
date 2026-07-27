"""Operator policy lifecycle API for MoonLadderStudios/MoonMind#3515."""

from __future__ import annotations

import json
from difflib import unified_diff
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.auth_providers import get_current_user
from api_service.db.base import get_async_session
from api_service.db.models import User
from api_service.services.omnigent_policies import (
    OmnigentPolicyService,
    PolicyConflict,
    PolicyNotFound,
)
from api_service.services.settings_catalog import has_settings_permission
from moonmind.omnigent.policies import PolicyDocument, PolicyState

router = APIRouter(prefix="/api/omnigent/policies", tags=["Omnigent Policies"])


class CreatePolicy(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    policy_id: str = Field(alias="policyId", pattern=r"^[a-z0-9][a-z0-9-]{1,127}$")
    name: str = Field(min_length=2, max_length=255)
    visibility: Literal["private", "deployment"] = "private"
    document: PolicyDocument
    clone_source_ref: str | None = Field(None, alias="cloneSourceRef")


class NewVersion(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    expected_parent_ref: str = Field(alias="expectedParentRef")
    document: PolicyDocument


class Transition(BaseModel):
    state: PolicyState
    make_default: bool = Field(False, alias="makeDefault")


def _actor(user: User) -> str:
    return str(getattr(user, "id", "system"))


def _require(user: User, permission: str) -> None:
    if not has_settings_permission(user, permission):
        raise HTTPException(403, f"Missing required Omnigent policy permission: {permission}.")


def _can_view_policy(policy: Any, user: User) -> bool:
    if bool(getattr(user, "is_superuser", False)):
        return True
    if policy.visibility == "deployment":
        return True
    owner_id = getattr(policy, "owner_user_id", None)
    user_id = getattr(user, "id", None)
    return owner_id is not None and user_id is not None and str(owner_id) == str(user_id)


def _require_policy_management(policy: Any, user: User) -> None:
    if bool(getattr(user, "is_superuser", False)):
        return
    owner_id = getattr(policy, "owner_user_id", None)
    user_id = getattr(user, "id", None)
    if owner_id is not None and user_id is not None and str(owner_id) == str(user_id):
        return
    raise HTTPException(403, "Not authorized to manage this Omnigent policy.")


async def _visible_policy(service: OmnigentPolicyService, policy_id: str, user: User) -> Any:
    policy = await service.get_policy(policy_id)
    if not _can_view_policy(policy, user):
        raise PolicyNotFound(policy_id)
    return policy


def _version_json(row: Any) -> dict[str, Any]:
    return {
        "policyId": row.policy_id, "version": row.version, "ref": f"{row.policy_id}@{row.version}",
        "state": row.state, "digest": row.digest, "document": row.document_json,
        "validation": row.validation_json, "compatibility": row.compatibility_json,
        "rollout": row.rollout_json,
        "lineage": {"parentRef": row.parent_ref, "cloneSourceRef": row.clone_source_ref, "supersedesRef": row.supersedes_ref},
        "audit": {"createdBy": row.created_by, "createdAt": row.created_at, "activatedBy": row.activated_by,
                  "activatedAt": row.activated_at, "disabledBy": row.disabled_by, "disabledAt": row.disabled_at},
        "envFallbackUsed": row.env_fallback_used,
        "stateHistory": row.state_history_json,
    }


@router.get("")
async def list_policies(session: AsyncSession = Depends(get_async_session), user: User = Depends(get_current_user())) -> dict[str, Any]:
    _require(user, "settings.catalog.read")
    rows = await OmnigentPolicyService(session).list()
    rows = [(policy, version) for policy, version in rows if _can_view_policy(policy, user)]
    can_write = has_settings_permission(user, "settings.system.write")
    user_id = getattr(user, "id", None)
    return {"canWrite": can_write, "items": [{"id": policy.policy_id, "name": policy.name, "visibility": policy.visibility,
                       "canManage": can_write and (bool(getattr(user, "is_superuser", False)) or (
                           policy.owner_user_id is not None and user_id is not None and str(policy.owner_user_id) == str(user_id))),
                       "status": version.state if version else "draft", "defaultVersion": policy.default_version,
                       "summary": f"Immutable policy authority; default {policy.default_version or 'not selected'}",
                       "version": _version_json(version) if version else None} for policy, version in rows]}


@router.post("", status_code=201)
async def create_policy(body: CreatePolicy, session: AsyncSession = Depends(get_async_session), user: User = Depends(get_current_user())) -> dict[str, Any]:
    _require(user, "settings.system.write")
    try:
        service = OmnigentPolicyService(session)
        if body.clone_source_ref:
            source = await service.resolve_ref(body.clone_source_ref)
            await _visible_policy(service, source.policy_id, user)
        row = await service.create(
            policy_id=body.policy_id, name=body.name, owner_user_id=getattr(user, "id", None),
            visibility=body.visibility, document=body.document, actor=_actor(user),
            clone_source_ref=body.clone_source_ref,
        )
        return _version_json(row)
    except PolicyNotFound as exc:
        raise HTTPException(404, f"Clone source not found: {exc}") from exc
    except PolicyConflict as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/{policy_id}/versions")
async def list_versions(policy_id: str, session: AsyncSession = Depends(get_async_session), user: User = Depends(get_current_user())) -> dict[str, Any]:
    _require(user, "settings.catalog.read")
    try:
        service = OmnigentPolicyService(session)
        await _visible_policy(service, policy_id, user)
        return {"items": [_version_json(row) for row in await service.versions(policy_id)]}
    except PolicyNotFound as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/{policy_id}/versions", status_code=201)
async def create_version(policy_id: str, body: NewVersion, session: AsyncSession = Depends(get_async_session), user: User = Depends(get_current_user())) -> dict[str, Any]:
    _require(user, "settings.system.write")
    try:
        service = OmnigentPolicyService(session)
        policy = await _visible_policy(service, policy_id, user)
        _require_policy_management(policy, user)
        row = await service.new_version(
            policy_id=policy_id, document=body.document, actor=_actor(user),
            expected_parent_ref=body.expected_parent_ref,
        )
        return _version_json(row)
    except PolicyNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except PolicyConflict as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/{policy_id}/versions/{version}/transition")
async def transition(policy_id: str, version: int, body: Transition, session: AsyncSession = Depends(get_async_session), user: User = Depends(get_current_user())) -> dict[str, Any]:
    _require(user, "settings.system.write")
    try:
        service = OmnigentPolicyService(session)
        policy = await _visible_policy(service, policy_id, user)
        _require_policy_management(policy, user)
        row = await service.transition(
            policy_id=policy_id, version=version, state=body.state, actor=_actor(user),
            make_default=body.make_default,
        )
        return _version_json(row)
    except PolicyNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except PolicyConflict as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/{policy_id}/versions/{version}/snapshot")
async def get_snapshot(policy_id: str, version: int, session: AsyncSession = Depends(get_async_session), user: User = Depends(get_current_user())) -> dict[str, Any]:
    _require(user, "settings.catalog.read")
    try:
        service = OmnigentPolicyService(session)
        await _visible_policy(service, policy_id, user)
        return await service.snapshot(policy_id, version)
    except PolicyNotFound as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/{policy_id}/diff")
async def diff_versions(policy_id: str, from_version: int, to_version: int, session: AsyncSession = Depends(get_async_session), user: User = Depends(get_current_user())) -> dict[str, Any]:
    _require(user, "settings.catalog.read")
    service = OmnigentPolicyService(session)
    try:
        await _visible_policy(service, policy_id, user)
        before, after = await service.get_version(policy_id, from_version), await service.get_version(policy_id, to_version)
    except PolicyNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    left = json.dumps(before.document_json, indent=2, sort_keys=True).splitlines()
    right = json.dumps(after.document_json, indent=2, sort_keys=True).splitlines()
    return {"fromRef": f"{policy_id}@{from_version}", "toRef": f"{policy_id}@{to_version}",
            "diff": "\n".join(unified_diff(left, right, lineterm=""))}
