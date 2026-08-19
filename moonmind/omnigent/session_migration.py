"""Idempotent, resumable Omnigent session migration planner and executor.

Source issue: MoonLadderStudios/MoonMind#3712.

This module turns a safe migration inventory
(:mod:`moonmind.omnigent.session_migration_inventory`) into a deterministic plan
of per-record actions and applies it through an injected canonical-session
repository (:class:`CanonicalMigrationRepository`). It supports the five
operator modes required by issue #3712::

    dry_run  apply  verify  resume  rollback_metadata

Guarantees:

* **Idempotent** — each action carries a stable idempotency key. Re-running
  ``apply``/``resume`` skips already-applied actions, so no canonical session,
  turn attempt, alias, or quarantine marker is duplicated.
* **Resumable** — a partial-failure run persists applied markers as it goes;
  re-running in ``resume`` mode continues from the failure without redoing
  completed work.
* **Evidence-preserving** — the executor only creates canonical records,
  aliases, quarantine markers, and cleanup markers. It never deletes legacy
  bridge rows or any canonical/legacy evidence. ``rollback_metadata`` emits a
  reversal manifest without destroying data.
* **Fail-closed on ambiguity** — ambiguous authority is quarantined, never
  resolved by recency.

The repository binding (create canonical session + first turn attempt, add safe
chat-binding alias, quarantine, mark cleanup) is the #3703 canonical control
plane. This module defines the migration contract over that repository so the
same executor drives dry-run and live application once #3703 repositories are
mounted.
"""

from __future__ import annotations

from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from moonmind.omnigent.session_migration_inventory import (
    InventoryClass,
    RecordInventoryView,
    classify_record,
)

MIGRATION_CONTRACT_VERSION = "moonmind.omnigent-session-migration/v1"

MigrationMode = Literal["dry_run", "apply", "verify", "resume", "rollback_metadata"]

_MIGRATION_MODES: frozenset[str] = frozenset(
    ("dry_run", "apply", "verify", "resume", "rollback_metadata")
)

PlannedActionKind = Literal[
    "noop_already_canonical",
    "create_canonical_session",
    "create_chat_alias",
    "retain_legacy_active",
    "retain_legacy_terminal",
    "quarantine",
    "mark_cleanup_required",
    "skip_unsupported",
]

# One action kind per inventory class. Deterministic and total.
_CLASS_ACTION: dict[InventoryClass, PlannedActionKind] = {
    InventoryClass.NEW_MODEL_READY: "noop_already_canonical",
    InventoryClass.CANONICALIZABLE: "create_canonical_session",
    InventoryClass.ALIAS_REQUIRED: "create_chat_alias",
    InventoryClass.LEGACY_ACTIVE: "retain_legacy_active",
    InventoryClass.LEGACY_TERMINAL_READABLE: "retain_legacy_terminal",
    InventoryClass.AMBIGUOUS_AUTHORITY: "quarantine",
    InventoryClass.CLEANUP_REQUIRED: "mark_cleanup_required",
    InventoryClass.UNSUPPORTED_OR_CORRUPT: "skip_unsupported",
}


def parse_migration_mode(value: object) -> MigrationMode:
    """Resolve a migration mode; unsupported values fail closed (Compatibility Policy)."""

    normalized = str(value or "").strip().lower().replace("-", "_")
    if normalized not in _MIGRATION_MODES:
        raise ValueError(f"unsupported Omnigent session migration mode: {value!r}")
    return normalized  # type: ignore[return-value]


class PlannedAction(BaseModel):
    """One deterministic, idempotent migration action for a single record."""

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    record_id: str = Field(alias="recordId")
    inventory_class: InventoryClass = Field(alias="inventoryClass")
    kind: PlannedActionKind
    idempotency_key: str = Field(alias="idempotencyKey")
    creates_canonical_session: bool = Field(alias="createsCanonicalSession")
    creates_chat_alias: bool = Field(alias="createsChatAlias")
    quarantines: bool = Field(alias="quarantines")


