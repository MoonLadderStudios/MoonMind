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
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy import func, select
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
    OmnigentBridgeSession,
    OmnigentPolicy,
    OmnigentPolicyVersion,
    OmnigentOAuthHostBindingRecord,
    OmnigentOAuthHostLeaseRecord,
    OmnigentObservation,
    ProviderProfileSlotLease,
    OmnigentSession,
    User,
)
from moonmind.config.container_backend_settings import (
    ContainerBackendConfigError,
    resolve_container_backend_settings,
)
from moonmind.config.settings import settings
from moonmind.omnigent.bridge_config import HOST_PROTOCOL_MODE_EMBEDDED
from moonmind.omnigent.control_plane.identities import (
    EGRESS_CLEANUP_AUTHORITY_KEY,
    EGRESS_CLEANUP_AUTHORITY_VERSION,
)
from moonmind.omnigent.execution_profiles import POLICIES, PROFILES
from moonmind.omnigent.host_auth_profile import HostAuthProfileError, host_auth_readiness
from moonmind.omnigent.conformance import (
    ConformanceContractError,
    validate_acceptance_manifest,
)
from moonmind.omnigent.exact_artifact_conformance import (
    ExactArtifactConformanceError,
    assert_exact_artifact_evidence,
)
from moonmind.omnigent.live_verification_health import (
    LiveVerificationHealthError,
    assert_live_health_projection,
)
from moonmind.omnigent.settings import build_omnigent_gate, resolved_server_url
from moonmind.omnigent.cutover import effective_phase
from moonmind.omnigent.remediation_matrix import load_remediation_release_status
from moonmind.omnigent.control_plane import metrics as control_plane_metrics
from moonmind.omnigent.control_plane.readiness import (
    ReadinessInputs,
    evaluate_admission_readiness,
)
from moonmind.omnigent.control_plane.records import SUPPORTED_SCHEMA_VERSIONS
from moonmind.utils.logging import redact_sensitive_payload

from .omnigent_bridge import (
    _active_host_auth_profile,
    _compatibility_diagnostics,
    _resolve_embedded_evidence,
    get_bridge_config,
)

router = APIRouter(prefix="/api/omnigent", tags=["Omnigent Catalog"])

_SCHEMA_VERSION = "moonmind.omnigent-codex-readiness.v2"
_BOOTSTRAP_EVIDENCE_SCHEMA_VERSION = (
    "moonmind.omnigent.catalog-bootstrap-evidence/v1"
)
_BOOTSTRAP_EVIDENCE_HEADER = "X-MoonMind-Acceptance-Evidence"
_MAX_BOOTSTRAP_EVIDENCE_BYTES = 4096
_MAX_BOOTSTRAP_EVIDENCE_AGE = timedelta(minutes=5)
_MAX_BUILD_OBSERVATION_AGE = timedelta(hours=24)
_DIGEST_IMAGE = re.compile(r"^[^\s@]+@sha256:[0-9a-f]{64}$")
_SAFE_BUILD_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,254}$")
_SNAPSHOT_OBSERVATION_TYPES = ("snapshot", "provider_snapshot")
_EVENT_OBSERVATION_TYPES = (
    "event",
    "event_frontier",
    "event_batch",
    "provider_event",
    "provider_event_batch",
)
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
    runtime_id: Literal["codex_cli", "claude_code", "opencode"] = Field(alias="runtimeId")
    busy: bool = False
    queue_when_busy: bool = Field(alias="queueWhenBusy")


