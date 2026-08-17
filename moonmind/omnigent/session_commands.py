"""Durable, fenced, idempotent logical commands for ``MoonMind.OmnigentSession``.

Implements the minimal command/fencing dispatch layer required by GitHub issue
MoonLadderStudios/MoonMind#3705 (the reusable core attributed to #3703). Every
side-effect the session workflow performs runs through
:class:`OmnigentSessionCommandExecutor`, which:

* validates the command's expected fencing generation against durable state and
  rejects a delayed command that carries a stale generation
  (:class:`OmnigentSessionFencedError`);
* de-duplicates by idempotency key so a command retried after a crash reuses the
  recorded outcome rather than creating a duplicate side effect; and
* persists the resulting frontier so the workflow, the store, and the provider
  cannot diverge.

The heavy provider/host realization stays behind :class:`OmnigentSessionProviderPort`
so it can be a hermetic fake in tests and, in production, a thin adapter over the
existing profile-bound coordinator without reimplementing its semantics.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from moonmind.omnigent.session_reconciler import (
    OmnigentSessionCommand,
    OmnigentSessionCommandCondition,
    OmnigentSessionCommandKind,
    OmnigentSessionFrontier,
    OmnigentSessionIntent,
)


class OmnigentSessionCommandError(Exception):
    """Base error for session command execution."""


class OmnigentSessionFencedError(OmnigentSessionCommandError):
    """Raised when a command's expected generation is stale.

    Non-retryable: the workflow has already advanced to a newer fencing
    generation, so this (delayed) command must not mutate state.
    """


class OmnigentSessionCommandUnavailableError(OmnigentSessionCommandError):
    """Raised when no provider port is configured (fail closed)."""


class OmnigentSessionCommandOutcome(BaseModel):
    """Result of executing one bounded command."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    condition: OmnigentSessionCommandCondition = Field(
        default=OmnigentSessionCommandCondition.OK
    )
    frontier_updates: dict[str, Any] = Field(default_factory=dict, alias="frontierUpdates")
    bump_generation: bool = Field(default=False, alias="bumpGeneration")
    result_ref: str | None = Field(default=None, alias="resultRef")

    def merged_frontier(self, frontier: OmnigentSessionFrontier) -> OmnigentSessionFrontier:
        """Return a copy of ``frontier`` with this outcome's updates applied."""

        updated = frontier.model_copy(update=dict(self.frontier_updates))
        if self.bump_generation:
            updated = updated.model_copy(
                update={"fencing_generation": frontier.fencing_generation + 1}
            )
        return updated


@runtime_checkable
class OmnigentSessionProviderPort(Protocol):
    """Side-effecting operations for one bounded command kind.

    Implementations perform the real host/provider/lease work. They receive the
    current frontier and return the frontier deltas plus a condition. They must
    be safe to call at every command window (idempotency is enforced by the
    executor, but the underlying operation should still reconcile durable
    external identity before creating a new side effect).
    """

    async def execute(
        self,
        kind: OmnigentSessionCommandKind,
        intent: OmnigentSessionIntent,
        command: OmnigentSessionCommand,
        frontier: OmnigentSessionFrontier,
    ) -> OmnigentSessionCommandOutcome:
        ...


