"""Resolve a Profile's subordinate execution configuration at API boundaries.

Inventory and selection never depend on model discovery or temporary runtime
health. Executable selections require a ready immutable configuration; credential
authorities are validated when a consumer snapshot is committed and on the host.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import and_, or_, select

from api_service.db.models import OmnigentAgentProfile, OmnigentAgentProfileVersion
from moonmind.omnigent.harness_platform.harness_registry import canonical_harness_id


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


def profile_has_native_inventory_route(
    provider: Any,
    configurations: list[tuple[Any, Any]],
) -> bool:
    """Identify direct-runtime inventory without bypassing configured authority.

    An unvalidated compatible configuration still owns the Profile's route. This
    projection must not reinterpret that configuration's failure as permission
    to execute through a different runtime.
    """
    from moonmind.workflows.executions.execution_contract import (
        SUPPORTED_EXECUTION_RUNTIMES,
    )

    return (
        not getattr(provider, "execution_configuration", None)
        and provider.runtime_id in SUPPORTED_EXECUTION_RUNTIMES
        and not any(
            configuration_accepts_profile(version.document, provider)
            for _, version in configurations
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
    compatible_configurations = [
        (row, version)
        for row, version in configurations
        if isinstance(getattr(version, "validation_result", None), Mapping)
        and version.validation_result.get("ready") is True
        and configuration_accepts_profile(version.document, provider)
    ]
    candidates = compatible_configurations
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
                    else "This Profile's execution configuration is unavailable, unvalidated, or incompatible."
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
        "harnessId": canonical_harness_id(document.get("harness")),
        "launchPolicyRef": policies[0] if policies else None,
        "defaultForRuntime": any(
            candidate.default_for_runtime
            and candidate_version.version == candidate.active_version
            for candidate, candidate_version in compatible_configurations
        ),
    }


async def profile_execution_selection(
    session: Any, provider: Any, user: Any
) -> dict[str, Any]:
    return select_execution_configuration(
        provider,
        await load_execution_configurations(session, user, [provider]),
    )


def resolved_execution_target_refs(snapshot: Mapping[str, Any]) -> set[str]:
    """Return the unambiguous target identities advertised by a snapshot.

    Generic (v2) Agent Profiles advertise readiness targets as the profile
    identity (``profileId@version``), while their compiled plan carries the
    host realizer ref. Legacy profiles advertise the execution-profile ref
    directly. Both forms resolve unambiguously (MoonLadderStudios/MoonMind#3833).
    """
    resolved_ref = str(snapshot.get("executionProfileRef") or "").strip()
    refs = {resolved_ref} if resolved_ref else set()
    profile_id = str(snapshot.get("profileId") or "").strip()
    version = snapshot.get("version")
    if profile_id and version is not None:
        refs.add(f"{profile_id}@{version}")
    return refs


def validate_omnigent_selection_agreement(
    *,
    expected_execution_configuration: Any,
    authored_omnigent: Any,
    profile_snapshot: Mapping[str, Any],
    selected_provider_profile_id: str | None = None,
) -> None:
    """Enforce the shared new-admission intent/conflict boundary (#3833).

    Every new-admission consumer (Create, schedules, checkpoint branches)
    funnels explicit authoring intent through this boundary instead of
    maintaining a per-surface map. Existing-owner retry/replay differs: those
    paths reuse the frozen recorded snapshot/plan authority and never call
    this helper.
    """
    validate_execution_configuration_expectation(
        expected_execution_configuration, profile_snapshot
    )
    if selected_provider_profile_id and (
        profile_snapshot.get("providerProfileRef") != selected_provider_profile_id
    ):
        # Preserve the Create surface's invalid_execution_request contract
        # verbatim so every consumer surfaces the identical 422 shape.
        raise HTTPException(
            422,
            {
                "code": "invalid_execution_request",
                "message": "The execution configuration must use the selected Profile.",
            },
        )
    authored_target_ref = (
        str(authored_omnigent.get("executionTargetRef") or "").strip()
        if isinstance(authored_omnigent, Mapping)
        else ""
    )
    if authored_target_ref and (
        authored_target_ref not in resolved_execution_target_refs(profile_snapshot)
    ):
        raise HTTPException(
            422,
            {
                "code": "invalid_execution_request",
                "message": (
                    "omnigent.executionTargetRef must match the selected "
                    "Agent Profile executionProfileRef."
                ),
            },
        )


def validate_execution_configuration_expectation(
    expected: Any, snapshot: Mapping[str, Any]
) -> None:
    """Reject stale authoring projections without selecting a different config.

    Older API clients omit the expectation and retain profile-first resolution.
    This check runs at admission only; recorded snapshots remain replay authority.
    """
    if expected is None:
        return
    from pydantic import ValidationError

    try:
        reference = ProfileExecutionConfiguration.model_validate(expected)
    except ValidationError as exc:
        raise HTTPException(
            422, "Invalid runtime.executionConfiguration reference."
        ) from exc
    if any(snapshot.get(key) != value for key, value in reference.model_dump().items()):
        raise HTTPException(
            409,
            {
                "code": "profile_execution_configuration_changed",
                "message": "This Profile's execution configuration changed. Refresh the form and review the Profile in Settings before submitting again.",
            },
        )
