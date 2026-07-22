"""Safe, versioned Omnigent Codex readiness projection for Workflow Create.

MoonLadderStudios/MoonMind#3451.  This boundary deliberately returns product
selection data, never launch authority or provider/host secret material.
"""

from __future__ import annotations

import os
import re
from typing import Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.api.routers.provider_profiles import (
    _managed_secret_statuses_for_rows,
    _provider_profile_readiness,
    _require_provider_profile_permission,
    _secret_ref_results_for_rows,
)
from api_service.auth_providers import get_current_user
from api_service.db.base import get_async_session
from api_service.db.models import (
    ManagedAgentProviderProfile,
    OmnigentOAuthHostBindingRecord,
    User,
)
from moonmind.config.container_backend_settings import (
    ContainerBackendConfigError,
    resolve_container_backend_settings,
)
from moonmind.omnigent.bridge_config import HOST_PROTOCOL_MODE_EMBEDDED
from moonmind.omnigent.execution_profiles import POLICIES, PROFILES
from moonmind.omnigent.host_auth_profile import HostAuthProfileError, host_auth_readiness
from moonmind.utils.logging import redact_sensitive_payload

from .omnigent_bridge import (
    _active_host_auth_profile,
    _resolve_embedded_evidence,
    get_bridge_config,
)

router = APIRouter(prefix="/api/omnigent", tags=["Omnigent Catalog"])

_SCHEMA_VERSION = "moonmind.omnigent-codex-readiness.v1"
_DIGEST_IMAGE = re.compile(r"^[^\s@]+@sha256:[0-9a-f]{64}$")


