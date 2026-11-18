"""In-process cache and refresh coordination for provider account usage.

``ProviderAccountUsageService`` runs inside the existing API (and worker)
processes on the existing database. It owns the latest-snapshot cache in
``provider_account_usage_observations``, the cached projection served with
Provider Profiles, and refresh coordination. It never collects: collection is
handed to an :class:`AccountUsageDispatcher` (the credential-ownership
executor installs one; until then every provider reports ``not_supported``).

Every write is an optimistic compare-and-set on ``row_version`` and commits
per profile, so coalescing and single-flight ownership hold across tabs, API
workers and processes without a process-local lock. Queued/running attempts
carry an attempt ID and deadline; a lost acknowledgment leaves an attempt that
expires and becomes reclaimable instead of staying ``running``. Observation
writes touch only this table, never profile settings, readiness, defaults,
priority, enablement or scheduler cooldowns.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from random import Random
from typing import Any, Callable, Iterable, Mapping, Protocol, Sequence
from uuid import uuid4

from sqlalchemy import exists, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.db.models import (
    ManagedAgentProviderProfile,
    ProviderAccountUsageObservation,
    ProviderProfileAuthState,
    ProviderProfileDisabledReason,
)
from moonmind.provider_profiles.account_usage import (
    ACCOUNT_USAGE_SCHEMA_VERSION,
    ATTEMPT_DEADLINE,
    BUSY_RETRY_AFTER,
    MANUAL_REFRESH_INTERVAL,
    MAX_AUTOMATIC_FAILURES,
    STALE_AFTER,
    AccountUsageFailureCode,
    AccountUsageObservation,
    AccountUsageStatus,
    ObservationRejection,
    RefreshOutcome,
    RefreshState,
    RefreshTrigger,
    UsageCompleteness,
    UsageInput,
    WindowFreshness,
    backoff_delay,
    merge_identity,
    merge_usage,
    parse_retry_after,
    parse_stored_instant,
    remaining_percent,
    window_fresh_until,
    window_freshness,
)

logger = logging.getLogger(__name__)

_TABLE = ProviderAccountUsageObservation.__table__
_CAS_ATTEMPTS = 5
_DISPATCH_TIMEOUT_SECONDS = 5.0
_INFLIGHT = frozenset({RefreshState.QUEUED.value, RefreshState.RUNNING.value})
_LIFECYCLE_BLOCKED_REASONS = frozenset(
    {
        ProviderProfileDisabledReason.MISSING_CREDENTIALS,
        ProviderProfileDisabledReason.AUTH_INVALID,
        ProviderProfileDisabledReason.POLICY_DISABLED,
        ProviderProfileDisabledReason.DISCONNECTED,
    }
)
_RECONNECT_AUTH_STATES = frozenset(
    {ProviderProfileAuthState.DISCONNECTED, ProviderProfileAuthState.VALIDATION_FAILED}
)
_PERIOD_ORDER = {"weekly": 0, "short": 1, "other": 2}
_INSTANT_COLUMNS = frozenset(
    {
        "refresh_requested_at",
        "refresh_deadline_at",
        "last_attempt_at",
        "last_success_at",
        "last_failure_at",
        "next_allowed_at",
        "last_manual_request_at",
        "reset_requested_for",
    }
)
_WRITABLE = (
    "credential_generation",
    "snapshot_revision",
    "provider_id",
    "runtime_id",
    "bound_account_id",
    "bound_workspace_id",
    "account_key",
    "identity_json",
    "usage_json",
    "refresh_state",
    "refresh_trigger",
    "refresh_attempt_id",
    *sorted(_INSTANT_COLUMNS),
    "last_failure_code",
    "consecutive_failures",
)


# ---------------------------------------------------------------------------
# Collector port
# ---------------------------------------------------------------------------


class DispatchResult(StrEnum):
    DISPATCHED = "dispatched"
    BUSY = "busy"
    NOT_SUPPORTED = "not_supported"


@dataclass(frozen=True)
class CollectionRequest:
    """Compact, secret-free request handed to the collection executor.

    The executor publishes results through
    :meth:`ProviderAccountUsageService.ingest_observation` (authority
    ``refresh_attempt`` with this ``attempt_id``) or
    :meth:`ProviderAccountUsageService.record_collection_failure`.
    """

    profile_id: str
    credential_generation: int
    provider_id: str
    runtime_id: str
    attempt_id: str
    trigger: str
    deadline_at: datetime


@dataclass(frozen=True)
class AccountUsageProfileRef:
    """The profile fields coordination reads, detached from any ORM session."""

    profile_id: str
    credential_generation: int
    provider_id: str
    runtime_id: str
    auth_state: ProviderProfileAuthState
    enabled: bool
    disabled_reason: ProviderProfileDisabledReason | None

    @classmethod
    def of(cls, profile: Any) -> "AccountUsageProfileRef":
        if isinstance(profile, cls):
            return profile
        return cls(
            profile_id=profile.profile_id,
            credential_generation=profile.credential_generation,
            provider_id=profile.provider_id,
            runtime_id=profile.runtime_id,
            auth_state=profile.auth_state,
            enabled=bool(profile.enabled),
            disabled_reason=profile.disabled_reason,
        )


_PROFILE_COLUMNS = (
    ManagedAgentProviderProfile.profile_id,
    ManagedAgentProviderProfile.credential_generation,
    ManagedAgentProviderProfile.provider_id,
    ManagedAgentProviderProfile.runtime_id,
    ManagedAgentProviderProfile.auth_state,
    ManagedAgentProviderProfile.enabled,
    ManagedAgentProviderProfile.disabled_reason,
)


class AccountUsageDispatcher(Protocol):
    def supports(self, profile: AccountUsageProfileRef) -> bool:
        """Synchronous, side-effect-free capability check (used by list/detail)."""

    async def dispatch(self, request: CollectionRequest) -> DispatchResult:
        """Enqueue bounded collection through runtime/activity facilities.

        Must return promptly; it never runs a CLI or mounts an OAuth home in
        the API process. ``BUSY`` means the credential's owning runtime is in
        use and the probe is deferred without a waiter.
        """


class UnsupportedAccountUsageDispatcher:
    def supports(self, profile: AccountUsageProfileRef) -> bool:
        return False

    async def dispatch(self, request: CollectionRequest) -> DispatchResult:
        return DispatchResult.NOT_SUPPORTED


_dispatcher: AccountUsageDispatcher = UnsupportedAccountUsageDispatcher()


def get_account_usage_dispatcher() -> AccountUsageDispatcher:
    return _dispatcher


def install_account_usage_dispatcher(dispatcher: AccountUsageDispatcher) -> None:
    """Install the process collection executor (owned by credential authority)."""

    global _dispatcher
    _dispatcher = dispatcher


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


class AccountUsageProfilesNotFound(LookupError):
    def __init__(self, profile_ids: Sequence[str]) -> None:
        super().__init__("provider profiles not found")
        self.profile_ids = list(profile_ids)


@dataclass(frozen=True)
class RefreshResult:
    profile_id: str
    credential_generation: int
    outcome: RefreshOutcome
    refresh_state: RefreshState
    request_id: str | None = None
    coalesced: bool = False
    retry_after_seconds: int | None = None
    next_allowed_at: datetime | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "credential_generation": self.credential_generation,
            "outcome": self.outcome.value,
            "refresh_state": self.refresh_state.value,
            "request_id": self.request_id,
            "coalesced": self.coalesced,
            "retry_after_seconds": self.retry_after_seconds,
            "next_allowed_at": self.next_allowed_at,
        }


@dataclass(frozen=True)
class IngestResult:
    accepted: bool
    rejection: ObservationRejection | None = None
    changed: bool = False
    snapshot_revision: int | None = None
    shared_with: tuple[str, ...] = ()


@dataclass(frozen=True)
class FailureResult:
    accepted: bool
    rejection: ObservationRejection | None = None
    next_allowed_at: datetime | None = None


@dataclass
class _Decision:
    result: Any
    request: CollectionRequest | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class _Conflict(RuntimeError):
    pass


class _ProfileChanged(Exception):
    """The profile was deleted or rebound while a decision was being made."""

    def __init__(self, profile: "AccountUsageProfileRef | None") -> None:
        super().__init__("provider profile changed")
        self.profile = profile


def _aware(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    return parse_stored_instant(value)


def _retry_seconds(until: datetime | None, now: datetime) -> int | None:
    if until is None:
        return None
    return max(0, int((until - now).total_seconds() + 0.999))


def _account_key(provider_id: str, account_id: str, workspace_id: str | None) -> str:
    material = "\x1f".join((provider_id, account_id, workspace_id or ""))
    return hashlib.sha256(material.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class ProviderAccountUsageService:
    """Owns the observation cache; commits its own per-profile transactions."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        dispatcher: AccountUsageDispatcher | None = None,
        clock: Callable[[], datetime] | None = None,
        rng: Random | None = None,
        dispatch_timeout_seconds: float = _DISPATCH_TIMEOUT_SECONDS,
    ) -> None:
        self._session = session
        self._dispatcher = dispatcher or get_account_usage_dispatcher()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._rng = rng or Random()
        self._dispatch_timeout = dispatch_timeout_seconds

    def _now(self) -> datetime:
        return _aware(self._clock())  # type: ignore[return-value]

    # -- persistence helpers -------------------------------------------------

    async def _profiles(
        self, profile_ids: Iterable[str]
    ) -> dict[str, AccountUsageProfileRef]:
        ids = sorted(set(profile_ids))
        if not ids:
            return {}
        result = await self._session.execute(
            select(*_PROFILE_COLUMNS).where(
                ManagedAgentProviderProfile.profile_id.in_(ids)
            )
        )
        return {
            row.profile_id: AccountUsageProfileRef(**row._mapping)
            for row in result.all()
        }

    async def _rows(self, profile_ids: Iterable[str]) -> dict[str, dict[str, Any]]:
        ids = sorted(set(profile_ids))
        if not ids:
            return {}
        result = await self._session.execute(
            select(_TABLE).where(_TABLE.c.profile_id.in_(ids))
        )
        return {
            row["profile_id"]: self._normalize_row(row)
            for row in result.mappings().all()
        }

    @staticmethod
    def _normalize_row(row: Mapping[str, Any]) -> dict[str, Any]:
        data = dict(row)
        for column in _INSTANT_COLUMNS:
            data[column] = _aware(data.get(column))
        return data

    async def _ensure_rows(self, profiles: Iterable[AccountUsageProfileRef]) -> None:
        values = [
            {
                "profile_id": profile.profile_id,
                "credential_generation": profile.credential_generation,
                "provider_id": profile.provider_id,
                "runtime_id": profile.runtime_id,
            }
            for profile in profiles
        ]
        if not values:
            return
        dialect = self._session.get_bind().dialect.name
        insert = {"postgresql": pg_insert, "sqlite": sqlite_insert}.get(dialect)
        if insert is None:
            raise RuntimeError(
                "Account usage observations require PostgreSQL or SQLite."
            )
        try:
            await self._session.execute(
                insert(_TABLE)
                .values(values)
                .on_conflict_do_nothing(index_elements=["profile_id"])
            )
            await self._session.commit()
        except IntegrityError:
            # A profile was deleted concurrently; callers re-read it.
            await self._session.rollback()

    @staticmethod
    def _logical(
        row: Mapping[str, Any], profile: AccountUsageProfileRef
    ) -> dict[str, Any]:
        """The row as seen by the profile's current credential binding."""

        logical = dict(row)
        if row["credential_generation"] != profile.credential_generation:
            # Credential rebinding invalidates every cached observation and
            # refresh state; nothing from the old binding carries over.
            logical.update(
                credential_generation=profile.credential_generation,
                snapshot_revision=0,
                bound_account_id=None,
                bound_workspace_id=None,
                account_key=None,
                identity_json=None,
                usage_json=None,
                refresh_state=RefreshState.IDLE.value,
                refresh_trigger=None,
                refresh_attempt_id=None,
                last_failure_code=None,
                consecutive_failures=0,
                **{column: None for column in _INSTANT_COLUMNS},
            )
        logical["provider_id"] = profile.provider_id
        logical["runtime_id"] = profile.runtime_id
        return logical

    async def _compare_and_set(
        self,
        row: Mapping[str, Any],
        generation: int,
        changes: Mapping[str, Any],
    ) -> bool:
        profiles = ManagedAgentProviderProfile.__table__
        result = await self._session.execute(
            update(_TABLE)
            .where(
                _TABLE.c.profile_id == row["profile_id"],
                _TABLE.c.row_version == row["row_version"],
                # Fence the write to the binding the decision was made for.
                exists().where(
                    profiles.c.profile_id == row["profile_id"],
                    profiles.c.credential_generation == generation,
                ),
            )
            .values(**changes, row_version=row["row_version"] + 1)
        )
        return result.rowcount == 1

    async def _cas(
        self,
        row: Mapping[str, Any],
        logical: Mapping[str, Any],
        peers: Sequence[tuple[AccountUsageProfileRef, Mapping[str, Any]]] = (),
    ) -> bool:
        """Compare-and-set the row; a change also fences the peers it read.

        Bumping each peer's ``row_version`` makes a claim on one profile and a
        concurrent claim on another profile of the same account conflict.
        Writes go in profile order, so concurrent claims serialize on their
        first shared row instead of deadlocking.
        """

        changes = {
            column: logical[column]
            for column in _WRITABLE
            if logical.get(column) != row.get(column)
        }
        if not changes:
            return True
        writes = [(row, logical["credential_generation"], changes)] + [
            (peer_row, peer.credential_generation, {}) for peer, peer_row in peers
        ]
        for target, generation, values in sorted(
            writes, key=lambda write: write[0]["profile_id"]
        ):
            if not await self._compare_and_set(target, generation, values):
                return False
        return True

    async def _transact(
        self,
        profile: AccountUsageProfileRef,
        decide: Callable[..., _Decision],
        *,
        account_scoped: bool = False,
    ) -> _Decision:
        """Read, decide and compare-and-set one row, then commit.

        ``account_scoped`` decisions also receive the verified peers read in
        the same attempt, and a write fences those peers.

        Raises :class:`_ProfileChanged` when the profile disappears or its
        credential generation moves on, so no decision lands on a binding it
        was not made for.
        """

        for _ in range(_CAS_ATTEMPTS):
            current = (await self._profiles([profile.profile_id])).get(
                profile.profile_id
            )
            if current is None or current.credential_generation != (
                profile.credential_generation
            ):
                await self._session.rollback()
                raise _ProfileChanged(current)
            rows = await self._rows([profile.profile_id])
            row = rows.get(profile.profile_id)
            if row is None:
                await self._ensure_rows([profile])
                continue
            logical = self._logical(row, profile)
            peers = []
            if account_scoped:
                peers = await self._verified_peers(profile, logical["account_key"])
                decision = decide(logical, self._now(), [peer for _, peer in peers])
            else:
                decision = decide(logical, self._now())
            if await self._cas(row, logical, peers):
                await self._session.commit()
                return decision
            await self._session.rollback()
        raise _Conflict(f"account usage row {profile.profile_id} kept changing")

    async def _verified_peers(
        self, profile: AccountUsageProfileRef, account_key: str | None
    ) -> list[tuple[AccountUsageProfileRef, dict[str, Any]]]:
        """Other profiles bound to the same verified provider account."""

        if not account_key:
            return []
        result = await self._session.execute(
            select(_TABLE.c.profile_id).where(
                _TABLE.c.account_key == account_key,
                _TABLE.c.profile_id != profile.profile_id,
            )
        )
        ids = [row[0] for row in result.all()]
        profiles = await self._profiles(ids)
        rows = await self._rows(ids)
        peers = []
        for peer_id in sorted(profiles):
            peer, row = profiles[peer_id], rows.get(peer_id)
            if (
                row is not None
                and peer.provider_id == profile.provider_id
                and row["credential_generation"] == peer.credential_generation
                and row["account_key"] == account_key
            ):
                peers.append((peer, row))
        return peers

    # -- projection ----------------------------------------------------------

    async def load_projections(
        self, profiles: Sequence[Any]
    ) -> dict[str, dict[str, Any] | None]:
        """Cache-only projections for many profiles in one bounded query."""

        profiles = [AccountUsageProfileRef.of(profile) for profile in profiles]
        rows = await self._rows(profile.profile_id for profile in profiles)
        now = self._now()
        projections: dict[str, dict[str, Any] | None] = {}
        for profile in profiles:
            try:
                projections[profile.profile_id] = self._project(
                    profile, rows.get(profile.profile_id), now
                )
            except Exception:
                logger.warning(
                    "Account usage projection unavailable for profile %s",
                    profile.profile_id,
                    exc_info=True,
                )
                projections[profile.profile_id] = None
        return projections

    def _project(
        self,
        profile: AccountUsageProfileRef,
        row: Mapping[str, Any] | None,
        now: datetime,
    ) -> dict[str, Any]:
        current = (
            row
            if row is not None
            and row["credential_generation"] == profile.credential_generation
            else None
        )
        identity = (current or {}).get("identity_json") or {}
        usage = (current or {}).get("usage_json") or None
        windows = self._project_windows(usage, now)
        refresh = self._project_refresh(current, now)
        return {
            "schema_version": ACCOUNT_USAGE_SCHEMA_VERSION,
            "profile_id": profile.profile_id,
            "credential_generation": profile.credential_generation,
            "snapshot_revision": (current or {}).get("snapshot_revision") or 0,
            "provider_id": profile.provider_id,
            "runtime_id": profile.runtime_id,
            "binding_verified": bool((current or {}).get("account_key")),
            "status": self._status(
                profile, current, usage, windows, refresh, now
            ).value,
            "account": {
                "status": identity.get("status") or "unknown",
                "email": identity.get("email"),
                "display_name": identity.get("display_name"),
                "plan": identity.get("plan"),
                "source": identity.get("source"),
                "observed_at": _aware(identity.get("observed_at")),
            },
            "usage": {
                "completeness": (usage or {}).get("completeness"),
                "primary_window_key": (usage or {}).get("primary_window_key"),
                "full_observed_at": _aware((usage or {}).get("full_observed_at")),
                "windows": windows,
            },
            "refresh": refresh,
        }

    @staticmethod
    def _project_windows(
        usage: Mapping[str, Any] | None, now: datetime
    ) -> list[dict[str, Any]]:
        projected = []
        for window in ((usage or {}).get("windows") or {}).values():
            observed_at = _aware(window["observed_at"])
            reset_at = _aware(window.get("reset_at"))
            used = window.get("used_percent")
            freshness = window_freshness(
                observed_at=observed_at, reset_at=reset_at, now=now
            )
            projected.append(
                {
                    "key": window["key"],
                    "period": window["period"],
                    "scope": window["scope"],
                    "model_label": window.get("model_label"),
                    "duration_seconds": window.get("duration_seconds"),
                    "used_percent": used,
                    "remaining_percent": remaining_percent(used),
                    "exhausted": None if used is None else used >= 100,
                    "reset_at": reset_at,
                    "source": window["source"],
                    "source_version": window.get("source_version"),
                    "observed_at": observed_at,
                    "fresh_until": window_fresh_until(observed_at, reset_at),
                    "freshness": freshness.value,
                    "last_known": freshness is not WindowFreshness.FRESH,
                }
            )
        projected.sort(
            key=lambda w: (
                _PERIOD_ORDER.get(w["period"], 9),
                w["scope"] != "general",
                w["key"],
            )
        )
        return projected

    @staticmethod
    def _effective_refresh(
        row: Mapping[str, Any] | None, now: datetime
    ) -> tuple[RefreshState, str | None]:
        if row is None:
            return RefreshState.IDLE, None
        state = row["refresh_state"]
        failure = row.get("last_failure_code")
        deadline = row.get("refresh_deadline_at")
        if state in _INFLIGHT and (deadline is None or deadline <= now):
            return RefreshState.IDLE, AccountUsageFailureCode.ATTEMPT_ABANDONED.value
        if state == RefreshState.DEFERRED.value and (
            deadline is None or deadline <= now
        ):
            state = RefreshState.IDLE.value
        next_allowed = row.get("next_allowed_at")
        if state == RefreshState.IDLE.value and next_allowed and next_allowed > now:
            return RefreshState.BACKOFF, failure
        return RefreshState(state), failure

    def _project_refresh(
        self, row: Mapping[str, Any] | None, now: datetime
    ) -> dict[str, Any]:
        state, failure = self._effective_refresh(row, now)
        row = row or {}
        return {
            "state": state.value,
            "request_id": (
                row.get("refresh_attempt_id")
                if state.value in _INFLIGHT or state is RefreshState.DEFERRED
                else None
            ),
            "last_attempt_at": row.get("last_attempt_at"),
            "last_success_at": row.get("last_success_at"),
            "last_failure_at": row.get("last_failure_at"),
            "last_failure": failure,
            "next_allowed_at": row.get("next_allowed_at"),
        }

    def _status(
        self,
        profile: AccountUsageProfileRef,
        row: Mapping[str, Any] | None,
        usage: Mapping[str, Any] | None,
        windows: list[dict[str, Any]],
        refresh: Mapping[str, Any],
        now: datetime,
    ) -> AccountUsageStatus:
        if profile.auth_state in _RECONNECT_AUTH_STATES:
            return AccountUsageStatus.RECONNECT_REQUIRED
        failure = refresh.get("last_failure")
        failure_at = (row or {}).get("last_failure_at")
        success_at = (row or {}).get("last_success_at")
        failure_current = bool(failure) and (
            failure == AccountUsageFailureCode.ATTEMPT_ABANDONED.value
            or (
                failure_at is not None
                and (success_at is None or failure_at >= success_at)
            )
        )
        if (
            failure_current
            and failure == AccountUsageFailureCode.RECONNECT_REQUIRED.value
        ):
            return AccountUsageStatus.RECONNECT_REQUIRED
        if (
            failure_current
            and failure == AccountUsageFailureCode.USAGE_ACCESS_MISSING.value
        ):
            return AccountUsageStatus.USAGE_ACCESS_MISSING
        if usage is None:
            if failure_current:
                return AccountUsageStatus.UNAVAILABLE
            if not self._dispatcher.supports(profile):
                return AccountUsageStatus.NOT_SUPPORTED
            return AccountUsageStatus.UNKNOWN
        if not windows and usage.get("reported") is False:
            return AccountUsageStatus.NOT_REPORTED
        if windows and all(
            w["freshness"] == WindowFreshness.FRESH.value for w in windows
        ):
            return AccountUsageStatus.FRESH
        if failure_current:
            return AccountUsageStatus.UNAVAILABLE
        return AccountUsageStatus.STALE

    # -- refresh coordination ------------------------------------------------

    async def request_refresh(
        self, profile_ids: Sequence[str], *, trigger: RefreshTrigger
    ) -> list[RefreshResult]:
        """Request collection for each profile; ``accepted`` means requested."""

        ordered = list(dict.fromkeys(profile_ids))
        profiles = await self._profiles(ordered)
        missing = [pid for pid in ordered if pid not in profiles]
        if missing:
            raise AccountUsageProfilesNotFound(missing)
        await self._ensure_rows(profiles.values())

        results: dict[str, RefreshResult] = {}
        pending: list[tuple[AccountUsageProfileRef, CollectionRequest]] = []
        # Sorted and committed per profile; a claim writes the rows of its
        # account peers in that same order.
        for profile_id in sorted(profiles):
            profile = profiles[profile_id]
            decision = await self._claim(profile, trigger)
            results[profile_id] = decision.result
            if decision.request is not None:
                pending.append((decision.extra["profile"], decision.request))

        outcomes = await asyncio.gather(
            *(self._dispatch(request) for _, request in pending)
        )
        for (profile, request), outcome in zip(pending, outcomes):
            results[profile.profile_id] = await self._after_dispatch(
                profile, request, outcome, results[profile.profile_id]
            )
        return [results[pid] for pid in ordered]

    def _lifecycle_block(
        self, profile: AccountUsageProfileRef, trigger: RefreshTrigger
    ) -> RefreshOutcome | None:
        if profile.auth_state != ProviderProfileAuthState.CONNECTED:
            return RefreshOutcome.NOT_CONNECTED
        if not profile.enabled and (
            trigger.automatic or profile.disabled_reason in _LIFECYCLE_BLOCKED_REASONS
        ):
            return RefreshOutcome.DISABLED
        return None

    @staticmethod
    def _crossed_reset(row: Mapping[str, Any], now: datetime) -> datetime | None:
        crossed = [
            reset
            for window in ((row.get("usage_json") or {}).get("windows") or {}).values()
            if (reset := _aware(window.get("reset_at"))) is not None and reset <= now
        ]
        return max(crossed) if crossed else None

    async def _claim(
        self, profile: AccountUsageProfileRef, trigger: RefreshTrigger
    ) -> _Decision:
        for _ in range(_CAS_ATTEMPTS):
            try:
                decision = await self._claim_once(profile, trigger)
                decision.extra["profile"] = profile
                return decision
            except _ProfileChanged as changed:
                if changed.profile is None:
                    raise AccountUsageProfilesNotFound([profile.profile_id]) from None
                profile = changed.profile
        raise _Conflict(f"provider profile {profile.profile_id} kept changing")

    async def _claim_once(
        self, profile: AccountUsageProfileRef, trigger: RefreshTrigger
    ) -> _Decision:
        generation = profile.credential_generation

        def result(
            outcome: RefreshOutcome,
            state: RefreshState,
            **extra: Any,
        ) -> RefreshResult:
            return RefreshResult(
                profile_id=profile.profile_id,
                credential_generation=generation,
                outcome=outcome,
                refresh_state=state,
                **extra,
            )

        if not self._dispatcher.supports(profile):
            return _Decision(result(RefreshOutcome.NOT_SUPPORTED, RefreshState.IDLE))
        blocked = self._lifecycle_block(profile, trigger)
        if blocked is not None:
            return _Decision(result(blocked, RefreshState.IDLE))

        def decide(
            row: dict[str, Any], now: datetime, peers: list[dict[str, Any]]
        ) -> _Decision:
            state = row["refresh_state"]
            deadline = row.get("refresh_deadline_at")
            if state in _INFLIGHT and (deadline is None or deadline <= now):
                # A lost acknowledgment: the attempt expired, so reclaim it.
                row.update(
                    refresh_state=RefreshState.IDLE.value,
                    refresh_attempt_id=None,
                    refresh_deadline_at=None,
                    last_failure_code=AccountUsageFailureCode.ATTEMPT_ABANDONED.value,
                    last_failure_at=deadline or now,
                    consecutive_failures=row["consecutive_failures"] + 1,
                )
                state = RefreshState.IDLE.value
            if state in _INFLIGHT:
                return _Decision(
                    result(
                        RefreshOutcome.ACCEPTED,
                        RefreshState(state),
                        request_id=row["refresh_attempt_id"],
                        coalesced=True,
                    )
                )
            if state == RefreshState.DEFERRED.value and deadline and deadline > now:
                return _Decision(
                    result(
                        RefreshOutcome.DEFERRED_BUSY,
                        RefreshState.DEFERRED,
                        request_id=row["refresh_attempt_id"],
                        retry_after_seconds=_retry_seconds(deadline, now),
                    )
                )
            for peer in peers:
                peer_deadline = peer.get("refresh_deadline_at")
                if (
                    peer["refresh_state"] in _INFLIGHT
                    and peer_deadline
                    and peer_deadline > now
                ):
                    # One collection serves every profile on this verified account.
                    row.update(
                        refresh_state=RefreshState.QUEUED.value,
                        refresh_trigger=trigger.value,
                        refresh_attempt_id=peer["refresh_attempt_id"],
                        refresh_requested_at=now,
                        refresh_deadline_at=peer_deadline,
                    )
                    return _Decision(
                        result(
                            RefreshOutcome.ACCEPTED,
                            RefreshState.QUEUED,
                            request_id=peer["refresh_attempt_id"],
                            coalesced=True,
                        )
                    )
            next_allowed = max(
                [
                    value
                    for value in [row.get("next_allowed_at")]
                    + [peer.get("next_allowed_at") for peer in peers]
                    if value is not None
                ],
                default=None,
            )
            if next_allowed is not None and now < next_allowed:
                return _Decision(
                    result(
                        RefreshOutcome.BACKOFF,
                        RefreshState.BACKOFF,
                        retry_after_seconds=_retry_seconds(next_allowed, now),
                        next_allowed_at=next_allowed,
                    )
                )
            if (
                trigger.automatic
                and row["consecutive_failures"] >= MAX_AUTOMATIC_FAILURES
            ):
                return _Decision(result(RefreshOutcome.BACKOFF, RefreshState.IDLE))
            if trigger is RefreshTrigger.MANUAL:
                last_manual = max(
                    [
                        value
                        for value in [row.get("last_manual_request_at")]
                        + [peer.get("last_manual_request_at") for peer in peers]
                        if value is not None
                    ],
                    default=None,
                )
                if (
                    last_manual is not None
                    and now < last_manual + MANUAL_REFRESH_INTERVAL
                ):
                    retry_at = last_manual + MANUAL_REFRESH_INTERVAL
                    return _Decision(
                        result(
                            RefreshOutcome.BACKOFF,
                            RefreshState.IDLE,
                            retry_after_seconds=_retry_seconds(retry_at, now),
                            next_allowed_at=retry_at,
                        )
                    )
            crossed = self._crossed_reset(row, now)
            reset_due = crossed is not None and (
                row.get("reset_requested_for") is None
                or crossed > row["reset_requested_for"]
            )
            # Staleness follows the last successful collection, not the
            # observation time, so re-delivered cached telemetry cannot loop.
            succeeded = row.get("last_success_at")
            due = (
                trigger in {RefreshTrigger.MANUAL, RefreshTrigger.OAUTH}
                or succeeded is None
                or reset_due
                or now - succeeded >= STALE_AFTER
            )
            if not due:
                return _Decision(
                    result(
                        RefreshOutcome.ALREADY_FRESH,
                        RefreshState.IDLE,
                        retry_after_seconds=_retry_seconds(
                            succeeded + STALE_AFTER, now
                        ),
                    )
                )
            attempt_id = uuid4().hex
            deadline_at = now + ATTEMPT_DEADLINE
            row.update(
                refresh_state=RefreshState.QUEUED.value,
                refresh_trigger=trigger.value,
                refresh_attempt_id=attempt_id,
                refresh_requested_at=now,
                refresh_deadline_at=deadline_at,
                last_attempt_at=now,
            )
            if crossed is not None:
                # Request once per reset boundary; never fabricate replenishment.
                row["reset_requested_for"] = max(
                    crossed, row.get("reset_requested_for") or crossed
                )
            if trigger is RefreshTrigger.MANUAL:
                row["last_manual_request_at"] = now
            request = CollectionRequest(
                profile_id=profile.profile_id,
                credential_generation=generation,
                provider_id=profile.provider_id,
                runtime_id=profile.runtime_id,
                attempt_id=attempt_id,
                trigger=trigger.value,
                deadline_at=deadline_at,
            )
            return _Decision(
                result(
                    RefreshOutcome.ACCEPTED, RefreshState.QUEUED, request_id=attempt_id
                ),
                request=request,
            )

        return await self._transact(profile, decide, account_scoped=True)

    async def _dispatch(self, request: CollectionRequest) -> DispatchResult | None:
        try:
            return await asyncio.wait_for(
                self._dispatcher.dispatch(request), timeout=self._dispatch_timeout
            )
        except Exception as exc:
            logger.warning(
                "Account usage collection dispatch failed for profile %s: %s",
                request.profile_id,
                type(exc).__name__,
            )
            return None

    async def _after_dispatch(
        self,
        profile: AccountUsageProfileRef,
        request: CollectionRequest,
        outcome: DispatchResult | None,
        accepted: RefreshResult,
    ) -> RefreshResult:
        if outcome is DispatchResult.DISPATCHED:
            return accepted
        if outcome is None:
            failure = await self.record_collection_failure(
                profile_id=profile.profile_id,
                credential_generation=request.credential_generation,
                attempt_id=request.attempt_id,
                code=AccountUsageFailureCode.DISPATCH_FAILED,
            )
            now = self._now()
            return RefreshResult(
                profile_id=profile.profile_id,
                credential_generation=request.credential_generation,
                outcome=RefreshOutcome.BACKOFF,
                refresh_state=RefreshState.BACKOFF,
                retry_after_seconds=_retry_seconds(failure.next_allowed_at, now),
                next_allowed_at=failure.next_allowed_at,
            )

        def decide(row: dict[str, Any], now: datetime) -> _Decision:
            if row.get("refresh_attempt_id") != request.attempt_id:
                return _Decision(None)
            if outcome is DispatchResult.BUSY:
                # No waiter: the owning runtime may service the deferred
                # request, otherwise a later request probes again.
                retry_at = now + BUSY_RETRY_AFTER
                row.update(
                    refresh_state=RefreshState.DEFERRED.value,
                    refresh_deadline_at=retry_at,
                )
                return _Decision(
                    RefreshResult(
                        profile_id=profile.profile_id,
                        credential_generation=request.credential_generation,
                        outcome=RefreshOutcome.DEFERRED_BUSY,
                        refresh_state=RefreshState.DEFERRED,
                        request_id=request.attempt_id,
                        retry_after_seconds=_retry_seconds(retry_at, now),
                    )
                )
            row.update(
                refresh_state=RefreshState.IDLE.value,
                refresh_attempt_id=None,
                refresh_deadline_at=None,
            )
            return _Decision(
                RefreshResult(
                    profile_id=profile.profile_id,
                    credential_generation=request.credential_generation,
                    outcome=RefreshOutcome.NOT_SUPPORTED,
                    refresh_state=RefreshState.IDLE,
                )
            )

        try:
            decision = await self._transact(profile, decide)
        except _ProfileChanged:
            # Rebinding or deletion already voided this attempt.
            return accepted
        return decision.result or accepted

    async def mark_running(
        self, *, profile_id: str, credential_generation: int, attempt_id: str
    ) -> bool:
        """Executor acknowledgment: queued -> running with a fresh deadline."""

        profile = (await self._profiles([profile_id])).get(profile_id)
        if profile is None or profile.credential_generation != credential_generation:
            return False

        def decide(row: dict[str, Any], now: datetime) -> _Decision:
            deadline = row.get("refresh_deadline_at")
            if (
                row["refresh_state"] not in _INFLIGHT
                or row.get("refresh_attempt_id") != attempt_id
                or deadline is None
                or deadline <= now
            ):
                return _Decision(False)
            row.update(
                refresh_state=RefreshState.RUNNING.value,
                refresh_deadline_at=now + ATTEMPT_DEADLINE,
            )
            return _Decision(True)

        try:
            return (await self._transact(profile, decide)).result
        except _ProfileChanged:
            return False

    @staticmethod
    def _owns_attempt(row: Mapping[str, Any], attempt_id: str, now: datetime) -> bool:
        deadline = row.get("refresh_deadline_at")
        return (
            row["refresh_state"] in _INFLIGHT
            and row.get("refresh_attempt_id") == attempt_id
            and deadline is not None
            and now < deadline
        )

    async def record_collection_failure(
        self,
        *,
        profile_id: str,
        credential_generation: int,
        attempt_id: str,
        code: AccountUsageFailureCode,
        retry_after: Any = None,
    ) -> FailureResult:
        """Record a typed failure and back off; cached data is retained.

        Only this table changes: a monitoring 429 never touches the
        scheduler's inference cooldown, readiness, or profile state.
        """

        profile = (await self._profiles([profile_id])).get(profile_id)
        if profile is None:
            return FailureResult(False, ObservationRejection.PROFILE_MISSING)
        if profile.credential_generation != credential_generation:
            return FailureResult(False, ObservationRejection.STALE_GENERATION)

        def apply(row: dict[str, Any], now: datetime, *, owned: bool) -> _Decision:
            if not owned:
                return _Decision(
                    FailureResult(False, ObservationRejection.OWNER_EXPIRED)
                )
            failures = row["consecutive_failures"] + 1
            delay = backoff_delay(
                consecutive_failures=failures,
                retry_after=parse_retry_after(retry_after, now=now),
                rng=self._rng,
            )
            row.update(
                refresh_state=RefreshState.IDLE.value,
                refresh_attempt_id=None,
                refresh_deadline_at=None,
                last_failure_code=code.value,
                last_failure_at=now,
                consecutive_failures=failures,
                next_allowed_at=now + delay,
            )
            return _Decision(FailureResult(True, next_allowed_at=now + delay))

        try:
            decision = await self._transact(
                profile,
                lambda row, now: apply(
                    row, now, owned=self._owns_attempt(row, attempt_id, now)
                ),
            )
        except _ProfileChanged as changed:
            return FailureResult(
                False,
                (
                    ObservationRejection.PROFILE_MISSING
                    if changed.profile is None
                    else ObservationRejection.STALE_GENERATION
                ),
            )
        if decision.result.accepted:
            row = (await self._rows([profile_id])).get(profile_id) or {}
            for peer, _ in await self._verified_peers(profile, row.get("account_key")):
                try:
                    await self._transact(
                        peer,
                        lambda row, now: (
                            apply(
                                row, now, owned=self._owns_attempt(row, attempt_id, now)
                            )
                            if row.get("refresh_attempt_id") == attempt_id
                            else _Decision(None)
                        ),
                    )
                except _ProfileChanged:
                    continue
        return decision.result

    # -- observation ingestion ----------------------------------------------

    async def ingest_observation(
        self, observation: AccountUsageObservation
    ) -> IngestResult:
        """Fence and merge one collector or native observation promptly.

        Ingestion never requests collection, so native observations and
        duplicate notifications cannot start a feedback loop.
        """

        profile = (await self._profiles([observation.profile_id])).get(
            observation.profile_id
        )
        if profile is None:
            return IngestResult(False, ObservationRejection.PROFILE_MISSING)
        if observation.credential_generation != profile.credential_generation:
            return IngestResult(False, ObservationRejection.STALE_GENERATION)
        if (
            observation.provider_id != profile.provider_id
            or observation.runtime_id != profile.runtime_id
        ):
            return IngestResult(False, ObservationRejection.ACCOUNT_MISMATCH)
        await self._ensure_rows([profile])
        authority = observation.authority
        identity = observation.identity
        trusted_account = observation.account_id or (
            identity.account_id if identity is not None and identity.verified else None
        )
        trusted_workspace = observation.workspace_id or (
            identity.workspace_id
            if identity is not None and identity.verified
            else None
        )
        reported_accounts = {
            value
            for value in (
                observation.account_id,
                identity.account_id if identity is not None else None,
            )
            if value
        }
        reported_workspaces = {
            value
            for value in (
                observation.workspace_id,
                identity.workspace_id if identity is not None else None,
            )
            if value
        }

        def decide(row: dict[str, Any], now: datetime) -> _Decision:
            completes_attempt = False
            if authority.kind == "refresh_attempt":
                if not self._owns_attempt(row, authority.attempt_id or "", now):
                    return _Decision(
                        IngestResult(False, ObservationRejection.OWNER_EXPIRED)
                    )
                completes_attempt = True
            elif (
                authority.owner_expires_at is None or authority.owner_expires_at <= now
            ):
                return _Decision(
                    IngestResult(False, ObservationRejection.OWNER_EXPIRED)
                )

            bound_account = row.get("bound_account_id")
            bound_workspace = row.get("bound_workspace_id")
            if len(reported_accounts) > 1 or len(reported_workspaces) > 1:
                return _Decision(
                    IngestResult(False, ObservationRejection.ACCOUNT_MISMATCH)
                )
            if (
                bound_account
                and reported_accounts
                and reported_accounts != {bound_account}
            ):
                return _Decision(
                    IngestResult(False, ObservationRejection.ACCOUNT_MISMATCH)
                )
            if (
                bound_workspace
                and reported_workspaces
                and reported_workspaces != {bound_workspace}
            ):
                return _Decision(
                    IngestResult(False, ObservationRejection.ACCOUNT_MISMATCH)
                )

            usage_changed = identity_changed = False
            if observation.usage is not None:
                merged = merge_usage(row.get("usage_json"), observation.usage)
                if merged.rejection is not None:
                    return _Decision(IngestResult(False, merged.rejection))
                usage_changed = merged.changed
                row["usage_json"] = merged.usage
            if identity is not None:
                row["identity_json"], identity_changed = merge_identity(
                    row.get("identity_json"), identity
                )
            account = bound_account or trusted_account
            workspace = bound_workspace or trusted_workspace
            if account and (account, workspace) != (bound_account, bound_workspace):
                # Bind on first trust, and re-key once a workspace is learned
                # so profiles on different workspaces stop sharing usage.
                row["bound_account_id"] = account
                row["bound_workspace_id"] = workspace
                row["account_key"] = _account_key(
                    profile.provider_id, account, workspace
                )
            changed = usage_changed or identity_changed
            if changed:
                row["snapshot_revision"] += 1
            full = (
                observation.usage is not None
                and observation.usage.completeness is UsageCompleteness.FULL
            )
            if completes_attempt:
                row.update(
                    refresh_state=RefreshState.IDLE.value,
                    refresh_attempt_id=None,
                    refresh_deadline_at=None,
                    last_success_at=now,
                    last_failure_code=None,
                    consecutive_failures=0,
                    next_allowed_at=None,
                )
            else:
                if row["refresh_state"] == RefreshState.DEFERRED.value:
                    # The owning runtime serviced the deferred request.
                    row.update(
                        refresh_state=RefreshState.IDLE.value,
                        refresh_attempt_id=None,
                        refresh_deadline_at=None,
                    )
                if full:
                    row.update(
                        last_success_at=now,
                        last_failure_code=None,
                        consecutive_failures=0,
                        next_allowed_at=None,
                    )
            return _Decision(
                IngestResult(
                    True, changed=changed, snapshot_revision=row["snapshot_revision"]
                ),
                extra={
                    "usage_changed": usage_changed,
                    "account_key": row.get("account_key"),
                },
            )

        try:
            decision = await self._transact(profile, decide)
        except _ProfileChanged as changed:
            return IngestResult(
                False,
                (
                    ObservationRejection.PROFILE_MISSING
                    if changed.profile is None
                    else ObservationRejection.STALE_GENERATION
                ),
            )
        result: IngestResult = decision.result
        attempt_id = (
            authority.attempt_id if authority.kind == "refresh_attempt" else None
        )
        # Profiles coalesced onto this attempt complete with it, even when
        # the collected usage matches what is already cached.
        if not result.accepted or not (decision.extra["usage_changed"] or attempt_id):
            return result
        shared = await self._share_usage(
            profile,
            decision.extra["account_key"],
            observation.usage,
            attempt_id=attempt_id,
        )
        return IngestResult(
            True,
            changed=result.changed,
            snapshot_revision=result.snapshot_revision,
            shared_with=tuple(shared),
        )

    async def _share_usage(
        self,
        profile: AccountUsageProfileRef,
        account_key: str | None,
        usage: UsageInput | None,
        *,
        attempt_id: str | None,
    ) -> list[str]:
        """Share usage only with profiles on the same verified account.

        Peers coalesced onto ``attempt_id`` complete with it, whether or not
        their cached usage changes.
        """

        shared = []
        for peer, _ in await self._verified_peers(profile, account_key):

            def decide(row: dict[str, Any], now: datetime) -> _Decision:
                if row.get("account_key") != account_key:
                    return _Decision(False)
                changed = False
                if usage is not None:
                    merged = merge_usage(row.get("usage_json"), usage)
                    changed = merged.rejection is None and merged.changed
                    if changed:
                        row["usage_json"] = merged.usage
                        row["snapshot_revision"] += 1
                if attempt_id and row.get("refresh_attempt_id") == attempt_id:
                    row.update(
                        refresh_state=RefreshState.IDLE.value,
                        refresh_attempt_id=None,
                        refresh_deadline_at=None,
                        last_success_at=now,
                        last_failure_code=None,
                        consecutive_failures=0,
                        next_allowed_at=None,
                    )
                return _Decision(changed)

            try:
                if (await self._transact(peer, decide)).result:
                    shared.append(peer.profile_id)
            except _ProfileChanged:
                continue
        return shared


async def request_initial_account_usage(
    session: AsyncSession, profile_id: str
) -> RefreshResult | None:
    """OAuth-success hook, called after enrollment ownership is released."""

    try:
        results = await ProviderAccountUsageService(session).request_refresh(
            [profile_id], trigger=RefreshTrigger.OAUTH
        )
    except Exception as exc:
        await session.rollback()
        logger.warning(
            "Initial account usage request failed for profile %s: %s",
            profile_id,
            type(exc).__name__,
        )
        return None
    return results[0]
