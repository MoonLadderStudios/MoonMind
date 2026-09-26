"""Resolve immutable Omnigent agent-profile selections at authoring boundaries."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import logging
import re
from typing import Any, Mapping

from fastapi import HTTPException, status
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.db.models import (
    ManagedAgentProviderProfile,
    OmnigentAgentProfile,
    OmnigentAgentProfileAuditEvent,
    OmnigentAgentProfileUsage,
    OmnigentAgentProfileVersion,
    OmnigentUpstreamAgentProjection,
    User,
)
from api_service.services.omnigent_agent_profile_service import (
    UpstreamInventoryRefreshError,
    projection_identity,
    projection_readiness,
    readiness_actionable_detail,
    refresh_upstream_inventory,
    synchronize_omnigent_harness_catalog,
)
from api_service.services.provider_profile_readiness import (
    provider_profile_launch_ready,
)
from api_service.services.provider_profile_service import (
    _managed_secret_statuses_for_profiles,
)
from moonmind.omnigent.harness_platform.failures import HarnessPlatformError

_OVERRIDABLE_SECTIONS = frozenset({"model", "capture", "rag", "publish"})

# Only version-drift 409s may trigger catalog recovery. Incompatible,
# contract-mismatch, capacity, and usage-conflict failures cannot be repaired
# by synchronization, so they fail fast without upstream load. Stale freshness
# alone never blocks (last-known good remains launchable), so it never
# triggers recovery.
_DRIFT_RECOVERABLE_PREFIXES = (
    "stable upstream identity is unavailable",
    "stable upstream identity has not been synchronized",
)
# Admission-sized bound for the fallback sync (per-request client timeouts are
# larger; without an aggregate deadline a degraded endpoint could hold an
# ordinary submission for minutes).
_DRIFT_RECOVERY_TIMEOUT_SECONDS = 30


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


def _accepted_provider_ids(document: Mapping[str, Any]) -> set[str]:
    """Return the provider ids a v2 profile's credential slots accept."""

    return {
        str(provider_id)
        for slot in document.get("credentialSlots") or ()
        for provider_id in slot.get("acceptedProviderIds") or ()
    }


