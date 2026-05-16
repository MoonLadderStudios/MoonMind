"""Read-side settings catalog and effective-value resolution."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Literal
from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy import desc, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.db.models import (
    ManagedAgentProviderProfile,
    ManagedSecret,
    ProviderCredentialSource,
    SecretStatus,
    SettingsAuditEvent,
    SettingsOverride,
)
from moonmind.config.settings import AppSettings, settings as app_settings

SettingScope = Literal["user", "workspace", "system", "operator"]
SettingSection = Literal["providers-secrets", "user-workspace", "operations"]
SettingApplyMode = Literal[
    "immediate",
    "next_request",
    "next_task",
    "next_launch",
    "worker_reload",
    "process_restart",
    "manual_operation",
]
SettingActivationState = Literal[
    "active",
    "pending_next_boundary",
    "pending_reload",
    "pending_restart",
    "pending_manual_operation",
]
SettingMigrationState = Literal["renamed", "deprecated", "removed", "type_changed"]
_DEFAULT_SUBJECT_ID = UUID("00000000-0000-0000-0000-000000000000")
_PERSISTED_SCOPES: set[SettingScope] = {"user", "workspace"}
_MAX_OVERRIDE_VALUE_BYTES = 16 * 1024
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
_UNSAFE_STRING_TOKENS = (
    "oauth_session",
    "decrypted_credential",
    "generated_config",
    "token=",
    "secret=",
    "api_key=",
    "apikey=",
    "password=",
    "private_key",
    "large_artifact",
    "workflow_payload",
    "command_history",
)
_UNSAFE_PROFILE_REF_ASSIGNMENT_RE = re.compile(
    r"(secret|token|password|api_key|apikey|credential|private_key|refresh|oauth|"
    r"workflow_payload|artifact|command_history|operational_history|decrypted|"
    r"generated_config|large_artifact)\w*\s*[:=]"
)

SETTINGS_PERMISSION_NAMES: frozenset[str] = frozenset(
    {
        "settings.catalog.read",
        "settings.effective.read",
        "settings.user.write",
        "settings.workspace.write",
        "settings.system.read",
        "settings.system.write",
        "secrets.metadata.read",
        "secrets.value.write",
        "secrets.rotate",
        "secrets.disable",
        "secrets.delete",
        "provider_profiles.read",
        "provider_profiles.write",
        "operations.read",
        "operations.invoke",
        "settings.audit.read",
    }
)

_SECRET_LIKE_SUBSTRINGS = (
    "token",
    "secret",
    "password",
    "private key",
    "private_key",
    "oauth",
    "credential",
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
    min_items: int | None = None
    max_items: int | None = None
    required_keys: list[str] | None = None
    allowed_keys: list[str] | None = None


SettingValidationBoundary = Literal[
    "descriptor_generation",
    "write_request",
    "pre_persistence",
    "effective_preview",
    "launch_execution",
    "operation_execution",
    "readiness_diagnostics",
]

_VALIDATION_BLOCKS_BY_BOUNDARY: dict[SettingValidationBoundary, list[str]] = {
    "descriptor_generation": ["catalog"],
    "write_request": ["persistence", "preview"],
    "pre_persistence": ["persistence", "preview"],
    "effective_preview": ["preview"],
    "launch_execution": ["launch", "readiness"],
    "operation_execution": ["operation"],
    "readiness_diagnostics": ["readiness"],
}


class SettingValidationIssue(BaseModel):
    key: str
    scope: SettingScope
    code: str
    message: str
    boundary: SettingValidationBoundary
    rule: str
    blocks: list[str] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)


class SettingsValidationError(ValueError):
    """Raised when server-owned settings validation rejects a value."""

    def __init__(self, issues: list[SettingValidationIssue]) -> None:
        if not issues:
            raise ValueError("SettingsValidationError requires at least one issue")
        self.issues = issues
        super().__init__("invalid_setting_value")

    @property
    def first_issue(self) -> SettingValidationIssue:
        return self.issues[0]

    def to_settings_error(self) -> "SettingsError":
        issue = self.first_issue
        error_code_by_issue_code = {
            "operator_locked": "operator_locked",
            "provider_profile_not_found": "provider_profile_not_found",
            "requires_confirmation": "requires_confirmation",
            "secret_ref_unresolved": "secret_ref_not_resolvable",
            "unsupported_scope": "scope_not_allowed",
        }
        error_code = error_code_by_issue_code.get(
            issue.code,
            "invalid_setting_value",
        )
        details = issue.model_dump(mode="json")
        details.pop("key", None)
        details.pop("scope", None)
        details.pop("message", None)
        if len(self.issues) > 1:
            details["issues"] = [
                item.model_dump(mode="json") for item in self.issues
            ]
        return settings_error(
            error_code,
            issue.message,
            key=issue.key,
            scope=issue.scope,
            details=details,
        )


class SettingsWorkspacePolicy(BaseModel):
    allowed_runtimes: tuple[str, ...] | None = None
    allowed_provider_profile_ids: tuple[str, ...] | None = None
    skills_canary_enabled: bool = True
    max_canary_percent: int = 100
    allowed_publication_modes: tuple[str, ...] | None = None
    allowed_secret_ref_backends: tuple[str, ...] = ("env", "db")
    maintenance_mode: bool = False
    allowed_operation_modes_during_maintenance: tuple[str, ...] = ()


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


@dataclass(frozen=True)
class SettingMigrationRule:
    old_key: str
    state: SettingMigrationState
    message: str
    new_key: str | None = None
    expected_schema_version: int = 1

    def __post_init__(self) -> None:
        if not self.old_key:
            raise ValueError("migration rule old_key is required")
        if self.state == "renamed" and not self.new_key:
            raise ValueError("renamed setting migration requires new_key")
        if self.expected_schema_version < 1:
            raise ValueError("expected_schema_version must be positive")


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
    apply_mode: SettingApplyMode
    activation_state: SettingActivationState
    active: bool
    pending_value: Any = None
    affected_process_or_worker: str | None = None
    completion_guidance: str | None = None
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
    default_value: Any = None
    source: str
    source_explanation: str
    inheritance_state: str = "inherited"
    apply_mode: SettingApplyMode
    activation_state: SettingActivationState
    active: bool
    pending_value: Any = None
    affected_process_or_worker: str | None = None
    completion_guidance: str | None = None
    read_only: bool = False
    read_only_reason: str | None = None
    requires_reload: bool = False
    requires_worker_restart: bool = False
    requires_process_restart: bool = False
    applies_to: list[str] = Field(default_factory=list)
    value_version: int = 1
    diagnostics: list[SettingDiagnostic] = Field(default_factory=list)


class EffectiveSettingsResponse(BaseModel):
    scope: SettingScope
    values: dict[str, EffectiveSettingValue]
    change_events: list["SettingsChangeEvent"] = Field(default_factory=list)


class SettingsValidationResponse(BaseModel):
    scope: SettingScope
    accepted: bool
    issues: list[SettingValidationIssue] = Field(default_factory=list)
    issues_by_key: dict[str, list[SettingValidationIssue]] = Field(default_factory=dict)


class SettingsPreviewValue(BaseModel):
    value: Any = None
    source: str
    value_version: int = 1
    activation_state: SettingActivationState


class SettingsPreviewDiff(BaseModel):
    key: str
    scope: SettingScope
    before: SettingsPreviewValue
    after: SettingsPreviewValue
    redacted: bool = False


class SettingsDependencyWarning(BaseModel):
    key: str
    dependency_key: str | None = None
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class SettingsReloadRequirement(BaseModel):
    key: str
    apply_mode: SettingApplyMode
    requires_reload: bool = False
    requires_worker_restart: bool = False
    requires_process_restart: bool = False
    applies_to: list[str] = Field(default_factory=list)
    completion_guidance: str | None = None


class SettingsPreviewResponse(SettingsValidationResponse):
    diffs: list[SettingsPreviewDiff] = Field(default_factory=list)
    dependency_warnings: list[SettingsDependencyWarning] = Field(default_factory=list)
    reload_requirements: list[SettingsReloadRequirement] = Field(default_factory=list)


class SettingsError(BaseModel):
    error: str
    message: str
    key: str | None = None
    scope: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class SettingsAuditRead(BaseModel):
    id: UUID
    event_type: str
    key: str
    scope: str
    source: str | None = None
    actor_user_id: UUID | None = None
    old_value: Any = None
    new_value: Any = None
    redacted: bool = False
    redaction_reasons: list[str] = Field(default_factory=list)
    reason: str | None = None
    request_id: str | None = None
    validation_outcome: str | None = None
    apply_mode: str | None = None
    affected_systems: list[str] = Field(default_factory=list)
    created_at: datetime | None = None


class SettingsAuditResponse(BaseModel):
    items: list[SettingsAuditRead]


class SettingsChangeEvent(BaseModel):
    event_type: Literal["setting_changed"] = "setting_changed"
    key: str
    scope: SettingScope
    source: str
    apply_mode: SettingApplyMode
    actor_user_id: UUID | None = None
    changed_at: datetime
    affected_systems: list[str] = Field(default_factory=list)
    refresh_targets: list[str] = Field(default_factory=list)


class SettingsRecentChange(BaseModel):
    event_type: str
    reason: str | None = None
    redacted: bool = False
    created_at: datetime | None = None


class SettingsDiagnosticRead(BaseModel):
    key: str
    scope: SettingScope
    value: Any = None
    default_value: Any = None
    source: str
    source_explanation: str
    inheritance_state: str = "inherited"
    apply_mode: SettingApplyMode
    activation_state: SettingActivationState
    active: bool
    pending_value: Any = None
    affected_process_or_worker: str | None = None
    completion_guidance: str | None = None
    read_only: bool = False
    read_only_reason: str | None = None
    requires_reload: bool = False
    requires_worker_restart: bool = False
    requires_process_restart: bool = False
    applies_to: list[str] = Field(default_factory=list)
    value_version: int = 1
    diagnostics: list[SettingDiagnostic] = Field(default_factory=list)
    recent_change: SettingsRecentChange | None = None


class SettingsDiagnosticsResponse(BaseModel):
    scope: SettingScope
    values: dict[str, SettingsDiagnosticRead]


def settings_permissions_for_user(user: Any) -> set[str]:
    if bool(getattr(user, "is_superuser", False)):
        return set(SETTINGS_PERMISSION_NAMES)
    raw_permissions = getattr(user, "settings_permissions", set()) or set()
    return {
        str(permission)
        for permission in raw_permissions
        if str(permission) in SETTINGS_PERMISSION_NAMES
    }


def has_settings_permission(user: Any, permission: str) -> bool:
    return permission in settings_permissions_for_user(user)


_SETTING_KEY_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$")

_CATALOG_KEY_LEDGER: frozenset[str] = frozenset(
    {
        "workflow.default_task_runtime",
        "workflow.default_publish_mode",
        "workflow.default_provider_profile_ref",
        "workflow.operation_mode",
        "skills.policy_mode",
        "skills.canary_percent",
        "live_sessions.default_enabled",
        "integrations.github.token_ref",
    }
)


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
    operator_locked_value: Any = None
    operator_lock_reason: str | None = None
    policy_blocked_reason: str | None = None
    apply_mode: SettingApplyMode = "immediate"
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
        apply_mode="next_task",
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
        apply_mode="next_task",
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
        apply_mode="worker_reload",
        requires_reload=True,
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
        apply_mode="next_task",
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
        apply_mode="next_task",
        applies_to=("task_creation", "live_sessions"),
        order=50,
    ),
    SettingRegistryEntry(
        key="integrations.github.token_ref",
        title="GitHub Token Reference",
        description="Secret reference used for GitHub API access.",
        category="Integrations",
        section="providers-secrets",
        value_type="secret_ref",
        ui="secret_ref_picker",
        scopes=("user", "workspace"),
        default_value=None,
        env_aliases=("MOONMIND_GITHUB_TOKEN_REF",),
        apply_mode="next_launch",
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
    SettingRegistryEntry(
        key="workflow.default_provider_profile_ref",
        title="Default Provider Profile",
        description=(
            "Provider profile reference used when a task or runtime request does "
            "not select a profile explicitly."
        ),
        category="Workflow",
        section="user-workspace",
        value_type="string",
        ui="provider_profile_picker",
        scopes=("user", "workspace"),
        default_value=None,
        env_aliases=("MOONMIND_DEFAULT_PROVIDER_PROFILE_REF",),
        apply_mode="next_launch",
        applies_to=("task_creation", "workflow_runtime", "provider_profiles"),
        order=70,
    ),
    SettingRegistryEntry(
        key="workflow.operation_mode",
        title="Operation Mode",
        description="Operational mode used when workspace operations are invoked.",
        category="Workflow",
        section="operations",
        value_type="enum",
        ui="select",
        scopes=("workspace",),
        default_value="normal",
        env_aliases=("MOONMIND_OPERATION_MODE",),
        apply_mode="manual_operation",
        options=(
            ("normal", "Normal"),
            ("maintenance", "Maintenance"),
            ("read_only", "Read Only"),
        ),
        applies_to=("operations",),
        order=80,
    ),
)


class SettingsRegistry:
    """Backend-owned registry of exposed settings descriptors with migration gate.

    Owns descriptor registration, key format validation, uniqueness enforcement,
    eligibility filtering, and migration-gate checking against the committed
    stable-key ledger.  Corresponds to the SettingsRegistry component in
    docs/Security/SettingsSystem.md §26.
    """

    def __init__(
        self,
        entries: tuple[SettingRegistryEntry, ...],
        migration_rules: tuple[SettingMigrationRule, ...] = (),
        stable_key_ledger: frozenset[str] | None = _CATALOG_KEY_LEDGER,
    ) -> None:
        self._entries = tuple(sorted(entries, key=lambda e: e.order))
        self._migration_rules = migration_rules
        self._stable_key_ledger = stable_key_ledger
        self._entries_by_key: dict[str, SettingRegistryEntry] = {
            e.key: e for e in self._entries
        }
        self._validate()

    def _validate(self) -> None:
        seen: set[str] = set()
        for entry in self._entries:
            if not _SETTING_KEY_RE.fullmatch(entry.key):
                raise ValueError(f"invalid_key_format: {entry.key!r}")
            if entry.key in seen:
                raise ValueError(f"duplicate_key: {entry.key!r}")
            seen.add(entry.key)
        if self._stable_key_ledger is not None:
            migrated = {r.old_key for r in self._migration_rules}
            removed_without_migration = self._stable_key_ledger - seen - migrated
            if removed_without_migration:
                raise ValueError(
                    "catalog_integrity_error: descriptors removed without migration "
                    f"entries: {sorted(removed_without_migration)}"
                )

    @property
    def entries(self) -> tuple[SettingRegistryEntry, ...]:
        return self._entries

    @property
    def entries_by_key(self) -> dict[str, SettingRegistryEntry]:
        return self._entries_by_key

    @property
    def migration_rules(self) -> tuple[SettingMigrationRule, ...]:
        return self._migration_rules

    def get(self, key: str) -> SettingRegistryEntry | None:
        return self._entries_by_key.get(key)

    @classmethod
    def from_pydantic_model(
        cls,
        model_class: type,
        migration_rules: tuple[SettingMigrationRule, ...] = (),
        stable_key_ledger: frozenset[str] | None = None,
    ) -> "SettingsRegistry":
        """Derive registry entries from Pydantic model fields with moonmind.expose metadata.

        Only top-level fields with json_schema_extra={"moonmind": {"expose": True, ...}}
        are extracted.  Fields without explicit moonmind.expose metadata are skipped.
        """
        entries: list[SettingRegistryEntry] = []
        model_fields = getattr(model_class, "model_fields", {})
        for _field_name, field_info in model_fields.items():
            extra = getattr(field_info, "json_schema_extra", None) or {}
            mm = extra.get("moonmind", {}) if isinstance(extra, dict) else {}
            if not mm.get("expose"):
                continue
            key = mm.get("key", "")
            if not key:
                continue
            section: SettingSection = mm.get("section", "user-workspace")
            category: str = mm.get("category", "General")
            scopes: tuple[SettingScope, ...] = tuple(mm.get("scopes", ["workspace"]))
            ui: str = mm.get("ui", "input")
            requires_reload: bool = bool(mm.get("requires_reload", False))
            apply_mode: SettingApplyMode = (
                "worker_reload" if requires_reload else mm.get("apply_mode", "next_task")
            )
            title: str = mm.get("title") or _field_name.replace("_", " ").title()
            description: str | None = mm.get("description") or getattr(
                field_info, "description", None
            )
            default_any = (
                None
                if field_info.is_required()
                or getattr(field_info, "default_factory", None) is not None
                else field_info.default
            )
            options_raw: list[tuple[str, str]] = mm.get("options", [])
            entries.append(
                SettingRegistryEntry(
                    key=key,
                    title=title,
                    description=description,
                    category=category,
                    section=section,
                    value_type=mm.get("type", "string"),
                    ui=ui,
                    scopes=scopes,
                    default_value=default_any,
                    apply_mode=apply_mode,
                    requires_reload=requires_reload,
                    options=tuple(options_raw),
                    applies_to=tuple(mm.get("applies_to", [])),
                    order=mm.get("order", 999),
                )
            )
        return cls(tuple(entries), migration_rules, stable_key_ledger)


class SettingsCatalogBuilder:
    """Builds SettingsCatalogResponse from a SettingsRegistry.

    Corresponds to the SettingsCatalogBuilder component in
    docs/Security/SettingsSystem.md §26.
    """

    def __init__(self, registry: SettingsRegistry) -> None:
        self._registry = registry

    def build(
        self,
        section: SettingSection | None = None,
        scope: SettingScope | None = None,
        descriptor_fn: Callable[[SettingRegistryEntry], SettingDescriptor] | None = None,
    ) -> SettingsCatalogResponse:
        """Build a catalog response, filtered by section and/or scope."""
        if descriptor_fn is None:
            raise ValueError("descriptor_fn is required to build catalog descriptors")
        categories: dict[str, list[SettingDescriptor]] = {}
        for entry in self._registry.entries:
            if section is not None and entry.section != section:
                continue
            if scope is not None and scope not in entry.scopes:
                continue
            descriptor = descriptor_fn(entry)
            categories.setdefault(entry.category, []).append(descriptor)
        return SettingsCatalogResponse(section=section, scope=scope, categories=categories)


class SettingsCatalogService:
    """Build catalog and effective settings responses from explicit metadata."""

    def __init__(
        self,
        *,
        settings: AppSettings | None = None,
        env: dict[str, str] | None = None,
        registry: tuple[SettingRegistryEntry, ...] = _REGISTRY,
        migration_rules: tuple[SettingMigrationRule, ...] = (),
        session: AsyncSession | None = None,
        workspace_id: UUID | None = None,
        user_id: UUID | None = None,
        workspace_policy: SettingsWorkspacePolicy | dict[str, Any] | None = None,
    ) -> None:
        self._settings = settings or app_settings
        self._env = env if env is not None else os.environ
        self._migration_rules = migration_rules
        ledger = _CATALOG_KEY_LEDGER if registry is _REGISTRY else None
        self._settings_registry = SettingsRegistry(
            registry,
            migration_rules,
            stable_key_ledger=ledger,
        )
        self._catalog_builder = SettingsCatalogBuilder(self._settings_registry)
        self._registry = self._settings_registry.entries
        self._entries_by_key = self._settings_registry.entries_by_key
        self._rules_by_old_key = {rule.old_key: rule for rule in migration_rules}
        self._rename_rules_by_new_key = {
            str(rule.new_key): rule
            for rule in migration_rules
            if rule.state == "renamed" and rule.new_key is not None
        }
        self._type_rules_by_key = {
            rule.old_key: rule for rule in migration_rules if rule.state == "type_changed"
        }
        self._migration_diagnostics_by_key: dict[str, SettingDiagnostic] = {}
        self._redacted_invalid_secret_refs: set[str] = set()
        self._session = session
        self._workspace_id = workspace_id or _DEFAULT_SUBJECT_ID
        self._user_id = user_id or _DEFAULT_SUBJECT_ID
        self._managed_secret_status_by_slug: dict[str, str | None] = {}
        self._provider_profile_enabled_by_id: dict[str, bool | None] = {}
        self._provider_profile_metadata_by_id: dict[str, dict[str, Any]] = {}
        self._workspace_policy = (
            workspace_policy
            if isinstance(workspace_policy, SettingsWorkspacePolicy)
            else SettingsWorkspacePolicy(**(workspace_policy or {}))
        )

    def catalog(
        self,
        *,
        section: SettingSection | None = None,
        scope: SettingScope | None = None,
    ) -> SettingsCatalogResponse:
        self._validate_apply_modes()
        return self._catalog_builder.build(
            section,
            scope,
            descriptor_fn=self._descriptor,
        )

    def _entries_for_scope(self, scope: SettingScope) -> list[SettingRegistryEntry]:
        return [
            entry
            for entry in self._registry
            if scope in entry.scopes
        ]

    async def catalog_async(
        self,
        *,
        section: SettingSection | None = None,
        scope: SettingScope | None = None,
    ) -> SettingsCatalogResponse:
        self._validate_apply_modes()
        entries = [
            entry
            for entry in self._registry
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
        await self._prime_provider_profile_statuses(
            self._resolve_value_from_overrides(
                entry,
                scope=scope,
                overrides=overrides,
            )[0]
            for entry in entries
            if scope is not None
            and entry.key == "workflow.default_provider_profile_ref"
        )
        return self._catalog_builder.build(
            section,
            scope,
            descriptor_fn=lambda entry: self._descriptor(
                entry,
                scope=scope,
                overrides=overrides,
            ),
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
        proposed_values = self._current_effective_values(scope=scope)
        self._prime_referenced_resources_sync(proposed_values)
        validation_by_key: dict[str, list[SettingValidationIssue]] = {}
        for issue in self._validate_values(
            proposed_values,
            scope=scope,
            boundary="effective_preview",
        ):
            validation_by_key.setdefault(issue.key, []).append(issue)
        diagnostics = self._diagnostics_for_override(
            entry,
            value,
            False,
            scope=scope,
            boundary="effective_preview",
        )
        existing_codes = {diagnostic.code for diagnostic in diagnostics}
        diagnostics.extend(
            self._diagnostic_from_validation_issue(issue)
            for issue in validation_by_key.get(entry.key, [])
            if not self._diagnostic_code_present(issue.code, existing_codes)
        )
        activation = self._activation_metadata(entry, value)
        return EffectiveSettingValue(
            key=entry.key,
            scope=scope,
            value=value,
            **self._effective_metadata(entry, value, source, False, diagnostics),
            **activation,
            diagnostics=diagnostics,
        )

    def ensure_write_allowed(self, key: str, *, scope: SettingScope) -> None:
        migration_rule = self._rules_by_old_key.get(key)
        if migration_rule is not None and migration_rule.state in {
            "renamed",
            "deprecated",
            "removed",
        }:
            raise PermissionError(migration_rule.message)
        entry = self._entries_by_key.get(key)
        if entry is None:
            raise KeyError(key)
        if scope not in entry.scopes:
            raise ValueError(scope)
        if self._read_only(entry):
            raise PermissionError(self._read_only_reason(entry) or "Setting is read-only.")

    def write_lock_error_code(self, key: str) -> str:
        entry = self._entries_by_key.get(key)
        if entry is not None and self._is_operator_locked(entry):
            return "operator_locked"
        return "read_only_setting"

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
        diagnostics = self._diagnostics_for_override(
            entry,
            value,
            override_present,
            scope=scope or "workspace",
            boundary="descriptor_generation",
        )
        metadata = self._effective_metadata(
            entry,
            value,
            source,
            override_present,
            diagnostics,
        )
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
            source=metadata["source"],
            source_explanation=metadata["source_explanation"],
            **self._activation_metadata(
                entry,
                value,
                pending_activation=override_present,
            ),
            options=[
                SettingOption(value=value, label=label)
                for value, label in entry.options
            ]
            or None,
            constraints=entry.constraints,
            sensitive=entry.sensitive,
            secret_role=entry.secret_role,
            read_only=metadata["read_only"],
            read_only_reason=metadata["read_only_reason"],
            requires_reload=entry.requires_reload,
            requires_worker_restart=entry.requires_worker_restart,
            requires_process_restart=entry.requires_process_restart,
            applies_to=list(entry.applies_to),
            depends_on=list(entry.depends_on),
            order=entry.order,
            audit=entry.audit,
            value_version=version,
            diagnostics=diagnostics,
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
        entries = self._entries_for_scope(scope)
        overrides = await self._get_effective_overrides(
            scope=scope,
            keys=[item.key for item in entries],
        )
        resolved_values = {
            item.key: self._resolve_value_from_overrides(
                item,
                scope=scope,
                overrides=overrides,
            )
            for item in entries
        }
        value, source, version, override_present = resolved_values[entry.key]
        proposed_values = {
            item_key: data[0] for item_key, data in resolved_values.items()
        }
        await self._prime_referenced_resources(proposed_values)
        validation_by_key: dict[str, list[SettingValidationIssue]] = {}
        for issue in self._validate_values(
            proposed_values,
            scope=scope,
            boundary="effective_preview",
        ):
            validation_by_key.setdefault(issue.key, []).append(issue)
        diagnostics = self._diagnostics_for_override(
            entry,
            value,
            override_present,
            scope=scope,
            boundary="effective_preview",
        )
        existing_codes = {diagnostic.code for diagnostic in diagnostics}
        diagnostics.extend(
            self._diagnostic_from_validation_issue(issue)
            for issue in validation_by_key.get(entry.key, [])
            if not self._diagnostic_code_present(issue.code, existing_codes)
        )
        return EffectiveSettingValue(
            key=entry.key,
            scope=scope,
            value=value,
            **self._effective_metadata(entry, value, source, override_present, diagnostics),
            **self._activation_metadata(
                entry,
                value,
                pending_activation=override_present,
            ),
            value_version=version,
            diagnostics=diagnostics,
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
        await self._prime_provider_profile_statuses(
            data[0]
            for entry, data in resolved.values()
            if entry.key == "workflow.default_provider_profile_ref"
        )
        proposed_values = {entry.key: data[0] for entry, data in resolved.values()}
        validation_by_key: dict[str, list[SettingValidationIssue]] = {}
        for issue in self._validate_values(
            proposed_values,
            scope=scope,
            boundary="effective_preview",
        ):
            validation_by_key.setdefault(issue.key, []).append(issue)
        values = {}
        for key, (entry, data) in resolved.items():
            diagnostics = self._diagnostics_for_override(
                entry,
                data[0],
                data[3],
                scope=scope,
                boundary="effective_preview",
            )
            existing_codes = {diagnostic.code for diagnostic in diagnostics}
            diagnostics.extend(
                self._diagnostic_from_validation_issue(issue)
                for issue in validation_by_key.get(entry.key, [])
                if not self._diagnostic_code_present(issue.code, existing_codes)
            )
            values[key] = EffectiveSettingValue(
                key=entry.key,
                scope=scope,
                value=data[0],
                **self._effective_metadata(entry, data[0], data[1], data[3], diagnostics),
                **self._activation_metadata(
                    entry,
                    data[0],
                    pending_activation=data[3],
                ),
                value_version=data[2],
                diagnostics=diagnostics,
            )
        return EffectiveSettingsResponse(scope=scope, values=values)

    async def validate_changes(
        self,
        *,
        scope: SettingScope,
        changes: dict[str, Any],
        expected_versions: dict[str, int] | None = None,
        confirmation: str | None = None,
    ) -> SettingsValidationResponse:
        context = await self._validate_preview_context(
            scope=scope,
            changes=changes,
            expected_versions=expected_versions,
            confirmation=confirmation,
        )
        return self._validation_response(scope=scope, issues=context["issues"])

    async def preview_changes(
        self,
        *,
        scope: SettingScope,
        changes: dict[str, Any],
        expected_versions: dict[str, int] | None = None,
        confirmation: str | None = None,
    ) -> SettingsPreviewResponse:
        context = await self._validate_preview_context(
            scope=scope,
            changes=changes,
            expected_versions=expected_versions,
            confirmation=confirmation,
        )
        issues: list[SettingValidationIssue] = context["issues"]
        entries: dict[str, SettingRegistryEntry] = context["entries"]
        current_resolved: dict[str, tuple[Any, str, int, bool]] = context[
            "current_resolved"
        ]
        proposed_values: dict[str, Any] = context["proposed_values"]

        diffs: list[SettingsPreviewDiff] = []
        reload_requirements: list[SettingsReloadRequirement] = []
        for key in changes:
            entry = entries.get(key)
            if entry is None:
                continue
            before_value, before_source, before_version, _override_present = (
                current_resolved[key]
            )
            after_value = proposed_values.get(key)
            redacted = self._should_redact_preview_value(entry, before_value) or (
                self._should_redact_preview_value(entry, after_value)
            )
            before_activation = self._activation_metadata(entry, before_value)
            after_activation = self._activation_metadata(
                entry,
                after_value,
                pending_activation=True,
            )
            diffs.append(
                SettingsPreviewDiff(
                    key=key,
                    scope=scope,
                    before=SettingsPreviewValue(
                        value=None if redacted else before_value,
                        source=before_source,
                        value_version=before_version,
                        activation_state=before_activation["activation_state"],
                    ),
                    after=SettingsPreviewValue(
                        value=None if redacted else after_value,
                        source=f"{scope}_preview",
                        value_version=before_version,
                        activation_state=after_activation["activation_state"],
                    ),
                    redacted=redacted,
                )
            )
            if (
                entry.apply_mode != "immediate"
                or entry.requires_reload
                or entry.requires_worker_restart
                or entry.requires_process_restart
            ):
                reload_requirements.append(
                    SettingsReloadRequirement(
                        key=key,
                        apply_mode=entry.apply_mode,
                        requires_reload=entry.requires_reload,
                        requires_worker_restart=entry.requires_worker_restart,
                        requires_process_restart=entry.requires_process_restart,
                        applies_to=list(entry.applies_to),
                        completion_guidance=after_activation["completion_guidance"],
                    )
                )

        dependency_warnings = [
            SettingsDependencyWarning(
                key=issue.key,
                dependency_key=issue.details.get("dependency_key"),
                message=issue.message,
                details=issue.details,
            )
            for issue in issues
            if issue.code == "dependency_not_satisfied"
        ]
        validation = self._validation_response(scope=scope, issues=issues)
        return SettingsPreviewResponse(
            scope=scope,
            accepted=validation.accepted,
            issues=validation.issues,
            issues_by_key=validation.issues_by_key,
            diffs=diffs,
            dependency_warnings=dependency_warnings,
            reload_requirements=reload_requirements,
        )

    async def _validate_preview_context(
        self,
        *,
        scope: SettingScope,
        changes: dict[str, Any],
        expected_versions: dict[str, int] | None,
        confirmation: str | None,
    ) -> dict[str, Any]:
        expected_versions = expected_versions or {}
        entries: dict[str, SettingRegistryEntry] = {}
        validation_issues: list[SettingValidationIssue] = []

        if scope not in _PERSISTED_SCOPES:
            validation_issues.append(
                self._validation_issue_for_key(
                    next(iter(changes), "*"),
                    scope,
                    code="unsupported_scope",
                    message=f"Setting writes are not available at scope {scope}.",
                    boundary="write_request",
                    rule="scope",
                )
            )
            return {
                "entries": entries,
                "current_resolved": {},
                "proposed_values": {},
                "issues": validation_issues,
            }

        for key, value in changes.items():
            migration_rule = self._rules_by_old_key.get(key)
            if migration_rule is not None and migration_rule.state in {
                "renamed",
                "deprecated",
                "removed",
            }:
                validation_issues.append(
                    self._validation_issue_for_key(
                        key,
                        scope,
                        code="setting_not_exposed",
                        message=migration_rule.message,
                        boundary="write_request",
                        rule="migration_state",
                    )
                )
                continue
            entry = self._entries_by_key.get(key)
            if entry is None:
                validation_issues.append(
                    self._validation_issue_for_key(
                        key,
                        scope,
                        code="setting_not_exposed",
                        message=f"Setting {key} is not exposed through the Settings API.",
                        boundary="write_request",
                        rule="exposed_setting",
                    )
                )
                continue
            if scope not in entry.scopes:
                validation_issues.append(
                    self._validation_issue(
                        entry,
                        scope,
                        code="unsupported_scope",
                        message=f"Setting {key} is not available at scope {scope}.",
                        boundary="write_request",
                        rule="scope",
                    )
                )
                continue
            if self._read_only(entry):
                code = (
                    "operator_locked"
                    if self._is_operator_locked(entry)
                    else "read_only_setting"
                )
                validation_issues.append(
                    self._validation_issue(
                        entry,
                        scope,
                        code=code,
                        message=self._read_only_reason(entry) or "Setting is read-only.",
                        boundary="write_request",
                        rule="write_lock",
                    )
                )
                continue
            entries[key] = entry

        effective_entries = self._entries_for_scope(scope)
        overrides = await self._get_effective_overrides(
            scope=scope,
            keys=[entry.key for entry in effective_entries],
        )
        current_resolved = {
            entry.key: self._resolve_value_from_overrides(
                entry,
                scope=scope,
                overrides=overrides,
            )
            for entry in effective_entries
        }

        for key in entries:
            row = overrides.get((scope, key))
            current_version = row.value_version if row is not None else 1
            expected = expected_versions.get(key)
            if expected is not None and expected != current_version:
                validation_issues.append(
                    self._validation_issue(
                        entries[key],
                        scope,
                        code="version_conflict",
                        message="Expected setting version does not match current version.",
                        boundary="write_request",
                        rule="expected_version",
                        details={
                            "expected_version": expected,
                            "current_version": current_version,
                        },
                    )
                )

        proposed_values = {
            key: data[0] for key, data in current_resolved.items()
        }
        proposed_values.update({key: changes[key] for key in entries})
        await self._prime_referenced_resources(proposed_values)
        validation_issues.extend(
            self._validate_values(
                proposed_values,
                scope=scope,
                boundary="write_request",
            )
        )
        validation_issues.extend(
            self._confirmation_issues_for_changes(
                entries,
                changes,
                scope=scope,
                confirmation=confirmation,
            )
        )
        return {
            "entries": entries,
            "current_resolved": current_resolved,
            "proposed_values": proposed_values,
            "issues": validation_issues,
        }

    def _validation_response(
        self,
        *,
        scope: SettingScope,
        issues: list[SettingValidationIssue],
    ) -> SettingsValidationResponse:
        issues_by_key: dict[str, list[SettingValidationIssue]] = {}
        for issue in issues:
            issues_by_key.setdefault(issue.key, []).append(issue)
        return SettingsValidationResponse(
            scope=scope,
            accepted=not issues,
            issues=issues,
            issues_by_key=issues_by_key,
        )

    def _should_redact_preview_value(
        self,
        entry: SettingRegistryEntry,
        value: Any,
    ) -> bool:
        return bool(
            entry.sensitive
            or entry.audit.redact
            or self._contains_secret_like_value(value)
        )

    async def apply_overrides(
        self,
        *,
        scope: SettingScope,
        changes: dict[str, Any],
        expected_versions: dict[str, int] | None = None,
        reason: str | None = None,
        request_id: str | None = None,
        confirmation: str | None = None,
    ) -> EffectiveSettingsResponse:
        if self._session is None:
            raise RuntimeError("settings override persistence requires a DB session")
        if scope not in _PERSISTED_SCOPES:
            raise SettingsValidationError(
                [
                    self._validation_issue_for_key(
                        next(iter(changes), "*"),
                        scope if scope in {"user", "workspace"} else "workspace",
                        code="unsupported_scope",
                        message=f"Setting writes are not available at scope {scope}.",
                        boundary="write_request",
                        rule="scope",
                    )
                ]
            )
        expected_versions = expected_versions or {}
        entries: dict[str, SettingRegistryEntry] = {}
        validated: dict[str, Any] = {}
        validation_issues: list[SettingValidationIssue] = []

        for key, value in changes.items():
            migration_rule = self._rules_by_old_key.get(key)
            if migration_rule is not None and migration_rule.state in {
                "renamed",
                "deprecated",
                "removed",
            }:
                validation_issues.append(
                    self._validation_issue_for_key(
                        key,
                        scope,
                        code="setting_not_exposed",
                        message=migration_rule.message,
                        boundary="write_request",
                        rule="migration_state",
                    )
                )
                continue
            entry = self._entries_by_key.get(key)
            if entry is None:
                validation_issues.append(
                    self._validation_issue_for_key(
                        key,
                        scope,
                        code="setting_not_exposed",
                        message=f"Setting {key} is not exposed through the Settings API.",
                        boundary="write_request",
                        rule="exposed_setting",
                    )
                )
                continue
            if scope not in entry.scopes:
                validation_issues.append(
                    self._validation_issue(
                        entry,
                        scope,
                        code="unsupported_scope",
                        message=f"Setting {key} is not available at scope {scope}.",
                        boundary="write_request",
                        rule="scope",
                    )
                )
                continue
            if self._read_only(entry):
                code = (
                    "operator_locked"
                    if self._is_operator_locked(entry)
                    else "read_only_setting"
                )
                validation_issues.append(
                    self._validation_issue(
                        entry,
                        scope,
                        code=code,
                        message=self._read_only_reason(entry) or "Setting is read-only.",
                        boundary="write_request",
                        rule="write_lock",
                    )
                )
                continue
            entries[key] = entry
            validated[key] = value
        if validation_issues:
            raise SettingsValidationError(validation_issues)

        current_rows = await self._get_overrides(
            scope=scope,
            keys=validated.keys(),
            for_update=True,
        )
        for key in validated:
            row = current_rows.get((scope, key))
            current_version = row.value_version if row is not None else 1
            expected = expected_versions.get(key)
            if expected is not None and expected != current_version:
                raise ValueError("version_conflict")

        proposed_values = await self._proposed_effective_values(
            scope=scope,
            changes=validated,
        )
        await self._prime_referenced_resources(proposed_values)
        validation_issues.extend(
            self._validate_values(
                proposed_values,
                scope=scope,
                boundary="write_request",
            )
        )
        validation_issues.extend(
            self._confirmation_issues_for_changes(
                entries,
                changes,
                scope=scope,
                confirmation=confirmation,
            )
        )
        if validation_issues:
            raise SettingsValidationError(validation_issues)

        pre_persistence_issues = self._validate_values(
            proposed_values,
            scope=scope,
            boundary="pre_persistence",
        )
        if pre_persistence_issues:
            raise SettingsValidationError(pre_persistence_issues)

        changed_at = datetime.now(timezone.utc)
        change_events: list[SettingsChangeEvent] = []
        for key, value in validated.items():
            entry = entries[key]
            row = current_rows.get((scope, key))
            old_value = row.value_json if row is not None else None
            source = f"{scope}_override"
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
                    request_id=request_id,
                )
            )
            change_events.append(
                SettingsChangeEvent(
                    key=entry.key,
                    scope=scope,
                    source=source,
                    apply_mode=entry.apply_mode,
                    actor_user_id=(
                        self._user_id if self._user_id != _DEFAULT_SUBJECT_ID else None
                    ),
                    changed_at=changed_at,
                    affected_systems=list(entry.applies_to),
                    refresh_targets=self._refresh_targets_for_entry(entry),
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
        await self._prime_provider_profile_statuses(
            data[0]
            for key, data in resolved.items()
            if entries[key].key == "workflow.default_provider_profile_ref"
        )
        values = {}
        for key, data in resolved.items():
            entry = entries[key]
            diagnostics = self._diagnostics_for_override(
                entry,
                data[0],
                data[3],
                scope=scope,
                boundary="effective_preview",
            )
            values[key] = EffectiveSettingValue(
                key=entry.key,
                scope=scope,
                value=data[0],
                **self._effective_metadata(entry, data[0], data[1], data[3], diagnostics),
                **self._activation_metadata(
                    entry,
                    data[0],
                    pending_activation=True,
                ),
                value_version=data[2],
                diagnostics=diagnostics,
            )
        return EffectiveSettingsResponse(
            scope=scope,
            values=values,
            change_events=change_events,
        )

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
                    request_id=None,
                )
            )
        await self._session.commit()
        return await self.effective_value_async(key, scope=scope)

    async def audit_event_count(self) -> int:
        if self._session is None:
            return 0
        result = await self._session.execute(select(func.count(SettingsAuditEvent.id)))
        return int(result.scalar_one())

    async def list_audit_events(
        self,
        *,
        permissions: set[str],
        key: str | None = None,
        scope: SettingScope | None = None,
        limit: int = 50,
    ) -> list[SettingsAuditRead]:
        if self._session is None:
            return []
        bounded_limit = min(max(limit, 1), 200)
        statement = select(SettingsAuditEvent).order_by(
            desc(SettingsAuditEvent.created_at)
        ).limit(bounded_limit)
        statement = statement.where(SettingsAuditEvent.workspace_id == self._workspace_id)
        if key:
            statement = statement.where(SettingsAuditEvent.key == key)
        if scope:
            statement = statement.where(SettingsAuditEvent.scope == scope)
            subject_id = self._user_id if scope == "user" else _DEFAULT_SUBJECT_ID
            statement = statement.where(SettingsAuditEvent.user_id == subject_id)
        else:
            statement = statement.where(
                SettingsAuditEvent.user_id.in_(
                    {_DEFAULT_SUBJECT_ID, self._user_id}
                )
            )
        result = await self._session.execute(statement)
        return [
            self._audit_read_model(row, permissions=permissions)
            for row in result.scalars().all()
        ]

    async def record_rejected_write_audit(
        self,
        *,
        scope: SettingScope,
        changes: dict[str, Any],
        reason: str | None = None,
        request_id: str | None = None,
    ) -> None:
        if self._session is None or not changes:
            return
        for key, value in changes.items():
            entry = self._entries_by_key.get(key)
            self._session.add(
                self._rejected_audit_event(
                    entry,
                    key=key,
                    scope=scope,
                    new_value=value,
                    reason=reason,
                    request_id=request_id,
                )
            )
        await self._session.commit()

    async def diagnostics(
        self,
        *,
        scope: SettingScope,
        key: str | None = None,
    ) -> SettingsDiagnosticsResponse:
        entries = self._entries_for_scope(scope)
        if key is not None:
            if key not in self._entries_by_key:
                raise KeyError(key)
            entry = self._entries_by_key[key]
            if scope not in entry.scopes:
                raise ValueError("invalid_scope")
            entries = [entry]

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
        await self._prime_managed_secret_statuses(data[0] for _entry, data in resolved.values())
        await self._prime_provider_profile_statuses(
            data[0]
            for entry, data in resolved.values()
            if entry.key == "workflow.default_provider_profile_ref"
        )
        proposed_values = {entry.key: data[0] for entry, data in resolved.values()}
        validation_by_key: dict[str, list[SettingValidationIssue]] = {}
        for issue in self._validate_values(
            proposed_values,
            scope=scope,
            boundary="readiness_diagnostics",
        ):
            validation_by_key.setdefault(issue.key, []).append(issue)
        recent_changes = await self._recent_changes([entry.key for entry in entries])
        values = {}
        for entry, data in resolved.values():
            diagnostics = list(
                self._diagnostics_for_override(
                    entry,
                    data[0],
                    data[3],
                    scope=scope,
                    boundary="readiness_diagnostics",
                )
            )
            existing_codes = {diagnostic.code for diagnostic in diagnostics}
            diagnostics.extend(
                self._diagnostic_from_validation_issue(issue)
                for issue in validation_by_key.get(entry.key, [])
                if not self._diagnostic_code_present(issue.code, existing_codes)
            )
            metadata = self._effective_metadata(entry, data[0], data[1], data[3], diagnostics)
            if metadata["read_only"]:
                diagnostics.append(
                    SettingDiagnostic(
                        code="read_only_setting",
                        message=metadata["read_only_reason"] or f"{entry.key} is read-only.",
                        severity="warning",
                    )
                )
            if entry.requires_reload:
                diagnostics.append(
                    SettingDiagnostic(
                        code="requires_reload",
                        message=f"{entry.key} requires reload after changes.",
                        severity="info",
                    )
                )
            if entry.requires_worker_restart or entry.requires_process_restart:
                diagnostics.append(
                    SettingDiagnostic(
                        code="requires_restart",
                        message=f"{entry.key} requires restart after changes.",
                        severity="info",
                    )
                )
            values[entry.key] = SettingsDiagnosticRead(
                key=entry.key,
                scope=scope,
                value=None if entry.audit.redact else data[0],
                default_value=entry.default_value,
                source=metadata["source"],
                source_explanation=metadata["source_explanation"],
                inheritance_state=metadata["inheritance_state"],
                **self._activation_metadata(entry, data[0]),
                read_only=metadata["read_only"],
                read_only_reason=metadata["read_only_reason"],
                requires_reload=entry.requires_reload,
                requires_worker_restart=entry.requires_worker_restart,
                requires_process_restart=entry.requires_process_restart,
                applies_to=list(entry.applies_to),
                value_version=data[2],
                diagnostics=diagnostics,
                recent_change=recent_changes.get(entry.key),
            )
        if key is None:
            values.update(await self._deprecated_override_diagnostics(scope=scope))
        return SettingsDiagnosticsResponse(scope=scope, values=values)

    def _validate_apply_modes(self) -> None:
        self._validate_migration_rules()
        for entry in self._registry:
            if not entry.apply_mode:
                raise ValueError(f"invalid_apply_mode for setting {entry.key!r}")
            if entry.apply_mode == "worker_reload" and not entry.requires_reload:
                raise ValueError(
                    f"invalid_apply_mode for setting {entry.key!r}: "
                    "worker_reload requires requires_reload"
                )
            if entry.apply_mode == "process_restart" and not (
                entry.requires_worker_restart or entry.requires_process_restart
            ):
                raise ValueError(
                    f"invalid_apply_mode for setting {entry.key!r}: "
                    "process_restart requires a restart flag"
                )
            if entry.apply_mode not in {"immediate"} and not entry.applies_to:
                raise ValueError(
                    f"invalid_apply_mode for setting {entry.key!r}: "
                    "non-immediate settings require affected systems"
                )

    def _validate_migration_rules(self) -> None:
        seen: set[str] = set()
        rename_targets: set[str] = set()
        for rule in self._migration_rules:
            if rule.old_key in seen:
                raise ValueError(f"duplicate migration rule for {rule.old_key!r}")
            seen.add(rule.old_key)
            if rule.state == "renamed":
                if rule.new_key in rename_targets:
                    raise ValueError(
                        f"duplicate rename migration target {rule.new_key!r}"
                    )
                if rule.new_key is not None:
                    rename_targets.add(rule.new_key)
                if rule.new_key not in self._entries_by_key:
                    raise ValueError(
                        f"renamed migration target {rule.new_key!r} is not exposed"
                    )

    def _activation_metadata(
        self,
        entry: SettingRegistryEntry,
        value: Any,
        *,
        pending_activation: bool = False,
    ) -> dict[str, Any]:
        pending_state_by_mode: dict[str, SettingActivationState] = {
            "next_request": "pending_next_boundary",
            "next_task": "pending_next_boundary",
            "next_launch": "pending_next_boundary",
            "worker_reload": "pending_reload",
            "process_restart": "pending_restart",
            "manual_operation": "pending_manual_operation",
        }
        guidance_by_mode = {
            "immediate": None,
            "next_request": "New requests will use this value.",
            "next_task": "New tasks will use this value when they are created.",
            "next_launch": "New launches will use this value the next time they start.",
            "worker_reload": "Reload affected workers to activate this value.",
            "process_restart": "Restart the affected process to activate this value.",
            "manual_operation": "Use the related operation control to activate this value.",
        }
        activation_state: SettingActivationState = "active"
        if pending_activation and entry.apply_mode != "immediate":
            activation_state = pending_state_by_mode[entry.apply_mode]
        pending_value = None if activation_state == "active" else value
        if (
            activation_state != "active"
            and entry.value_type != "secret_ref"
            and (entry.sensitive or entry.audit.redact)
        ):
            pending_value = "[redacted]"
        return {
            "apply_mode": entry.apply_mode,
            "activation_state": activation_state,
            "active": activation_state == "active",
            "pending_value": pending_value,
            "affected_process_or_worker": ", ".join(entry.applies_to) or None,
            "completion_guidance": guidance_by_mode[entry.apply_mode],
        }

    def _is_operator_locked(self, entry: SettingRegistryEntry) -> bool:
        return entry.operator_lock_reason is not None

    def _read_only(self, entry: SettingRegistryEntry) -> bool:
        return entry.read_only or self._is_operator_locked(entry)

    def _read_only_reason(self, entry: SettingRegistryEntry) -> str | None:
        if self._is_operator_locked(entry):
            return entry.operator_lock_reason
        return entry.read_only_reason

    def _canonical_source(
        self,
        entry: SettingRegistryEntry,
        source: str,
        value: Any,
    ) -> str:
        if entry.value_type == "secret_ref" and isinstance(value, str):
            if value.startswith("db://"):
                return "secret_ref"
        if (
            entry.key == "workflow.default_provider_profile_ref"
            and isinstance(value, str)
            and value.strip()
            and source not in {"workspace_override", "user_override"}
        ):
            return "provider_profile"
        return source

    def _inheritance_state(
        self,
        entry: SettingRegistryEntry,
        value: Any,
        source: str,
        override_present: bool,
        diagnostics: list[SettingDiagnostic],
    ) -> str:
        diagnostic_codes = {diagnostic.code for diagnostic in diagnostics}
        if source == "operator_lock":
            return "locked"
        if "policy_blocked" in diagnostic_codes:
            return "blocked"
        if "post_migration_invalid" in diagnostic_codes:
            return "invalid"
        if "no_default" in diagnostic_codes:
            return "missing"
        if "intentional_null_override" in diagnostic_codes:
            return "intentionally_null"
        if override_present:
            return "overridden"
        if value is None:
            return "inherited_null"
        return "inherited"

    def _effective_metadata(
        self,
        entry: SettingRegistryEntry,
        value: Any,
        source: str,
        override_present: bool,
        diagnostics: list[SettingDiagnostic],
    ) -> dict[str, Any]:
        canonical_source = self._canonical_source(entry, source, value)
        return {
            "default_value": entry.default_value,
            "source": canonical_source,
            "source_explanation": self._source_explanation(entry, canonical_source),
            "inheritance_state": self._inheritance_state(
                entry,
                value,
                canonical_source,
                override_present,
                diagnostics,
            ),
            "read_only": self._read_only(entry),
            "read_only_reason": self._read_only_reason(entry),
            "requires_reload": entry.requires_reload,
            "requires_worker_restart": entry.requires_worker_restart,
            "requires_process_restart": entry.requires_process_restart,
            "applies_to": list(entry.applies_to),
        }

    def _refresh_targets_for_entry(self, entry: SettingRegistryEntry) -> list[str]:
        targets = {"settings_catalog"}
        applies_to = set(entry.applies_to)
        if "task_creation" in applies_to:
            targets.add("task_creation_defaults")
        if "provider_profiles" in applies_to:
            targets.add("provider_profile_manager")
        if entry.apply_mode == "worker_reload" or entry.requires_reload:
            targets.add("worker_reloaders")
        if "operations" in applies_to or entry.apply_mode == "manual_operation":
            targets.add("operational_controls")
        return sorted(targets)

    def _resolve_value(self, entry: SettingRegistryEntry) -> tuple[Any, str]:
        if self._is_operator_locked(entry):
            return entry.operator_locked_value, "operator_lock"
        for alias in entry.env_aliases:
            if alias in self._env:
                return self._parse_env_value(entry, self._env[alias]), "environment"
        if entry.settings_path is not None:
            current: Any = self._settings
            try:
                for part in entry.settings_path:
                    current = getattr(current, part)
                return current, "config_file"
            except AttributeError:
                return None, "missing"
        if entry.default_value is None and not entry.env_aliases:
            return None, "no_default"
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
        diagnostics = self._diagnostics_for_override(
            entry,
            value,
            override_present,
            scope=scope,
            boundary="effective_preview",
        )
        return EffectiveSettingValue(
            key=entry.key,
            scope=scope,
            value=value,
            **self._effective_metadata(entry, value, source, override_present, diagnostics),
            **self._activation_metadata(entry, value),
            value_version=version,
            diagnostics=diagnostics,
        )

    def _diagnostics_for_override(
        self,
        entry: SettingRegistryEntry,
        value: Any,
        override_present: bool,
        *,
        scope: SettingScope = "workspace",
        boundary: SettingValidationBoundary = "effective_preview",
    ) -> list[SettingDiagnostic]:
        migration_diagnostic = self._migration_diagnostics_by_key.get(entry.key)
        if migration_diagnostic is not None:
            return [migration_diagnostic]
        diagnostics: list[SettingDiagnostic] = []
        if entry.policy_blocked_reason is not None:
            diagnostics.append(
                SettingDiagnostic(
                    code="policy_blocked",
                    message=entry.policy_blocked_reason,
                    severity="error",
                )
            )
        if override_present and value is None:
            diagnostics.append(
                SettingDiagnostic(
                    code="intentional_null_override",
                    message=f"{entry.key} is intentionally cleared by override.",
                    severity="info",
                )
            )
            return diagnostics
        diagnostics.extend(self._diagnostics(entry, value))
        validation_issues = self._validation_issues_for_value(
            entry,
            value,
            scope=scope,
            boundary=boundary,
        )
        validation_issues.extend(
            self._policy_issues_for_changes(
                {entry.key: value},
                scope=scope,
                boundary=boundary,
            )
        )
        diagnostics.extend(
            self._diagnostic_from_validation_issue(issue)
            for issue in validation_issues
        )
        return diagnostics

    def _diagnostic_from_validation_issue(
        self,
        issue: SettingValidationIssue,
    ) -> SettingDiagnostic:
        details = issue.model_dump(mode="json")
        details.pop("key", None)
        details.pop("scope", None)
        details.pop("code", None)
        details.pop("message", None)
        return SettingDiagnostic(
            code=issue.code,
            message=issue.message,
            severity="error",
            details=details,
        )

    def _diagnostic_code_present(
        self,
        issue_code: str,
        existing_codes: set[str],
    ) -> bool:
        if issue_code in existing_codes:
            return True
        return issue_code == "secret_ref_unresolved" and (
            "unresolved_secret_ref" in existing_codes
        )

    async def _deprecated_override_diagnostics(
        self,
        *,
        scope: SettingScope,
    ) -> dict[str, SettingsDiagnosticRead]:
        deprecated_rules = [
            rule
            for rule in self._migration_rules
            if rule.state in {"deprecated", "removed"}
        ]
        if self._session is None or not deprecated_rules:
            return {}
        overrides = await self._get_effective_overrides(
            scope=scope,
            keys=[rule.old_key for rule in deprecated_rules],
        )
        values: dict[str, SettingsDiagnosticRead] = {}
        for rule in deprecated_rules:
            for row_scope in (("user", "workspace") if scope == "user" else ("workspace",)):
                row = overrides.get((row_scope, rule.old_key))
                if row is None:
                    continue
                values[rule.old_key] = SettingsDiagnosticRead(
                    key=rule.old_key,
                    scope=scope,
                    source="deprecated_override",
                    source_explanation=rule.message,
                    apply_mode="immediate",
                    activation_state="active",
                    active=True,
                    diagnostics=[
                        SettingDiagnostic(
                            code="setting_deprecated_override",
                            message=rule.message,
                            severity="warning",
                            details={
                                "old_key": rule.old_key,
                                "state": rule.state,
                                "value_version": row.value_version,
                                "schema_version": row.schema_version,
                            },
                        )
                    ],
                    recent_change=None,
                )
                break
        return values

    async def _prime_managed_secret_statuses(self, values: Iterable[Any]) -> None:
        if self._session is None:
            return
        slugs = {
            value.removeprefix("db://")
            for value in values
            if isinstance(value, str) and value.startswith("db://")
        }
        missing = [
            slug for slug in slugs if slug not in self._managed_secret_status_by_slug
        ]
        if not missing:
            return
        result = await self._session.execute(
            select(ManagedSecret.slug, ManagedSecret.status).where(
                ManagedSecret.slug.in_(missing)
            )
        )

        statuses_from_db = {
            slug: status.value if isinstance(status, SecretStatus) else str(status)
            for slug, status in result.all()
        }

        for slug in missing:
            self._managed_secret_status_by_slug[slug] = statuses_from_db.get(slug)

    async def _prime_provider_profile_statuses(self, values: Iterable[Any]) -> None:
        if self._session is None:
            return
        profile_ids = {
            value.strip()
            for value in values
            if isinstance(value, str) and value.strip()
        }
        missing = [
            profile_id
            for profile_id in profile_ids
            if profile_id not in self._provider_profile_enabled_by_id
        ]
        if not missing:
            return
        result = await self._session.execute(
            select(
                ManagedAgentProviderProfile.profile_id,
                ManagedAgentProviderProfile.enabled,
                ManagedAgentProviderProfile.credential_source,
                ManagedAgentProviderProfile.volume_ref,
            ).where(ManagedAgentProviderProfile.profile_id.in_(missing))
        )
        rows = result.all()
        for profile_id, enabled, credential_source, volume_ref in rows:
            is_enabled = bool(enabled)
            self._provider_profile_enabled_by_id[profile_id] = is_enabled
            self._provider_profile_metadata_by_id[profile_id] = {
                "enabled": is_enabled,
                "credential_source": credential_source,
                "volume_ref": volume_ref,
            }
        for profile_id in missing:
            if profile_id not in self._provider_profile_enabled_by_id:
                self._provider_profile_enabled_by_id[profile_id] = None

    def _resolve_value_from_overrides(
        self,
        entry: SettingRegistryEntry,
        *,
        scope: SettingScope,
        overrides: dict[tuple[SettingScope, str], SettingsOverride],
    ) -> tuple[Any, str, int, bool]:
        self._migration_diagnostics_by_key.pop(entry.key, None)
        if self._is_operator_locked(entry):
            return entry.operator_locked_value, "operator_lock", 1, False
        if scope == "user":
            user_override = overrides.get(("user", entry.key))
            if user_override is not None:
                type_migration = self._type_migration_diagnostic(entry, user_override)
                if type_migration is not None:
                    self._migration_diagnostics_by_key[entry.key] = type_migration
                    return (
                        None,
                        "user_override",
                        user_override.value_version,
                        True,
                    )
                return (
                    user_override.value_json,
                    "user_override",
                    user_override.value_version,
                    True,
                )
            workspace_override = overrides.get(("workspace", entry.key))
            if workspace_override is not None:
                type_migration = self._type_migration_diagnostic(
                    entry, workspace_override
                )
                if type_migration is not None:
                    self._migration_diagnostics_by_key[entry.key] = type_migration
                    return (
                        None,
                        "workspace_override",
                        workspace_override.value_version,
                        True,
                    )
                return (
                    workspace_override.value_json,
                    "workspace_override",
                    workspace_override.value_version,
                    True,
                )
        elif scope == "workspace":
            workspace_override = overrides.get(("workspace", entry.key))
            if workspace_override is not None:
                type_migration = self._type_migration_diagnostic(
                    entry, workspace_override
                )
                if type_migration is not None:
                    self._migration_diagnostics_by_key[entry.key] = type_migration
                    return (
                        None,
                        "workspace_override",
                        workspace_override.value_version,
                        True,
                    )
                return (
                    workspace_override.value_json,
                    "workspace_override",
                    workspace_override.value_version,
                    True,
                )
        migrated = self._resolve_migrated_override(entry, scope=scope, overrides=overrides)
        if migrated is not None:
            row_scope, row, rule = migrated
            source = "user_override" if row_scope == "user" else "workspace_override"
            self._migration_diagnostics_by_key[entry.key] = SettingDiagnostic(
                code="setting_renamed_override",
                message=rule.message,
                severity="warning",
                details={
                    "old_key": rule.old_key,
                    "new_key": rule.new_key,
                    "state": rule.state,
                },
            )
            return row.value_json, source, row.value_version, True
        value, source = self._resolve_value(entry)
        return value, source, 1, False

    def _type_migration_diagnostic(
        self,
        entry: SettingRegistryEntry,
        row: SettingsOverride,
    ) -> SettingDiagnostic | None:
        rule = self._type_rules_by_key.get(entry.key)
        if rule is None or row.schema_version == rule.expected_schema_version:
            return None
        return SettingDiagnostic(
            code="post_migration_invalid",
            message=rule.message,
            severity="error",
            details={
                "key": entry.key,
                "state": rule.state,
                "schema_version": row.schema_version,
                "expected_schema_version": rule.expected_schema_version,
            },
        )

    def _resolve_migrated_override(
        self,
        entry: SettingRegistryEntry,
        *,
        scope: SettingScope,
        overrides: dict[tuple[SettingScope, str], SettingsOverride],
    ) -> tuple[SettingScope, SettingsOverride, SettingMigrationRule] | None:
        rule = self._rename_rules_by_new_key.get(entry.key)
        if rule is None:
            return None
        if scope == "user":
            user_override = overrides.get(("user", rule.old_key))
            if user_override is not None:
                return "user", user_override, rule
            workspace_override = overrides.get(("workspace", rule.old_key))
            if workspace_override is not None:
                return "workspace", workspace_override, rule
        elif scope == "workspace":
            workspace_override = overrides.get(("workspace", rule.old_key))
            if workspace_override is not None:
                return "workspace", workspace_override, rule
        return None

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
        expanded_keys = set(keys)
        for key in keys:
            rule = self._rename_rules_by_new_key.get(key)
            if rule is not None:
                expanded_keys.add(rule.old_key)
        for rule in self._migration_rules:
            if rule.state in {"deprecated", "removed"} and rule.old_key in keys:
                expanded_keys.add(rule.old_key)
        return await self._get_overrides(scopes=scopes, keys=expanded_keys)

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
        if source == "config_file":
            return "Resolved from application settings configuration."
        if source == "default":
            return "Resolved from the catalog default value."
        if source == "missing":
            return "No configured value or catalog default could be resolved."
        if source == "no_default":
            return "No configured value or catalog default could be resolved."
        if source == "workspace_override":
            return "Resolved from a workspace override."
        if source == "user_override":
            return "Resolved from a user override."
        if source == "operator_lock":
            return entry.operator_lock_reason or "Resolved from an operator lock."
        if source == "secret_ref":
            return "Resolved as a managed SecretRef reference."
        if source == "provider_profile":
            return "Resolved as a provider profile reference."
        if source == "deprecated_override":
            return "Resolved from a deprecated setting override."
        return f"Resolved from {source}."

    def validate_descriptor_generation(self) -> list[SettingValidationIssue]:
        issues: list[SettingValidationIssue] = []
        supported_types = {
            "enum",
            "integer",
            "number",
            "boolean",
            "secret_ref",
            "string",
            "list",
            "object",
        }
        supported_scopes = {"user", "workspace", "system", "operator"}
        supported_apply_modes = {
            "immediate",
            "next_request",
            "next_task",
            "next_launch",
            "worker_reload",
            "process_restart",
            "manual_operation",
        }
        for entry in self._registry:
            if not _SETTING_KEY_RE.fullmatch(entry.key):
                issues.append(
                    self._validation_issue(
                        entry,
                        None,
                        code="descriptor_constraint_invalid",
                        message=f"{entry.key} has an invalid setting key format.",
                        boundary="descriptor_generation",
                        rule="key_format",
                    )
                )
            if entry.value_type not in supported_types:
                issues.append(
                    self._validation_issue(
                        entry,
                        None,
                        code="descriptor_constraint_invalid",
                        message=f"{entry.key} uses unsupported setting type.",
                        boundary="descriptor_generation",
                        rule="value_type",
                        details={"value_type": entry.value_type},
                    )
                )
            if not entry.scopes or any(scope not in supported_scopes for scope in entry.scopes):
                issues.append(
                    self._validation_issue(
                        entry,
                        None,
                        code="descriptor_constraint_invalid",
                        message=f"{entry.key} declares invalid scopes.",
                        boundary="descriptor_generation",
                        rule="scopes",
                    )
                )
            if entry.value_type == "enum" and not entry.options:
                issues.append(
                    self._validation_issue(
                        entry,
                        None,
                        code="descriptor_constraint_invalid",
                        message=f"{entry.key} enum descriptor requires options.",
                        boundary="descriptor_generation",
                        rule="descriptor_options",
                    )
                )
            if entry.value_type == "enum" and entry.options:
                option_values = [value for value, _label in entry.options]
                if len(option_values) != len(set(option_values)):
                    issues.append(
                        self._validation_issue(
                            entry,
                            None,
                            code="descriptor_constraint_invalid",
                            message=f"{entry.key} enum descriptor has duplicate options.",
                            boundary="descriptor_generation",
                            rule="descriptor_options",
                        )
                    )
            constraints = entry.constraints
            if constraints is not None:
                if (
                    constraints.minimum is not None
                    and constraints.maximum is not None
                    and constraints.minimum > constraints.maximum
                ):
                    issues.append(
                        self._validation_issue(
                            entry,
                            None,
                            code="descriptor_constraint_invalid",
                            message=f"{entry.key} numeric constraints are incoherent.",
                            boundary="descriptor_generation",
                            rule="numeric_constraints",
                        )
                    )
                if (
                    constraints.min_length is not None
                    and constraints.max_length is not None
                    and constraints.min_length > constraints.max_length
                ):
                    issues.append(
                        self._validation_issue(
                            entry,
                            None,
                            code="descriptor_constraint_invalid",
                            message=f"{entry.key} string constraints are incoherent.",
                            boundary="descriptor_generation",
                            rule="string_constraints",
                        )
                    )
                if (
                    constraints.min_items is not None
                    and constraints.max_items is not None
                    and constraints.min_items > constraints.max_items
                ):
                    issues.append(
                        self._validation_issue(
                            entry,
                            None,
                            code="descriptor_constraint_invalid",
                            message=f"{entry.key} list constraints are incoherent.",
                            boundary="descriptor_generation",
                            rule="list_constraints",
                        )
                    )
                if constraints.allowed_keys is not None and constraints.required_keys is not None:
                    extra_required = set(constraints.required_keys) - set(
                        constraints.allowed_keys
                    )
                    if extra_required:
                        issues.append(
                            self._validation_issue(
                                entry,
                                None,
                                code="descriptor_constraint_invalid",
                                message=(
                                    f"{entry.key} requires fields not in allowed keys."
                                ),
                                boundary="descriptor_generation",
                                rule="object_constraints",
                            )
                        )
            if entry.apply_mode not in supported_apply_modes:
                issues.append(
                    self._validation_issue(
                        entry,
                        None,
                        code="descriptor_constraint_invalid",
                        message=f"{entry.key} has an invalid apply mode.",
                        boundary="descriptor_generation",
                        rule="apply_mode",
                    )
                )
            if entry.apply_mode != "immediate" and not entry.applies_to:
                issues.append(
                    self._validation_issue(
                        entry,
                        None,
                        code="descriptor_constraint_invalid",
                        message=f"{entry.key} non-immediate apply mode requires affected systems.",
                        boundary="descriptor_generation",
                        rule="affected_systems",
                    )
                )
            for dependency in entry.depends_on:
                if dependency.key not in self._entries_by_key:
                    issues.append(
                        self._validation_issue(
                            entry,
                            None,
                            code="descriptor_constraint_invalid",
                            message=f"{entry.key} depends on an unknown setting.",
                            boundary="descriptor_generation",
                            rule="dependency_reference",
                            details={"dependency_key": dependency.key},
                        )
                    )
        return issues

    def validate_effective_preview(
        self,
        key: str,
        value: Any,
        *,
        scope: SettingScope,
    ) -> list[SettingValidationIssue]:
        proposed_values = self._current_effective_values(scope=scope)
        proposed_values[key] = value
        self._prime_referenced_resources_sync(proposed_values)
        return self._validate_values(
            proposed_values,
            scope=scope,
            boundary="effective_preview",
        )

    def validate_launch_execution(
        self,
        values: dict[str, Any],
        *,
        scope: SettingScope,
    ) -> list[SettingValidationIssue]:
        return self._validate_runtime_values(
            values,
            scope=scope,
            boundary="launch_execution",
        )

    def validate_operation_execution(
        self,
        values: dict[str, Any],
        *,
        scope: SettingScope,
    ) -> list[SettingValidationIssue]:
        return self._validate_runtime_values(
            values,
            scope=scope,
            boundary="operation_execution",
        )

    def readiness_diagnostics(
        self,
        values: dict[str, Any],
        *,
        scope: SettingScope,
    ) -> dict[str, list[SettingValidationIssue]]:
        issues = self._validate_runtime_values(
            values,
            scope=scope,
            boundary="readiness_diagnostics",
        )
        by_key: dict[str, list[SettingValidationIssue]] = {}
        for issue in issues:
            by_key.setdefault(issue.key, []).append(issue)
        return by_key

    def _validate_runtime_values(
        self,
        values: dict[str, Any],
        *,
        scope: SettingScope,
        boundary: SettingValidationBoundary,
    ) -> list[SettingValidationIssue]:
        proposed_values = self._current_effective_values(scope=scope)
        proposed_values.update(values)
        self._prime_referenced_resources_sync(proposed_values)
        return self._validate_values(
            proposed_values,
            scope=scope,
            boundary=boundary,
        )

    def _validate_override_value(
        self,
        entry: SettingRegistryEntry,
        value: Any,
        *,
        scope: SettingScope,
    ) -> None:
        issues = self._validation_issues_for_value(
            entry,
            value,
            scope=scope,
            boundary="write_request",
        )
        if issues:
            raise SettingsValidationError(issues)

    async def _proposed_effective_values(
        self,
        *,
        scope: SettingScope,
        changes: dict[str, Any],
    ) -> dict[str, Any]:
        entries = self._entries_for_scope(scope)
        overrides = await self._get_effective_overrides(
            scope=scope,
            keys=[entry.key for entry in entries],
        )
        proposed_values = {
            entry.key: self._resolve_value_from_overrides(
                entry,
                scope=scope,
                overrides=overrides,
            )[0]
            for entry in entries
        }
        proposed_values.update(changes)
        return proposed_values

    def _current_effective_values(self, *, scope: SettingScope) -> dict[str, Any]:
        return {
            entry.key: self._resolve_value(entry)[0]
            for entry in self._entries_for_scope(scope)
        }

    async def _prime_referenced_resources(self, values: dict[str, Any]) -> None:
        await self._prime_managed_secret_statuses(values.values())
        await self._prime_provider_profile_statuses(
            value
            for key, value in values.items()
            if key == "workflow.default_provider_profile_ref"
        )

    def _prime_referenced_resources_sync(self, values: dict[str, Any]) -> None:
        for value in values.values():
            if isinstance(value, str) and value.startswith("db://"):
                self._managed_secret_status_by_slug.setdefault(
                    value.removeprefix("db://"),
                    None,
                )
        provider_profile = values.get("workflow.default_provider_profile_ref")
        if isinstance(provider_profile, str) and provider_profile.strip():
            self._provider_profile_enabled_by_id.setdefault(
                provider_profile.strip(),
                None,
            )

    def _validate_values(
        self,
        values: dict[str, Any],
        *,
        scope: SettingScope,
        boundary: SettingValidationBoundary,
    ) -> list[SettingValidationIssue]:
        issues: list[SettingValidationIssue] = []
        for key, value in values.items():
            entry = self._entries_by_key.get(key)
            if entry is None:
                issues.append(
                    self._validation_issue_for_key(
                        key,
                        scope,
                        code="setting_not_exposed",
                        message=f"Setting {key} is not exposed through the Settings API.",
                        boundary=boundary,
                        rule="exposed_setting",
                    )
                )
                continue
            if scope not in entry.scopes:
                issues.append(
                    self._validation_issue(
                        entry,
                        scope,
                        code="unsupported_scope",
                        message=f"Setting {key} is not available at scope {scope}.",
                        boundary=boundary,
                        rule="scope",
                    )
                )
                continue
            issues.extend(
                self._validation_issues_for_value(
                    entry,
                    value,
                    scope=scope,
                    boundary=boundary,
                )
            )
            issues.extend(
                self._dependency_issues_for_values(
                    entry,
                    values,
                    scope=scope,
                    boundary=boundary,
                )
            )
            issues.extend(
                self._reference_validation_issues(
                    entry,
                    value,
                    scope=scope,
                    boundary=boundary,
                )
            )
        issues.extend(
            self._policy_issues_for_changes(values, scope=scope, boundary=boundary)
        )
        return issues

    def _confirmation_issues_for_changes(
        self,
        entries: dict[str, SettingRegistryEntry],
        changes: dict[str, Any],
        *,
        scope: SettingScope,
        confirmation: str | None,
    ) -> list[SettingValidationIssue]:
        if confirmation is not None and confirmation.strip():
            return []
        issues: list[SettingValidationIssue] = []
        for key in changes:
            entry = entries.get(key)
            if entry is None or entry.apply_mode != "manual_operation":
                continue
            issues.append(
                self._validation_issue(
                    entry,
                    scope,
                    code="requires_confirmation",
                    message=f"{key} requires confirmation before it can be changed.",
                    boundary="write_request",
                    rule="confirmation",
                    details={
                        "apply_mode": entry.apply_mode,
                        "applies_to": list(entry.applies_to),
                    },
                )
            )
        return issues

    def _validation_issue(
        self,
        entry: SettingRegistryEntry,
        scope: SettingScope | None,
        *,
        code: str,
        message: str,
        boundary: SettingValidationBoundary,
        rule: str,
        details: dict[str, Any] | None = None,
    ) -> SettingValidationIssue:
        return SettingValidationIssue(
            key=entry.key,
            scope=scope or "workspace",
            code=code,
            message=message,
            boundary=boundary,
            rule=rule,
            blocks=list(_VALIDATION_BLOCKS_BY_BOUNDARY[boundary]),
            details=self._redact_validation_details(details or {}),
        )

    def _validation_issue_for_key(
        self,
        key: str,
        scope: SettingScope,
        *,
        code: str,
        message: str,
        boundary: SettingValidationBoundary,
        rule: str,
        details: dict[str, Any] | None = None,
    ) -> SettingValidationIssue:
        return SettingValidationIssue(
            key=key,
            scope=scope,
            code=code,
            message=message,
            boundary=boundary,
            rule=rule,
            blocks=list(_VALIDATION_BLOCKS_BY_BOUNDARY[boundary]),
            details=self._redact_validation_details(details or {}),
        )

    def _redact_validation_details(self, details: dict[str, Any]) -> dict[str, Any]:
        safe: dict[str, Any] = {}
        for key, value in details.items():
            key_text = str(key).lower()
            if key_text == "profile_id" and isinstance(value, str) and len(value) > 128:
                safe[key] = "[redacted]"
                continue
            if any(token in key_text for token in _SECRET_LIKE_SUBSTRINGS):
                if key in {"ref_scheme", "status", "launch_blocker", "operation_blocker", "blocks"}:
                    safe[key] = value
                else:
                    safe[key] = "[redacted]"
                continue
            if isinstance(value, str) and (
                any(prefix in value for prefix in _SECRET_PREFIXES)
                or any(token in value.lower() for token in _UNSAFE_STRING_TOKENS)
            ):
                safe[key] = "[redacted]"
            elif isinstance(value, dict):
                safe[key] = self._redact_validation_details(value)
            elif isinstance(value, list):
                safe[key] = [
                    self._redact_validation_details(item)
                    if isinstance(item, dict)
                    else item
                    for item in value
                ]
            else:
                safe[key] = value
        return safe

    def _validation_issues_for_value(
        self,
        entry: SettingRegistryEntry,
        value: Any,
        *,
        scope: SettingScope,
        boundary: SettingValidationBoundary,
    ) -> list[SettingValidationIssue]:
        issues: list[SettingValidationIssue] = []
        if self._override_value_size(value) > _MAX_OVERRIDE_VALUE_BYTES:
            return [
                self._validation_issue(
                    entry,
                    scope,
                    code="value_size_limit_exceeded",
                    message=f"{entry.key} exceeds the maximum settings value size.",
                    boundary=boundary,
                    rule="max_size",
                )
            ]
        if self._contains_unsafe_payload(
            value,
            allow_profile_ref_string_tokens=entry.key
            == "workflow.default_provider_profile_ref",
        ):
            return [
                self._validation_issue(
                    entry,
                    scope,
                    code="unsafe_setting_payload",
                    message=f"{entry.key} contains unsafe setting payload content.",
                    boundary=boundary,
                    rule="unsafe_payload",
                )
            ]
        if value is None:
            return issues
        if entry.value_type == "enum":
            allowed = {option for option, _label in entry.options}
            if not isinstance(value, str) or value not in allowed:
                issues.append(
                    self._validation_issue(
                        entry,
                        scope,
                        code="enum_value_invalid",
                        message=f"{entry.key} must be one of the allowed values.",
                        boundary=boundary,
                        rule="enum",
                        details={"allowed": sorted(allowed)},
                    )
                )
        elif entry.value_type == "integer":
            if not isinstance(value, int) or isinstance(value, bool):
                issues.append(
                    self._type_mismatch_issue(entry, scope, boundary, "integer")
                )
            else:
                issues.extend(self._numeric_constraint_issues(entry, value, scope, boundary))
        elif entry.value_type == "number":
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                issues.append(self._type_mismatch_issue(entry, scope, boundary, "number"))
            else:
                issues.extend(self._numeric_constraint_issues(entry, value, scope, boundary))
        elif entry.value_type == "boolean":
            if not isinstance(value, bool):
                issues.append(self._type_mismatch_issue(entry, scope, boundary, "boolean"))
        elif entry.value_type == "secret_ref":
            if not isinstance(value, str) or "://" not in value:
                issues.append(
                    self._validation_issue(
                        entry,
                        scope,
                        code="invalid_secret_ref",
                        message=f"{entry.key} must be a SecretRef.",
                        boundary=boundary,
                        rule="secret_ref_syntax",
                    )
                )
            elif any(value.startswith(prefix) for prefix in _SECRET_PREFIXES):
                issues.append(
                    self._validation_issue(
                        entry,
                        scope,
                        code="unsafe_setting_payload",
                        message=f"{entry.key} contains unsafe setting payload content.",
                        boundary=boundary,
                        rule="unsafe_payload",
                    )
                )
        elif entry.value_type == "string":
            if not isinstance(value, str):
                issues.append(self._type_mismatch_issue(entry, scope, boundary, "string"))
            else:
                issues.extend(self._string_constraint_issues(entry, value, scope, boundary))
        elif entry.value_type == "list":
            if not isinstance(value, list):
                issues.append(self._type_mismatch_issue(entry, scope, boundary, "list"))
            else:
                issues.extend(self._list_constraint_issues(entry, value, scope, boundary))
        elif entry.value_type == "object":
            if not isinstance(value, dict):
                issues.append(self._type_mismatch_issue(entry, scope, boundary, "object"))
            else:
                issues.extend(self._object_constraint_issues(entry, value, scope, boundary))
        else:
            issues.append(
                self._validation_issue(
                    entry,
                    scope,
                    code="unsupported_setting_type",
                    message=f"{entry.key} uses unsupported setting type {entry.value_type}.",
                    boundary=boundary,
                    rule="value_type",
                    details={"value_type": entry.value_type},
                )
            )
        return issues

    def _type_mismatch_issue(
        self,
        entry: SettingRegistryEntry,
        scope: SettingScope,
        boundary: SettingValidationBoundary,
        expected_type: str,
    ) -> SettingValidationIssue:
        return self._validation_issue(
            entry,
            scope,
            code="type_mismatch",
            message=f"{entry.key} must be a {expected_type} value.",
            boundary=boundary,
            rule="value_type",
            details={"expected_type": expected_type},
        )

    def _numeric_constraint_issues(
        self,
        entry: SettingRegistryEntry,
        value: int | float,
        scope: SettingScope,
        boundary: SettingValidationBoundary,
    ) -> list[SettingValidationIssue]:
        constraints = entry.constraints
        if constraints is None:
            return []
        if constraints.minimum is not None and value < constraints.minimum:
            return [
                self._validation_issue(
                    entry,
                    scope,
                    code="numeric_constraint_failed",
                    message=f"{entry.key} is below the allowed minimum.",
                    boundary=boundary,
                    rule="minimum",
                    details={"minimum": constraints.minimum},
                )
            ]
        if constraints.maximum is not None and value > constraints.maximum:
            return [
                self._validation_issue(
                    entry,
                    scope,
                    code="numeric_constraint_failed",
                    message=f"{entry.key} is above the allowed maximum.",
                    boundary=boundary,
                    rule="maximum",
                    details={"maximum": constraints.maximum},
                )
            ]
        return []

    def _string_constraint_issues(
        self,
        entry: SettingRegistryEntry,
        value: str,
        scope: SettingScope,
        boundary: SettingValidationBoundary,
    ) -> list[SettingValidationIssue]:
        constraints = entry.constraints
        if constraints is None:
            return []
        if constraints.min_length is not None and len(value) < constraints.min_length:
            return [
                self._validation_issue(
                    entry,
                    scope,
                    code="string_constraint_failed",
                    message=f"{entry.key} is shorter than the allowed minimum.",
                    boundary=boundary,
                    rule="min_length",
                    details={"min_length": constraints.min_length},
                )
            ]
        if constraints.max_length is not None and len(value) > constraints.max_length:
            return [
                self._validation_issue(
                    entry,
                    scope,
                    code="string_constraint_failed",
                    message=f"{entry.key} is longer than the allowed maximum.",
                    boundary=boundary,
                    rule="max_length",
                    details={"max_length": constraints.max_length},
                )
            ]
        if constraints.pattern is not None and not re.fullmatch(constraints.pattern, value):
            return [
                self._validation_issue(
                    entry,
                    scope,
                    code="string_constraint_failed",
                    message=f"{entry.key} does not match the required pattern.",
                    boundary=boundary,
                    rule="pattern",
                )
            ]
        return []

    def _list_constraint_issues(
        self,
        entry: SettingRegistryEntry,
        value: list[Any],
        scope: SettingScope,
        boundary: SettingValidationBoundary,
    ) -> list[SettingValidationIssue]:
        constraints = entry.constraints
        if constraints is None:
            return []
        if constraints.min_items is not None and len(value) < constraints.min_items:
            return [
                self._validation_issue(
                    entry,
                    scope,
                    code="list_constraint_failed",
                    message=f"{entry.key} has fewer items than allowed.",
                    boundary=boundary,
                    rule="min_items",
                    details={"min_items": constraints.min_items},
                )
            ]
        if constraints.max_items is not None and len(value) > constraints.max_items:
            return [
                self._validation_issue(
                    entry,
                    scope,
                    code="list_constraint_failed",
                    message=f"{entry.key} has more items than allowed.",
                    boundary=boundary,
                    rule="max_items",
                    details={"max_items": constraints.max_items},
                )
            ]
        return []

    def _object_constraint_issues(
        self,
        entry: SettingRegistryEntry,
        value: dict[str, Any],
        scope: SettingScope,
        boundary: SettingValidationBoundary,
    ) -> list[SettingValidationIssue]:
        constraints = entry.constraints
        if constraints is None:
            return []
        keys = set(value)
        required = set(constraints.required_keys or [])
        missing = sorted(required - keys)
        if missing:
            return [
                self._validation_issue(
                    entry,
                    scope,
                    code="object_constraint_failed",
                    message=f"{entry.key} is missing required fields.",
                    boundary=boundary,
                    rule="required_keys",
                    details={"missing_keys": missing},
                )
            ]
        if constraints.allowed_keys is not None:
            extra = sorted(keys - set(constraints.allowed_keys))
            if extra:
                return [
                    self._validation_issue(
                        entry,
                        scope,
                        code="object_constraint_failed",
                        message=f"{entry.key} contains unsupported fields.",
                        boundary=boundary,
                        rule="allowed_keys",
                        details={"unsupported_keys": extra},
                    )
                ]
        unsafe_nested = self._unsafe_object_paths(value)
        if unsafe_nested:
            return [
                self._validation_issue(
                    entry,
                    scope,
                    code="object_constraint_failed",
                    message=f"{entry.key} contains unsafe object fields.",
                    boundary=boundary,
                    rule="unsafe_object_fields",
                    details={"unsafe_paths": unsafe_nested},
                )
            ]
        return []

    def _unsafe_object_paths(
        self,
        value: dict[str, Any],
        *,
        prefix: str = "",
    ) -> list[str]:
        unsafe: list[str] = []
        for key, nested in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            normalized = str(key).lower()
            if any(token in normalized for token in _UNSAFE_FIELD_TOKENS):
                unsafe.append(path)
                continue
            if normalized in {"command", "commands", "script", "template", "shell"}:
                unsafe.append(path)
                continue
            if isinstance(nested, dict):
                unsafe.extend(self._unsafe_object_paths(nested, prefix=path))
            elif isinstance(nested, list):
                for index, item in enumerate(nested):
                    if isinstance(item, dict):
                        unsafe.extend(
                            self._unsafe_object_paths(item, prefix=f"{path}.{index}")
                        )
                    elif isinstance(item, str) and self._contains_unsafe_payload(item):
                        unsafe.append(f"{path}.{index}")
            elif isinstance(nested, str) and self._contains_unsafe_payload(nested):
                unsafe.append(path)
        return unsafe

    def _dependency_issues_for_values(
        self,
        entry: SettingRegistryEntry,
        values: dict[str, Any],
        *,
        scope: SettingScope,
        boundary: SettingValidationBoundary,
    ) -> list[SettingValidationIssue]:
        issues: list[SettingValidationIssue] = []
        for dependency in entry.depends_on:
            dependency_entry = self._entries_by_key.get(dependency.key)
            if dependency_entry is None:
                issues.append(
                    self._validation_issue(
                        entry,
                        scope,
                        code="dependency_not_satisfied",
                        message=f"{entry.key} depends on an unavailable setting.",
                        boundary=boundary,
                        rule="dependency",
                        details={"dependency_key": dependency.key},
                    )
                )
                continue
            if scope not in dependency_entry.scopes:
                continue
            actual = values.get(dependency.key)
            required = dependency.required_value
            satisfied = bool(actual) if required is None else actual == required
            if not satisfied:
                details: dict[str, Any] = {"dependency_key": dependency.key}
                if required is not None and not self._contains_unsafe_payload(required):
                    details["required_value"] = required
                if dependency.reason:
                    details["reason"] = dependency.reason
                issues.append(
                    self._validation_issue(
                        entry,
                        scope,
                        code="dependency_not_satisfied",
                        message=f"{entry.key} dependency is not satisfied.",
                        boundary=boundary,
                        rule="dependency",
                        details=details,
                    )
                )
        return issues

    def _policy_issues_for_changes(
        self,
        changes: dict[str, Any],
        *,
        scope: SettingScope,
        boundary: SettingValidationBoundary,
    ) -> list[SettingValidationIssue]:
        issues: list[SettingValidationIssue] = []
        policy = self._workspace_policy
        runtime = changes.get("workflow.default_task_runtime")
        if (
            isinstance(runtime, str)
            and policy.allowed_runtimes is not None
            and runtime not in policy.allowed_runtimes
        ):
            issues.append(
                self._validation_issue(
                    self._entries_by_key["workflow.default_task_runtime"],
                    scope,
                    code="runtime_policy_denied",
                    message="workflow.default_task_runtime is not allowed by workspace policy.",
                    boundary=boundary,
                    rule="allowed_runtimes",
                    details={"allowed": sorted(policy.allowed_runtimes)},
                )
            )
        publish_mode = changes.get("workflow.default_publish_mode")
        if (
            isinstance(publish_mode, str)
            and policy.allowed_publication_modes is not None
            and publish_mode not in policy.allowed_publication_modes
        ):
            issues.append(
                self._validation_issue(
                    self._entries_by_key["workflow.default_publish_mode"],
                    scope,
                    code="publication_mode_policy_denied",
                    message="workflow.default_publish_mode is not allowed by workspace policy.",
                    boundary=boundary,
                    rule="allowed_publication_modes",
                    details={"allowed": sorted(policy.allowed_publication_modes)},
                )
            )
        canary = changes.get("skills.canary_percent")
        if isinstance(canary, int) and not isinstance(canary, bool):
            if not policy.skills_canary_enabled and canary > 0:
                issues.append(
                    self._validation_issue(
                        self._entries_by_key["skills.canary_percent"],
                        scope,
                        code="feature_disabled_canary_percent",
                        message="skills.canary_percent must be zero while skills canary is disabled.",
                        boundary=boundary,
                        rule="feature_enabled",
                    )
                )
            if canary > policy.max_canary_percent:
                issues.append(
                    self._validation_issue(
                        self._entries_by_key["skills.canary_percent"],
                        scope,
                        code="max_canary_percent_exceeded",
                        message=(
                            "skills.canary_percent exceeds the workspace policy "
                            "maximum."
                        ),
                        boundary=boundary,
                        rule="max_canary_percent",
                        details={"maximum": policy.max_canary_percent},
                    )
                )
        for key, value in changes.items():
            entry = self._entries_by_key.get(key)
            if (
                entry is None
                or entry.value_type != "secret_ref"
                or not isinstance(value, str)
                or "://" not in value
            ):
                continue
            scheme = value.split("://", 1)[0]
            if scheme in policy.allowed_secret_ref_backends:
                continue
            issues.append(
                self._validation_issue(
                    entry,
                    scope,
                    code="secret_ref_backend_policy_denied",
                    message=f"{key} uses a SecretRef backend denied by workspace policy.",
                    boundary=boundary,
                    rule="allowed_secret_ref_backends",
                    details={"ref_scheme": scheme},
                )
            )
        provider_profile = changes.get("workflow.default_provider_profile_ref")
        if (
            isinstance(provider_profile, str)
            and provider_profile.strip()
            and policy.allowed_provider_profile_ids is not None
            and provider_profile not in policy.allowed_provider_profile_ids
        ):
            issues.append(
                self._validation_issue(
                    self._entries_by_key["workflow.default_provider_profile_ref"],
                    scope,
                    code="provider_policy_denied",
                    message="workflow.default_provider_profile_ref is not allowed by workspace policy.",
                    boundary=boundary,
                    rule="allowed_provider_profiles",
                )
            )
        operation_mode = changes.get("workflow.operation_mode")
        if (
            policy.maintenance_mode
            and isinstance(operation_mode, str)
            and operation_mode not in policy.allowed_operation_modes_during_maintenance
        ):
            issues.append(
                self._validation_issue(
                    self._entries_by_key["workflow.operation_mode"],
                    scope,
                    code="maintenance_mode_conflict",
                    message="workflow.operation_mode is not allowed during maintenance mode.",
                    boundary=boundary,
                    rule="maintenance_mode",
                )
            )
        return issues

    def _reference_validation_issues(
        self,
        entry: SettingRegistryEntry,
        value: Any,
        *,
        scope: SettingScope,
        boundary: SettingValidationBoundary,
    ) -> list[SettingValidationIssue]:
        if value is None:
            return []
        if entry.value_type == "secret_ref" and isinstance(value, str):
            if value.startswith("env://") and boundary in {
                "write_request",
                "pre_persistence",
            }:
                return []
            diagnostic = self._secret_ref_diagnostic(entry, value)
            if diagnostic is None:
                return []
            details = {
                key: item
                for key, item in diagnostic.details.items()
                if key
                in {
                    "ref_scheme",
                    "status",
                    "launch_blocker",
                    "operation_blocker",
                    "blocks",
                }
            }
            return [
                self._validation_issue(
                    entry,
                    scope,
                    code=(
                        "secret_ref_unresolved"
                        if diagnostic.code == "unresolved_secret_ref"
                        else diagnostic.code
                    ),
                    message=diagnostic.message,
                    boundary=boundary,
                    rule="referenced_resource",
                    details=details,
                )
            ]
        if entry.key == "workflow.default_provider_profile_ref" and isinstance(value, str):
            diagnostic = self._provider_profile_ref_diagnostic(entry, value)
            if diagnostic is None:
                return []
            details = {
                key: item
                for key, item in diagnostic.details.items()
                if key
                in {
                    "profile_id",
                    "launch_blocker",
                    "operation_blocker",
                    "blocks",
                }
            }
            return [
                self._validation_issue(
                    entry,
                    scope,
                    code=diagnostic.code,
                    message=diagnostic.message,
                    boundary=boundary,
                    rule="referenced_resource",
                    details=details,
                )
            ]
        return []

    def _override_value_size(self, value: Any) -> int:
        return len(
            json.dumps(
                value,
                default=str,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        )

    def _contains_unsafe_payload(
        self,
        value: Any,
        *,
        allow_profile_ref_string_tokens: bool = False,
    ) -> bool:
        if isinstance(value, str):
            normalized = value.lower()
            if allow_profile_ref_string_tokens:
                return any(prefix in value for prefix in _SECRET_PREFIXES) or bool(
                    _UNSAFE_PROFILE_REF_ASSIGNMENT_RE.search(normalized)
                )
            return any(prefix in value for prefix in _SECRET_PREFIXES) or any(
                token in normalized for token in _UNSAFE_STRING_TOKENS
            )
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
        request_id: str | None,
    ) -> SettingsAuditEvent:
        redacted = entry.audit.redact
        return SettingsAuditEvent(
            event_type=event_type,
            key=entry.key,
            scope=scope,
            workspace_id=self._workspace_id,
            user_id=self._user_id if scope == "user" else _DEFAULT_SUBJECT_ID,
            actor_user_id=(
                self._user_id if self._user_id != _DEFAULT_SUBJECT_ID else None
            ),
            old_value_json=(
                None
                if (redacted and entry.value_type != "secret_ref")
                or not entry.audit.store_old_value
                else old_value
            ),
            new_value_json=(
                None
                if (redacted and entry.value_type != "secret_ref")
                or not entry.audit.store_new_value
                else new_value
            ),
            redacted=redacted,
            reason=reason,
            request_id=request_id,
        )

    def _rejected_audit_event(
        self,
        entry: SettingRegistryEntry | None,
        *,
        key: str,
        scope: SettingScope,
        new_value: Any,
        reason: str | None,
        request_id: str | None,
    ) -> SettingsAuditEvent:
        descriptor_redacted = entry.audit.redact if entry is not None else False
        contains_unsafe_payload = self._contains_unsafe_payload(new_value)
        unsafe_value = self._contains_secret_like_value(
            new_value,
        ) or contains_unsafe_payload
        redacted = descriptor_redacted or unsafe_value or entry is None
        may_store_secret_ref_metadata = (
            entry is not None
            and entry.value_type == "secret_ref"
            and descriptor_redacted
            and isinstance(new_value, str)
            and not contains_unsafe_payload
        )
        return SettingsAuditEvent(
            event_type="settings.override.rejected",
            key=key,
            scope=scope,
            workspace_id=self._workspace_id,
            user_id=self._user_id if scope == "user" else _DEFAULT_SUBJECT_ID,
            actor_user_id=(
                self._user_id if self._user_id != _DEFAULT_SUBJECT_ID else None
            ),
            old_value_json=None,
            new_value_json=(
                new_value
                if (not redacted or may_store_secret_ref_metadata)
                else None
            ),
            redacted=redacted,
            reason=reason,
            request_id=request_id,
        )

    def _diagnostics(
        self,
        entry: SettingRegistryEntry,
        value: Any,
    ) -> list[SettingDiagnostic]:
        diagnostics: list[SettingDiagnostic] = []
        if value is None:
            if (
                entry.default_value is None
                and entry.settings_path is None
                and not entry.env_aliases
            ):
                diagnostics.append(
                    SettingDiagnostic(
                        code="no_default",
                        message=f"{entry.key} has no configured value and no catalog default.",
                        severity="warning",
                    )
                )
                return diagnostics
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
        if entry.key == "workflow.default_provider_profile_ref" and isinstance(value, str):
            diagnostic = self._provider_profile_ref_diagnostic(entry, value)
            if diagnostic is not None:
                diagnostics.append(diagnostic)
        return diagnostics

    def _provider_profile_ref_diagnostic(
        self, entry: SettingRegistryEntry, value: str
    ) -> SettingDiagnostic | None:
        profile_id = value.strip()
        if not profile_id:
            return None
        if profile_id not in self._provider_profile_enabled_by_id:
            return None
        enabled = self._provider_profile_enabled_by_id.get(profile_id)
        if enabled is None:
            return SettingDiagnostic(
                code="provider_profile_not_found",
                message=(
                    f"{entry.key} references a provider profile that does not exist."
                ),
                severity="error",
                details={
                    "profile_id": profile_id,
                    "launch_blocker": True,
                    "blocks": ["launch", "readiness"],
                },
            )
        if enabled is False:
            return SettingDiagnostic(
                code="provider_profile_disabled",
                message=(
                    f"{entry.key} references a disabled provider profile."
                ),
                severity="error",
                details={
                    "profile_id": profile_id,
                    "launch_blocker": True,
                    "blocks": ["launch", "readiness"],
                },
            )
        metadata = self._provider_profile_metadata_by_id.get(profile_id, {})
        credential_source = metadata.get("credential_source")
        credential_source_value = (
            credential_source.value
            if isinstance(credential_source, ProviderCredentialSource)
            else str(credential_source or "")
        )
        if (
            credential_source_value == ProviderCredentialSource.OAUTH_VOLUME.value
            and not str(metadata.get("volume_ref") or "").strip()
        ):
            return SettingDiagnostic(
                code="provider_profile_oauth_volume_missing",
                message=(
                    f"{entry.key} references an OAuth-backed provider profile "
                    "without an available OAuth volume."
                ),
                severity="error",
                details={
                    "profile_id": profile_id,
                    "credential_source": credential_source_value,
                    "launch_blocker": True,
                    "blocks": ["launch", "readiness"],
                },
            )
        return None

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
                    details={
                        "ref_scheme": "env",
                        "launch_blocker": True,
                        "blocks": ["launch", "readiness"],
                    },
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
                    details={
                        "ref_scheme": "db",
                        "status": "missing",
                        "launch_blocker": True,
                        "blocks": ["launch", "readiness"],
                    },
                )
            if status != SecretStatus.ACTIVE.value:
                return SettingDiagnostic(
                    code="unresolved_secret_ref",
                    message=(
                        f"{entry.key} references a managed secret that is "
                        f"{status}."
                    ),
                    severity="error",
                    details={
                        "ref_scheme": "db",
                        "status": status,
                        "launch_blocker": True,
                        "blocks": ["launch", "readiness"],
                    },
                )
        elif "://" not in value:
            return SettingDiagnostic(
                code="invalid_secret_ref",
                message=f"{entry.key} is not a valid SecretRef.",
                severity="error",
            )
        return None

    async def _recent_changes(
        self, keys: list[str]
    ) -> dict[str, SettingsRecentChange]:
        if self._session is None or not keys:
            return {}
        result = await self._session.execute(
            select(SettingsAuditEvent)
            .where(
                SettingsAuditEvent.workspace_id == self._workspace_id,
                SettingsAuditEvent.key.in_(keys),
                SettingsAuditEvent.event_type != "settings.override.rejected",
                SettingsAuditEvent.user_id.in_(
                    {_DEFAULT_SUBJECT_ID, self._user_id}
                ),
            )
            .order_by(desc(SettingsAuditEvent.created_at))
        )
        output: dict[str, SettingsRecentChange] = {}
        for row in result.scalars().all():
            if row.key in output:
                continue
            output[row.key] = SettingsRecentChange(
                event_type=row.event_type,
                reason=row.reason,
                redacted=row.redacted,
                created_at=row.created_at,
            )
        return output

    def _audit_read_model(
        self,
        row: SettingsAuditEvent,
        *,
        permissions: set[str],
    ) -> SettingsAuditRead:
        entry = self._entries_by_key.get(row.key)
        affected_systems = list(entry.applies_to) if entry is not None else []
        old_value, old_reasons = self._visible_audit_value(
            row.old_value_json,
            entry=entry,
            row_redacted=row.redacted
            and (row.old_value_json is not None or row.new_value_json is None),
            permissions=permissions,
        )
        new_value, new_reasons = self._visible_audit_value(
            row.new_value_json,
            entry=entry,
            row_redacted=row.redacted,
            permissions=permissions,
        )
        reasons = sorted(set(old_reasons + new_reasons))
        redacted = bool(reasons)
        return SettingsAuditRead(
            id=row.id,
            event_type=row.event_type,
            key=row.key,
            scope=row.scope,
            source=(
                f"{row.scope}_override"
                if row.event_type != "settings.override.reset"
                else "inherited"
            ),
            actor_user_id=row.actor_user_id,
            old_value=old_value,
            new_value=new_value,
            redacted=redacted,
            redaction_reasons=reasons,
            reason=row.reason,
            request_id=row.request_id,
            validation_outcome=(
                "rejected"
                if row.event_type == "settings.override.rejected"
                else "accepted"
            ),
            apply_mode=entry.apply_mode if entry is not None else None,
            affected_systems=affected_systems,
            created_at=row.created_at,
        )

    def _visible_audit_value(
        self,
        value: Any,
        *,
        entry: SettingRegistryEntry | None,
        row_redacted: bool,
        permissions: set[str],
    ) -> tuple[Any, list[str]]:
        reasons: list[str] = []
        if value is None:
            if row_redacted:
                reasons.append("stored_redacted")
            return None, reasons
        if entry is not None and entry.audit.redact:
            if (
                entry.value_type == "secret_ref"
                and "secrets.metadata.read" in permissions
                and isinstance(value, str)
            ):
                return value, reasons
            reasons.append("descriptor_policy")
        if self._contains_secret_like_value(value):
            return None, sorted(set(reasons + ["secret_like_value"]))
        if row_redacted and not reasons:
            reasons.append("stored_redacted")
        if reasons:
            return None, reasons
        return value, reasons

    def _contains_secret_like_value(self, value: Any) -> bool:
        if isinstance(value, str):
            normalized = value.lower()
            return any(prefix in value for prefix in _SECRET_PREFIXES) or any(
                marker in normalized for marker in _SECRET_LIKE_SUBSTRINGS
            )
        if isinstance(value, dict):
            return any(
                self._contains_secret_like_value(key)
                or self._contains_secret_like_value(nested)
                for key, nested in value.items()
            )
        if isinstance(value, list):
            return any(self._contains_secret_like_value(item) for item in value)
        return False


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