class MigrationPlan(BaseModel):
    """The full ordered set of planned actions plus bounded counts."""

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    contract_version: str = Field(MIGRATION_CONTRACT_VERSION, alias="contractVersion")
    actions: tuple[PlannedAction, ...] = Field(default_factory=tuple)

    def summary(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for action in self.actions:
            counts[action.kind] = counts.get(action.kind, 0) + 1
        return counts


class RollbackManifestEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    record_id: str = Field(alias="recordId")
    idempotency_key: str = Field(alias="idempotencyKey")
    kind: PlannedActionKind
    reversible_marker: str = Field(alias="reversibleMarker")


class MigrationResult(BaseModel):
    """Outcome of one migration run in a given mode."""

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    mode: MigrationMode
    planned: int = Field(ge=0)
    applied: int = Field(0, ge=0)
    skipped_already_applied: int = Field(0, ge=0, alias="skippedAlreadyApplied")
    created_canonical_sessions: int = Field(0, ge=0, alias="createdCanonicalSessions")
    created_aliases: int = Field(0, ge=0, alias="createdAliases")
    quarantined: int = Field(0, ge=0)
    cleanup_marked: int = Field(0, ge=0, alias="cleanupMarked")
    discrepancies: tuple[str, ...] = Field(default_factory=tuple)
    rollback_manifest: tuple[RollbackManifestEntry, ...] = Field(
        default_factory=tuple, alias="rollbackManifest"
    )
    evidence_preserved: bool = Field(True, alias="evidencePreserved")

    def as_dict(self) -> dict[str, object]:
        return self.model_dump(by_alias=True)


@runtime_checkable
class CanonicalMigrationRepository(Protocol):
    """Side-effect surface the executor drives, bound to the #3703 control plane.

    Every method must be idempotent on its own; the executor additionally guards
    each action with an applied-marker check so a resumed run never duplicates a
    side effect.
    """

    async def is_applied(self, idempotency_key: str) -> bool:
        """Whether an action's idempotency marker is already recorded."""

    async def record_applied(self, idempotency_key: str) -> None:
        """Persist an action's idempotency marker after its side effects succeed."""

    async def create_canonical_session(self, record_id: str) -> None:
        """Create the canonical session and its first turn attempt for a record."""

    async def create_chat_alias(self, record_id: str) -> None:
        """Create a safe chat-binding alias for a record."""

    async def quarantine(self, record_id: str) -> None:
        """Mark an ambiguous-authority group quarantined (never newest-wins)."""

    async def mark_cleanup_required(self, record_id: str) -> None:
        """Record that a legacy record still owns pending cleanup authority."""

    async def has_canonical_session(self, record_id: str) -> bool:
        """Whether the canonical session for a record has been created."""

    async def has_chat_alias(self, record_id: str) -> bool:
        """Whether the safe chat-binding alias for a record has been created."""

    async def is_quarantined(self, record_id: str) -> bool:
        """Whether an ambiguous-authority record is marked quarantined."""

    async def has_cleanup_marker(self, record_id: str) -> bool:
        """Whether the pending-cleanup marker for a record has been persisted."""


def _idempotency_key(record_id: str, kind: PlannedActionKind) -> str:
    return f"{MIGRATION_CONTRACT_VERSION}:{record_id}:{kind}"


def plan_action(view: RecordInventoryView) -> PlannedAction:
    """Plan the single deterministic action for one record."""

    inventory_class = classify_record(view)
    kind = _CLASS_ACTION[inventory_class]
    # A canonicalizable record whose chat binding needs aliasing creates both the
    # canonical session and its safe alias under one action.
    creates_alias = kind == "create_chat_alias" or (
        kind == "create_canonical_session" and view.requires_chat_alias
    )
    return PlannedAction(
        recordId=view.record_id,
        inventoryClass=inventory_class,
        kind=kind,
        idempotencyKey=_idempotency_key(view.record_id, kind),
        createsCanonicalSession=kind == "create_canonical_session",
        createsChatAlias=creates_alias,
        quarantines=kind == "quarantine",
    )


def plan_migration(views: list[RecordInventoryView]) -> MigrationPlan:
    """Build the deterministic migration plan from a safe inventory."""

    return MigrationPlan(actions=tuple(plan_action(view) for view in views))


async def _apply_action(
    action: PlannedAction, repo: CanonicalMigrationRepository
) -> None:
    """Perform an action's side effects, then record the applied marker.

    The applied marker is recorded only after the side effects succeed, so a
    partial-failure run leaves completed records marked and the failed record
    unmarked for a clean resume.
    """

    if action.creates_canonical_session:
        await repo.create_canonical_session(action.record_id)
    if action.creates_chat_alias:
        await repo.create_chat_alias(action.record_id)
    if action.quarantines:
        await repo.quarantine(action.record_id)
    if action.kind == "mark_cleanup_required":
        await repo.mark_cleanup_required(action.record_id)
    await repo.record_applied(action.idempotency_key)


async def execute_migration(
    plan: MigrationPlan,
    repo: CanonicalMigrationRepository,
    *,
    mode: MigrationMode,
) -> MigrationResult:
    """Execute the plan in the requested mode.

    ``dry_run`` performs no writes. ``apply``/``resume`` are identical
    idempotent passes (``resume`` continues a partial run). ``verify`` checks the
    persisted end-state. ``rollback_metadata`` emits a reversal manifest and
    performs no writes.
    """

    mode = parse_migration_mode(mode)
    planned = len(plan.actions)

    if mode == "dry_run":
        summary = plan.summary()
        return MigrationResult(
            mode=mode,
            planned=planned,
            createdCanonicalSessions=summary.get("create_canonical_session", 0),
            createdAliases=sum(1 for a in plan.actions if a.creates_chat_alias),
            quarantined=summary.get("quarantine", 0),
            cleanupMarked=summary.get("mark_cleanup_required", 0),
        )

    if mode == "verify":
        discrepancies: list[str] = []
        for action in plan.actions:
            if action.creates_canonical_session and not await repo.has_canonical_session(
                action.record_id
            ):
                discrepancies.append(f"missing_canonical_session:{action.record_id}")
            if action.creates_chat_alias and not await repo.has_chat_alias(
                action.record_id
            ):
                discrepancies.append(f"missing_chat_alias:{action.record_id}")
            if action.quarantines and not await repo.is_quarantined(action.record_id):
                discrepancies.append(f"missing_quarantine:{action.record_id}")
            if action.kind == "mark_cleanup_required" and not (
                await repo.has_cleanup_marker(action.record_id)
            ):
                discrepancies.append(f"missing_cleanup_marker:{action.record_id}")
        return MigrationResult(
            mode=mode, planned=planned, discrepancies=tuple(discrepancies)
        )

    if mode == "rollback_metadata":
        # Only actions that actually ran (their idempotency marker is present in
        # the repository) may appear in the reversal manifest. A partial-failure
        # run leaves later planned side effects unapplied and unmarked; emitting
        # reversal entries for them would let a consumer try to reverse work that
        # never happened, including pre-existing unrelated state.
        manifest_entries: list[RollbackManifestEntry] = []
        for action in plan.actions:
            if not (
                action.creates_canonical_session
                or action.creates_chat_alias
                or action.quarantines
            ):
                continue
            if not await repo.is_applied(action.idempotency_key):
                continue
            manifest_entries.append(
                RollbackManifestEntry(
                    recordId=action.record_id,
                    idempotencyKey=action.idempotency_key,
                    kind=action.kind,
                    reversibleMarker=action.idempotency_key,
                )
            )
        manifest = tuple(manifest_entries)
        # Rollback metadata never deletes evidence; it only describes reversal.
        return MigrationResult(
            mode=mode,
            planned=planned,
            rollbackManifest=manifest,
            evidencePreserved=True,
        )

    # apply / resume — identical idempotent side-effecting pass.
    applied = 0
    skipped = 0
    created_sessions = 0
    created_aliases = 0
    quarantined = 0
    cleanup_marked = 0
    for action in plan.actions:
        if action.kind in ("noop_already_canonical", "retain_legacy_active",
                           "retain_legacy_terminal", "skip_unsupported"):
            # No durable side effect; nothing to apply or duplicate.
            continue
        if await repo.is_applied(action.idempotency_key):
            skipped += 1
            continue
        await _apply_action(action, repo)
        applied += 1
        if action.creates_canonical_session:
            created_sessions += 1
        if action.creates_chat_alias:
            created_aliases += 1
        if action.quarantines:
            quarantined += 1
        if action.kind == "mark_cleanup_required":
            cleanup_marked += 1

    return MigrationResult(
        mode=mode,
        planned=planned,
        applied=applied,
        skippedAlreadyApplied=skipped,
        createdCanonicalSessions=created_sessions,
        createdAliases=created_aliases,
        quarantined=quarantined,
        cleanupMarked=cleanup_marked,
    )


__all__ = [
    "MIGRATION_CONTRACT_VERSION",
    "MigrationMode",
    "PlannedActionKind",
    "PlannedAction",
    "MigrationPlan",
    "RollbackManifestEntry",
    "MigrationResult",
    "CanonicalMigrationRepository",
    "parse_migration_mode",
    "plan_action",
    "plan_migration",
    "execute_migration",
]