class OmnigentSessionStateRecord(BaseModel):
    """Durable per-session projection used for fencing and idempotency."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    canonical_session_id: str = Field(alias="canonicalSessionId")
    generation: int = Field(default=1, ge=1)
    frontier: OmnigentSessionFrontier = Field(
        default_factory=OmnigentSessionFrontier
    )
    applied_keys: list[str] = Field(default_factory=list, alias="appliedKeys")


@runtime_checkable
class OmnigentSessionStore(Protocol):
    """Durable store for the per-session fencing/idempotency record."""

    async def load(self, canonical_session_id: str) -> OmnigentSessionStateRecord | None:
        ...

    async def save(self, record: OmnigentSessionStateRecord) -> None:
        ...


class InMemoryOmnigentSessionStore:
    """Hermetic in-memory store for tests and deterministic verification."""

    def __init__(self) -> None:
        self._records: dict[str, OmnigentSessionStateRecord] = {}
        self._outcomes: dict[tuple[str, str], OmnigentSessionCommandOutcome] = {}

    async def load(self, canonical_session_id: str) -> OmnigentSessionStateRecord | None:
        record = self._records.get(canonical_session_id)
        return record.model_copy(deep=True) if record is not None else None

    async def save(self, record: OmnigentSessionStateRecord) -> None:
        self._records[record.canonical_session_id] = record.model_copy(deep=True)

    def cache_outcome(
        self,
        canonical_session_id: str,
        idempotency_key: str,
        outcome: OmnigentSessionCommandOutcome,
    ) -> None:
        self._outcomes[(canonical_session_id, idempotency_key)] = outcome

    def cached_outcome(
        self, canonical_session_id: str, idempotency_key: str
    ) -> OmnigentSessionCommandOutcome | None:
        return self._outcomes.get((canonical_session_id, idempotency_key))


class OmnigentSessionCommandExecutor:
    """Fenced, idempotent dispatcher for bounded session commands."""

    def __init__(
        self,
        *,
        store: OmnigentSessionStore,
        port: OmnigentSessionProviderPort,
    ) -> None:
        self._store = store
        self._port = port

    @property
    def store(self) -> OmnigentSessionStore:
        return self._store

    async def execute(
        self,
        intent: OmnigentSessionIntent,
        command: OmnigentSessionCommand,
    ) -> OmnigentSessionCommandOutcome:
        session_id = intent.canonical_session_id
        record = await self._store.load(session_id)
        if record is None:
            record = OmnigentSessionStateRecord(
                canonicalSessionId=session_id,
                generation=command.expected_generation,
                frontier=OmnigentSessionFrontier(
                    fencingGeneration=command.expected_generation
                ),
            )

        # Fencing: a delayed command from a superseded generation must not run.
        if command.expected_generation < record.generation:
            raise OmnigentSessionFencedError(
                f"command {command.kind.value} carries stale generation "
                f"{command.expected_generation}; current generation is {record.generation}"
            )
        if command.expected_generation > record.generation:
            # The workflow advanced the fencing generation; adopt it before
            # executing so the durable record tracks the authoritative epoch.
            record.generation = command.expected_generation
            record.frontier = record.frontier.model_copy(
                update={"fencing_generation": command.expected_generation}
            )

        # Idempotency: reuse the recorded outcome for a retried command.
        if command.idempotency_key in record.applied_keys:
            cached = self._cached_outcome(record, command.idempotency_key)
            if cached is not None:
                return cached
            return OmnigentSessionCommandOutcome()

        outcome = await self._port.execute(
            command.kind, intent, command, record.frontier
        )

        record.frontier = outcome.merged_frontier(record.frontier)
        if outcome.bump_generation:
            record.generation = record.frontier.fencing_generation
        record.applied_keys.append(command.idempotency_key)
        # Bound the applied-key ledger; older keys cannot recur for a completed
        # generation because idempotency keys embed the generation.
        if len(record.applied_keys) > 256:
            record.applied_keys = record.applied_keys[-256:]
        await self._store.save(record)
        self._cache_outcome(record, command.idempotency_key, outcome)
        return outcome

    def _cache_outcome(
        self,
        record: OmnigentSessionStateRecord,
        idempotency_key: str,
        outcome: OmnigentSessionCommandOutcome,
    ) -> None:
        cache = getattr(self._store, "cache_outcome", None)
        if callable(cache):
            cache(record.canonical_session_id, idempotency_key, outcome)

    def _cached_outcome(
        self, record: OmnigentSessionStateRecord, idempotency_key: str
    ) -> OmnigentSessionCommandOutcome | None:
        cached = getattr(self._store, "cached_outcome", None)
        if callable(cached):
            return cached(record.canonical_session_id, idempotency_key)
        return None


class NullOmnigentSessionProviderPort:
    """Fail-closed port used when no production adapter is configured."""

    async def execute(
        self,
        kind: OmnigentSessionCommandKind,
        intent: OmnigentSessionIntent,
        command: OmnigentSessionCommand,
        frontier: OmnigentSessionFrontier,
    ) -> OmnigentSessionCommandOutcome:
        del intent, command, frontier
        raise OmnigentSessionCommandUnavailableError(
            f"No Omnigent session provider port is configured for {kind.value}; "
            "the MoonMind.OmnigentSession workflow is disabled by default and "
            "must be wired to a provider adapter before admission is enabled."
        )


__all__ = [
    "InMemoryOmnigentSessionStore",
    "NullOmnigentSessionProviderPort",
    "OmnigentSessionCommandError",
    "OmnigentSessionCommandExecutor",
    "OmnigentSessionCommandOutcome",
    "OmnigentSessionCommandUnavailableError",
    "OmnigentSessionFencedError",
    "OmnigentSessionProviderPort",
    "OmnigentSessionStateRecord",
    "OmnigentSessionStore",
]
