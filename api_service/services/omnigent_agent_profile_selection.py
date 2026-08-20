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


def compile_agent_profile_snapshot_parameters(
    parameters: Mapping[str, Any],
    *,
    snapshot: Mapping[str, Any],
) -> dict[str, Any]:
    """Compile one trusted agent-profile snapshot into workflow parameters.

    ``parameters.omnigent`` remains the authored product-intent surface.  The
    immutable snapshot carries the selected upstream agent and profile
    authority separately so the Temporal workflow can validate and merge it at
    the runtime-request boundary.
    """

    document = snapshot.get("document")
    if not isinstance(document, Mapping):
        raise TypeError("agent profile snapshot document must be an object")

    required_snapshot_fields = (
        "profileId",
        "version",
        "digest",
        "providerProfileRef",
        "executionProfileRef",
        "launchPolicyRef",
        "agentId",
    )
    missing = [
        field
        for field in required_snapshot_fields
        if snapshot.get(field) is None or str(snapshot.get(field)).strip() == ""
    ]
    if missing:
        raise ValueError(
            "agent profile snapshot is missing required fields: "
            + ", ".join(missing)
        )

    compiled = copy.deepcopy(dict(parameters))
    compiled["agentProfileSnapshot"] = copy.deepcopy(dict(snapshot))
    compiled["agentProfile"] = {
        "profileId": snapshot["profileId"],
        "version": snapshot["version"],
        "digest": snapshot["digest"],
    }
    compiled["profileId"] = snapshot["providerProfileRef"]

    effective_model = document.get("model")
    if isinstance(effective_model, Mapping):
        if effective_model.get("model") is not None:
            compiled["model"] = effective_model["model"]
        if effective_model.get("effort") is not None:
            compiled["effort"] = effective_model["effort"]

    # Host realization uses the canonical authored names.  Profile identity,
    # Provider Profile identity, and upstream agent identity stay exclusively
    # in the trusted snapshot instead of leaking into this authored block.
    authored_omnigent = compiled.get("omnigent")
    omnigent = (
        copy.deepcopy(dict(authored_omnigent))
        if isinstance(authored_omnigent, Mapping)
        else {}
    )
    # Older executions duplicated trusted snapshot identity into the authored
    # block. A newly resolved snapshot must remove those stale copies before it
    # writes the canonical target/policy fields, otherwise immutable profile
    # version advancement creates two contradictory authorities in one request.
    omnigent.pop("agentProfileRef", None)
    omnigent.pop("executionProfileRef", None)
    raw_agent = omnigent.get("agent")
    if isinstance(raw_agent, Mapping):
        agent = copy.deepcopy(dict(raw_agent))
        agent.pop("agentId", None)
        if agent:
            omnigent["agent"] = agent
        else:
            omnigent.pop("agent", None)
    omnigent["executionTargetRef"] = snapshot["executionProfileRef"]
    omnigent["launchPolicyRef"] = snapshot["launchPolicyRef"]
    compiled["omnigent"] = omnigent

    compiled["rag"] = copy.deepcopy(document.get("rag") or {})
    compiled["capture"] = copy.deepcopy(document.get("capture") or {})
    compiled["workspace"] = copy.deepcopy(document.get("workspace") or {})
    return compiled


async def resolve_agent_profile_snapshot(
    session: AsyncSession,
    *,
    selection: Mapping[str, Any],
    consumer_type: str,
    consumer_id: str,
    user: User | None,
    replace_existing_usage: bool = False,
) -> dict[str, Any]:
    """Validate, persist, and return one launch-authoritative profile snapshot.

    The caller must invoke this before committing the consumer so the usage row
    and authored consumer are one database transaction.
    """
    profile_id = str(selection.get("profileId") or "").strip()
    if not profile_id:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "agentProfile.profileId is required")
    profile = await session.get(OmnigentAgentProfile, profile_id)
    if profile is None or (
        profile.visibility == "private"
        and (user is None or profile.owner_id != user.id)
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "agent profile not found")
    if profile.state != "active":
        raise HTTPException(status.HTTP_409_CONFLICT, "agent profile is not active")

    requested_version = selection.get("version")
    try:
        version_number = int(requested_version) if requested_version is not None else profile.active_version
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "agentProfile.version must be a positive integer",
        ) from exc
    if version_number is None or version_number < 1:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "agentProfile.version must be a positive integer",
        )
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

    # Overrides cross the same authority boundary as authored versions. Re-run
    # the canonical document schema so unknown fields and authority-bearing
    # values cannot enter an effective launch snapshot.
    from api_service.api.routers.omnigent_agent_profiles import AgentProfileDocument
    try:
        document = AgentProfileDocument.model_validate(document).model_dump(
            mode="json", by_alias=True, exclude_none=True
        )
    except Exception as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "agentProfile overrides do not form a valid profile document",
        ) from exc

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

    source = document.get("source") or {}
    bundle_import = (version.rollout_metadata or {}).get("bundleImport") or {}
    imported_agent = bundle_import.get("upstreamAgent") or {}
    agent_id = str(source.get("upstreamId") or imported_agent.get("id") or "").strip()
    if not agent_id:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "selected profile has no stable launch agent identity",
        )
    allowed_launch_policies = document["execution"]["allowedLaunchPolicyRefs"]
    requested_launch_policy = str(selection.get("launchPolicyRef") or "").strip()
    if requested_launch_policy and requested_launch_policy not in allowed_launch_policies:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "agentProfile.launchPolicyRef is not allowed by the selected profile",
        )
    launch_policy_ref = requested_launch_policy or allowed_launch_policies[0]

    snapshot = {
        "schemaVersion": "moonmind.omnigent-agent-profile-snapshot.v1",
        "profileId": profile_id,
        "version": version.version,
        "digest": version.digest,
        "document": document,
        "providerProfileRef": compatible_provider.profile_id,
        "executionProfileRef": document["execution"]["defaultExecutionProfileRef"],
        "allowedLaunchPolicyRefs": document["execution"]["allowedLaunchPolicyRefs"],
        "launchPolicyRef": launch_policy_ref,
        "agentId": agent_id,
        "policyRef": document["policyRef"],
        "upstreamSnapshot": upstream_snapshot,
        "validationResult": version.validation_result,
    }
    if replace_existing_usage:
        usage = await session.scalar(
            select(OmnigentAgentProfileUsage).where(
                OmnigentAgentProfileUsage.consumer_type == consumer_type,
                OmnigentAgentProfileUsage.consumer_id == consumer_id,
            )
        )
        if usage is None:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "managed Agent Profile usage is unavailable for replacement",
            )
        usage.profile_id = profile_id
        usage.version = version.version
        usage.digest = version.digest
        usage.effective_snapshot = snapshot
    else:
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


