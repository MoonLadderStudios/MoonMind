"""Settings backup snapshot and broken-reference scan helpers.

Implements the operator-facing pieces required by
``docs/Security/SettingsSystem.md`` §23 (Backup and Recovery):

* :func:`export_settings_backup` produces a serializable
  :class:`SettingsBackupBundle` from ``settings_overrides`` and
  ``settings_audit_events`` while enforcing the backup-exclusion policy
  (no managed-secret plaintext, no leaked audit payloads on rows marked
  redacted).
* :func:`scan_broken_references` walks every persisted override and reports
  the ones whose SecretRef or provider-profile target is missing or in a
  non-launchable state, so operators can surface broken references after a
  partial restore.

The helpers intentionally operate at the data layer and do not load the
full :class:`SettingsCatalogService`. They consume a
:class:`SettingsRegistry` (or descriptor tuple) for descriptor metadata and
fetch override / managed-secret / provider-profile rows directly.
"""

from __future__ import annotations

import os
import re
from datetime import datetime
from typing import Any, Literal, Mapping
from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.db.models import (
    ManagedAgentProviderProfile,
    ManagedSecret,
    SecretStatus,
    SettingsAuditEvent,
    SettingsOverride,
)
from api_service.services.settings_catalog import (
    SettingRegistryEntry,
    SettingsRegistry,
)


_PROVIDER_PROFILE_REF_KEY = "workflow.default_provider_profile_ref"

_SECRET_REF_SCHEME_RE = re.compile(r"^(?P<scheme>env|db|exec|oauth_volume)://(?P<rest>.*)$")


class SettingsBackupViolation(ValueError):
    """Raised when a settings backup would leak plaintext credentials."""


class SettingsBackupOverrideRecord(BaseModel):
    """One row from ``settings_overrides`` formatted for backup export."""

    key: str
    scope: str
    workspace_id: UUID
    user_id: UUID
    value_json: Any = None
    schema_version: int
    value_version: int
    is_secret_ref: bool = False
    created_at: datetime | None = None
    updated_at: datetime | None = None


class SettingsBackupAuditRecord(BaseModel):
    """One row from ``settings_audit_events`` formatted for backup export."""

    event_type: str
    key: str
    scope: str
    workspace_id: UUID
    user_id: UUID
    actor_user_id: UUID | None = None
    old_value_json: Any = None
    new_value_json: Any = None
    redacted: bool = False
    reason: str | None = None
    request_id: str | None = None
    created_at: datetime | None = None


class SettingsBackupBundle(BaseModel):
    """Serializable backup snapshot of the Settings System tables."""

    overrides: list[SettingsBackupOverrideRecord] = Field(default_factory=list)
    audit_events: list[SettingsBackupAuditRecord] = Field(default_factory=list)
    excluded_keys: list[str] = Field(default_factory=list)


class SettingsBrokenReference(BaseModel):
    """A persisted override whose target dependency is missing or unusable."""

    key: str
    scope: str
    workspace_id: UUID
    user_id: UUID
    value: Any
    code: Literal[
        "unresolved_secret_ref",
        "provider_profile_not_found",
        "provider_profile_disabled",
        "invalid_secret_ref",
    ]
    severity: Literal["error", "warning"] = "error"
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


def _registry_entries(
    registry: SettingsRegistry | tuple[SettingRegistryEntry, ...],
) -> dict[str, SettingRegistryEntry]:
    if isinstance(registry, SettingsRegistry):
        return dict(registry.entries_by_key)
    return {entry.key: entry for entry in registry}


def _looks_like_secret_ref(value: str) -> bool:
    return bool(_SECRET_REF_SCHEME_RE.match(value))


