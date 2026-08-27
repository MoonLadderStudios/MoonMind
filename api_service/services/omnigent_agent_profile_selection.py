"""Resolve immutable Omnigent agent-profile selections at authoring boundaries."""

from __future__ import annotations

import copy
from typing import Any, Mapping

from fastapi import HTTPException, status
from sqlalchemy import or_, select
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
from api_service.services.provider_profile_readiness import (
    provider_profile_launch_ready,
)
from api_service.services.provider_profile_service import (
    _managed_secret_statuses_for_profiles,
)
from moonmind.omnigent.harness_platform.failures import HarnessPlatformError

_OVERRIDABLE_SECTIONS = frozenset({"model", "capture", "rag", "publish"})


def default_launch_policy_ref(allowed_launch_policy_refs: Any) -> str:
    """Return the launch policy admission selects when none is authored.

    Deployment qualification must qualify the same combination admission
    compiles, so the launch policy is derived from the Agent Profile instead of
    being restated per call site.
    """

    for candidate in allowed_launch_policy_refs or ():
        cleaned = str(candidate or "").strip()
        if cleaned:
            return cleaned
    raise ValueError("agent profile declares no allowed launch policy")


def _provider_profile_visibility_filter(user: User | None) -> Any | None:
    """Return the SQL visibility boundary shared by explicit/default selection."""

    user_id = getattr(user, "id", None)
    if user_id is None or bool(getattr(user, "is_superuser", False)):
        return None
    return or_(
        ManagedAgentProviderProfile.owner_user_id.is_(None),
        ManagedAgentProviderProfile.owner_user_id == user_id,
    )


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
            "agent profile snapshot is missing required fields: " + ", ".join(missing)
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
        selected_model = effective_model.get("qualifiedId") or effective_model.get(
            "model"
        )
        if selected_model is not None:
            compiled["model"] = selected_model
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
    if document.get("schemaVersion") == "moonmind.omnigent-agent-profile.v2":
        omnigent["agentProfileRef"] = {
            "profileId": snapshot["profileId"],
            "version": snapshot["version"],
            "digest": snapshot["digest"],
        }
    else:
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
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "agentProfile.profileId is required"
        )
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
        version_number = (
            int(requested_version)
            if requested_version is not None
            else profile.active_version
        )
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
    if (
        version is None
        or not version.validation_result
        or version.validation_result.get("ready") is not True
    ):
        raise HTTPException(
            status.HTTP_409_CONFLICT, "selected profile version is not launch ready"
        )
    requested_digest = str(selection.get("digest") or "").strip()
    if requested_digest and requested_digest != version.digest:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "agentProfile.digest does not match the selected immutable version",
        )

    document = copy.deepcopy(version.document)
    is_v2 = document.get("schemaVersion") == "moonmind.omnigent-agent-profile.v2"
    source = document.get("source") or {}
    upstream_snapshot = version.upstream_snapshot
    if source.get("upstreamId"):
        projection = await session.get(
            OmnigentUpstreamAgentProjection,
            projection_identity(
                document["endpointRef"],
                source["upstreamId"],
                source.get("upstreamVersion"),
            ),
        )
        readiness = projection_readiness(
            projection,
            bridge_mode=(None if is_v2 else document["bridgeMode"]),
            harness=(
                str((document.get("harness") or {}).get("id") or "")
                if is_v2
                else document["harness"]
            ),
            required_capabilities=(
                (document.get("requirements") or {})
                .get("moonmind", {})
                .get("required", [])
                if is_v2
                else document.get("requiredCapabilities", [])
            ),
        )
        if not readiness["ready"]:
            raise HTTPException(status.HTTP_409_CONFLICT, readiness["reason"])
        upstream_snapshot = projection.metadata_snapshot

    overrides = selection.get("overrides") or {}
    if not isinstance(overrides, Mapping):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "agentProfile.overrides must be an object",
        )
    rejected = set(overrides) - _OVERRIDABLE_SECTIONS
    if rejected:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"unsupported profile overrides: {', '.join(sorted(rejected))}",
        )
    _enforce_override_ceilings(defaults=document, overrides=overrides)
    for key, value in overrides.items():
        if not isinstance(value, Mapping):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                f"{key} override must be an object",
            )
        document[key] = {**document.get(key, {}), **dict(value)}

    # Overrides cross the same authority boundary as authored versions. Re-run
    # the canonical document schema so unknown fields and authority-bearing
    # values cannot enter an effective launch snapshot.
    try:
        if is_v2:
            from moonmind.omnigent.harness_platform.agent_profile import (
                OmnigentAgentProfileV2,
            )

            document = OmnigentAgentProfileV2.model_validate(document).model_dump(
                mode="json", by_alias=True, exclude_none=True
            )
        else:
            from api_service.api.routers.omnigent_agent_profiles import (
                AgentProfileDocument,
            )

            document = AgentProfileDocument.model_validate(document).model_dump(
                mode="json", by_alias=True, exclude_none=True
            )
    except Exception as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "agentProfile overrides do not form a valid profile document",
        ) from exc

    requested_provider_profile = str(
        selection.get("providerProfileRef") or selection.get("providerProfileId") or ""
    ).strip()
    if not requested_provider_profile:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "agentProfile.providerProfileRef is required",
        )
    if is_v2:
        provider_query = select(ManagedAgentProviderProfile).where(
            ManagedAgentProviderProfile.enabled.is_(True),
            ManagedAgentProviderProfile.profile_id == requested_provider_profile,
        )
        visibility_filter = _provider_profile_visibility_filter(user)
        if visibility_filter is not None:
            provider_query = provider_query.where(visibility_filter)
        compatible_provider = await session.scalar(provider_query.limit(1))
        accepted_provider_ids = {
            str(provider_id)
            for slot in document.get("credentialSlots", [])
            for provider_id in slot.get("acceptedProviderIds", [])
        }
        if (
            compatible_provider is not None
            and accepted_provider_ids
            and compatible_provider.provider_id not in accepted_provider_ids
        ):
            compatible_provider = None
        if compatible_provider is not None:
            # A Provider Profile stays owned by its managed runtime even when it
            # launches through Omnigent, so the requested profile must be one the
            # selected harness can actually materialize under every launch policy
            # the profile allows. Proving only that *some* materializer exists for
            # the pair would accept a profile the readiness projection excludes:
            # `codex-oauth-home@1` is registered for `codex_cli/openai` but is not
            # accepted by the `pi-native` harness. This is the same capability
            # boundary the readiness projection uses to build
            # `compatibleProviderProfiles`, not a second compatibility source.
            from moonmind.omnigent.harness_platform.host_classes import (
                get_launch_policy,
            )
            from moonmind.omnigent.harness_platform.materializers import (
                materializer_ref_for_provider,
                validate_binding_materializer,
            )

            harness_selection = document.get("harness") or {}
            try:
                materializer_ref = materializer_ref_for_provider(
                    compatible_provider.runtime_id,
                    compatible_provider.provider_id,
                )
                for policy_ref in document.get("allowedLaunchPolicyRefs") or ():
                    validate_binding_materializer(
                        materializer_ref=materializer_ref,
                        harness_implementation_ref=str(
                            harness_selection.get("implementationRef") or ""
                        ),
                        harness_id=str(harness_selection.get("id") or "") or None,
                        host_mode=get_launch_policy(policy_ref).hostMode,
                    )
            except HarnessPlatformError as exc:
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    (
                        f"selected Provider Profile "
                        f"{compatible_provider.profile_id!r} belongs to runtime "
                        f"{compatible_provider.runtime_id!r} and is not "
                        f"compatible with the selected Omnigent execution target"
                    ),
                ) from exc
    else:
        requirements = document["providerRequirements"]
        provider_query = select(ManagedAgentProviderProfile).where(
            ManagedAgentProviderProfile.enabled.is_(True),
            ManagedAgentProviderProfile.runtime_id == requirements["runtimeId"],
            ManagedAgentProviderProfile.credential_source
            == requirements["credentialSource"],
            ManagedAgentProviderProfile.runtime_materialization_mode
            == requirements["materializationMode"],
            ManagedAgentProviderProfile.profile_id == requested_provider_profile,
        )
        visibility_filter = _provider_profile_visibility_filter(user)
        if visibility_filter is not None:
            provider_query = provider_query.where(visibility_filter)
        if requirements.get("providerIds"):
            provider_query = provider_query.where(
                ManagedAgentProviderProfile.provider_id.in_(requirements["providerIds"])
            )
        compatible_provider = await session.scalar(provider_query.limit(1))
    if compatible_provider is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "selected Provider Profile is not enabled or compatible",
        )
    secret_statuses = await _managed_secret_statuses_for_profiles(
        session=session, rows=[compatible_provider]
    )
    if not provider_profile_launch_ready(
        compatible_provider, managed_secret_statuses=secret_statuses
    ):
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
    allowed_launch_policies = (
        document["allowedLaunchPolicyRefs"]
        if is_v2
        else document["execution"]["allowedLaunchPolicyRefs"]
    )
    requested_launch_policy = str(selection.get("launchPolicyRef") or "").strip()
    if (
        requested_launch_policy
        and requested_launch_policy not in allowed_launch_policies
    ):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "agentProfile.launchPolicyRef is not allowed by the selected profile",
        )
    launch_policy_ref = requested_launch_policy or default_launch_policy_ref(
        allowed_launch_policies
    )

    # Generic (v2) profiles do not carry an execution-profile declaration of
    # their own; the launch policy owns that identity. Derive the canonical
    # per-harness ref so the compiled plan can verify profile/policy agreement.
    v2_execution_profile_ref = ""
    if is_v2:
        harness_id = str(
            ((document.get("harness") or {}).get("id") or "")
        ).strip()
        if harness_id:
            v2_execution_profile_ref = (
                f"omnigent-{harness_id.removesuffix('-native')}@1"
            )

    snapshot = {
        "schemaVersion": "moonmind.omnigent-agent-profile-snapshot.v1",
        "profileId": profile_id,
        "version": version.version,
        "digest": version.digest,
        "document": document,
        "providerProfileRef": compatible_provider.profile_id,
        "executionProfileRef": (
            v2_execution_profile_ref
            if is_v2
            else document["execution"]["defaultExecutionProfileRef"]
        ),
        "allowedLaunchPolicyRefs": allowed_launch_policies,
        "launchPolicyRef": launch_policy_ref,
        "agentId": agent_id,
        "policyRef": (launch_policy_ref if is_v2 else document["policyRef"]),
        "upstreamSnapshot": upstream_snapshot,
        "validationResult": version.validation_result,
    }
    usage = await session.scalar(
        select(OmnigentAgentProfileUsage).where(
            OmnigentAgentProfileUsage.consumer_type == consumer_type,
            OmnigentAgentProfileUsage.consumer_id == consumer_id,
        )
    )
    if usage is not None and not replace_existing_usage:
        if (
            usage.profile_id != profile_id
            or usage.version != version.version
            or usage.digest != version.digest
            or usage.effective_snapshot != snapshot
        ):
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "Agent Profile usage conflicts with the existing consumer authority",
            )
        return copy.deepcopy(dict(usage.effective_snapshot))
    if replace_existing_usage:
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
        session.add(
            OmnigentAgentProfileUsage(
                consumer_type=consumer_type,
                consumer_id=consumer_id,
                profile_id=profile_id,
                version=version.version,
                digest=version.digest,
                effective_snapshot=snapshot,
            )
        )
    await session.flush()
    return snapshot


