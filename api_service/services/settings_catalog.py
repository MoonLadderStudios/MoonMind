"""Read-side settings catalog and effective-value resolution."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Iterable, Literal
from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.db.models import (
    ManagedSecret,
    SecretStatus,
    SettingsAuditEvent,
    SettingsOverride,
)
from moonmind.config.settings import AppSettings, settings as app_settings

SettingScope = Literal["user", "workspace", "system", "operator"]
SettingSection = Literal["providers-secrets", "user-workspace", "operations"]
_DEFAULT_SUBJECT_ID = UUID("00000000-0000-0000-0000-000000000000")
_PERSISTED_SCOPES: set[SettingScope] = {"user", "workspace"}
_SECRET_PREFIXES = ("ghp_", "github_pat_", "AIza", "AKIA")
_UNSAFE_FIELD_TOKENS = (
    "secret",
    "token",
    "password",
    "api_key",
    "apikey",
    "credential",
    "private_key",
    "refresh",
    "oauth",
    "workflow_payload",
    "artifact",
    "command_history",
    "operational_history",
    "decrypted",
)


class SettingOption(BaseModel):
    value: str
    label: str


class SettingConstraints(BaseModel):
    minimum: int | float | None = None
    maximum: int | float | None = None
    min_length: int | None = None
    max_length: int | None = None
    pattern: str | None = None


class SettingDependency(BaseModel):
    key: str
    required_value: Any = None
    reason: str | None = None


class SettingAuditPolicy(BaseModel):
    store_old_value: bool = True
    store_new_value: bool = True
    redact: bool = False


class SettingDiagnostic(BaseModel):
    code: str
    message: str
    severity: Literal["info", "warning", "error"] = "info"
    details: dict[str, Any] = Field(default_factory=dict)


class SettingDescriptor(BaseModel):
    key: str
    title: str
    description: str | None = None
    category: str
    section: SettingSection
    type: str
    ui: str
    scopes: list[SettingScope]
    default_value: Any = None
    effective_value: Any = None
    override_value: Any = None
    source: str
    source_explanation: str
    options: list[SettingOption] | None = None
    constraints: SettingConstraints | None = None
    sensitive: bool = False
    secret_role: str | None = None
    read_only: bool = False
    read_only_reason: str | None = None
    requires_reload: bool = False
    requires_worker_restart: bool = False
    requires_process_restart: bool = False
    applies_to: list[str] = Field(default_factory=list)
    depends_on: list[SettingDependency] = Field(default_factory=list)
    order: int
    audit: SettingAuditPolicy
    value_version: int = 1
    diagnostics: list[SettingDiagnostic] = Field(default_factory=list)


class SettingsCatalogResponse(BaseModel):
    section: SettingSection | None = None
    scope: SettingScope | None = None
    categories: dict[str, list[SettingDescriptor]]


class EffectiveSettingValue(BaseModel):
    key: str
    scope: SettingScope
    value: Any = None
    source: str
    source_explanation: str
    value_version: int = 1
    diagnostics: list[SettingDiagnostic] = Field(default_factory=list)


class EffectiveSettingsResponse(BaseModel):
    scope: SettingScope
    values: dict[str, EffectiveSettingValue]


class SettingsError(BaseModel):
    error: str
    message: str
    key: str | None = None
    scope: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


@dataclass(frozen=True)
class SettingRegistryEntry:
    key: str
    title: str
    category: str
    section: SettingSection
    value_type: str
    ui: str
    scopes: tuple[SettingScope, ...]
    order: int
    default_value: Any = None
    description: str | None = None
    settings_path: tuple[str, ...] | None = None
    env_aliases: tuple[str, ...] = ()
    options: tuple[tuple[str, str], ...] = ()
    constraints: SettingConstraints | None = None
    sensitive: bool = False
    secret_role: str | None = None
    read_only: bool = False
    read_only_reason: str | None = None
    requires_reload: bool = False
    requires_worker_restart: bool = False
    requires_process_restart: bool = False
    applies_to: tuple[str, ...] = ()
    depends_on: tuple[SettingDependency, ...] = ()
    audit: SettingAuditPolicy = field(default_factory=SettingAuditPolicy)


_REGISTRY: tuple[SettingRegistryEntry, ...] = (
    SettingRegistryEntry(
        key="workflow.default_task_runtime",
        title="Default Task Runtime",
        description="Runtime used when a task does not explicitly request one.",
        category="Workflow",
        section="user-workspace",
        value_type="enum",
        ui="select",
        scopes=("workspace",),
        default_value="codex",
        settings_path=("workflow", "default_task_runtime"),
        env_aliases=("WORKFLOW_DEFAULT_TASK_RUNTIME", "MOONMIND_DEFAULT_TASK_RUNTIME"),
        options=(
            ("codex", "Codex"),
            ("codex_cli", "Codex CLI"),
            ("claude_code", "Claude Code"),
            ("gemini_cli", "Gemini CLI"),
            ("jules", "Jules"),
        ),
        applies_to=("task_creation", "workflow_runtime"),
        order=10,
    ),
    SettingRegistryEntry(
        key="workflow.default_publish_mode",
        title="Default Publish Mode",
        description="Fallback publish mode used when tasks omit publish mode.",
        category="Workflow",
        section="user-workspace",
        value_type="enum",
        ui="select",
        scopes=("workspace",),
        default_value="pr",
        settings_path=("workflow", "default_publish_mode"),
        env_aliases=("WORKFLOW_DEFAULT_PUBLISH_MODE", "MOONMIND_DEFAULT_PUBLISH_MODE"),
        options=(("none", "None"), ("branch", "Branch"), ("pr", "Pull Request")),
        applies_to=("task_creation", "publishing"),
        order=20,
    ),
    SettingRegistryEntry(
        key="skills.policy_mode",
        title="Skill Policy Mode",
        description="Policy used when resolving workflow skills.",
        category="Skills",
        section="user-workspace",
        value_type="enum",
        ui="select",
        scopes=("workspace",),
        default_value="permissive",
        settings_path=("workflow", "skill_policy_mode"),
        env_aliases=(
            "WORKFLOW_SKILL_POLICY_MODE",
            "MOONMIND_SKILL_POLICY_MODE",
            "SKILL_POLICY_MODE",
        ),
        options=(("permissive", "Permissive"), ("allowlist", "Allowlist")),
        applies_to=("workflow_runtime", "skills"),
        order=30,
    ),
    SettingRegistryEntry(
        key="skills.canary_percent",
        title="Skills Canary Percent",
        description="Percentage of runs routed through skills-first policy.",
        category="Skills",
        section="user-workspace",
        value_type="integer",
        ui="number",
        scopes=("workspace",),
        default_value=100,
        settings_path=("workflow", "skills_canary_percent"),
        env_aliases=("WORKFLOW_SKILLS_CANARY_PERCENT",),
        constraints=SettingConstraints(minimum=0, maximum=100),
        applies_to=("workflow_runtime", "skills"),
        order=40,
    ),
    SettingRegistryEntry(
        key="live_sessions.default_enabled",
        title="Live Sessions Enabled By Default",
        description=(
            "Whether live task sessions are enabled by default for queue task runs."
        ),
        category="Live Sessions",
        section="user-workspace",
        value_type="boolean",
        ui="toggle",
        scopes=("workspace",),
        default_value=True,
        settings_path=("workflow", "live_session_enabled_default"),
        env_aliases=("MOONMIND_LIVE_SESSION_ENABLED_DEFAULT",),
        applies_to=("task_creation", "live_sessions"),
        order=50,
    ),
    SettingRegistryEntry(
        key="integrations.github.token_ref",
        title="GitHub Token Reference",
        description="Secret reference used for GitHub API access.",
        category="Integrations",
        section="user-workspace",
        value_type="secret_ref",
        ui="secret_ref_picker",
        scopes=("user", "workspace"),
        default_value=None,
        env_aliases=("MOONMIND_GITHUB_TOKEN_REF",),
        sensitive=False,
        secret_role="github_token",
        applies_to=("github", "integrations"),
        audit=SettingAuditPolicy(
            store_old_value=True,
            store_new_value=True,
            redact=True,
        ),
        order=60,
    ),
)


class SettingsCatalogService:
    """Build catalog and effective settings responses from explicit metadata."""

    def __init__(
        self,
        *,
        settings: AppSettings | None = None,
        env: dict[str, str] | None = None,
        registry: tuple[SettingRegistryEntry, ...] = _REGISTRY,
        session: AsyncSession | None = None,
        workspace_id: UUID | None = None,
        user_id: UUID | None = None,
    ) -> None:
        self._settings = settings or app_settings
        self._env = env if env is not None else os.environ
        self._registry = registry
        self._entries_by_key = {entry.key: entry for entry in registry}
        self._redacted_invalid_secret_refs: set[str] = set()
        self._session = session
        self._workspace_id = workspace_id or _DEFAULT_SUBJECT_ID
        self._user_id = user_id or _DEFAULT_SUBJECT_ID
        self._managed_secret_status_by_slug: dict[str, str | None] = {}

    def catalog(
        self,
        *,
        section: SettingSection | None = None,
        scope: SettingScope | None = None,
    ) -> SettingsCatalogResponse:
        categories: dict[str, list[SettingDescriptor]] = {}
        for entry in sorted(self._registry, key=lambda item: item.order):
            if section is not None and entry.section != section:
                continue
            if scope is not None and scope not in entry.scopes:
                continue
            descriptor = self._descriptor(entry)
            categories.setdefault(entry.category, []).append(descriptor)
        return SettingsCatalogResponse(
            section=section,
            scope=scope,
            categories=categories,
        )

    async def catalog_async(
        self,
        *,
        section: SettingSection | None = None,
        scope: SettingScope | None = None,
    ) -> SettingsCatalogResponse:
        entries = [
            entry
            for entry in sorted(self._registry, key=lambda item: item.order)
            if (section is None or entry.section == section)
            and (scope is None or scope in entry.scopes)
        ]
        overrides = await self._get_effective_overrides(
            scope=scope,
            keys=[entry.key for entry in entries],
        )
        await self._prime_managed_secret_statuses(
            self._resolve_value_from_overrides(
                entry,
                scope=scope,
                overrides=overrides,
            )[0]
            for entry in entries
            if scope is not None
        )
        categories: dict[str, list[SettingDescriptor]] = {}
        for entry in entries:
            descriptor = self._descriptor(
                entry,
                scope=scope,
                overrides=overrides,
            )
            categories.setdefault(entry.category, []).append(descriptor)
        return SettingsCatalogResponse(
            section=section,
            scope=scope,
            categories=categories,
        )

    def effective_values(self, *, scope: SettingScope) -> EffectiveSettingsResponse:
        values = {
            entry.key: self.effective_value(entry.key, scope=scope)
            for entry in self._registry
            if scope in entry.scopes
        }
        return EffectiveSettingsResponse(scope=scope, values=values)

    def effective_value(
        self,
        key: str,
        *,
        scope: SettingScope,
    ) -> EffectiveSettingValue:
        entry = self._entries_by_key.get(key)
        if entry is None:
            raise KeyError(key)
        if scope not in entry.scopes:
            raise ValueError(scope)
        value, source = self._resolve_value(entry)
        return EffectiveSettingValue(
            key=entry.key,
            scope=scope,
            value=value,
            source=source,
            source_explanation=self._source_explanation(entry, source),
            diagnostics=self._diagnostics(entry, value),
        )

    def ensure_write_allowed(self, key: str, *, scope: SettingScope) -> None:
        entry = self._entries_by_key.get(key)
        if entry is None:
            raise KeyError(key)
        if scope not in entry.scopes:
            raise ValueError(scope)
        if entry.read_only:
            raise PermissionError(entry.read_only_reason or "Setting is read-only.")

    def _descriptor(
        self,
        entry: SettingRegistryEntry,
        *,
        scope: SettingScope | None = None,
        overrides: dict[tuple[SettingScope, str], SettingsOverride] | None = None,
    ) -> SettingDescriptor:
        if scope is not None and overrides is not None:
            value, source, version, override_present = self._resolve_value_from_overrides(
                entry,
                scope=scope,
                overrides=overrides,
            )
        else:
            value, source = self._resolve_value(entry)
            version = 1
            override_present = False
        return SettingDescriptor(
            key=entry.key,
            title=entry.title,
            description=entry.description,
            category=entry.category,
            section=entry.section,
            type=entry.value_type,
            ui=entry.ui,
            scopes=list(entry.scopes),
            default_value=entry.default_value,
            effective_value=value,
            override_value=None,
            source=source,
            source_explanation=self._source_explanation(entry, source),
            options=[
                SettingOption(value=value, label=label)
                for value, label in entry.options
            ]
            or None,
            constraints=entry.constraints,
            sensitive=entry.sensitive,
            secret_role=entry.secret_role,
            read_only=entry.read_only,
            read_only_reason=entry.read_only_reason,
            requires_reload=entry.requires_reload,
            requires_worker_restart=entry.requires_worker_restart,
            requires_process_restart=entry.requires_process_restart,
            applies_to=list(entry.applies_to),
            depends_on=list(entry.depends_on),
            order=entry.order,
            audit=entry.audit,
            value_version=version,
            diagnostics=self._diagnostics_for_override(entry, value, override_present),
        )

    async def effective_value_async(
        self,
        key: str,
        *,
        scope: SettingScope,
    ) -> EffectiveSettingValue:
        entry = self._entries_by_key.get(key)
        if entry is None:
            raise KeyError(key)
        if scope not in entry.scopes:
            raise ValueError(scope)
        overrides = await self._get_effective_overrides(scope=scope, keys=[entry.key])
        value, source, version, override_present = self._resolve_value_from_overrides(
            entry,
            scope=scope,
            overrides=overrides,
        )
        await self._prime_managed_secret_statuses([value])
        return EffectiveSettingValue(
            key=entry.key,
            scope=scope,
            value=value,
            source=source,
            source_explanation=self._source_explanation(entry, source),
            value_version=version,
            diagnostics=self._diagnostics_for_override(entry, value, override_present),
        )

    async def effective_values_async(
        self,
        *,
        scope: SettingScope,
    ) -> EffectiveSettingsResponse:
        entries = [entry for entry in self._registry if scope in entry.scopes]
        overrides = await self._get_effective_overrides(
            scope=scope,
            keys=[entry.key for entry in entries],
        )
        resolved = {
            entry.key: (
                entry,
                self._resolve_value_from_overrides(
                    entry,
                    scope=scope,
                    overrides=overrides,
                ),
            )
            for entry in entries
        }
        await self._prime_managed_secret_statuses(
            data[0] for _entry, data in resolved.values()
        )
        values = {
            key: EffectiveSettingValue(
                key=entry.key,
                scope=scope,
                value=data[0],
                source=data[1],
                source_explanation=self._source_explanation(entry, data[1]),
                value_version=data[2],
                diagnostics=self._diagnostics_for_override(entry, data[0], data[3]),
            )
            for key, (entry, data) in resolved.items()
        }
        return EffectiveSettingsResponse(scope=scope, values=values)

    async def apply_overrides(
        self,
        *,
        scope: SettingScope,
        changes: dict[str, Any],
        expected_versions: dict[str, int] | None = None,
        reason: str | None = None,
    ) -> EffectiveSettingsResponse:
        if self._session is None:
            raise RuntimeError("settings override persistence requires a DB session")
        if scope not in _PERSISTED_SCOPES:
            raise ValueError("invalid_scope")
        expected_versions = expected_versions or {}
        entries: dict[str, SettingRegistryEntry] = {}
        validated: dict[str, Any] = {}
        current_rows = await self._get_overrides(
            scope=scope,
            keys=changes.keys(),
            for_update=True,
        )

        for key, value in changes.items():
            entry = self._entries_by_key.get(key)
            if entry is None:
                raise KeyError(key)
            if scope not in entry.scopes:
                raise ValueError("invalid_scope")
            if entry.read_only:
                raise PermissionError(entry.read_only_reason or "Setting is read-only.")
            self._validate_override_value(entry, value)
            row = current_rows.get((scope, key))
            current_version = row.value_version if row is not None else 1
            expected = expected_versions.get(key)
            if expected is not None and expected != current_version:
                raise ValueError("version_conflict")
            entries[key] = entry
            validated[key] = value

        for key, value in validated.items():
            entry = entries[key]
            row = current_rows.get((scope, key))
            old_value = row.value_json if row is not None else None
            if row is None:
                row = SettingsOverride(
                    scope=scope,
                    workspace_id=self._workspace_id,
                    user_id=self._user_id if scope == "user" else _DEFAULT_SUBJECT_ID,
                    key=key,
                    value_json=value,
                    value_version=1,
                )
                self._session.add(row)
                current_rows[(scope, key)] = row
            else:
                row.value_json = value
                row.value_version += 1
            self._session.add(
                self._audit_event(
                    entry,
                    event_type="settings.override.updated",
                    scope=scope,
                    old_value=old_value,
                    new_value=value,
                    reason=reason,
                )
            )

        try:
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            raise ValueError("version_conflict") from exc
        changed_overrides = {
            (row_scope, changed_key): row
            for (row_scope, changed_key), row in current_rows.items()
            if row_scope == scope and changed_key in changes
        }
        resolved = {
            key: self._resolve_value_from_overrides(
                entries[key],
                scope=scope,
                overrides=changed_overrides,
            )
            for key in changes
        }
        await self._prime_managed_secret_statuses(data[0] for data in resolved.values())
        values = {
            key: EffectiveSettingValue(
                key=entries[key].key,
                scope=scope,
                value=data[0],
                source=data[1],
                source_explanation=self._source_explanation(entries[key], data[1]),
                value_version=data[2],
                diagnostics=self._diagnostics_for_override(
                    entries[key], data[0], data[3]
                ),
            )
            for key, data in resolved.items()
        }
        return EffectiveSettingsResponse(scope=scope, values=values)

    async def reset_override(
        self,
        key: str,
        *,
        scope: SettingScope,
        reason: str | None = None,
    ) -> EffectiveSettingValue:
        if self._session is None:
            raise RuntimeError("settings override persistence requires a DB session")
        if scope not in _PERSISTED_SCOPES:
            raise ValueError("invalid_scope")
        entry = self._entries_by_key.get(key)
        if entry is None:
            raise KeyError(key)
        if scope not in entry.scopes:
            raise ValueError("invalid_scope")
        row = await self._get_override(scope=scope, key=key)
        if row is not None:
            old_value = row.value_json
            await self._session.delete(row)
            self._session.add(
                self._audit_event(
                    entry,
                    event_type="settings.override.reset",
                    scope=scope,
                    old_value=old_value,
                    new_value=None,
                    reason=reason,
                )
            )
        await self._session.commit()
        return await self.effective_value_async(key, scope=scope)

    async def audit_event_count(self) -> int:
        if self._session is None:
            return 0
        result = await self._session.execute(select(func.count(SettingsAuditEvent.id)))
        return int(result.scalar_one())

    def _resolve_value(self, entry: SettingRegistryEntry) -> tuple[Any, str]:
        for alias in entry.env_aliases:
            if alias in self._env:
                return self._parse_env_value(entry, self._env[alias]), "environment"
        if entry.settings_path is not None:
            current: Any = self._settings
            try:
                for part in entry.settings_path:
                    current = getattr(current, part)
                return current, "config_or_default"
            except AttributeError:
                return None, "missing"
        return entry.default_value, "default"

    async def _resolve_value_async(
        self,
        entry: SettingRegistryEntry,
        *,
        scope: SettingScope,
    ) -> tuple[Any, str, int, bool]:
        if self._session is not None:
            if scope == "user":
                user_override = await self._get_override(scope="user", key=entry.key)
                if user_override is not None:
                    return (
                        user_override.value_json,
                        "user_override",
                        user_override.value_version,
                        True,
                    )
                workspace_override = await self._get_override(
                    scope="workspace", key=entry.key
                )
                if workspace_override is not None:
                    return (
                        workspace_override.value_json,
                        "workspace_override",
                        workspace_override.value_version,
                        True,
                    )
            elif scope == "workspace":
                workspace_override = await self._get_override(
                    scope="workspace", key=entry.key
                )
                if workspace_override is not None:
                    return (
                        workspace_override.value_json,
                        "workspace_override",
                        workspace_override.value_version,
                        True,
                    )
        value, source = self._resolve_value(entry)
        return value, source, 1, False

    def _effective_value_from_overrides(
        self,
        entry: SettingRegistryEntry,
        *,
        scope: SettingScope,
        overrides: dict[tuple[SettingScope, str], SettingsOverride],
    ) -> EffectiveSettingValue:
        value, source, version, override_present = self._resolve_value_from_overrides(
            entry,
            scope=scope,
            overrides=overrides,
        )
        return EffectiveSettingValue(
            key=entry.key,
            scope=scope,
            value=value,
            source=source,
            source_explanation=self._source_explanation(entry, source),
            value_version=version,
            diagnostics=self._diagnostics_for_override(entry, value, override_present),
        )

    def _diagnostics_for_override(
        self,
        entry: SettingRegistryEntry,
        value: Any,
        override_present: bool,
    ) -> list[SettingDiagnostic]:
        if override_present and (entry.value_type != "secret_ref" or value is None):
            return []
        return self._diagnostics(entry, value)

    async def _prime_managed_secret_statuses(self, values: Iterable[Any]) -> None:
        if self._session is None:
            return
        slugs = {
            value.removeprefix("db://")
            for value in values
            if isinstance(value, str) and value.startswith("db://")
        }
        missing = sorted(
            slug for slug in slugs if slug not in self._managed_secret_status_by_slug
        )
        if not missing:
            return
        result = await self._session.execute(
            select(ManagedSecret.slug, ManagedSecret.status).where(
                ManagedSecret.slug.in_(missing)
            )
        )
        found: set[str] = set()
        for slug, status in result.all():
            found.add(slug)
            self._managed_secret_status_by_slug[slug] = (
                status.value if isinstance(status, SecretStatus) else str(status)
            )
        for slug in missing:
            if slug not in found:
                self._managed_secret_status_by_slug[slug] = None

    def _resolve_value_from_overrides(
        self,
        entry: SettingRegistryEntry,
        *,
        scope: SettingScope,
        overrides: dict[tuple[SettingScope, str], SettingsOverride],
    ) -> tuple[Any, str, int, bool]:
        if scope == "user":
            user_override = overrides.get(("user", entry.key))
            if user_override is not None:
                return (
                    user_override.value_json,
                    "user_override",
                    user_override.value_version,
                    True,
                )
            workspace_override = overrides.get(("workspace", entry.key))
            if workspace_override is not None:
                return (
                    workspace_override.value_json,
                    "workspace_override",
                    workspace_override.value_version,
                    True,
                )
        elif scope == "workspace":
            workspace_override = overrides.get(("workspace", entry.key))
            if workspace_override is not None:
                return (
                    workspace_override.value_json,
                    "workspace_override",
                    workspace_override.value_version,
                    True,
                )
        value, source = self._resolve_value(entry)
        return value, source, 1, False

    async def _get_effective_overrides(
        self,
        *,
        scope: SettingScope | None,
        keys: list[str],
    ) -> dict[tuple[SettingScope, str], SettingsOverride]:
        if self._session is None or not keys:
            return {}
        scopes: tuple[SettingScope, ...]
        if scope == "user":
            scopes = ("user", "workspace")
        elif scope == "workspace":
            scopes = ("workspace",)
        else:
            return {}
        return await self._get_overrides(scopes=scopes, keys=keys)

    async def _get_overrides(
        self,
        *,
        keys: Iterable[str],
        scope: SettingScope | None = None,
        scopes: tuple[SettingScope, ...] | None = None,
        for_update: bool = False,
    ) -> dict[tuple[SettingScope, str], SettingsOverride]:
        if self._session is None:
            return {}
        key_list = list(keys)
        if not key_list:
            return {}
        resolved_scopes = scopes or ((scope,) if scope is not None else ())
        if not resolved_scopes:
            return {}
        statement = select(SettingsOverride).where(
            SettingsOverride.scope.in_(resolved_scopes),
            SettingsOverride.workspace_id == self._workspace_id,
            SettingsOverride.key.in_(key_list),
        )
        if "user" in resolved_scopes:
            statement = statement.where(
                (
                    (SettingsOverride.scope == "user")
                    & (SettingsOverride.user_id == self._user_id)
                )
                | (
                    (SettingsOverride.scope != "user")
                    & (SettingsOverride.user_id == _DEFAULT_SUBJECT_ID)
                )
            )
        else:
            statement = statement.where(
                SettingsOverride.user_id == _DEFAULT_SUBJECT_ID
            )
        if for_update:
            statement = statement.with_for_update()
        result = await self._session.execute(statement)
        return {(row.scope, row.key): row for row in result.scalars().all()}

    async def _get_override(
        self,
        *,
        scope: SettingScope,
        key: str,
    ) -> SettingsOverride | None:
        rows = await self._get_overrides(
            scope=scope,
            keys=[key],
        )
        return rows.get((scope, key))

    def _parse_env_value(self, entry: SettingRegistryEntry, raw_value: str) -> Any:
        if entry.value_type == "integer":
            try:
                return int(raw_value)
            except ValueError:
                return raw_value
        if entry.value_type == "boolean":
            normalized = raw_value.strip().lower()
            if normalized in {"1", "true", "yes", "on"}:
                return True
            if normalized in {"0", "false", "no", "off"}:
                return False
            return raw_value
        if entry.value_type == "secret_ref":
            diagnostic = self._secret_ref_diagnostic(entry, raw_value)
            if diagnostic is not None and diagnostic.code == "invalid_secret_ref":
                self._redacted_invalid_secret_refs.add(entry.key)
                return None
        return raw_value

    def _source_explanation(self, entry: SettingRegistryEntry, source: str) -> str:
        if source == "environment":
            aliases = ", ".join(entry.env_aliases)
            return f"Resolved from deployment environment using one of: {aliases}."
        if source == "config_or_default":
            return (
                "Resolved from application settings after config and default loading."
            )
        if source == "default":
            return "Resolved from the catalog default value."
        if source == "missing":
            return "No configured value or catalog default could be resolved."
        if source == "workspace_override":
            return "Resolved from a workspace override."
        if source == "user_override":
            return "Resolved from a user override."
        return f"Resolved from {source}."

    def _validate_override_value(self, entry: SettingRegistryEntry, value: Any) -> None:
        if self._contains_unsafe_payload(value):
            raise ValueError("invalid_setting_value")
        if value is None:
            return
        if entry.value_type == "enum":
            allowed = {option for option, _label in entry.options}
            if not isinstance(value, str) or value not in allowed:
                raise ValueError("invalid_setting_value")
        elif entry.value_type == "integer":
            if not isinstance(value, int) or isinstance(value, bool):
                raise ValueError("invalid_setting_value")
            if entry.constraints is not None:
                if entry.constraints.minimum is not None and value < entry.constraints.minimum:
                    raise ValueError("invalid_setting_value")
                if entry.constraints.maximum is not None and value > entry.constraints.maximum:
                    raise ValueError("invalid_setting_value")
        elif entry.value_type == "boolean":
            if not isinstance(value, bool):
                raise ValueError("invalid_setting_value")
        elif entry.value_type == "secret_ref":
            if not isinstance(value, str) or "://" not in value:
                raise ValueError("invalid_setting_value")
            if any(value.startswith(prefix) for prefix in _SECRET_PREFIXES):
                raise ValueError("invalid_setting_value")
        elif entry.value_type == "string":
            if not isinstance(value, str):
                raise ValueError("invalid_setting_value")
        else:
            raise ValueError("invalid_setting_value")

    def _contains_unsafe_payload(self, value: Any) -> bool:
        if isinstance(value, str):
            return any(value.startswith(prefix) for prefix in _SECRET_PREFIXES)
        if isinstance(value, dict):
            for key, nested in value.items():
                normalized = str(key).lower()
                if any(token in normalized for token in _UNSAFE_FIELD_TOKENS):
                    return True
                if self._contains_unsafe_payload(nested):
                    return True
        if isinstance(value, list):
            return any(self._contains_unsafe_payload(item) for item in value)
        return False

    def _audit_event(
        self,
        entry: SettingRegistryEntry,
        *,
        event_type: str,
        scope: SettingScope,
        old_value: Any,
        new_value: Any,
        reason: str | None,
    ) -> SettingsAuditEvent:
        redacted = entry.audit.redact
        return SettingsAuditEvent(
            event_type=event_type,
            key=entry.key,
            scope=scope,
            workspace_id=self._workspace_id,
            user_id=self._user_id if scope == "user" else _DEFAULT_SUBJECT_ID,
            old_value_json=None if redacted or not entry.audit.store_old_value else old_value,
            new_value_json=None if redacted or not entry.audit.store_new_value else new_value,
            redacted=redacted,
            reason=reason,
        )

    def _diagnostics(
        self,
        entry: SettingRegistryEntry,
        value: Any,
    ) -> list[SettingDiagnostic]:
        diagnostics: list[SettingDiagnostic] = []
        if value is None:
            diagnostics.append(
                SettingDiagnostic(
                    code="inherited_null",
                    message=(
                        f"{entry.key} resolves to null because no override or "
                        "configured value is present."
                    ),
                    severity="info",
                )
            )
            if entry.key in self._redacted_invalid_secret_refs:
                diagnostics.append(
                    SettingDiagnostic(
                        code="invalid_secret_ref",
                        message=f"{entry.key} is not a valid SecretRef.",
                        severity="error",
                    )
                )
        if entry.value_type == "secret_ref" and isinstance(value, str):
            diagnostic = self._secret_ref_diagnostic(entry, value)
            if diagnostic is not None:
                diagnostics.append(diagnostic)
        return diagnostics

    def _secret_ref_diagnostic(
        self, entry: SettingRegistryEntry, value: str
    ) -> SettingDiagnostic | None:
        if value.startswith("env://"):
            env_name = value.removeprefix("env://")
            if not env_name or env_name not in self._env:
                return SettingDiagnostic(
                    code="unresolved_secret_ref",
                    message=(
                        f"{entry.key} references an environment secret that is "
                        "not available."
                    ),
                    severity="error",
                    details={"ref_scheme": "env"},
                )
        elif value.startswith("db://"):
            slug = value.removeprefix("db://")
            if slug not in self._managed_secret_status_by_slug:
                return None
            status = self._managed_secret_status_by_slug.get(slug)
            if status is None:
                return SettingDiagnostic(
                    code="unresolved_secret_ref",
                    message=(
                        f"{entry.key} references a managed secret that does "
                        "not exist."
                    ),
                    severity="error",
                    details={"ref_scheme": "db", "status": "missing"},
                )
            if status != SecretStatus.ACTIVE.value:
                return SettingDiagnostic(
                    code="unresolved_secret_ref",
                    message=(
                        f"{entry.key} references a managed secret that is "
                        f"{status}."
                    ),
                    severity="error",
                    details={"ref_scheme": "db", "status": status},
                )
        elif "://" not in value:
            return SettingDiagnostic(
                code="invalid_secret_ref",
                message=f"{entry.key} is not a valid SecretRef.",
                severity="error",
            )
        return None


def settings_error(
    error: str,
    message: str,
    *,
    key: str | None = None,
    scope: str | None = None,
    details: dict[str, Any] | None = None,
) -> SettingsError:
    return SettingsError(
        error=error,
        message=message,
        key=key,
        scope=scope,
        details=details or {},
    )