async def refresh_managed_bootstrap_snapshot(
    session: AsyncSession,
    *,
    parameters: Mapping[str, Any],
    consumer_type: str,
    consumer_id: str,
    user: User | None,
    replace_existing_usage: bool = False,
) -> dict[str, Any]:
    """Refresh product-managed launch authority while preserving consumer intent.

    Operator-owned immutable profile selections are retained. The
    deployment-managed bootstrap profile is different: its active version
    advances when MoonMind moves a built-in launch policy, so long-lived
    consumers such as reruns and recurring schedules must re-resolve that
    trusted snapshot rather than replaying authority that the durable host
    binding no longer selects.
    """

    from api_service.services.omnigent_agent_bootstrap_service import (
        BOOTSTRAP_PROFILE_ID,
    )

    compiled = copy.deepcopy(dict(parameters))
    previous = compiled.get("agentProfileSnapshot")
    if (
        not isinstance(previous, Mapping)
        or previous.get("profileId") != BOOTSTRAP_PROFILE_ID
    ):
        return compiled
    previous_version_number = previous.get("version")
    previous_digest = str(previous.get("digest") or "").strip()
    if (
        isinstance(previous_version_number, bool)
        or not isinstance(previous_version_number, int)
        or previous_version_number < 1
        or not previous_digest
    ):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "managed bootstrap snapshot lineage is incomplete",
        )
    previous_version = await session.scalar(
        select(OmnigentAgentProfileVersion).where(
            OmnigentAgentProfileVersion.profile_id == BOOTSTRAP_PROFILE_ID,
            OmnigentAgentProfileVersion.version == previous_version_number,
        )
    )
    if previous_version is None or previous_version.digest != previous_digest:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "managed bootstrap snapshot lineage does not match durable state",
        )
    if replace_existing_usage:
        existing_usage = await session.scalar(
            select(OmnigentAgentProfileUsage).where(
                OmnigentAgentProfileUsage.consumer_type == consumer_type,
                OmnigentAgentProfileUsage.consumer_id == consumer_id,
            )
        )
        if (
            existing_usage is None
            or existing_usage.profile_id != BOOTSTRAP_PROFILE_ID
            or existing_usage.version != previous_version_number
            or existing_usage.digest != previous_digest
            or existing_usage.effective_snapshot != dict(previous)
        ):
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "managed bootstrap snapshot usage does not match durable state",
            )

    selection: dict[str, Any] = {
        "profileId": BOOTSTRAP_PROFILE_ID,
        "providerProfileRef": previous.get("providerProfileRef"),
    }
    previous_document = previous.get("document")
    if not isinstance(previous_document, Mapping):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "managed bootstrap snapshot document is unavailable",
        )
    overrides: dict[str, dict[str, Any]] = {}
    for section in _OVERRIDABLE_SECTIONS:
        baseline_section = previous_version.document.get(section) or {}
        effective_section = previous_document.get(section) or {}
        if not isinstance(baseline_section, Mapping) or not isinstance(
            effective_section, Mapping
        ):
            continue
        missing = object()
        changed: dict[str, Any] = {}
        for key in sorted(set(baseline_section) | set(effective_section)):
            baseline_value = baseline_section.get(key, missing)
            effective_value = effective_section.get(key, missing)
            if effective_value is missing:
                if baseline_value is not missing:
                    changed[key] = None
            elif baseline_value is missing or baseline_value != effective_value:
                changed[key] = copy.deepcopy(effective_value)
        if changed:
            overrides[section] = changed
    if overrides:
        selection["overrides"] = overrides

    refreshed = await resolve_agent_profile_snapshot(
        session,
        selection=selection,
        consumer_type=consumer_type,
        consumer_id=consumer_id,
        user=user,
        replace_existing_usage=replace_existing_usage,
    )
    return compile_agent_profile_snapshot_parameters(
        compiled,
        snapshot=refreshed,
    )


__all__ = [
    "compile_agent_profile_snapshot_parameters",
    "refresh_managed_bootstrap_snapshot",
    "resolve_agent_profile_snapshot",
]
