"""Safe, versioned Omnigent OAuth readiness projection for Workflow Create.

MoonLadderStudios/MoonMind#3451.  This boundary deliberately returns product
selection data, never launch authority or provider/host secret material.
"""

from __future__ import annotations

import json
import os
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import httpx

from fastapi import APIRouter, Depends, Request
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
    OmnigentPolicy,
    OmnigentPolicyVersion,
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
from moonmind.omnigent.conformance import (
    ConformanceContractError,
    validate_acceptance_manifest,
)
from moonmind.omnigent.settings import build_omnigent_gate, resolved_server_url
from moonmind.omnigent.cutover import effective_phase
from moonmind.omnigent.remediation_matrix import load_remediation_release_status
from moonmind.omnigent.control_plane import metrics as control_plane_metrics
from moonmind.omnigent.control_plane.readiness import (
    ReadinessInputs,
    evaluate_admission_readiness,
)
from moonmind.utils.logging import redact_sensitive_payload

from .omnigent_bridge import (
    _active_host_auth_profile,
    _compatibility_diagnostics,
    _resolve_embedded_evidence,
    get_bridge_config,
)

router = APIRouter(prefix="/api/omnigent", tags=["Omnigent Catalog"])

_SCHEMA_VERSION = "moonmind.omnigent-codex-readiness.v2"
_DIGEST_IMAGE = re.compile(r"^[^\s@]+@sha256:[0-9a-f]{64}$")
_ACCEPTANCE_ROWS = (
    "static_profile_bound",
    "static_restart_replay",
    "on_demand_policy_selected",
    "repository_read_analysis",
    "repository_mutation_publication",
    "failed_credential_readiness_admission",
    "failed_host_registration_readiness",
    "active_cancellation_interruption",
    "partial_start_cleanup_janitor",
)


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
    runtime_id: Literal["codex_cli", "claude_code"] = Field(alias="runtimeId")
    busy: bool = False
    queue_when_busy: bool = Field(alias="queueWhenBusy")


