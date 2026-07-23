"""Service helpers for listing and expanding presets."""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import socket
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import UUID, uuid4

import yaml
from jinja2 import StrictUndefined, TemplateError, UndefinedError
from jinja2.sandbox import SandboxedEnvironment
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from api_service.db.models import (
    Preset,
    PresetFavorite,
    PresetRecent,
    PresetReleaseStatus,
    PresetScopeType,
)
from moonmind.capabilities.input_contracts import (
    CapabilityInputOwner,
    capability_contract_from_legacy_inputs,
    normalize_capability_input_contract,
)
from moonmind.config.settings import settings
from moonmind.workflows.checkpoint_branches import (
    CheckpointBranchGitBindingError,
    _validate_work_branch,
)
from moonmind.runtime_intent import (
    RuntimeIntentValidationError,
    validate_runtime_tier_intent,
)
from moonmind.workflows.temporal.remediation_loop import RemediationLoopSpec

_FORBIDDEN_STEP_KEYS = frozenset(
    {
        "bash",
        "runtime",
        "targetRuntime",
        "target_runtime",
        "model",
        "effort",
        "repository",
        "repo",
        "git",
        "publish",
        "container",
        "command",
        "cmd",
        "script",
        "shell",
    }
)
_SUPPORTED_INPUT_TYPES = frozenset(
    {
        "text",
        "textarea",
        "markdown",
        "enum",
        "boolean",
        "user",
        "team",
        "repo_path",
        "jira_board",
    }
)
_JIRA_BREAKDOWN_SLUG = "jira-breakdown"
_JIRA_BREAKDOWN_ORCHESTRATE_SLUG = "jira-breakdown-orchestrate"
_JIRA_BREAKDOWN_IMPLEMENT_SLUG = "jira-breakdown-implement"
_JIRA_BREAKDOWN_COMPOSITE_SLUGS = frozenset(
    {_JIRA_BREAKDOWN_ORCHESTRATE_SLUG, _JIRA_BREAKDOWN_IMPLEMENT_SLUG}
)
_GITHUB_ISSUE_BREAKDOWN_SLUGS = frozenset(
    {
        "github-issue-breakdown-implement",
        "github-issue-breakdown-orchestrate",
    }
)
_JIRA_BREAKDOWN_PROJECT_DEFAULT_SLUGS = frozenset(
    {
        _JIRA_BREAKDOWN_SLUG,
        _JIRA_BREAKDOWN_ORCHESTRATE_SLUG,
        _JIRA_BREAKDOWN_IMPLEMENT_SLUG,
    }
)
_JIRA_BREAKDOWN_PROJECT_INPUT = "jira_project_key"
_JIRA_BREAKDOWN_REPLACEABLE_PROJECT_DEFAULTS = frozenset({"TOOL", "MM"})
_JIRA_BREAKDOWN_SOURCE_INPUTS = (
    "source_design_path",
    "source_issue_key",
    "feature_request",
)
_GITHUB_ISSUE_BREAKDOWN_SOURCE_INPUTS = (
    "source_design_path",
    "feature_request",
)
_SLUG_PATTERN = re.compile(r"[^a-z0-9-]+")
_UNRESOLVED_PLACEHOLDER_PATTERN = re.compile(r"{{\s*[^}]+\s*}}")
_NATIVE_SCALAR_TEMPLATE_PATTERN = re.compile(r"^\{\{.*\}\}$", re.DOTALL)
_SECRET_LIKE_KEY_PATTERN = re.compile(
    r"(authorization|cookie|password|secret|token|api[_-]?key|access[_-]?key)",
    re.IGNORECASE,
)
_SECRET_LIKE_VALUE_PATTERN = re.compile(
    r"(token=|password=|bearer\s+|ghp_|github_pat_|akia[0-9a-z]{16}|aiza|atatt|-----begin [a-z ]*private key)",
    re.IGNORECASE,
)
_STEP_RESERVED_KEYS = frozenset(
    {
        "id",
        "kind",
        "source",
        "type",
        "title",
        "slug",
        "version",
        "alias",
        "scope",
        "inputMapping",
        "input_mapping",
        "originalStepId",
        "instructions",
        "tool",
        "skill",
        "skills",
        "preset",
        "annotations",
    }
)
_INCLUDE_STEP_KEYS = frozenset(
    {
        "kind",
        "slug",
        "alias",
        "scope",
        "inputMapping",
        "input_mapping",
        "annotations",
    }
)
_STEP_KIND = "step"
_INCLUDE_KIND = "include"
_STEP_TYPE_TOOL = "tool"
_STEP_TYPE_SKILL = "skill"
_STEP_TYPE_PRESET = "preset"
_RUNTIME_ONLY_ORCHESTRATE_SLUGS = {"jira-orchestrate", "moonspec-orchestrate"}
_ORCHESTRATION_MODE_INPUT = "orchestration_mode"
_BATCH_WORKFLOWS_SLUG = "batch-workflows"
_BATCH_GITHUB_WORKFLOWS_SLUG = "batch-github-workflows"
_REMOVED_BATCH_WORKFLOWS_INPUTS = frozenset({"target_preset_version"})
_SKILL_METADATA_KEYS = frozenset(
    {"context", "permissions", "autonomy", "runtime", "allowedTools"}
)
_TOOL_METADATA_KEYS = frozenset(
    {
        "requiredAuthorization",
        "requiredCapabilities",
        "sideEffectPolicy",
        "retryPolicy",
        "execution",
        "validation",
    }
)
_COMMAND_TOOL_MARKERS = ("command", "shell", "bash")
_CHECKPOINT_BRANCHING_SUPPORTED_TRIGGERS = frozenset(
    {
        "gate_additional_work_needed",
        "failed_step",
        "operator_requested",
    }
)
_CHECKPOINT_BRANCHING_WORKSPACE_POLICIES = frozenset(
    {
        "restore_pre_execution",
        "apply_previous_execution_diff_to_clean_baseline",
        "start_from_last_passed_commit",
        "fresh_branch_from_source",
    }
)
_CHECKPOINT_BRANCHING_RUNTIME_CONTEXT_POLICIES = frozenset(
    {
        "fresh_agent_run",
        "reuse_session_new_epoch",
        "reuse_session_same_epoch",
    }
)
_CHECKPOINT_BRANCHING_SIDE_EFFECT_POLICIES = frozenset(
    {
        "isolated",
        "none",
    }
)
logger = logging.getLogger(__name__)

class _StatsdEmitter:
    """Best-effort StatsD counter emitter for template catalog activity."""

    def __init__(self) -> None:
        host = (
            os.getenv("TASK_TEMPLATE_METRICS_HOST")
            or os.getenv("WORKFLOW_METRICS_HOST")
            or os.getenv("STATSD_HOST")
            or ""
        ).strip()
        port_raw = (
            os.getenv("TASK_TEMPLATE_METRICS_PORT")
            or os.getenv("WORKFLOW_METRICS_PORT")
            or os.getenv("STATSD_PORT")
            or "8125"
        ).strip()
        prefix = (
            os.getenv("PRESET_METRICS_PREFIX") or "moonmind.presets"
        ).strip()
        self._prefix = prefix.rstrip(".")
        self._address: tuple[str, int] | None = None
        self._socket: socket.socket | None = None
        self._lock = threading.Lock()
        self._enabled = False
        self._disabled_until: float | None = None
        self._backoff_seconds = 5.0
        if host:
            try:
                self._address = (host, int(port_raw))
                self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                self._enabled = True
            except Exception:
                logger.warning(
                    "Preset metrics emitter disabled due to init failure."
                )
                self._enabled = False

    @property
    def enabled(self) -> bool:
        if not self._enabled or self._socket is None or self._address is None:
            return False
        if self._disabled_until is not None and time.monotonic() < self._disabled_until:
            return False
        return True

    def increment(self, metric: str, value: int = 1) -> None:
        if not self.enabled:
            return
        payload = f"{self._prefix}.{metric}:{int(value)}|c".encode("utf-8")
        with self._lock:
            if not self.enabled:
                return
            try:
                assert self._socket is not None and self._address is not None
                self._socket.sendto(payload, self._address)
                self._backoff_seconds = 5.0
            except OSError:
                self._disabled_until = time.monotonic() + self._backoff_seconds
                self._backoff_seconds = min(self._backoff_seconds * 2, 60.0)

_METRICS = _StatsdEmitter()

class PresetError(RuntimeError):
    """Base error for preset catalog operations."""

class PresetNotFoundError(PresetError):
    """Raised when a template or version is missing."""

class PresetValidationError(PresetError):
    """Raised when template payloads fail validation."""

    def __init__(
        self, message: str, *, errors: list[dict[str, Any]] | None = None
    ) -> None:
        super().__init__(message)
        self.errors = errors or []

class PresetConflictError(PresetError):
    """Raised when uniqueness constraints are violated."""

@dataclass(slots=True)
class ExpandOptions:
    """Options provided when expanding template steps."""

    should_enforce_step_limit: bool = True

def _normalize_slug(value: str) -> str:
    normalized = _SLUG_PATTERN.sub("-", str(value or "").strip().lower()).strip("-")
    if not normalized:
        raise PresetValidationError("Template slug is required.")
    if len(normalized) > 128:
        raise PresetValidationError("Template slug exceeds max length (128).")
    return normalized

def _normalize_scope(scope: str) -> PresetScopeType:
    raw = str(scope or "").strip().lower()
    if raw not in {
        PresetScopeType.GLOBAL.value,
        PresetScopeType.PERSONAL.value,
    }:
        raise PresetValidationError(
            "scope must be one of: global, personal"
        )
    return PresetScopeType(raw)

def _normalize_scope_ref(
    scope: PresetScopeType, scope_ref: str | None
) -> str | None:
    cleaned = str(scope_ref or "").strip() or None
    if scope is PresetScopeType.GLOBAL:
        return None
    if cleaned is None:
        raise PresetValidationError(
            "scopeRef is required for personal scopes."
        )
    return cleaned

def _normalize_tag_list(values: list[Any] | None) -> list[str]:
    if values is None:
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in values:
        tag = str(raw or "").strip().lower()
        if not tag or tag in seen:
            continue
        seen.add(tag)
        normalized.append(tag)
    return normalized

def _normalize_capabilities(values: list[Any] | None) -> list[str]:
    if values is None:
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in values:
        capability = str(raw or "").strip().lower()
        if not capability or capability in seen:
            continue
        seen.add(capability)
        normalized.append(capability)
    return normalized

def _extract_step_capabilities(step: dict[str, Any]) -> list[str]:
    skill = step.get("skill")
    if not isinstance(skill, dict):
        return []
    caps = skill.get("requiredCapabilities")
    if not isinstance(caps, list):
        return []
    return _normalize_capabilities(caps)

def _normalize_step_type(raw_step: Mapping[str, Any], *, index: int) -> str:
    raw_type = raw_step.get("type")
    if raw_type is None or str(raw_type).strip() == "":
        return _STEP_TYPE_SKILL
    step_type = str(raw_type).strip().lower()
    if step_type not in {_STEP_TYPE_TOOL, _STEP_TYPE_SKILL, _STEP_TYPE_PRESET}:
        raise PresetValidationError(
            f"Step {index} type must be one of: tool, skill, preset."
        )
    return step_type


def _step_validation_error(
    *,
    index: int,
    path: str,
    message: str,
    code: str = "invalid",
    recoverable: bool = True,
) -> PresetValidationError:
    return PresetValidationError(
        message,
        errors=[
            {
                "path": f"steps[{index - 1}].{path}",
                "message": message,
                "code": code,
                "recoverable": recoverable,
            }
        ],
    )


def _include_tree_error(
    *,
    message: str,
    code: str,
    include_path: list[str],
    recoverable: bool = True,
) -> PresetValidationError:
    return PresetValidationError(
        message,
        errors=[
            {
                "path": "preset.includeTree",
                "message": message,
                "code": code,
                "includePath": list(include_path),
                "recoverable": recoverable,
            }
        ],
    )


