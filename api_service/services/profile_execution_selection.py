"""Resolve a Profile's subordinate execution configuration at API boundaries.

Inventory and selection never depend on model discovery or temporary runtime
health. The existing immutable configuration and credential authorities are
validated when a consumer snapshot is committed and on the actual host.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import and_, or_, select

from api_service.db.models import OmnigentAgentProfile, OmnigentAgentProfileVersion


class ProfileExecutionConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profileId: str = Field(min_length=1)
    version: int = Field(ge=1)
    digest: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")


def configuration_accepts_profile(document: Mapping[str, Any], provider: Any) -> bool:
    from api_service.services.omnigent_agent_profile_selection import (
        _accepted_provider_ids,
        _provider_materializer_error,
    )

    if document.get("schemaVersion") == "moonmind.omnigent-agent-profile.v2":
        accepted = _accepted_provider_ids(document)
        return (not accepted or provider.provider_id in accepted) and (
            _provider_materializer_error(document, provider) is None
        )
    requirements = document.get("providerRequirements") or {}

    def value(raw: Any) -> Any:
        return getattr(raw, "value", raw)

    return bool(requirements) and all(
        (
            provider.runtime_id == requirements.get("runtimeId"),
            value(provider.credential_source) == requirements.get("credentialSource"),
            value(provider.runtime_materialization_mode)
            == requirements.get("materializationMode"),
            not requirements.get("providerIds")
            or provider.provider_id in requirements["providerIds"],
        )
    )


async def load_execution_configurations(
    session: Any,
    user: Any,
    providers: list[Any],
) -> list[tuple[Any, Any]]:
    selected_versions = [
        OmnigentAgentProfileVersion.version == OmnigentAgentProfile.active_version,
    ]
    for provider in providers:
        pinned = getattr(provider, "execution_configuration", None)
        if pinned:
            selected_versions.append(
                and_(
                    OmnigentAgentProfileVersion.profile_id == pinned.get("profileId"),
                    OmnigentAgentProfileVersion.version == pinned.get("version"),
                    OmnigentAgentProfileVersion.digest == pinned.get("digest"),
                )
            )
    statement = (
        select(OmnigentAgentProfile, OmnigentAgentProfileVersion)
        .join(
            OmnigentAgentProfileVersion,
            OmnigentAgentProfileVersion.profile_id == OmnigentAgentProfile.profile_id,
        )
        .where(OmnigentAgentProfile.state == "active", or_(*selected_versions))
    )
    return [
        (row, version)
        for row, version in (await session.execute(statement)).all()
        if row.visibility != "private" or row.owner_id == getattr(user, "id", None)
    ]


def select_execution_configuration(
    provider: Any,
    configurations: list[tuple[Any, Any]],
) -> dict[str, Any]:
    authored = getattr(provider, "execution_configuration", None)
    candidates = [
        (row, version)
        for row, version in configurations
        if configuration_accepts_profile(version.document, provider)
    ]
    if authored:
        candidates = [
            (row, version)
            for row, version in candidates
            if row.profile_id == authored.get("profileId")
            and version.version == authored.get("version")
            and version.digest == authored.get("digest")
        ]
    else:
        candidates = [
            (row, version)
            for row, version in candidates
            if version.version == row.active_version
        ]
        preferred = [
            (row, version) for row, version in candidates if row.default_for_runtime
        ]
        if preferred:
            candidates = preferred
    if len(candidates) != 1:
        raise HTTPException(
            409,
            {
                "code": "profile_execution_configuration_required",
                "message": (
                    "Choose an execution configuration in Profile settings."
                    if candidates
                    else "This Profile's execution configuration is unavailable or incompatible."
                ),
                "profileId": provider.profile_id,
            },
        )
    row, version = candidates[0]
    document = version.document
    policies = (
        document.get("allowedLaunchPolicyRefs")
        or (document.get("execution") or {}).get("allowedLaunchPolicyRefs")
        or []
    )
    return {
        "profileId": row.profile_id,
        "version": version.version,
        "digest": version.digest,
        "providerProfileRef": provider.profile_id,
        "launchPolicyRef": policies[0] if policies else None,
        "defaultForRuntime": any(
            candidate.default_for_runtime
            and candidate_version.version == candidate.active_version
            and configuration_accepts_profile(candidate_version.document, provider)
            for candidate, candidate_version in configurations
        ),
    }


async def profile_execution_selection(
    session: Any, provider: Any, user: Any
) -> dict[str, Any]:
    return select_execution_configuration(
        provider,
        await load_execution_configurations(session, user, [provider]),
    )