async def export_settings_backup(
    *,
    session: AsyncSession,
    registry: SettingsRegistry | tuple[SettingRegistryEntry, ...],
) -> SettingsBackupBundle:
    """Build a :class:`SettingsBackupBundle` from the persisted Settings tables.

    Policy enforced by this helper:

    * Overrides whose descriptor declares ``audit.redact = True`` AND whose
      ``value_type`` is not ``secret_ref`` are dropped from the bundle and
      listed in ``excluded_keys`` — those descriptors may carry inline
      credentials that must never reach a backup artifact.
    * SecretRef-typed overrides remain in the bundle because their stored
      value is a reference (``env://`` / ``db://`` / etc.), not plaintext.
      If a SecretRef descriptor's persisted value does NOT look like a
      SecretRef the helper raises :class:`SettingsBackupViolation` instead
      of emitting a bundle that might leak the plaintext.
    * Audit events whose ``redacted`` flag is true MUST NOT carry plaintext
      payloads. Any row violating that invariant raises
      :class:`SettingsBackupViolation`.
    """

    entries_by_key = _registry_entries(registry)

    overrides: list[SettingsBackupOverrideRecord] = []
    excluded_keys: set[str] = set()

    override_rows = (
        await session.execute(select(SettingsOverride).order_by(SettingsOverride.key))
    ).scalars().all()
    for row in override_rows:
        entry = entries_by_key.get(row.key)
        is_secret_ref = entry is not None and entry.value_type == "secret_ref"
        sensitive_inline = (
            entry is not None
            and entry.audit.redact
            and entry.value_type != "secret_ref"
        )
        if sensitive_inline:
            excluded_keys.add(row.key)
            continue
        if is_secret_ref and row.value_json is not None:
            if not isinstance(row.value_json, str) or not _looks_like_secret_ref(
                row.value_json
            ):
                raise SettingsBackupViolation(
                    f"settings_overrides row for {row.key!r} carries a value that "
                    "does not look like a SecretRef; refusing to back up to avoid "
                    "leaking plaintext credentials."
                )
        overrides.append(
            SettingsBackupOverrideRecord(
                key=row.key,
                scope=row.scope,
                workspace_id=row.workspace_id,
                user_id=row.user_id,
                value_json=row.value_json,
                schema_version=row.schema_version,
                value_version=row.value_version,
                is_secret_ref=is_secret_ref,
                created_at=row.created_at,
                updated_at=row.updated_at,
            )
        )

    audit_rows = (
        await session.execute(
            select(SettingsAuditEvent).order_by(SettingsAuditEvent.created_at)
        )
    ).scalars().all()
    audit_records: list[SettingsBackupAuditRecord] = []
    for row in audit_rows:
        if row.redacted and (
            row.old_value_json is not None or row.new_value_json is not None
        ):
            raise SettingsBackupViolation(
                f"settings_audit_events row for {row.key!r} is marked redacted but "
                "carries a non-null value payload; refusing to back up to avoid "
                "leaking plaintext audit data."
            )
        audit_records.append(
            SettingsBackupAuditRecord(
                event_type=row.event_type,
                key=row.key,
                scope=row.scope,
                workspace_id=row.workspace_id,
                user_id=row.user_id,
                actor_user_id=row.actor_user_id,
                old_value_json=row.old_value_json,
                new_value_json=row.new_value_json,
                redacted=row.redacted,
                reason=row.reason,
                request_id=row.request_id,
                created_at=row.created_at,
            )
        )

    return SettingsBackupBundle(
        overrides=overrides,
        audit_events=audit_records,
        excluded_keys=sorted(excluded_keys),
    )


async def scan_broken_references(
    *,
    session: AsyncSession,
    registry: SettingsRegistry | tuple[SettingRegistryEntry, ...],
    env: Mapping[str, str] | None = None,
) -> list[SettingsBrokenReference]:
    """Return broken-reference diagnostics for every persisted override.

    The scan mirrors the per-request diagnostics produced by
    :class:`SettingsCatalogService` but operates across every workspace and
    user scope so operators can surface broken references after a partial
    restore (settings restored without dependencies, or vice versa). The
    returned list is sorted by ``(key, scope, user_id)`` for stable
    operator-facing rendering.
    """

    environment: Mapping[str, str] = env if env is not None else os.environ
    entries_by_key = _registry_entries(registry)

    override_rows = (
        await session.execute(select(SettingsOverride))
    ).scalars().all()

    db_slugs: set[str] = set()
    profile_ids: set[str] = set()
    for row in override_rows:
        entry = entries_by_key.get(row.key)
        value = row.value_json
        if not isinstance(value, str):
            continue
        if entry is not None and entry.value_type == "secret_ref":
            if value.startswith("db://"):
                slug = value.removeprefix("db://").strip()
                if slug:
                    db_slugs.add(slug)
        if row.key == _PROVIDER_PROFILE_REF_KEY:
            profile_id = value.strip()
            if profile_id:
                profile_ids.add(profile_id)

    secret_status_by_slug: dict[str, str | None] = {}
    if db_slugs:
        result = await session.execute(
            select(ManagedSecret.slug, ManagedSecret.status).where(
                ManagedSecret.slug.in_(db_slugs)
            )
        )
        for slug, status in result.all():
            secret_status_by_slug[slug] = (
                status.value if isinstance(status, SecretStatus) else str(status)
            )
        for slug in db_slugs:
            secret_status_by_slug.setdefault(slug, None)

    profile_enabled_by_id: dict[str, bool | None] = {}
    if profile_ids:
        result = await session.execute(
            select(
                ManagedAgentProviderProfile.profile_id,
                ManagedAgentProviderProfile.enabled,
            ).where(
                ManagedAgentProviderProfile.profile_id.in_(profile_ids)
            )
        )
        for profile_id, enabled in result.all():
            profile_enabled_by_id[profile_id] = bool(enabled) if enabled is not None else False
        for profile_id in profile_ids:
            profile_enabled_by_id.setdefault(profile_id, None)

    broken: list[SettingsBrokenReference] = []
    for row in override_rows:
        entry = entries_by_key.get(row.key)
        value = row.value_json
        if not isinstance(value, str):
            continue
        if entry is not None and entry.value_type == "secret_ref":
            diagnostic = _secret_ref_broken_reference(
                row=row,
                value=value,
                secret_status_by_slug=secret_status_by_slug,
                environment=environment,
            )
            if diagnostic is not None:
                broken.append(diagnostic)
        if row.key == _PROVIDER_PROFILE_REF_KEY:
            diagnostic = _provider_profile_broken_reference(
                row=row,
                value=value,
                profile_enabled_by_id=profile_enabled_by_id,
            )
            if diagnostic is not None:
                broken.append(diagnostic)

    broken.sort(key=lambda item: (item.key, item.scope, str(item.user_id)))
    return broken