def _provider_materializer_error(
    document: Mapping[str, Any], profile: ManagedAgentProviderProfile
) -> HarnessPlatformError | None:
    """Return why the selected harness cannot materialize this profile, if any.

    A Provider Profile stays owned by its managed runtime even when it launches
    through Omnigent, so the profile must be one the selected harness can
    actually materialize under every launch policy the document allows. Proving
    only that *some* materializer exists for the pair would accept a profile the
    readiness projection excludes: ``codex-oauth-home@1`` is registered for
    ``codex_cli/openai`` but is not accepted by the ``pi-native`` harness. This
    is the same capability boundary the readiness projection uses to build
    ``compatibleProviderProfiles``, not a second compatibility source.

    A credential slot also pins the auth models it accepts, and the plan builder
    rebuilds the slot auth model from whichever materializer the chosen Provider
    Profile resolves to. Provider ids and harness compatibility alone would
    therefore let a slot restricted to ``acceptedAuthModels: ["none"]`` select an
    ``own-auth`` profile and launch with API-key credentials the immutable
    profile forbids, so the slot auth-model constraint the planner enforces at
    bind time is applied here too.
    """

    from moonmind.omnigent.harness_platform.failures import HarnessPlatformFailure
    from moonmind.omnigent.harness_platform.host_classes import get_launch_policy
    from moonmind.omnigent.harness_platform.materializers import (
        get_materializer,
        materializer_ref_for_provider,
        validate_binding_materializer,
    )

    harness_selection = document.get("harness") or {}
    try:
        materializer_ref = materializer_ref_for_provider(
            profile.runtime_id, profile.provider_id
        )
        materializer_auth_models = set(
            get_materializer(materializer_ref).acceptedAuthModels
        )
        provider_slots = [
            slot
            for slot in document.get("credentialSlots") or ()
            if isinstance(slot, Mapping)
            and (
                not slot.get("acceptedProviderIds")
                or str(profile.provider_id)
                in {
                    str(provider_id)
                    for provider_id in slot.get("acceptedProviderIds") or ()
                }
            )
        ]
        if provider_slots and not any(
            not slot.get("acceptedAuthModels")
            or materializer_auth_models.intersection(
                str(auth_model) for auth_model in slot.get("acceptedAuthModels") or ()
            )
            for slot in provider_slots
        ):
            raise HarnessPlatformError(
                f"materializer {materializer_ref} auth model "
                f"{sorted(materializer_auth_models)} is not accepted by any "
                f"credential slot that accepts provider {profile.provider_id!r}",
                code=HarnessPlatformFailure.OMNIGENT_PROVIDER_PROFILE_INCOMPATIBLE,
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
        return exc
    return None


def _provider_profile_visibility_filter(user: User | None) -> Any | None:
    """Return the SQL visibility boundary shared by explicit/default selection."""

    # Single-user (#4349): provider profiles are instance resources.
    # ``owner_user_id`` is legacy provenance, never an access predicate.
    return None


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
    persist_usage: bool = True,
) -> dict[str, Any]:
    """Validate, persist, and return one launch-authoritative profile snapshot.

    The caller must invoke this before committing the consumer so the usage row
    and authored consumer are one database transaction. Candidate compilation
    may set persist_usage=False and publish usage with the final consumer revision.
    """
    profile_id = str(selection.get("profileId") or "").strip()
    if not profile_id:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "agentProfile.profileId is required"
        )
    profile = await session.get(OmnigentAgentProfile, profile_id)
    # Single-user (#4349): execution configurations are instance resources;
    # private/workspace scoping by human owner is removed. ``user`` is
    # retained for call-site compatibility only.
    if profile is None:
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
        projection_id = projection_identity(
            document["endpointRef"], source["upstreamId"], source.get("upstreamVersion")
        )
        projection = await session.get(
            OmnigentUpstreamAgentProjection, projection_id
        )
        if projection_readiness(projection)["freshness"] in {"stale", "missing"}:
            try:
                await refresh_upstream_inventory(endpoint_ref=document["endpointRef"])
            except UpstreamInventoryRefreshError as exc:
                raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
            # Refresh committed in its own session. Re-read the exact pinned
            # identity instead of accepting the session's stale identity map or
            # substituting the latest upstream version/profile default.
            projection = await session.get(
                OmnigentUpstreamAgentProjection, projection_id, populate_existing=True
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
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                readiness_actionable_detail(
                    readiness,
                    profile_id=profile_id,
                    version=version_number,
                    endpoint_ref=str(document.get("endpointRef") or ""),
                    upstream_id=str(source.get("upstreamId") or ""),
                    upstream_version=source.get("upstreamVersion"),
                ),
            )
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
        accepted_provider_ids = _accepted_provider_ids(document)
        if (
            compatible_provider is not None
            and accepted_provider_ids
            and compatible_provider.provider_id not in accepted_provider_ids
        ):
            compatible_provider = None
        if compatible_provider is not None:
            materializer_error = _provider_materializer_error(
                document, compatible_provider
            )
            if materializer_error is not None:
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    (
                        f"selected Provider Profile "
                        f"{compatible_provider.profile_id!r} belongs to runtime "
                        f"{compatible_provider.runtime_id!r} and is not "
                        f"compatible with the selected Omnigent execution target"
                    ),
                ) from materializer_error
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
    if not persist_usage:
        return snapshot
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
    _allow_drift_recovery: bool = True,
) -> dict[str, Any]:
    """Resolve the deployment-managed default into explicit launch authority.

    The default is selected only at API admission.  The returned immutable
    snapshot is then compiled into the same plan as an explicitly authored
    Agent Profile, so workers never repeat default/profile resolution.

    The caller floated on the default (no explicit profile version), so one
    bounded catalog recovery is attempted before failing: upstream bumps the
    exact pinned version frequently, and the background sync may not have
    advanced ``active_version`` yet. Explicit pins never take this path.
    """

    if provider_profile_ref:
        from api_service.services.profile_execution_selection import (
            profile_execution_selection,
        )

        query = select(ManagedAgentProviderProfile).where(
            ManagedAgentProviderProfile.profile_id == provider_profile_ref
        )
        visibility_filter = _provider_profile_visibility_filter(user)
        if visibility_filter is not None:
            query = query.where(visibility_filter)
        provider = await session.scalar(query)
        if provider is None:
            raise HTTPException(404, "Profile not found")
        selection = await profile_execution_selection(session, provider, user)
        configurations = await session.scalar(
            select(OmnigentAgentProfileVersion).where(
                OmnigentAgentProfileVersion.profile_id == selection["profileId"],
                OmnigentAgentProfileVersion.version == selection["version"],
            )
        )
        # Profile model authority belongs to execution parameters. Clear legacy
        # configuration defaults without sending provider values through the
        # legacy model schema (whose validation is intentionally narrower).
        selection["overrides"] = {"model": {
            key: None for key in ("model", "qualifiedId", "effort")
            if key in (configurations.document.get("model") or {})
        }}
        if launch_policy_ref:
            selection["launchPolicyRef"] = launch_policy_ref
        try:
            return await resolve_agent_profile_snapshot(
                session, selection=selection, consumer_type=consumer_type,
                consumer_id=consumer_id, user=user,
            )
        except HTTPException as exc:
            if exc.status_code != status.HTTP_409_CONFLICT or not _allow_drift_recovery:
                raise
            detail = str(getattr(exc, "detail", "") or "")
            if not detail.startswith(_DRIFT_RECOVERABLE_PREFIXES):
                raise
            # Recovery runs in an independent session so catalog maintenance
            # commits can never commit the caller's partially authored
            # admission state. A losing concurrent sync still falls through
            # to the single permitted retry, which then reads the winner's
            # advanced default authority.
            from api_service.db.base import async_session_maker

            try:
                async with asyncio.timeout(_DRIFT_RECOVERY_TIMEOUT_SECONDS):
                    async with async_session_maker() as sync_session:
                        await synchronize_omnigent_harness_catalog(sync_session)
            except Exception:
                logging.getLogger(__name__).warning(
                    "Default profile drift recovery sync failed",
                    exc_info=True,
                )
            if user is not None:
                try:
                    session.expunge(user)
                except Exception:
                    # Best effort only: detached/test principals are not in
                    # this session, and expire_all must still run below.
                    pass
            session.expire_all()
            return await resolve_default_agent_profile_snapshot(
                session,
                provider_profile_ref=provider_profile_ref,
                launch_policy_ref=launch_policy_ref,
                consumer_type=consumer_type,
                consumer_id=consumer_id,
                user=user,
                _allow_drift_recovery=False,
            )

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
        document = version.document
        is_v2 = (
            document.get("schemaVersion") == "moonmind.omnigent-agent-profile.v2"
        )
        query = select(ManagedAgentProviderProfile).where(
            ManagedAgentProviderProfile.enabled.is_(True)
        )
        if is_v2:
            # A v2 profile declares which providers its credential slots accept
            # instead of pinning one credential contract, so the default is the
            # highest-ranked accepted Provider Profile the selected harness can
            # materialize. MoonLadderStudios/MoonMind#3877: on the default
            # deployment path that resolves the credentialless OpenCode Zen
            # profile, which holds the OpenCode runtime default.
            accepted_provider_ids = _accepted_provider_ids(document)
            if accepted_provider_ids:
                query = query.where(
                    ManagedAgentProviderProfile.provider_id.in_(
                        sorted(accepted_provider_ids)
                    )
                )
        else:
            requirements = document.get("providerRequirements") or {}
            query = query.where(
                ManagedAgentProviderProfile.runtime_id
                == requirements.get("runtimeId"),
                ManagedAgentProviderProfile.credential_source
                == requirements.get("credentialSource"),
                ManagedAgentProviderProfile.runtime_materialization_mode
                == requirements.get("materializationMode"),
            )
            if requirements.get("providerIds"):
                query = query.where(
                    ManagedAgentProviderProfile.provider_id.in_(
                        requirements["providerIds"]
                    )
                )
        query = query.order_by(
            ManagedAgentProviderProfile.is_default.desc(),
            ManagedAgentProviderProfile.priority.desc(),
            ManagedAgentProviderProfile.profile_id.asc(),
        )
        visibility_filter = _provider_profile_visibility_filter(user)
        if visibility_filter is not None:
            query = query.where(visibility_filter)
        candidates = list((await session.scalars(query)).all())
        if is_v2:
            candidates = [
                item
                for item in candidates
                if _provider_materializer_error(document, item) is None
            ]
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
    return await resolve_default_agent_profile_snapshot(
        session, provider_profile_ref=selected_provider_ref,
        launch_policy_ref=launch_policy_ref, consumer_type=consumer_type,
        consumer_id=consumer_id, user=user,
    )


