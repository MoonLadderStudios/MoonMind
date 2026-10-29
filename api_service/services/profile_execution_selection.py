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
from moonmind.runtime_identity import normalize_runtime_id


class ProfileExecutionConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profileId: str = Field(min_length=1)
    version: int = Field(ge=1)
    digest: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")


def _expected_harness_for_runtime(runtime_id: Any) -> str | None:
    """Return the native harness an execution runtime resolves through.

    Legacy v1 configuration documents carry provider requirements but no
    harness authority; when a document does name an explicit harness, a
    cross-harness substitution must fail at the selection boundary rather
    than resolve through the foreign harness configuration.
    """
    mapping = {
        "opencode": "opencode-native",
        "codex": "codex-native",
        "codex_cli": "codex-native",
        "claude": "claude-native",
        "claude_code": "claude-native",
        "omnigent": "pi-native",
    }
    return mapping.get(str(runtime_id or ""))


def profile_runtime_supported(runtime_id: Any) -> bool:
    """Use existing native harness and external capability owners for admission."""
    from moonmind.workflows.executions.runtime_capabilities import (
        RUNTIME_EXECUTION_CAPABILITIES,
    )

    raw_runtime = str(runtime_id or "").strip()
    normalized = normalize_runtime_id(raw_runtime) if raw_runtime else ""
    return (
        _expected_harness_for_runtime(normalized) is not None
        or normalized in RUNTIME_EXECUTION_CAPABILITIES.runtime_ids
    )


def configuration_accepts_profile(document: Mapping[str, Any], provider: Any) -> bool:
    # #4644: unsupported saved Profiles remain readable, never executable.
    if not profile_runtime_supported(getattr(provider, "runtime_id", None)):
        return False

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

    if not (
        bool(requirements)
        and all(
            (
                provider.runtime_id == requirements.get("runtimeId"),
                value(provider.credential_source)
                == requirements.get("credentialSource"),
                value(provider.runtime_materialization_mode)
                == requirements.get("materializationMode"),
                not requirements.get("providerIds")
                or provider.provider_id in requirements["providerIds"],
            )
        )
    ):
        return False

    # An explicit harness naming a foreign native target must not resolve
    # through this Profile, even when the legacy provider requirements match.
    # Documents without a harness predate harness authority and stay accepted.
    harness_raw = document.get("harness")
    if harness_raw:
        harness_id = canonical_harness_id(harness_raw)
        expected = _expected_harness_for_runtime(getattr(provider, "runtime_id", None))
        if harness_id and expected and harness_id != expected:
            return False
    from moonmind.omnigent.harness_platform.materializers import (
        is_api_key_launch_profile,
        provider_config_endpoint,
    )

    if is_api_key_launch_profile(provider):
        # An API-key Codex or Claude Profile leaves its direct launch only
        # when the Omnigent provider config carries its key, endpoint and
        # model pins exactly; otherwise its direct route keeps serving it.
        return provider_config_endpoint(provider)[0] is not None
    return True


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


async def profile_route_runtime(session: Any, user: Any, provider: Any) -> str:
    """Return the runtime a selected Profile implies when none is authored.

    Every new-work surface (Create, schedules, preset expansion, CLI) asks this
    one question so the same Profile never reaches a different Backend by
    caller. An unready or pinned Omnigent configuration keeps the Omnigent
    route rather than falling back direct.
    """
    configurations = await load_execution_configurations(session, user, [provider])
    if profile_has_native_inventory_route(provider, configurations):
        return provider.runtime_id
    return "omnigent"


def _is_managed_fallback(row: Any, version: Any) -> bool:
    """Mirror the bootstrap reconciler's managed-stock ownership check.

    The stock Claude configurations (OAuth and API key) and the stock Codex
    API-key configuration are fallbacks only while their active version is
    still managed (reconciliation-owned, carrying the managedBuiltin marker).
    An operator-edited active version is an authored configuration: it stays
    a candidate (or forces explicit selection) instead of being silently
    discarded from ambiguity.
    """
    from api_service.services.omnigent_agent_bootstrap_service import (
        MANAGED_BUILTIN_MARKERS,
    )

    marker = MANAGED_BUILTIN_MARKERS.get(getattr(row, "profile_id", None))
    if marker is None:
        return False
    if getattr(version, "created_by", None) is not None:
        return False
    metadata = getattr(version, "rollout_metadata", None) or {}
    return isinstance(metadata, Mapping) and metadata.get("managedBuiltin") == marker


async def load_execution_configurations(
    session: Any,
    user: Any,
    providers: list[Any],
) -> list[tuple[Any, Any]]:
    # Single-user (#4349): execution configurations are instance resources.
    # ``user`` is retained for call-site compatibility (admission identity
    # stays owned by #4347) but is never a visibility predicate here:
    # private/workspace scoping by human owner is removed; settings
    # permission gates at the routers remain the access boundary.
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
    return [(row, version) for row, version in (await session.execute(statement)).all()]


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
        else:
            authored_candidates = [
                (row, version)
                for row, version in candidates
                if not _is_managed_fallback(row, version)
            ]
            if authored_candidates:
                # The managed stock configuration is a fallback, not a reason
                # to make an existing operator configuration ambiguous.
                candidates = authored_candidates
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
