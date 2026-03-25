"""Singleton per-runtime-family auth profile manager workflow.

Each managed agent runtime family (gemini_cli, claude_code, codex_cli) gets its
own long-lived AuthProfileManager workflow instance. The manager owns the truth
about slot leases — which profiles have available capacity and which are in
cooldown — and assigns slots to AgentRun workflows via Temporal Signals.

Workflow ID convention: ``auth-profile-manager:<runtime_id>``
  e.g. ``auth-profile-manager:gemini_cli``

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

WORKFLOW_NAME = "MoonMind.AuthProfileManager"
WORKFLOW_TASK_QUEUE = "mm.workflow"
ACTIVITY_TASK_QUEUE = "mm.activity.artifacts"

# Continue-as-new threshold to bound history growth.
_MAX_EVENTS_BEFORE_CONTINUE_AS_NEW = 2000

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Input / Output types
# ---------------------------------------------------------------------------


class AuthProfileManagerInput(TypedDict, total=False):
    """Input payload for starting or continuing the manager."""

    runtime_id: str
    profiles: list[dict[str, Any]]
    leases: dict[str, list[str]]
    cooldowns: dict[str, str]
    lease_granted_at: dict[str, dict[str, str]]
    pending_requests: list[dict[str, str]]


class AuthProfileManagerOutput(TypedDict):
    status: str
    runtime_id: Optional[str]


# ---------------------------------------------------------------------------
# Signal payloads (documented as TypedDicts for clarity; actual transport is dict)
# ---------------------------------------------------------------------------


class SlotRequestPayload(TypedDict):
    """Signal payload: an AgentRun requests a profile slot."""

    requester_workflow_id: str
    runtime_id: str


class SlotReleasePayload(TypedDict):
    """Signal payload: an AgentRun releases its profile slot."""

    requester_workflow_id: str
    profile_id: str


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


_MAX_LEASE_DURATION_SECONDS = 7200  # 2 hours — safety net for leaked slots


@dataclass
class ProfileSlotState:
    """In-workflow tracking of one auth profile's slot availability."""

    profile_id: str
    max_parallel_runs: int
    cooldown_after_429_seconds: int
    rate_limit_policy: str
    enabled: bool
    current_leases: list[str] = field(default_factory=list)
    lease_granted_at: dict[str, str] = field(default_factory=dict)  # wf_id -> ISO ts
    cooldown_until: Optional[str] = None  # ISO timestamp string or None

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
            "current_leases": list(self.current_leases),
            "lease_granted_at": dict(self.lease_granted_at),
            "cooldown_until": self.cooldown_until,
        }


@dataclass
class PendingRequest:
    """A queued slot request waiting for assignment."""

    requester_workflow_id: str
    runtime_id: str


# ---------------------------------------------------------------------------
# Workflow definition
# ---------------------------------------------------------------------------