def _secret_ref_broken_reference(
    *,
    row: SettingsOverride,
    value: str,
    secret_status_by_slug: Mapping[str, str | None],
    environment: Mapping[str, str],
) -> SettingsBrokenReference | None:
    if value.startswith("env://"):
        env_name = value.removeprefix("env://").strip()
        if not env_name or env_name not in environment:
            return SettingsBrokenReference(
                key=row.key,
                scope=row.scope,
                workspace_id=row.workspace_id,
                user_id=row.user_id,
                value=value,
                code="unresolved_secret_ref",
                message=(
                    f"{row.key} references an environment secret that is "
                    "not available."
                ),
                details={
                    "ref_scheme": "env",
                    "env_name": env_name or None,
                },
            )
        return None
    if value.startswith("db://"):
        slug = value.removeprefix("db://").strip()
        if not slug:
            return SettingsBrokenReference(
                key=row.key,
                scope=row.scope,
                workspace_id=row.workspace_id,
                user_id=row.user_id,
                value=value,
                code="invalid_secret_ref",
                message=f"{row.key} carries an empty SecretRef slug.",
                details={"ref_scheme": "db"},
            )
        status = secret_status_by_slug.get(slug)
        if status is None:
            return SettingsBrokenReference(
                key=row.key,
                scope=row.scope,
                workspace_id=row.workspace_id,
                user_id=row.user_id,
                value=value,
                code="unresolved_secret_ref",
                message=f"{row.key} references a managed secret that does not exist.",
                details={
                    "ref_scheme": "db",
                    "slug": slug,
                    "status": "missing",
                },
            )
        if status != SecretStatus.ACTIVE.value:
            return SettingsBrokenReference(
                key=row.key,
                scope=row.scope,
                workspace_id=row.workspace_id,
                user_id=row.user_id,
                value=value,
                code="unresolved_secret_ref",
                message=f"{row.key} references a managed secret that is {status}.",
                details={
                    "ref_scheme": "db",
                    "slug": slug,
                    "status": status,
                },
            )
        return None
    if not _looks_like_secret_ref(value):
        return SettingsBrokenReference(
            key=row.key,
            scope=row.scope,
            workspace_id=row.workspace_id,
            user_id=row.user_id,
            value=value,
            code="invalid_secret_ref",
            message=f"{row.key} is not a valid SecretRef.",
            details={},
        )
    return None


def _provider_profile_broken_reference(
    *,
    row: SettingsOverride,
    value: str,
    profile_enabled_by_id: Mapping[str, bool | None],
) -> SettingsBrokenReference | None:
    profile_id = value.strip()
    if not profile_id:
        return None
    enabled = profile_enabled_by_id.get(profile_id)
    if enabled is None:
        return SettingsBrokenReference(
            key=row.key,
            scope=row.scope,
            workspace_id=row.workspace_id,
            user_id=row.user_id,
            value=value,
            code="provider_profile_not_found",
            message=(
                f"{row.key} references a provider profile that does not exist."
            ),
            details={"profile_id": profile_id},
        )
    if enabled is False:
        return SettingsBrokenReference(
            key=row.key,
            scope=row.scope,
            workspace_id=row.workspace_id,
            user_id=row.user_id,
            value=value,
            code="provider_profile_disabled",
            message=(
                f"{row.key} references a disabled provider profile."
            ),
            details={"profile_id": profile_id},
        )
    return None


__all__ = [
    "SettingsBackupAuditRecord",
    "SettingsBackupBundle",
    "SettingsBackupOverrideRecord",
    "SettingsBackupViolation",
    "SettingsBrokenReference",
    "export_settings_backup",
    "scan_broken_references",
]
