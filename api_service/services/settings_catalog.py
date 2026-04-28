"""Read-side settings catalog and effective-value resolution."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field

from moonmind.config.settings import AppSettings, settings as app_settings

SettingScope = Literal["user", "workspace", "system", "operator"]
SettingSection = Literal["providers-secrets", "user-workspace", "operations"]


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
    read_only: bool = True
    read_only_reason: str | None = (
        "Scoped override persistence is not enabled for this story."
    )
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
    ) -> None:
        self._settings = settings or app_settings
        self._env = env if env is not None else os.environ
        self._registry = registry
        self._entries_by_key = {entry.key: entry for entry in registry}
        self._redacted_invalid_secret_refs: set[str] = set()

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

    def _descriptor(self, entry: SettingRegistryEntry) -> SettingDescriptor:
        value, source = self._resolve_value(entry)
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
            diagnostics=self._diagnostics(entry, value),
        )

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
        return f"Resolved from {source}."

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