@workflow.defn(name=WORKFLOW_NAME)
class MoonMindAuthProfileManagerWorkflow:
    """Per-runtime-family singleton that manages auth profile slot leases.

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
            return logging.LoggerAdapter(logging.getLogger(__name__), extra=extra)

    def __init__(self) -> None:
        self._runtime_id: Optional[str] = None
        self._profiles: dict[str, ProfileSlotState] = {}
        self._pending_requests: list[PendingRequest] = []
        self._event_count: int = 0
        self._shutdown_requested: bool = False
        self._has_new_events: bool = False

    # -- Signals ---------------------------------------------------------------

    @workflow.signal
    def request_slot(self, payload: dict[str, Any]) -> None:
        """An AgentRun requests a profile slot for this runtime family."""
        self._event_count += 1
        self._has_new_events = True
        self._pending_requests.append(
            PendingRequest(
                requester_workflow_id=payload["requester_workflow_id"],
                runtime_id=payload.get("runtime_id", self._runtime_id or ""),
            )
        )

    @workflow.signal
    def release_slot(self, payload: dict[str, Any]) -> None:
        """An AgentRun releases its profile slot."""
        self._event_count += 1
        self._has_new_events = True
        profile_id = payload["profile_id"]
        requester_id = payload["requester_workflow_id"]
        profile = self._profiles.get(profile_id)
        if profile:
            profile.release(requester_id)

    @workflow.signal
    def report_cooldown(self, payload: dict[str, Any]) -> None:
        """Report a 429-triggered cooldown on a profile."""
        self._event_count += 1
        self._has_new_events = True
        profile_id = payload["profile_id"]
        cooldown_seconds = payload.get("cooldown_seconds", 300)
        profile = self._profiles.get(profile_id)
        if profile:
            now = workflow.now()
            cooldown_until = now + timedelta(seconds=cooldown_seconds)
            profile.cooldown_until = cooldown_until.isoformat()

    @workflow.signal
    def sync_profiles(self, payload: dict[str, Any]) -> None:
        """Receive an updated profile list from the DB sync activity."""
        self._event_count += 1
        self._has_new_events = True
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
                }
                for r in self._pending_requests
            ],
            "event_count": self._event_count,
        }

    # -- Main loop -------------------------------------------------------------

    @workflow.run
    async def run(
        self, input_payload: dict[str, Any]
    ) -> AuthProfileManagerOutput:
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

        # Main event loop: process signals, drain queue, clear cooldowns.
        while not self._shutdown_requested:
            # Drain pending requests against available profiles.
            await self._drain_queue()

            # Clear expired cooldowns.
            self._clear_expired_cooldowns()

            # Evict leases that exceed the max duration (safety net for
            # cancelled/terminated workflows that failed to release).
            self._evict_expired_leases()

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

        return AuthProfileManagerOutput(
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

        self._pending_requests = [
            PendingRequest(
                requester_workflow_id=req.get("requester_workflow_id", ""),
                runtime_id=req.get("runtime_id", "")
            )
            for req in pending_data
            if req.get("requester_workflow_id")
        ]

        for p in profiles_data:
            pid = p["profile_id"]
            state = ProfileSlotState(
                profile_id=pid,
                max_parallel_runs=p.get("max_parallel_runs", 1),
                cooldown_after_429_seconds=p.get("cooldown_after_429_seconds", 300),
                rate_limit_policy=p.get("rate_limit_policy", "backoff"),
                enabled=p.get("enabled", True),
                current_leases=list(leases_data.get(pid, [])),
                lease_granted_at=dict(lease_times_data.get(pid, {})),
                cooldown_until=cooldowns_data.get(pid),
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
            else:
                self._profiles[pid] = ProfileSlotState(
                    profile_id=pid,
                    max_parallel_runs=p.get("max_parallel_runs", 1),
                    cooldown_after_429_seconds=p.get(
                        "cooldown_after_429_seconds", 300
                    ),
                    rate_limit_policy=p.get("rate_limit_policy", "backoff"),
                    enabled=p.get("enabled", True),
                )

        # Disable profiles that were removed from DB (but don't drop leases).
        for pid in list(self._profiles.keys()):
            if pid not in seen:
                self._profiles[pid].enabled = False

    async def _drain_queue(self) -> None:
        """Try to assign slots to pending requests in FIFO order."""
        now = workflow.now()
        remaining: list[PendingRequest] = []
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
                continue

            profile = self._find_available_profile()
            if profile and profile.reserve(req.requester_workflow_id, now):
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
            else:
                remaining.append(req)
        self._pending_requests = remaining

    def _find_available_profile(self) -> Optional[ProfileSlotState]:
        """Find the best available profile (most free slots)."""
        best: Optional[ProfileSlotState] = None
        for profile in self._profiles.values():
            if not profile.is_available():
                continue
            if best is None or profile.available_slots > best.available_slots:
                best = profile
        return best

    async def _signal_slot_assigned(
        self, requester_workflow_id: str, profile_id: str
    ) -> None:
        """Send a slot_assigned signal to the requesting AgentRun workflow."""
        handle = workflow.get_external_workflow_handle(requester_workflow_id)
        await handle.signal("slot_assigned", {"profile_id": profile_id})

    def _evict_expired_leases(self) -> None:
        """Remove leases held longer than the max duration."""
        now = workflow.now()
        for profile in self._profiles.values():
            evicted = profile.evict_expired_leases(now, _MAX_LEASE_DURATION_SECONDS)
            for wf_id in evicted:
                self._get_logger().warning(
                    "Evicted stale lease for profile %s held by %s",
                    profile.profile_id,
                    wf_id,
                )

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
                }
                for r in self._pending_requests
            ],
        }

    async def _load_profiles_from_db(self) -> None:
        """Load auth profiles for this runtime from the database via activity."""
        try:
            result = await workflow.execute_activity(
                "auth_profile.list",
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
        except Exception:
            # If we can't load profiles, we'll wait for a sync_profiles signal.
            pass