class IneligibleProviderProfile(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    profile_id: str = Field(alias="profileId")
    label: str
    runtime_id: Literal["codex_cli", "claude_code", "opencode"] = Field(alias="runtimeId")
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


class AcceptanceBootstrapEvidence(BaseModel):
    """Bounded live observations supplied only by the protected canary."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    schema_version: Literal[
        "moonmind.omnigent.catalog-bootstrap-evidence/v1"
    ] = Field(_BOOTSTRAP_EVIDENCE_SCHEMA_VERSION, alias="schemaVersion")
    observed_at: datetime = Field(alias="observedAt")
    provider_snapshot_observed: bool = Field(alias="providerSnapshotObserved")
    event_transport_observed: bool = Field(alias="eventTransportObserved")
    server_image_ref_observed: str = Field(
        min_length=1, max_length=255, alias="serverImageRefObserved"
    )
    host_image_ref_observed: str = Field(
        min_length=1, max_length=255, alias="hostImageRefObserved"
    )
    ui_build_ref_observed: str = Field(
        min_length=1, max_length=255, alias="uiBuildRefObserved"
    )

    @field_validator(
        "provider_snapshot_observed", "event_transport_observed", mode="before"
    )
    @classmethod
    def _require_observed_true(cls, value: object) -> object:
        if value is not True:
            raise ValueError("bootstrap capability must be directly observed")
        return value

    @field_validator("server_image_ref_observed", "host_image_ref_observed")
    @classmethod
    def _require_immutable_image(cls, value: str) -> str:
        if not _DIGEST_IMAGE.fullmatch(value) or value.endswith(
            "@sha256:" + "0" * 64
        ):
            raise ValueError("bootstrap image observation must be immutable")
        return value

    @field_validator("ui_build_ref_observed")
    @classmethod
    def _require_safe_ui_build_ref(cls, value: str) -> str:
        if not _SAFE_BUILD_REF.fullmatch(value):
            raise ValueError("bootstrap UI build observation is invalid")
        return value

    @field_validator("observed_at")
    @classmethod
    def _require_aware_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("bootstrap observation timestamp must include a timezone")
        return value.astimezone(UTC)


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
    "exact_artifact_evidence_unavailable": ("Publish current Tier-1 exact deployable-artifact conformance for this source commit.", "/settings#omnigent"),
    "live_verification_stale": ("Restore fresh protected-live verification evidence before rollout.", "/settings#omnigent"),
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


@dataclass(frozen=True, slots=True)
class SupportEvidence:
    reasons: tuple[GateReason, ...] = ()
    age: timedelta | None = None
    server_digest: str | None = None
    host_digest: str | None = None
    ui_source_commit: str | None = None


@dataclass(frozen=True, slots=True)
class ObservedDeploymentManifest:
    """Actual image/build identities captured by a production owner."""

    observed_at: datetime
    server_image_ref: str
    host_image_ref: str
    ui_build_ref: str


def _evidence_age(observed_at: datetime, *, now: datetime) -> timedelta:
    if observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=UTC)
    return now - observed_at.astimezone(UTC)


def _parse_acceptance_bootstrap_evidence(
    request: Request,
    *,
    acceptance_canary: bool,
    now: datetime,
) -> AcceptanceBootstrapEvidence | None:
    """Resolve fresh bootstrap evidence after authenticating canary authority.

    The bounded header exists only to break the empty-deployment cycle for the
    protected first-run journey. It is never read for an unauthenticated
    request, and stale/future/malformed evidence cannot grant readiness.
    """

    if not acceptance_canary:
        return None
    raw = request.headers.get(_BOOTSTRAP_EVIDENCE_HEADER, "")
    if not raw or len(raw.encode("utf-8")) > _MAX_BOOTSTRAP_EVIDENCE_BYTES:
        return None
    try:
        evidence = AcceptanceBootstrapEvidence.model_validate_json(raw)
    except ValidationError:
        return None
    age = _evidence_age(evidence.observed_at, now=now)
    if age < timedelta(0) or age > _MAX_BOOTSTRAP_EVIDENCE_AGE:
        return None
    return evidence


def _support_evidence(
    *,
    bootstrap_evidence: AcceptanceBootstrapEvidence | None = None,
    now: datetime | None = None,
) -> SupportEvidence:
    """Return release-support failures and bounded protected-evidence age.

    Publication/readiness (#3508 / #3642) is only *supported* when the current
    source commit is proven three ways, each fail-closed and each surfaced as a
    distinct support reason rather than a hard blocker: the #3508 browser
    acceptance matrix, the Tier-1 exact deployable-artifact conformance
    (MoonLadderStudios/MoonMind#3710 AC10), and a fresh protected-live
    verification projection. A code-only fix therefore cannot advertise
    deployed acceptance/compatibility status until the exact deployable images
    and the protected provider tier prove it.
    """

    if bootstrap_evidence is not None:
        # The authenticated canary is itself producing the protected acceptance
        # manifest. Its fresh, directly observed bounded manifest is the only
        # supported bootstrap source before that durable manifest exists.
        return SupportEvidence(
            age=_evidence_age(
                bootstrap_evidence.observed_at,
                now=now or datetime.now(UTC),
            ),
            server_digest=bootstrap_evidence.server_image_ref_observed.rsplit(
                "@", 1
            )[1],
            host_digest=bootstrap_evidence.host_image_ref_observed.rsplit("@", 1)[1],
            ui_source_commit=bootstrap_evidence.ui_build_ref_observed,
        )
    source_commit = os.getenv("MOONMIND_SOURCE_COMMIT", "").strip()
    reasons: list[GateReason] = []
    age: timedelta | None = None
    server_digest: str | None = None
    host_digest: str | None = None
    ui_source_commit: str | None = None

    # --- #3508 browser acceptance matrix -------------------------------------
    manifest_path = os.getenv("MOONMIND_OMNIGENT_ACCEPTANCE_MANIFEST", "").strip()
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
        reasons.append(_reason("acceptance_evidence_unavailable"))
    else:
        try:
            generated_at = datetime.fromisoformat(
                str(manifest["generatedAt"]).replace("Z", "+00:00")
            )
            if generated_at.tzinfo is None:
                generated_at = generated_at.replace(tzinfo=UTC)
            evidence_age = (now or datetime.now(UTC)) - generated_at
        except (KeyError, TypeError, ValueError):
            # The validator owns this field in production. A non-authoritative test
            # double may omit it; validated evidence is still known-current.
            evidence_age = timedelta(0)
        images = (
            manifest.get("images")
            if isinstance(manifest.get("images"), dict)
            else {}
        )
        age = evidence_age
        server_digest = str(images.get("serverDigest") or "") or None
        host_digest = str(images.get("hostDigest") or "") or None
        ui_source_commit = str(manifest.get("sourceCommit") or "") or None

    # --- Tier-1 exact deployable-artifact conformance (#3710 AC10) -----------
    exact_path = os.getenv("MOONMIND_OMNIGENT_EXACT_ARTIFACT_EVIDENCE", "").strip()
    try:
        if not exact_path or not source_commit:
            raise ExactArtifactConformanceError("exact-artifact evidence is not configured")
        evidence = json.loads(Path(exact_path).read_text(encoding="utf-8"))
        if not isinstance(evidence, dict):
            raise ExactArtifactConformanceError("exact-artifact evidence must be an object")
        assert_exact_artifact_evidence(evidence, expected_commit=source_commit)
    except (OSError, json.JSONDecodeError, ExactArtifactConformanceError):
        reasons.append(_reason("exact_artifact_evidence_unavailable"))

    # --- Fresh protected-live verification projection ------------------------
    # Revalidated at *consumption*, not just at publication: the versioned
    # schema, the ready verdict, the deployed commit, the projection's own
    # freshness window, and the acceptance expiry it inherits. A once-ready file
    # must stop being accepted when its manifest expires or scheduled
    # monitoring stops publishing.
    projection_path = os.getenv("MOONMIND_OMNIGENT_LIVE_HEALTH_PROJECTION", "").strip()
    try:
        if not projection_path or not source_commit:
            raise LiveVerificationHealthError(
                "live-verification projection is not configured"
            )
        projection = json.loads(Path(projection_path).read_text(encoding="utf-8"))
        if not isinstance(projection, dict):
            raise LiveVerificationHealthError(
                "live-verification projection must be an object"
            )
        assert_live_health_projection(projection, expected_commit=source_commit)
    except (
        OSError,
        json.JSONDecodeError,
        LiveVerificationHealthError,
        ConformanceContractError,
    ):
        reasons.append(_reason("live_verification_stale"))

    return SupportEvidence(
        reasons=tuple(reasons),
        age=age,
        server_digest=server_digest,
        host_digest=host_digest,
        ui_source_commit=ui_source_commit,
    )


def _support_reasons(*, acceptance_canary: bool = False) -> list[GateReason]:
    """Compatibility shim for tests that still call the old helper.

    The canonical helper is now :func:`_support_evidence`, which returns a
    :class:`SupportEvidence` with age and digests. This shim preserves the
    previous list-returning contract for existing tests.
    """

    if acceptance_canary:
        return []
    return list(_support_evidence(now=datetime.now(UTC)).reasons)


def _observed_deployment_manifest(
    rows: list[Any],
) -> ObservedDeploymentManifest | None:
    """Read the newest valid runtime-owned build attestation from durable rows."""

    for row in rows:
        metadata = getattr(row, "metadata_", None)
        authority = (
            metadata.get(EGRESS_CLEANUP_AUTHORITY_KEY)
            if isinstance(metadata, dict)
            else None
        )
        if (
            not isinstance(authority, dict)
            or authority.get("schemaVersion") != EGRESS_CLEANUP_AUTHORITY_VERSION
            or authority.get("phase") != "attested"
        ):
            continue
        observed = authority.get("egressEvidence")
        if not isinstance(observed, dict):
            continue
        server_ref = str(observed.get("serverImageRefObserved") or "").strip()
        host_ref = str(observed.get("workloadImageRef") or "").strip()
        server_digest = str(observed.get("serverImageDigest") or "").strip()
        host_digest = str(observed.get("workloadImageDigest") or "").strip()
        observed_at_text = str(observed.get("validatedAt") or "").strip()
        try:
            observed_at = datetime.fromisoformat(
                observed_at_text.replace("Z", "+00:00")
            )
        except ValueError:
            continue
        if observed_at.tzinfo is None:
            observed_at = observed_at.replace(tzinfo=UTC)
        if (
            not _DIGEST_IMAGE.fullmatch(server_ref)
            or not _DIGEST_IMAGE.fullmatch(host_ref)
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", server_digest)
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", host_digest)
            or not server_ref.endswith("@" + server_digest)
            or not host_ref.endswith("@" + host_digest)
        ):
            continue
        # The stock UI is bundled in the attested server image, so the actual
        # server image identity is also its deployed UI build identity.
        return ObservedDeploymentManifest(
            observed_at=observed_at.astimezone(UTC),
            server_image_ref=server_ref,
            host_image_ref=host_ref,
            ui_build_ref=server_ref,
        )
    return None


def _bootstrap_deployment_manifest(
    evidence: AcceptanceBootstrapEvidence | None,
) -> ObservedDeploymentManifest | None:
    if evidence is None:
        return None
    return ObservedDeploymentManifest(
        observed_at=evidence.observed_at,
        server_image_ref=evidence.server_image_ref_observed,
        host_image_ref=evidence.host_image_ref_observed,
        ui_build_ref=evidence.ui_build_ref_observed,
    )


def _reconciler_generation_available() -> bool:
    """Confirm the canonical durable session supervisor is in the worker fleet."""

    from moonmind.workflows.temporal.workflow_registry import (
        STATIC_WORKFLOW_REGISTRATIONS,
    )

    return any(
        registration.class_name == "MoonMindAgentSessionWorkflow"
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
    workflow_types: frozenset[str] = frozenset()
    activity_types: frozenset[str] = frozenset()
    immutable_worker_build: bool = False


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

    agent_worker_url = os.getenv(
        "TEMPORAL_AGENT_RUNTIME_READINESS_URL",
        "http://temporal-worker-agent-runtime:8080/readyz",
    )
    workflow_worker_url = os.getenv(
        "TEMPORAL_WORKFLOW_READINESS_URL",
        "http://temporal-worker-workflow:8080/readyz",
    )
    enforced_network_refs: set[str] = set()
    enforced_egress_profile_refs: set[str] = set()
    backend_ready = False
    workflow_types: set[str] = set()
    activity_types: set[str] = set()
    agent_build_id = ""
    agent_build_ready = False
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            response = await client.get(agent_worker_url)
            response.raise_for_status()
            payload = response.json()
        task_queues = {str(value) for value in payload.get("taskQueues", [])}
        backend = payload.get("containerBackend", {})
        backend_ready = (
            payload.get("ready") is True
            and settings.temporal.activity_agent_runtime_task_queue in task_queues
            and backend.get("ready") is True
        )
        activity_types = {str(value) for value in payload.get("activityTypes", [])}
        agent_build_id = str(payload.get("buildId") or "").strip()
        agent_build_ready = bool(
            payload.get("immutableReleaseIdentity") is True
            and agent_build_id
            and str(payload.get("registryFingerprint") or "").strip()
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
    workflow_build_id = ""
    workflow_build_ready = False
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            response = await client.get(workflow_worker_url)
            response.raise_for_status()
            payload = response.json()
        task_queues = {str(value) for value in payload.get("taskQueues", [])}
        workflow_ready = (
            payload.get("ready") is True
            and settings.temporal.workflow_task_queue in task_queues
        )
        if workflow_ready:
            workflow_types = {
                str(value) for value in payload.get("workflowTypes", [])
            }
        workflow_build_id = str(payload.get("buildId") or "").strip()
        workflow_build_ready = bool(
            workflow_ready
            and payload.get("immutableReleaseIdentity") is True
            and workflow_build_id
            and str(payload.get("registryFingerprint") or "").strip()
        )
    except (httpx.HTTPError, ValueError, TypeError, AttributeError):
        # Readiness is fail-closed; malformed or unavailable worker metadata must not advertise launch authority.
        pass
    immutable_worker_build = bool(
        agent_build_ready
        and workflow_build_ready
        and agent_build_id == workflow_build_id
    )
    return LiveDeploymentReadiness(
        endpoint_ready=endpoint_ready,
        backend_ready=backend_ready,
        enforced_network_refs=frozenset(enforced_network_refs if backend_ready else ()),
        enforced_egress_profile_refs=frozenset(
            enforced_egress_profile_refs if backend_ready else ()
        ),
        workflow_types=frozenset(workflow_types if backend_ready else ()),
        activity_types=frozenset(activity_types if backend_ready else ()),
        immutable_worker_build=immutable_worker_build if backend_ready else False,
    )


def _resolved_policy_images(policy: Any) -> tuple[str, str]:
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
    return values[0], values[1]


def _policy_images_ready(policy: Any) -> bool:
    values = _resolved_policy_images(policy)
    placeholder_digest = "0" * 64
    return all(
        _DIGEST_IMAGE.fullmatch(value)
        and not value.endswith(f"@sha256:{placeholder_digest}")
        for value in values
    )


def _images_match_support(
    server_ref: object,
    host_ref: object,
    evidence: SupportEvidence,
) -> bool:
    return bool(
        evidence.server_digest
        and evidence.host_digest
        and str(server_ref or "").endswith(evidence.server_digest)
        and str(host_ref or "").endswith(evidence.host_digest)
    )


def _images_match_observed(
    server_ref: object,
    host_ref: object,
    observed: ObservedDeploymentManifest | None,
) -> bool:
    return bool(
        observed is not None
        and str(server_ref or "") == observed.server_image_ref
        and str(host_ref or "") == observed.host_image_ref
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
    now = datetime.now(UTC)
    bootstrap_evidence = _parse_acceptance_bootstrap_evidence(
        request,
        acceptance_canary=acceptance_canary,
        now=now,
    )
    deployment_reasons = _deployment_reasons(config, bridge)
    support_evidence = _support_evidence(
        bootstrap_evidence=bootstrap_evidence,
        now=now,
    )
    support_reasons = list(support_evidence.reasons)
    protected_evidence_age = support_evidence.age
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
                        ("codex_cli", "claude_code", "opencode")
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
                        ("codex_cli", "claude_code", "opencode")
                    )
                )
            )
        )
        .scalars()
        .all()
    )
    active_slot_counts: dict[str, int] = {}
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
            (credential_source == "oauth_volume" and materialization == "oauth_home")
            or (
                runtime_id == "opencode"
                and credential_source == "secret_ref"
                and materialization in {"composite", "api_key_env", "config_bundle"}
                and row.provider_id in {"opencode-go", "opencode"}
            )
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
    attested_bridge_sessions = list(
        (
            await session.execute(
                select(OmnigentBridgeSession)
                .order_by(OmnigentBridgeSession.updated_at.desc())
                .limit(100)
            )
        )
        .scalars()
        .all()
    )
    observed_deployment = _observed_deployment_manifest(attested_bridge_sessions)
    if observed_deployment is None:
        observed_deployment = _bootstrap_deployment_manifest(bootstrap_evidence)
    schema_versions = (
        await session.execute(
            select(
                select(func.max(OmnigentSession.schema_version)).scalar_subquery(),
                select(func.max(OmnigentObservation.schema_version)).scalar_subquery(),
            )
        )
    ).one()
    schema_compatible = all(
        version is None or int(version) in SUPPORTED_SCHEMA_VERSIONS
        for version in schema_versions
    )
    snapshot_observed_at, event_observed_at = (
        await session.execute(
            select(
                select(func.max(OmnigentObservation.observed_at))
                .where(
                    OmnigentObservation.observation_type.in_(
                        _SNAPSHOT_OBSERVATION_TYPES
                    )
                )
                .scalar_subquery(),
                select(func.max(OmnigentObservation.observed_at))
                .where(
                    OmnigentObservation.observation_type.in_(
                        _EVENT_OBSERVATION_TYPES
                    )
                )
                .scalar_subquery(),
            )
        )
    ).one()

    def observation_age(value: datetime | None) -> timedelta | None:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return _evidence_age(value, now=now)

    snapshot_age = observation_age(snapshot_observed_at)
    event_age = observation_age(event_observed_at)
    if bootstrap_evidence is not None:
        bootstrap_age = _evidence_age(bootstrap_evidence.observed_at, now=now)
        snapshot_age = snapshot_age if snapshot_age is not None else bootstrap_age
        event_age = event_age if event_age is not None else bootstrap_age
    freshest_observation_age = (
        max(snapshot_age, event_age)
        if snapshot_age is not None and event_age is not None
        else None
    )

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
    deployed_reconciler_ready = (
        _reconciler_generation_available()
        and "MoonMind.AgentSession" in live_readiness.workflow_types
        and "agent_runtime.reconcile_managed_sessions"
        in live_readiness.activity_types
    )
    janitor_healthy = (
        live_readiness.backend_ready
        and "integration.omnigent.oauth_host_janitor"
        in live_readiness.activity_types
    )
    build_observation_age = (
        _evidence_age(observed_deployment.observed_at, now=now)
        if observed_deployment is not None
        else None
    )
    build_observation_fresh = bool(
        build_observation_age is not None
        and timedelta(0) <= build_observation_age <= _MAX_BUILD_OBSERVATION_AGE
    )
    observed_build_matches_support = bool(
        observed_deployment is not None
        and _images_match_support(
            observed_deployment.server_image_ref,
            observed_deployment.host_image_ref,
            support_evidence,
        )
    )
    exact_image_conformant = bool(
        build_observation_fresh
        and observed_build_matches_support
        and (
            any(
                _images_match_observed(
                    *_resolved_policy_images(policy), observed_deployment
                )
                for policy in POLICIES.values()
                if _policy_images_ready(policy)
            )
            or any(
                _images_match_observed(
                    version.document_json.get("host", {}).get("serverImageRef"),
                    version.document_json.get("host", {}).get("hostImageRef"),
                    observed_deployment,
                )
                for _identity, version in persisted_policies
                if version.validation_json.get("valid")
            )
        )
    )
    snapshot_ready = bool(
        snapshot_age is not None
        and timedelta(0) <= snapshot_age <= timedelta(minutes=10)
    )
    event_transport_ready = bool(
        event_age is not None
        and timedelta(0) <= event_age <= timedelta(minutes=10)
    )
    admission = evaluate_admission_readiness(
        ReadinessInputs(
            reconciler_generation_ready=deployed_reconciler_ready,
            schema_compatible=schema_compatible,
            provider_snapshot_ready=snapshot_ready,
            event_transport_ready=event_transport_ready,
            server_build_ready=(
                build_observation_fresh and observed_build_matches_support
            ),
            ui_build_ready=bool(
                build_observation_fresh
                and observed_build_matches_support
                and observed_deployment is not None
                and observed_deployment.ui_build_ref
                and support_evidence.ui_source_commit
            ),
            host_build_ready=(
                build_observation_fresh and observed_build_matches_support
            ),
            websocket_available=_websocket_runtime_available(),
            worker_backend_ready=(
                live_readiness.backend_ready
                and live_readiness.immutable_worker_build
            ),
            container_backend_ready=backend_ready,
            observation_age=freshest_observation_age,
            janitor_healthy=janitor_healthy,
            exact_image_conformant=exact_image_conformant,
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
            pass  # Telemetry failures must not affect lifecycle authority
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
