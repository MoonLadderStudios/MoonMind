"""Canonical task payload models and normalization helpers for queue jobs."""

from __future__ import annotations

import json
import re
from typing import Any, Mapping
from urllib.parse import urlsplit

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from moonmind.config.settings import settings

from .job_types import CANONICAL_TASK_JOB_TYPE, LEGACY_TASK_JOB_TYPES

DEFAULT_TASK_RUNTIME = "codex"
SUPPORTED_RUNTIME_MODES = {"codex", "gemini", "claude", "universal"}
SUPPORTED_EXECUTION_RUNTIMES = {"codex", "gemini", "claude"}
SUPPORTED_PUBLISH_MODES = {"none", "branch", "pr"}
_SECRET_REF_MOUNT_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")
_SECRET_REF_PATH_PATTERN = re.compile(r"^[A-Za-z0-9._/-]+$")
_SECRET_REF_FIELD_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")
_CONTAINER_VOLUME_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_CONTAINER_RESERVED_ENV_KEYS = frozenset({"ARTIFACT_DIR", "JOB_ID", "REPOSITORY"})


class TaskContractError(ValueError):
    """Raised when queue payloads violate task contract requirements."""


def _clean_str(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _clean_optional_str(value: object) -> str | None:
    cleaned = _clean_str(value)
    return cleaned or None


def _default_publish_mode() -> str:
    mode = getattr(settings.spec_workflow, "default_publish_mode", "pr") or "pr"
    normalized = str(mode).strip().lower()
    return normalized if normalized in SUPPORTED_PUBLISH_MODES else "pr"


def _normalize_runtime_value(value: object, *, field_name: str) -> str | None:
    candidate = _clean_optional_str(value)
    if candidate is None:
        return None
    lowered = candidate.lower()
    if lowered not in SUPPORTED_RUNTIME_MODES:
        supported = ", ".join(sorted(SUPPORTED_RUNTIME_MODES))
        raise TaskContractError(f"{field_name} must be one of: {supported}")
    return lowered


def _normalize_publish_mode(value: object) -> str:
    candidate = (_clean_optional_str(value) or _default_publish_mode()).lower()
    if candidate not in SUPPORTED_PUBLISH_MODES:
        supported = ", ".join(sorted(SUPPORTED_PUBLISH_MODES))
        raise TaskContractError(f"publish.mode must be one of: {supported}")
    return candidate


def _normalize_capabilities(values: list[object] | tuple[object, ...]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in values:
        item = _clean_str(raw).lower()
        if not item or item in seen:
            continue
        normalized.append(item)
        seen.add(item)
    return normalized


def _normalize_secret_ref(value: object, *, field_name: str) -> str | None:
    """Validate and normalize optional secret references.

    Phase-5 hardening only permits Vault secret URIs so queue payloads never
    carry raw credentials.
    """

    candidate = _clean_optional_str(value)
    if candidate is None:
        return None
    if len(candidate) > 512:
        raise TaskContractError(f"{field_name} exceeds max length")

    parsed = urlsplit(candidate)
    if parsed.scheme.lower() != "vault":
        raise TaskContractError(f"{field_name} must use vault:// secret references")

    mount = parsed.netloc.strip()
    path = parsed.path.lstrip("/").strip()
    field = parsed.fragment.strip()
    if not mount or not path or not field:
        raise TaskContractError(
            f"{field_name} must include mount/path and #field (vault://<mount>/<path>#<field>)"
        )
    if not _SECRET_REF_MOUNT_PATTERN.fullmatch(mount):
        raise TaskContractError(f"{field_name} mount contains invalid characters")
    if not _SECRET_REF_PATH_PATTERN.fullmatch(path):
        raise TaskContractError(f"{field_name} path contains invalid characters")
    if any(segment in {"..", "."} for segment in path.split("/")):
        raise TaskContractError(f"{field_name} path traversal is not allowed")
    if not _SECRET_REF_FIELD_PATTERN.fullmatch(field):
        raise TaskContractError(f"{field_name} field contains invalid characters")
    return f"vault://{mount}/{path}#{field}"


class TaskSkillSelection(BaseModel):
    """Selected skill and optional skill argument object."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    id: str = Field("auto", alias="id")
    args: dict[str, Any] = Field(default_factory=dict, alias="args")
    required_capabilities: list[str] | None = Field(
        None,
        alias="requiredCapabilities",
    )

    @field_validator("id", mode="before")
    @classmethod
    def _normalize_id(cls, value: object) -> str:
        cleaned = _clean_optional_str(value) or "auto"
        return cleaned

    @field_validator("required_capabilities", mode="before")
    @classmethod
    def _normalize_required_capabilities(cls, value: object) -> list[str] | None:
        if value is None:
            return None
        if not isinstance(value, list):
            raise TaskContractError("task.skill.requiredCapabilities must be a list")
        normalized = _normalize_capabilities(value)
        return normalized or None


class TaskRuntimeSelection(BaseModel):
    """Runtime mode and optional model/effort overrides."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    mode: str | None = Field(
        None,
        alias="mode",
        validation_alias=AliasChoices("mode", "targetRuntime", "target_runtime"),
    )
    model: str | None = Field(None, alias="model")
    effort: str | None = Field(None, alias="effort")

    @field_validator("mode", mode="before")
    @classmethod
    def _normalize_mode(cls, value: object) -> str | None:
        return _normalize_runtime_value(value, field_name="task.runtime.mode")

    @field_validator("model", "effort", mode="before")
    @classmethod
    def _normalize_optional_strings(cls, value: object) -> str | None:
        return _clean_optional_str(value)


class TaskGitSelection(BaseModel):
    """Branch-selection values for task execution."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    starting_branch: str | None = Field(None, alias="startingBranch")
    new_branch: str | None = Field(None, alias="newBranch")

    @field_validator("starting_branch", "new_branch", mode="before")
    @classmethod
    def _normalize_branches(cls, value: object) -> str | None:
        return _clean_optional_str(value)


class TaskPublishSelection(BaseModel):
    """Publish controls for branch/pull-request behavior."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    mode: str = Field("pr", alias="mode")
    pr_base_branch: str | None = Field(
        None,
        alias="prBaseBranch",
        validation_alias=AliasChoices("prBaseBranch", "baseBranch"),
    )
    commit_message: str | None = Field(None, alias="commitMessage")
    pr_title: str | None = Field(None, alias="prTitle")
    pr_body: str | None = Field(None, alias="prBody")

    @field_validator("mode", mode="before")
    @classmethod
    def _normalize_mode(cls, value: object) -> str:
        return _normalize_publish_mode(value)

    @field_validator(
        "pr_base_branch",
        "commit_message",
        "pr_title",
        "pr_body",
        mode="before",
    )
    @classmethod
    def _normalize_optional_strings(cls, value: object) -> str | None:
        return _clean_optional_str(value)


class TaskAuthSelection(BaseModel):
    """Optional secret references for repo and publish authentication."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    repo_auth_ref: str | None = Field(None, alias="repoAuthRef")
    publish_auth_ref: str | None = Field(None, alias="publishAuthRef")

    @field_validator("repo_auth_ref", mode="before")
    @classmethod
    def _normalize_repo_auth_ref(cls, value: object) -> str | None:
        return _normalize_secret_ref(value, field_name="auth.repoAuthRef")

    @field_validator("publish_auth_ref", mode="before")
    @classmethod
    def _normalize_publish_auth_ref(cls, value: object) -> str | None:
        return _normalize_secret_ref(value, field_name="auth.publishAuthRef")


class TaskContainerCacheVolume(BaseModel):
    """Named volume mount requested by container-enabled task execution."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    name: str = Field(..., alias="name")
    target: str = Field(..., alias="target")

    @field_validator("name", mode="before")
    @classmethod
    def _normalize_name(cls, value: object) -> str:
        cleaned = _clean_optional_str(value)
        if not cleaned:
            raise TaskContractError("task.container.cacheVolumes[].name is required")
        if "," in cleaned or "=" in cleaned:
            raise TaskContractError(
                "task.container.cacheVolumes[].name contains invalid characters"
            )
        if not _CONTAINER_VOLUME_NAME_PATTERN.fullmatch(cleaned):
            raise TaskContractError(
                "task.container.cacheVolumes[].name has invalid format"
            )
        return cleaned

    @field_validator("target", mode="before")
    @classmethod
    def _normalize_target(cls, value: object) -> str:
        cleaned = _clean_optional_str(value)
        if not cleaned:
            raise TaskContractError("task.container.cacheVolumes[].target is required")
        if "," in cleaned:
            raise TaskContractError(
                "task.container.cacheVolumes[].target may not contain ','"
            )
        if not cleaned.startswith("/"):
            raise TaskContractError(
                "task.container.cacheVolumes[].target must be an absolute path"
            )
        return cleaned


class TaskContainerSelection(BaseModel):
    """Optional container execution controls for canonical tasks."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    enabled: bool = Field(False, alias="enabled")
    image: str | None = Field(None, alias="image")
    command: list[str] | None = Field(None, alias="command")
    workdir: str | None = Field(None, alias="workdir")
    env: dict[str, str] | None = Field(None, alias="env")
    artifacts_subdir: str | None = Field(None, alias="artifactsSubdir")
    timeout_seconds: int | None = Field(None, alias="timeoutSeconds")
    pull: str | None = Field(None, alias="pull")
    resources: dict[str, Any] | None = Field(None, alias="resources")
    cache_volumes: list[TaskContainerCacheVolume] | None = Field(
        None, alias="cacheVolumes"
    )

    @field_validator("enabled", mode="before")
    @classmethod
    def _normalize_enabled(cls, value: object) -> bool:
        if isinstance(value, bool):
            return value
        candidate = _clean_optional_str(value)
        if not candidate:
            return False
        lowered = candidate.lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
        raise TaskContractError("task.container.enabled must be a boolean")

    @field_validator("image", "workdir", "artifacts_subdir", "pull", mode="before")
    @classmethod
    def _normalize_optional_strings(cls, value: object) -> str | None:
        return _clean_optional_str(value)

    @field_validator("command", mode="before")
    @classmethod
    def _normalize_command(cls, value: object) -> list[str] | None:
        if value is None:
            return None
        if not isinstance(value, list):
            raise TaskContractError("task.container.command must be a list")
        normalized: list[str] = []
        for raw in value:
            item = _clean_optional_str(raw)
            if item is None:
                continue
            normalized.append(item)
        return normalized or None

    @field_validator("env", mode="before")
    @classmethod
    def _normalize_env(cls, value: object) -> dict[str, str] | None:
        if value is None:
            return None
        if not isinstance(value, Mapping):
            raise TaskContractError("task.container.env must be an object")
        normalized: dict[str, str] = {}
        for raw_key, raw_value in value.items():
            key = _clean_optional_str(raw_key)
            if key is None:
                continue
            if "=" in key:
                raise TaskContractError("task.container.env keys may not contain '='")
            if key.upper() in _CONTAINER_RESERVED_ENV_KEYS:
                raise TaskContractError(
                    f"task.container.env may not override reserved key '{key}'"
                )
            normalized[key] = _clean_str(raw_value)
        return normalized or None

    @field_validator("timeout_seconds", mode="before")
    @classmethod
    def _normalize_timeout_seconds(cls, value: object) -> int | None:
        if value is None or value == "":
            return None
        try:
            timeout = int(value)
        except (TypeError, ValueError) as exc:
            raise TaskContractError(
                "task.container.timeoutSeconds must be an integer"
            ) from exc
        if timeout < 1:
            raise TaskContractError(
                "task.container.timeoutSeconds must be greater than zero"
            )
        return timeout

    @field_validator("pull", mode="after")
    @classmethod
    def _validate_pull_mode(cls, value: str | None) -> str | None:
        if value is None:
            return None
        lowered = value.lower()
        if lowered not in {"if-missing", "always"}:
            raise TaskContractError("task.container.pull must be if-missing or always")
        return lowered

    @model_validator(mode="after")
    def _validate_enabled_requirements(self) -> "TaskContainerSelection":
        if not self.enabled:
            return self
        if not self.image:
            raise TaskContractError(
                "task.container.image is required when task.container.enabled=true"
            )
        if not self.command:
            raise TaskContractError(
                "task.container.command is required when task.container.enabled=true"
            )
        return self


class TaskStepSpec(BaseModel):
    """Optional execution step contained within a canonical task payload."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    id: str | None = Field(None, alias="id")
    title: str | None = Field(None, alias="title")
    instructions: str | None = Field(None, alias="instructions")
    skill: TaskSkillSelection | None = Field(None, alias="skill")

    @field_validator("id", "title", "instructions", mode="before")
    @classmethod
    def _normalize_optional_strings(cls, value: object) -> str | None:
        return _clean_optional_str(value)

    @model_validator(mode="before")
    @classmethod
    def _reject_forbidden_step_overrides(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        payload = dict(value)
        forbidden = {
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
        }
        blocked = sorted(key for key in payload.keys() if str(key).strip() in forbidden)
        if blocked:
            formatted = ", ".join(blocked)
            raise TaskContractError(
                f"task.steps entries may not define task-scoped overrides: {formatted}"
            )
        return payload


class TaskExecutionSpec(BaseModel):
    """Main task execution body."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    instructions: str = Field(
        ...,
        alias="instructions",
        validation_alias=AliasChoices("instructions", "instruction"),
    )
    skill: TaskSkillSelection = Field(default_factory=TaskSkillSelection, alias="skill")
    runtime: TaskRuntimeSelection = Field(
        default_factory=TaskRuntimeSelection, alias="runtime"
    )
    git: TaskGitSelection = Field(default_factory=TaskGitSelection, alias="git")
    publish: TaskPublishSelection = Field(
        default_factory=TaskPublishSelection, alias="publish"
    )
    steps: list[TaskStepSpec] = Field(default_factory=list, alias="steps")
    container: TaskContainerSelection | None = Field(None, alias="container")

    @field_validator("instructions", mode="before")
    @classmethod
    def _normalize_instructions(cls, value: object) -> str:
        cleaned = _clean_optional_str(value)
        if not cleaned:
            raise TaskContractError("task.instructions is required")
        return cleaned

    @model_validator(mode="before")
    @classmethod
    def _lift_legacy_task_shape(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        payload = dict(value)
        runtime_node = payload.get("runtime")
        if isinstance(runtime_node, str):
            payload["runtime"] = {"mode": runtime_node}
        elif runtime_node is None:
            legacy_runtime = (
                payload.get("targetRuntime")
                or payload.get("target_runtime")
                or payload.get("runtime")
            )
            if isinstance(legacy_runtime, str) and legacy_runtime.strip():
                payload["runtime"] = {"mode": legacy_runtime}
        return payload

    @field_validator("steps", mode="before")
    @classmethod
    def _normalize_steps(cls, value: object) -> list[object]:
        if value is None or value == "":
            return []
        if not isinstance(value, list):
            raise TaskContractError("task.steps must be a list")
        return list(value)

    @model_validator(mode="after")
    def _validate_container_steps_compatibility(self) -> "TaskExecutionSpec":
        if self.container is None:
            return self
        if self.container.enabled and self.steps:
            raise TaskContractError(
                "task.steps is not supported when task.container.enabled=true"
            )
        return self


class CanonicalTaskPayload(BaseModel):
    """Top-level canonical queue payload for `type=task` jobs."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    repository: str = Field(
        ...,
        alias="repository",
        validation_alias=AliasChoices("repository", "repo"),
    )
    required_capabilities: list[str] | None = Field(
        None,
        alias="requiredCapabilities",
    )
    target_runtime: str | None = Field(
        None,
        alias="targetRuntime",
        validation_alias=AliasChoices("targetRuntime", "target_runtime"),
    )
    auth: TaskAuthSelection | None = Field(
        None,
        alias="auth",
    )
    task: TaskExecutionSpec = Field(..., alias="task")

    @field_validator("repository", mode="before")
    @classmethod
    def _normalize_repository(cls, value: object) -> str:
        cleaned = _clean_optional_str(value)
        if not cleaned:
            raise TaskContractError("repository is required")
        return cleaned

    @field_validator("target_runtime", mode="before")
    @classmethod
    def _normalize_target_runtime(cls, value: object) -> str | None:
        return _normalize_runtime_value(value, field_name="targetRuntime")

    @field_validator("required_capabilities", mode="before")
    @classmethod
    def _normalize_required_capabilities(cls, value: object) -> list[str] | None:
        if value is None:
            return None
        if not isinstance(value, list):
            raise TaskContractError("requiredCapabilities must be a list")
        normalized = _normalize_capabilities(value)
        return normalized or None

    @model_validator(mode="before")
    @classmethod
    def _lift_legacy_top_level_shape(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        payload = dict(value)
        task_node = payload.get("task")
        if not isinstance(task_node, Mapping):
            legacy_instruction = (
                payload.get("instructions") or payload.get("instruction") or ""
            )
            payload["task"] = {
                "instructions": legacy_instruction,
                "runtime": {
                    "mode": payload.get("targetRuntime")
                    or payload.get("target_runtime")
                    or payload.get("runtime")
                },
            }
        else:
            task_node = dict(task_node)
            if not task_node.get("instructions"):
                lifted_instruction = payload.get("instructions") or payload.get(
                    "instruction"
                )
                if lifted_instruction:
                    task_node["instructions"] = lifted_instruction
            payload["task"] = task_node
        return payload


def _build_task_from_codex_exec_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    publish_raw = payload.get("publish")
    publish = publish_raw if isinstance(publish_raw, Mapping) else {}
    codex_raw = payload.get("codex")
    codex = codex_raw if isinstance(codex_raw, Mapping) else {}

    return {
        "instructions": _clean_optional_str(payload.get("instruction"))
        or "Legacy codex_exec job",
        "skill": {"id": "auto", "args": {}},
        "runtime": {
            "mode": "codex",
            "model": _clean_optional_str(codex.get("model")),
            "effort": _clean_optional_str(codex.get("effort")),
        },
        "git": {
            "startingBranch": _clean_optional_str(payload.get("ref")),
            "newBranch": None,
        },
        "publish": {
            "mode": _normalize_publish_mode(publish.get("mode") or "none"),
            "prBaseBranch": _clean_optional_str(
                publish.get("prBaseBranch") or publish.get("baseBranch")
            ),
            "commitMessage": None,
            "prTitle": None,
            "prBody": None,
        },
    }


def _build_auth_from_payload(payload: Mapping[str, Any]) -> dict[str, Any] | None:
    """Normalize optional auth secret reference object from source payload."""

    raw_auth = payload.get("auth")
    if not isinstance(raw_auth, Mapping):
        return None
    try:
        auth = TaskAuthSelection.model_validate(dict(raw_auth))
    except (ValidationError, TaskContractError) as exc:
        raise TaskContractError(str(exc)) from exc
    return auth.model_dump(by_alias=True, exclude_none=False)


def _build_task_from_codex_skill_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    raw_inputs = payload.get("inputs")
    inputs = dict(raw_inputs) if isinstance(raw_inputs, Mapping) else {}
    codex_raw = payload.get("codex")
    codex = codex_raw if isinstance(codex_raw, Mapping) else {}
    input_codex_raw = inputs.get("codex")
    input_codex = input_codex_raw if isinstance(input_codex_raw, Mapping) else {}

    skill_id = _clean_optional_str(payload.get("skillId")) or "speckit"
    repository = (
        _clean_optional_str(inputs.get("repo"))
        or _clean_optional_str(inputs.get("repository"))
        or _clean_optional_str(payload.get("repository"))
        or ""
    )
    instruction = (
        _clean_optional_str(inputs.get("instruction"))
        or _clean_optional_str(payload.get("instruction"))
        or f"Execute skill '{skill_id}' with inputs:\n"
        + json.dumps(inputs, indent=2, sort_keys=True)
    )
    publish_mode = (
        _clean_optional_str(inputs.get("publishMode"))
        or _clean_optional_str(payload.get("publishMode"))
        or "none"
    )
    publish_base = (
        _clean_optional_str(inputs.get("publishBaseBranch"))
        or _clean_optional_str(payload.get("publishBaseBranch"))
        or None
    )
    ref = _clean_optional_str(inputs.get("ref")) or _clean_optional_str(
        payload.get("ref")
    )

    task = {
        "instructions": instruction,
        "skill": {"id": skill_id, "args": dict(inputs)},
        "runtime": {
            "mode": "codex",
            "model": _clean_optional_str(codex.get("model"))
            or _clean_optional_str(input_codex.get("model")),
            "effort": _clean_optional_str(codex.get("effort"))
            or _clean_optional_str(input_codex.get("effort")),
        },
        "git": {
            "startingBranch": ref,
            "newBranch": None,
        },
        "publish": {
            "mode": _normalize_publish_mode(publish_mode),
            "prBaseBranch": publish_base,
            "commitMessage": None,
            "prTitle": None,
            "prBody": None,
        },
    }
    if repository:
        task["skill"]["args"].setdefault("repo", repository)
    return task


def build_canonical_task_view(
    *,
    job_type: str,
    payload: Mapping[str, Any] | None,
    default_runtime: str = DEFAULT_TASK_RUNTIME,
) -> dict[str, Any]:
    """Return a canonical task-view payload for queue processing."""

    source = dict(payload or {})
    normalized_type = _clean_str(job_type)
    resolved_default_runtime = _normalize_runtime_value(
        default_runtime, field_name="default runtime"
    )
    if resolved_default_runtime in {None, "universal"}:
        resolved_default_runtime = DEFAULT_TASK_RUNTIME

    if normalized_type == CANONICAL_TASK_JOB_TYPE:
        try:
            model = CanonicalTaskPayload.model_validate(source)
        except (ValidationError, TaskContractError) as exc:
            raise TaskContractError(str(exc)) from exc
        canonical = model.model_dump(by_alias=True, exclude_none=False)
    elif normalized_type == "codex_exec":
        repository = _clean_optional_str(source.get("repository")) or ""
        if not repository:
            raise TaskContractError("repository is required")
        canonical = {
            "repository": repository,
            "targetRuntime": "codex",
            "auth": _build_auth_from_payload(source),
            "task": _build_task_from_codex_exec_payload(source),
        }
    elif normalized_type == "codex_skill":
        repository = (
            _clean_optional_str(source.get("repository"))
            or _clean_optional_str(
                (source.get("inputs") or {}).get("repo")
                if isinstance(source.get("inputs"), Mapping)
                else None
            )
            or _clean_optional_str(
                (source.get("inputs") or {}).get("repository")
                if isinstance(source.get("inputs"), Mapping)
                else None
            )
            or ""
        )
        if not repository:
            raise TaskContractError("repository is required")
        canonical = {
            "repository": repository,
            "targetRuntime": "codex",
            "auth": _build_auth_from_payload(source),
            "task": _build_task_from_codex_skill_payload(source),
        }
    else:
        canonical = {
            "repository": _clean_optional_str(source.get("repository")) or "",
            "targetRuntime": resolved_default_runtime,
            "auth": _build_auth_from_payload(source),
            "task": {
                "instructions": _clean_optional_str(source.get("instruction"))
                or "Queue job",
                "skill": {"id": "auto", "args": {}},
                "runtime": {
                    "mode": resolved_default_runtime,
                    "model": None,
                    "effort": None,
                },
                "git": {"startingBranch": None, "newBranch": None},
                "publish": {
                    "mode": "none",
                    "prBaseBranch": None,
                    "commitMessage": None,
                    "prTitle": None,
                    "prBody": None,
                },
            },
        }

    target_runtime = (
        _normalize_runtime_value(
            canonical.get("targetRuntime"), field_name="targetRuntime"
        )
        or _normalize_runtime_value(
            ((canonical.get("task") or {}).get("runtime") or {}).get("mode"),
            field_name="task.runtime.mode",
        )
        or resolved_default_runtime
    )
    if target_runtime == "universal":
        target_runtime = resolved_default_runtime

    runtime_node = (canonical.get("task") or {}).get("runtime")
    if not isinstance(runtime_node, dict):
        runtime_node = {}
        canonical.setdefault("task", {})["runtime"] = runtime_node
    runtime_node["mode"] = target_runtime
    canonical["targetRuntime"] = target_runtime

    required = []
    existing = source.get("requiredCapabilities")
    if isinstance(existing, list):
        required.extend(existing)
    canonical_existing = canonical.get("requiredCapabilities")
    if isinstance(canonical_existing, list):
        required.extend(canonical_existing)

    required.append(target_runtime)
    required.append("git")

    publish_mode = _normalize_publish_mode(
        ((canonical.get("task") or {}).get("publish") or {}).get("mode")
    )
    canonical["task"]["publish"]["mode"] = publish_mode
    if publish_mode == "pr":
        required.append("gh")

    skill_node = (canonical.get("task") or {}).get("skill") or {}
    skill_caps = skill_node.get("requiredCapabilities")
    if isinstance(skill_caps, list):
        required.extend(skill_caps)

    steps_node = (canonical.get("task") or {}).get("steps")
    if isinstance(steps_node, list):
        for step_raw in steps_node:
            if not isinstance(step_raw, Mapping):
                continue
            step_skill_raw = step_raw.get("skill")
            step_skill = step_skill_raw if isinstance(step_skill_raw, Mapping) else {}
            step_skill_caps = step_skill.get("requiredCapabilities")
            if isinstance(step_skill_caps, list):
                required.extend(step_skill_caps)

    container_node = (canonical.get("task") or {}).get("container")
    container = container_node if isinstance(container_node, Mapping) else {}
    if bool(container.get("enabled")):
        required.append("docker")

    canonical["requiredCapabilities"] = _normalize_capabilities(tuple(required))
    return canonical


def normalize_queue_job_payload(
    *,
    job_type: str,
    payload: Mapping[str, Any] | None,
    default_runtime: str = DEFAULT_TASK_RUNTIME,
) -> dict[str, Any]:
    """Normalize queue payloads while preserving legacy compatibility fields."""

    source = dict(payload or {})
    normalized_type = _clean_str(job_type)
    canonical = build_canonical_task_view(
        job_type=normalized_type,
        payload=source,
        default_runtime=default_runtime,
    )

    if normalized_type == CANONICAL_TASK_JOB_TYPE:
        return canonical

    if normalized_type in LEGACY_TASK_JOB_TYPES:
        source["repository"] = canonical.get("repository")
        source["targetRuntime"] = canonical.get("targetRuntime")
        source["auth"] = canonical.get("auth")
        source["requiredCapabilities"] = canonical.get("requiredCapabilities")
        source["task"] = canonical.get("task")
        return source

    required = source.get("requiredCapabilities")
    if isinstance(required, list):
        source["requiredCapabilities"] = _normalize_capabilities(tuple(required))
    return source


def build_task_stage_plan(canonical_payload: Mapping[str, Any]) -> list[str]:
    """Return ordered stage identifiers for canonical task execution."""

    task_node = canonical_payload.get("task")
    task = task_node if isinstance(task_node, Mapping) else {}
    publish_node = task.get("publish")
    publish = publish_node if isinstance(publish_node, Mapping) else {}
    publish_mode = _normalize_publish_mode(publish.get("mode"))

    stages = ["moonmind.task.prepare", "moonmind.task.execute"]
    if publish_mode != "none":
        stages.append("moonmind.task.publish")
    return stages


__all__ = [
    "CANONICAL_TASK_JOB_TYPE",
    "DEFAULT_TASK_RUNTIME",
    "LEGACY_TASK_JOB_TYPES",
    "SUPPORTED_EXECUTION_RUNTIMES",
    "CanonicalTaskPayload",
    "TaskContractError",
    "build_task_stage_plan",
    "build_canonical_task_view",
    "normalize_queue_job_payload",
]