def _source_original_step_id(rendered: Mapping[str, Any]) -> str | None:
    explicit = str(rendered.get("originalStepId") or "").strip()
    if explicit:
        return explicit
    step_id = str(rendered.get("id") or "").strip()
    return step_id or None


def _has_substantive_tool_metadata(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, Mapping | list | tuple | set):
        return bool(value)
    return True

def _normalize_tool_payload(raw_tool: Any, *, index: int) -> dict[str, Any]:
    if not isinstance(raw_tool, Mapping):
        raise PresetValidationError(
            f"Step {index} Tool step requires a tool payload object."
        )
    raw_tool = dict(raw_tool)
    raw_tool.pop("version", None)
    raw_tool.pop("toolVersion", None)
    tool_id = str(raw_tool.get("id") or raw_tool.get("name") or "").strip()
    if not tool_id:
        raise PresetValidationError(
            f"Step {index} tool.id or tool.name is required."
        )
    inputs = raw_tool.get("inputs")
    args = raw_tool.get("args")
    if inputs is None or (
        isinstance(inputs, Mapping) and not inputs and args is not None
    ):
        inputs = raw_tool.get("args")
    if inputs is None:
        inputs = {}
    if not isinstance(inputs, Mapping):
        raise PresetValidationError(
            f"Step {index} tool.inputs must be an object when provided."
        )
    tool_payload: dict[str, Any] = {"id": tool_id, "inputs": dict(inputs)}
    caps = raw_tool.get("requiredCapabilities")
    if caps is not None:
        if not isinstance(caps, list):
            raise PresetValidationError(
                f"Step {index} tool.requiredCapabilities must be a list."
            )
        normalized_caps = _normalize_capabilities(caps)
        if normalized_caps:
            tool_payload["requiredCapabilities"] = normalized_caps
    for key in _TOOL_METADATA_KEYS - {"requiredCapabilities"}:
        value = raw_tool.get(key)
        if value is not None:
            tool_payload[key] = value
    if any(marker in tool_id.lower() for marker in _COMMAND_TOOL_MARKERS):
        has_policy_metadata = any(
            _has_substantive_tool_metadata(tool_payload.get(key))
            for key in ("sideEffectPolicy", "validation")
        )
        if not tool_payload["inputs"] or not has_policy_metadata:
            raise PresetValidationError(
                f"Step {index} command-like Tool steps require bounded inputs and policy metadata."
            )
    return tool_payload

def _normalize_skill_payload(raw_skill: Any, *, index: int) -> dict[str, Any]:
    if raw_skill is None:
        return {"id": "auto", "args": {}}
    if not isinstance(raw_skill, Mapping):
        raise PresetValidationError(
            f"Step {index} skill must be an object when provided."
        )
    skill_id = str(raw_skill.get("id") or "auto").strip() or "auto"
    skill_args = raw_skill.get("args")
    if skill_args is None:
        skill_args = {}
    if not isinstance(skill_args, Mapping):
        raise PresetValidationError(
            f"Step {index} skill.args must be an object when provided."
        )
    skill_payload: dict[str, Any] = {"id": skill_id, "args": dict(skill_args)}
    caps = raw_skill.get("requiredCapabilities")
    if caps is not None:
        if not isinstance(caps, list):
            raise PresetValidationError(
                f"Step {index} skill.requiredCapabilities must be a list."
            )
        normalized_caps = _normalize_capabilities(caps)
        if normalized_caps:
            skill_payload["requiredCapabilities"] = normalized_caps
    for key in _SKILL_METADATA_KEYS:
        value = raw_skill.get(key)
        if value is not None:
            if not isinstance(value, Mapping) and key in {
                "context",
                "permissions",
                "autonomy",
                "runtime",
            }:
                raise PresetValidationError(
                    f"Step {index} skill.{key} must be an object when provided."
                )
            if key == "runtime":
                try:
                    skill_payload[key] = validate_runtime_tier_intent(
                        value,
                        field_name=f"steps[{index - 1}].skill.runtime",
                    )
                except RuntimeIntentValidationError as exc:
                    raise PresetValidationError(str(exc)) from exc
            else:
                skill_payload[key] = dict(value)
    return skill_payload


def _promote_skill_runtime(step_payload: dict[str, Any]) -> None:
    skill_payload = step_payload.get("skill")
    if not isinstance(skill_payload, Mapping):
        return
    skill_runtime = skill_payload.get("runtime")
    if not isinstance(skill_runtime, Mapping):
        return
    step_runtime = (
        dict(step_payload.get("runtime"))
        if isinstance(step_payload.get("runtime"), Mapping)
        else {}
    )
    for key, value in skill_runtime.items():
        step_runtime.setdefault(key, value)
    if step_runtime:
        step_payload["runtime"] = step_runtime


def _normalize_preset_payload(raw_preset: Any, *, index: int) -> dict[str, Any]:
    if not isinstance(raw_preset, Mapping):
        raise _step_validation_error(
            index=index,
            path="preset",
            message="Preset steps require a preset payload object.",
            code="required",
        )
    raw_preset = dict(raw_preset)
    raw_preset.pop("version", None)
    raw_preset.pop("presetVersion", None)
    preset_slug = str(raw_preset.get("slug") or raw_preset.get("id") or "").strip()
    if not preset_slug:
        raise _step_validation_error(
            index=index,
            path="preset.slug",
            message="Preset steps require preset.slug or preset.id.",
            code="required",
        )
    inputs = raw_preset.get("inputs", raw_preset.get("inputMapping", {}))
    if inputs is None:
        inputs = {}
    if not isinstance(inputs, Mapping):
        raise _step_validation_error(
            index=index,
            path="preset.inputs",
            message="Preset step inputs must be an object when provided.",
        )
    preset_payload: dict[str, Any] = {
        "slug": _normalize_slug(preset_slug),
        "inputs": dict(inputs),
    }
    scope = raw_preset.get("scope")
    if scope is not None:
        preset_payload["scope"] = _normalize_scope(str(scope)).value
    return preset_payload


def _preset_step_to_include(
    raw_step: Mapping[str, Any], *, index: int
) -> dict[str, Any]:
    preset = _normalize_preset_payload(raw_step.get("preset"), index=index)
    alias_source = (
        raw_step.get("alias")
        or raw_step.get("id")
        or raw_step.get("title")
        or preset["slug"]
    )
    include_payload: dict[str, Any] = {
        "kind": _INCLUDE_KIND,
        "slug": preset["slug"],
        "alias": _normalize_slug(str(alias_source)),
        "inputMapping": dict(preset["inputs"]),
    }
    if "scope" in preset:
        include_payload["scope"] = preset["scope"]
    annotations = raw_step.get("annotations")
    if annotations is not None:
        include_payload["annotations"] = annotations
    return include_payload

def _slugify_from_title(title: str) -> str:
    return _normalize_slug(title)

def _hash_from_inputs(values: dict[str, Any]) -> str:
    normalized = repr(sorted(values.items())).encode("utf-8")
    return hashlib.sha1(normalized).hexdigest()[:8]

