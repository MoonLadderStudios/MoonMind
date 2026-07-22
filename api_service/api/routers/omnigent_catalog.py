"""Safe, versioned Omnigent Codex readiness projection for Workflow Create.

MoonLadderStudios/MoonMind#3451.  This boundary deliberately returns product
selection data, never launch authority or provider/host secret material.
"""

from __future__ import annotations

import os
import re
from datetime import UTC, datetime
from typing import Any, Literal

import httpx

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.api.routers.provider_profiles import (
    _can_view_profile,
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
    OmnigentOAuthHostLeaseRecord,
    ProviderProfileSlotLease,
    User,
)
from moonmind.config.container_backend_settings import (
    ContainerBackendConfigError,
    resolve_container_backend_settings,
)
from moonmind.config.settings import settings
from moonmind.omnigent.bridge_config import HOST_PROTOCOL_MODE_EMBEDDED
from moonmind.omnigent.execution_profiles import POLICIES, PROFILES
from moonmind.omnigent.host_auth_profile import HostAuthProfileError, host_auth_readiness
from moonmind.omnigent.settings import build_omnigent_gate, resolved_server_url
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


class IneligibleProviderProfile(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    profile_id: str = Field(alias="profileId")
    label: str
    gate_reasons: list[GateReason] = Field(alias="gateReasons")


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
    ineligible_provider_profiles: list[IneligibleProviderProfile] = Field(
        alias="ineligibleProviderProfiles"
    )
    host_modes: list[str] = Field(alias="hostModes")
    gate_reasons: list[GateReason] = Field(alias="gateReasons")


_REASONS: dict[str, tuple[str, str]] = {
    "bridge_disabled": ("Enable the Omnigent bridge in deployment settings.", "/settings#omnigent"),
    "bridge_conformance_gated": ("Complete Omnigent bridge conformance checks.", "/settings#omnigent"),
    "bridge_endpoint_unavailable": ("Configure the selected Omnigent endpoint.", "/settings#omnigent"),
    "rollout_gate_disabled": ("Enable the Omnigent runtime rollout gate.", "/settings#omnigent"),
    "host_auth_unavailable": ("Configure or rotate Omnigent bridge credentials.", "/settings#omnigent"),
    "no_eligible_codex_oauth_profile": ("Connect and validate a Codex OAuth Provider Profile.", "/settings#provider-profiles"),
    "execution_profile_unavailable": ("Enable a compatible Omnigent execution profile.", "/settings#omnigent"),
    "on_demand_backend_unavailable": ("Enable the trusted container backend and worker route.", "/settings#system"),
    "static_host_not_ready": ("Start and validate the static Omnigent Codex host.", "/settings#omnigent"),
    "immutable_image_unavailable": ("Configure immutable Omnigent server and host image digests.", "/settings#omnigent"),
    "network_policy_unavailable": ("Configure the required enforced egress policy.", "/settings#omnigent"),
    "workspace_resolver_unavailable": ("Restore the workflow workspace resolver.", "/settings#system"),
    "profile_reconnect_required": ("Reconnect this Codex OAuth Provider Profile.", "/settings#provider-profiles"),
    "profile_validation_required": ("Validate this Codex OAuth Provider Profile.", "/settings#provider-profiles"),
    "profile_capacity_unavailable": ("Wait for Provider Profile capacity or enable queued execution.", "/settings#provider-profiles"),
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
    runtime_gate = build_omnigent_gate()
    if not runtime_gate.enabled:
        reasons.append(_reason("rollout_gate_disabled"))
    if (
        config.host_protocol_mode != HOST_PROTOCOL_MODE_EMBEDDED
        and not resolved_server_url()
    ):
        reasons.append(_reason("bridge_endpoint_unavailable"))
    if os.getenv("MOONMIND_WORKSPACE_RESOLVER_ENABLED", "true").lower() not in {
        "1", "true", "yes", "on"
    }:
        reasons.append(_reason("workspace_resolver_unavailable"))
    return reasons


async def _live_deployment_readiness() -> tuple[bool, set[str]]:
    """Read bounded health projections from the services that own readiness."""

    endpoint = resolved_server_url()
    endpoint_ready = False
    if endpoint:
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                response = await client.get(endpoint.rstrip("/") + "/health")
                endpoint_ready = response.status_code < 400
        except (httpx.HTTPError, ValueError):
            # Readiness is fail-closed; an unreachable optional endpoint is
            # represented by endpoint_ready=False in the catalog response.
            pass

    worker_url = os.getenv(
        "TEMPORAL_AGENT_RUNTIME_READINESS_URL",
        "http://temporal-worker-agent-runtime:8080/readyz",
    )
    enforced_network_refs: set[str] = set()
    backend_ready = False
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            response = await client.get(worker_url)
            response.raise_for_status()
            payload = response.json()
        task_queues = {str(value) for value in payload.get("taskQueues", [])}
        backend = payload.get("containerBackend", {})
        backend_ready = (
            payload.get("ready") is True
            and settings.temporal.activity_agent_runtime_task_queue in task_queues
            and backend.get("ready") is True
        )
        enforced_network_refs = {
            str(value) for value in backend.get("enforcedNetworkRefs", [])
        }
    except (httpx.HTTPError, ValueError, TypeError, AttributeError):
        # Readiness is fail-closed; malformed or unavailable worker metadata
        # must not advertise launch authority.
        pass
    return endpoint_ready, enforced_network_refs if backend_ready else set()


def _policy_images_ready(policy: Any) -> bool:
    values = []
    for value, variable in (
        (policy.server_image_ref, "OMNIGENT_IMAGE_REF"),
        (policy.host_image_ref, "OMNIGENT_HOST_IMAGE_REF"),
    ):
        values.append(
            os.getenv(variable, "").strip()
            if value.startswith("bootstrap://")
            else value
        )
    placeholder_digest = "0" * 64
    return all(
        _DIGEST_IMAGE.fullmatch(value)
        and not value.endswith(f"@sha256:{placeholder_digest}")
        for value in values
    )


def _profile_gate_codes(readiness: dict[str, Any]) -> list[str]:
    codes: list[str] = []
    for check in readiness.get("checks", []):
        if check.get("status") != "error":
            continue
        check_id = str(check.get("id") or "")
        code = (
            "profile_reconnect_required"
            if check_id in {"auth_state", "oauth_volume", "secret_refs"}
            else "profile_capacity_unavailable"
            if check_id in {"concurrency", "cooldown"}
            else "profile_validation_required"
        )
        if code not in codes:
            codes.append(code)
    return codes


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
    endpoint_ready, enforced_network_refs = await _live_deployment_readiness()
    if (
        config.enabled
        and config.host_protocol_mode != HOST_PROTOCOL_MODE_EMBEDDED
        and resolved_server_url()
        and not endpoint_ready
    ):
        deployment_reasons.append(_reason("bridge_endpoint_unavailable"))
    if config.enabled and config.host_protocol_mode == HOST_PROTOCOL_MODE_EMBEDDED:
        try:
            auth = await host_auth_readiness(profile=await _active_host_auth_profile())
        except HostAuthProfileError:
            auth = {"ready": False}
        if not auth.get("ready"):
            deployment_reasons.append(_reason("host_auth_unavailable"))

    rows = list(
        (
            await session.execute(
                select(ManagedAgentProviderProfile).where(
                    ManagedAgentProviderProfile.runtime_id == "codex_cli"
                )
            )
        )
        .scalars()
        .all()
    )
    rows = [row for row in rows if _can_view_profile(row, current_user)]
    secret_results = _secret_ref_results_for_rows(rows)
    secret_statuses = await _managed_secret_statuses_for_rows(
        session, rows, secret_ref_results=secret_results
    )
    active_slots = list(
        (
            await session.execute(
                select(ProviderProfileSlotLease).where(
                    ProviderProfileSlotLease.runtime_id == "codex_cli"
                )
            )
        )
        .scalars()
        .all()
    )
    active_slot_counts: dict[str, int] = {}
    now = datetime.now(UTC)
    for slot in active_slots:
        if slot.expires_at is None or slot.expires_at > now:
            active_slot_counts[slot.profile_id] = (
                active_slot_counts.get(slot.profile_id, 0) + 1
            )

    eligible: list[EligibleProviderProfile] = []
    ineligible: list[IneligibleProviderProfile] = []
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
        label = str(
            redact_sensitive_payload(
                row.account_label or row.provider_label or row.profile_id
            )
        )
        if label.lower().startswith(("ghp_", "github_pat_", "aiza", "akia")):
            label = "[REDACTED]"
        busy = active_slot_counts.get(row.profile_id, 0) >= (row.max_parallel_runs or 1)
        queue_when_busy = (
            getattr(row.rate_limit_policy, "value", row.rate_limit_policy) == "queue"
        )
        compatible = (
            credential_source == "oauth_volume" and materialization == "oauth_home"
        )
        if compatible and readiness["launch_ready"] and (not busy or queue_when_busy):
            eligible.append(
                EligibleProviderProfile(
                    profileId=row.profile_id,
                    label=label,
                    providerId=row.provider_id,
                    busy=busy,
                    queueWhenBusy=queue_when_busy,
                )
            )
        elif compatible:
            codes = _profile_gate_codes(readiness)
            if (
                busy
                and not queue_when_busy
                and "profile_capacity_unavailable" not in codes
            ):
                codes.append("profile_capacity_unavailable")
            ineligible.append(
                IneligibleProviderProfile(
                    profileId=row.profile_id,
                    label=label,
                    gateReasons=[
                        _reason(code)
                        for code in codes or ["profile_validation_required"]
                    ],
                )
            )

    bindings = list(
        (await session.execute(select(OmnigentOAuthHostBindingRecord))).scalars().all()
    )
    host_leases = list(
        (await session.execute(select(OmnigentOAuthHostLeaseRecord))).scalars().all()
    )
    static_profile_ids = {
        binding.provider_profile_id for binding in bindings if binding.static_host_id
    }
    static_ready = any(
        lease.provider_profile_id in static_profile_ids
        and lease.status in {"ready", "assigned"}
        and lease.expires_at > now
        and lease.disconnected_at is None
        and (lease.host_readiness or lease.status) in {"ready", "assigned"}
        for lease in host_leases
    )
    try:
        backend_configured = resolve_container_backend_settings().enabled
    except ContainerBackendConfigError:
        backend_configured = False
    backend_ready = backend_configured and bool(enforced_network_refs)

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
            if not _policy_images_ready(policy):
                policy_reasons.append(_reason("immutable_image_unavailable"))
            if (
                not policy.enforced_egress
                or not policy.network_ref
                or policy.network_ref not in enforced_network_refs
            ):
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
        ineligibleProviderProfiles=ineligible,
        hostModes=sorted(set(available_modes)),
        gateReasons=top_reasons,
    )