def _profile_document_digest(document: Mapping[str, Any]) -> str:
    """Return the canonical digest for a profile document.

    Mirrors the profile API's immutable version identity so cutover versions
    deduplicate against manually authored equivalents instead of forking.
    """
    encoded = json.dumps(
        document, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _allowed_policy_refs(document: Mapping[str, Any]) -> list[str]:
    """Return the launch policy refs a profile document admits."""
    if document.get("schemaVersion") == "moonmind.omnigent-agent-profile.v2":
        refs = document.get("allowedLaunchPolicyRefs")
    else:
        execution = document.get("execution")
        refs = execution.get("allowedLaunchPolicyRefs") if isinstance(execution, Mapping) else None
    return [str(ref) for ref in refs or () if str(ref or "").strip()]


def _replace_policy_ref_deduped(
    refs: Any, *, old_ref: str, new_ref: str
) -> list[str]:
    """Replace ``old_ref`` with ``new_ref`` while deduplicating the result.

    When an active profile already admits both the predecessor and the
    successor, a plain in-place replacement produces duplicate successor refs
    such as ``["p@3", "p@3"]``. ``refresh_schedule_deployment_snapshot`` then
    finds two same-identity candidates and rejects the schedule, so the
    automatic cutover recreates the wedge it is meant to remove. Deduplicate
    while preserving order so the cutover admits the successor exactly once.
    """
    replaced = [new_ref if str(ref) == old_ref else str(ref) for ref in refs or ()]
    deduped: list[str] = []
    for ref in replaced:
        if ref not in deduped:
            deduped.append(ref)
    return deduped


async def advance_agent_profiles_for_policy_cutover(
    session: AsyncSession,
    *,
    cutovers: Mapping[str, str],
    actor: str = "bootstrap",
) -> list[dict[str, Any]]:
    """Advance active profiles across a same-policy cutover without manual edits.

    When bootstrap reconcile (or the release migration) moves a policy default
    from ``policy@14`` to ``policy@16`` for a compatible rebuild, long-lived
    schedules still pin ``@14`` and the Agent Profile still allows only
    ``@14``. ``refresh_schedule_deployment_snapshot`` then keeps requiring an
    explicit revision. This advances every active profile whose active version
    admits the predecessor ref to an equivalent version admitting the
    successor ref, preserving all other semantics.

    Only same-policy cutovers advance automatically (``policy@old`` to
    ``policy@new`` with equal policy ids); a different policy identity still
    requires an explicit schedule revision. Existing usages and in-flight runs
    keep their recorded version authority; only the active pointer moves, so
    the next schedule refresh can cut over while running executions stay on
    their recorded-host plans. Re-running with the same cutover reuses the
    existing version instead of forking.
    """
    pairs: list[tuple[str, str, str]] = []
    for old_ref, new_ref in dict(cutovers).items():
        old_id, separator, _old_version = str(old_ref).rpartition("@")
        new_id, new_separator, _new_version = str(new_ref).rpartition("@")
        if not separator or not new_separator or old_id != new_id:
            continue
        if str(old_ref) == str(new_ref):
            continue
        pairs.append((old_id, str(old_ref), str(new_ref)))
    if not pairs:
        return []
    profiles = list(
        (
            await session.execute(
                select(OmnigentAgentProfile).where(
                    OmnigentAgentProfile.state == "active",
                    OmnigentAgentProfile.active_version.is_not(None),
                )
            )
        )
        .scalars()
        .all()
    )
    advanced: list[dict[str, Any]] = []
    for profile in profiles:
        observed_version = profile.active_version
        active = await session.scalar(
            select(OmnigentAgentProfileVersion).where(
                OmnigentAgentProfileVersion.profile_id == profile.profile_id,
                OmnigentAgentProfileVersion.version == observed_version,
            )
        )
        if active is None or not isinstance(active.document, Mapping):
            continue
        document = copy.deepcopy(dict(active.document))
        is_v2 = document.get("schemaVersion") == "moonmind.omnigent-agent-profile.v2"
        changed = False
        applied: list[dict[str, str]] = []
        for policy_id, old_ref, new_ref in pairs:
            refs = _allowed_policy_refs(document)
            if old_ref not in refs:
                continue
            if is_v2:
                document["allowedLaunchPolicyRefs"] = _replace_policy_ref_deduped(
                    document.get("allowedLaunchPolicyRefs"),
                    old_ref=old_ref,
                    new_ref=new_ref,
                )
            else:
                execution = copy.deepcopy(dict(document.get("execution") or {}))
                execution["allowedLaunchPolicyRefs"] = _replace_policy_ref_deduped(
                    execution.get("allowedLaunchPolicyRefs"),
                    old_ref=old_ref,
                    new_ref=new_ref,
                )
                document["execution"] = execution
                if document.get("policyRef") == old_ref:
                    document["policyRef"] = new_ref
            changed = True
            applied.append({"previousRef": old_ref, "policyRef": new_ref})
        if not changed:
            continue
        digest = _profile_document_digest(document)
        candidate = await session.scalar(
            select(OmnigentAgentProfileVersion).where(
                OmnigentAgentProfileVersion.profile_id == profile.profile_id,
                OmnigentAgentProfileVersion.digest == digest,
            )
        )
        if candidate is None:
            for _attempt in range(3):
                latest = int(
                    await session.scalar(
                        select(func.max(OmnigentAgentProfileVersion.version)).where(
                            OmnigentAgentProfileVersion.profile_id == profile.profile_id
                        )
                    )
                    or 0
                )
                pending = OmnigentAgentProfileVersion(
                    profile_id=profile.profile_id,
                    version=latest + 1,
                    digest=digest,
                    document=document,
                    parent_version=active.version,
                    upstream_snapshot=copy.deepcopy(active.upstream_snapshot),
                    validation_result=copy.deepcopy(active.validation_result),
                    rollout_metadata={
                        **(copy.deepcopy(active.rollout_metadata) or {}),
                        "origin": "bootstrap_policy_cutover",
                        "previousVersion": active.version,
                        "policyCutovers": applied,
                        "materializedBy": actor,
                    },
                    created_by=None,
                )
                try:
                    async with session.begin_nested():
                        session.add(pending)
                        await session.flush()
                except IntegrityError:
                    # Concurrent cutover or activation allocated the same
                    # version number or the same digest first. Reuse whatever
                    # won instead of forking a duplicate.
                    candidate = await session.scalar(
                        select(OmnigentAgentProfileVersion).where(
                            OmnigentAgentProfileVersion.profile_id
                            == profile.profile_id,
                            OmnigentAgentProfileVersion.digest == digest,
                        )
                    )
                    if candidate is not None:
                        break
                    continue
                candidate = pending
                break
            if candidate is None:
                continue
        validation = candidate.validation_result
        if not isinstance(validation, Mapping) or validation.get("ready") is not True:
            # Never activate an unvalidated matching version: normal
            # resolution rejects a not-ready active version as not
            # launch-ready, wedging new launches and schedule refreshes.
            # Leave the current active pointer until the candidate is
            # validated.
            continue
        if observed_version != candidate.version:
            # Condition the pointer move on the previously observed active
            # version so an automatic cutover cannot silently undo an explicit
            # concurrent profile activation. A rowcount of zero means the
            # operator moved the pointer after our read; keep their choice.
            moved = await session.execute(
                update(OmnigentAgentProfile)
                .where(
                    OmnigentAgentProfile.profile_id == profile.profile_id,
                    OmnigentAgentProfile.active_version == observed_version,
                )
                .values(active_version=candidate.version)
            )
            if (moved.rowcount or 0) == 0:
                continue
            profile.active_version = candidate.version
            session.add(
                OmnigentAgentProfileAuditEvent(
                    profile_id=profile.profile_id,
                    action="bootstrap_launch_policy_cutover",
                    version=candidate.version,
                    actor_id=None,
                    metadata_json={
                        "previousVersion": observed_version,
                        "policyCutovers": applied,
                        "state": "active",
                    },
                )
            )
            await session.flush()
        advanced.append(
            {
                "profileId": profile.profile_id,
                "version": candidate.version,
                "digest": candidate.digest,
                "policyCutovers": applied,
            }
        )
    return advanced


async def refresh_schedule_deployment_snapshot(
    session: AsyncSession,
    *,
    parameters: Mapping[str, Any],
    consumer_id: str,
    user: User | None,
) -> dict[str, Any]:
    """Advance deployment bindings only when the scheduled semantics are equal.

    Image qualification can move catalog and policy versions independently of
    a long-lived schedule. Verify durable profile/usage lineage and compare
    every execution boundary before resolving a replacement snapshot. Existing
    executions keep their original authority.
    """
    from api_service.services.omnigent_policies import OmnigentPolicyService

    compiled = copy.deepcopy(dict(parameters))
    previous = compiled.get("agentProfileSnapshot") or {}
    document = previous.get("document") or {}
    if document.get("schemaVersion") != "moonmind.omnigent-agent-profile.v2":
        return compiled
    profile_id = previous.get("profileId")
    profile = await session.get(OmnigentAgentProfile, profile_id)
    if profile is None or profile.state != "active" or not profile.active_version:
        raise ValueError("scheduled Agent Profile is not active")
    if profile.active_version == previous.get("version"):
        return compiled
    versions = {}
    for number in (previous.get("version"), profile.active_version):
        versions[number] = await session.scalar(
            select(OmnigentAgentProfileVersion).where(
                OmnigentAgentProfileVersion.profile_id == profile_id,
                OmnigentAgentProfileVersion.version == number,
            )
        )
    old, active = versions[previous.get("version")], versions[profile.active_version]
    usage = await session.scalar(
        select(OmnigentAgentProfileUsage).where(
            OmnigentAgentProfileUsage.consumer_type == "schedule",
            OmnigentAgentProfileUsage.consumer_id == consumer_id,
        )
    )
    if (
        old is None
        or active is None
        or old.digest != previous.get("digest")
        or usage is None
        or usage.profile_id != profile_id
        or usage.version != previous.get("version")
        or usage.digest != previous.get("digest")
        or usage.effective_snapshot != previous
    ):
        raise ValueError("scheduled Agent Profile snapshot lineage conflicts")

    def semantics(value: Mapping[str, Any]) -> dict[str, Any]:
        result = copy.deepcopy(dict(value))
        result.pop("allowedLaunchPolicyRefs", None)
        result.get("harness", {}).pop("catalogRef", None)
        source = result.get("source", {})
        source.pop("upstreamVersion", None)
        source.pop("upstreamSnapshotDigest", None)
        return result

    old_semantics = semantics(old.document)
    active_semantics = semantics(active.document)
    if old_semantics != active_semantics:
        # A schedule pins one Provider Profile. Widening or narrowing the
        # profile's authoring choices does not change this schedule's launch
        # when its selected provider remains allowed by both versions. Keep
        # every other credential and execution boundary in the comparison.
        provider_ref = previous.get("providerProfileRef")
        provider = (
            await session.get(ManagedAgentProviderProfile, provider_ref)
            if isinstance(provider_ref, str) and provider_ref
            else None
        )
        old_slots = old_semantics.get("credentialSlots") or []
        active_slots = active_semantics.get("credentialSlots") or []
        if (
            provider is not None
            and provider.provider_id
            and len(old_slots) == len(active_slots) == 1
        ):
            old_ids = old_slots[0].get("acceptedProviderIds") or []
            active_ids = active_slots[0].get("acceptedProviderIds") or []
            if (
                (not old_ids or provider.provider_id in old_ids)
                and (not active_ids or provider.provider_id in active_ids)
            ):
                old_slots[0].pop("acceptedProviderIds", None)
                active_slots[0].pop("acceptedProviderIds", None)
        if old_semantics != active_semantics:
            raise ValueError(
                "scheduled Agent Profile semantics changed; revise the schedule explicitly"
            )
    old_upstream = copy.deepcopy(old.upstream_snapshot or {})
    active_upstream = copy.deepcopy(active.upstream_snapshot or {})
    old_upstream.pop("version", None)
    active_upstream.pop("version", None)
    if old_upstream != active_upstream:
        raise ValueError(
            "scheduled upstream agent metadata changed; revise the schedule explicitly"
        )
    # Retain the chosen policy identity, including non-default selections.
    old_ref = str(previous.get("launchPolicyRef") or "")
    policy_id, separator, _version = old_ref.rpartition("@")
    candidates = [
        ref
        for ref in active.document.get("allowedLaunchPolicyRefs", [])
        if ref.rpartition("@")[0] == policy_id
    ]
    if not separator or len(candidates) != 1:
        raise ValueError(
            "scheduled launch policy identity is no longer available "
            f"(pinned={old_ref or '<missing>'} "
            f"profile={profile_id}@active "
            f"candidates={candidates}); revise the schedule explicitly to a "
            "currently allowed policy ref"
        )
    new_ref = candidates[0]
    policies = OmnigentPolicyService(session)
    # A predecessor may already be superseded. Its immutable document is
    # comparison evidence; only the replacement grants new runtime authority.
    old_policy = await policies.snapshot(policy_id, int(_version))
    new_policy = await policies.resolve_runtime_snapshot(new_ref)
    boundaries = []
    for policy in (old_policy, new_policy):
        boundary = copy.deepcopy(policy["boundaries"])
        host = boundary.get("host", {})
        for field in ("serverImageRef", "hostImageRef"):
            image_ref = host.get(field)
            if isinstance(image_ref, str) and re.fullmatch(
                r"[^\s@]+@sha256:[0-9a-f]{64}", image_ref
            ):
                # Only the immutable digest may advance. Registry, repository,
                # and any authored tag remain part of executable source authority.
                host[field] = (image_ref.rpartition("@")[0], "sha256")
        boundaries.append(boundary)
    if boundaries[0] != boundaries[1]:
        raise ValueError(
            "scheduled launch policy boundaries changed; revise the schedule explicitly "
            f"(pinned={old_ref} replacement={new_ref})"
        )

    refreshed = await resolve_agent_profile_snapshot(
        session,
        selection={
            "profileId": profile_id,
            "version": active.version,
            "digest": active.digest,
            "providerProfileRef": previous.get("providerProfileRef"),
            "launchPolicyRef": new_ref,
            "overrides": {
                section: {
                    key: copy.deepcopy((document.get(section) or {}).get(key))
                    for key in set(old.document.get(section) or {})
                    | set(document.get(section) or {})
                }
                for section in _OVERRIDABLE_SECTIONS
                if section in document or section in old.document
            },
        },
        consumer_type="schedule",
        consumer_id=consumer_id,
        user=user,
        persist_usage=False,
    )
    if refreshed.get("upstreamSnapshot") != active.upstream_snapshot:
        raise ValueError(
            "resolved upstream agent metadata differs from the checked profile version"
        )
    result = compile_agent_profile_snapshot_parameters(compiled, snapshot=refreshed)
    # Schedule authoring resolves these after profile compilation, so the
    # persisted top-level selections take precedence over profile defaults.
    for field in ("model", "effort"):
        if field in compiled:
            result[field] = compiled[field]
    return result


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
    "advance_agent_profiles_for_policy_cutover",
    "compile_agent_profile_snapshot_parameters",
    "default_launch_policy_ref",
    "refresh_managed_bootstrap_snapshot",
    "refresh_schedule_deployment_snapshot",
    "resolve_agent_profile_snapshot",
    "resolve_default_agent_profile_snapshot",
]