def _preset_digest(template: Preset) -> str:
    payload = json.dumps(
        {
            "slug": template.slug,
            "scope": template.scope_type.value,
            "scopeRef": template.scope_ref,
            "inputs": template.inputs_schema or [],
            "steps": template.steps or [],
            "annotations": template.annotations or {},
            "requiredCapabilities": template.required_capabilities or [],
            "maxStepCount": template.max_step_count,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


def _build_step_id(*, slug: str, index: int, inputs: dict[str, Any]) -> str:
    return f"tpl:{slug}:{index:02d}:{_hash_from_inputs(inputs)}"

def _template_path_label(*, slug: str, alias: str | None = None) -> str:
    label = slug
    if alias:
        return f"{alias}:{label}"
    return label

def _format_include_path(path: list[str]) -> str:
    return " -> ".join(path)

def _composition_capabilities(node: dict[str, Any]) -> list[str]:
    capabilities = list(node.get("requiredCapabilities") or [])
    for child in node.get("includes") or []:
        if isinstance(child, dict):
            capabilities.extend(_composition_capabilities(child))
    return _normalize_capabilities(capabilities)

def _authored_presets_from_composition(node: dict[str, Any]) -> list[dict[str, Any]]:
    presets: list[dict[str, Any]] = []

    def has_authored_preset_value(value: Any) -> bool:
        if value is None:
            return False
        if isinstance(value, str):
            return bool(value)
        if isinstance(value, (list, dict, tuple, set)):
            return bool(value)
        return True

    def visit(candidate: dict[str, Any]) -> None:
        entry: dict[str, Any] = {
            "presetSlug": str(candidate.get("slug") or "").strip(),
            "presetDigest": str(candidate.get("digest") or "").strip(),
            "scope": str(candidate.get("scope") or "").strip(),
            "includePath": [
                str(item).strip()
                for item in candidate.get("path") or []
                if str(item).strip()
            ],
        }
        alias = str(candidate.get("alias") or "").strip()
        if alias:
            entry["alias"] = alias
        input_mapping = candidate.get("inputMapping")
        if isinstance(input_mapping, Mapping) and input_mapping:
            entry["inputMapping"] = dict(input_mapping)
        presets.append(
            {
                key: value
                for key, value in entry.items()
                if has_authored_preset_value(value)
            }
        )
        for child in candidate.get("includes") or []:
            if isinstance(child, dict):
                visit(child)

    visit(node)
    return presets

def _render_value(
    env: SandboxedEnvironment,
    value: Any,
    *,
    variables: dict[str, Any],
    key: str | None = None,
) -> Any:
    if isinstance(value, str):
        try:
            rendered = env.from_string(value).render(**variables)
        except UndefinedError as exc:
            raise PresetValidationError(
                f"Template references an unknown variable: {exc}."
            ) from exc
        except TemplateError as exc:
            raise PresetValidationError(
                f"Template rendering failed: {exc}."
            ) from exc
        stripped = rendered.strip()
        if _NATIVE_SCALAR_TEMPLATE_PATTERN.match(value.strip()):
            lowered = stripped.lower()
            if lowered in {"true", "false"}:
                return lowered == "true"
            if key == "moonSpecRemediationMaxAttempts" and re.fullmatch(
                r"[+-]?\d+",
                stripped,
            ):
                return int(stripped)
        return stripped
    if isinstance(value, list):
        return [_render_value(env, item, variables=variables) for item in value]
    if isinstance(value, dict):
        return {
            str(item_key): _render_value(
                env,
                item,
                variables=variables,
                key=str(item_key),
            )
            for item_key, item in value.items()
        }
    return value


def _preset_step_enabled(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    normalized = str(value).strip().lower()
    if normalized in {
        "",
        "0",
        "false",
        "no",
        "off",
        "none",
        "null",
        "nil",
        "undefined",
    }:
        return False
    return True

def _validate_breakdown_source_inputs(
    *,
    slug: str,
    inputs: Mapping[str, Any],
) -> None:
    if slug in _JIRA_BREAKDOWN_PROJECT_DEFAULT_SLUGS:
        source_inputs = _JIRA_BREAKDOWN_SOURCE_INPUTS
        provider_label = "Jira"
        source_message = (
            "Provide a Source Document Path, Source Jira Issue Key, "
            "or Workflow Instructions."
        )
    elif slug in _GITHUB_ISSUE_BREAKDOWN_SLUGS:
        source_inputs = _GITHUB_ISSUE_BREAKDOWN_SOURCE_INPUTS
        provider_label = "GitHub issue"
        source_message = "Provide a Source Document Path or Workflow Instructions."
    else:
        return
    if any(str(inputs.get(name) or "").strip() for name in source_inputs):
        return
    raise PresetValidationError(
        f"{provider_label} breakdown presets require a source input.",
        errors=[
            {
                "path": "preset.inputs",
                "message": source_message,
                "code": "required",
                "recoverable": True,
            }
        ],
    )

def _single_allowed_jira_project_key() -> str | None:
    projects = settings.atlassian.jira.jira_allowed_projects
    if not projects:
        return None
    allowed = [project.strip() for project in projects.split(",") if project.strip()]
    if len(allowed) != 1:
        return None
    return allowed[0]

def _effective_inputs_schema(
    *,
    slug: str,
    inputs_schema: list[dict[str, Any]],
    context: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Apply runtime-derived defaults without mutating stored template versions."""

    if slug not in _JIRA_BREAKDOWN_PROJECT_DEFAULT_SLUGS:
        return inputs_schema

    repository = _repository_from_context(context)
    repository_project_key = _jira_project_default_for_repository(repository)
    project_key = repository_project_key or _single_allowed_jira_project_key()
    repository_default = (
        repository if slug in _JIRA_BREAKDOWN_COMPOSITE_SLUGS else None
    )
    effective_schema = [dict(definition) for definition in inputs_schema]
    default_configured_repository = str(
        settings.workflow.github_repository or ""
    ).strip()
    for definition in effective_schema:
        name = definition.get("name")
        if name == _JIRA_BREAKDOWN_PROJECT_INPUT:
            if project_key:
                definition["default"] = project_key
            elif str(definition.get("default") or "").strip() == "TOOL":
                definition["default"] = None
        elif name == "repository" and repository_default:
            definition["default"] = repository_default
        elif name == "repository" and slug in _JIRA_BREAKDOWN_COMPOSITE_SLUGS:
            default_repository = str(definition.get("default") or "").strip()
            if default_repository and default_repository == default_configured_repository:
                definition["default"] = None
    return effective_schema

def _repository_from_context(context: Mapping[str, Any] | None) -> str | None:
    if not isinstance(context, Mapping):
        return None
    for key in ("repository", "repo"):
        value = context.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _normalize_runtime_orchestration_mode(
    *,
    slug: str,
    submitted: Mapping[str, Any],
    resolved: dict[str, Any],
) -> dict[str, Any]:
    if slug not in _RUNTIME_ONLY_ORCHESTRATE_SLUGS:
        return resolved
    if (
        _ORCHESTRATION_MODE_INPUT not in submitted
        and _ORCHESTRATION_MODE_INPUT not in resolved
    ):
        return resolved
    normalized = dict(resolved)
    normalized[_ORCHESTRATION_MODE_INPUT] = "runtime"
    return normalized

def _jira_project_default_for_repository(repository: str | None) -> str | None:
    if not repository:
        return None
    raw = settings.atlassian.jira.jira_project_defaults_by_repository
    if not raw:
        return None
    normalized_repository = repository.strip().lower()
    allowed_projects = {
        project.strip().upper()
        for project in str(settings.atlassian.jira.jira_allowed_projects or "").split(",")
        if project.strip()
    }
    for item in raw.split(","):
        candidate = item.strip()
        if not candidate or "=" not in candidate:
            continue
        mapped_repository, mapped_project = candidate.split("=", 1)
        project_key = mapped_project.strip().upper()
        if mapped_repository.strip().lower() != normalized_repository:
            continue
        if allowed_projects and project_key not in allowed_projects:
            raise PresetValidationError(
                f"Configured Jira project default {project_key!r} for repository "
                f"{repository!r} is not in ATLASSIAN_JIRA_ALLOWED_PROJECTS."
            )
        return project_key
    return None

def _jira_project_default_for_context(repository: str | None) -> str | None:
    return _jira_project_default_for_repository(
        repository
    ) or _single_allowed_jira_project_key()


def _is_valid_github_repository(repository: str) -> bool:
    parts = repository.split("/")
    allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.-")
    return (
        len(parts) == 2
        and all(parts)
        and all(all(char in allowed for char in part) for part in parts)
    )


def _render_issue_ref_template(
    template: str,
    issue_value: Mapping[str, Any],
) -> str:
    rendered = str(template or "")
    for key, value in issue_value.items():
        token_value = str(value or "").strip()
        rendered = rendered.replace("{{ " + str(key) + " }}", token_value)
        rendered = rendered.replace("{{" + str(key) + "}}", token_value)
        rendered = rendered.replace("{" + str(key) + "}", token_value)
    return rendered.strip()


def _issue_object_from_ref(
    *,
    provider: str,
    ref_value: str,
    ref_path: Sequence[Any] | None,
) -> dict[str, Any] | None:
    ref = ref_value.strip()
    if not ref:
        return None
    if provider == "github":
        if "#" in ref and not ref.startswith(("http://", "https://")):
            repository, number_text = ref.rsplit("#", 1)
            repository = repository.strip("/")
            if _is_valid_github_repository(repository) and number_text.isdigit():
                return {"repository": repository, "number": int(number_text)}
        parsed = urlparse(ref)
        if (
            parsed.scheme in {"http", "https"}
            and parsed.netloc.lower() == "github.com"
        ):
            parts = [part for part in parsed.path.split("/") if part]
            if (
                len(parts) >= 4
                and parts[2] == "issues"
                and _is_valid_github_repository(f"{parts[0]}/{parts[1]}")
                and parts[3].isdigit()
            ):
                return {
                    "repository": f"{parts[0]}/{parts[1]}",
                    "number": int(parts[3]),
                }
    keys = [str(item or "").strip() for item in (ref_path or []) if str(item or "").strip()]
    if keys:
        target: dict[str, Any] = {}
        cursor = target
        for key in keys[:-1]:
            cursor[key] = {}
            cursor = cursor[key]
        cursor[keys[-1]] = ref
        return target
    return None


def _issue_ref_from_mapping(
    issue_value: Mapping[str, Any],
    *,
    provider: str,
    ref_path: Sequence[Any] | None,
) -> str:
    if isinstance(ref_path, Sequence) and not isinstance(
        ref_path, (str, bytes, bytearray)
    ):
        cursor: Any = issue_value
        for path_item in ref_path:
            if not isinstance(cursor, Mapping):
                cursor = None
                break
            cursor = cursor.get(str(path_item or "").strip())
        issue_ref = str(cursor or "").strip()
        if issue_ref:
            return issue_ref

    candidate_keys = (
        ("repository", "number")
        if provider == "github"
        else ("key", "issueKey", "issue_key", "jiraIssueKey")
    )
    if provider == "github":
        repository = str(issue_value.get("repository") or "").strip()
        number = str(issue_value.get("number") or "").strip()
        if repository and number:
            return f"{repository}#{number}"
        for key in ("url", "ref"):
            issue_ref = str(issue_value.get(key) or "").strip()
            if issue_ref:
                return issue_ref
        return ""

    for key in candidate_keys:
        issue_ref = str(issue_value.get(key) or "").strip()
        if issue_ref:
            return issue_ref
    return ""


def _apply_issue_input_overrides(
    *,
    annotations: Mapping[str, Any] | None,
    submitted: dict[str, Any],
) -> dict[str, Any]:
    issue_input = (annotations or {}).get("issueInput")
    if not isinstance(issue_input, Mapping):
        return submitted
    object_input = str(issue_input.get("objectInput") or "").strip()
    ref_input = str(issue_input.get("refInput") or "").strip()
    if not object_input or not ref_input:
        return submitted
    adjusted = dict(submitted)
    provider = str(issue_input.get("provider") or "").strip().lower()
    issue_value = adjusted.get(object_input)
    ref_path_raw = issue_input.get("refPath")
    ref_path = (
        ref_path_raw
        if isinstance(ref_path_raw, Sequence)
        and not isinstance(ref_path_raw, (str, bytes, bytearray))
        else None
    )
    if isinstance(issue_value, Mapping):
        ref_template = str(issue_input.get("refTemplate") or "").strip()
        issue_ref = ""
        if ref_template:
            issue_ref = _render_issue_ref_template(ref_template, issue_value)
        else:
            issue_ref = _issue_ref_from_mapping(
                issue_value,
                provider=provider,
                ref_path=ref_path,
            )
        if issue_ref:
            if not str(adjusted.get(ref_input) or "").strip():
                adjusted[ref_input] = issue_ref
            issue_object = _issue_object_from_ref(
                provider=provider,
                ref_value=issue_ref,
                ref_path=ref_path,
            )
            if issue_object is not None:
                adjusted[object_input] = {**issue_value, **issue_object}
        return adjusted
    if isinstance(issue_value, str):
        issue_ref = issue_value.strip()
        if issue_ref and not str(adjusted.get(ref_input) or "").strip():
            adjusted[ref_input] = issue_ref
        if issue_ref:
            issue_object = _issue_object_from_ref(
                provider=provider,
                ref_value=issue_ref,
                ref_path=ref_path,
            )
            if issue_object is not None:
                adjusted[object_input] = issue_object
        return adjusted
    legacy_ref = str(adjusted.get(ref_input) or "").strip()
    if legacy_ref:
        issue_object = _issue_object_from_ref(
            provider=provider,
            ref_value=legacy_ref,
            ref_path=ref_path,
        )
        if issue_object is not None:
            adjusted[object_input] = issue_object
    return adjusted


def _apply_contextual_input_overrides(
    *,
    slug: str,
    inputs_schema: list[dict[str, Any]],
    submitted: dict[str, Any],
    context: Mapping[str, Any] | None,
    annotations: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    adjusted = _apply_issue_input_overrides(
        annotations=annotations,
        submitted=dict(submitted),
    )

    if slug in {_BATCH_WORKFLOWS_SLUG, _BATCH_GITHUB_WORKFLOWS_SLUG}:
        repository = _repository_from_context(context)
        schema_defaults = _input_schema_defaults_by_name(inputs_schema)
        submitted_repository = str(adjusted.get("repository") or "").strip()
        schema_repository = str(schema_defaults.get("repository", "")).strip()
        if (
            repository
            and (not submitted_repository or submitted_repository == schema_repository)
        ):
            adjusted["repository"] = repository
        return adjusted

    if slug not in _JIRA_BREAKDOWN_PROJECT_DEFAULT_SLUGS:
        return adjusted

    repository = _repository_from_context(context)
    repository_project_key = _jira_project_default_for_repository(repository)
    project_key = repository_project_key or _single_allowed_jira_project_key()
    if not repository and not project_key:
        return submitted

    schema_defaults = _input_schema_defaults_by_name(inputs_schema)
    if slug in _JIRA_BREAKDOWN_COMPOSITE_SLUGS:
        submitted_repository = str(adjusted.get("repository") or "").strip()
        schema_repository = schema_defaults.get("repository", "")
        if (
            repository
            and (not submitted_repository or submitted_repository == schema_repository)
        ):
            adjusted["repository"] = repository

    if project_key:
        submitted_project = str(adjusted.get(_JIRA_BREAKDOWN_PROJECT_INPUT) or "").strip()
        if (
            not submitted_project
            or submitted_project in _JIRA_BREAKDOWN_REPLACEABLE_PROJECT_DEFAULTS
        ):
            adjusted[_JIRA_BREAKDOWN_PROJECT_INPUT] = project_key
    return adjusted

def _input_schema_defaults_by_name(inputs_schema: list[dict[str, Any]]) -> dict[str, str]:
    return {
        str(definition.get("name") or "").strip(): str(
            definition.get("default") or ""
        ).strip()
        for definition in inputs_schema
        if str(definition.get("name") or "").strip()
    }

def _contains_secret_like_value(value: Any) -> bool:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if _SECRET_LIKE_KEY_PATTERN.search(str(key)):
                return True
            if _contains_secret_like_value(nested):
                return True
        return False
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return any(_contains_secret_like_value(item) for item in value)
    return bool(_SECRET_LIKE_VALUE_PATTERN.search(str(value or "")))

def _capability_contract_from_inputs(
    *,
    inputs_schema: list[dict[str, Any]],
    annotations: Mapping[str, Any] | None,
    slug: str,
    title: str,
    description: str | None,
    preset_digest: str | None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    parts = capability_contract_from_legacy_inputs(
        inputs_schema=inputs_schema,
        annotations=annotations,
    )
    contract = normalize_capability_input_contract(
        owner=CapabilityInputOwner(
            id=slug,
            kind="preset",
            label=title,
            description=description,
            content_digest=preset_digest,
        ),
        parts=parts,
    )
    return contract["inputSchema"], contract["uiSchema"], contract["defaults"]

def _schema_contract_input_definitions(
    *,
    input_schema: Mapping[str, Any],
    ui_schema: Mapping[str, Any],
    defaults: Mapping[str, Any],
) -> list[dict[str, Any]]:
    properties = input_schema.get("properties")
    if not isinstance(properties, Mapping):
        return []
    required = {
        str(item or "").strip()
        for item in input_schema.get("required") or []
        if str(item or "").strip()
    }
    definitions: list[dict[str, Any]] = []
    for name, raw_field_schema in properties.items():
        field_name = str(name or "").strip()
        field_schema = dict(raw_field_schema) if isinstance(raw_field_schema, Mapping) else {}
        if not field_name:
            continue
        field_type = str(field_schema.get("type") or "text").strip().lower()
        if field_type == "boolean":
            input_type = "boolean"
        elif isinstance(field_schema.get("enum"), list):
            input_type = "enum"
        else:
            input_type = "text"
        definitions.append(
            {
                "name": field_name,
                "label": str(field_schema.get("title") or field_name).strip(),
                "type": input_type,
                "required": field_name in required,
                **(
                    {"default": defaults[field_name]}
                    if field_name in defaults
                    else {}
                ),
                **(
                    {"options": [str(item) for item in field_schema.get("enum") or []]}
                    if input_type == "enum"
                    else {}
                ),
                "schema": field_schema,
                **(
                    {"uiSchema": dict(ui_schema[field_name])}
                    if isinstance(ui_schema.get(field_name), Mapping)
                    else {}
                ),
            }
        )
    return definitions

def _normalize_seed_annotations(item: Mapping[str, Any]) -> dict[str, Any]:
    annotations = dict(item.get("annotations") or {})
    for source_key, target_key in (
        ("inputSchema", "inputSchema"),
        ("input_schema", "inputSchema"),
        ("uiSchema", "uiSchema"),
        ("ui_schema", "uiSchema"),
        ("defaults", "defaults"),
        ("checkpointBranching", "checkpointBranching"),
        ("checkpoint_branching", "checkpointBranching"),
    ):
        value = item.get(source_key)
        if isinstance(value, Mapping):
            annotations[target_key] = dict(value)
    if _contains_secret_like_value(annotations.get("defaults")):
        raise PresetValidationError(
            "Capability schema defaults contain a secret-like value."
        )
    return _normalize_preset_annotations(annotations)


def _normalize_preset_annotations(annotations: Mapping[str, Any] | None) -> dict[str, Any]:
    normalized = dict(annotations or {})
    checkpoint_branching_alias = normalized.pop("checkpoint_branching", None)
    if (
        checkpoint_branching_alias is not None
        and "checkpointBranching" not in normalized
    ):
        normalized["checkpointBranching"] = checkpoint_branching_alias
    checkpoint_branching = normalized.get("checkpointBranching")
    if checkpoint_branching is not None:
        normalized["checkpointBranching"] = _normalize_checkpoint_branching_policy(
            checkpoint_branching
        )
    return normalized


def _normalize_checkpoint_branching_policy(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise PresetValidationError(
            "Template checkpointBranching annotation must be an object."
        )

    enabled = value.get("enabled", False)
    if enabled is False:
        return {"enabled": False}
    if enabled is not True:
        raise PresetValidationError(
            "checkpointBranching.enabled must be true or false."
        )

    triggers = _normalize_checkpoint_branching_triggers(value.get("triggers"))
    max_branches = _positive_int_field(
        value.get("maxBranchesPerCheckpoint"),
        field_name="maxBranchesPerCheckpoint",
    )
    max_turns = _positive_int_field(
        value.get("maxTurnsPerBranch"),
        field_name="maxTurnsPerBranch",
    )

    promotion_policy = str(value.get("promotionPolicy") or "").strip()
    if promotion_policy != "approval_gated":
        raise PresetValidationError(
            "checkpointBranching.promotionPolicy must be 'approval_gated'."
        )

    workspace_policy = str(value.get("defaultWorkspacePolicy") or "").strip()
    if workspace_policy not in _CHECKPOINT_BRANCHING_WORKSPACE_POLICIES:
        supported = ", ".join(sorted(_CHECKPOINT_BRANCHING_WORKSPACE_POLICIES))
        raise PresetValidationError(
            "checkpointBranching.defaultWorkspacePolicy must be one of: "
            f"{supported}."
        )

    runtime_context_policy = str(
        value.get("runtimeContextPolicy") or "fresh_agent_run"
    ).strip()
    if runtime_context_policy not in _CHECKPOINT_BRANCHING_RUNTIME_CONTEXT_POLICIES:
        supported = ", ".join(sorted(_CHECKPOINT_BRANCHING_RUNTIME_CONTEXT_POLICIES))
        raise PresetValidationError(
            "checkpointBranching.runtimeContextPolicy must be one of: "
            f"{supported}."
        )

    publish_mode = str(value.get("publishMode") or "none").strip()
    if publish_mode != "none":
        raise PresetValidationError(
            "checkpointBranching.publishMode must be 'none'; promotion remains "
            "approval-gated."
        )

    side_effect_policy = str(value.get("sideEffectPolicy") or "isolated").strip()
    if side_effect_policy not in _CHECKPOINT_BRANCHING_SIDE_EFFECT_POLICIES:
        supported = ", ".join(sorted(_CHECKPOINT_BRANCHING_SIDE_EFFECT_POLICIES))
        raise PresetValidationError(
            "checkpointBranching.sideEffectPolicy must be one of: "
            f"{supported}."
        )

    budget = value.get("maxBudgetUsd")
    if budget is not None:
        try:
            budget_value = float(budget)
        except (TypeError, ValueError) as exc:
            raise PresetValidationError(
                "checkpointBranching.maxBudgetUsd must be a positive number."
            ) from exc
        if not math.isfinite(budget_value):
            raise PresetValidationError(
                "checkpointBranching.maxBudgetUsd must be a finite number."
            )
        if budget_value <= 0:
            raise PresetValidationError(
                "checkpointBranching.maxBudgetUsd must be greater than zero."
            )
    else:
        budget_value = None

    requested_work_branch = str(value.get("gitWorkBranch") or "").strip()
    if requested_work_branch and _is_protected_branch_ref(requested_work_branch):
        raise PresetValidationError(
            "checkpointBranching.gitWorkBranch must not target a protected branch."
        )
    if requested_work_branch:
        try:
            _validate_work_branch(
                requested_work_branch,
                product_branch_id="checkpoint-branching-preset-policy",
            )
        except CheckpointBranchGitBindingError as exc:
            raise PresetValidationError(
                "checkpointBranching.gitWorkBranch must be a sanitized, "
                "non-protected branch ref."
            ) from exc

    branch_templates = _normalize_checkpoint_branch_templates(
        value.get("branchTemplates")
    )
    normalized: dict[str, Any] = {
        "enabled": True,
        "triggers": triggers,
        "maxBranchesPerCheckpoint": max_branches,
        "maxTurnsPerBranch": max_turns,
        "promotionPolicy": promotion_policy,
        "defaultWorkspacePolicy": workspace_policy,
        "runtimeContextPolicy": runtime_context_policy,
        "publishMode": "none",
        "sideEffectPolicy": side_effect_policy,
        "branchTemplates": branch_templates,
    }
    if budget_value is not None:
        normalized["maxBudgetUsd"] = budget_value
    if requested_work_branch:
        normalized["gitWorkBranch"] = requested_work_branch
    return normalized


def _normalize_checkpoint_branching_triggers(value: Any) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise PresetValidationError(
            "checkpointBranching.triggers must be a non-empty list."
        )
    triggers: list[str] = []
    for raw_trigger in value:
        trigger = str(raw_trigger or "").strip()
        if not trigger:
            continue
        if trigger not in _CHECKPOINT_BRANCHING_SUPPORTED_TRIGGERS:
            supported = ", ".join(sorted(_CHECKPOINT_BRANCHING_SUPPORTED_TRIGGERS))
            raise PresetValidationError(
                f"Unsupported checkpointBranching trigger '{trigger}'. "
                f"Supported: {supported}."
            )
        if trigger not in triggers:
            triggers.append(trigger)
    if not triggers:
        raise PresetValidationError(
            "checkpointBranching.triggers must include at least one configured trigger."
        )
    return triggers


def _positive_int_field(value: Any, *, field_name: str) -> int:
    if isinstance(value, bool):
        raise PresetValidationError(f"checkpointBranching.{field_name} must be positive.")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise PresetValidationError(
            f"checkpointBranching.{field_name} must be positive."
        ) from exc
    if parsed <= 0:
        raise PresetValidationError(
            f"checkpointBranching.{field_name} must be greater than zero."
        )
    return parsed


def _normalize_checkpoint_branch_templates(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise PresetValidationError(
            "checkpointBranching.branchTemplates must be a non-empty list."
        )
    templates: list[dict[str, str]] = []
    labels: set[str] = set()
    for index, raw_template in enumerate(value, start=1):
        if not isinstance(raw_template, Mapping):
            raise PresetValidationError(
                f"checkpointBranching.branchTemplates[{index}] must be an object."
            )
        label = str(raw_template.get("label") or "").strip()
        instructions_ref = str(raw_template.get("instructionsRef") or "").strip()
        if not label or not instructions_ref:
            raise PresetValidationError(
                "checkpointBranching.branchTemplates require label and instructionsRef."
            )
        if label in labels:
            raise PresetValidationError(
                f"Duplicate checkpointBranching branch template label '{label}'."
            )
        labels.add(label)
        templates.append({"label": label, "instructionsRef": instructions_ref})
    if not templates:
        raise PresetValidationError(
            "checkpointBranching.branchTemplates must include at least one template."
        )
    return templates


def _is_protected_branch_ref(ref: str) -> bool:
    normalized = ref.removeprefix("refs/heads/").lower()
    return normalized in {"main", "master", "trunk", "develop"} or normalized.startswith(
        "release/"
    )


def _serialize_template(
    *,
    template: Preset,
    is_favorite: bool,
    recent_applied_at: datetime | None,
    include_detail: bool,
) -> dict[str, Any]:
    preset_digest = _preset_digest(template)
    input_schema, ui_schema, defaults = _capability_contract_from_inputs(
        inputs_schema=template.inputs_schema or [],
        annotations=template.annotations or {},
        slug=template.slug,
        title=template.title,
        description=template.description,
        preset_digest=preset_digest,
    )
    serialized = {
        "slug": template.slug,
        "scope": template.scope_type.value,
        "scopeRef": template.scope_ref,
        "title": template.title,
        "description": template.description,
        "tags": list(template.tags or []),
        "inputs": _effective_inputs_schema(
            slug=template.slug,
            inputs_schema=template.inputs_schema or [],
        ),
        "inputSchema": input_schema,
        "uiSchema": ui_schema,
        "defaults": defaults,
        "requiredCapabilities": _normalize_capabilities(
            list(template.required_capabilities or [])
        ),
        "releaseStatus": template.release_status.value,
        "presetDigest": preset_digest,
        "isFavorite": is_favorite,
        "recentAppliedAt": (
            recent_applied_at.astimezone(UTC).isoformat() if recent_applied_at else None
        ),
    }
    if include_detail:
        serialized.update(
            {
                "steps": list(template.steps or []),
                "annotations": dict(template.annotations or {}),
                "reviewedBy": str(template.reviewed_by)
                if template.reviewed_by
                else None,
                "reviewedAt": template.reviewed_at.isoformat()
                if template.reviewed_at
                else None,
            }
        )
    return serialized

def load_seed_template_definitions(seed_dir: Path) -> list[dict[str, Any]]:
    """Load seed template definitions from YAML files."""

    if not seed_dir.exists():
        return []
    loaded: list[dict[str, Any]] = []
    for yaml_path in sorted(seed_dir.glob("*.yaml")):
        document = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
        if not isinstance(document, dict):
            continue
        document["seedSource"] = str(yaml_path)
        loaded.append(document)
    return loaded

@dataclass(slots=True)
class SeedSyncResult:
    """Summary of a seed synchronization run."""

    created: int = 0
    updated: int = 0


def _validate_moonspec_remediation_topology(steps: list[dict[str, Any]]) -> None:
    """Validate either the canonical loop or a replay-only static topology."""

    loop_specs: list[Mapping[str, Any]] = []
    for step in steps:
        annotations = step.get("annotations")
        if not isinstance(annotations, Mapping):
            continue
        raw_loop = annotations.get("remediationLoop")
        if isinstance(raw_loop, Mapping):
            loop_specs.append(raw_loop)
    if loop_specs:
        if len(loop_specs) != 1:
            raise PresetValidationError(
                "A resolved plan may declare only one remediation loop."
            )
        try:
            RemediationLoopSpec.model_validate(loop_specs[0])
        except ValueError as exc:
            raise PresetValidationError(
                f"Invalid remediation loop contract: {exc}"
            ) from exc
        if any(
            isinstance(step.get("annotations"), Mapping)
            and (
                step["annotations"].get("moonSpecRemediationAttempt") is not None
                or step["annotations"].get("moonSpecRemediationMaxAttempts")
                is not None
            )
            for step in steps
        ):
            raise PresetValidationError(
                "A remediation loop cannot be mixed with pre-expanded attempts."
            )
        return

    chain: list[tuple[int, str, int, int, bool]] = []
    for index, step in enumerate(steps):
        annotations = step.get("annotations")
        if not isinstance(annotations, Mapping):
            continue
        role = str(
            annotations.get("issueImplementRole")
            or annotations.get("jiraOrchestrateRole")
            or ""
        ).strip().lower()
        if role not in {"moonspec-remediation", "moonspec-verification-gate"}:
            continue
        try:
            attempt = int(annotations.get("moonSpecRemediationAttempt"))
            maximum = int(annotations.get("moonSpecRemediationMaxAttempts"))
        except (TypeError, ValueError) as exc:
            raise PresetValidationError(
                "MoonSpec remediation annotations require integer attempt metadata."
            ) from exc
        chain.append(
            (
                index,
                role,
                attempt,
                maximum,
                annotations.get("moonSpecFinalRemediationGate") is True,
            )
        )
    if not chain:
        return
    maxima = {item[3] for item in chain}
    if len(maxima) != 1:
        raise PresetValidationError(
            "MoonSpec remediation nodes must declare one shared maximum attempt count."
        )
    maximum = next(iter(maxima))
    if maximum < 1:
        raise PresetValidationError("MoonSpec remediation maximum must be positive.")
    active = [item for item in chain if item[2] <= maximum]
    expected_count = maximum * 2
    if len(active) != expected_count:
        raise PresetValidationError(
            "remediation_max_attempts must activate a complete, unambiguous "
            "remediation and verification topology."
        )
    chain_indices = [item[0] for item in active]
    if chain_indices != list(
        range(chain_indices[0], chain_indices[0] + expected_count)
    ):
        raise PresetValidationError(
            "No publication or unrelated node may appear inside the MoonSpec "
            "remediation chain."
        )
    for attempt in range(1, maximum + 1):
        pair = sorted(
            (item for item in active if item[2] == attempt),
            key=lambda item: item[0],
        )
        if (
            len(pair) != 2
            or pair[0][1] != "moonspec-remediation"
            or pair[1][1] != "moonspec-verification-gate"
            or pair[1][0] != pair[0][0] + 1
        ):
            raise PresetValidationError(
                f"MoonSpec remediation attempt {attempt} must be one adjacent "
                "remediation/verifier pair."
            )
        if pair[1][4] != (attempt == maximum):
            raise PresetValidationError(
                "Only the active final MoonSpec verifier may be marked as the final gate."
            )


class PresetCatalogService:
    """Catalog service for presets."""

    def __init__(self, session: AsyncSession):
        self._session = session
        self._template_env = SandboxedEnvironment(
            autoescape=False,
            undefined=StrictUndefined,
        )

    async def _get_template_for_scope(
        self,
        *,
        slug: str,
        scope: PresetScopeType,
        scope_ref: str | None,
        include_inactive: bool = False,
    ) -> Preset:
        stmt = (
            select(Preset)
            .where(
                Preset.slug == slug,
                Preset.scope_type == scope,
                Preset.scope_ref == scope_ref,
            )
            .limit(1)
        )
        if not include_inactive:
            stmt = stmt.where(Preset.is_active.is_(True))
        result = await self._session.execute(stmt)
        template = result.scalar_one_or_none()
        if template is None:
            raise PresetNotFoundError("Template not found.")
        return template

    async def list_templates(
        self,
        *,
        scope: str | None = None,
        scope_ref: str | None = None,
        tag: str | None = None,
        search: str | None = None,
        favorites_only: bool = False,
        user_id: UUID | None = None,
        include_inactive: bool = False,
    ) -> list[dict[str, Any]]:
        stmt = select(Preset)
        if not include_inactive:
            stmt = stmt.where(Preset.is_active.is_(True))
        if scope is not None:
            scope_type = _normalize_scope(scope)
            stmt = stmt.where(Preset.scope_type == scope_type)
            if scope_type is not PresetScopeType.GLOBAL:
                stmt = stmt.where(
                    Preset.scope_ref
                    == _normalize_scope_ref(scope_type, scope_ref)
                )

        template_rows = (await self._session.execute(stmt)).scalars().all()
        lowered_tag = str(tag or "").strip().lower()
        lowered_search = str(search or "").strip().lower()

        favorites_map: dict[UUID, bool] = {}
        recents_map: dict[UUID, datetime] = {}
        if user_id is not None:
            favorite_rows = (
                (
                    await self._session.execute(
                        select(PresetFavorite.template_id).where(
                            PresetFavorite.user_id == user_id
                        )
                    )
                )
                .scalars()
                .all()
            )
            favorites_map = {template_id: True for template_id in favorite_rows}

            recent_rows = (
                await self._session.execute(
                    select(
                        PresetRecent.template_id,
                        PresetRecent.applied_at,
                    )
                    .where(PresetRecent.user_id == user_id)
                    .order_by(PresetRecent.applied_at.desc())
                )
            ).all()
            for template_id, applied_at in recent_rows:
                if template_id not in recents_map:
                    recents_map[template_id] = applied_at

        serialized: list[dict[str, Any]] = []
        for template in template_rows:
            if lowered_tag and lowered_tag not in {
                str(item).lower() for item in template.tags or []
            }:
                continue
            if lowered_search:
                haystack = " ".join(
                    [
                        template.slug,
                        template.title,
                        template.description,
                        " ".join(str(item) for item in (template.tags or [])),
                    ]
                ).lower()
                if lowered_search not in haystack:
                    continue
            is_favorite = bool(favorites_map.get(template.id, False))
            if favorites_only and not is_favorite:
                continue
            serialized.append(
                _serialize_template(
                    template=template,
                    is_favorite=is_favorite,
                    recent_applied_at=recents_map.get(template.id),
                    include_detail=False,
                )
            )
        serialized.sort(
            key=lambda item: (
                not item["isFavorite"],
                item["recentAppliedAt"] is None,
                item["recentAppliedAt"] or "",
                item["title"].lower(),
            )
        )
        logger.info(
            "preset_catalog.list",
            extra={
                "scope": scope,
                "favorites_only": favorites_only,
                "results": len(serialized),
            },
        )
        _METRICS.increment("list")
        return serialized

    async def get_template(
        self,
        *,
        slug: str,
        scope: str,
        scope_ref: str | None,
        user_id: UUID | None = None,
    ) -> dict[str, Any]:
        scope_type = _normalize_scope(scope)
        normalized_scope_ref = _normalize_scope_ref(scope_type, scope_ref)
        template = await self._get_template_for_scope(
            slug=_normalize_slug(slug),
            scope=scope_type,
            scope_ref=normalized_scope_ref,
        )
        is_favorite = False
        recent_applied_at = None
        if user_id is not None:
            favorite = await self._session.execute(
                select(PresetFavorite.id).where(
                    PresetFavorite.user_id == user_id,
                    PresetFavorite.template_id == template.id,
                )
            )
            is_favorite = favorite.scalar_one_or_none() is not None
            recent = await self._session.execute(
                select(PresetRecent.applied_at)
                .where(
                    PresetRecent.user_id == user_id,
                    PresetRecent.template_id == template.id,
                )
                .order_by(PresetRecent.applied_at.desc())
                .limit(1)
            )
            recent_applied_at = recent.scalar_one_or_none()

        return _serialize_template(
            template=template,
            is_favorite=is_favorite,
            recent_applied_at=recent_applied_at,
            include_detail=True,
        )

    async def create_template(
        self,
        *,
        slug: str,
        title: str,
        description: str,
        scope: str,
        scope_ref: str | None,
        tags: list[Any] | None,
        inputs_schema: list[dict[str, Any]],
        steps: list[dict[str, Any]],
        annotations: dict[str, Any] | None = None,
        required_capabilities: list[Any] | None = None,
        created_by: UUID | None = None,
        release_status: PresetReleaseStatus = PresetReleaseStatus.DRAFT,
        seed_source: str | None = None,
        auto_commit: bool = True,
    ) -> dict[str, Any]:
        normalized_scope = _normalize_scope(scope)
        normalized_scope_ref = _normalize_scope_ref(normalized_scope, scope_ref)
        normalized_slug = _normalize_slug(slug)
        existing = await self._session.execute(
            select(Preset.id).where(
                Preset.slug == normalized_slug,
                Preset.scope_type == normalized_scope,
                Preset.scope_ref == normalized_scope_ref,
            )
        )
        if existing.scalar_one_or_none() is not None:
            raise PresetConflictError(
                "Template slug already exists for this scope."
            )

        normalized_title = str(title or "").strip()
        normalized_description = str(description or "").strip()
        if not normalized_title:
            raise PresetValidationError("title is required")
        if not normalized_description:
            raise PresetValidationError("description is required")

        validated_inputs = self._validate_inputs_schema(inputs_schema)
        validated_steps = self._validate_template_steps(steps)
        derived_capabilities = _normalize_capabilities(
            (required_capabilities or [])
            + [
                cap
                for step in validated_steps
                for cap in _extract_step_capabilities(step)
            ]
        )
        template = Preset(
            id=uuid4(),
            slug=normalized_slug,
            scope_type=normalized_scope,
            scope_ref=normalized_scope_ref,
            title=normalized_title,
            description=normalized_description,
            tags=_normalize_tag_list(tags),
            required_capabilities=derived_capabilities,
            inputs_schema=validated_inputs,
            steps=validated_steps,
            annotations=_normalize_preset_annotations(annotations),
            max_step_count=max(25, len(validated_steps)),
            release_status=release_status,
            seed_source=seed_source,
            is_active=True,
            created_by=created_by,
        )
        self._session.add(template)
        await self._session.flush()
        if auto_commit:
            await self._session.commit()
            logger.info(
                "preset_catalog.create",
                extra={
                    "slug": normalized_slug,
                    "scope": normalized_scope.value,
                },
            )
            _METRICS.increment("create")
        return _serialize_template(
            template=template,
            is_favorite=False,
            recent_applied_at=None,
            include_detail=True,
        )

    def _validate_inputs_schema(
        self, inputs_schema: list[dict[str, Any]] | None
    ) -> list[dict[str, Any]]:
        if inputs_schema is None:
            return []
        validated: list[dict[str, Any]] = []
        names: set[str] = set()
        for raw_input in inputs_schema:
            if not isinstance(raw_input, dict):
                raise PresetValidationError("Template inputs must be objects.")
            name = str(raw_input.get("name") or "").strip()
            label = str(raw_input.get("label") or "").strip()
            input_type = str(raw_input.get("type") or "text").strip().lower()
            if not name:
                raise PresetValidationError(
                    "Template inputs require non-empty names."
                )
            if name in names:
                raise PresetValidationError(
                    f"Duplicate template input '{name}' is not allowed."
                )
            if not label:
                raise PresetValidationError(
                    f"Template input '{name}' requires a non-empty label."
                )
            if input_type not in _SUPPORTED_INPUT_TYPES:
                supported = ", ".join(sorted(_SUPPORTED_INPUT_TYPES))
                raise PresetValidationError(
                    f"Template input '{name}' has unsupported type '{input_type}'. "
                    f"Supported: {supported}"
                )
            options = raw_input.get("options")
            if input_type == "enum":
                if not isinstance(options, list) or len(options) == 0:
                    raise PresetValidationError(
                        f"Enum input '{name}' requires a non-empty options list."
                    )
                normalized_options = [
                    str(item).strip() for item in options if str(item).strip()
                ]
                if not normalized_options:
                    raise PresetValidationError(
                        f"Enum input '{name}' requires at least one non-empty option."
                    )
            else:
                normalized_options = []
            placeholder = str(raw_input.get("placeholder") or "").strip()
            default_value = raw_input.get("default")
            if _contains_secret_like_value(default_value):
                raise PresetValidationError(
                    f"Template input '{name}' has a secret-like default value."
                )
            schema_value = raw_input.get("schema")
            if schema_value is not None and not isinstance(schema_value, dict):
                raise PresetValidationError(
                    f"Template input '{name}' schema must be an object."
                )
            ui_schema_value = raw_input.get("uiSchema") or raw_input.get("ui_schema")
            if ui_schema_value is not None and not isinstance(ui_schema_value, dict):
                raise PresetValidationError(
                    f"Template input '{name}' uiSchema must be an object."
                )
            names.add(name)
            validated.append(
                {
                    "name": name,
                    "label": label,
                    "type": input_type,
                    "required": bool(raw_input.get("required", False)),
                    "default": default_value,
                    **({"options": normalized_options} if normalized_options else {}),
                    **({"placeholder": placeholder} if placeholder else {}),
                    **({"schema": dict(schema_value)} if isinstance(schema_value, dict) else {}),
                    **(
                        {"uiSchema": dict(ui_schema_value)}
                        if isinstance(ui_schema_value, dict)
                        else {}
                    ),
                }
            )
        return validated

    def _validate_template_steps(
        self, steps: list[dict[str, Any]] | None
    ) -> list[dict[str, Any]]:
        if not steps:
            raise PresetValidationError("Template steps must not be empty.")
        validated: list[dict[str, Any]] = []
        include_aliases: set[str] = set()
        for index, raw_step in enumerate(steps, start=1):
            if not isinstance(raw_step, dict):
                raise PresetValidationError(
                    f"Step {index} must be an object with instructions and optional skill."
                )
            raw_step = dict(raw_step)
            raw_step.pop("version", None)
            raw_step.pop("presetVersion", None)
            blocked = sorted(
                key for key in raw_step if str(key).strip() in _FORBIDDEN_STEP_KEYS
            )
            if blocked:
                raise PresetValidationError(
                    f"Step {index} uses forbidden keys: {', '.join(blocked)}."
                )
            kind = str(raw_step.get("kind") or _STEP_KIND).strip().lower()
            if kind not in {_STEP_KIND, _INCLUDE_KIND}:
                raise PresetValidationError(
                    f"Step {index} kind must be one of: {_STEP_KIND}, {_INCLUDE_KIND}."
                )
            if kind == _INCLUDE_KIND:
                unsupported = sorted(
                    key
                    for key in raw_step
                    if str(key).strip() not in _INCLUDE_STEP_KEYS
                )
                if unsupported:
                    raise PresetValidationError(
                        f"Step {index} include uses unsupported keys: "
                        f"{', '.join(unsupported)}."
                    )
                include_slug = _normalize_slug(str(raw_step.get("slug") or ""))
                include_alias = _normalize_slug(str(raw_step.get("alias") or ""))
                if include_alias in include_aliases:
                    raise PresetValidationError(
                        f"Step {index} include alias '{include_alias}' is duplicated."
                    )
                include_aliases.add(include_alias)
                include_scope = raw_step.get("scope")
                include_payload: dict[str, Any] = {
                    "kind": _INCLUDE_KIND,
                    "slug": include_slug,
                    "alias": include_alias,
                }
                if include_scope is not None:
                    include_payload["scope"] = _normalize_scope(str(include_scope)).value
                input_mapping = raw_step.get("inputMapping", raw_step.get("input_mapping"))
                if input_mapping is not None:
                    if not isinstance(input_mapping, dict):
                        raise PresetValidationError(
                            f"Step {index} include inputMapping must be an object."
                        )
                    include_payload["inputMapping"] = dict(input_mapping)
                annotations = raw_step.get("annotations")
                if annotations is not None:
                    if not isinstance(annotations, dict):
                        raise PresetValidationError(
                            f"Step {index} annotations must be an object when provided."
                        )
                    include_payload["annotations"] = dict(annotations)
                validated.append(include_payload)
                continue
            instructions = str(raw_step.get("instructions") or "").strip()
            if not instructions:
                raise PresetValidationError(
                    f"Step {index} requires non-empty instructions."
                )
            step_type = _normalize_step_type(raw_step, index=index)
            step_payload: dict[str, Any] = {
                "type": step_type,
                "instructions": instructions,
            }
            step_id = str(raw_step.get("id") or "").strip()
            if step_id:
                step_payload["id"] = step_id
            original_step_id = str(raw_step.get("originalStepId") or "").strip()
            if original_step_id:
                step_payload["originalStepId"] = original_step_id
            title = str(raw_step.get("title") or "").strip()
            if title:
                step_payload["title"] = title
            if "slug" in raw_step and str(raw_step.get("slug") or "").strip():
                step_payload["slug"] = str(raw_step.get("slug")).strip()
            if step_type == _STEP_TYPE_TOOL:
                if raw_step.get("skill") is not None:
                    raise PresetValidationError(
                        f"Step {index} Tool step must not include a skill payload."
                    )
                if raw_step.get("preset") is not None:
                    raise PresetValidationError(
                        f"Step {index} Tool step must not include a preset payload."
                    )
                step_payload["tool"] = _normalize_tool_payload(
                    raw_step.get("tool"), index=index
                )
            elif step_type == _STEP_TYPE_SKILL:
                if raw_step.get("tool") is not None:
                    raise PresetValidationError(
                        f"Step {index} Skill step must not include a tool payload."
                    )
                if raw_step.get("preset") is not None:
                    raise PresetValidationError(
                        f"Step {index} Skill step must not include a preset payload."
                    )
                step_payload["skill"] = _normalize_skill_payload(
                    raw_step.get("skill"), index=index
                )
            else:
                if (
                    raw_step.get("tool") is not None
                    or raw_step.get("skill") is not None
                ):
                    raise PresetValidationError(
                        f"Step {index} Preset step must not include tool or skill payloads."
                    )
                step_payload["preset"] = _normalize_preset_payload(
                    raw_step.get("preset"), index=index
                )
            annotations = raw_step.get("annotations")
            if annotations is not None:
                if not isinstance(annotations, dict):
                    raise PresetValidationError(
                        f"Step {index} annotations must be an object when provided."
                    )
                step_payload["annotations"] = dict(annotations)
            step_payload.update(
                {
                    str(key).strip(): value
                    for key, value in raw_step.items()
                    if str(key).strip()
                    and str(key).strip() not in _STEP_RESERVED_KEYS
                }
            )
            validated.append(step_payload)
        return validated

    async def _expand_preset_steps(
        self,
        *,
        template: Preset,
        scope: PresetScopeType,
        scope_ref: str | None,
        variables: dict[str, Any],
        root_slug: str,
        root_inputs: dict[str, Any],
        root_max_step_count: int,
        enforce_limit: bool,
        path: list[str],
        visited: set[tuple[str, str, str | None]],
        resolved_steps: list[dict[str, Any]],
        alias: str | None = None,
        input_mapping: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        node: dict[str, Any] = {
            "slug": template.slug,
            "digest": _preset_digest(template),
            "scope": scope.value,
            "path": list(path),
            "stepIds": [],
            "includes": [],
            "requiredCapabilities": _normalize_capabilities(
                list(template.required_capabilities or [])
            ),
        }
        if alias:
            node["alias"] = alias
        if input_mapping:
            node["inputMapping"] = dict(input_mapping)

        for source_index, source_step in enumerate(template.steps or [], start=1):
            rendered = _render_value(
                self._template_env, source_step, variables=variables
            )
            if not isinstance(rendered, dict):
                raise PresetValidationError(
                    f"Expanded step at {_format_include_path(path)} must be an object."
                )
            if not _preset_step_enabled(rendered.pop("enabled", True)):
                continue
            rendered.pop("version", None)
            rendered.pop("presetVersion", None)
            kind = str(rendered.get("kind") or _STEP_KIND).strip().lower()
            if (
                kind == _STEP_KIND
                and str(rendered.get("type") or "").strip().lower()
                == _STEP_TYPE_PRESET
            ):
                rendered = _preset_step_to_include(rendered, index=source_index)
                rendered.pop("version", None)
                rendered.pop("presetVersion", None)
                kind = _INCLUDE_KIND
            if kind == _INCLUDE_KIND:
                include_slug = _normalize_slug(str(rendered.get("slug") or ""))
                include_alias = _normalize_slug(str(rendered.get("alias") or ""))
                include_scope = _normalize_scope(str(rendered.get("scope") or scope.value))
                include_scope_ref = (
                    None
                    if include_scope is PresetScopeType.GLOBAL
                    else scope_ref
                )
                include_path = [
                    *path,
                    _template_path_label(
                        slug=include_slug,
                        alias=include_alias,
                    ),
                ]
                if scope is PresetScopeType.GLOBAL and include_scope is PresetScopeType.PERSONAL:
                    message = (
                        "Global presets cannot include personal presets at "
                        f"{_format_include_path(include_path)}."
                    )
                    raise _include_tree_error(
                        message=message,
                        code="preset_include_scope_violation",
                        include_path=include_path,
                    )
                target_key = (include_scope.value, include_slug, include_scope_ref)
                if target_key in visited:
                    message = (
                        "Preset include cycle detected at "
                        f"{_format_include_path(include_path)}."
                    )
                    raise _include_tree_error(
                        message=message,
                        code="preset_include_cycle",
                        include_path=include_path,
                    )
                try:
                    child_template = await self._get_template_for_scope(
                        slug=include_slug,
                        scope=include_scope,
                        scope_ref=include_scope_ref,
                    )
                except PresetError as exc:
                    message = (
                        f"Preset include target unavailable at "
                        f"{_format_include_path(include_path)}: {exc}"
                    )
                    raise _include_tree_error(
                        message=message,
                        code="preset_include_missing",
                        include_path=include_path,
                    ) from exc
                if child_template.release_status is PresetReleaseStatus.INACTIVE:
                    message = (
                        f"Preset include target is inactive at "
                        f"{_format_include_path(include_path)}."
                    )
                    raise _include_tree_error(
                        message=message,
                        code="preset_include_inactive",
                        include_path=include_path,
                    )
                input_mapping = rendered.get("inputMapping") or {}
                if not isinstance(input_mapping, dict):
                    message = (
                        f"Preset include inputMapping must be an object at "
                        f"{_format_include_path(include_path)}."
                    )
                    raise _include_tree_error(
                        message=message,
                        code="preset_include_input_mapping_invalid",
                        include_path=include_path,
                    )
                try:
                    child_schema = _effective_inputs_schema(
                        slug=child_template.slug,
                        inputs_schema=child_template.inputs_schema or [],
                        context=variables.get("context"),
                    )
                    submitted_child_inputs = _apply_contextual_input_overrides(
                        slug=child_template.slug,
                        inputs_schema=child_schema,
                        submitted=dict(input_mapping),
                        context=variables.get("context"),
                        annotations=child_template.annotations or {},
                    )
                    child_inputs = self._resolve_inputs(
                        schema=child_schema,
                        submitted=submitted_child_inputs,
                    )
                    child_inputs = _normalize_runtime_orchestration_mode(
                        slug=child_template.slug,
                        submitted=submitted_child_inputs,
                        resolved=child_inputs,
                    )
                except PresetValidationError as exc:
                    message = (
                        f"Preset include input mapping is incompatible at "
                        f"{_format_include_path(include_path)}: {exc}"
                    )
                    raise _include_tree_error(
                        message=message,
                        code="preset_include_input_mapping_invalid",
                        include_path=include_path,
                    ) from exc
                child_variables = {
                    **variables,
                    "inputs": child_inputs,
                }
                child_node = await self._expand_preset_steps(
                    template=child_template,
                    scope=include_scope,
                    scope_ref=include_scope_ref,
                    variables=child_variables,
                    root_slug=root_slug,
                    root_inputs=root_inputs,
                    root_max_step_count=root_max_step_count,
                    enforce_limit=enforce_limit,
                    path=include_path,
                    visited={*visited, target_key},
                    resolved_steps=resolved_steps,
                    alias=include_alias,
                    input_mapping=child_inputs,
                )
                node["includes"].append(child_node)
                node["stepIds"].extend(child_node["stepIds"])
                continue

            if kind != _STEP_KIND:
                raise PresetValidationError(
                    f"Expanded step kind must be one of: {_STEP_KIND}, {_INCLUDE_KIND}."
                )
            blocked = sorted(
                key for key in rendered if str(key).strip() in _FORBIDDEN_STEP_KEYS
            )
            if blocked:
                raise PresetValidationError(
                    f"Expanded step uses forbidden keys at {_format_include_path(path)}: "
                    f"{', '.join(blocked)}."
                )
            instructions = str(rendered.get("instructions") or "").strip()
            if not instructions:
                raise PresetValidationError(
                    f"Expanded step instructions may not be empty at "
                    f"{_format_include_path(path)}."
                )
            step_type = _normalize_step_type(rendered, index=source_index)
            next_index = len(resolved_steps) + 1
            if enforce_limit and next_index > root_max_step_count:
                message = (
                    f"Template expansion exceeded max_step_count={root_max_step_count} "
                    f"at {_format_include_path(path)}."
                )
                raise _include_tree_error(
                    message=message,
                    code="preset_include_limit_exceeded",
                    include_path=path,
                )
            step_payload: dict[str, Any] = {
                "id": _build_step_id(
                    slug=root_slug,
                    index=next_index,
                    inputs=root_inputs,
                ),
                "type": step_type,
                "instructions": instructions,
                "presetProvenance": {
                    "root": {"slug": root_slug},
                    "source": {
                        "slug": template.slug,
                        "presetDigest": _preset_digest(template),
                        "scope": scope.value,
                        "stepIndex": source_index,
                    },
                    "path": list(path),
                },
                "source": {
                    "kind": "preset-derived",
                    "presetSlug": template.slug,
                    "presetDigest": _preset_digest(template),
                    "includePath": list(path),
                },
            }
            original_step_id = _source_original_step_id(rendered)
            if original_step_id:
                step_payload["presetProvenance"]["source"][
                    "originalStepId"
                ] = original_step_id
                step_payload["source"]["originalStepId"] = original_step_id
            if alias:
                step_payload["presetProvenance"]["alias"] = alias
            title = str(rendered.get("title") or "").strip()
            if title:
                step_payload["title"] = title
            annotations = rendered.get("annotations")
            if annotations is not None:
                if not isinstance(annotations, dict):
                    raise PresetValidationError(
                        f"Expanded step annotations must be an object at "
                        f"{_format_include_path(path)}."
                    )
                step_payload["annotations"] = dict(annotations)
            if step_type == _STEP_TYPE_TOOL:
                if rendered.get("skill") is not None:
                    raise PresetValidationError(
                        f"Expanded Tool step must not include a skill payload at "
                        f"{_format_include_path(path)}."
                    )
                step_payload["tool"] = _normalize_tool_payload(
                    rendered.get("tool"), index=source_index
                )
            else:
                if rendered.get("tool") is not None:
                    raise PresetValidationError(
                        f"Expanded Skill step must not include a tool payload at "
                        f"{_format_include_path(path)}."
                    )
                step_payload["skill"] = _normalize_skill_payload(
                    rendered.get("skill"), index=source_index
                )
                _promote_skill_runtime(step_payload)
            step_payload.update(
                {
                    str(key).strip(): value
                    for key, value in rendered.items()
                    if str(key).strip()
                    and str(key).strip() not in _STEP_RESERVED_KEYS
                }
            )
            resolved_steps.append(step_payload)
            node["stepIds"].append(step_payload["id"])
        return node

    async def expand_template(
        self,
        *,
        slug: str,
        scope: str,
        scope_ref: str | None,
        inputs: dict[str, Any],
        context: dict[str, Any] | None = None,
        options: ExpandOptions | None = None,
        user_id: UUID | None = None,
    ) -> dict[str, Any]:
        normalized_scope = _normalize_scope(scope)
        normalized_scope_ref = _normalize_scope_ref(normalized_scope, scope_ref)
        template = await self._get_template_for_scope(
            slug=_normalize_slug(slug),
            scope=normalized_scope,
            scope_ref=normalized_scope_ref,
        )

        effective_context = dict(context or {})
        if not _repository_from_context(effective_context):
            input_repository = (inputs or {}).get("repository") or (inputs or {}).get(
                "repo"
            )
            if isinstance(input_repository, str) and input_repository.strip():
                effective_context["repository"] = input_repository.strip()
                effective_context["repo"] = input_repository.strip()
        effective_schema = _effective_inputs_schema(
            slug=template.slug,
            inputs_schema=template.inputs_schema or [],
            context=effective_context,
        )
        annotated_schema = (template.annotations or {}).get("inputSchema")
        if isinstance(annotated_schema, Mapping):
            annotated_ui_schema = (template.annotations or {}).get("uiSchema")
            annotated_defaults = (template.annotations or {}).get("defaults")
            contract_definitions = _schema_contract_input_definitions(
                input_schema=annotated_schema,
                ui_schema=annotated_ui_schema
                if isinstance(annotated_ui_schema, Mapping)
                else {},
                defaults=annotated_defaults
                if isinstance(annotated_defaults, Mapping)
                else {},
            )
            existing_names = {
                str(definition.get("name") or "").strip()
                for definition in effective_schema
            }
            effective_schema = [
                *effective_schema,
                *[
                    definition
                    for definition in contract_definitions
                    if definition["name"] not in existing_names
                ],
            ]
        submitted_inputs = _apply_contextual_input_overrides(
            slug=template.slug,
            inputs_schema=effective_schema,
            submitted=dict(inputs or {}),
            context=effective_context,
            annotations=template.annotations or {},
        )
        removed_batch_inputs = sorted(
            name
            for name in _REMOVED_BATCH_WORKFLOWS_INPUTS
            if template.slug == _BATCH_WORKFLOWS_SLUG and name in submitted_inputs
        )
        for name in removed_batch_inputs:
            submitted_inputs.pop(name, None)
        validated_inputs = self._resolve_inputs(
            schema=effective_schema,
            submitted=submitted_inputs,
        )
        validated_inputs = _normalize_runtime_orchestration_mode(
            slug=template.slug,
            submitted=submitted_inputs,
            resolved=validated_inputs,
        )
        _validate_breakdown_source_inputs(
            slug=template.slug,
            inputs=validated_inputs,
        )
        variables = {
            "inputs": validated_inputs,
            "context": effective_context,
            "now": datetime.now(UTC).isoformat(),
            "iso_today": datetime.now(UTC).date().isoformat(),
        }

        resolved_steps: list[dict[str, Any]] = []
        warnings: list[str] = []
        enforce_limit = options.should_enforce_step_limit if options else True
        max_step_count = max(int(template.max_step_count or 25), 1)
        root_path = [
            _template_path_label(slug=template.slug)
        ]
        composition = await self._expand_preset_steps(
            template=template,
            scope=normalized_scope,
            scope_ref=normalized_scope_ref,
            variables=variables,
            root_slug=template.slug,
            root_inputs=validated_inputs,
            root_max_step_count=max_step_count,
            enforce_limit=enforce_limit,
            path=root_path,
            visited={(normalized_scope.value, template.slug, normalized_scope_ref)},
            resolved_steps=resolved_steps,
        )
        _validate_moonspec_remediation_topology(resolved_steps)
        authored_presets = _authored_presets_from_composition(composition)

        template_caps = _normalize_capabilities(
            list(template.required_capabilities or [])
            + _composition_capabilities(composition)
            + [
                cap
                for step in resolved_steps
                for cap in _extract_step_capabilities(step)
            ]
        )
        workflow_publish = (template.annotations or {}).get("workflowPublish")
        if workflow_publish is not None and not isinstance(workflow_publish, Mapping):
            raise PresetValidationError(
                "Template workflowPublish annotation must be an object."
            )
        annotations = template.annotations or {}
        checkpoint_branching = annotations.get("checkpointBranching") or annotations.get(
            "checkpoint_branching"
        )
        if checkpoint_branching is not None:
            checkpoint_branching = _normalize_checkpoint_branching_policy(
                checkpoint_branching
            )

        if template.release_status is PresetReleaseStatus.INACTIVE:
            warnings.append("Template is marked inactive.")

        if user_id is not None:
            await self.record_recent(
                user_id=user_id,
                template_id=template.id,
            )

        applied_at = datetime.now(UTC).isoformat()
        logger.info(
            "preset_catalog.expand",
            extra={
                "slug": template.slug,
                "scope": normalized_scope.value,
                "steps": len(resolved_steps),
            },
        )
        _METRICS.increment("expand")
        expanded_payload = {
            "steps": resolved_steps,
            "composition": composition,
            "authoredPresets": authored_presets,
            "appliedTemplate": {
                "slug": template.slug,
                "presetDigest": _preset_digest(template),
                "inputs": validated_inputs,
                "stepIds": [step["id"] for step in resolved_steps],
                "appliedAt": applied_at,
                "composition": composition,
                "authoredPresets": authored_presets,
            },
            "capabilities": template_caps,
            "warnings": warnings,
        }
        if isinstance(workflow_publish, Mapping):
            expanded_payload["publish"] = dict(workflow_publish)
        if isinstance(checkpoint_branching, Mapping):
            expanded_payload["checkpointBranching"] = dict(checkpoint_branching)
        return expanded_payload

    def _resolve_inputs(
        self,
        *,
        schema: list[dict[str, Any]],
        submitted: dict[str, Any],
    ) -> dict[str, Any]:
        resolved: dict[str, Any] = {}
        for definition in schema:
            name = str(definition.get("name") or "").strip()
            input_type = str(definition.get("type") or "text").strip().lower()
            required = bool(definition.get("required", False))
            default = definition.get("default")
            raw_value = submitted[name] if name in submitted else default
            if raw_value in (None, "") and required:
                raise PresetValidationError(
                    f"Missing required template input '{name}'.",
                    errors=[
                        {
                            "path": f"preset.inputs.{name}",
                            "message": f"{definition.get('label') or name} is required.",
                            "code": "required",
                            "recoverable": True,
                        }
                    ],
                )
            if raw_value in (None, ""):
                resolved[name] = raw_value
                continue
            field_schema = definition.get("schema")
            if isinstance(field_schema, dict) and field_schema.get("type") == "object":
                if not isinstance(raw_value, dict):
                    raise PresetValidationError(
                        f"Input '{name}' must be an object.",
                        errors=[
                            {
                                "path": f"preset.inputs.{name}",
                                "message": f"{definition.get('label') or name} must be an object.",
                                "code": "invalid_type",
                                "recoverable": True,
                            }
                        ],
                    )
                object_value = dict(raw_value)
                nested_errors: list[dict[str, Any]] = []
                properties = field_schema.get("properties")
                property_schemas = properties if isinstance(properties, dict) else {}
                for required_key in field_schema.get("required") or []:
                    key = str(required_key or "").strip()
                    if not key:
                        continue
                    nested = object_value.get(key)
                    if nested in (None, ""):
                        nested_schema = property_schemas.get(key)
                        title = (
                            nested_schema.get("title")
                            if isinstance(nested_schema, dict)
                            else None
                        )
                        label = str(title or f"{definition.get('label') or name} {key}").strip()
                        if key == "key" and name == "jira_issue":
                            message = "Jira issue key is required."
                        else:
                            message = f"{label} is required."
                        nested_errors.append(
                            {
                                "path": f"preset.inputs.{name}.{key}",
                                "message": message,
                                "code": "required",
                                "recoverable": True,
                            }
                        )
                if nested_errors:
                    raise PresetValidationError(
                        f"Input '{name}' failed schema validation.",
                        errors=nested_errors,
                    )
                if _contains_secret_like_value(object_value):
                    raise PresetValidationError(
                        f"Input '{name}' contains a secret-like value.",
                        errors=[
                            {
                                "path": f"preset.inputs.{name}",
                                "message": "Input contains a secret-like value.",
                                "code": "secret_like_value",
                                "recoverable": True,
                            }
                        ],
                    )
                resolved[name] = object_value
                continue
            if input_type == "boolean":
                if isinstance(raw_value, bool):
                    resolved[name] = raw_value
                else:
                    lowered = str(raw_value).strip().lower()
                    if lowered in {"1", "true", "yes", "on"}:
                        resolved[name] = True
                    elif lowered in {"0", "false", "no", "off"}:
                        resolved[name] = False
                    else:
                        raise PresetValidationError(
                            f"Input '{name}' must be a boolean value."
                        )
                continue
            if input_type == "enum":
                options = [str(item).strip() for item in definition.get("options", [])]
                candidate = str(raw_value).strip()
                if candidate not in options:
                    raise PresetValidationError(
                        f"Input '{name}' must be one of: {', '.join(options)}."
                    )
                resolved[name] = candidate
                continue
            resolved[name] = str(raw_value).strip()
        return resolved

    async def set_favorite(
        self,
        *,
        user_id: UUID,
        slug: str,
        scope: str,
        scope_ref: str | None,
        auto_commit: bool = True,
    ) -> None:
        scope_type = _normalize_scope(scope)
        normalized_scope_ref = _normalize_scope_ref(scope_type, scope_ref)
        template = await self._get_template_for_scope(
            slug=_normalize_slug(slug),
            scope=scope_type,
            scope_ref=normalized_scope_ref,
        )
        existing = await self._session.execute(
            select(PresetFavorite.id).where(
                PresetFavorite.user_id == user_id,
                PresetFavorite.template_id == template.id,
            )
        )
        if existing.scalar_one_or_none() is None:
            self._session.add(
                PresetFavorite(
                    user_id=user_id,
                    template_id=template.id,
                )
            )
            if auto_commit:
                await self._session.commit()
            else:
                await self._session.flush()
            logger.info(
                "preset_catalog.favorite",
                extra={
                    "slug": template.slug,
                    "scope": scope_type.value,
                },
            )
            _METRICS.increment("favorite")

    async def clear_favorite(
        self,
        *,
        user_id: UUID,
        slug: str,
        scope: str,
        scope_ref: str | None,
    ) -> None:
        scope_type = _normalize_scope(scope)
        normalized_scope_ref = _normalize_scope_ref(scope_type, scope_ref)
        template = await self._get_template_for_scope(
            slug=_normalize_slug(slug),
            scope=scope_type,
            scope_ref=normalized_scope_ref,
            include_inactive=True,
        )
        await self._session.execute(
            delete(PresetFavorite).where(
                PresetFavorite.user_id == user_id,
                PresetFavorite.template_id == template.id,
            )
        )
        await self._session.commit()
        logger.info(
            "preset_catalog.unfavorite",
            extra={
                "slug": template.slug,
                "scope": scope_type.value,
            },
        )
        _METRICS.increment("unfavorite")

    async def record_recent(
        self,
        *,
        user_id: UUID,
        template_id: UUID,
        auto_commit: bool = True,
    ) -> None:
        dialect_name = self._session.bind.dialect.name if self._session.bind else ""
        if dialect_name == "postgresql":
            await self._session.execute(
                pg_insert(PresetRecent)
                .values(
                    user_id=user_id,
                    template_id=template_id,
                )
                .on_conflict_do_update(
                    index_elements=["user_id", "template_id"],
                    set_={"applied_at": datetime.now(UTC)},
                )
            )
        else:
            self._session.add(
                PresetRecent(
                    user_id=user_id,
                    template_id=template_id,
                )
            )
        await self._session.flush()
        keep_recent_ids = (
            select(PresetRecent.id)
            .where(PresetRecent.user_id == user_id)
            .order_by(
                PresetRecent.applied_at.desc(),
                PresetRecent.id.desc(),
            )
            .limit(5)
        )
        await self._session.execute(
            delete(PresetRecent).where(
                PresetRecent.user_id == user_id,
                PresetRecent.id.not_in(keep_recent_ids),
            )
        )
        if auto_commit:
            await self._session.commit()
        else:
            await self._session.flush()
        logger.info(
            "preset_catalog.recent",
            extra={"template_id": str(template_id)},
        )
        _METRICS.increment("recent")

    async def soft_delete_template(
        self,
        *,
        slug: str,
        scope: str,
        scope_ref: str | None,
    ) -> None:
        scope_type = _normalize_scope(scope)
        normalized_scope_ref = _normalize_scope_ref(scope_type, scope_ref)
        template = await self._get_template_for_scope(
            slug=_normalize_slug(slug),
            scope=scope_type,
            scope_ref=normalized_scope_ref,
            include_inactive=True,
        )
        template.is_active = False
        await self._session.commit()
        logger.info(
            "preset_catalog.delete",
            extra={
                "slug": template.slug,
                "scope": scope_type.value,
            },
        )
        _METRICS.increment("delete")

    async def deactivate_templates(
        self,
        *,
        slugs: Sequence[str],
        scope: str,
        scope_ref: str | None,
    ) -> int:
        scope_type = _normalize_scope(scope)
        normalized_scope_ref = _normalize_scope_ref(scope_type, scope_ref)
        normalized_slugs = [
            _normalize_slug(slug) for slug in slugs if str(slug or "").strip()
        ]
        if not normalized_slugs:
            return 0

        result = await self._session.execute(
            select(Preset).where(
                Preset.slug.in_(normalized_slugs),
                Preset.scope_type == scope_type,
                Preset.scope_ref == normalized_scope_ref,
                Preset.is_active.is_(True),
            )
        )
        templates = result.scalars().all()
        for template in templates:
            template.is_active = False

        if templates:
            await self._session.commit()
            logger.info(
                "preset_catalog.deactivate",
                extra={
                    "slugs": [template.slug for template in templates],
                    "scope": scope_type.value,
                },
            )
            _METRICS.increment("delete", len(templates))
        return len(templates)

    async def set_release_status(
        self,
        *,
        slug: str,
        scope: str,
        scope_ref: str | None,
        release_status: PresetReleaseStatus,
        reviewer_id: UUID | None = None,
    ) -> dict[str, Any]:
        scope_type = _normalize_scope(scope)
        normalized_scope_ref = _normalize_scope_ref(scope_type, scope_ref)
        template = await self._get_template_for_scope(
            slug=_normalize_slug(slug),
            scope=scope_type,
            scope_ref=normalized_scope_ref,
            include_inactive=True,
        )
        template.release_status = release_status
        if reviewer_id is not None:
            template.reviewed_by = reviewer_id
            template.reviewed_at = datetime.now(UTC)
        await self._session.commit()
        logger.info(
            "preset_catalog.review",
            extra={
                "slug": template.slug,
                "scope": scope_type.value,
                "release_status": release_status.value,
            },
        )
        _METRICS.increment("review")
        return _serialize_template(
            template=template,
            is_favorite=False,
            recent_applied_at=None,
            include_detail=True,
        )

    async def import_seed_templates(
        self,
        *,
        seed_dir: Path,
    ) -> int:
        loaded = load_seed_template_definitions(seed_dir)

        created = 0
        for item in loaded:
            item = dict(item)
            item.pop("version", None)
            item.pop("presetVersion", None)
            scope = str(item.get("scope", "global")).strip() or "global"
            try:
                await self.create_template(
                    slug=str(
                        item.get("slug") or _slugify_from_title(item.get("title", ""))
                    ),
                    title=str(item.get("title") or "").strip(),
                    description=str(item.get("description") or "").strip()
                    or "Seed template.",
                    scope=scope,
                    scope_ref=item.get("scopeRef"),
                    tags=item.get("tags") or [],
                    inputs_schema=item.get("inputs") or [],
                    steps=item.get("steps") or [],
                    annotations=_normalize_seed_annotations(item),
                    required_capabilities=item.get("requiredCapabilities") or [],
                    created_by=None,
                    release_status=PresetReleaseStatus.ACTIVE,
                    seed_source=item.get("seedSource"),
                    auto_commit=False,
                )
                created += 1
            except PresetConflictError:
                # Seed templates are idempotent; an existing conflicting row is skipped.
                continue
        if created > 0:
            await self._session.commit()
        return created

    async def sync_seed_templates(
        self,
        *,
        seed_dir: Path,
    ) -> SeedSyncResult:
        """Create or refresh seeded templates from YAML definitions."""

        loaded = load_seed_template_definitions(seed_dir)
        result = SeedSyncResult()

        for item in loaded:
            item = dict(item)
            item.pop("version", None)
            item.pop("presetVersion", None)
            scope = _normalize_scope(str(item.get("scope", "global")).strip() or "global")
            scope_ref = _normalize_scope_ref(scope, item.get("scopeRef"))
            slug = str(item.get("slug") or _slugify_from_title(item.get("title", "")))
            normalized_slug = _normalize_slug(slug)
            title = str(item.get("title") or "").strip()
            description = str(item.get("description") or "").strip() or "Seed template."
            validated_inputs = self._validate_inputs_schema(item.get("inputs") or [])
            validated_steps = self._validate_template_steps(item.get("steps") or [])
            annotations = _normalize_seed_annotations(item)
            derived_capabilities = _normalize_capabilities(
                (item.get("requiredCapabilities") or [])
                + [
                    cap
                    for step in validated_steps
                    for cap in _extract_step_capabilities(step)
                ]
            )

            existing = await self._session.execute(
                select(Preset)
                .where(
                    Preset.slug == normalized_slug,
                    Preset.scope_type == scope,
                    Preset.scope_ref == scope_ref,
                )
                .options(
                )
                .limit(1)
            )
            template = existing.scalar_one_or_none()

            if template is None:
                await self.create_template(
                    slug=normalized_slug,
                    title=title,
                    description=description,
                    scope=scope.value,
                    scope_ref=scope_ref,
                    tags=item.get("tags") or [],
                    inputs_schema=validated_inputs,
                    steps=validated_steps,
                    annotations=annotations,
                    required_capabilities=derived_capabilities,
                    created_by=None,
                    release_status=PresetReleaseStatus.ACTIVE,
                    seed_source=item.get("seedSource"),
                    auto_commit=False,
                )
                result.created += 1
                continue

            updated = False
            normalized_tags = _normalize_tag_list(item.get("tags") or [])
            if template.title != title:
                template.title = title
                updated = True
            if template.description != description:
                template.description = description
                updated = True
            if list(template.tags or []) != normalized_tags:
                template.tags = normalized_tags
                updated = True
            if list(template.required_capabilities or []) != list(derived_capabilities):
                template.required_capabilities = list(derived_capabilities)
                updated = True
            if not template.is_active:
                template.is_active = True
                updated = True

            if list(template.inputs_schema or []) != validated_inputs:
                template.inputs_schema = validated_inputs
                updated = True
            if list(template.steps or []) != validated_steps:
                template.steps = validated_steps
                updated = True
            if dict(template.annotations or {}) != annotations:
                template.annotations = annotations
                updated = True
            max_step_count = max(25, len(validated_steps))
            if int(template.max_step_count or 0) != max_step_count:
                template.max_step_count = max_step_count
                updated = True
            if template.release_status is not PresetReleaseStatus.ACTIVE:
                template.release_status = PresetReleaseStatus.ACTIVE
                updated = True
            if template.seed_source != item.get("seedSource"):
                template.seed_source = item.get("seedSource")
                updated = True
            if updated:
                result.updated += 1

        if result.created > 0 or result.updated > 0:
            await self._session.commit()

        return result
