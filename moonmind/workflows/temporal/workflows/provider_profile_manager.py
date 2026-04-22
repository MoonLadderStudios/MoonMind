"""Singleton per-runtime-family provider profile manager workflow.

Each managed agent runtime family (gemini_cli, claude_code, codex_cli) gets its
own long-lived ProviderProfileManager workflow instance. The manager owns the truth
about slot leases — which profiles have available capacity and which are in
cooldown — and assigns slots to AgentRun workflows via Temporal Signals.

Workflow ID convention: ``provider-profile-manager:<runtime_id>``
  e.g. ``provider-profile-manager:gemini_cli``

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

WORKFLOW_NAME = "MoonMind.ProviderProfileManager"
WORKFLOW_TASK_QUEUE = "mm.workflow"
ACTIVITY_TASK_QUEUE = "mm.activity.artifacts"
WORKFLOW_ID_PREFIX = "provider-profile-manager"

# Replay patch IDs are durable Temporal history markers. Preserve legacy
# "auth-profile" spellings in identifiers until a deliberate workflow migration.
VERIFY_LEASE_HOLDERS_PATCH = "auth-profile-manager-verify-leases-v1"
DB_LEASE_PERSISTENCE_PATCH = "provider-profile-manager-db-lease-persistence-v1"
SLOT_HANDOFF_RESERVATION_PATCH = "provider-profile-manager-slot-handoff-v1"
REFRESH_RESTORED_PROFILES_PATCH = "provider-profile-manager-refresh-restored-profiles-v1"
DB_AUTHORITATIVE_PROFILE_SYNC_PATCH = (
    "provider-profile-manager-db-authoritative-profile-sync-v1"
)
VERIFY_PENDING_REQUESTS_PATCH = "provider-profile-manager-verify-pending-requests-v1"
DEFAULT_PROFILE_EXCLUSIVE_SELECTION_PATCH = (
    "provider-profile-manager-default-profile-exclusive-selection-v1"
)

# Continue-as-new threshold to bound history growth.
_MAX_EVENTS_BEFORE_CONTINUE_AS_NEW = 2000
_VERIFY_WORKFLOW_STATUS_BATCH_SIZE = 100

logger = logging.getLogger(__name__)


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
    pending_requests: list[dict[str, str]]
    handoff_reservations: dict[str, dict[str, str]]


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
    execution_profile_ref: str | None
    lease_group_id: str | None


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
    is_default: bool = False
    max_lease_duration_seconds: int = _MAX_LEASE_DURATION_SECONDS
    current_leases: list[str] = field(default_factory=list)
    lease_granted_at: dict[str, str] = field(default_factory=dict)  # wf_id -> ISO ts
    cooldown_until: Optional[str] = None  # ISO timestamp string or None
    provider_id: Optional[str] = None
    tags: list[str] = field(default_factory=list)
    priority: int = 100
    runtime_materialization_mode: Optional[str] = None

    @property
    def available_slots(self) -> int:
        if not self.enabled:
            return 0
        return max(0, self.max_parallel_runs - len(self.current_leases))

    def is_available(self) -> bool:
        if not self.enabled or self.available_slots <= 0:
            return False
        if self.cooldown_until is not None:
            return False
        return True

    def reserve(self, requester_workflow_id: str, now: datetime) -> bool:
        if not self.is_available():
            return False
        self.current_leases.append(requester_workflow_id)
        self.lease_granted_at[requester_workflow_id] = now.isoformat()
        return True

    def release(self, requester_workflow_id: str) -> bool:
        if requester_workflow_id in self.current_leases:
            self.current_leases.remove(requester_workflow_id)
            self.lease_granted_at.pop(requester_workflow_id, None)
            return True
        return False

    def evict_expired_leases(self, now: datetime, max_duration_seconds: int) -> list[str]:
        """Remove leases that have exceeded the maximum duration. Returns evicted IDs."""
        evicted: list[str] = []
        for wf_id in list(self.current_leases):
            granted_str = self.lease_granted_at.get(wf_id)
            if granted_str is None:
                # Legacy lease without timestamp — evict it as we can't verify age.
                self.current_leases.remove(wf_id)
                evicted.append(wf_id)
                continue
            try:
                granted_dt = datetime.fromisoformat(granted_str)
                if granted_dt.tzinfo is None:
                    granted_dt = granted_dt.replace(tzinfo=timezone.utc)
                if (now - granted_dt).total_seconds() > max_duration_seconds:
                    self.current_leases.remove(wf_id)
                    self.lease_granted_at.pop(wf_id, None)
                    evicted.append(wf_id)
            except (ValueError, TypeError):
                self.current_leases.remove(wf_id)
                self.lease_granted_at.pop(wf_id, None)
                evicted.append(wf_id)
        return evicted

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "max_parallel_runs": self.max_parallel_runs,
            "cooldown_after_429_seconds": self.cooldown_after_429_seconds,
            "rate_limit_policy": self.rate_limit_policy,
            "enabled": self.enabled,
            "is_default": self.is_default,
            "max_lease_duration_seconds": self.max_lease_duration_seconds,
            "current_leases": list(self.current_leases),
            "lease_granted_at": dict(self.lease_granted_at),
            "cooldown_until": self.cooldown_until,
            "provider_id": self.provider_id,
            "tags": list(self.tags),
            "priority": self.priority,
            "runtime_materialization_mode": self.runtime_materialization_mode,
        }


@dataclass
class PendingRequest:
    """A queued slot request waiting for assignment."""

    requester_workflow_id: str
    runtime_id: str
    execution_profile_ref: str | None = None
    profile_selector: Optional[dict[str, Any]] = None
    lease_group_id: str | None = None


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
            logging.getLogger(__name__).exception("Error getting workflow info in _get_logger")
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
            logging.getLogger(__name__).exception("Error checking logger capabilities in _get_logger")
            return logging.LoggerAdapter(logging.getLogger(__name__), extra=extra)

    def __init__(self) -> None:
        self._runtime_id: Optional[str] = None
        self._profiles: dict[str, ProfileSlotState] = {}
        self._pending_requests: list[PendingRequest] = []
        self._handoff_reservations: dict[str, HandoffReservation] = {}
        self._event_count: int = 0
        self._shutdown_requested: bool = False
        self._has_new_events: bool = False
        self._profile_refresh_requested: bool = False
        self._has_db_profile_snapshot: bool = False

    # -- Signals ---------------------------------------------------------------

    @workflow.signal
    def request_slot(self, payload: dict[str, Any]) -> None:
        """An AgentRun requests a profile slot for this runtime family."""
        self._event_count += 1
        self._has_new_events = True
        if not workflow.patched(SLOT_HANDOFF_RESERVATION_PATCH):
            self._pending_requests.append(
                PendingRequest(
                    requester_workflow_id=payload["requester_workflow_id"],
                    runtime_id=payload.get("runtime_id", self._runtime_id or ""),
                    execution_profile_ref=payload.get("execution_profile_ref"),
                    profile_selector=payload.get("profile_selector"),
                )
            )
            return
        request = PendingRequest(
            requester_workflow_id=payload["requester_workflow_id"],
            runtime_id=payload.get("runtime_id", self._runtime_id or ""),
            execution_profile_ref=payload.get("execution_profile_ref"),
            profile_selector=payload.get("profile_selector"),
            lease_group_id=self._normalize_optional_string(
                payload.get("lease_group_id")
            ),
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

    # -- Queries ---------------------------------------------------------------

    @workflow.query
    def get_state(self) -> dict[str, Any]:
        """Return current manager state for observability."""
        return {
            "runtime_id": self._runtime_id,
            "profiles": {
                pid: p.to_dict() for pid, p in self._profiles.items()
            },
            "pending_requests": [
                {
                    "requester_workflow_id": r.requester_workflow_id,
                    "runtime_id": r.runtime_id,
                    "execution_profile_ref": r.execution_profile_ref,
                    "profile_selector": r.profile_selector,
                    "lease_group_id": r.lease_group_id,
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
            "event_count": self._event_count,
        }

    # -- Main loop -------------------------------------------------------------

    @workflow.run
    async def run(
        self, input_payload: dict[str, Any]
    ) -> ProviderProfileManagerOutput:
        self._runtime_id = input_payload.get("runtime_id")
        if not self._runtime_id:
            raise exceptions.ApplicationError(
                "runtime_id is required", non_retryable=True
            )

        # Restore state from continue-as-new or initial profile load.
        self._restore_state(input_payload)

        # If no profiles were provided, load them via activity.
        if not self._profiles:
            await self._load_profiles_from_db()

        # If we restored state from a crash (not continue-as-new), the
        # pending_requests and current_leases would be empty in the input.
        # In that case, try to restore leases from the database.
        if workflow.patched(DB_LEASE_PERSISTENCE_PATCH):
            has_leases = any(p.current_leases for p in self._profiles.values())
            has_pending = bool(self._pending_requests)
            if not has_leases and not has_pending:
                # This looks like a fresh start after a crash - try to restore leases from DB
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
                                lambda: self._has_new_events
                                or self._shutdown_requested,
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
            if workflow.info().get_current_history_length() >= _MAX_EVENTS_BEFORE_CONTINUE_AS_NEW or workflow.info().is_continue_as_new_suggested():
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

    def _restore_state(self, input_payload: dict[str, Any]) -> None:
        """Restore profile and lease state from input (e.g. after continue-as-new)."""
        profiles_data = input_payload.get("profiles", [])
        leases_data = input_payload.get("leases", {})
        cooldowns_data = input_payload.get("cooldowns", {})
        lease_times_data = input_payload.get("lease_granted_at", {})
        pending_data = input_payload.get("pending_requests", [])
        reservations_data = input_payload.get("handoff_reservations", {})

        self._pending_requests = [
            PendingRequest(
                requester_workflow_id=req.get("requester_workflow_id", ""),
                runtime_id=req.get("runtime_id", ""),
                execution_profile_ref=req.get("execution_profile_ref"),
                profile_selector=req.get("profile_selector"),
                lease_group_id=self._normalize_optional_string(
                    req.get("lease_group_id")
                ),
            )
            for req in pending_data
            if req.get("requester_workflow_id")
        ]
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
            state = ProfileSlotState(
                profile_id=pid,
                max_parallel_runs=p.get("max_parallel_runs", 1),
                cooldown_after_429_seconds=p.get("cooldown_after_429_seconds", 900),
                rate_limit_policy=p.get("rate_limit_policy", "backoff"),
                enabled=p.get("enabled", True),
                is_default=p.get("is_default", False),
                max_lease_duration_seconds=p.get(
                    "max_lease_duration_seconds", _MAX_LEASE_DURATION_SECONDS
                ),
                current_leases=list(leases_data.get(pid, [])),
                lease_granted_at=dict(lease_times_data.get(pid, {})),
                cooldown_until=cooldowns_data.get(pid),
                provider_id=p.get("provider_id"),
                tags=p.get("tags") or [],
                priority=p.get("priority", 100),
                runtime_materialization_mode=p.get("runtime_materialization_mode"),
            )
            self._profiles[pid] = state

    def _apply_profile_sync(self, profiles_data: list[dict[str, Any]]) -> None:
        """Merge a fresh profile list from the DB into in-memory state."""
        seen: set[str] = set()
        for p in profiles_data:
            pid = p["profile_id"]
            seen.add(pid)
            existing = self._profiles.get(pid)
            if existing:
                existing.max_parallel_runs = p.get(
                    "max_parallel_runs", existing.max_parallel_runs
                )
                existing.cooldown_after_429_seconds = p.get(
                    "cooldown_after_429_seconds",
                    existing.cooldown_after_429_seconds,
                )
                existing.rate_limit_policy = p.get(
                    "rate_limit_policy", existing.rate_limit_policy
                )
                existing.enabled = p.get("enabled", existing.enabled)
                existing.is_default = p.get("is_default", existing.is_default)
                existing.max_lease_duration_seconds = p.get(
                    "max_lease_duration_seconds", existing.max_lease_duration_seconds
                )
                existing.provider_id = p.get("provider_id", existing.provider_id)
                existing.tags = p.get("tags") or existing.tags
                existing.priority = p.get("priority", existing.priority)
                existing.runtime_materialization_mode = p.get(
                    "runtime_materialization_mode", existing.runtime_materialization_mode
                )
            else:
                self._profiles[pid] = ProfileSlotState(
                    profile_id=pid,
                    max_parallel_runs=p.get("max_parallel_runs", 1),
                    cooldown_after_429_seconds=p.get(
                        "cooldown_after_429_seconds", 900
                    ),
                    rate_limit_policy=p.get("rate_limit_policy", "backoff"),
                    enabled=p.get("enabled", True),
                    is_default=p.get("is_default", False),
                    max_lease_duration_seconds=p.get(
                        "max_lease_duration_seconds", _MAX_LEASE_DURATION_SECONDS
                    ),
                    provider_id=p.get("provider_id"),
                    tags=p.get("tags") or [],
                    priority=p.get("priority", 100),
                    runtime_materialization_mode=p.get("runtime_materialization_mode"),
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
    def _normalize_optional_string(value: object) -> str | None:
        normalized = str(value or "").strip()
        return normalized or None

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
        if (
            selector.get("providerId")
            and profile.provider_id != selector.get("providerId")
        ):
            return False
        if (
            selector.get("runtimeMaterializationMode")
            and profile.runtime_materialization_mode
            != selector.get("runtimeMaterializationMode")
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
        """Try to assign slots to pending requests in FIFO order."""
        now = workflow.now()
        self._clear_expired_handoff_reservations(now)
        remaining: list[PendingRequest] = []
        leases_changed = False
        for req in self._pending_requests:
            # Check if this requester already has a lease (e.g. from a retried workflow task)
            existing_profile_id = None
            for p in self._profiles.values():
                if req.requester_workflow_id in p.current_leases:
                    existing_profile_id = p.profile_id
                    break

            if existing_profile_id:
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
                    self._profiles[existing_profile_id].release(req.requester_workflow_id)
                    leases_changed = True
                continue

            profile = self._find_available_profile(
                selector=req.profile_selector,
                execution_profile_ref=req.execution_profile_ref,
                lease_group_id=req.lease_group_id,
            )
            if profile and profile.reserve(req.requester_workflow_id, now):
                leases_changed = True
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
                    profile.release(req.requester_workflow_id)
                    leases_changed = True
            else:
                remaining.append(req)
        self._pending_requests = remaining

        # Persist lease changes to DB for crash recovery
        if leases_changed and workflow.patched(DB_LEASE_PERSISTENCE_PATCH):
            await self._sync_leases_to_db()

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
                if profile.is_default and profile.enabled
            ]
            default_profiles = [
                profile for profile in eligible_profiles if profile.is_default
            ]
            if workflow.patched(DEFAULT_PROFILE_EXCLUSIVE_SELECTION_PATCH):
                if default_profiles:
                    eligible_profiles = default_profiles
                elif configured_default_profiles:
                    return None
                elif len(eligible_profiles) == 1:
                    return eligible_profiles[0]
                eligible_profiles.sort(
                    key=lambda p: (p.priority, p.available_slots),
                    reverse=True,
                )
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

        # Sort descending by priority, then by available slots
        eligible_profiles.sort(key=lambda p: (p.priority, p.available_slots), reverse=True)
        return eligible_profiles[0]

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
            max_duration = getattr(profile, "max_lease_duration_seconds", None) or _MAX_LEASE_DURATION_SECONDS
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
            all_wf_ids.extend(profile.current_leases)
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
                status_info = workflow_statuses.get(wf_id, {})
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
                    "tags": list(state.tags),
                    "priority": state.priority,
                    "runtime_materialization_mode": state.runtime_materialization_mode,
                }
            )
            if state.current_leases:
                leases[pid] = list(state.current_leases)
            if state.lease_granted_at:
                lease_times[pid] = dict(state.lease_granted_at)
            if state.cooldown_until:
                cooldowns[pid] = state.cooldown_until

        return {
            "runtime_id": self._runtime_id,
            "profiles": profiles_list,
            "leases": leases,
            "lease_granted_at": lease_times,
            "cooldowns": cooldowns,
            "pending_requests": [
                {
                    "requester_workflow_id": r.requester_workflow_id,
                    "runtime_id": r.runtime_id,
                    "execution_profile_ref": r.execution_profile_ref,
                    "profile_selector": r.profile_selector,
                    "lease_group_id": r.lease_group_id,
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
            self._apply_profile_sync(profiles_data)
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

    async def _sync_leases_to_db(self) -> None:
        """Persist current lease state to the database for crash recovery."""
        try:
            leases = []
            for profile in self._profiles.values():
                for wf_id in profile.current_leases:
                    leases.append({
                        "workflow_id": wf_id,
                        "profile_id": profile.profile_id,
                        "granted_at": profile.lease_granted_at.get(wf_id),
                    })
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
        except Exception:
            # If we can't persist leases, log but don't fail.
            # The manager will still function, just without DB persistence.
            self._get_logger().warning(
                "Failed to persist leases to DB, continuing without persistence"
            )

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

    async def _load_leases_from_db(self) -> None:
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
                return

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

                # Check if this profile still exists and is enabled
                profile = self._profiles.get(profile_id)
                if not profile or not profile.enabled:
                    self._get_logger().warning(
                        "Persisted lease for %s references unknown or disabled profile %s, skipping",
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

                # Send slot_assigned to the workflow to reconnect
                try:
                    await self._signal_slot_assigned(wf_id, profile_id)
                    self._get_logger().info(
                        "Restored lease: %s -> profile %s", wf_id, profile_id
                    )
                except Exception as e:
                    self._get_logger().warning(
                        "Failed to reconnect to workflow %s: %s", wf_id, e
                    )
                    # Release the lease since the workflow is likely dead
                    profile.release(wf_id)
                    await self._remove_lease_from_db(wf_id)

        except Exception:
            self._get_logger().warning(
                "Failed to load leases from DB, continuing without persisted state"
            )