class GateReason(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    code: str
    message: str
    remediation_href: str = Field(alias="remediationHref")


class EligibleProviderProfile(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    profile_id: str = Field(alias="profileId")
    label: str
    provider_id: str = Field(alias="providerId")
    busy: bool = False
    queue_when_busy: bool = Field(alias="queueWhenBusy")


class ExecutionProfileReadiness(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    ref: str
    display_name: str = Field(alias="displayName")
    available: bool
    policy_refs: list[str] = Field(alias="policyRefs")
    gate_reasons: list[GateReason] = Field(alias="gateReasons")


class OmnigentCodexCatalogReadiness(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    schema_version: Literal["moonmind.omnigent-codex-readiness.v1"] = Field(
        _SCHEMA_VERSION, alias="schemaVersion"
    )
    runtime_id: Literal["omnigent"] = Field("omnigent", alias="runtimeId")
    display_name: Literal["Codex via Omnigent"] = Field(
        "Codex via Omnigent", alias="displayName"
    )
    agent_kind: Literal["external"] = Field("external", alias="agentKind")
    agent_id: Literal["omnigent"] = Field("omnigent", alias="agentId")
    harness: Literal["codex-native"] = "codex-native"
    available: bool
    default_execution_profile_ref: str = Field(alias="defaultExecutionProfileRef")
    execution_profiles: list[ExecutionProfileReadiness] = Field(alias="executionProfiles")
    eligible_provider_profiles: list[EligibleProviderProfile] = Field(
        alias="eligibleProviderProfiles"
    )
    host_modes: list[str] = Field(alias="hostModes")
    gate_reasons: list[GateReason] = Field(alias="gateReasons")


_REASONS: dict[str, tuple[str, str]] = {
    "bridge_disabled": ("Enable the Omnigent bridge in deployment settings.", "/settings#omnigent"),
    "bridge_conformance_gated": ("Complete Omnigent bridge conformance checks.", "/settings#omnigent"),
    "bridge_endpoint_unavailable": ("Configure the selected Omnigent endpoint.", "/settings#omnigent"),
    "host_auth_unavailable": ("Configure or rotate Omnigent bridge credentials.", "/settings#omnigent"),
    "no_eligible_codex_oauth_profile": ("Connect and validate a Codex OAuth Provider Profile.", "/settings#provider-profiles"),
    "execution_profile_unavailable": ("Enable a compatible Omnigent execution profile.", "/settings#omnigent"),
    "on_demand_backend_unavailable": ("Enable the trusted container backend and worker route.", "/settings#system"),
    "static_host_not_ready": ("Start and validate the static Omnigent Codex host.", "/settings#omnigent"),
    "immutable_image_unavailable": ("Configure immutable Omnigent server and host image digests.", "/settings#omnigent"),
    "network_policy_unavailable": ("Configure the required enforced egress policy.", "/settings#omnigent"),
    "workspace_resolver_unavailable": ("Restore the workflow workspace resolver.", "/settings#system"),
}


def _reason(code: str) -> GateReason:
    message, href = _REASONS[code]
    return GateReason(code=code, message=message, remediationHref=href)


def _deployment_reasons(config: Any, bridge: dict[str, Any]) -> list[GateReason]:
    reasons: list[GateReason] = []
    if not config.enabled:
        return [_reason("bridge_disabled")]
    if bridge.get("conformanceState") != "ready":
        reasons.append(_reason("bridge_conformance_gated"))
    if os.getenv("MOONMIND_WORKSPACE_RESOLVER_ENABLED", "true").lower() not in {
        "1", "true", "yes", "on"
    }:
        reasons.append(_reason("workspace_resolver_unavailable"))
    return reasons


def _images_ready() -> bool:
    return all(
        _DIGEST_IMAGE.fullmatch(os.getenv(name, "").strip())
        for name in ("OMNIGENT_IMAGE_REF", "OMNIGENT_HOST_IMAGE_REF")
    )


@router.get(
    "/codex-catalog-readiness",
    response_model=OmnigentCodexCatalogReadiness,
    response_model_by_alias=True,
)
async def get_omnigent_codex_catalog_readiness(
    session: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(get_current_user()),
) -> OmnigentCodexCatalogReadiness:
    """Return a fresh, bounded readiness snapshot; this response is not cached."""

    _require_provider_profile_permission(current_user, "provider_profiles.read")
    config = get_bridge_config()
    evidence = (
        await _resolve_embedded_evidence(config)
        if config.enabled and config.host_protocol_mode == HOST_PROTOCOL_MODE_EMBEDDED
        else None
    )
    bridge = config.readiness(evidence_validation=evidence)
    deployment_reasons = _deployment_reasons(config, bridge)
    if config.enabled and config.host_protocol_mode == HOST_PROTOCOL_MODE_EMBEDDED:
        try:
            auth = await host_auth_readiness(profile=await _active_host_auth_profile())
        except HostAuthProfileError:
            auth = {"ready": False}
        if not auth.get("ready"):
            deployment_reasons.append(_reason("host_auth_unavailable"))

    rows = list((await session.execute(
        select(ManagedAgentProviderProfile).where(
            ManagedAgentProviderProfile.runtime_id == "codex_cli"
        )
    )).scalars().all())
    secret_results = _secret_ref_results_for_rows(rows)
    secret_statuses = await _managed_secret_statuses_for_rows(
        session, rows, secret_ref_results=secret_results
    )
    eligible: list[EligibleProviderProfile] = []
    for row in rows:
        credential_source = getattr(row.credential_source, "value", row.credential_source)
        materialization = getattr(
            row.runtime_materialization_mode, "value", row.runtime_materialization_mode
        )
        readiness = _provider_profile_readiness(
            row,
            managed_secret_statuses=secret_statuses,
            secret_ref_results=secret_results.get(row.profile_id),
        )
        if (
            credential_source == "oauth_volume"
            and materialization == "oauth_home"
            and readiness["launch_ready"]
        ):
            eligible.append(EligibleProviderProfile(
                profileId=row.profile_id,
                label=str(redact_sensitive_payload(
                    row.account_label or row.provider_label or row.profile_id
                )),
                providerId=row.provider_id,
                busy=False,
                queueWhenBusy=getattr(row.rate_limit_policy, "value", row.rate_limit_policy) == "queue",
            ))

    bindings = list((await session.execute(select(OmnigentOAuthHostBindingRecord))).scalars().all())
    static_ready = any(
        binding.static_host_id and binding.host_launch_profile_ref == "static"
        for binding in bindings
    )
    try:
        backend_ready = resolve_container_backend_settings().enabled
    except ContainerBackendConfigError:
        backend_ready = False
    images_ready = _images_ready()

    profile_views: list[ExecutionProfileReadiness] = []
    available_modes: list[str] = []
    for profile in PROFILES.values():
        profile_reasons = list(deployment_reasons)
        policy_refs: list[str] = []
        policy_gate_reasons: list[GateReason] = []
        for policy in POLICIES.values():
            policy_reasons: list[GateReason] = []
            if not policy.enabled:
                policy_reasons.append(_reason("execution_profile_unavailable"))
            if not images_ready:
                policy_reasons.append(_reason("immutable_image_unavailable"))
            if not policy.enforced_egress or not policy.network_ref:
                policy_reasons.append(_reason("network_policy_unavailable"))
            if policy.host_mode == "on_demand_docker" and not backend_ready:
                policy_reasons.append(_reason("on_demand_backend_unavailable"))
            if policy.host_mode == "static_compose" and not static_ready:
                policy_reasons.append(_reason("static_host_not_ready"))
            if not policy_reasons:
                policy_refs.append(policy.ref)
                available_modes.append(policy.host_mode)
            policy_gate_reasons.extend(policy_reasons)
        for reason in policy_gate_reasons:
            if reason.code not in {existing.code for existing in profile_reasons}:
                profile_reasons.append(reason)
        if not eligible:
            profile_reasons.append(_reason("no_eligible_codex_oauth_profile"))
        profile_views.append(ExecutionProfileReadiness(
            ref=profile.ref,
            displayName=profile.display_name,
            available=profile.enabled and bool(policy_refs) and bool(eligible) and not deployment_reasons,
            policyRefs=policy_refs,
            gateReasons=profile_reasons,
        ))

    available = any(item.available for item in profile_views)
    top_reasons = [] if available else (profile_views[0].gate_reasons if profile_views else [_reason("execution_profile_unavailable")])
    return OmnigentCodexCatalogReadiness(
        available=available,
        defaultExecutionProfileRef=next(iter(PROFILES)),
        executionProfiles=profile_views,
        eligibleProviderProfiles=eligible,
        hostModes=sorted(set(available_modes)),
        gateReasons=top_reasons,
    )