class IneligibleProviderProfile(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    profile_id: str = Field(alias="profileId")
    label: str
    runtime_id: Literal["codex_cli", "claude_code"] = Field(alias="runtimeId")
    gate_reasons: list[GateReason] = Field(alias="gateReasons")


class LaunchPolicyReadiness(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    ref: str
    display_name: str = Field(alias="displayName")
    host_mode: Literal["static_compose", "on_demand_docker"] = Field(
        alias="hostMode"
    )
    is_default: bool = Field(False, alias="isDefault")


class ExecutionProfileReadiness(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    ref: str
    display_name: str = Field(alias="displayName")
    available: bool
    launch_policies: list[LaunchPolicyReadiness] = Field(alias="launchPolicies")
    gate_reasons: list[GateReason] = Field(alias="gateReasons")


class OmnigentCodexCatalogReadiness(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    schema_version: Literal["moonmind.omnigent-codex-readiness.v2"] = Field(
        _SCHEMA_VERSION, alias="schemaVersion"
    )
    runtime_id: Literal["omnigent"] = Field("omnigent", alias="runtimeId")
    display_name: Literal["Codex via Omnigent"] = Field(
        "Codex via Omnigent", alias="displayName"
    )
    agent_kind: Literal["external"] = Field("external", alias="agentKind")
    agent_id: Literal["omnigent"] = Field("omnigent", alias="agentId")
    harness: Literal["codex-native"] = "codex-native"
    harnesses: list[Literal["codex-native", "claude-native"]] = Field(
        default_factory=lambda: ["codex-native", "claude-native"]
    )
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
    support_gate_reasons: list[GateReason] = Field(alias="supportGateReasons")
    compatibility_diagnostics: dict[str, Any] = Field(alias="compatibilityDiagnostics")
    cutover: dict[str, Any]
    remediation_release: dict[str, Any] = Field(alias="remediationRelease")
    admission_readiness: dict[str, Any] = Field(alias="admissionReadiness")


_REASONS: dict[str, tuple[str, str]] = {
    "bridge_disabled": ("Enable the Omnigent bridge in deployment settings.", "/settings#omnigent"),
    "bridge_conformance_gated": ("Complete Omnigent bridge conformance checks.", "/settings#omnigent"),
    "bridge_endpoint_unavailable": ("Configure the selected Omnigent endpoint.", "/settings#omnigent"),
    "bridge_endpoint_not_ready": (
        "The configured Omnigent endpoint is starting or temporarily unavailable. "
        "MoonMind will retry automatically.",
        "/settings#omnigent",
    ),
    "rollout_gate_disabled": ("Enable the Omnigent runtime rollout gate.", "/settings#omnigent"),
    "host_auth_unavailable": ("Configure or rotate Omnigent bridge credentials.", "/settings#omnigent"),
    "no_eligible_codex_oauth_profile": ("Connect and validate a compatible OAuth Provider Profile for the selected execution target.", "/settings#provider-profiles"),
    "execution_profile_unavailable": ("Enable a compatible Omnigent execution profile.", "/settings#omnigent"),
    "on_demand_backend_unavailable": ("Enable the trusted container backend and worker route.", "/settings#system"),
    "static_host_not_ready": ("Start and validate the selected static Omnigent host.", "/settings#omnigent"),
    "immutable_image_unavailable": ("Retry stock Omnigent image acquisition or configure explicit server and host image digests.", "/settings#omnigent"),
    "network_policy_unavailable": ("Configure the required enforced egress policy.", "/settings#omnigent"),
    "acceptance_evidence_unavailable": ("Publish a current #3508 browser acceptance matrix for this source commit.", "/settings#omnigent"),
    "omnigent_admission_readiness_failed": (
        "Restore the blocked Omnigent runtime capability or refresh its protected evidence.",
        "/settings#omnigent",
    ),
    "workspace_resolver_unavailable": ("Restore the workflow workspace resolver.", "/settings#system"),
    "profile_reconnect_required": ("Reconnect this OAuth Provider Profile.", "/settings#provider-profiles"),
    "profile_validation_required": ("Validate this OAuth Provider Profile.", "/settings#provider-profiles"),
    "profile_capacity_unavailable": ("Wait for Provider Profile capacity or enable queued execution.", "/settings#provider-profiles"),
}


def _reason(code: str) -> GateReason:
    message, href = _REASONS[code]
    return GateReason(code=code, message=message, remediationHref=href)


def _valid_server_url(value: str) -> bool:
    try:
        url = httpx.URL(value)
    except (TypeError, ValueError):
        return False
    return url.scheme in {"http", "https"} and bool(url.host)


def _deployment_reasons(config: Any, bridge: dict[str, Any]) -> list[GateReason]:
    reasons: list[GateReason] = []
    if not config.enabled:
        return [_reason("bridge_disabled")]
    if bridge.get("conformanceState") != "ready":
        reasons.append(_reason("bridge_conformance_gated"))
    runtime_gate = build_omnigent_gate()
    if not runtime_gate.enabled:
        reasons.append(_reason("rollout_gate_disabled"))
    if config.host_protocol_mode != HOST_PROTOCOL_MODE_EMBEDDED and not _valid_server_url(
        resolved_server_url()
    ):
        reasons.append(_reason("bridge_endpoint_unavailable"))
    if os.getenv("MOONMIND_WORKSPACE_RESOLVER_ENABLED", "true").lower() not in {
        "1", "true", "yes", "on"
    }:
        reasons.append(_reason("workspace_resolver_unavailable"))
    return reasons


def _support_reasons(
    *, acceptance_canary: bool = False
) -> tuple[list[GateReason], timedelta | None]:
    """Return release-support failures and bounded protected-evidence age."""

    if acceptance_canary:
        return [], timedelta(0)
    manifest_path = os.getenv("MOONMIND_OMNIGENT_ACCEPTANCE_MANIFEST", "").strip()
    source_commit = os.getenv("MOONMIND_SOURCE_COMMIT", "").strip()
    try:
        if not manifest_path or not source_commit:
            raise ConformanceContractError("acceptance evidence is not configured")
        manifest_file = Path(manifest_path)
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
        if not isinstance(manifest, dict):
            raise ConformanceContractError("acceptance manifest must be an object")
        validate_acceptance_manifest(
            manifest,
            expected_commit=source_commit,
            required_rows=_ACCEPTANCE_ROWS,
            evidence_root=manifest_file.parent,
        )
    except (OSError, json.JSONDecodeError, ConformanceContractError):
        return [_reason("acceptance_evidence_unavailable")], None
    try:
        generated_at = datetime.fromisoformat(
            str(manifest["generatedAt"]).replace("Z", "+00:00")
        )
        evidence_age = datetime.now(UTC) - generated_at
    except (KeyError, TypeError, ValueError):
        # The validator owns this field in production. A non-authoritative test
        # double may omit it; validated evidence is still known-current.
        evidence_age = timedelta(0)
    return [], evidence_age


def _reconciler_generation_available() -> bool:
    """Confirm the canonical durable session supervisor is in the worker fleet."""

    from moonmind.workflows.temporal.workflow_registry import (
        STATIC_WORKFLOW_REGISTRATIONS,
    )

    return any(
        registration.class_name == "MoonMindOmnigentSessionWorkflow"
        for registration in STATIC_WORKFLOW_REGISTRATIONS
    )


def _websocket_runtime_available() -> bool:
    """Confirm the binding-scoped WebSocket route is registered in this build."""

    from .omnigent_bridge import workflow_chat_router

    return any(
        getattr(route, "path", "")
        == "/{chat_binding_id}/omnigent/{omnigent_path:path}"
        for route in workflow_chat_router.routes
    )


@dataclass(frozen=True, slots=True)
class LiveDeploymentReadiness:
    endpoint_ready: bool = False
    backend_ready: bool = False
    enforced_network_refs: frozenset[str] = frozenset()
    enforced_egress_profile_refs: frozenset[str] = frozenset()


async def _live_deployment_readiness() -> LiveDeploymentReadiness:
    """Read bounded health projections from the services that own readiness."""

    endpoint = resolved_server_url()
    endpoint_ready = False
    if _valid_server_url(endpoint):
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
    enforced_egress_profile_refs: set[str] = set()
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
        enforced_egress_profile_refs = {
            str(value) for value in backend.get("enforcedEgressProfileRefs", [])
        }
    except (httpx.HTTPError, ValueError, TypeError, AttributeError):
        # Readiness is fail-closed; malformed or unavailable worker metadata
        # must not advertise launch authority.
        pass
    return LiveDeploymentReadiness(
        endpoint_ready=endpoint_ready,
        backend_ready=backend_ready,
        enforced_network_refs=frozenset(enforced_network_refs if backend_ready else ()),
        enforced_egress_profile_refs=frozenset(
            enforced_egress_profile_refs if backend_ready else ()
        ),
    )


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
    request: Request,
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
    configured_canary_token = os.getenv(
        "MOONMIND_OMNIGENT_ACCEPTANCE_CANARY_TOKEN", ""
    ).strip()
    supplied_canary_token = request.headers.get(
        "X-MoonMind-Acceptance-Canary", ""
    ).strip()
    acceptance_canary = bool(
        configured_canary_token
        and supplied_canary_token
        and secrets.compare_digest(configured_canary_token, supplied_canary_token)
    )
    deployment_reasons = _deployment_reasons(config, bridge)
    support_reasons, protected_evidence_age = _support_reasons(
        acceptance_canary=acceptance_canary
    )
    live_readiness = await _live_deployment_readiness()
    if (
        config.enabled
        and config.host_protocol_mode != HOST_PROTOCOL_MODE_EMBEDDED
        and _valid_server_url(resolved_server_url())
        and not live_readiness.endpoint_ready
    ):
        deployment_reasons.append(_reason("bridge_endpoint_not_ready"))
    auth: dict[str, Any] | None = None
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
                    ManagedAgentProviderProfile.runtime_id.in_(
                        ("codex_cli", "claude_code")
                    )
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
                    ProviderProfileSlotLease.runtime_id.in_(
                        ("codex_cli", "claude_code")
                    )
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
    eligible_by_runtime: dict[str, int] = {}
    ineligible: list[IneligibleProviderProfile] = []
    for row in rows:
        raw_runtime_id = getattr(row, "runtime_id", "codex_cli")
        runtime_id = str(getattr(raw_runtime_id, "value", raw_runtime_id))
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
            eligible_by_runtime[runtime_id] = eligible_by_runtime.get(runtime_id, 0) + 1
            eligible.append(
                EligibleProviderProfile(
                    profileId=row.profile_id,
                    label=label,
                    providerId=row.provider_id,
                    runtimeId=runtime_id,
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
                    runtimeId=runtime_id,
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
    profile_runtime_by_id = {
        row.profile_id: str(getattr(row.runtime_id, "value", row.runtime_id))
        for row in rows
    }
    static_ready_runtimes = {
        profile_runtime_by_id.get(lease.provider_profile_id)
        for lease in host_leases
        if lease.provider_profile_id in static_profile_ids
        and lease.status in {"ready", "assigned"}
        and lease.expires_at > now
        and lease.disconnected_at is None
        and (lease.host_readiness or lease.status) in {"ready", "assigned"}
    }
    try:
        backend_configured = resolve_container_backend_settings().enabled
    except ContainerBackendConfigError:
        backend_configured = False
    backend_ready = backend_configured and live_readiness.backend_ready
    persisted_policies = list((await session.execute(
        select(OmnigentPolicy, OmnigentPolicyVersion)
        .join(
            OmnigentPolicyVersion,
            OmnigentPolicyVersion.policy_id == OmnigentPolicy.policy_id,
        )
        .where(OmnigentPolicyVersion.state == "active")
    )).all())

    profile_views: list[ExecutionProfileReadiness] = []
    available_modes: list[str] = []
    for profile in PROFILES.values():
        profile_reasons = list(deployment_reasons)
        provider_slug = (
            "claude" if profile.provider_runtime == "claude_code" else "codex"
        )
        default_policy = POLICIES.get(profile.default_policy_ref)
        default_policy_id = default_policy.policy_id if default_policy else None
        launch_policies_by_ref: dict[str, LaunchPolicyReadiness] = {}
        persisted_default_ref: str | None = None
        unavailable_policy_reasons: list[GateReason] = []
        for policy in POLICIES.values():
            if not policy.policy_id.startswith(provider_slug + "-"):
                continue
            policy_reasons: list[GateReason] = []
            if not policy.enabled:
                policy_reasons.append(_reason("execution_profile_unavailable"))
            if not _policy_images_ready(policy):
                policy_reasons.append(_reason("immutable_image_unavailable"))
            if (
                not policy.enforced_egress
                or not policy.network_ref
                or policy.network_ref not in live_readiness.enforced_network_refs
            ):
                policy_reasons.append(_reason("network_policy_unavailable"))
            if policy.host_mode == "on_demand_docker" and not backend_ready:
                policy_reasons.append(_reason("on_demand_backend_unavailable"))
            if (
                policy.host_mode == "static_compose"
                and profile.provider_runtime not in static_ready_runtimes
            ):
                policy_reasons.append(_reason("static_host_not_ready"))
            if config.host_protocol_mode == HOST_PROTOCOL_MODE_EMBEDDED:
                mode_readiness = config.readiness(
                    evidence_validation=evidence,
                    host_mode=policy.host_mode,
                )
                if mode_readiness["conformanceState"] != "ready":
                    policy_reasons.append(_reason("bridge_conformance_gated"))
            if not policy_reasons:
                launch_policies_by_ref[policy.ref] = LaunchPolicyReadiness(
                    ref=policy.ref,
                    displayName=(
                        "On-demand Docker"
                        if policy.host_mode == "on_demand_docker"
                        else "Static Compose"
                    ),
                    hostMode=policy.host_mode,
                    isDefault=False,
                )
                available_modes.append(policy.host_mode)
            unavailable_policy_reasons.extend(policy_reasons)
        for identity, version in persisted_policies:
            if not (
                identity.visibility == "deployment"
                or identity.owner_user_id == getattr(current_user, "id", None)
            ):
                continue
            document = version.document_json
            if document.get("execution", {}).get("profileRef") != profile.ref:
                continue
            host = document.get("host", {})
            network = document.get("network", {})
            policy_reasons = []
            if not version.validation_json.get("valid"):
                policy_reasons.append(_reason("execution_profile_unavailable"))
            if not all(_DIGEST_IMAGE.fullmatch(str(host.get(field) or "")) for field in ("serverImageRef", "hostImageRef")):
                policy_reasons.append(_reason("immutable_image_unavailable"))
            if (
                network.get("attachmentRef")
                not in live_readiness.enforced_network_refs
                or network.get("egressProfileRef")
                not in live_readiness.enforced_egress_profile_refs
            ):
                policy_reasons.append(_reason("network_policy_unavailable"))
            host_mode = str(host.get("mode") or "")
            if host_mode == "on_demand_docker" and not backend_ready:
                policy_reasons.append(_reason("on_demand_backend_unavailable"))
            if (
                host_mode == "static_compose"
                and profile.provider_runtime not in static_ready_runtimes
            ):
                policy_reasons.append(_reason("static_host_not_ready"))
            if not policy_reasons:
                persisted_ref = f"{identity.policy_id}@{version.version}"
                launch_policies_by_ref[persisted_ref] = LaunchPolicyReadiness(
                    ref=persisted_ref,
                    displayName=(
                        str(getattr(identity, "name", "") or "").strip()
                        or (
                            "On-demand Docker"
                            if host_mode == "on_demand_docker"
                            else "Static Compose"
                        )
                    ),
                    hostMode=host_mode,
                    isDefault=False,
                )
                if (
                    identity.policy_id == default_policy_id
                    and version.version == identity.default_version
                ):
                    persisted_default_ref = persisted_ref
                available_modes.append(host_mode)
            unavailable_policy_reasons.extend(policy_reasons)
        preferred_default_ref = (
            persisted_default_ref
            if persisted_default_ref in launch_policies_by_ref
            else profile.default_policy_ref
        )
        launch_policies = sorted(
            (
                policy.model_copy(
                    update={"is_default": policy.ref == preferred_default_ref}
                )
                for policy in launch_policies_by_ref.values()
            ),
            key=lambda item: (not item.is_default, item.display_name, item.ref),
        )
        if not launch_policies:
            for reason in unavailable_policy_reasons:
                if reason.code not in {existing.code for existing in profile_reasons}:
                    profile_reasons.append(reason)
        if not eligible_by_runtime.get(profile.provider_runtime):
            profile_reasons.append(_reason("no_eligible_codex_oauth_profile"))
        profile_views.append(ExecutionProfileReadiness(
            ref=profile.ref,
            displayName=profile.display_name,
            available=(
                profile.enabled
                and bool(launch_policies)
                and bool(eligible_by_runtime.get(profile.provider_runtime))
                and not deployment_reasons
            ),
            launchPolicies=launch_policies,
            gateReasons=profile_reasons,
        ))

    available_before_admission = any(item.available for item in profile_views)
    bridge_ready = bridge.get("conformanceState") == "ready"
    admission = evaluate_admission_readiness(
        ReadinessInputs(
            reconciler_generation_ready=_reconciler_generation_available(),
            schema_compatible=True,
            provider_snapshot_ready=bridge_ready,
            event_transport_ready=bridge_ready,
            server_build_ready=bool(available_modes),
            ui_build_ready=bridge_ready,
            host_build_ready=bool(available_modes),
            websocket_available=_websocket_runtime_available(),
            worker_backend_ready=live_readiness.backend_ready,
            container_backend_ready=backend_ready,
            observation_age=(
                timedelta(0)
                if live_readiness.endpoint_ready or bridge_ready
                else None
            ),
            janitor_healthy=live_readiness.backend_ready,
            exact_image_conformant=bool(available_modes),
            protected_live_evidence_age=protected_evidence_age,
        )
    )
    for capability in admission.capabilities:
        try:
            control_plane_metrics.increment(
                control_plane_metrics.RUNTIME_CAPABILITY_READINESS,
                capability=capability.capability.value,
                readiness=capability.state.value,
            )
        except Exception:
            # Readiness authority is the evaluated document, never telemetry.
            pass
    if protected_evidence_age is not None:
        try:
            control_plane_metrics.observe(
                control_plane_metrics.PROTECTED_LIVE_EVIDENCE_AGE,
                max(0.0, protected_evidence_age.total_seconds()),
            )
        except Exception:
            pass
    if not admission.admit_new:
        gate = _reason("omnigent_admission_readiness_failed")
        profile_views = [
            item.model_copy(
                update={
                    "available": False,
                    "gate_reasons": [*item.gate_reasons, gate],
                }
            )
            for item in profile_views
        ]
    available = available_before_admission and admission.admit_new
    top_reasons = (
        []
        if available
        else (
            profile_views[0].gate_reasons
            if profile_views
            else [_reason("execution_profile_unavailable")]
        )
    )
    # The catalog readiness projection does not depend on the immutable policy
    # authority (that lives on the bridge /readiness surface), so it is not
    # resolved here: doing so would add a fail-closed dependency that returns
    # 503 in a proxy-first deployment without a seeded default policy.
    diagnostics = _compatibility_diagnostics(
        config=config, readiness=bridge, auth=auth, policy_authority=None
    )
    diagnostics["capabilitySummary"] = sorted({
        str(capability)
        for lease in host_leases
        for field in ("harnesses", "capabilities")
        for capability in (
            (getattr(lease, "host_capabilities_json", None) or {}).get(field, [])
        )
    })
    cutover_status = effective_phase()
    remediation_release = load_remediation_release_status()
    return OmnigentCodexCatalogReadiness(
        available=available,
        defaultExecutionProfileRef=next(iter(PROFILES)),
        executionProfiles=profile_views,
        eligibleProviderProfiles=eligible,
        ineligibleProviderProfiles=ineligible,
        hostModes=sorted(set(available_modes)),
        gateReasons=top_reasons,
        supportGateReasons=support_reasons,
        compatibilityDiagnostics=diagnostics,
        cutover=cutover_status.as_dict(),
        remediationRelease=remediation_release.as_dict(),
        admissionReadiness=admission.to_dict(),
    )