async def resolve_default_agent_profile_snapshot(
    session: AsyncSession,
    *,
    provider_profile_ref: str | None,
    launch_policy_ref: str | None,
    consumer_type: str,
    consumer_id: str,
    user: User | None,
) -> dict[str, Any]:
    """Resolve the deployment-managed default into explicit launch authority.

    The default is selected only at API admission.  The returned immutable
    snapshot is then compiled into the same plan as an explicitly authored
    Agent Profile, so workers never repeat default/profile resolution.
    """

    profile = await session.scalar(
        select(OmnigentAgentProfile)
        .where(OmnigentAgentProfile.default_for_runtime.is_(True))
        .limit(1)
    )
    if profile is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "default Omnigent Agent Profile is unavailable",
        )
    if profile.state != "active" or profile.active_version is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "default Omnigent Agent Profile is not launch ready",
        )
    version = await session.scalar(
        select(OmnigentAgentProfileVersion).where(
            OmnigentAgentProfileVersion.profile_id == profile.profile_id,
            OmnigentAgentProfileVersion.version == profile.active_version,
        )
    )
    if version is None or not isinstance(version.document, Mapping):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "default Omnigent Agent Profile version is unavailable",
        )
    selected_provider_ref = str(provider_profile_ref or "").strip()
    if not selected_provider_ref:
        requirements = version.document.get("providerRequirements") or {}
        query = (
            select(ManagedAgentProviderProfile)
            .where(
                ManagedAgentProviderProfile.enabled.is_(True),
                ManagedAgentProviderProfile.runtime_id
                == requirements.get("runtimeId"),
                ManagedAgentProviderProfile.credential_source
                == requirements.get("credentialSource"),
                ManagedAgentProviderProfile.runtime_materialization_mode
                == requirements.get("materializationMode"),
            )
            .order_by(
                ManagedAgentProviderProfile.is_default.desc(),
                ManagedAgentProviderProfile.priority.desc(),
                ManagedAgentProviderProfile.profile_id.asc(),
            )
        )
        visibility_filter = _provider_profile_visibility_filter(user)
        if visibility_filter is not None:
            query = query.where(visibility_filter)
        if requirements.get("providerIds"):
            query = query.where(
                ManagedAgentProviderProfile.provider_id.in_(
                    requirements["providerIds"]
                )
            )
        candidates = list((await session.scalars(query)).all())
        candidate_statuses = await _managed_secret_statuses_for_profiles(
            session=session, rows=candidates
        )
        selected = next(
            (
                item
                for item in candidates
                if provider_profile_launch_ready(
                    item, managed_secret_statuses=candidate_statuses
                )
            ),
            None,
        )
        if selected is None:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "no launch-ready Provider Profile matches the default Omnigent "
                "Agent Profile",
            )
        selected_provider_ref = selected.profile_id
    selection = {
        "profileId": profile.profile_id,
        "version": profile.active_version,
        "providerProfileRef": selected_provider_ref,
        **(
            {"launchPolicyRef": str(launch_policy_ref).strip()}
            if str(launch_policy_ref or "").strip()
            else {}
        ),
    }
    return await resolve_agent_profile_snapshot(
        session,
        selection=selection,
        consumer_type=consumer_type,
        consumer_id=consumer_id,
        user=user,
    )


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
    "default_launch_policy_ref",
    "refresh_managed_bootstrap_snapshot",
    "resolve_agent_profile_snapshot",
    "resolve_default_agent_profile_snapshot",
]
