"""Singleton per-runtime-family provider profile manager workflow.

Each managed agent runtime family (claude_code, codex_cli) gets its
own long-lived ProviderProfileManager workflow instance. The manager owns the truth
about slot leases — which profiles have available capacity and which are in
cooldown — and assigns slots to AgentRun workflows via Temporal Signals.

Workflow ID convention: ``provider-profile-manager:<runtime_id>``
  e.g. ``provider-profile-manager:codex_cli``

Design references:
  - docs/ManagedAgents/ManagedAgentsAuthentication.md (Section 5)
  - docs/Temporal/ManagedAndExternalAgentExecutionModel.md (Section 7)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, TypedDict

from temporalio import exceptions, workflow

with workflow.unsafe.imports_passed_through():
    from temporalio.common import RetryPolicy
    from moonmind.billing.costs import pricing_from_profile_metadata
    from moonmind.provider_profiles.oauth_policy import (
        CODEX_OAUTH_EXCLUSIVE_CAPACITY_ERROR,
        is_codex_oauth_profile,
        validate_codex_oauth_capacity,
    )
    from moonmind.provider_profiles.lease_client import CredentialLeasePurpose

WORKFLOW_NAME = "MoonMind.ProviderProfileManager"
ACTIVITY_TASK_QUEUE = "mm.activity.artifacts"
WORKFLOW_ID_PREFIX = "provider-profile-manager"

# Replay patch IDs are durable Temporal history markers. Preserve legacy
# "auth-profile" spellings in identifiers until a deliberate workflow migration.
VERIFY_LEASE_HOLDERS_PATCH = "auth-profile-manager-verify-leases-v1"
DB_LEASE_PERSISTENCE_PATCH = "provider-profile-manager-db-lease-persistence-v1"
SLOT_HANDOFF_RESERVATION_PATCH = "provider-profile-manager-slot-handoff-v1"
REFRESH_RESTORED_PROFILES_PATCH = (
    "provider-profile-manager-refresh-restored-profiles-v1"
)
DB_AUTHORITATIVE_PROFILE_SYNC_PATCH = (
    "provider-profile-manager-db-authoritative-profile-sync-v1"
)
VERIFY_PENDING_REQUESTS_PATCH = "provider-profile-manager-verify-pending-requests-v1"
DEFAULT_PROFILE_EXCLUSIVE_SELECTION_PATCH = (
    "provider-profile-manager-default-profile-exclusive-selection-v1"
)
BILLING_AWARE_PROFILE_SELECTION_PATCH = (
    "provider-profile-manager-billing-aware-selection-v1"
)
PRIORITY_PENDING_REQUESTS_PATCH = (
    "provider-profile-manager-priority-pending-requests-v1"
)
QUEUE_ORDER_PENDING_REQUESTS_PATCH = (
    "provider-profile-manager-queue-order-pending-requests-v1"
)
SCHEDULED_PENDING_REQUESTS_PATCH = (
    "provider-profile-manager-scheduled-pending-requests-v1"
)
CODEX_OAUTH_LEGACY_RESTORE_PATCH = (
    "provider-profile-manager-codex-oauth-legacy-restore-v1"
)
PURPOSE_AWARE_CREDENTIAL_LEASE_PATCH = (
    "provider-profile-manager-purpose-aware-credential-lease-v1"
)
FRESH_START_DB_LEASE_RESTORE_PATCH = (
    "provider-profile-manager-fresh-start-db-lease-restore-v1"
)
DURABLE_LEASE_GRANT_PATCH = "provider-profile-manager-durable-lease-grant-v1"

# Deterministic sort sentinel for pending requests whose scheduled queue order
# cannot be resolved (missing scheduled_for / created_at). ISO-8601 strings sort
# lexically, so this value sorts after any real UTC timestamp.
_FAR_FUTURE_ORDER_VALUE = "9999-12-31T23:59:59.999999+00:00"

# Continue-as-new threshold to bound history growth.
_MAX_EVENTS_BEFORE_CONTINUE_AS_NEW = 2000
_VERIFY_WORKFLOW_STATUS_BATCH_SIZE = 100

logger = logging.getLogger(__name__)


def _profile_is_codex_oauth(
    profile: dict[str, Any],
    *,
    runtime_id: str | None = None,
    infer_legacy_source: bool = False,
) -> bool:
    resolved_runtime_id = profile.get("runtime_id", runtime_id)
    credential_source = profile.get("credential_source")
    materialization_mode = profile.get("runtime_materialization_mode")
    if (
        infer_legacy_source
        and credential_source is None
        and str(resolved_runtime_id or "").strip() == "codex_cli"
        and str(materialization_mode or "").strip() == "oauth_home"
    ):
        credential_source = "oauth_volume"
    return is_codex_oauth_profile(
        runtime_id=resolved_runtime_id,
        credential_source=credential_source,
        materialization_mode=materialization_mode,
    )


def _validated_profile_capacity(
    profile: dict[str, Any],
    *,
    runtime_id: str | None = None,
    existing_capacity: int | None = None,
    repair_legacy: bool = False,
) -> int:
    if "max_parallel_runs" not in profile and existing_capacity is not None:
        capacity = existing_capacity
    else:
        capacity = profile.get("max_parallel_runs", 1)
    if not isinstance(capacity, int) or isinstance(capacity, bool) or capacity < 1:
        raise exceptions.ApplicationError(
            "Provider Profile max_parallel_runs must be a positive integer",
            non_retryable=True,
        )
    is_codex_oauth = _profile_is_codex_oauth(
        profile,
        runtime_id=runtime_id,
        infer_legacy_source=repair_legacy,
    )
    if is_codex_oauth and capacity != 1:
        if repair_legacy:
            return 1
        try:
            validate_codex_oauth_capacity(
                runtime_id=profile.get("runtime_id", runtime_id),
                credential_source=profile.get("credential_source"),
                materialization_mode=profile.get("runtime_materialization_mode"),
                max_parallel_runs=capacity,
            )
        except ValueError as exc:
            raise exceptions.ApplicationError(
                CODEX_OAUTH_EXCLUSIVE_CAPACITY_ERROR,
                non_retryable=True,
            ) from exc
    return capacity


def workflow_id_for_runtime(runtime_id: str) -> str:
    """Return the canonical ProviderProfileManager workflow ID for a runtime."""

    normalized = str(runtime_id or "").strip()
    if not normalized:
        raise ValueError("runtime_id is required")
    return f"{WORKFLOW_ID_PREFIX}:{normalized}"


# ---------------------------------------------------------------------------
# Input / Output types
# ---------------------------------------------------------------------------


class ProviderProfileManagerInput(TypedDict, total=False):
    """Input payload for starting or continuing the manager."""

    runtime_id: str
    profiles: list[dict[str, Any]]
    leases: dict[str, list[str]]
    cooldowns: dict[str, str]
    lease_granted_at: dict[str, dict[str, str]]
    pending_requests: list[dict[str, Any]]
    handoff_reservations: dict[str, dict[str, str]]
    lease_metadata: dict[str, dict[str, dict[str, Any]]]


class ProviderProfileManagerOutput(TypedDict):
    status: str
    runtime_id: Optional[str]


# ---------------------------------------------------------------------------
# Signal payloads (documented as TypedDicts for clarity; actual transport is dict)
# ---------------------------------------------------------------------------


class SlotRequestPayload(TypedDict):
    """Signal payload: an AgentRun requests a profile slot."""

    requester_workflow_id: str
    runtime_id: str
    priority: int
    queue_order: int | None
    queued_at: str | None
    execution_profile_ref: str | None
    lease_group_id: str | None


class SlotAcquirePayload(TypedDict, total=False):
    """Update payload: synchronously reserve a provider slot for an activity caller."""

    requester_workflow_id: str
    runtime_id: str
    execution_profile_ref: str | None
    profile_selector: dict[str, Any] | None
    lease_group_id: str | None
    metadata: dict[str, Any]
    owner_id: str
    purpose: str


class SlotReleasePayload(TypedDict):
    """Signal payload: an AgentRun releases its profile slot."""

    requester_workflow_id: str
    profile_id: str
    lease_group_id: str | None
    handoff_ttl_seconds: int | None


class CooldownReportPayload(TypedDict):
    """Signal payload: report a 429 cooldown on a profile."""

    profile_id: str
    cooldown_seconds: int


class ProfileSyncPayload(TypedDict):
    """Signal payload: updated profile list from DB."""

    profiles: list[dict[str, Any]]


# ---------------------------------------------------------------------------
# Internal state helpers
# ---------------------------------------------------------------------------

_MAX_LEASE_DURATION_SECONDS = 5400  # 1.5 hours — safety net for leaked slots
_MAX_HANDOFF_RESERVATION_SECONDS = 30


@dataclass
class ProfileSlotState:
    """In-workflow tracking of one provider profile's slot availability."""

    profile_id: str
    max_parallel_runs: int
    cooldown_after_429_seconds: int
    rate_limit_policy: str
    enabled: bool
    launch_ready: bool = True
    is_default: bool = False
    max_lease_duration_seconds: int = _MAX_LEASE_DURATION_SECONDS
    current_leases: list[str] = field(default_factory=list)
    lease_granted_at: dict[str, str] = field(default_factory=dict)  # wf_id -> ISO ts
    lease_metadata: dict[str, dict[str, Any]] = field(default_factory=dict)
    cooldown_until: Optional[str] = None  # ISO timestamp string or None
    provider_id: Optional[str] = None
    credential_source: Optional[str] = None
    tags: list[str] = field(default_factory=list)
    priority: int = 100
    runtime_materialization_mode: Optional[str] = None
    input_per_million_usd: Optional[float] = None
    output_per_million_usd: Optional[float] = None
    pricing_source: Optional[str] = None
    model_tiers: list[dict[str, Any]] = field(default_factory=list)
    default_model_tier: int = 1
    over_capacity_legacy_snapshot: bool = False
    authoritative_policy_confirmed: bool = False

    @property
    def available_slots(self) -> int:
        if not self.enabled or not self.launch_ready:
            return 0
        return max(0, self.max_parallel_runs - len(self.current_leases))

    def is_available(self) -> bool:
        if not self.enabled or not self.launch_ready or self.available_slots <= 0:
            return False
        if self.cooldown_until is not None:
            return False
        return True

    def reserve(
        self,
        requester_workflow_id: str,
        now: datetime,
        *,
        purpose: str = "execution_direct",
        metadata: dict[str, Any] | None = None,
        allow_unready: bool = False,
    ) -> bool:
        if allow_unready:
            if len(self.current_leases) >= self.max_parallel_runs:
                return False
        elif not self.is_available():
            return False
        self.current_leases.append(requester_workflow_id)
        self.lease_granted_at[requester_workflow_id] = now.isoformat()
        self.lease_metadata[requester_workflow_id] = {
            "leaseId": requester_workflow_id,
            "ownerId": requester_workflow_id,
            "purpose": purpose,
            "acquiredAt": now.isoformat(),
            "expiresAt": (
                now + timedelta(seconds=self.max_lease_duration_seconds)
            ).isoformat(),
            **dict(metadata or {}),
        }
        return True

    def release(self, requester_workflow_id: str) -> bool:
        if requester_workflow_id in self.current_leases:
            self.current_leases.remove(requester_workflow_id)
            self.lease_granted_at.pop(requester_workflow_id, None)
            self.lease_metadata.pop(requester_workflow_id, None)
            if (
                self.authoritative_policy_confirmed
                and len(self.current_leases) <= self.max_parallel_runs
            ):
                self.over_capacity_legacy_snapshot = False
            return True
        return False

    def evict_expired_leases(
        self, now: datetime, max_duration_seconds: int
    ) -> list[str]:
        """Remove leases that have exceeded the maximum duration. Returns evicted IDs."""
        evicted: list[str] = []
        for wf_id in list(self.current_leases):
            granted_str = self.lease_granted_at.get(wf_id)
            if granted_str is None:
                # Legacy lease without timestamp — evict it as we can't verify age.
                self.current_leases.remove(wf_id)
                self.lease_metadata.pop(wf_id, None)
                evicted.append(wf_id)
                continue
            try:
                granted_dt = datetime.fromisoformat(granted_str)
                if granted_dt.tzinfo is None:
                    granted_dt = granted_dt.replace(tzinfo=timezone.utc)
                if (now - granted_dt).total_seconds() > max_duration_seconds:
                    self.current_leases.remove(wf_id)
                    self.lease_granted_at.pop(wf_id, None)
                    self.lease_metadata.pop(wf_id, None)
                    evicted.append(wf_id)
            except (ValueError, TypeError):
                self.current_leases.remove(wf_id)
                self.lease_granted_at.pop(wf_id, None)
                self.lease_metadata.pop(wf_id, None)
                evicted.append(wf_id)
        return evicted

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "max_parallel_runs": self.max_parallel_runs,
            "cooldown_after_429_seconds": self.cooldown_after_429_seconds,
            "rate_limit_policy": self.rate_limit_policy,
            "enabled": self.enabled,
            "launch_ready": self.launch_ready,
            "is_default": self.is_default,
            "max_lease_duration_seconds": self.max_lease_duration_seconds,
            "current_leases": list(self.current_leases),
            "lease_granted_at": dict(self.lease_granted_at),
            "lease_metadata": dict(self.lease_metadata),
            "cooldown_until": self.cooldown_until,
            "provider_id": self.provider_id,
            "credential_source": self.credential_source,
            "tags": list(self.tags),
            "priority": self.priority,
            "runtime_materialization_mode": self.runtime_materialization_mode,
            "input_per_million_usd": self.input_per_million_usd,
            "output_per_million_usd": self.output_per_million_usd,
            "pricing_source": self.pricing_source,
            "model_tiers": list(self.model_tiers),
            "default_model_tier": self.default_model_tier,
            "overCapacityLegacySnapshot": self.over_capacity_legacy_snapshot,
        }

    @property
    def blended_per_million_usd(self) -> Optional[float]:
        if self.input_per_million_usd is None or self.output_per_million_usd is None:
            return None
        return self.input_per_million_usd + self.output_per_million_usd


