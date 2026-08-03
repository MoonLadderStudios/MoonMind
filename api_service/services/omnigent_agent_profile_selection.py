"""Resolve immutable Omnigent agent-profile selections at authoring boundaries."""
from __future__ import annotations

import copy
from typing import Any, Mapping

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.db.models import (
    ManagedAgentProviderProfile,
    OmnigentAgentProfile,
    OmnigentAgentProfileUsage,
    OmnigentAgentProfileVersion,
    OmnigentUpstreamAgentProjection,
    User,
)
from api_service.services.omnigent_agent_profile_service import (
    projection_identity,
    projection_readiness,
)
from api_service.services.provider_profile_readiness import provider_profile_launch_ready

_OVERRIDABLE_SECTIONS = frozenset({"model", "capture", "rag", "publish"})


def _enforce_override_ceilings(
    *, defaults: Mapping[str, Any], overrides: Mapping[str, Any]
) -> None:
    """Reject authored values that exceed ceilings stored in the version."""
    rag_defaults = defaults.get("rag") or {}
    rag_overrides = overrides.get("rag") or {}
    for key in ("maxTokens", "maxLatencyMs"):
        ceiling = rag_defaults.get(key)
        requested = rag_overrides.get(key)
        if ceiling is not None and requested is not None and requested > ceiling:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                f"rag.{key} override exceeds the selected profile policy ceiling",
            )

    capture_defaults = defaults.get("capture") or {}
    capture_overrides = overrides.get("capture") or {}
    retention_ceiling = capture_defaults.get("retentionDays")
    requested_retention = capture_overrides.get("retentionDays")
    if (
        retention_ceiling is not None
        and requested_retention is not None
        and requested_retention > retention_ceiling
    ):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "capture.retentionDays override exceeds the selected profile policy ceiling",
        )

    publish_rank = {"none": 0, "draft": 1, "ready": 2, "auto": 3}
    publish_default = (defaults.get("publish") or {}).get("mode")
    publish_override = (overrides.get("publish") or {}).get("mode")
    if (
        publish_default in publish_rank
        and publish_override in publish_rank
        and publish_rank[publish_override] > publish_rank[publish_default]
    ):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "publish.mode override exceeds the selected profile policy ceiling",
        )


async def resolve_agent_profile_snapshot(
    session: AsyncSession,
    *,
    selection: Mapping[str, Any],
    consumer_type: str,
    consumer_id: str,
    user: User,
) -> dict[str, Any]:
    """Validate, persist, and return one launch-authoritative profile snapshot.

    The caller must invoke this before committing the consumer so the usage row
    and authored consumer are one database transaction.
    """
    profile_id = str(selection.get("profileId") or "").strip()
    if not profile_id:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "agentProfile.profileId is required")
    profile = await session.get(OmnigentAgentProfile, profile_id)
    if profile is None or (profile.visibility == "private" and profile.owner_id != user.id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "agent profile not found")
    if profile.state != "active":
        raise HTTPException(status.HTTP_409_CONFLICT, "agent profile is not active")

    requested_version = selection.get("version")
    version_number = int(requested_version) if requested_version is not None else profile.active_version
    version = await session.scalar(
        select(OmnigentAgentProfileVersion).where(
            OmnigentAgentProfileVersion.profile_id == profile_id,
            OmnigentAgentProfileVersion.version == version_number,
        )
    )
    if version is None or not version.validation_result or version.validation_result.get("ready") is not True:
        raise HTTPException(status.HTTP_409_CONFLICT, "selected profile version is not launch ready")

    document = copy.deepcopy(version.document)
    source = document.get("source") or {}
    upstream_snapshot = version.upstream_snapshot
    if source.get("upstreamId"):
        projection = await session.get(
            OmnigentUpstreamAgentProjection,
            projection_identity(document["endpointRef"], source["upstreamId"], source.get("upstreamVersion")),
        )
        readiness = projection_readiness(
            projection,
            bridge_mode=document["bridgeMode"],
            harness=document["harness"],
            required_capabilities=document.get("requiredCapabilities", []),
        )
        if not readiness["ready"]:
            raise HTTPException(status.HTTP_409_CONFLICT, readiness["reason"])
        upstream_snapshot = projection.metadata_snapshot

    overrides = selection.get("overrides") or {}
    if not isinstance(overrides, Mapping):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "agentProfile.overrides must be an object")
    rejected = set(overrides) - _OVERRIDABLE_SECTIONS
    if rejected:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"unsupported profile overrides: {', '.join(sorted(rejected))}",
        )
    _enforce_override_ceilings(defaults=document, overrides=overrides)
    for key, value in overrides.items():
        if not isinstance(value, Mapping):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"{key} override must be an object")
        document[key] = {**document.get(key, {}), **dict(value)}

    requirements = document["providerRequirements"]
    provider_query = select(ManagedAgentProviderProfile).where(
            ManagedAgentProviderProfile.enabled.is_(True),
            ManagedAgentProviderProfile.runtime_id == requirements["runtimeId"],
            ManagedAgentProviderProfile.credential_source == requirements["credentialSource"],
            ManagedAgentProviderProfile.runtime_materialization_mode == requirements["materializationMode"],
        )
    if requirements.get("providerIds"):
        provider_query = provider_query.where(
            ManagedAgentProviderProfile.provider_id.in_(requirements["providerIds"])
        )
    requested_provider_profile = str(
        selection.get("providerProfileRef") or selection.get("providerProfileId") or ""
    ).strip()
    if not requested_provider_profile:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "agentProfile.providerProfileRef is required",
        )
    provider_query = provider_query.where(
        ManagedAgentProviderProfile.profile_id == requested_provider_profile
    )
    compatible_provider = await session.scalar(provider_query.limit(1))
    if compatible_provider is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "selected Provider Profile is not enabled or compatible",
        )
    if not provider_profile_launch_ready(compatible_provider):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "selected Provider Profile is not launch ready or has no capacity",
        )

    snapshot = {
        "schemaVersion": "moonmind.omnigent-agent-profile-snapshot.v1",
        "profileId": profile_id,
        "version": version.version,
        "digest": version.digest,
        "document": document,
        "providerProfileRef": compatible_provider.profile_id,
        "executionProfileRef": document["execution"]["defaultExecutionProfileRef"],
        "allowedLaunchPolicyRefs": document["execution"]["allowedLaunchPolicyRefs"],
        "policyRef": document["policyRef"],
        "upstreamSnapshot": upstream_snapshot,
        "validationResult": version.validation_result,
    }
    session.add(OmnigentAgentProfileUsage(
        consumer_type=consumer_type,
        consumer_id=consumer_id,
        profile_id=profile_id,
        version=version.version,
        digest=version.digest,
        effective_snapshot=snapshot,
    ))
    await session.flush()
    return snapshot


__all__ = ["resolve_agent_profile_snapshot"]
