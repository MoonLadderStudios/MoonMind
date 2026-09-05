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
        CLAUDE_OAUTH_EXCLUSIVE_CAPACITY_ERROR,
        is_claude_oauth_profile,
        is_codex_oauth_profile,
        validate_claude_oauth_capacity,
        validate_codex_oauth_capacity,
    )
    from moonmind.provider_profiles.lease_client import (
        MANAGER_ROLLOVER_ERROR_TYPE,
        CredentialLeaseMode,
        CredentialLeasePurpose,
        credential_lease_mode,
        credential_source_is_credentialless,
    )

WORKFLOW_NAME = "MoonMind.ProviderProfileManager"
ACTIVITY_TASK_QUEUE = "mm.activity.artifacts"
WORKFLOW_ID_PREFIX = "provider-profile-manager"

# Replay patch IDs are durable Temporal history markers. Preserve legacy
# "auth-profile" spellings in identifiers until a deliberate workflow migration.
VERIFY_LEASE_HOLDERS_PATCH = "auth-profile-manager-verify-leases-v1"
DB_LEASE_PERSISTENCE_PATCH = "provider-profile-manager-db-lease-persistence-v1"
LEASE_TOMBSTONE_PURGE_PATCH = "provider-profile-manager-lease-tombstone-purge-v1"
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
CLAUDE_OAUTH_EXCLUSIVE_CAPACITY_PATCH = (
    "provider-profile-manager-claude-oauth-exclusive-capacity-v1"
)
PURPOSE_AWARE_CREDENTIAL_LEASE_PATCH = (
    "provider-profile-manager-purpose-aware-credential-lease-v1"
)
FRESH_START_DB_LEASE_RESTORE_PATCH = (
    "provider-profile-manager-fresh-start-db-lease-restore-v1"
)
DURABLE_LEASE_GRANT_PATCH = "provider-profile-manager-durable-lease-grant-v1"
ACTIVITY_OWNED_LEASE_VERIFICATION_PATCH = (
    "provider-profile-manager-activity-owned-lease-verification-v1"
)
# MoonLadderStudios/MoonMind#3878: configured capacity is a shared execution
# ceiling. Validation and maintenance leases stop consuming execution slots,
# credentialless validation becomes single-flight instead of exclusive, and
# rate limiting lowers effective admission instead of withdrawing the profile.
PURPOSE_AWARE_CAPACITY_LEDGER_PATCH = (
    "provider-profile-manager-purpose-aware-capacity-ledger-v1"
)
PROVIDER_CAPACITY_SCOPE_PATCH = "provider-profile-manager-capacity-scope-v1"
# MoonLadderStudios/MoonMind#3879: pending exclusive maintenance becomes a
# durable ordered queue that survives Continue-As-New with owner and order
# intact, its wait predicate matches the real grant conditions, and every grant
# carries a monotonic fencing generation so a stale release cannot free the
# replacement holder.
MAINTENANCE_QUEUE_DURABILITY_PATCH = (
    "provider-profile-manager-maintenance-queue-durability-v1"
)
# Incremental durable lease operations replace the runtime-wide snapshot
# rewrite that every grant previously performed.
PROVIDER_INCREMENTAL_LEASE_PATCH = "provider-profile-manager-incremental-lease-v1"

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
    apply_claude_exclusive_capacity: bool = True,
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
    identity = {
        "runtime_id": profile.get("runtime_id", runtime_id),
        "credential_source": profile.get("credential_source"),
        "materialization_mode": profile.get("runtime_materialization_mode"),
    }
    is_claude_oauth = is_claude_oauth_profile(**identity)
    if is_claude_oauth and not apply_claude_exclusive_capacity:
        return capacity
    if (is_codex_oauth or is_claude_oauth) and capacity != 1:
        if repair_legacy:
            return 1
        try:
            validator = (
                validate_codex_oauth_capacity
                if is_codex_oauth
                else validate_claude_oauth_capacity
            )
            validator(**identity, max_parallel_runs=capacity)
        except ValueError as exc:
            raise exceptions.ApplicationError(
                (
                    CODEX_OAUTH_EXCLUSIVE_CAPACITY_ERROR
                    if is_codex_oauth
                    else CLAUDE_OAUTH_EXCLUSIVE_CAPACITY_ERROR
                ),
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
# A single-flight validation lease is held by a bounded provider probe, not by
# a durable workflow. Its safety-net eviction must be short enough that a lost
# maintainer process cannot stall refresh for an execution-length window.
_MAX_VALIDATION_LEASE_DURATION_SECONDS = 900
# Adaptive backpressure never edits the operator's configured ceiling. It
# lowers the effective admission limit, halving on each rate-limit report and
# restoring one slot per recovery interval until the ceiling is reached again.
_ADAPTIVE_CAPACITY_RECOVERY_SECONDS = 60
_MIN_ADAPTIVE_CAPACITY = 1

_EXECUTION_PURPOSE_VALUES = frozenset(
    {
        CredentialLeasePurpose.EXECUTION_DIRECT.value,
        CredentialLeasePurpose.EXECUTION_OMNIGENT.value,
    }
)

# Released lease rows are tombstoned rather than deleted so the fencing
# high-water mark survives them. Reaping them after this horizon keeps the
# table bounded: every release path funnels through activities with bounded
# retries, so no duplicate of a completed release can still be redelivered
# after this long, and reissuing a number below a reaped tombstone cannot
# match a release that is still in flight.
_LEASE_TOMBSTONE_RETENTION_SECONDS = 30 * 24 * 3600
# Purge tombstones at most this often, keyed off the monotonically increasing
# event count so the cadence is deterministic on replay.
_LEASE_TOMBSTONE_PURGE_EVENT_INTERVAL = 60


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
    capacity_scope_ref: str = ""
    effective_limit: int = 0
    # MoonLadderStudios/MoonMind#3878 ledger fields. ``purpose_aware_capacity``
    # keeps histories recorded before the patch on their original accounting.
    purpose_aware_capacity: bool = False
    adaptive_capacity_limit: Optional[int] = None
    adaptive_capacity_updated_at: Optional[str] = None
    # MoonLadderStudios/MoonMind#3879: pending exclusive maintenance is a
    # durable ordered queue of waiter records, not a bare count. Serializing a
    # count without its owners loses exactly the thing the queue exists to
    # protect — which request is next, and whose it is.
    exclusive_maintenance_queue: list[dict[str, Any]] = field(default_factory=list)

    @property
    def configured_capacity(self) -> int:
        """The operator-owned ceiling. Never lowered by runtime backpressure."""

        return self.max_parallel_runs

    @property
    def effective_capacity(self) -> int:
        """The current admission limit: the ceiling, or a lower adaptive limit."""

        if not self.purpose_aware_capacity or self.adaptive_capacity_limit is None:
            return self.max_parallel_runs
        return max(
            _MIN_ADAPTIVE_CAPACITY,
            min(self.max_parallel_runs, self.adaptive_capacity_limit),
        )

    def lease_purpose(self, lease_id: str) -> str:
        """Return the recorded purpose of one lease, defaulting to direct execution."""

        metadata = self.lease_metadata.get(lease_id) or {}
        return str(
            metadata.get("purpose") or CredentialLeasePurpose.EXECUTION_DIRECT.value
        )

    def consumes_execution_capacity(self, lease_id: str) -> bool:
        if not self.purpose_aware_capacity:
            return True
        return self.lease_purpose(lease_id) in _EXECUTION_PURPOSE_VALUES

    @property
    def credentialless(self) -> bool:
        return credential_source_is_credentialless(self.credential_source)

    def lease_mode(self, lease_id: str) -> "CredentialLeaseMode":
        """Return the ledger mode a held lease was granted under."""

        try:
            return credential_lease_mode(
                purpose=self.lease_purpose(lease_id),
                credentialless=self.credentialless,
            )
        except ValueError:
            # An unrecognized persisted purpose is treated as the most
            # restrictive mode so it can never silently widen admission.
            return CredentialLeaseMode.EXCLUSIVE_MAINTENANCE

    @property
    def exclusive_maintenance_lease_count(self) -> int:
        if not self.purpose_aware_capacity:
            return 0
        return sum(
            1
            for lease_id in self.current_leases
            if self.lease_mode(lease_id)
            is CredentialLeaseMode.EXCLUSIVE_MAINTENANCE
        )

    @property
    def exclusive_maintenance_waiters(self) -> int:
        """How many exclusive maintenance requests are queued and waiting."""

        return len(self.exclusive_maintenance_queue)

    @property
    def maintenance_queue_head(self) -> Optional[dict[str, Any]]:
        """The waiter whose turn it is, or ``None`` when nothing is queued."""

        if not self.exclusive_maintenance_queue:
            return None
        return self.exclusive_maintenance_queue[0]

    def maintenance_queue_position(self, owner_id: str) -> int:
        """Return the waiter's 0-based position, or ``-1`` when not queued."""

        for index, entry in enumerate(self.exclusive_maintenance_queue):
            if entry.get("ownerId") == owner_id:
                return index
        return -1

    def enqueue_maintenance_waiter(
        self,
        owner_id: str,
        *,
        purpose: str,
        queue_order: int,
        queued_at: str,
        metadata: dict[str, Any] | None = None,
        evidence_identity: str | None = None,
    ) -> int:
        """Record one pending exclusive maintenance request, preserving order.

        Re-enqueueing an owner that is already waiting is the caller-retry and
        rollover-reattachment path: it keeps the original position rather than
        sending a request that has already waited to the back of the queue.
        """

        existing = self.maintenance_queue_position(owner_id)
        if existing >= 0:
            return existing
        self.exclusive_maintenance_queue.append(
            {
                "ownerId": owner_id,
                "purpose": purpose,
                "queueOrder": queue_order,
                "queuedAt": queued_at,
                "metadata": dict(metadata or {}),
                **(
                    {"evidenceIdentity": evidence_identity}
                    if evidence_identity
                    else {}
                ),
            }
        )
        self.exclusive_maintenance_queue.sort(
            key=lambda entry: (
                int(entry.get("queueOrder") or 0),
                str(entry.get("ownerId") or ""),
            )
        )
        return self.maintenance_queue_position(owner_id)

    def dequeue_maintenance_waiter(self, owner_id: str) -> bool:
        """Remove one pending request; returns whether it was queued."""

        position = self.maintenance_queue_position(owner_id)
        if position < 0:
            return False
        del self.exclusive_maintenance_queue[position]
        return True

    @property
    def exclusive_maintenance_active(self) -> bool:
        """Whether exclusive credential maintenance is waiting or holding.

        Exclusive maintenance blocks every new credential consumer, including
        single-flight validation, for as long as it waits to drain and for as
        long as it holds the credential state.
        """

        if not self.purpose_aware_capacity:
            return False
        return (
            self.exclusive_maintenance_waiters > 0
            or self.exclusive_maintenance_lease_count > 0
        )

    @property
    def execution_lease_count(self) -> int:
        return sum(
            1
            for lease_id in self.current_leases
            if self.consumes_execution_capacity(lease_id)
        )

    def scope_consuming_lease_count(self) -> int:
        """Leases that spend the shared upstream provider resource.

        The scope ledger describes provider fullness and cooldown, so only
        purposes that actually spend provider capacity may fill it.
        Credential repair and revocation are local credential-state work: once
        granted they must not occupy the shared scope and block execution on
        sibling profiles. A lease with an unrecognized purpose fails closed
        and counts against the scope.
        """

        count = 0
        for lease_id in self.current_leases:
            try:
                consuming = CredentialLeasePurpose(
                    self.lease_purpose(lease_id)
                ).consumes_provider_capacity
            except ValueError:
                consuming = True
            if consuming:
                count += 1
        return count

    def purpose_max_duration_seconds(self, purpose: str) -> int:
        """The longest one acquisition for this purpose may hold or wait."""

        base = self.max_lease_duration_seconds or _MAX_LEASE_DURATION_SECONDS
        if not self.purpose_aware_capacity:
            return base
        if purpose == CredentialLeasePurpose.CREDENTIAL_VALIDATION.value:
            return min(base, _MAX_VALIDATION_LEASE_DURATION_SECONDS)
        return base

    def lease_max_duration_seconds(self, lease_id: str) -> int:
        return self.purpose_max_duration_seconds(self.lease_purpose(lease_id))

    @property
    def available_slots(self) -> int:
        if not self.enabled or not self.launch_ready:
            return 0
        return max(0, self.effective_capacity - self.execution_lease_count)

    def is_available(self) -> bool:
        if not self.enabled or not self.launch_ready or self.available_slots <= 0:
            return False
        if self.cooldown_until is not None:
            return False
        if self.exclusive_maintenance_active:
            # Exclusive credential maintenance must block new consumers while
            # it waits for existing ones to drain, and for as long as it holds
            # the credential state.
            return False
        return True

    @property
    def credential_consumer_leases(self) -> list[str]:
        """Leases holding the credential resource maintenance is about to take.

        MoonLadderStudios/MoonMind#3879: the drain set is the leases that hold
        the *resource* exclusive maintenance needs, not every lease that
        happens to exist. A profile whose credential source is ``none``
        materializes no shared credential state, so its executions hold only
        the upstream provider route; there is nothing for credential work to
        corrupt and nothing it has to wait for. Whether that upstream route is
        available is a separate question, asked per purpose by
        ``_maintenance_consumes_scope``.
        """

        if not self.purpose_aware_capacity:
            return list(self.current_leases)
        if self.credentialless:
            return []
        return list(self.current_leases)

    def lease_fencing_generation(self, lease_id: str) -> int:
        """Return the monotonic grant generation recorded for one lease."""

        metadata = self.lease_metadata.get(lease_id) or {}
        try:
            return int(metadata.get("fencingGeneration") or 0)
        except (TypeError, ValueError):
            return 0

    def lease_evidence_identity(self, lease_id: str) -> str | None:
        """Return the compact evidence identity one lease was granted against."""

        metadata = self.lease_metadata.get(lease_id) or {}
        identity = metadata.get("evidenceIdentity")
        return str(identity) if identity else None

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
            if self.execution_lease_count >= self.max_parallel_runs:
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
                now
                + timedelta(
                    seconds=self.lease_max_duration_seconds(requester_workflow_id)
                )
            ).isoformat(),
            **dict(metadata or {}),
        }
        return True

    def reserve_unmetered(
        self,
        requester_workflow_id: str,
        now: datetime,
        *,
        purpose: str,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Record a lease that consumes no execution slot (single-flight validation).

        The caller has already proven the identity is not held, so this only
        refuses when the profile is missing its purpose-aware ledger.
        """

        if not self.purpose_aware_capacity:
            return False
        if self.exclusive_maintenance_active:
            return False
        if requester_workflow_id in self.current_leases:
            return False
        self.current_leases.append(requester_workflow_id)
        self.lease_granted_at[requester_workflow_id] = now.isoformat()
        self.lease_metadata[requester_workflow_id] = {
            "leaseId": requester_workflow_id,
            "ownerId": requester_workflow_id,
            "purpose": purpose,
            "acquiredAt": now.isoformat(),
            "expiresAt": (
                now
                + timedelta(
                    seconds=self.lease_max_duration_seconds(requester_workflow_id)
                )
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
                and self.execution_lease_count <= self.max_parallel_runs
            ):
                self.over_capacity_legacy_snapshot = False
            return True
        return False

    def apply_rate_limit_backpressure(self, now: datetime) -> int:
        """Halve the effective admission limit without editing the ceiling.

        Returns the new effective limit. Queued work is untouched: it simply
        waits for one of the remaining admitted slots. The caller withdraws the
        profile entirely only when the limit is already at the floor, because
        at that point there is no concurrency left to trade away.
        """

        current = self.effective_capacity
        lowered = max(_MIN_ADAPTIVE_CAPACITY, current // 2)
        self.adaptive_capacity_limit = lowered
        self.adaptive_capacity_updated_at = now.isoformat()
        return lowered

    def rate_limit_requires_withdrawal(self) -> bool:
        """Whether a rate-limit report must withdraw the profile entirely."""

        if not self.purpose_aware_capacity:
            return True
        return self.effective_capacity <= _MIN_ADAPTIVE_CAPACITY

    def recover_adaptive_capacity(self, now: datetime) -> bool:
        """Restore one slot per recovery interval until the ceiling is reached."""

        if self.adaptive_capacity_limit is None:
            return False
        updated_at = self.adaptive_capacity_updated_at
        if updated_at is not None:
            try:
                updated_dt = datetime.fromisoformat(updated_at)
                if updated_dt.tzinfo is None:
                    updated_dt = updated_dt.replace(tzinfo=timezone.utc)
            except (TypeError, ValueError):
                updated_dt = None
            if (
                updated_dt is not None
                and (now - updated_dt).total_seconds()
                < _ADAPTIVE_CAPACITY_RECOVERY_SECONDS
            ):
                return False
        restored = self.adaptive_capacity_limit + 1
        if restored >= self.max_parallel_runs:
            self.adaptive_capacity_limit = None
            self.adaptive_capacity_updated_at = None
            return True
        self.adaptive_capacity_limit = restored
        self.adaptive_capacity_updated_at = now.isoformat()
        return True

    def evict_expired_leases(
        self, now: datetime, max_duration_seconds: int
    ) -> list[str]:
        """Remove leases that have exceeded the maximum duration. Returns evicted IDs."""
        evicted: list[str] = []
        for wf_id in list(self.current_leases):
            granted_str = self.lease_granted_at.get(wf_id)
            limit = (
                min(max_duration_seconds, self.lease_max_duration_seconds(wf_id))
                if self.purpose_aware_capacity
                else max_duration_seconds
            )
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
                if (now - granted_dt).total_seconds() > limit:
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

    def evict_expired_maintenance_waiters(
        self, now: datetime, max_duration_seconds: int
    ) -> list[str]:
        """Drop pending requests whose caller never came back.

        A queue head blocks admission for everyone behind it, so a request
        detached by a rollover whose client never reattaches would wedge the
        profile permanently. Bounding the wait by the same duration the lease
        itself would have had keeps the durable queue from becoming a durable
        outage. Returns the evicted owner IDs.
        """

        if not self.purpose_aware_capacity:
            return []
        abandoned: list[str] = []
        for entry in list(self.exclusive_maintenance_queue):
            owner_id = str(entry.get("ownerId") or "")
            limit = min(
                max_duration_seconds,
                self.purpose_max_duration_seconds(str(entry.get("purpose") or "")),
            )
            queued_at = entry.get("queuedAt")
            try:
                queued_dt = datetime.fromisoformat(str(queued_at))
            except (TypeError, ValueError):
                # A request that cannot state when it was queued cannot be
                # shown to be waiting rather than abandoned.
                self.exclusive_maintenance_queue.remove(entry)
                abandoned.append(owner_id)
                continue
            if queued_dt.tzinfo is None:
                queued_dt = queued_dt.replace(tzinfo=timezone.utc)
            if (now - queued_dt).total_seconds() > limit:
                self.exclusive_maintenance_queue.remove(entry)
                abandoned.append(owner_id)
        return abandoned

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
            "capacity_scope_ref": self.capacity_scope_ref,
            "configured_capacity": self.configured_capacity,
            "effective_capacity": self.effective_capacity,
            "execution_lease_count": self.execution_lease_count,
            "adaptive_capacity_limit": self.adaptive_capacity_limit,
            "exclusive_maintenance_waiters": self.exclusive_maintenance_waiters,
            "exclusive_maintenance_queue": [
                {
                    "ownerId": entry.get("ownerId"),
                    "purpose": entry.get("purpose"),
                    "queueOrder": entry.get("queueOrder"),
                    "queuedAt": entry.get("queuedAt"),
                }
                for entry in self.exclusive_maintenance_queue
            ],
            "effective_limit": self.effective_limit,
        }

    @property
    def blended_per_million_usd(self) -> Optional[float]:
        if self.input_per_million_usd is None or self.output_per_million_usd is None:
            return None
        return self.input_per_million_usd + self.output_per_million_usd


@dataclass
class CapacityScopeState:
    """In-workflow tracking of one shared provider-capacity aggregate."""

    scope_ref: str
    runtime_id: str = ""
    provider_class: str = "unknown"
    generation: int = 1
    configured_limit: int = 1
    effective_limit: int = 1
    cooldown_until: Optional[str] = None
    backpressure_state: str = "healthy"
    recovery_policy_ref: str = "additive-increase-multiplicative-decrease@1"
    healthy_since: Optional[str] = None
    last_decrease_at: Optional[str] = None
    last_increase_at: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "scope_ref": self.scope_ref,
            "runtime_id": self.runtime_id,
            "provider_class": self.provider_class,
            "generation": self.generation,
            "configured_limit": self.configured_limit,
            "effective_limit": self.effective_limit,
            "cooldown_until": self.cooldown_until,
            "backpressure_state": self.backpressure_state,
            "recovery_policy_ref": self.recovery_policy_ref,
            "healthy_since": self.healthy_since,
            "last_decrease_at": self.last_decrease_at,
            "last_increase_at": self.last_increase_at,
        }


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
        self._scopes: dict[str, CapacityScopeState] = {}
        self._seen_rate_limit_reports: list[str] = []
        self._lease_profile_index: dict[str, str] = {}
        self._owner_lease_index: dict[str, str] = {}
        self._pending_requests: list[PendingRequest] = []
        self._pending_requests_ordered: bool = False
        self._handoff_reservations: dict[str, HandoffReservation] = {}
        self._event_count: int = 0
        self._shutdown_requested: bool = False
        self._has_new_events: bool = False
        self._profile_refresh_requested: bool = False
        self._has_db_profile_snapshot: bool = False
        self._purpose_aware_leases: bool = False
        self._purpose_aware_capacity_ledger: bool = False
        # MoonLadderStudios/MoonMind#3879 durability state.
        self._durable_maintenance_queue: bool = False
        self._rollover_requested: bool = False
        self._maintenance_queue_sequence: int = 0
        self._lease_grant_sequence: int = 0
        # Owners with an in-memory reservation whose durable persistence has
        # not reached a committed or rolled-back outcome yet. A Continue-As-New
        # snapshot taken while this is non-empty would publish authority the
        # persistence activity may never commit, so the rollover detach waits
        # for it to drain first. Transient only: never serialized, never
        # restored, and only ever tested for emptiness so set order cannot
        # affect replay.
        self._pending_grant_handoffs: set[str] = set()
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
            # Compact versioned identity of the work this lease authorizes.
            # Non-secret by contract: it is a digest, never raw credential or
            # provider material.
            "evidenceIdentity",
        }
        return {key: source[key] for key in allowed if source.get(key) is not None}

    def _next_fencing_generation(self) -> int:
        """Return the next monotonic grant generation for this manager."""

        self._lease_grant_sequence += 1
        return self._lease_grant_sequence

    def _grant_metadata(
        self, lease_metadata: dict[str, Any], *, fencing_generation: int
    ) -> dict[str, Any]:
        """Stamp caller metadata with the manager-owned grant generation."""

        if not self._durable_maintenance_queue:
            return dict(lease_metadata)
        return {**lease_metadata, "fencingGeneration": fencing_generation}

    def _fence_result(self, fencing_generation: int | None) -> dict[str, Any]:
        """Report the grant generation only when this manager actually fences.

        A history recorded before fenced grants keeps its exact grant payload,
        so no client is handed a generation the ledger never stored — quoting
        one back would be refused as stale.
        """

        if not self._durable_maintenance_queue or not fencing_generation:
            return {}
        return {"lease_fencing_generation": int(fencing_generation)}

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
        if profile and self._release_is_fenced_out(profile, requester_id, payload):
            # A duplicate or delayed release that quotes an older grant
            # generation must not free the lease the same deterministic owner
            # ID holds now. Dropping it here is the whole point of the fence:
            # the current holder keeps its authority and its own release still
            # works, because it quotes the current generation.
            self._get_logger().warning(
                "Ignoring a stale provider profile lease release for owner %s on "
                "profile %s; it names an earlier grant generation",
                requester_id,
                profile_id,
            )
            return
        released_generation = 0
        if profile:
            released_generation = profile.lease_fencing_generation(requester_id)
            released = profile.release(requester_id)
            if released:
                self._unindex_lease(requester_id)
            # A release also withdraws any pending maintenance request from the
            # same owner, so a caller that gave up cannot hold the queue head.
            # Pre-marker histories never withdrew a waiter here, and doing so
            # would reopen admission — and schedule grants — at a history
            # position that recorded none.
            if self._durable_maintenance_queue:
                profile.dequeue_maintenance_waiter(requester_id)
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
            await self._remove_lease_from_db(
                requester_id, fencing_generation=released_generation
            )

    def _release_is_fenced_out(
        self,
        profile: ProfileSlotState,
        requester_id: str,
        payload: dict[str, Any],
    ) -> bool:
        """Whether this release names a grant generation that is already gone.

        A release that carries no generation is honoured as-is. Releases sent
        by callers whose handle predates fenced grants must keep working, or a
        rollout would leak capacity for every lease already in flight. A
        release that carries a malformed generation is ignored instead: only
        an absent field receives legacy behavior, while a present invalid
        value fails closed so it cannot bypass the fence.
        """

        if not self._durable_maintenance_queue:
            return False
        raw = payload.get("fencing_generation")
        if raw is None:
            return False
        try:
            claimed = int(raw)
        except (TypeError, ValueError):
            # A present but malformed generation fails closed. Only an absent
            # field receives legacy unfenced behavior; a corrupted or
            # untrusted payload must not bypass the stale-release fence by
            # supplying an invalid value. Ignoring the release keeps the
            # current holder's authority intact.
            self._get_logger().warning(
                "Ignoring a provider profile lease release for owner %s on "
                "profile %s; it carries a malformed grant generation",
                requester_id,
                profile.profile_id,
            )
            return True
        held = profile.lease_fencing_generation(requester_id)
        if held == 0:
            # Nothing fenced is held under this owner: either it was already
            # released, or it predates fenced grants. Fall through so the
            # normal (idempotent) release path runs.
            return False
        return claimed != held

    @workflow.signal
    def withdraw_maintenance_waiter(self, payload: dict[str, Any]) -> None:
        """Withdraw one caller's pending exclusive-maintenance request.

        The client sends this when its bounded rollover-reattach budget is
        exhausted. Every rollover deliberately preserves the request's queue
        entry, and after the client gives up no handler remains to reattach
        it \u2014 without an explicit withdrawal the entry would hold the queue
        head and block every consumer behind it until the abandonment timeout.
        Withdrawing touches only the waiter record: a held lease is never
        released here, so a grant that won the race against the give-up keeps
        its authority.
        """
        self._event_count += 1
        self._has_new_events = True
        owner_id = self._normalize_optional_string(
            payload.get("requester_workflow_id") or payload.get("owner_id")
        )
        if not owner_id:
            return
        profile_id = self._normalize_optional_string(payload.get("profile_id"))
        if profile_id is not None and profile_id in self._profiles:
            self._profiles[profile_id].dequeue_maintenance_waiter(owner_id)
            return
        for profile in self._profiles.values():
            if profile.dequeue_maintenance_waiter(owner_id):
                return

    @workflow.signal
    def report_cooldown(self, payload: dict[str, Any]) -> None:
        """Report a 429-triggered cooldown on a profile."""
        self._event_count += 1
        self._has_new_events = True
        profile_id = payload["profile_id"]
        profile = self._profiles.get(profile_id)
        if not profile:
            return
        if not workflow.patched(PROVIDER_CAPACITY_SCOPE_PATCH):
            cooldown_seconds = payload.get(
                "cooldown_seconds",
                profile.cooldown_after_429_seconds,
            )
            now = workflow.now()
            if profile.purpose_aware_capacity:
                # Adaptive backpressure: trade concurrency before availability.
                # The configured ceiling and the pending queue are untouched.
                withdraw = profile.rate_limit_requires_withdrawal()
                lowered = profile.apply_rate_limit_backpressure(now)
                self._get_logger().warning(
                    "Provider profile %s lowered effective admission to %d of %d "
                    "after a rate-limit report",
                    profile_id,
                    lowered,
                    profile.configured_capacity,
                )
                if not withdraw:
                    return
            cooldown_until = now + timedelta(seconds=cooldown_seconds)
            profile.cooldown_until = cooldown_until.isoformat()
            return
        now = workflow.now()
        failure_class = str(payload.get("failure_class") or "rate_limit").strip()
        if failure_class not in {"rate_limit", "429", "provider_rate_limit"}:
            # Profile-specific credential errors must not reduce a shared scope.
            cooldown_seconds = payload.get(
                "cooldown_seconds", profile.cooldown_after_429_seconds
            )
            profile.cooldown_until = (now + timedelta(seconds=cooldown_seconds)).isoformat()
            return
        retry_after = payload.get("retry_after_seconds")
        try:
            retry_after_seconds = int(retry_after) if retry_after is not None else None
        except (TypeError, ValueError):
            retry_after_seconds = None
        if retry_after_seconds is None:
            retry_after_seconds = int(
                payload.get("cooldown_seconds", profile.cooldown_after_429_seconds)
            )
        scope_ref = (
            str(payload.get("capacity_scope_ref") or "").strip()
            or profile.capacity_scope_ref
            or f"provider-profile:{profile_id}"
        )
        report_id = payload.get("report_id") or payload.get("idempotency_key")
        self._apply_scope_rate_limit(
            scope_ref=scope_ref,
            retry_after_seconds=retry_after_seconds,
            report_id=str(report_id) if report_id else None,
            now=now,
        )
        profile.effective_limit = max(1, profile.effective_limit // 2) if profile.effective_limit else max(1, profile.max_parallel_runs // 2)
        profile.cooldown_until = (now + timedelta(seconds=max(1, min(3600, int(retry_after_seconds))))).isoformat()

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
        """Compatibility handler for Updates already present in workflow history."""

        return await self._acquire_slot(payload, verify_activity_owner=False)

    @workflow.update(name="AcquireSlotV2")
    async def acquire_slot_v2(self, payload: SlotAcquirePayload) -> dict[str, Any]:
        """Reserve capacity only while the Activity's parent workflow is live."""

        return await self._acquire_slot(payload, verify_activity_owner=True)

    async def _acquire_slot(
        self,
        payload: SlotAcquirePayload,
        *,
        verify_activity_owner: bool,
    ) -> dict[str, Any]:
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

        while not self._shutdown_requested and not self._rollover_requested:
            if verify_activity_owner:
                await self._assert_activity_lease_owner_running(lease_metadata)
            existing_profile_id = self._profile_id_for_lease(requester_id)
            if existing_profile_id is not None:
                return {
                    "profile_id": existing_profile_id,
                    "lease_id": requester_id,
                    "already_held": True,
                    "lease_mode": CredentialLeaseMode.SHARED_EXECUTION.value,
                    **self._fence_result(
                        self._profiles[existing_profile_id].lease_fencing_generation(
                            requester_id
                        )
                    ),
                }

            now = workflow.now()
            self._clear_expired_handoff_reservations(now)
            self._clear_expired_cooldowns()
            profile = self._find_available_profile(
                selector=selector,
                execution_profile_ref=execution_profile_ref,
                lease_group_id=lease_group_id,
            )
            fencing_generation = (
                self._next_fencing_generation() if profile is not None else 0
            )
            grant_metadata = self._grant_metadata(
                lease_metadata, fencing_generation=fencing_generation
            )
            if profile and profile.reserve(
                requester_id,
                now,
                purpose=purpose,
                metadata=grant_metadata,
            ):
                self._index_lease(profile.profile_id, requester_id, requester_id)
                if workflow.patched(DB_LEASE_PERSISTENCE_PATCH):
                    persisted = await self._persist_lease_grant(
                        profile,
                        requester_id,
                        purpose=purpose,
                        metadata=grant_metadata,
                    )
                    if (
                        workflow.patched(DURABLE_LEASE_GRANT_PATCH)
                        and not persisted
                    ):
                        profile.release(requester_id)
                        self._unindex_lease(requester_id)
                        raise exceptions.ApplicationError(
                            "Provider profile lease persistence failed before direct grant",
                            type="ProviderProfileLeasePersistenceFailed",
                        )
                self._has_new_events = True
                return {
                    "profile_id": profile.profile_id,
                    "lease_id": requester_id,
                    "already_held": False,
                    "lease_mode": CredentialLeaseMode.SHARED_EXECUTION.value,
                    **self._fence_result(fencing_generation),
                }

            try:
                await workflow.wait_condition(
                    lambda: (
                        self._shutdown_requested
                        or self._rollover_requested
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

        if self._rollover_requested:
            # An acquisition still waiting for capacity would otherwise hold the
            # manager's rollover open and then be dropped anyway. Detaching it
            # is safe because the request is idempotent on its owner ID: the
            # resubmission either observes the lease it was already granted or
            # takes its place in the same queue.
            raise exceptions.ApplicationError(
                "provider profile manager is rolling over; resubmit the same "
                "owner request against the successor run",
                type=MANAGER_ROLLOVER_ERROR_TYPE,
            )
        raise exceptions.ApplicationError(
            "provider profile manager is shutting down", non_retryable=True
        )

    async def _assert_activity_lease_owner_running(
        self, lease_metadata: dict[str, Any]
    ) -> None:
        """Reject a late Activity-owned grant once its parent is terminal."""

        if lease_metadata.get("ownerIsWorkflow") is not False:
            return
        owner_workflow_id = self._normalize_optional_string(
            lease_metadata.get("workflowId")
        )
        if owner_workflow_id is None:
            raise exceptions.ApplicationError(
                "Activity-owned provider lease requires workflowId authority",
                type="ProviderProfileLeaseOwnerMissing",
                non_retryable=True,
            )
        statuses = await self._verify_workflow_statuses([owner_workflow_id])
        if statuses is None:
            raise exceptions.ApplicationError(
                "Unable to verify provider lease owner workflow",
                type="ProviderProfileLeaseOwnerVerificationFailed",
            )
        status = statuses.get(owner_workflow_id)
        if status is not None and not status.get("running", True):
            raise exceptions.ApplicationError(
                "Provider lease owner workflow is terminal",
                type="ProviderProfileLeaseOwnerTerminal",
                non_retryable=True,
            )

    @workflow.update(name="AcquireCredentialMaintenanceLease")
    async def acquire_credential_maintenance_lease(
        self, payload: SlotAcquirePayload
    ) -> dict[str, Any]:
        """Acquire exact-profile credential authority for validation or maintenance.

        The ledger mode is derived from the declared purpose and the profile's
        durable credential contract, never from caller-supplied policy:

        * ``single_flight_validation`` — a credentialless profile has no shared
          mutable authentication state, so one holder per exact evidence
          identity is sufficient. It consumes no execution slot and imposes no
          capacity-one requirement (MoonLadderStudios/MoonMind#3878 invariant 3).
        * ``exclusive_maintenance`` — blocks new consumers and waits for every
          existing credential consumer to drain (invariant 4).
        """

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
        requested_identity = self._normalize_optional_string(
            self._safe_lease_metadata(payload).get("evidenceIdentity")
        )
        existing_profile_id = self._profile_id_for_lease(requester_id)
        if existing_profile_id is not None:
            if existing_profile_id != profile_id:
                raise exceptions.ApplicationError(
                    "lease owner already holds a different profile",
                    non_retryable=True,
                )
            held = self._profiles[profile_id]
            # MoonLadderStudios/MoonMind#3879: an owner ID is authority for the
            # exact request it was granted for. A second request that reuses it
            # for a different purpose or a different evidence identity is not
            # the same work, so it fails closed instead of inheriting authority
            # the manager never granted.
            if self._durable_maintenance_queue:
                held_purpose = held.lease_purpose(requester_id)
                if held_purpose != purpose:
                    raise exceptions.ApplicationError(
                        "lease owner already holds this profile for a different purpose",
                        type="ProviderProfileLeaseIdentityConflict",
                        non_retryable=True,
                    )
                held_identity = held.lease_evidence_identity(requester_id)
                if (
                    requested_identity is not None
                    and held_identity is not None
                    and requested_identity != held_identity
                ):
                    raise exceptions.ApplicationError(
                        "lease owner already holds this profile for a "
                        "different evidence identity",
                        type="ProviderProfileLeaseIdentityConflict",
                        non_retryable=True,
                    )
            return {
                "profile_id": profile_id,
                "lease_id": requester_id,
                "already_held": True,
                "lease_mode": self._credential_lease_mode(held, purpose).value,
                **self._fence_result(held.lease_fencing_generation(requester_id)),
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
                purpose_aware_capacity=self._purpose_aware_capacity_ledger,
                capacity_scope_ref=f"provider-profile:{profile_id}",
                effective_limit=1,
            )
            self._profiles[profile_id] = profile
        mode = self._credential_lease_mode(profile, purpose)
        if mode is CredentialLeaseMode.SINGLE_FLIGHT_VALIDATION:
            return await self._acquire_single_flight_validation_lease(
                profile=profile,
                requester_id=requester_id,
                purpose=purpose,
                payload=payload,
            )
        if profile.max_parallel_runs != 1 and not profile.purpose_aware_capacity:
            raise exceptions.ApplicationError(
                "credential maintenance requires exclusive profile capacity",
                non_retryable=True,
            )
        return await self._acquire_exclusive_maintenance_lease(
            profile=profile,
            requester_id=requester_id,
            purpose=purpose,
            payload=payload,
        )

    def _credential_lease_mode(
        self, profile: ProfileSlotState, purpose: str
    ) -> "CredentialLeaseMode":
        """Return the ledger mode for one acquisition against this profile."""

        if not self._purpose_aware_capacity_ledger:
            return (
                CredentialLeaseMode.EXCLUSIVE_MAINTENANCE
                if CredentialLeasePurpose(purpose).is_maintenance
                else CredentialLeaseMode.SHARED_EXECUTION
            )
        return credential_lease_mode(
            purpose=purpose,
            credentialless=credential_source_is_credentialless(
                profile.credential_source
            ),
        )

    async def _acquire_single_flight_validation_lease(
        self,
        *,
        profile: ProfileSlotState,
        requester_id: str,
        purpose: str,
        payload: SlotAcquirePayload,
    ) -> dict[str, Any]:
        """Grant one credentialless validation lease per exact evidence identity.

        The owner ID is derived from the evidence identity by the caller, so a
        second concurrent maintainer computing the same identity observes
        ``already_held`` and stands down instead of re-probing the provider.

        A validation lease is still a credential consumer, so it queues behind
        exclusive credential maintenance rather than running concurrently with
        credential mutation and publishing evidence for stale state.

        Validation consumes provider capacity, so the single-flight path waits
        on the same shared-scope condition as the exclusive path: granting a
        catalog probe while the shared scope is saturated or cooling down
        would exceed the configured provider ceiling and add traffic during
        rate-limit recovery.
        """

        profile_id = profile.profile_id
        lease_metadata = self._safe_lease_metadata(payload)
        scope_gated = self._maintenance_consumes_scope(purpose)

        def _blocked() -> bool:
            current = self._profiles.get(profile_id)
            if current is None:
                return False
            if current.exclusive_maintenance_active:
                return True
            return scope_gated and not self._profile_scope_available(current)

        while (
            not self._shutdown_requested
            and not self._rollover_requested
            and _blocked()
        ):
            try:
                await workflow.wait_condition(
                    lambda: (
                        self._shutdown_requested
                        or self._rollover_requested
                        or not _blocked()
                    ),
                    timeout=timedelta(seconds=60),
                )
            except TimeoutError:
                continue
        if self._shutdown_requested:
            raise exceptions.ApplicationError(
                "provider profile manager is shutting down", non_retryable=True
            )
        if self._rollover_requested:
            raise exceptions.ApplicationError(
                "provider profile manager is rolling over; resubmit the same "
                "owner request to reattach to this validation identity",
                type=MANAGER_ROLLOVER_ERROR_TYPE,
            )

        # A profile refresh may have replaced the object this handler was
        # started with, so re-resolve it rather than reserving against a
        # detached copy the manager no longer publishes.
        profile = self._profiles.get(profile_id) or profile
        fencing_generation = self._next_fencing_generation()
        grant_metadata = self._grant_metadata(
            lease_metadata, fencing_generation=fencing_generation
        )
        if not profile.reserve_unmetered(
            requester_id,
            workflow.now(),
            purpose=purpose,
            metadata=grant_metadata,
        ):
            # The identity is already held by an in-flight validator.
            return {
                "profile_id": profile_id,
                "lease_id": requester_id,
                "already_held": True,
                "lease_mode": CredentialLeaseMode.SINGLE_FLIGHT_VALIDATION.value,
                **self._fence_result(
                    profile.lease_fencing_generation(requester_id)
                ),
            }
        self._index_lease(profile_id, requester_id, requester_id)
        # The reservation is tentative until the grant activity commits it;
        # see _acquire_exclusive_maintenance_lease for why rollover waits.
        self._begin_grant_handoff(requester_id)
        try:
            if workflow.patched(DB_LEASE_PERSISTENCE_PATCH):
                persisted = await self._persist_lease_grant(
                    profile,
                    requester_id,
                    purpose=purpose,
                    metadata=grant_metadata,
                )
                if workflow.patched(DURABLE_LEASE_GRANT_PATCH) and not persisted:
                    profile.release(requester_id)
                    self._unindex_lease(requester_id)
                    raise exceptions.ApplicationError(
                        "Provider profile lease persistence failed "
                        "before validation grant",
                        type="ProviderProfileLeasePersistenceFailed",
                    )
            self._has_new_events = True
            return {
                "profile_id": profile_id,
                "lease_id": requester_id,
                "already_held": False,
                "lease_mode": CredentialLeaseMode.SINGLE_FLIGHT_VALIDATION.value,
                **self._fence_result(fencing_generation),
            }
        finally:
            self._end_grant_handoff(requester_id)

    def _maintenance_grant_blocker(
        self,
        *,
        profile_id: str,
        requester_id: str,
        scope_gated: bool,
    ) -> str | None:
        """Return why exclusive maintenance cannot be granted now, or ``None``.

        MoonLadderStudios/MoonMind#3879: this is both the grant condition and
        the wait predicate. Expressing them once is what keeps the waiter from
        spinning — a wake-up can only happen when a grant attempt will actually
        be made. It resolves the profile by ID on every evaluation, so a
        profile refresh during the wait cannot leave the waiter reasoning about
        a replaced object.

        ``scope_gated`` is resolved once per request by
        ``_maintenance_consumes_scope`` rather than here, so this stays a pure
        predicate that a wait condition can evaluate as often as it likes.
        """

        profile = self._profiles.get(profile_id)
        if profile is None:
            return "profile_missing"
        if requester_id in profile.current_leases:
            return None
        if not profile.purpose_aware_capacity:
            # Pre-ledger histories keep their original "drain everything" rule.
            return (
                "draining_credential_consumers"
                if profile.current_leases
                else None
            )
        if self._durable_maintenance_queue:
            head = profile.maintenance_queue_head
            if head is not None and head.get("ownerId") != requester_id:
                return "queued_behind_earlier_maintenance"
            draining = profile.credential_consumer_leases
        else:
            # A history recorded before the durable queue admitted every
            # grantable waiter at once and drained every lease, credentialless
            # or not. Serializing on the queue head or exempting a
            # credentialless profile from the drain would make the same
            # history position emit a grant where it recorded a timer, so
            # pre-marker runs keep the admission rule they were started under.
            draining = list(profile.current_leases)
        if draining:
            return "draining_credential_consumers"
        if scope_gated and not self._profile_scope_available(profile):
            return "scope_unavailable"
        if profile.execution_lease_count >= profile.max_parallel_runs:
            # Mirrors ``reserve(allow_unready=True)``: waking here would be a
            # wake-up that cannot become a grant.
            return "profile_capacity_full"
        return None

    def _maintenance_consumes_scope(self, purpose: str) -> bool:
        """Whether shared-scope availability may gate this maintenance work.

        Scope fullness and cooldown describe the upstream provider resource.
        Credential repair and revocation do not spend it, so a saturated or
        cooling-down scope must never be what stops a broken credential from
        being fixed or revoked.

        That exemption is an admission-semantics change, so it is gated on the
        same marker as the durable queue: a history recorded before it gated
        every maintenance purpose on scope, and must keep doing so on replay.
        """

        try:
            scoped = workflow.patched(PROVIDER_CAPACITY_SCOPE_PATCH)
        except Exception:
            return False
        if not scoped:
            return False
        if not self._durable_maintenance_queue:
            return True
        try:
            return CredentialLeasePurpose(purpose).consumes_provider_capacity
        except ValueError:
            return True

    async def _acquire_exclusive_maintenance_lease(
        self,
        *,
        profile: ProfileSlotState,
        requester_id: str,
        purpose: str,
        payload: SlotAcquirePayload,
    ) -> dict[str, Any]:
        profile_id = profile.profile_id
        lease_metadata = self._safe_lease_metadata(payload)
        scope_gated = self._maintenance_consumes_scope(purpose)
        blocks_new_consumers = profile.purpose_aware_capacity
        queued = False
        if blocks_new_consumers:
            # Admission stops before the drain wait, so a busy profile cannot
            # starve maintenance by continuously replacing its consumers. The
            # queue entry is durable and ordered: a caller retry, a manager
            # restart, or a Continue-As-New rollover reattaches to the same
            # request in the same position instead of losing its turn.
            self._maintenance_queue_sequence += 1
            profile.enqueue_maintenance_waiter(
                requester_id,
                purpose=purpose,
                queue_order=self._maintenance_queue_sequence,
                queued_at=workflow.now().isoformat(),
                metadata=lease_metadata,
                evidence_identity=lease_metadata.get("evidenceIdentity"),
            )
            queued = True
            self._has_new_events = True
        granted = False
        try:
            while not self._shutdown_requested and not self._rollover_requested:
                blocker = self._maintenance_grant_blocker(
                    profile_id=profile_id,
                    requester_id=requester_id,
                    scope_gated=scope_gated,
                )
                current = self._profiles.get(profile_id)
                if current is not None and requester_id in current.current_leases:
                    # A caller retry can leave two handlers waiting for one
                    # deterministic owner. Whichever is granted, the other must
                    # report the existing lease rather than reserving the same
                    # owner twice and leaving two releasers for one authority.
                    granted = True
                    return {
                        "profile_id": profile_id,
                        "lease_id": requester_id,
                        "already_held": True,
                        "lease_mode": (
                            CredentialLeaseMode.EXCLUSIVE_MAINTENANCE.value
                        ),
                        **self._fence_result(
                            current.lease_fencing_generation(requester_id)
                        ),
                    }
                if blocker is None and current is not None:
                    fencing_generation = self._next_fencing_generation()
                    grant_metadata = self._grant_metadata(
                        lease_metadata, fencing_generation=fencing_generation
                    )
                    if current.reserve(
                        requester_id,
                        workflow.now(),
                        purpose=purpose,
                        metadata=grant_metadata,
                        allow_unready=True,
                    ):
                        # The in-memory reservation is tentative until the
                        # grant activity commits it. Rollover must not
                        # snapshot this lease \u2014 or detach this handler \u2014
                        # while the handoff is unresolved.
                        self._begin_grant_handoff(requester_id)
                        try:
                            self._index_lease(
                                profile_id, requester_id, requester_id
                            )
                            if workflow.patched(DB_LEASE_PERSISTENCE_PATCH):
                                persisted = await self._persist_lease_grant(
                                    current,
                                    requester_id,
                                    purpose=purpose,
                                    metadata=grant_metadata,
                                )
                                if (
                                    workflow.patched(DURABLE_LEASE_GRANT_PATCH)
                                    and not persisted
                                ):
                                    current.release(requester_id)
                                    self._unindex_lease(requester_id)
                                    raise exceptions.ApplicationError(
                                        "Provider profile lease persistence "
                                        "failed before maintenance grant",
                                        type="ProviderProfileLeasePersistenceFailed",
                                    )
                            granted = True
                            return {
                                "profile_id": profile_id,
                                "lease_id": requester_id,
                                "already_held": False,
                                "lease_mode": (
                                    CredentialLeaseMode.EXCLUSIVE_MAINTENANCE.value
                                ),
                                **self._fence_result(fencing_generation),
                            }
                        finally:
                            self._end_grant_handoff(requester_id)
                try:
                    await workflow.wait_condition(
                        lambda: (
                            self._shutdown_requested
                            or self._rollover_requested
                            or self._maintenance_grant_blocker(
                                profile_id=profile_id,
                                requester_id=requester_id,
                                scope_gated=scope_gated,
                            )
                            is None
                        ),
                        timeout=timedelta(seconds=60),
                    )
                except TimeoutError:
                    continue
        finally:
            # A rollover detach is the one exit that keeps the pending request:
            # its owner and position must survive so the reattaching caller
            # resumes its turn. Every other exit — grant, shutdown, persistence
            # failure, caller cancellation — must not leave a queue head that
            # nobody owns, because the head blocks new consumers.
            if queued and (granted or not self._rollover_requested):
                current = self._profiles.get(profile_id)
                if current is not None:
                    current.dequeue_maintenance_waiter(requester_id)
                self._has_new_events = True
        if self._rollover_requested:
            raise exceptions.ApplicationError(
                "provider profile manager is rolling over; resubmit the same "
                "owner request to reattach to the queued maintenance request",
                type=MANAGER_ROLLOVER_ERROR_TYPE,
            )
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
        apply_claude_exclusive_capacity = workflow.patched(
            CLAUDE_OAUTH_EXCLUSIVE_CAPACITY_PATCH
        )
        self._purpose_aware_leases = workflow.patched(
            PURPOSE_AWARE_CREDENTIAL_LEASE_PATCH
        )
        self._purpose_aware_capacity_ledger = workflow.patched(
            PURPOSE_AWARE_CAPACITY_LEDGER_PATCH
        )
        self._durable_maintenance_queue = workflow.patched(
            MAINTENANCE_QUEUE_DURABILITY_PATCH
        )
        self._restore_state(
            input_payload,
            repair_legacy_codex_oauth=repair_legacy_codex_oauth,
            apply_claude_exclusive_capacity=apply_claude_exclusive_capacity,
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

            # Step any adaptive backpressure back toward the configured
            # ceiling, then offer the restored slots to waiting requests.
            if self._recover_adaptive_capacity() > 0:
                await self._drain_queue()

            # Evict leases that exceed the max duration (safety net for
            # cancelled/terminated workflows that failed to release).
            evicted_count = self._evict_expired_leases()
            if evicted_count > 0 and workflow.patched(DB_LEASE_PERSISTENCE_PATCH):
                await self._sync_leases_to_db()

            # Reap release tombstones on a deterministic cadence so the lease
            # table stays bounded while the high-water mark outlives them.
            # Maintenance durability predates cleanup, so neither its marker
            # nor DB persistence identifies a history that scheduled a purge.
            # Preserve older histories' direct transition to lease verification.
            if (
                workflow.patched(LEASE_TOMBSTONE_PURGE_PATCH)
                and workflow.patched(DB_LEASE_PERSISTENCE_PATCH)
                and self._event_count % _LEASE_TOMBSTONE_PURGE_EVENT_INTERVAL == 0
            ):
                await self._purge_released_lease_rows()

            verify_lease_holders = workflow.patched(VERIFY_LEASE_HOLDERS_PATCH)
            verify_activity_owned_leases = workflow.patched(
                ACTIVITY_OWNED_LEASE_VERIFICATION_PATCH
            )
            verify_pending_requesters = workflow.patched(VERIFY_PENDING_REQUESTS_PATCH)
            if verify_lease_holders or verify_pending_requesters:
                await self._verify_active_workflows(
                    verify_lease_holders=verify_lease_holders,
                    verify_activity_owned_leases=verify_activity_owned_leases,
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
                if self._durable_maintenance_queue:
                    await self._detach_handlers_for_rollover()
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

    async def _detach_handlers_for_rollover(self) -> None:
        """Release accepted Updates before Continue-As-New, keeping their requests.

        MoonLadderStudios/MoonMind#3879: rolling over with Update handlers still
        waiting drops those requests, and Temporal warns about exactly that.
        Waiting for them unconditionally is not an option either, because a
        maintenance waiter can legitimately wait for a long drain.

        The manager therefore does both halves of an explicit protocol: the
        pending maintenance request stays in the durable queue with its owner
        and position, and each waiting handler fails with
        ``ProviderProfileManagerRollover`` so its client reattaches by
        resubmitting the identical owner request against the new run.

        A handler inside the persistence handoff \u2014 in-memory lease reserved
        but the grant activity unresolved \u2014 is not merely waiting: its
        outcome decides whether the snapshotted lease is real authority or a
        phantom. The detach therefore waits for in-progress handoffs to reach
        a definitive committed or rolled-back state before snapshotting, and
        only then waits for the remaining handlers to finish.
        """

        self._rollover_requested = True
        if self._pending_grant_handoffs:
            try:
                await workflow.wait_condition(
                    lambda: not self._pending_grant_handoffs,
                    timeout=timedelta(seconds=30),
                )
            except TimeoutError:
                # Bounded: an unresponsive persistence activity must not hold
                # the manager's history open forever. The uncommitted owners
                # are logged so the snapshot can be audited.
                self._get_logger().warning(
                    "Provider profile manager rolled over with %d uncommitted "
                    "lease grants",
                    len(self._pending_grant_handoffs),
                )
        try:
            await workflow.wait_condition(
                workflow.all_handlers_finished,
                timeout=timedelta(seconds=30),
            )
        except TimeoutError:
            # A handler that will not yield must not hold the manager's history
            # open forever. The queued requests are durable either way.
            self._get_logger().warning(
                "Provider profile manager rolled over with unfinished handlers"
            )

    def _begin_grant_handoff(self, owner_id: str) -> None:
        """Mark one in-memory grant as awaiting durable persistence."""

        self._pending_grant_handoffs.add(owner_id)

    def _end_grant_handoff(self, owner_id: str) -> None:
        """Clear one grant handoff once it committed or rolled back."""

        self._pending_grant_handoffs.discard(owner_id)

    def _restore_state(
        self,
        input_payload: dict[str, Any],
        *,
        repair_legacy_codex_oauth: bool = True,
        apply_claude_exclusive_capacity: bool = True,
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
        self._maintenance_queue_sequence = self._normalize_sequence(
            input_payload.get("maintenance_queue_sequence")
        )
        self._lease_grant_sequence = self._normalize_sequence(
            input_payload.get("lease_grant_sequence")
        )
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
            restored_max = _validated_profile_capacity(
                p,
                runtime_id=self._runtime_id,
                repair_legacy=repair_legacy_codex_oauth,
                apply_claude_exclusive_capacity=apply_claude_exclusive_capacity,
            )
            restored_effective = p.get("effective_limit")
            try:
                restored_effective = int(restored_effective)
            except (TypeError, ValueError):
                restored_effective = 0
            if restored_effective <= 0:
                restored_effective = restored_max
            state = ProfileSlotState(
                profile_id=pid,
                max_parallel_runs=restored_max,
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
                capacity_scope_ref=(
                    str(p.get("capacity_scope_ref") or "").strip()
                    or f"provider-profile:{pid}"
                ),
                effective_limit=restored_effective,
                purpose_aware_capacity=self._purpose_aware_capacity_ledger,
                adaptive_capacity_limit=self._normalize_capacity_limit(
                    p.get("adaptive_capacity_limit")
                ),
                adaptive_capacity_updated_at=self._normalize_optional_string(
                    p.get("adaptive_capacity_updated_at")
                ),
                exclusive_maintenance_queue=self._restore_maintenance_queue(
                    p.get("exclusive_maintenance_queue")
                ),
            )
            self._profiles[pid] = state

        seen = input_payload.get("seen_rate_limit_reports", [])
        self._seen_rate_limit_reports = (
            [str(value) for value in seen[-500:] if str(value)]
            if isinstance(seen, list)
            else []
        )
        scopes_data = input_payload.get("scopes", [])
        if isinstance(scopes_data, list) and scopes_data:
            for entry in scopes_data:
                if not isinstance(entry, dict):
                    continue
                scope_ref = str(entry.get("scope_ref") or "").strip()
                if not scope_ref:
                    continue
                try:
                    configured = int(entry.get("configured_limit") or 0)
                    effective = int(entry.get("effective_limit") or 0)
                except (TypeError, ValueError):
                    continue
                if configured <= 0:
                    continue
                self._scopes[scope_ref] = CapacityScopeState(
                    scope_ref=scope_ref,
                    runtime_id=str(entry.get("runtime_id") or ""),
                    provider_class=str(entry.get("provider_class") or "unknown"),
                    generation=int(entry.get("generation") or 1),
                    configured_limit=configured,
                    effective_limit=effective if effective > 0 else configured,
                    cooldown_until=entry.get("cooldown_until"),
                    backpressure_state=str(entry.get("backpressure_state") or "healthy"),
                    recovery_policy_ref=str(
                        entry.get("recovery_policy_ref")
                        or "additive-increase-multiplicative-decrease@1"
                    ),
                    healthy_since=entry.get("healthy_since"),
                    last_decrease_at=entry.get("last_decrease_at"),
                    last_increase_at=entry.get("last_increase_at"),
                )
        else:
            for profile in self._profiles.values():
                scope_ref = profile.capacity_scope_ref or f"provider-profile:{profile.profile_id}"
                scope = self._scopes.get(scope_ref)
                if scope is None:
                    self._scopes[scope_ref] = CapacityScopeState(
                        scope_ref=scope_ref,
                        configured_limit=profile.max_parallel_runs,
                        effective_limit=profile.effective_limit or profile.max_parallel_runs,
                    )
        self._absorb_fencing_generations()
        self._rebuild_lease_indexes()

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
                new_scope = str(p.get("capacity_scope_ref") or "").strip()
                if new_scope:
                    existing.capacity_scope_ref = new_scope
                elif not existing.capacity_scope_ref:
                    existing.capacity_scope_ref = f"provider-profile:{pid}"
                existing.purpose_aware_capacity = (
                    self._purpose_aware_capacity_ledger
                )
                if not existing.effective_limit:
                    existing.effective_limit = existing.max_parallel_runs
                existing.effective_limit = min(
                    existing.effective_limit, existing.max_parallel_runs
                )
                try:
                    scoped_sync = workflow.patched(PROVIDER_CAPACITY_SCOPE_PATCH)
                except Exception:
                    scoped_sync = False
                if scoped_sync:
                    self._ensure_scope(
                        existing.capacity_scope_ref or f"provider-profile:{pid}",
                        runtime_id=self._runtime_id or "",
                    )
                self._apply_profile_pricing(existing, p)
                if authoritative:
                    existing.authoritative_policy_confirmed = True
                    existing.over_capacity_legacy_snapshot = (
                        existing.execution_lease_count > existing.max_parallel_runs
                    )
            else:
                pricing = pricing_from_profile_metadata(p)
                synced_max = _validated_profile_capacity(
                    p,
                    runtime_id=self._runtime_id,
                )
                self._profiles[pid] = ProfileSlotState(
                    profile_id=pid,
                    max_parallel_runs=synced_max,
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
                    capacity_scope_ref=(
                        str(p.get("capacity_scope_ref") or "").strip()
                        or f"provider-profile:{pid}"
                    ),
                    effective_limit=synced_max,
                    purpose_aware_capacity=self._purpose_aware_capacity_ledger,
                )

        # Disable profiles that were removed from DB (but don't drop leases).
        for pid in list(self._profiles.keys()):
            if pid not in seen:
                self._profiles[pid].enabled = False
                self._profiles[pid].is_default = False

    def _apply_scope_sync(self, scopes_data: list[dict[str, Any]]) -> None:
        """Merge authoritative scope configured limits without wiping adaptation.

        Effective limits adapt in-workflow via AIMD; a DB reload updates the
        configured ceiling and clamps effective down when the operator reduces
        below current effective, but never auto-raises effective (gradual
        recovery owns increases). Reducing below active usage blocks new
        grants without terminating work, since admission compares usage to
        the clamped effective.
        """
        for entry in scopes_data:
            if not isinstance(entry, dict):
                continue
            scope_ref = str(entry.get("scope_ref") or "").strip()
            if not scope_ref:
                continue
            try:
                configured = int(entry.get("configured_limit") or 0)
            except (TypeError, ValueError):
                continue
            if configured <= 0:
                continue
            scope = self._ensure_scope(
                scope_ref,
                runtime_id=str(entry.get("runtime_id") or self._runtime_id or ""),
                provider_class=str(entry.get("provider_class") or "unknown"),
            )
            try:
                generation = int(entry.get("generation") or scope.generation)
            except (TypeError, ValueError):
                generation = scope.generation
            if generation > scope.generation:
                scope.generation = generation
            scope.runtime_id = str(entry.get("runtime_id") or scope.runtime_id)
            scope.provider_class = str(entry.get("provider_class") or scope.provider_class)
            scope.configured_limit = configured
            scope.effective_limit = min(scope.effective_limit or configured, configured)
            if scope.effective_limit >= scope.configured_limit:
                scope.backpressure_state = "healthy"

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

    def _absorb_fencing_generations(self) -> None:
        """Keep the grant sequence ahead of every generation already recorded.

        Restored leases carry the generation they were granted under. Reissuing
        one of those numbers would let a stale release match a newer grant, so
        the sequence resumes above the highest generation in the ledger.
        """

        highest = self._lease_grant_sequence
        for profile in self._profiles.values():
            for lease_id in profile.current_leases:
                highest = max(highest, profile.lease_fencing_generation(lease_id))
        self._lease_grant_sequence = highest

    @staticmethod
    def _normalize_sequence(value: object) -> int:
        """Coerce a rolled-over monotonic counter, never going backwards."""

        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return 0

    def _restore_maintenance_queue(
        self, value: object
    ) -> list[dict[str, Any]]:
        """Rebuild the pending exclusive-maintenance queue with order intact."""

        if not isinstance(value, list):
            return []
        restored: list[dict[str, Any]] = []
        for entry in value:
            if not isinstance(entry, dict):
                continue
            owner_id = self._normalize_optional_string(entry.get("ownerId"))
            purpose = self._normalize_optional_string(entry.get("purpose"))
            if not owner_id or not purpose:
                # A waiter without an owner or a purpose is exactly the
                # serialized-count failure this queue exists to prevent; it
                # cannot be resumed, so it must not block admission either.
                continue
            queue_order = self._normalize_sequence(entry.get("queueOrder"))
            restored.append(
                {
                    "ownerId": owner_id,
                    "purpose": purpose,
                    "queueOrder": queue_order,
                    "queuedAt": self._normalize_optional_string(
                        entry.get("queuedAt")
                    ),
                    "metadata": (
                        dict(entry.get("metadata") or {})
                        if isinstance(entry.get("metadata"), dict)
                        else {}
                    ),
                    **(
                        {"evidenceIdentity": entry["evidenceIdentity"]}
                        if entry.get("evidenceIdentity")
                        else {}
                    ),
                }
            )
            self._maintenance_queue_sequence = max(
                self._maintenance_queue_sequence, queue_order
            )
        restored.sort(
            key=lambda item: (int(item["queueOrder"]), str(item["ownerId"]))
        )
        return restored

    @staticmethod
    def _normalize_optional_string(value: object) -> str | None:
        normalized = str(value or "").strip()
        return normalized or None

    @staticmethod
    def _normalize_capacity_limit(value: object) -> int | None:
        """Coerce a persisted adaptive limit, dropping anything unusable."""

        if value is None or isinstance(value, bool):
            return None
        try:
            limit = int(value)
        except (TypeError, ValueError):
            return None
        return limit if limit >= _MIN_ADAPTIVE_CAPACITY else None

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

    def _rebuild_lease_indexes(self) -> None:
        self._lease_profile_index = {}
        self._owner_lease_index = {}
        for profile in self._profiles.values():
            for lease_id in profile.current_leases:
                if lease_id in self._lease_profile_index:
                    continue
                self._lease_profile_index[lease_id] = profile.profile_id
                metadata = profile.lease_metadata.get(lease_id) or {}
                owner = str(metadata.get("ownerId") or lease_id)
                if owner not in self._owner_lease_index:
                    self._owner_lease_index[owner] = lease_id

    def _index_lease(self, profile_id: str, lease_id: str, owner_id: str | None = None) -> None:
        if lease_id and lease_id not in self._lease_profile_index:
            self._lease_profile_index[lease_id] = profile_id
        owner = str(owner_id or lease_id)
        if owner and owner not in self._owner_lease_index:
            self._owner_lease_index[owner] = lease_id

    def _unindex_lease(self, lease_id: str) -> None:
        profile_id = self._lease_profile_index.pop(lease_id, None)
        for owner, mapped_lease in list(self._owner_lease_index.items()):
            if mapped_lease == lease_id:
                del self._owner_lease_index[owner]
        _ = profile_id

    def _profile_id_for_lease(self, requester_workflow_id: str) -> str | None:
        try:
            use_index = workflow.patched(PROVIDER_INCREMENTAL_LEASE_PATCH)
        except Exception:
            use_index = False
        if use_index:
            return self._lease_profile_index.get(requester_workflow_id)
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
                    existing_generation: int | None = None
                    if self._durable_maintenance_queue:
                        held_generation = self._profiles[
                            existing_profile_id
                        ].lease_fencing_generation(req.requester_workflow_id)
                        existing_generation = held_generation or None
                    # The generation travels only on fenced assignments, so
                    # the legacy call shape \u2014 and every recorded command
                    # it produces \u2014 is unchanged for pre-fencing histories.
                    if existing_generation is not None:
                        await self._signal_slot_assigned(
                            req.requester_workflow_id,
                            existing_profile_id,
                            fencing_generation=existing_generation,
                        )
                    else:
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
                        self._unindex_lease(req.requester_workflow_id)
                        leases_changed = True
                continue

            profile = self._find_available_profile(
                selector=req.profile_selector,
                execution_profile_ref=req.execution_profile_ref,
                lease_group_id=req.lease_group_id,
            )
            grant_generation: int | None = None
            grant_metadata = req.lease_metadata
            if profile is not None and self._durable_maintenance_queue:
                # The signal grant path fences like the Update grant paths:
                # the reservation, the persisted row, and the assignment all
                # carry the same manager-owned generation, so a delayed
                # duplicate release cannot free a replacement grant that
                # reused this deterministic owner ID. Histories that predate
                # fenced grants keep the exact legacy reservation and signal.
                grant_generation = self._next_fencing_generation()
                grant_metadata = self._grant_metadata(
                    req.lease_metadata,
                    fencing_generation=grant_generation,
                )
            if profile is not None and profile.reserve(
                req.requester_workflow_id,
                now,
                purpose=req.purpose,
                metadata=grant_metadata,
            ):
                leases_changed = True
                self._index_lease(
                    profile.profile_id,
                    req.requester_workflow_id,
                    req.requester_workflow_id,
                )
                if durable_grants:
                    persisted = await self._persist_lease_grant(
                        profile,
                        req.requester_workflow_id,
                        purpose=req.purpose,
                        metadata=grant_metadata,
                    )
                    if not persisted:
                        # Hold the in-memory reservation and retry persistence on
                        # the next loop. Never signal a consumer before its lease
                        # is durable.
                        remaining.append(req)
                        continue
                try:
                    if grant_generation is not None:
                        await self._signal_slot_assigned(
                            req.requester_workflow_id,
                            profile.profile_id,
                            fencing_generation=grant_generation,
                        )
                    else:
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
                        self._unindex_lease(req.requester_workflow_id)
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
                    if not self._profile_admitted_by_capacity(reserved_profile):
                        return None
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
            if not self._profile_admitted_by_capacity(
                exact_profile, reserved_slots=reserved_slots
            ):
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
            if not self._profile_admitted_by_capacity(
                profile, reserved_slots=reserved_slots
            ):
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
        self,
        requester_workflow_id: str,
        profile_id: str,
        *,
        fencing_generation: int | None = None,
    ) -> None:
        """Send a slot_assigned signal to the requesting AgentRun workflow.

        The grant generation travels with the assignment so the holder quotes
        it back on release. A delayed duplicate release can then no longer
        free a replacement grant that reused the same deterministic owner ID.
        The generation is omitted for histories that predate fenced grants;
        the release path honours a generation-less release as legacy.
        """
        payload: dict[str, Any] = {"profile_id": profile_id}
        if fencing_generation is not None:
            payload["fencing_generation"] = int(fencing_generation)
        handle = workflow.get_external_workflow_handle(requester_workflow_id)
        await handle.signal("slot_assigned", payload)

    def _evict_expired_leases(self) -> int:
        """Remove leases held longer than the max duration. Returns total eviction count."""
        now = workflow.now()
        total_evicted = 0
        for profile in self._profiles.values():
            max_duration = (
                getattr(profile, "max_lease_duration_seconds", None)
                or _MAX_LEASE_DURATION_SECONDS
            )
            if self._durable_maintenance_queue:
                # Pending requests are bounded too: a queue head nobody comes
                # back for blocks every consumer behind it.
                for owner_id in profile.evict_expired_maintenance_waiters(
                    now, max_duration
                ):
                    self._has_new_events = True
                    self._get_logger().warning(
                        "Dropped an abandoned credential maintenance request for "
                        "profile %s queued by %s",
                        profile.profile_id,
                        owner_id,
                    )
            evicted = profile.evict_expired_leases(now, max_duration)
            total_evicted += len(evicted)
            for wf_id in evicted:
                self._unindex_lease(wf_id)
                self._get_logger().warning(
                    "Evicted stale lease for profile %s held by %s",
                    profile.profile_id,
                    wf_id,
                )
        return total_evicted

    def _lease_holder_workflow_ids(
        self, *, include_activity_owned: bool = False
    ) -> list[str]:
        """Return unique workflow IDs that currently hold profile leases."""
        all_wf_ids: list[str] = []
        for profile in self._profiles.values():
            for lease_id in profile.current_leases:
                metadata = profile.lease_metadata.get(lease_id) or {}
                owner_is_workflow = metadata.get("ownerIsWorkflow") is not False
                owner_workflow_id = str(metadata.get("workflowId") or "").strip()
                if not owner_is_workflow and not include_activity_owned:
                    continue
                if not owner_is_workflow and not owner_workflow_id:
                    continue
                all_wf_ids.append(owner_workflow_id or lease_id)
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
        self,
        workflow_statuses: dict[str, dict[str, Any]],
        *,
        include_activity_owned: bool = False,
    ) -> bool:
        """Remove leases held by workflows that are in a terminal state."""
        reclaimed = False

        for profile in list(self._profiles.values()):
            for wf_id in list(profile.current_leases):
                metadata = profile.lease_metadata.get(wf_id) or {}
                owner_is_workflow = metadata.get("ownerIsWorkflow") is not False
                owner_workflow_id = str(metadata.get("workflowId") or "").strip()
                if not owner_is_workflow and not include_activity_owned:
                    continue
                if not owner_is_workflow and not owner_workflow_id:
                    continue
                owner_workflow_id = owner_workflow_id or wf_id
                status_info = workflow_statuses.get(owner_workflow_id, {})
                if not status_info.get("running", True):
                    profile.release(wf_id)
                    self._unindex_lease(wf_id)
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
        verify_activity_owned_leases: bool = False,
        verify_pending_requesters: bool,
    ) -> None:
        """Verify lease holders and pending requesters with one status pass."""
        workflow_ids: list[str] = []
        if verify_lease_holders:
            workflow_ids.extend(
                self._lease_holder_workflow_ids(
                    include_activity_owned=verify_activity_owned_leases
                )
            )
        if verify_pending_requesters:
            workflow_ids.extend(self._pending_requester_workflow_ids())

        workflow_statuses = await self._verify_workflow_statuses(workflow_ids)
        if workflow_statuses is None:
            return

        reclaimed = False
        if verify_lease_holders:
            reclaimed = self._reclaim_terminal_leases(
                workflow_statuses,
                include_activity_owned=verify_activity_owned_leases,
            )
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
        if workflow.patched(PROVIDER_CAPACITY_SCOPE_PATCH):
            self._clear_expired_scope_cooldowns(now)
            self._recover_scope_capacity(now)

    def _ensure_scope(
        self, scope_ref: str, *, runtime_id: str = "", provider_class: str = "unknown"
    ) -> CapacityScopeState:
        scope = self._scopes.get(scope_ref)
        if scope is None:
            derived_max = 0
            for profile in self._profiles.values():
                if (profile.capacity_scope_ref or f"provider-profile:{profile.profile_id}") == scope_ref:
                    derived_max = max(derived_max, profile.max_parallel_runs)
            configured = derived_max if derived_max > 0 else 1
            scope = CapacityScopeState(
                scope_ref=scope_ref,
                runtime_id=runtime_id,
                provider_class=provider_class,
                configured_limit=configured,
                effective_limit=configured,
            )
            self._scopes[scope_ref] = scope
        return scope

    def _scope_active_units(self, scope_ref: str) -> int:
        return sum(
            p.scope_consuming_lease_count()
            for p in self._profiles.values()
            if (p.capacity_scope_ref or f"provider-profile:{p.profile_id}") == scope_ref
        )

    def _scope_is_available(self, scope: CapacityScopeState) -> bool:
        if scope.backpressure_state == "disabled":
            return False
        if scope.cooldown_until is not None:
            try:
                cooldown_dt = datetime.fromisoformat(scope.cooldown_until)
                if cooldown_dt.tzinfo is None:
                    cooldown_dt = cooldown_dt.replace(tzinfo=timezone.utc)
                if workflow.now() < cooldown_dt:
                    return False
            except (ValueError, TypeError):
                # Expected: malformed cooldown never blocks admission.
                pass
        return self._scope_active_units(scope.scope_ref) < max(1, scope.effective_limit)

    def _clear_expired_scope_cooldowns(self, now: datetime) -> None:
        for scope in self._scopes.values():
            if scope.cooldown_until is not None:
                try:
                    cooldown_dt = datetime.fromisoformat(scope.cooldown_until)
                    if cooldown_dt.tzinfo is None:
                        cooldown_dt = cooldown_dt.replace(tzinfo=timezone.utc)
                    if now >= cooldown_dt:
                        scope.cooldown_until = None
                        if scope.backpressure_state == "cooldown":
                            scope.backpressure_state = "probing"
                            scope.healthy_since = now.isoformat()
                except (ValueError, TypeError):
                    scope.cooldown_until = None

    def _recover_scope_capacity(self, now: datetime) -> None:
        healthy_interval = timedelta(seconds=300)
        for scope in self._scopes.values():
            if scope.cooldown_until is not None:
                continue
            if scope.effective_limit >= scope.configured_limit:
                continue
            since_raw = scope.healthy_since or scope.last_decrease_at
            if since_raw is None:
                scope.healthy_since = now.isoformat()
                continue
            try:
                since = datetime.fromisoformat(since_raw)
                if since.tzinfo is None:
                    since = since.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                scope.healthy_since = now.isoformat()
                continue
            if now - since >= healthy_interval:
                scope.effective_limit = min(
                    scope.configured_limit, scope.effective_limit + 1
                )
                scope.last_increase_at = now.isoformat()
                scope.healthy_since = now.isoformat()
                if scope.effective_limit >= scope.configured_limit:
                    scope.backpressure_state = "healthy"
        for profile in self._profiles.values():
            effective = profile.effective_limit or profile.max_parallel_runs
            if effective >= profile.max_parallel_runs:
                continue
            if profile.cooldown_until is not None:
                continue
            profile.effective_limit = min(profile.max_parallel_runs, effective + 1)

    def _apply_scope_rate_limit(
        self,
        *,
        scope_ref: str,
        retry_after_seconds: int | None,
        report_id: str | None,
        now: datetime,
    ) -> None:
        if report_id:
            if report_id in self._seen_rate_limit_reports:
                return
            self._seen_rate_limit_reports.append(report_id)
            del self._seen_rate_limit_reports[:-500]
        scope = self._ensure_scope(scope_ref)
        scope.effective_limit = max(1, scope.effective_limit // 2)
        scope.last_decrease_at = now.isoformat()
        scope.healthy_since = None
        scope.backpressure_state = (
            "reduced" if scope.cooldown_until is None else scope.backpressure_state
        )
        if retry_after_seconds is not None and retry_after_seconds > 0:
            bounded = max(1, min(3600, int(retry_after_seconds)))
            new_until = now + timedelta(seconds=bounded)
            if scope.cooldown_until is not None:
                try:
                    existing = datetime.fromisoformat(scope.cooldown_until)
                    if existing.tzinfo is None:
                        existing = existing.replace(tzinfo=timezone.utc)
                    if existing >= new_until:
                        new_until = existing
                except (ValueError, TypeError):
                    # Expected: malformed existing deadline never shortens the new one.
                    pass
            scope.cooldown_until = new_until.isoformat()
            scope.backpressure_state = "cooldown"

    def _profile_scope_ref(self, profile: ProfileSlotState) -> str:
        return (
            profile.capacity_scope_ref or f"provider-profile:{profile.profile_id}"
        )

    def _profile_effective_available(self, profile: ProfileSlotState) -> bool:
        effective = profile.effective_limit or profile.max_parallel_runs
        return len(profile.current_leases) < max(1, effective)

    def _profile_scope_available(self, profile: ProfileSlotState) -> bool:
        scope = self._ensure_scope(self._profile_scope_ref(profile))
        return self._scope_is_available(scope)

    def _profile_admitted_by_capacity(
        self, profile: ProfileSlotState, *, reserved_slots: int = 0
    ) -> bool:
        try:
            scoped = workflow.patched(PROVIDER_CAPACITY_SCOPE_PATCH)
        except Exception:
            return True
        if not scoped:
            return True
        if not self._profile_effective_available(profile):
            return False
        if len(profile.current_leases) + reserved_slots >= max(
            1, profile.effective_limit or profile.max_parallel_runs
        ):
            return False
        return self._profile_scope_available(profile)

    def _recover_adaptive_capacity(self) -> int:
        """Step lowered effective limits back toward the configured ceiling."""

        now = workflow.now()
        recovered = 0
        for profile in self._profiles.values():
            if not profile.purpose_aware_capacity:
                continue
            if profile.recover_adaptive_capacity(now):
                recovered += 1
        return recovered

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
                    "capacity_scope_ref": state.capacity_scope_ref,
                    "adaptive_capacity_limit": state.adaptive_capacity_limit,
                    "adaptive_capacity_updated_at": (
                        state.adaptive_capacity_updated_at
                    ),
                    "effective_limit": state.effective_limit,
                    **(
                        {
                            "exclusive_maintenance_queue": [
                                dict(entry)
                                for entry in state.exclusive_maintenance_queue
                            ]
                        }
                        if self._durable_maintenance_queue
                        else {}
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
            "scopes": [s.to_dict() for s in self._scopes.values()],
            "seen_rate_limit_reports": list(self._seen_rate_limit_reports[-500:]),
            **(
                {
                    # Fairness and fencing are both order-sensitive, so the
                    # sequences that produce them must roll over with the state
                    # they order.
                    "maintenance_queue_sequence": self._maintenance_queue_sequence,
                    "lease_grant_sequence": self._lease_grant_sequence,
                }
                if self._durable_maintenance_queue
                else {}
            ),
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
            if workflow.patched(PROVIDER_CAPACITY_SCOPE_PATCH):
                self._apply_scope_sync(result.get("scopes", []) if result else [])
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

    def _lease_row(self, profile: ProfileSlotState, lease_id: str) -> dict[str, Any]:
        return {
            "workflow_id": lease_id,
            "profile_id": profile.profile_id,
            "granted_at": profile.lease_granted_at.get(lease_id),
            "profileId": profile.profile_id,
            "runtimeId": self._runtime_id,
            "capacity_scope_ref": (
                profile.capacity_scope_ref
                or f"provider-profile:{profile.profile_id}"
            ),
            **dict(profile.lease_metadata.get(lease_id) or {}),
        }

    async def _persist_lease_grant(
        self,
        profile: ProfileSlotState,
        lease_id: str,
        *,
        purpose: str = "execution_direct",
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Durably record one lease grant.

        MoonLadderStudios/MoonMind#3878: at capacity ``N`` the runtime-wide
        snapshot rewrite made every grant cost O(active leases) of durable work
        and serialized unrelated profiles behind one another. Writing the single
        granted row carries the same recovery guarantee, so the incremental
        operation is the canonical path and the snapshot rewrite remains only
        for histories recorded before the incremental patch.

        Every grant funnels through here so there is exactly one place that
        decides between the incremental row write and the snapshot rewrite.
        """

        if workflow.patched(PROVIDER_INCREMENTAL_LEASE_PATCH):
            return await self._grant_lease_to_db(
                profile=profile,
                lease_id=lease_id,
                purpose=purpose,
                metadata=metadata,
            )
        return await self._sync_leases_to_db()

    async def _sync_leases_to_db(self) -> bool:
        """Persist current lease state to the database for crash recovery."""
        try:
            leases = []
            for profile in self._profiles.values():
                for wf_id in profile.current_leases:
                    leases.append(self._lease_row(profile, wf_id))
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

    async def _remove_lease_from_db(
        self, workflow_id: str, *, fencing_generation: int = 0
    ) -> None:
        """Remove a single lease from the database."""
        try:
            use_incremental = False
            try:
                use_incremental = workflow.patched(PROVIDER_INCREMENTAL_LEASE_PATCH)
            except Exception:
                use_incremental = False
            if use_incremental:
                await workflow.execute_activity(
                    "provider_profile.sync_slot_leases",
                    {
                        "runtime_id": self._runtime_id,
                        "leases": [
                            {
                                "lease_id": workflow_id,
                                "fencing_generation": fencing_generation or 1,
                            }
                        ],
                        "action": "release_one",
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
                return
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

    async def _grant_lease_to_db(
        self,
        *,
        profile: ProfileSlotState,
        lease_id: str,
        purpose: str = "execution_direct",
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Persist one lease grant; idempotent retry returns existing lease."""
        safe = dict(metadata or {})
        owner_id = str(safe.get("ownerId") or safe.get("workflowId") or lease_id)
        owner_is_workflow = safe.get("ownerIsWorkflow", True) is not False
        try:
            fencing_generation = int(safe.get("fencingGeneration") or 0)
        except (TypeError, ValueError):
            fencing_generation = 0
        evidence_identity = safe.get("evidenceIdentity")
        try:
            result = await workflow.execute_activity(
                "provider_profile.sync_slot_leases",
                {
                    "runtime_id": self._runtime_id,
                    "leases": [
                        {
                            "lease_id": lease_id,
                            "workflow_id": str(safe.get("workflowId") or lease_id),
                            "profile_id": profile.profile_id,
                            "owner_id": owner_id,
                            "owner_kind": "workflow" if owner_is_workflow else "activity",
                            "purpose": purpose,
                            "fencing_generation": fencing_generation or 1,
                            "scope_generation": 1,
                            "capacity_scope_ref": (
                                profile.capacity_scope_ref
                                or f"provider-profile:{profile.profile_id}"
                            ),
                            "lease_state": "held",
                            "stepExecutionId": safe.get("stepExecutionId"),
                            "oauthSessionId": safe.get("oauthSessionId"),
                            "idempotencyKey": safe.get("idempotencyKey"),
                            "ownerIsWorkflow": owner_is_workflow,
                            # The compact versioned identity this lease
                            # authorizes, so a manager restart restores the
                            # evidence contract with the lease rather than
                            # inferring it from the owner ID alone.
                            "safe_metadata": (
                                {"evidenceIdentity": str(evidence_identity)}
                                if evidence_identity
                                else None
                            ),
                        }
                    ],
                    "action": "grant",
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
            if isinstance(result, dict) and result.get("error"):
                return False
            return True
        except Exception:
            self._get_logger().warning(
                "Failed to persist lease grant for %s", lease_id
            )
            return False

    async def _purge_released_lease_rows(self) -> None:
        """Delete release tombstones older than the redelivery horizon."""

        try:
            await workflow.execute_activity(
                "provider_profile.sync_slot_leases",
                {
                    "runtime_id": self._runtime_id,
                    "action": "purge_released",
                    "leases": [
                        {"older_than_seconds": _LEASE_TOMBSTONE_RETENTION_SECONDS}
                    ],
                },
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
                "Failed to purge released provider profile lease rows"
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

            # The high-water mark is stored independently of the live rows:
            # release tombstones keep their generation, so a fresh manager
            # that finds no live rows still resumes above every number it
            # ever issued instead of reissuing generation 1, where a delayed
            # retry of an earlier release could free the replacement holder.
            try:
                persisted_high_water = int(
                    (result or {}).get("max_fencing_generation") or 0
                )
            except (TypeError, ValueError):
                persisted_high_water = 0
            if persisted_high_water > self._lease_grant_sequence:
                self._lease_grant_sequence = persisted_high_water

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
                    safe_metadata = lease.get("safeMetadata")
                    restored_identity = (
                        safe_metadata.get("evidenceIdentity")
                        if isinstance(safe_metadata, dict)
                        else None
                    )
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
                        **(
                            {
                                "fencingGeneration": self._normalize_sequence(
                                    lease.get("fencingGeneration")
                                )
                            }
                            if lease.get("fencingGeneration") is not None
                            else {}
                        ),
                        **(
                            {"evidenceIdentity": str(restored_identity)}
                            if restored_identity
                            else {}
                        ),
                    }
                    self._index_lease(
                        profile_id,
                        wf_id,
                        str(lease.get("ownerId") or wf_id),
                    )
                    self._lease_grant_sequence = max(
                        self._lease_grant_sequence,
                        profile.lease_fencing_generation(wf_id),
                    )

                # Send slot_assigned to the workflow to reconnect
                if is_maintenance:
                    continue
                try:
                    reconnect_generation: int | None = None
                    if self._durable_maintenance_queue:
                        restored_generation = profile.lease_fencing_generation(
                            wf_id
                        )
                        reconnect_generation = restored_generation or None
                    if reconnect_generation is not None:
                        await self._signal_slot_assigned(
                            wf_id,
                            profile_id,
                            fencing_generation=reconnect_generation,
                        )
                    else:
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
                        restored_generation = profile.lease_fencing_generation(wf_id)
                        profile.release(wf_id)
                        self._unindex_lease(wf_id)
                        await self._remove_lease_from_db(
                            wf_id, fencing_generation=restored_generation
                        )

            return True

        except Exception:
            self._get_logger().warning(
                "Failed to load leases from DB; refusing unverified capacity"
            )
            return False