@dataclass
class PendingRequest:
    """A queued slot request waiting for assignment."""

    requester_workflow_id: str
    runtime_id: str
    priority: int = 0
    queue_order: int | None = None
    queued_at: str | None = None
    execution_profile_ref: str | None = None
    profile_selector: Optional[dict[str, Any]] = None
    lease_group_id: str | None = None
    purpose: str = "execution_direct"
    lease_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class HandoffReservation:
    """Short-lived profile reservation for the next step in the same run."""

    profile_id: str
    expires_at: str


# ---------------------------------------------------------------------------
# Workflow definition
# ---------------------------------------------------------------------------


@workflow.defn(name=WORKFLOW_NAME)
class MoonMindProviderProfileManagerWorkflow:
    """Per-runtime-family singleton that manages provider profile slot leases.

    The manager:
      1. Maintains in-memory slot state for all profiles of its runtime family.
      2. Receives slot requests via signal, assigns immediately if possible,
         or queues the request.
      3. On slot release, drains the queue by assigning freed slots.
      4. Handles cooldown reports (429s) by marking profiles temporarily
         unavailable.
      5. Periodically clears expired cooldowns.
      6. Uses continue-as-new to bound workflow history.
    """

    def _get_logger(self) -> logging.LoggerAdapter | logging.Logger:
        try:
            info = workflow.info()
        except Exception:
            logging.getLogger(__name__).exception(
                "Error getting workflow info in _get_logger"
            )
            return logging.getLogger(__name__)

        extra = {
            "workflow_id": getattr(info, "workflow_id", "unknown"),
            "run_id": getattr(info, "run_id", "unknown"),
            "task_queue": getattr(info, "task_queue", "unknown"),
        }

        logger_to_use = workflow.logger
        if not hasattr(logger_to_use, "isEnabledFor"):
            logger_to_use = logging.getLogger(__name__)

        try:
            logger_to_use.isEnabledFor(logging.INFO)
            return logging.LoggerAdapter(logger_to_use, extra=extra)
        except Exception:
            logging.getLogger(__name__).exception(
                "Error checking logger capabilities in _get_logger"
            )
            return logging.LoggerAdapter(logging.getLogger(__name__), extra=extra)

    def __init__(self) -> None:
        self._runtime_id: Optional[str] = None
        self._profiles: dict[str, ProfileSlotState] = {}
        self._pending_requests: list[PendingRequest] = []
        self._pending_requests_ordered: bool = False
        self._handoff_reservations: dict[str, HandoffReservation] = {}
        self._event_count: int = 0
        self._shutdown_requested: bool = False
        self._has_new_events: bool = False
        self._profile_refresh_requested: bool = False
        self._has_db_profile_snapshot: bool = False
        self._purpose_aware_leases: bool = False
        # Cache of resolved scheduled/created ordering keyed by queue-order
        # workflow id. Workflow creation/scheduled times are immutable, so a
        # resolved entry never has to be re-queried; this keeps the
        # ``provider_profile.pending_request_order`` activity from re-hitting the
        # database for the same ids on every drain cycle.
        self._resolved_orders: dict[str, dict[str, Any]] = {}

    @staticmethod
    def _lease_purpose(payload: dict[str, Any], *, maintenance: bool = False) -> str:
        default = (
            CredentialLeasePurpose.CREDENTIAL_VALIDATION.value
            if maintenance
            else CredentialLeasePurpose.EXECUTION_DIRECT.value
        )
        try:
            purpose = CredentialLeasePurpose(payload.get("purpose", default))
        except ValueError as exc:
            raise exceptions.ApplicationError(
                "Unsupported credential lease purpose", non_retryable=True
            ) from exc
        if purpose.is_maintenance != maintenance:
            raise exceptions.ApplicationError(
                "Credential lease purpose does not match acquisition mode",
                non_retryable=True,
            )
        return purpose.value

    @staticmethod
    def _safe_lease_metadata(payload: dict[str, Any]) -> dict[str, Any]:
        source = payload.get("metadata")
        if not isinstance(source, dict):
            source = {}
        allowed = {
            "workflowId",
            "stepExecutionId",
            "oauthSessionId",
            "idempotencyKey",
            "ownerIsWorkflow",
        }
        return {key: source[key] for key in allowed if source.get(key) is not None}

    # -- Signals ---------------------------------------------------------------

    @workflow.signal
    def request_slot(self, payload: dict[str, Any]) -> None:
        """An AgentRun requests a profile slot for this runtime family."""
        self._event_count += 1
        self._has_new_events = True
        priority = self._normalize_request_priority(payload.get("priority"))
        queue_order = self._normalize_queue_order(payload.get("queue_order"))
        queued_at = self._normalize_optional_string(payload.get("queued_at"))
        self._pending_requests_ordered = False
        if not workflow.patched(SLOT_HANDOFF_RESERVATION_PATCH):
            self._pending_requests.append(
                PendingRequest(
                    requester_workflow_id=payload["requester_workflow_id"],
                    runtime_id=payload.get("runtime_id", self._runtime_id or ""),
                    priority=priority,
                    queue_order=queue_order,
                    queued_at=queued_at,
                    execution_profile_ref=payload.get("execution_profile_ref"),
                    profile_selector=payload.get("profile_selector"),
                    purpose=self._lease_purpose(payload),
                    lease_metadata=self._safe_lease_metadata(payload),
                )
            )
            return
        request = PendingRequest(
            requester_workflow_id=payload["requester_workflow_id"],
            runtime_id=payload.get("runtime_id", self._runtime_id or ""),
            priority=priority,
            queue_order=queue_order,
            queued_at=queued_at,
            execution_profile_ref=payload.get("execution_profile_ref"),
            profile_selector=payload.get("profile_selector"),
            lease_group_id=self._normalize_optional_string(
                payload.get("lease_group_id")
            ),
            purpose=self._lease_purpose(payload),
            lease_metadata=self._safe_lease_metadata(payload),
        )
        for index, existing in enumerate(self._pending_requests):
            if existing.requester_workflow_id == request.requester_workflow_id:
                self._pending_requests[index] = request
                if workflow.patched(DB_AUTHORITATIVE_PROFILE_SYNC_PATCH):
                    self._profile_refresh_requested = True
                return
        self._pending_requests.append(request)
        if workflow.patched(DB_AUTHORITATIVE_PROFILE_SYNC_PATCH):
            self._profile_refresh_requested = True

    @workflow.signal
    async def release_slot(self, payload: dict[str, Any]) -> None:
        """An AgentRun releases its profile slot."""
        self._event_count += 1
        self._has_new_events = True
        profile_id = payload["profile_id"]
        requester_id = payload["requester_workflow_id"]
        profile = self._profiles.get(profile_id)
        released = False
        if profile:
            released = profile.release(requester_id)
        if workflow.patched(SLOT_HANDOFF_RESERVATION_PATCH):
            self._pending_requests = [
                req
                for req in self._pending_requests
                if req.requester_workflow_id != requester_id
            ]
            lease_group_id = self._normalize_optional_string(
                payload.get("lease_group_id")
            )
            handoff_ttl_seconds = self._coerce_handoff_ttl_seconds(
                payload.get("handoff_ttl_seconds")
            )
            if released and lease_group_id and handoff_ttl_seconds > 0:
                self._handoff_reservations[lease_group_id] = HandoffReservation(
                    profile_id=profile_id,
                    expires_at=(
                        workflow.now() + timedelta(seconds=handoff_ttl_seconds)
                    ).isoformat(),
                )
        # Always remove from DB regardless of whether profile exists in memory,
        # so stale rows don't survive profile removals or disablement.
        if workflow.patched(DB_LEASE_PERSISTENCE_PATCH):
            await self._remove_lease_from_db(requester_id)

    @workflow.signal
    def report_cooldown(self, payload: dict[str, Any]) -> None:
        """Report a 429-triggered cooldown on a profile."""
        self._event_count += 1
        self._has_new_events = True
        profile_id = payload["profile_id"]
        profile = self._profiles.get(profile_id)
        if profile:
            cooldown_seconds = payload.get(
                "cooldown_seconds",
                profile.cooldown_after_429_seconds,
            )
            now = workflow.now()
            cooldown_until = now + timedelta(seconds=cooldown_seconds)
            profile.cooldown_until = cooldown_until.isoformat()

    @workflow.signal
    def sync_profiles(self, payload: dict[str, Any]) -> None:
        """Request a provider-profile refresh from the authoritative DB snapshot.

        The signal payload shape is preserved for in-flight workflow
        compatibility, but new executions intentionally ignore embedded profile
        rows. Profile existence and enabled/default state must come from the
        provider_profile.list activity so stray or stale signal payloads cannot
        poison slot assignment.
        """
        self._event_count += 1
        self._has_new_events = True
        if workflow.patched(DB_AUTHORITATIVE_PROFILE_SYNC_PATCH):
            self._profile_refresh_requested = True
            return
        profiles_data = payload.get("profiles", [])
        self._apply_profile_sync(profiles_data)

    @workflow.signal
    def shutdown(self) -> None:
        """Gracefully shut down the manager."""
        self._shutdown_requested = True
        self._has_new_events = True

    @workflow.update(name="AcquireSlot")
    async def acquire_slot(self, payload: SlotAcquirePayload) -> dict[str, Any]:
        """Reserve and return a slot without requiring a callback signal.

        Activity-owned workloads cannot receive ``slot_assigned``. This update
        keeps those callers on the same manager-owned capacity ledger while
        preserving the existing signal protocol for AgentRun workflows.
        """

        requester_id = self._normalize_optional_string(
            payload.get("requester_workflow_id")
        )
        if requester_id is None:
            raise exceptions.ApplicationError(
                "requester_workflow_id is required", non_retryable=True
            )
        runtime_id = self._normalize_optional_string(payload.get("runtime_id"))
        if runtime_id is None:
            raise exceptions.ApplicationError(
                "runtime_id is required", non_retryable=True
            )

        selector = self._normalize_selector(payload.get("profile_selector"))
        execution_profile_ref = self._normalize_optional_string(
            payload.get("execution_profile_ref")
        )
        lease_group_id = self._normalize_optional_string(payload.get("lease_group_id"))
        purpose = self._lease_purpose(payload)
        lease_metadata = self._safe_lease_metadata(payload)

        while not self._shutdown_requested:
            existing_profile_id = self._profile_id_for_lease(requester_id)
            if existing_profile_id is not None:
                return {
                    "profile_id": existing_profile_id,
                    "lease_id": requester_id,
                    "already_held": True,
                }

            now = workflow.now()
            self._clear_expired_handoff_reservations(now)
            self._clear_expired_cooldowns()
            profile = self._find_available_profile(
                selector=selector,
                execution_profile_ref=execution_profile_ref,
                lease_group_id=lease_group_id,
            )
            if profile and profile.reserve(
                requester_id,
                now,
                purpose=purpose,
                metadata=lease_metadata,
            ):
                if workflow.patched(DB_LEASE_PERSISTENCE_PATCH):
                    persisted = await self._sync_leases_to_db()
                    if (
                        workflow.patched(DURABLE_LEASE_GRANT_PATCH)
                        and not persisted
                    ):
                        profile.release(requester_id)
                        raise exceptions.ApplicationError(
                            "Provider profile lease persistence failed before direct grant",
                            type="ProviderProfileLeasePersistenceFailed",
                        )
                self._has_new_events = True
                return {
                    "profile_id": profile.profile_id,
                    "lease_id": requester_id,
                    "already_held": False,
                }

            try:
                await workflow.wait_condition(
                    lambda: (
                        self._shutdown_requested
                        or self._profile_id_for_lease(requester_id) is not None
                        or self._has_available_profile(
                            selector=selector,
                            execution_profile_ref=execution_profile_ref,
                            lease_group_id=lease_group_id,
                        )
                    ),
                    timeout=timedelta(seconds=60),
                )
            except TimeoutError:
                # Periodic wake-up: re-check capacity, cooldowns, and shutdown.
                continue

        raise exceptions.ApplicationError(
            "provider profile manager is shutting down", non_retryable=True
        )

    @workflow.update(name="AcquireCredentialMaintenanceLease")
    async def acquire_credential_maintenance_lease(
        self, payload: SlotAcquirePayload
    ) -> dict[str, Any]:
        """Acquire exact-profile capacity while a profile is disabled or unready."""

        requester_id = self._normalize_optional_string(
            payload.get("requester_workflow_id") or payload.get("owner_id")
        )
        runtime_id = self._normalize_optional_string(payload.get("runtime_id"))
        profile_id = self._normalize_optional_string(
            payload.get("execution_profile_ref")
        )
        if not requester_id or not runtime_id or not profile_id:
            raise exceptions.ApplicationError(
                "maintenance lease requires requester_workflow_id, runtime_id, and exact profile",
                non_retryable=True,
            )
        if payload.get("profile_selector"):
            raise exceptions.ApplicationError(
                "maintenance lease does not allow profile selectors",
                non_retryable=True,
            )
        purpose = self._lease_purpose(payload, maintenance=True)
        existing_profile_id = self._profile_id_for_lease(requester_id)
        if existing_profile_id is not None:
            if existing_profile_id != profile_id:
                raise exceptions.ApplicationError(
                    "lease owner already holds a different profile",
                    non_retryable=True,
                )
            return {
                "profile_id": profile_id,
                "lease_id": requester_id,
                "already_held": True,
            }
        profile = self._profiles.get(profile_id)
        if profile is None:
            profile = ProfileSlotState(
                profile_id=profile_id,
                max_parallel_runs=1,
                cooldown_after_429_seconds=900,
                rate_limit_policy="backoff",
                enabled=False,
                launch_ready=False,
            )
            self._profiles[profile_id] = profile
        if profile.max_parallel_runs != 1:
            raise exceptions.ApplicationError(
                "credential maintenance requires exclusive profile capacity",
                non_retryable=True,
            )
        while not self._shutdown_requested:
            if profile.reserve(
                requester_id,
                workflow.now(),
                purpose=purpose,
                metadata=self._safe_lease_metadata(payload),
                allow_unready=True,
            ):
                if workflow.patched(DB_LEASE_PERSISTENCE_PATCH):
                    persisted = await self._sync_leases_to_db()
                    if (
                        workflow.patched(DURABLE_LEASE_GRANT_PATCH)
                        and not persisted
                    ):
                        profile.release(requester_id)
                        raise exceptions.ApplicationError(
                            "Provider profile lease persistence failed before maintenance grant",
                            type="ProviderProfileLeasePersistenceFailed",
                        )
                return {
                    "profile_id": profile_id,
                    "lease_id": requester_id,
                    "already_held": False,
                }
            try:
                await workflow.wait_condition(
                    lambda: (
                        self._shutdown_requested
                        or not profile.current_leases
                        or requester_id in profile.current_leases
                    ),
                    timeout=timedelta(seconds=60),
                )
            except TimeoutError:
                continue
        raise exceptions.ApplicationError(
            "provider profile manager is shutting down", non_retryable=True
        )

    @workflow.update(name="InspectCredentialLease")
    def inspect_credential_lease(self, payload: dict[str, Any]) -> dict[str, Any]:
        lease_id = self._normalize_optional_string(
            payload.get("lease_id") or payload.get("owner_id")
        )
        if not lease_id:
            raise exceptions.ApplicationError(
                "lease_id is required", non_retryable=True
            )
        profile_id = self._profile_id_for_lease(lease_id)
        if profile_id is None:
            return {"active": False, "lease_id": lease_id}
        profile = self._profiles[profile_id]
        return {
            "active": True,
            "lease_id": lease_id,
            "profile_id": profile_id,
            **dict(profile.lease_metadata.get(lease_id) or {}),
            "acquiredAt": profile.lease_granted_at.get(lease_id),
        }

    # -- Queries ---------------------------------------------------------------

    @workflow.query
    def get_state(self) -> dict[str, Any]:
        """Return current manager state for observability."""
        return {
            "runtime_id": self._runtime_id,
            "profiles": {pid: p.to_dict() for pid, p in self._profiles.items()},
            "pending_requests": [
                {
                    "requester_workflow_id": r.requester_workflow_id,
                    "runtime_id": r.runtime_id,
                    "priority": r.priority,
                    "queue_order": r.queue_order,
                    "queued_at": r.queued_at,
                    "execution_profile_ref": r.execution_profile_ref,
                    "profile_selector": r.profile_selector,
                    "lease_group_id": r.lease_group_id,
                    "purpose": r.purpose,
                    "lease_metadata": dict(r.lease_metadata),
                }
                for r in self._pending_requests
            ],
            "pending_requests_ordered": self._pending_requests_ordered,
            "handoff_reservations": {
                group_id: {
                    "profile_id": reservation.profile_id,
                    "expires_at": reservation.expires_at,
                }
                for group_id, reservation in self._handoff_reservations.items()
            },
            "event_count": self._event_count,
        }

    # -- Main loop -------------------------------------------------------------

    @workflow.run
    async def run(self, input_payload: dict[str, Any]) -> ProviderProfileManagerOutput:
        self._runtime_id = input_payload.get("runtime_id")
        if not self._runtime_id:
            raise exceptions.ApplicationError(
                "runtime_id is required", non_retryable=True
            )

        # Restore state from continue-as-new or initial profile load.
        repair_legacy_codex_oauth = workflow.patched(CODEX_OAUTH_LEGACY_RESTORE_PATCH)
        self._purpose_aware_leases = workflow.patched(
            PURPOSE_AWARE_CREDENTIAL_LEASE_PATCH
        )
        self._restore_state(
            input_payload,
            repair_legacy_codex_oauth=repair_legacy_codex_oauth,
        )

        # If no profiles were provided, load them via activity.
        if not self._profiles:
            await self._load_profiles_from_db()

        # A fresh singleton execution must restore the durable lease ledger
        # before it drains requests. Signal handlers can populate the pending
        # queue while the profile-list activity is in flight, so pending state
        # is not evidence that startup lease recovery already happened.
        if workflow.patched(DB_LEASE_PERSISTENCE_PATCH):
            if workflow.patched(FRESH_START_DB_LEASE_RESTORE_PATCH):
                if workflow.info().continued_run_id is None:
                    leases_restored = await self._load_leases_from_db()
                    if not leases_restored:
                        raise exceptions.ApplicationError(
                            "Provider profile lease recovery failed; refusing to grant capacity without the authoritative lease ledger",
                            type="ProviderProfileLeaseRecoveryFailed",
                            non_retryable=True,
                        )
            else:
                has_leases = any(p.current_leases for p in self._profiles.values())
                has_pending = bool(self._pending_requests)
                if not has_leases and not has_pending:
                    await self._load_leases_from_db()

        # Refresh restored state from the authoritative DB snapshot. This keeps
        # continued-as-new managers from routing to profiles deleted or changed
        # since the prior history payload was created. This patch is evaluated
        # after older startup patch markers to preserve replay order.
        if self._profiles and workflow.patched(REFRESH_RESTORED_PROFILES_PATCH):
            await self._load_profiles_from_db()

        # Main event loop: process signals, drain queue, clear cooldowns.
        while not self._shutdown_requested:
            if workflow.patched(DB_AUTHORITATIVE_PROFILE_SYNC_PATCH):
                if self._profile_refresh_requested:
                    refresh_succeeded = await self._load_profiles_from_db(
                        prune_removed_profiles=True
                    )
                    if not refresh_succeeded and not self._has_db_profile_snapshot:
                        self._has_new_events = False
                        try:
                            await workflow.wait_condition(
                                lambda: (
                                    self._has_new_events or self._shutdown_requested
                                ),
                                timeout=timedelta(seconds=60),
                            )
                        except TimeoutError:
                            # Expected: retry the authoritative profile refresh on the next loop.
                            pass
                        continue

            # Drain pending requests against available profiles before any
            # best-effort terminal-workflow verification activity.
            await self._drain_queue()

            # Clear expired cooldowns.
            self._clear_expired_cooldowns()

            # Evict leases that exceed the max duration (safety net for
            # cancelled/terminated workflows that failed to release).
            evicted_count = self._evict_expired_leases()
            if evicted_count > 0 and workflow.patched(DB_LEASE_PERSISTENCE_PATCH):
                await self._sync_leases_to_db()

            verify_lease_holders = workflow.patched(VERIFY_LEASE_HOLDERS_PATCH)
            verify_pending_requesters = workflow.patched(VERIFY_PENDING_REQUESTS_PATCH)
            if verify_lease_holders or verify_pending_requesters:
                await self._verify_active_workflows(
                    verify_lease_holders=verify_lease_holders,
                    verify_pending_requesters=verify_pending_requesters,
                )

            if verify_lease_holders:
                # Immediately offer any reclaimed slots to waiting requests.
                await self._drain_queue()

            # Check continue-as-new threshold.
            # We use get_current_history_length() to account for timer loops
            # that don't increment self._event_count, or server suggestions.
            if (
                workflow.info().get_current_history_length()
                >= _MAX_EVENTS_BEFORE_CONTINUE_AS_NEW
                or workflow.info().is_continue_as_new_suggested()
            ):
                workflow.continue_as_new(self._build_continue_as_new_input())

            # Reset event flag and wait for new signals or periodic wake-up.
            self._has_new_events = False
            try:
                await workflow.wait_condition(
                    lambda: self._has_new_events or self._shutdown_requested,
                    timeout=timedelta(seconds=60),
                )
            except TimeoutError:
                # Expected: Periodic wake-up to clear expired cooldowns.
                pass

        return ProviderProfileManagerOutput(
            status="shutdown",
            runtime_id=self._runtime_id,
        )

    # -- Internal helpers ------------------------------------------------------

    def _restore_state(
        self,
        input_payload: dict[str, Any],
        *,
        repair_legacy_codex_oauth: bool = True,
    ) -> None:
        """Restore profile and lease state from input (e.g. after continue-as-new)."""
        profiles_data = input_payload.get("profiles", [])
        leases_data = input_payload.get("leases", {})
        cooldowns_data = input_payload.get("cooldowns", {})
        lease_times_data = input_payload.get("lease_granted_at", {})
        lease_metadata_data = input_payload.get("lease_metadata", {})
        pending_data = input_payload.get("pending_requests", [])
        reservations_data = input_payload.get("handoff_reservations", {})

        self._pending_requests = [
            PendingRequest(
                requester_workflow_id=req.get("requester_workflow_id", ""),
                runtime_id=req.get("runtime_id", ""),
                priority=self._normalize_request_priority(req.get("priority")),
                queue_order=self._normalize_queue_order(req.get("queue_order")),
                queued_at=self._normalize_optional_string(req.get("queued_at")),
                execution_profile_ref=req.get("execution_profile_ref"),
                profile_selector=req.get("profile_selector"),
                lease_group_id=self._normalize_optional_string(
                    req.get("lease_group_id")
                ),
                purpose=str(req.get("purpose") or "execution_direct"),
                lease_metadata=(
                    dict(req.get("lease_metadata") or {})
                    if isinstance(req.get("lease_metadata"), dict)
                    else {}
                ),
            )
            for req in pending_data
            if req.get("requester_workflow_id")
        ]
        self._pending_requests_ordered = False
        self._handoff_reservations = {}
        if isinstance(reservations_data, dict):
            for group_id, reservation in reservations_data.items():
                normalized_group_id = self._normalize_optional_string(group_id)
                if not normalized_group_id or not isinstance(reservation, dict):
                    continue
                profile_id = self._normalize_optional_string(
                    reservation.get("profile_id")
                )
                expires_at = self._normalize_optional_string(
                    reservation.get("expires_at")
                )
                if profile_id and expires_at:
                    self._handoff_reservations[normalized_group_id] = (
                        HandoffReservation(
                            profile_id=profile_id,
                            expires_at=expires_at,
                        )
                    )

        for p in profiles_data:
            pid = p["profile_id"]
            original_capacity = p.get("max_parallel_runs", 1)
            restored_credential_source = p.get("credential_source")
            if (
                repair_legacy_codex_oauth
                and restored_credential_source is None
                and self._runtime_id == "codex_cli"
                and p.get("runtime_materialization_mode") == "oauth_home"
            ):
                restored_credential_source = "oauth_volume"
            is_legacy_codex_oauth = _profile_is_codex_oauth(
                {
                    **p,
                    "credential_source": restored_credential_source,
                },
                runtime_id=self._runtime_id,
                infer_legacy_source=repair_legacy_codex_oauth,
            )
            state = ProfileSlotState(
                profile_id=pid,
                max_parallel_runs=_validated_profile_capacity(
                    p,
                    runtime_id=self._runtime_id,
                    repair_legacy=repair_legacy_codex_oauth,
                ),
                cooldown_after_429_seconds=p.get("cooldown_after_429_seconds", 900),
                rate_limit_policy=p.get("rate_limit_policy", "backoff"),
                enabled=p.get("enabled", True),
                launch_ready=p.get("launch_ready", p.get("launchReady", True)),
                is_default=p.get("is_default", False),
                max_lease_duration_seconds=p.get(
                    "max_lease_duration_seconds", _MAX_LEASE_DURATION_SECONDS
                ),
                current_leases=list(leases_data.get(pid, [])),
                lease_granted_at=dict(lease_times_data.get(pid, {})),
                lease_metadata=dict(lease_metadata_data.get(pid, {})),
                cooldown_until=cooldowns_data.get(pid),
                provider_id=p.get("provider_id"),
                credential_source=restored_credential_source,
                tags=p.get("tags") or [],
                priority=p.get("priority", 100),
                runtime_materialization_mode=p.get("runtime_materialization_mode"),
                input_per_million_usd=p.get("input_per_million_usd"),
                output_per_million_usd=p.get("output_per_million_usd"),
                pricing_source=p.get("pricing_source"),
                model_tiers=p.get("model_tiers") or [],
                default_model_tier=p.get("default_model_tier", 1),
                over_capacity_legacy_snapshot=(
                    is_legacy_codex_oauth and original_capacity != 1
                )
                or bool(p.get("over_capacity_legacy_snapshot", False)),
            )
            self._profiles[pid] = state

    def _apply_profile_sync(
        self,
        profiles_data: list[dict[str, Any]],
        *,
        authoritative: bool = False,
    ) -> None:
        """Merge a fresh profile list from the DB into in-memory state."""
        seen: set[str] = set()
        for p in profiles_data:
            pid = p["profile_id"]
            seen.add(pid)
            existing = self._profiles.get(pid)
            if existing:
                existing.max_parallel_runs = _validated_profile_capacity(
                    p,
                    runtime_id=self._runtime_id,
                    existing_capacity=existing.max_parallel_runs,
                )
                existing.cooldown_after_429_seconds = p.get(
                    "cooldown_after_429_seconds",
                    existing.cooldown_after_429_seconds,
                )
                existing.rate_limit_policy = p.get(
                    "rate_limit_policy", existing.rate_limit_policy
                )
                existing.enabled = p.get("enabled", existing.enabled)
                existing.launch_ready = p.get(
                    "launch_ready",
                    p.get("launchReady", existing.launch_ready),
                )
                existing.is_default = p.get("is_default", existing.is_default)
                existing.max_lease_duration_seconds = p.get(
                    "max_lease_duration_seconds", existing.max_lease_duration_seconds
                )
                existing.provider_id = p.get("provider_id", existing.provider_id)
                existing.credential_source = p.get(
                    "credential_source", existing.credential_source
                )
                existing.tags = p.get("tags") or existing.tags
                existing.priority = p.get("priority", existing.priority)
                existing.runtime_materialization_mode = p.get(
                    "runtime_materialization_mode",
                    existing.runtime_materialization_mode,
                )
                existing.model_tiers = p.get("model_tiers") or existing.model_tiers
                existing.default_model_tier = p.get(
                    "default_model_tier", existing.default_model_tier
                )
                self._apply_profile_pricing(existing, p)
                if authoritative:
                    existing.authoritative_policy_confirmed = True
                    existing.over_capacity_legacy_snapshot = (
                        len(existing.current_leases) > existing.max_parallel_runs
                    )
            else:
                pricing = pricing_from_profile_metadata(p)
                self._profiles[pid] = ProfileSlotState(
                    profile_id=pid,
                    max_parallel_runs=_validated_profile_capacity(
                        p,
                        runtime_id=self._runtime_id,
                    ),
                    cooldown_after_429_seconds=p.get("cooldown_after_429_seconds", 900),
                    rate_limit_policy=p.get("rate_limit_policy", "backoff"),
                    enabled=p.get("enabled", True),
                    launch_ready=p.get("launch_ready", p.get("launchReady", True)),
                    is_default=p.get("is_default", False),
                    max_lease_duration_seconds=p.get(
                        "max_lease_duration_seconds", _MAX_LEASE_DURATION_SECONDS
                    ),
                    provider_id=p.get("provider_id"),
                    credential_source=p.get("credential_source"),
                    tags=p.get("tags") or [],
                    priority=p.get("priority", 100),
                    runtime_materialization_mode=p.get("runtime_materialization_mode"),
                    input_per_million_usd=(
                        pricing.input_per_million_usd if pricing else None
                    ),
                    output_per_million_usd=(
                        pricing.output_per_million_usd if pricing else None
                    ),
                    pricing_source=pricing.source if pricing else None,
                    model_tiers=p.get("model_tiers") or [],
                    default_model_tier=p.get("default_model_tier", 1),
                    authoritative_policy_confirmed=authoritative,
                )

        # Disable profiles that were removed from DB (but don't drop leases).
        for pid in list(self._profiles.keys()):
            if pid not in seen:
                self._profiles[pid].enabled = False
                self._profiles[pid].is_default = False

    def _prune_disabled_profiles_without_leases(self) -> None:
        """Drop stale profile metadata that cannot still own runtime leases."""
        for pid, profile in list(self._profiles.items()):
            if not profile.enabled and not profile.current_leases:
                self._profiles.pop(pid, None)

    @staticmethod
    def _apply_profile_pricing(
        profile: ProfileSlotState,
        payload: dict[str, Any],
    ) -> None:
        pricing = pricing_from_profile_metadata(payload)
        if pricing is None:
            profile.input_per_million_usd = None
            profile.output_per_million_usd = None
            profile.pricing_source = None
            return
        profile.input_per_million_usd = pricing.input_per_million_usd
        profile.output_per_million_usd = pricing.output_per_million_usd
        profile.pricing_source = pricing.source

    @staticmethod
    def _normalize_optional_string(value: object) -> str | None:
        normalized = str(value or "").strip()
        return normalized or None

    @staticmethod
    def _normalize_request_priority(value: object) -> int:
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _normalize_queue_order(value: object) -> int | None:
        if value is None or isinstance(value, bool):
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _coerce_handoff_ttl_seconds(value: object) -> int:
        try:
            seconds = int(value or 0)
        except (TypeError, ValueError):
            return 0
        return max(0, min(seconds, _MAX_HANDOFF_RESERVATION_SECONDS))

    def _clear_expired_handoff_reservations(self, now: datetime) -> None:
        for group_id, reservation in list(self._handoff_reservations.items()):
            try:
                expires_at = datetime.fromisoformat(reservation.expires_at)
                if expires_at.tzinfo is None:
                    expires_at = expires_at.replace(tzinfo=timezone.utc)
            except (TypeError, ValueError):
                self._handoff_reservations.pop(group_id, None)
                continue
            if now >= expires_at:
                self._handoff_reservations.pop(group_id, None)

    def _reserved_slot_count_for_other_groups(
        self,
        profile_id: str,
        lease_group_id: str | None,
    ) -> int:
        reserved_slots = 0
        for reserved_group_id, reservation in self._handoff_reservations.items():
            if reservation.profile_id != profile_id:
                continue
            if reserved_group_id != lease_group_id:
                reserved_slots += 1
        return reserved_slots

    def _profile_id_for_lease(self, requester_workflow_id: str) -> str | None:
        for profile in self._profiles.values():
            if requester_workflow_id in profile.current_leases:
                return profile.profile_id
        return None

    def _has_available_profile(
        self,
        *,
        selector: Optional[dict[str, Any]],
        execution_profile_ref: str | None,
        lease_group_id: str | None,
    ) -> bool:
        return (
            self._find_available_profile(
                selector=selector,
                execution_profile_ref=execution_profile_ref,
                lease_group_id=lease_group_id,
            )
            is not None
        )

    @staticmethod
    def _profile_matches_request(
        profile: ProfileSlotState,
        *,
        selector: Optional[dict[str, Any]],
        exact_profile_id: str | None,
    ) -> bool:
        if not profile.is_available():
            return False
        if exact_profile_id and profile.profile_id != exact_profile_id:
            return False
        if not selector:
            return True
        if selector.get("providerId") and profile.provider_id != selector.get(
            "providerId"
        ):
            return False
        if selector.get(
            "runtimeMaterializationMode"
        ) and profile.runtime_materialization_mode != selector.get(
            "runtimeMaterializationMode"
        ):
            return False

        tags_any = selector.get("tagsAny", [])
        if tags_any and not set(tags_any).intersection(set(profile.tags)):
            return False

        tags_all = selector.get("tagsAll", [])
        if tags_all and not set(tags_all).issubset(set(profile.tags)):
            return False

        return True

    async def _drain_queue(self) -> None:
        """Try to assign slots to pending requests in priority order."""
        now = workflow.now()
        durable_grants = workflow.patched(DURABLE_LEASE_GRANT_PATCH)
        self._clear_expired_handoff_reservations(now)
        remaining: list[PendingRequest] = []
        leases_changed = False
        pending_requests = self._pending_requests
        if workflow.patched(PRIORITY_PENDING_REQUESTS_PATCH):
            pending_requests = sorted(
                self._pending_requests,
                key=lambda request: -request.priority,
            )
        if workflow.patched(QUEUE_ORDER_PENDING_REQUESTS_PATCH):
            pending_requests = sorted(
                self._pending_requests,
                key=self._pending_request_sort_key,
            )
        if workflow.patched(SCHEDULED_PENDING_REQUESTS_PATCH):
            pending_requests = await self._order_pending_requests_by_schedule()
        for req in pending_requests:
            # Check if this requester already has a lease (e.g. from a retried workflow task)
            existing_profile_id = None
            for p in self._profiles.values():
                if req.requester_workflow_id in p.current_leases:
                    existing_profile_id = p.profile_id
                    break

            if existing_profile_id:
                if durable_grants and not await self._sync_leases_to_db():
                    remaining.append(req)
                    continue
                try:
                    await self._signal_slot_assigned(
                        req.requester_workflow_id, existing_profile_id
                    )
                except Exception as e:
                    self._get_logger().warning(
                        "Failed to signal existing slot to %s: %s",
                        req.requester_workflow_id,
                        e,
                    )
                    if durable_grants:
                        # Signal failure is ambiguous. Keep the durable lease
                        # until workflow-status verification proves the owner
                        # terminal; releasing here could authorize a second
                        # credential consumer while the first is still alive.
                        remaining.append(req)
                    else:
                        self._profiles[existing_profile_id].release(
                            req.requester_workflow_id
                        )
                        leases_changed = True
                continue

            profile = self._find_available_profile(
                selector=req.profile_selector,
                execution_profile_ref=req.execution_profile_ref,
                lease_group_id=req.lease_group_id,
            )
            if profile and profile.reserve(
                req.requester_workflow_id,
                now,
                purpose=req.purpose,
                metadata=req.lease_metadata,
            ):
                leases_changed = True
                if durable_grants and not await self._sync_leases_to_db():
                    # Hold the in-memory reservation and retry persistence on
                    # the next loop. Never signal a consumer before its lease
                    # is durable.
                    remaining.append(req)
                    continue
                try:
                    await self._signal_slot_assigned(
                        req.requester_workflow_id, profile.profile_id
                    )
                except Exception as e:
                    self._get_logger().warning(
                        "Failed to signal slot_assigned to %s (likely completed or dead): %s",
                        req.requester_workflow_id,
                        e,
                    )
                    if durable_grants:
                        remaining.append(req)
                    else:
                        profile.release(req.requester_workflow_id)
                        leases_changed = True
            else:
                remaining.append(req)
        self._pending_requests = remaining
        self._pending_requests_ordered = True

        # Persist lease changes to DB for crash recovery
        if (
            leases_changed
            and not durable_grants
            and workflow.patched(DB_LEASE_PERSISTENCE_PATCH)
        ):
            await self._sync_leases_to_db()

    @staticmethod
    def _pending_request_sort_key(
        request: PendingRequest,
    ) -> tuple[int, int, int, str]:
        queued_at = request.queued_at or ""
        if request.queue_order is None:
            return (
                -request.priority,
                0,
                0,
                queued_at,
            )
        return (
            -request.priority,
            1,
            request.queue_order,
            queued_at,
        )

    def _pending_request_order_lookup_ids(self) -> list[str]:
        """Collect the parent/root queue-order keys for pending slot requests.

        Slot requests originate from ``MoonMind.AgentRun`` child workflows, but
        the visible queue order belongs to the parent/root workflow. ``lease_group_id``
        is derived from the parent workflow id when present, so it is the primary
        lookup key; the requester workflow id is used only as a fallback.
        """
        lookup_ids: list[str] = []
        for request in self._pending_requests:
            workflow_id = self._normalize_optional_string(
                request.lease_group_id or request.requester_workflow_id
            )
            if workflow_id:
                lookup_ids.append(workflow_id)
        return list(dict.fromkeys(lookup_ids))

    async def _order_pending_requests_by_schedule(self) -> list[PendingRequest]:
        """Order pending requests by existing scheduled queue order (MM-869).

        Resolves ``scheduled_for`` / ``created_at`` for each pending request's
        parent queue-order key via the ``provider_profile.pending_request_order``
        activity, then sorts by priority DESC, scheduled_for ASC, created_at ASC,
        queue-order key ASC, requester_workflow_id ASC.

        If the ordering lookup activity fails, this logs enough context to
        diagnose the failure and falls back to the deterministic queue-order /
        priority sort so slot assignment is never blocked for that drain cycle.

        Resolved orders are cached per queue-order workflow id (their scheduled
        and created timestamps are immutable), so each id is only looked up
        once; subsequent drain cycles reuse the cache instead of re-querying the
        database. The lookup is also bounded with a ``schedule_to_start_timeout``
        so that a starved activity task queue cannot leave available
        provider-profile slots idle indefinitely waiting on this best-effort,
        non-critical ordering call -- it times out and falls back to the
        deterministic queue-order drain instead.
        """
        lookup_ids = self._pending_request_order_lookup_ids()
        # Drop cached entries for ids that are no longer pending so the cache
        # stays bounded over the lifetime of this long-lived workflow.
        if self._resolved_orders:
            lookup_set = set(lookup_ids)
            self._resolved_orders = {
                workflow_id: order
                for workflow_id, order in self._resolved_orders.items()
                if workflow_id in lookup_set
            }
        uncached_ids = [
            workflow_id
            for workflow_id in lookup_ids
            if workflow_id not in self._resolved_orders
        ]
        if uncached_ids:
            try:
                result = await workflow.execute_activity(
                    "provider_profile.pending_request_order",
                    {"workflow_ids": uncached_ids},
                    task_queue=ACTIVITY_TASK_QUEUE,
                    schedule_to_start_timeout=timedelta(seconds=30),
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=RetryPolicy(
                        initial_interval=timedelta(seconds=2),
                        backoff_coefficient=2.0,
                        maximum_interval=timedelta(seconds=30),
                        maximum_attempts=3,
                    ),
                )
                orders = (result or {}).get("orders")
                if isinstance(orders, dict):
                    for workflow_id in uncached_ids:
                        resolved = orders.get(workflow_id)
                        self._resolved_orders[workflow_id] = (
                            resolved if isinstance(resolved, dict) else {}
                        )
            except Exception:
                self._get_logger().warning(
                    "pending_request_order activity failed; falling back to "
                    "queue-order drain for %d pending request(s) on runtime %s",
                    len(self._pending_requests),
                    self._runtime_id,
                )
                return sorted(
                    self._pending_requests,
                    key=self._pending_request_sort_key,
                )
        return sorted(
            self._pending_requests,
            key=lambda request: self._scheduled_pending_request_sort_key(
                request, self._resolved_orders
            ),
        )

    @classmethod
    def _scheduled_pending_request_sort_key(
        cls,
        request: PendingRequest,
        order_by_workflow_id: dict[str, dict[str, Any]],
    ) -> tuple[int, str, str, str, str]:
        workflow_id = (
            cls._normalize_optional_string(
                request.lease_group_id or request.requester_workflow_id
            )
            or ""
        )
        ordering = order_by_workflow_id.get(workflow_id) or {}
        scheduled_for = (
            cls._normalize_optional_string(ordering.get("scheduled_for"))
            or _FAR_FUTURE_ORDER_VALUE
        )
        created_at = (
            cls._normalize_optional_string(ordering.get("created_at"))
            or _FAR_FUTURE_ORDER_VALUE
        )
        return (
            -request.priority,
            scheduled_for,
            created_at,
            workflow_id,
            request.requester_workflow_id or "",
        )

    @staticmethod
    def _normalize_selector(
        selector: Optional[dict[str, Any]],
    ) -> Optional[dict[str, Any]]:
        if not selector:
            return None
        normalized: dict[str, Any] = {}
        for key, value in selector.items():
            if value is None:
                continue
            if isinstance(value, str) and not value.strip():
                continue
            if isinstance(value, list) and not value:
                continue
            normalized[key] = value
        return normalized or None

    def _find_available_profile(
        self,
        selector: Optional[dict[str, Any]] = None,
        execution_profile_ref: str | None = None,
        lease_group_id: str | None = None,
    ) -> Optional[ProfileSlotState]:
        """Find the best available profile matching the selector."""
        selector = self._normalize_selector(selector)
        allow_default_fallback = False
        if selector:
            allow_default_fallback = bool(selector.pop("allowDefaultFallback", False))
            selector = selector or None
        exact_profile_id = str(execution_profile_ref or "").strip()
        normalized_group_id = self._normalize_optional_string(lease_group_id)
        if normalized_group_id:
            reservation = self._handoff_reservations.get(normalized_group_id)
            if reservation:
                reserved_profile = self._profiles.get(reservation.profile_id)
                if reserved_profile and self._profile_matches_request(
                    reserved_profile,
                    selector=selector,
                    exact_profile_id=exact_profile_id,
                ):
                    self._handoff_reservations.pop(normalized_group_id, None)
                    return reserved_profile
                self._handoff_reservations.pop(normalized_group_id, None)

        if exact_profile_id:
            exact_profile = self._profiles.get(exact_profile_id)
            if exact_profile is None or not exact_profile.is_available():
                return None
            reserved_slots = self._reserved_slot_count_for_other_groups(
                exact_profile.profile_id, normalized_group_id
            )
            if exact_profile.available_slots <= reserved_slots:
                return None
            return (
                exact_profile
                if self._profile_matches_request(
                    exact_profile,
                    selector=selector,
                    exact_profile_id=exact_profile_id,
                )
                else None
            )

        eligible_profiles: list[ProfileSlotState] = []
        for profile in self._profiles.values():
            if not profile.is_available():
                continue
            reserved_slots = self._reserved_slot_count_for_other_groups(
                profile.profile_id, normalized_group_id
            )
            if profile.available_slots <= reserved_slots:
                continue
            if not self._profile_matches_request(
                profile,
                selector=selector,
                exact_profile_id=None,
            ):
                continue

            eligible_profiles.append(profile)

        if not eligible_profiles:
            return None

        if not selector:
            configured_default_profiles = [
                profile
                for profile in self._profiles.values()
                if profile.is_default and profile.enabled and profile.launch_ready
            ]
            default_profiles = [
                profile for profile in eligible_profiles if profile.is_default
            ]
            if workflow.patched(DEFAULT_PROFILE_EXCLUSIVE_SELECTION_PATCH):
                if allow_default_fallback:
                    self._sort_profiles_for_selection(eligible_profiles)
                    return eligible_profiles[0]
                if default_profiles:
                    eligible_profiles = default_profiles
                elif configured_default_profiles:
                    return None
                elif len(eligible_profiles) == 1:
                    return eligible_profiles[0]
                self._sort_profiles_for_selection(eligible_profiles)
                return eligible_profiles[0]
            if len(default_profiles) == 1:
                return default_profiles[0]
            if len(eligible_profiles) == 1:
                return eligible_profiles[0]
            if not default_profiles:
                # Preserve lease assignment for in-flight manager state restored
                # from payloads created before is_default existed.
                eligible_profiles.sort(
                    key=lambda p: (p.priority, p.available_slots),
                    reverse=True,
                )
                return eligible_profiles[0]
            return None

        self._sort_profiles_for_selection(eligible_profiles)
        return eligible_profiles[0]

    @staticmethod
    def _billing_sort_key(profile: ProfileSlotState) -> tuple[int, float, int, int]:
        blended_price = profile.blended_per_million_usd
        has_price = 0 if blended_price is not None else 1
        price = blended_price if blended_price is not None else float("inf")
        return (has_price, price, -profile.priority, -profile.available_slots)

    @staticmethod
    def _workflow_patch_enabled(patch_id: str) -> bool:
        try:
            return workflow.patched(patch_id)
        except Exception:
            return False

    def _sort_profiles_for_selection(self, profiles: list[ProfileSlotState]) -> None:
        profiles.sort(key=lambda p: (p.priority, p.available_slots), reverse=True)

    async def _signal_slot_assigned(
        self, requester_workflow_id: str, profile_id: str
    ) -> None:
        """Send a slot_assigned signal to the requesting AgentRun workflow."""
        handle = workflow.get_external_workflow_handle(requester_workflow_id)
        await handle.signal("slot_assigned", {"profile_id": profile_id})

    def _evict_expired_leases(self) -> int:
        """Remove leases held longer than the max duration. Returns total eviction count."""
        now = workflow.now()
        total_evicted = 0
        for profile in self._profiles.values():
            max_duration = (
                getattr(profile, "max_lease_duration_seconds", None)
                or _MAX_LEASE_DURATION_SECONDS
            )
            evicted = profile.evict_expired_leases(now, max_duration)
            total_evicted += len(evicted)
            for wf_id in evicted:
                self._get_logger().warning(
                    "Evicted stale lease for profile %s held by %s",
                    profile.profile_id,
                    wf_id,
                )
        return total_evicted

    def _lease_holder_workflow_ids(self) -> list[str]:
        """Return unique workflow IDs that currently hold profile leases."""
        all_wf_ids: list[str] = []
        for profile in self._profiles.values():
            for lease_id in profile.current_leases:
                metadata = profile.lease_metadata.get(lease_id) or {}
                if metadata.get("ownerIsWorkflow") is False:
                    continue
                all_wf_ids.append(str(metadata.get("workflowId") or lease_id))
        return list(dict.fromkeys(all_wf_ids))

    def _pending_requester_workflow_ids(self) -> list[str]:
        """Return unique workflow IDs with pending slot requests."""
        return list(
            dict.fromkeys(req.requester_workflow_id for req in self._pending_requests)
        )

    async def _verify_workflow_statuses(
        self, workflow_ids: list[str]
    ) -> dict[str, dict[str, Any]] | None:
        """Fetch workflow running status in bounded batches."""
        unique_workflow_ids = list(dict.fromkeys(workflow_ids))
        if not unique_workflow_ids:
            return {}

        statuses: dict[str, dict[str, Any]] = {}
        for start in range(
            0,
            len(unique_workflow_ids),
            _VERIFY_WORKFLOW_STATUS_BATCH_SIZE,
        ):
            batch = unique_workflow_ids[
                start : start + _VERIFY_WORKFLOW_STATUS_BATCH_SIZE
            ]
            try:
                result = await workflow.execute_activity(
                    "provider_profile.verify_lease_holders",
                    {"workflow_ids": batch},
                    task_queue=ACTIVITY_TASK_QUEUE,
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=RetryPolicy(
                        initial_interval=timedelta(seconds=2),
                        backoff_coefficient=2.0,
                        maximum_interval=timedelta(seconds=30),
                        maximum_attempts=3,
                    ),
                )
            except Exception:
                self._get_logger().warning(
                    "verify_lease_holders activity failed, skipping verification cycle"
                )
                return None
            statuses.update(result or {})
        return statuses

    def _reclaim_terminal_leases(
        self, workflow_statuses: dict[str, dict[str, Any]]
    ) -> bool:
        """Remove leases held by workflows that are in a terminal state."""
        reclaimed = False

        for profile in list(self._profiles.values()):
            for wf_id in list(profile.current_leases):
                metadata = profile.lease_metadata.get(wf_id) or {}
                if metadata.get("ownerIsWorkflow") is False:
                    continue
                owner_workflow_id = str(metadata.get("workflowId") or wf_id)
                status_info = workflow_statuses.get(owner_workflow_id, {})
                if not status_info.get("running", True):
                    profile.release(wf_id)
                    reclaimed = True
                    self._get_logger().warning(
                        "Reclaimed slot for profile %s from terminated workflow %s (status=%s)",
                        profile.profile_id,
                        wf_id,
                        status_info.get("status", "UNKNOWN"),
                    )

        return reclaimed

    def _prune_terminal_pending_requesters(
        self, workflow_statuses: dict[str, dict[str, Any]]
    ) -> int:
        """Remove pending slot requests whose requester workflows are terminal."""
        remaining: list[PendingRequest] = []
        removed_count = 0
        for request in self._pending_requests:
            status_info = workflow_statuses.get(request.requester_workflow_id, {})
            if status_info.get("running", True):
                remaining.append(request)
                continue
            removed_count += 1
            self._get_logger().warning(
                "Pruned pending slot request for terminal workflow %s (status=%s)",
                request.requester_workflow_id,
                status_info.get("status", "UNKNOWN"),
            )

        if removed_count:
            self._pending_requests = remaining
        return removed_count

    async def _verify_active_workflows(
        self,
        *,
        verify_lease_holders: bool,
        verify_pending_requesters: bool,
    ) -> None:
        """Verify lease holders and pending requesters with one status pass."""
        workflow_ids: list[str] = []
        if verify_lease_holders:
            workflow_ids.extend(self._lease_holder_workflow_ids())
        if verify_pending_requesters:
            workflow_ids.extend(self._pending_requester_workflow_ids())

        workflow_statuses = await self._verify_workflow_statuses(workflow_ids)
        if workflow_statuses is None:
            return

        reclaimed = False
        if verify_lease_holders:
            reclaimed = self._reclaim_terminal_leases(workflow_statuses)
        if verify_pending_requesters:
            self._prune_terminal_pending_requesters(workflow_statuses)

        if reclaimed and workflow.patched(DB_LEASE_PERSISTENCE_PATCH):
            await self._sync_leases_to_db()

    async def _verify_lease_holders(self) -> None:
        """Remove leases held by workflows that are in a terminal state.

        Uses the verify_lease_holders activity to check whether each lease-holding
        workflow is still running. This allows faster reclaim of slots from
        cancelled/terminated workflows without waiting for the lease duration timeout.
        """
        workflow_statuses = await self._verify_workflow_statuses(
            self._lease_holder_workflow_ids()
        )
        if workflow_statuses is None:
            return

        reclaimed = self._reclaim_terminal_leases(workflow_statuses)
        if reclaimed and workflow.patched(DB_LEASE_PERSISTENCE_PATCH):
            await self._sync_leases_to_db()

    async def _verify_pending_requesters(self) -> int:
        """Remove pending slot requests whose requester workflows are terminal."""
        workflow_statuses = await self._verify_workflow_statuses(
            self._pending_requester_workflow_ids()
        )
        if workflow_statuses is None:
            return 0

        return self._prune_terminal_pending_requesters(workflow_statuses)

    def _clear_expired_cooldowns(self) -> None:
        """Remove cooldown markers that have expired."""
        now = workflow.now()
        for profile in self._profiles.values():
            if profile.cooldown_until is not None:
                try:
                    cooldown_dt = datetime.fromisoformat(profile.cooldown_until)
                    if cooldown_dt.tzinfo is None:
                        cooldown_dt = cooldown_dt.replace(tzinfo=timezone.utc)
                    if now >= cooldown_dt:
                        profile.cooldown_until = None
                except (ValueError, TypeError):
                    profile.cooldown_until = None

    def _build_continue_as_new_input(self) -> dict[str, Any]:
        """Serialize current state for continue-as-new."""
        profiles_list = []
        leases: dict[str, list[str]] = {}
        cooldowns: dict[str, str] = {}
        lease_times: dict[str, dict[str, str]] = {}
        lease_metadata: dict[str, dict[str, dict[str, Any]]] = {}

        for pid, state in self._profiles.items():
            profiles_list.append(
                {
                    "profile_id": pid,
                    "max_parallel_runs": state.max_parallel_runs,
                    "cooldown_after_429_seconds": state.cooldown_after_429_seconds,
                    "rate_limit_policy": state.rate_limit_policy,
                    "enabled": state.enabled,
                    "is_default": state.is_default,
                    "max_lease_duration_seconds": state.max_lease_duration_seconds,
                    "provider_id": state.provider_id,
                    "credential_source": state.credential_source,
                    "tags": list(state.tags),
                    "priority": state.priority,
                    "runtime_materialization_mode": state.runtime_materialization_mode,
                    "input_per_million_usd": state.input_per_million_usd,
                    "output_per_million_usd": state.output_per_million_usd,
                    "pricing_source": state.pricing_source,
                    "over_capacity_legacy_snapshot": (
                        state.over_capacity_legacy_snapshot
                    ),
                }
            )
            if state.current_leases:
                leases[pid] = list(state.current_leases)
            if state.lease_granted_at:
                lease_times[pid] = dict(state.lease_granted_at)
            if state.lease_metadata:
                lease_metadata[pid] = dict(state.lease_metadata)
            if state.cooldown_until:
                cooldowns[pid] = state.cooldown_until

        return {
            "runtime_id": self._runtime_id,
            "profiles": profiles_list,
            "leases": leases,
            "lease_granted_at": lease_times,
            "lease_metadata": lease_metadata,
            "cooldowns": cooldowns,
            "pending_requests": [
                {
                    "requester_workflow_id": r.requester_workflow_id,
                    "runtime_id": r.runtime_id,
                    "priority": r.priority,
                    "queue_order": r.queue_order,
                    "queued_at": r.queued_at,
                    "execution_profile_ref": r.execution_profile_ref,
                    "profile_selector": r.profile_selector,
                    "lease_group_id": r.lease_group_id,
                    **(
                        {
                            "purpose": r.purpose,
                            "lease_metadata": dict(r.lease_metadata),
                        }
                        if self._purpose_aware_leases
                        else {}
                    ),
                }
                for r in self._pending_requests
            ],
            "handoff_reservations": {
                group_id: {
                    "profile_id": reservation.profile_id,
                    "expires_at": reservation.expires_at,
                }
                for group_id, reservation in self._handoff_reservations.items()
            },
        }

    async def _load_profiles_from_db(
        self, *, prune_removed_profiles: bool = False
    ) -> bool:
        """Load provider profiles for this runtime from the database via activity."""
        self._profile_refresh_requested = False
        try:
            result = await workflow.execute_activity(
                "provider_profile.list",
                {"runtime_id": self._runtime_id},
                task_queue=ACTIVITY_TASK_QUEUE,
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=RetryPolicy(
                    initial_interval=timedelta(seconds=2),
                    backoff_coefficient=2.0,
                    maximum_interval=timedelta(seconds=30),
                    maximum_attempts=5,
                ),
            )
            profiles_data = result.get("profiles", []) if result else []
            self._apply_profile_sync(profiles_data, authoritative=True)
            if prune_removed_profiles:
                self._prune_disabled_profiles_without_leases()
            self._has_db_profile_snapshot = True
            return True
        except Exception:
            self._profile_refresh_requested = True
            self._get_logger().warning(
                "Failed to refresh provider profiles from DB for runtime %s",
                self._runtime_id,
            )
            return False

    async def _sync_leases_to_db(self) -> bool:
        """Persist current lease state to the database for crash recovery."""
        try:
            leases = []
            for profile in self._profiles.values():
                for wf_id in profile.current_leases:
                    leases.append(
                        {
                            "workflow_id": wf_id,
                            "profile_id": profile.profile_id,
                            "granted_at": profile.lease_granted_at.get(wf_id),
                            "profileId": profile.profile_id,
                            "runtimeId": self._runtime_id,
                            **dict(profile.lease_metadata.get(wf_id) or {}),
                        }
                    )
            await workflow.execute_activity(
                "provider_profile.sync_slot_leases",
                {"runtime_id": self._runtime_id, "leases": leases, "action": "save"},
                task_queue=ACTIVITY_TASK_QUEUE,
                start_to_close_timeout=timedelta(seconds=10),
                retry_policy=RetryPolicy(
                    initial_interval=timedelta(seconds=1),
                    backoff_coefficient=2.0,
                    maximum_interval=timedelta(seconds=10),
                    maximum_attempts=3,
                ),
            )
            return True
        except Exception:
            self._get_logger().warning(
                "Failed to persist leases to DB; provider capacity remains blocked"
            )
            return False

    async def _remove_lease_from_db(self, workflow_id: str) -> None:
        """Remove a single lease from the database."""
        try:
            await workflow.execute_activity(
                "provider_profile.sync_slot_leases",
                {
                    "runtime_id": self._runtime_id,
                    "leases": [{"workflow_id": workflow_id}],
                    "action": "remove",
                },
                task_queue=ACTIVITY_TASK_QUEUE,
                start_to_close_timeout=timedelta(seconds=10),
                retry_policy=RetryPolicy(
                    initial_interval=timedelta(seconds=1),
                    backoff_coefficient=2.0,
                    maximum_interval=timedelta(seconds=10),
                    maximum_attempts=3,
                ),
            )
        except Exception:
            self._get_logger().warning(
                "Failed to remove lease for %s from DB", workflow_id
            )

    async def _load_leases_from_db(self) -> bool:
        """Load persisted leases from DB and reconnect to running workflows.

        On manager startup (after a crash), we load leases from the DB and
        send slot_assigned to any workflows that had active leases. This
        prevents workflows from being orphaned when the manager restarts.

        This method is called after profiles are loaded on startup.
        """
        try:
            result = await workflow.execute_activity(
                "provider_profile.sync_slot_leases",
                {"runtime_id": self._runtime_id, "action": "load"},
                task_queue=ACTIVITY_TASK_QUEUE,
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=RetryPolicy(
                    initial_interval=timedelta(seconds=2),
                    backoff_coefficient=2.0,
                    maximum_interval=timedelta(seconds=30),
                    maximum_attempts=3,
                ),
            )
            leases = result.get("leases", []) if result else []

            if not leases:
                self._get_logger().info(
                    "No persisted leases found in DB for runtime %s", self._runtime_id
                )
                return True

            self._get_logger().info(
                "Restoring %d persisted leases from DB for runtime %s",
                len(leases),
                self._runtime_id,
            )

            # Reconnect to each workflow that had a lease.
            # We send slot_assigned with the persisted profile_id.
            # The workflow will either:
            # - Already have a slot and ignore the duplicate signal
            # - Be waiting and receive the slot assignment
            # - Have a mismatch and re-request if needed
            for lease in leases:
                wf_id = lease.get("workflow_id")
                profile_id = lease.get("profile_id")
                if not wf_id or not profile_id:
                    continue

                # Check if this profile still exists. A disabled profile must
                # still retain an existing lease; disabled only prevents new
                # grants. If the profile is missing entirely, the manager
                # cannot safely establish the credential authority boundary.
                profile = self._profiles.get(profile_id)
                purpose = str(lease.get("purpose") or "execution_direct")
                is_maintenance = purpose not in {
                    CredentialLeasePurpose.EXECUTION_DIRECT.value,
                    CredentialLeasePurpose.EXECUTION_OMNIGENT.value,
                }
                durable_grants = workflow.patched(DURABLE_LEASE_GRANT_PATCH)
                if not profile:
                    self._get_logger().warning(
                        "Persisted lease for %s references unknown profile %s",
                        wf_id,
                        profile_id,
                    )
                    if durable_grants:
                        return False
                    continue
                if not durable_grants and not profile.enabled and not is_maintenance:
                    self._get_logger().warning(
                        "Persisted lease for %s references disabled profile %s, skipping",
                        wf_id,
                        profile_id,
                    )
                    continue

                # Restore the lease to the profile's current_leases
                if wf_id not in profile.current_leases:
                    profile.current_leases.append(wf_id)
                    granted_at = lease.get("granted_at")
                    if granted_at:
                        profile.lease_granted_at[wf_id] = granted_at
                    profile.lease_metadata[wf_id] = {
                        "leaseId": lease.get("leaseId") or wf_id,
                        "ownerId": lease.get("ownerId") or wf_id,
                        "purpose": purpose,
                        **{
                            key: lease[key]
                            for key in (
                                "workflowId",
                                "stepExecutionId",
                                "oauthSessionId",
                                "idempotencyKey",
                                "ownerIsWorkflow",
                            )
                            if lease.get(key) is not None
                        },
                    }

                # Send slot_assigned to the workflow to reconnect
                if is_maintenance:
                    continue
                try:
                    await self._signal_slot_assigned(wf_id, profile_id)
                    self._get_logger().info(
                        "Restored lease: %s -> profile %s", wf_id, profile_id
                    )
                except Exception as e:
                    self._get_logger().warning(
                        "Failed to reconnect to workflow %s: %s", wf_id, e
                    )
                    if not durable_grants:
                        # Preserve the old behavior for replaying histories
                        # recorded before ambiguous reconnect failures became
                        # fail-closed.
                        profile.release(wf_id)
                        await self._remove_lease_from_db(wf_id)

            return True

        except Exception:
            self._get_logger().warning(
                "Failed to load leases from DB; refusing unverified capacity"
            )
            return False
