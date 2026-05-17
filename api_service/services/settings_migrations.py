"""Settings catalog migration orchestrator.

Implements the rename / type-change / removal helpers required by
``docs/Security/SettingsSystem.md`` §24 (Migration and Deprecation). The
orchestrator copies overrides from ``old_key`` to ``new_key`` for renamed
settings, coerces values explicitly on type changes (no implicit JSON
reinterpretation), and records a ``SettingsAuditEvent`` for each migration
event it performs.

The orchestrator operates at the data layer only. It does **not** mutate
``SettingMigrationRule`` registrations or descriptor metadata; those continue
to be the source of truth for which keys are exposed and how reads resolve.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Literal
from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.db.models import SettingsAuditEvent, SettingsOverride
from api_service.services.settings_catalog import (
    SettingMigrationRule,
    SettingScope,
)

_DEFAULT_SUBJECT_ID = UUID("00000000-0000-0000-0000-000000000000")

SettingsMigrationEventType = Literal[
    "settings.migration.renamed",
    "settings.migration.type_changed",
    "settings.migration.removed",
]


class SettingsMigrationError(ValueError):
    """Raised when a migration cannot proceed safely."""


class SettingsMigrationCollisionError(SettingsMigrationError):
    """Raised when the rename target already has a persisted override."""


class SettingsMigrationCoercionError(SettingsMigrationError):
    """Raised when a type-change rule is applied without an explicit coercion."""


class SettingsMigrationOutcome(BaseModel):
    """Structured result for one applied migration row."""

    rule_old_key: str
    rule_new_key: str | None = None
    state: str
    event_type: SettingsMigrationEventType
    scope: SettingScope
    workspace_id: UUID
    user_id: UUID
    old_schema_version: int | None = None
    new_schema_version: int | None = None
    old_value: Any = None
    new_value: Any = None
    audit_event_id: UUID | None = None
    applied: bool = True


class SettingsMigrationReport(BaseModel):
    """Aggregated outcome of a ``run_all`` invocation."""

    outcomes: list[SettingsMigrationOutcome] = Field(default_factory=list)
    skipped_rules: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class TypeChangeCoercion:
    """Explicit coercion plan for a ``type_changed`` migration rule.

    Callers MUST supply ``coerce`` — the orchestrator never inspects or
    reinterprets the raw JSON value on its own. ``target_schema_version``
    matches the rule's ``expected_schema_version`` so that diagnostics stop
    reporting ``post_migration_invalid`` once the override is updated.
    """

    coerce: Callable[[Any], Any]
    target_schema_version: int


@dataclass
class SettingsMigrationOrchestrator:
    """Apply rename / type-change / removal rules to persisted overrides.

    The orchestrator is intentionally narrow: it only writes to the
    ``settings_overrides`` and ``settings_audit_events`` tables. Diagnostics
    surfaced through :class:`SettingsCatalogService` continue to derive from
    the same rule set, so the orchestrator and the read path stay in sync.
    """

    session: AsyncSession
    migration_rules: tuple[SettingMigrationRule, ...]
    actor_user_id: UUID | None = None
    request_id: str | None = None
    workspace_id: UUID = field(default=_DEFAULT_SUBJECT_ID)
    redact_keys: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        seen: set[str] = set()
        for rule in self.migration_rules:
            if rule.old_key in seen:
                raise SettingsMigrationError(
                    f"duplicate migration rule for {rule.old_key!r}"
                )
            seen.add(rule.old_key)
        self._rules_by_old_key = {rule.old_key: rule for rule in self.migration_rules}

    async def apply_rename(
        self,
        rule: SettingMigrationRule,
        *,
        on_collision: Literal["raise", "skip"] = "raise",
    ) -> list[SettingsMigrationOutcome]:
        """Copy overrides from ``rule.old_key`` to ``rule.new_key``.

        For every ``settings_overrides`` row that targets ``rule.old_key`` in
        this orchestrator's workspace, the helper:

        * Inserts an equivalent row keyed by ``rule.new_key`` (preserving
          ``scope``, ``user_id``, ``workspace_id``, ``value_json``,
          ``schema_version``, and ``value_version``), unless the target row
          already exists.
        * Deletes the source row so subsequent diagnostics see the rename as
          completed.
        * Records a ``settings.migration.renamed`` audit event referencing
          both keys for forensic visibility.

        Effective values are preserved across the cutover because the target
        row inherits the source row's ``value_json`` and ``value_version``.
        """

        self._require_state(rule, "renamed")
        if rule.new_key is None:
            raise SettingsMigrationError(
                f"rename rule for {rule.old_key!r} requires new_key"
            )

        outcomes: list[SettingsMigrationOutcome] = []
        old_rows = await self._fetch_overrides(rule.old_key)
        for row in old_rows:
            target = await self._fetch_override(
                scope=row.scope,
                user_id=row.user_id,
                key=rule.new_key,
            )
            if target is not None:
                if on_collision == "skip":
                    outcomes.append(
                        SettingsMigrationOutcome(
                            rule_old_key=rule.old_key,
                            rule_new_key=rule.new_key,
                            state=rule.state,
                            event_type="settings.migration.renamed",
                            scope=row.scope,
                            workspace_id=row.workspace_id,
                            user_id=row.user_id,
                            old_schema_version=row.schema_version,
                            new_schema_version=target.schema_version,
                            old_value=row.value_json,
                            new_value=target.value_json,
                            applied=False,
                        )
                    )
                    continue
                raise SettingsMigrationCollisionError(
                    f"rename target {rule.new_key!r} already has an override "
                    f"in scope {row.scope!r}"
                )

            old_value = row.value_json
            schema_version = row.schema_version
            value_version = row.value_version

            new_row = SettingsOverride(
                scope=row.scope,
                workspace_id=row.workspace_id,
                user_id=row.user_id,
                key=rule.new_key,
                value_json=old_value,
                schema_version=schema_version,
                value_version=value_version,
                created_by=row.created_by,
                updated_by=self.actor_user_id or row.updated_by,
            )
            self.session.add(new_row)
            await self.session.delete(row)

            event = self._record_audit_event(
                event_type="settings.migration.renamed",
                key=rule.old_key,
                scope=row.scope,
                workspace_id=row.workspace_id,
                user_id=row.user_id,
                old_value=old_value,
                new_value=old_value,
                reason=rule.message,
            )
            outcomes.append(
                SettingsMigrationOutcome(
                    rule_old_key=rule.old_key,
                    rule_new_key=rule.new_key,
                    state=rule.state,
                    event_type="settings.migration.renamed",
                    scope=row.scope,
                    workspace_id=row.workspace_id,
                    user_id=row.user_id,
                    old_schema_version=schema_version,
                    new_schema_version=schema_version,
                    old_value=old_value,
                    new_value=old_value,
                    audit_event_id=event.id,
                )
            )

        await self.session.flush()
        return outcomes

    async def apply_type_change(
        self,
        rule: SettingMigrationRule,
        coercion: TypeChangeCoercion,
    ) -> list[SettingsMigrationOutcome]:
        """Re-encode overrides for ``rule.old_key`` with an explicit coercion.

        The orchestrator never reinterprets the existing JSON value on its
        own. Callers must provide :class:`TypeChangeCoercion` carrying the
        ``coerce`` callable that maps the old value to the new value, and the
        ``target_schema_version`` (which MUST match
        ``rule.expected_schema_version``).
        """

        self._require_state(rule, "type_changed")
        if coercion.target_schema_version != rule.expected_schema_version:
            raise SettingsMigrationCoercionError(
                f"type_changed rule for {rule.old_key!r} expects schema "
                f"version {rule.expected_schema_version}, coercion targets "
                f"{coercion.target_schema_version}"
            )

        outcomes: list[SettingsMigrationOutcome] = []
        for row in await self._fetch_overrides(rule.old_key):
            old_value = row.value_json
            old_schema_version = row.schema_version
            new_value = coercion.coerce(old_value)
            row.value_json = new_value
            row.schema_version = coercion.target_schema_version
            row.value_version = row.value_version + 1
            row.updated_by = self.actor_user_id or row.updated_by

            event = self._record_audit_event(
                event_type="settings.migration.type_changed",
                key=rule.old_key,
                scope=row.scope,
                workspace_id=row.workspace_id,
                user_id=row.user_id,
                old_value=old_value,
                new_value=new_value,
                reason=rule.message,
            )
            outcomes.append(
                SettingsMigrationOutcome(
                    rule_old_key=rule.old_key,
                    rule_new_key=None,
                    state=rule.state,
                    event_type="settings.migration.type_changed",
                    scope=row.scope,
                    workspace_id=row.workspace_id,
                    user_id=row.user_id,
                    old_schema_version=old_schema_version,
                    new_schema_version=coercion.target_schema_version,
                    old_value=old_value,
                    new_value=new_value,
                    audit_event_id=event.id,
                )
            )

        await self.session.flush()
        return outcomes

    async def apply_removal(
        self,
        rule: SettingMigrationRule,
    ) -> list[SettingsMigrationOutcome]:
        """Record removal migration events for diagnostic continuity.

        New writes to removed keys are already rejected by the registry, so
        the orchestrator only stamps an audit trail. Existing override rows
        are preserved so deprecation diagnostics can still surface broken
        references after a partial restore.
        """

        self._require_state(rule, "removed", "deprecated")
        outcomes: list[SettingsMigrationOutcome] = []
        for row in await self._fetch_overrides(rule.old_key):
            event = self._record_audit_event(
                event_type="settings.migration.removed",
                key=rule.old_key,
                scope=row.scope,
                workspace_id=row.workspace_id,
                user_id=row.user_id,
                old_value=row.value_json,
                new_value=row.value_json,
                reason=rule.message,
            )
            outcomes.append(
                SettingsMigrationOutcome(
                    rule_old_key=rule.old_key,
                    rule_new_key=None,
                    state=rule.state,
                    event_type="settings.migration.removed",
                    scope=row.scope,
                    workspace_id=row.workspace_id,
                    user_id=row.user_id,
                    old_schema_version=row.schema_version,
                    new_schema_version=row.schema_version,
                    old_value=row.value_json,
                    new_value=row.value_json,
                    audit_event_id=event.id,
                )
            )

        await self.session.flush()
        return outcomes

    async def run_all(
        self,
        *,
        type_coercions: dict[str, TypeChangeCoercion] | None = None,
        on_collision: Literal["raise", "skip"] = "raise",
    ) -> SettingsMigrationReport:
        """Apply every rename/type-change/removal rule in registration order.

        ``type_coercions`` MUST contain an explicit coercion for each
        ``type_changed`` rule encountered. Rules without a coercion are
        recorded as skipped — the caller is expected to surface this as a
        deployment-blocking error rather than silently reinterpret values.
        """

        coercions = type_coercions or {}
        report = SettingsMigrationReport()
        for rule in self.migration_rules:
            if rule.state == "renamed":
                outcomes = await self.apply_rename(rule, on_collision=on_collision)
                report.outcomes.extend(outcomes)
            elif rule.state == "type_changed":
                coercion = coercions.get(rule.old_key)
                if coercion is None:
                    report.skipped_rules.append(rule.old_key)
                    continue
                outcomes = await self.apply_type_change(rule, coercion)
                report.outcomes.extend(outcomes)
            elif rule.state in {"removed", "deprecated"}:
                outcomes = await self.apply_removal(rule)
                report.outcomes.extend(outcomes)
        return report

    async def _fetch_overrides(self, key: str) -> list[SettingsOverride]:
        result = await self.session.execute(
            select(SettingsOverride).where(
                SettingsOverride.key == key,
                SettingsOverride.workspace_id == self.workspace_id,
            )
        )
        return list(result.scalars().all())

    async def _fetch_override(
        self,
        *,
        scope: str,
        user_id: UUID,
        key: str,
    ) -> SettingsOverride | None:
        result = await self.session.execute(
            select(SettingsOverride).where(
                SettingsOverride.scope == scope,
                SettingsOverride.workspace_id == self.workspace_id,
                SettingsOverride.user_id == user_id,
                SettingsOverride.key == key,
            )
        )
        return result.scalars().first()

    def _record_audit_event(
        self,
        *,
        event_type: SettingsMigrationEventType,
        key: str,
        scope: str,
        workspace_id: UUID,
        user_id: UUID,
        old_value: Any,
        new_value: Any,
        reason: str | None,
    ) -> SettingsAuditEvent:
        redacted = key in self.redact_keys
        event = SettingsAuditEvent(
            event_type=event_type,
            key=key,
            scope=scope,
            workspace_id=workspace_id,
            user_id=user_id,
            actor_user_id=self.actor_user_id,
            old_value_json=None if redacted else old_value,
            new_value_json=None if redacted else new_value,
            redacted=redacted,
            reason=reason,
            request_id=self.request_id,
        )
        self.session.add(event)
        return event

    def _require_state(
        self,
        rule: SettingMigrationRule,
        *expected: str,
    ) -> None:
        if rule.state not in expected:
            joined = "/".join(expected)
            raise SettingsMigrationError(
                f"migration rule for {rule.old_key!r} has state "
                f"{rule.state!r}; expected one of {joined}"
            )


__all__ = [
    "SettingsMigrationCoercionError",
    "SettingsMigrationCollisionError",
    "SettingsMigrationError",
    "SettingsMigrationEventType",
    "SettingsMigrationOrchestrator",
    "SettingsMigrationOutcome",
    "SettingsMigrationReport",
    "TypeChangeCoercion",
]
