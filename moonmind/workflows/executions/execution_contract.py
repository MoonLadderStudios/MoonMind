"""Canonical workflow execution payload models and normalization helpers."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Literal, Mapping, Sequence
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
from moonmind.services.skill_resolution import (
    extract_publish_metadata_from_skill_markdown,
    extract_required_capabilities_from_skill_markdown,
)
from moonmind.workflows.skills.resolver import (
    SkillResolutionError,
    resolve_skill_markdown_path,
)

from .job_types import CANONICAL_WORKFLOW_JOB_TYPE, LEGACY_WORKFLOW_JOB_TYPES

DEFAULT_WORKFLOW_RUNTIME = "codex"
SUPPORTED_RUNTIME_MODES = {
    "codex",
    "codex_cli",
    "codex_cloud",
    "claude",
    "claude_code",
    "jules",
    "omnigent",
    "universal",
}
SUPPORTED_EXECUTION_RUNTIMES = {
    "codex",
    "codex_cli",
    "claude",
    "claude_code",
    "jules",
}
SUPPORTED_PUBLISH_MODES = {"auto", "none", "branch", "pr"}
_SECRET_REF_MOUNT_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")
_SECRET_REF_PATH_PATTERN = re.compile(r"^[A-Za-z0-9._/-]+$")
_SECRET_REF_FIELD_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")
_CONTAINER_VOLUME_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_CONTAINER_RESERVED_ENV_KEYS = frozenset({"ARTIFACT_DIR", "JOB_ID", "REPOSITORY"})
_PROPOSAL_POLICY_TARGETS = ("workflow_repo", "moonmind")
_PROPOSAL_SEVERITIES = ("low", "medium", "high", "critical")
_AUTO_PUBLISH_MIGRATION_FALLBACK_SKILLS = frozenset(
    {
        "pr-resolver",
        "fix-comments",
        "fix-ci",
        "fix-merge-conflicts",
    }
)
_NON_REPOSITORY_SIDE_EFFECT_SKILLS = frozenset(
    {
        "batch-pr-resolver",
        "batch-dependabot-resolver",
        "batch-workflows",
        "jira-issue-creator",
        "jira-issue-updater",
        "jira-pr-verify",
        "jira-verify",
    }
)
_REPOSITORY_PUBLISH_COMPATIBLE_SIDE_EFFECT_SKILLS = frozenset({"jira-issue-updater"})
_JIRA_ORCHESTRATE_PRESET_SLUGS = frozenset({"jira-orchestrate"})
_RUNTIME_COMMAND_HINT_CATALOG_VERSION = "2026-05-13"
_SLASH_COMMAND_PASSTHROUGH_RUNTIMES = frozenset(
    {"codex", "codex_cli", "claude", "claude_code", "universal"}
)
_KNOWN_RUNTIME_COMMAND_HINTS = frozenset({"review", "simplify"})
_RUNTIME_COMMAND_HINT_DETAILS: dict[str, dict[str, Any]] = {
    "review": {
        "label": "Review",
        "aliases": ["/review"],
        "description": (
            "Ask the selected runtime to review the current task or code state."
        ),
        "argumentPolicy": {"allowed": True, "required": False},
        "bodyPolicy": {"allowed": True, "required": False},
    },
    "simplify": {
        "label": "Simplify",
        "aliases": ["/simplify"],
        "description": "Ask the selected runtime to simplify the implementation.",
        "argumentPolicy": {"allowed": True, "required": False},
        "bodyPolicy": {"allowed": True, "required": False},
    },
}
_RUNTIME_COMMAND_TOKEN_PATTERN = re.compile(
    r"^/([A-Za-z][A-Za-z0-9_-]*(?:(?::|\.)[A-Za-z0-9_-]+)?)(?:\s+(.*))?$"
)
_RESOLVE_PR_OBJECTIVE_PATTERN = re.compile(
    r"\bresolve(?:d|s|ing)?\s+(?:an?\s+|the\s+)?(?:pr|pull\s+request)\b",
    re.IGNORECASE,
)
_NO_COMMIT_PUSH_PATTERN = re.compile(
    r"\bdo\s+not\s+commit(?:\s+or\s+push|/push)\b",
    re.IGNORECASE,
)
_DATA_IMAGE_URL_PATTERN = re.compile(r"^data:image/", re.IGNORECASE)
_EMBEDDED_ATTACHMENT_DATA_FIELDS = frozenset(
    {
        "base64",
        "bytes",
        "content",
        "data",
        "data_url",
        "dataUrl",
        "dataURL",
        "image_data",
        "imageData",
        "raw",
        "rawBytes",
    }
)
_TOOL_IDENTITY_VERSION_KEYS = frozenset({"version", "toolVersion"})
_SKILL_IDENTITY_VERSION_KEYS = frozenset({"version", "skillVersion"})
_PRESET_IDENTITY_VERSION_KEYS = frozenset({"version", "presetVersion"})
_CAPABILITY_IDENTITY_VERSION_KEYS = (
    _TOOL_IDENTITY_VERSION_KEYS
    | _SKILL_IDENTITY_VERSION_KEYS
    | _PRESET_IDENTITY_VERSION_KEYS
)
_SKILL_METADATA_CAPABILITY_CACHE: dict[str, tuple[str, ...]] = {}
_SKILL_PUBLISH_METADATA_CACHE: dict[str, dict[str, Any] | None] = {}

class WorkflowContractError(ValueError):
    """Raised when queue payloads violate task contract requirements."""

    def __init__(
        self, message: str, *, diagnostic: Mapping[str, object] | None = None
    ) -> None:
        super().__init__(message)
        self.diagnostic = dict(diagnostic) if diagnostic is not None else None

def _clean_str(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()

def _clean_optional_str(value: object) -> str | None:
    cleaned = _clean_str(value)
    return cleaned or None


def _strip_identity_version_keys(
    value: Mapping[str, Any],
    keys: frozenset[str],
) -> dict[str, Any]:
    payload = dict(value)
    for key in keys:
        payload.pop(key, None)
    return payload


def _reject_identity_version_keys(
    value: Mapping[str, Any],
    keys: frozenset[str],
    *,
    field_path: str,
) -> None:
    present = sorted(key for key in keys if key in value)
    if present:
        raise WorkflowContractError(
            f"{field_path} must not include semantic versions for capability selectors: "
            + ", ".join(present)
        )


def _strip_preset_identity_versions(value: Mapping[str, Any]) -> dict[str, Any]:
    payload = _strip_identity_version_keys(value, _PRESET_IDENTITY_VERSION_KEYS)
    composition = payload.get("composition")
    if isinstance(composition, Mapping):
        payload["composition"] = _strip_preset_identity_versions(composition)
    for list_key in (
        "includes",
        "authoredPresets",
        "authored_presets",
        "appliedStepTemplates",
        "applied_step_templates",
    ):
        items = payload.get(list_key)
        if isinstance(items, list):
            payload[list_key] = [
                _strip_preset_identity_versions(item)
                if isinstance(item, Mapping)
                else item
                for item in items
            ]
    return payload


def _reject_preset_identity_versions(
    value: Mapping[str, Any],
    *,
    field_path: str,
) -> None:
    _reject_identity_version_keys(
        value,
        _PRESET_IDENTITY_VERSION_KEYS,
        field_path=field_path,
    )
    composition = value.get("composition")
    if isinstance(composition, Mapping):
        _reject_preset_identity_versions(
            composition,
            field_path=f"{field_path}.composition",
        )
    for list_key in (
        "includes",
        "authoredPresets",
        "authored_presets",
        "appliedStepTemplates",
        "applied_step_templates",
    ):
        items = value.get(list_key)
        if isinstance(items, list):
            for index, item in enumerate(items):
                if isinstance(item, Mapping):
                    _reject_preset_identity_versions(
                        item,
                        field_path=f"{field_path}.{list_key}[{index}]",
                    )


def strip_workflow_capability_identity_versions(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Drop stale semantic version fields from authored capability selectors."""

    sanitized = dict(payload)
    for key in ("tool", "skill"):
        node = sanitized.get(key)
        if isinstance(node, Mapping):
            sanitized[key] = _strip_identity_version_keys(
                node,
                _CAPABILITY_IDENTITY_VERSION_KEYS,
            )
    skills = sanitized.get("skills")
    if isinstance(skills, Mapping):
        sanitized["skills"] = {
            list_key: [
                _strip_identity_version_keys(item, _SKILL_IDENTITY_VERSION_KEYS)
                if isinstance(item, Mapping)
                else item
                for item in items
            ]
            if isinstance(items, list)
            else items
            for list_key, items in skills.items()
        }
    for key in (
        "taskTemplate",
        "task_template",
        "presetSchedule",
        "preset_schedule",
        "source",
    ):
        node = sanitized.get(key)
        if isinstance(node, Mapping):
            sanitized[key] = _strip_preset_identity_versions(node)
    for key in (
        "appliedStepTemplates",
        "applied_step_templates",
        "authoredPresets",
        "authored_presets",
    ):
        items = sanitized.get(key)
        if isinstance(items, list):
            sanitized[key] = [
                _strip_preset_identity_versions(item)
                if isinstance(item, Mapping)
                else item
                for item in items
            ]
    steps = sanitized.get("steps")
    if isinstance(steps, list):
        sanitized["steps"] = [
            strip_workflow_capability_identity_versions(step)
            if isinstance(step, Mapping)
            else step
            for step in steps
        ]
    return sanitized


def reject_workflow_capability_identity_versions(
    payload: Mapping[str, Any],
    *,
    field_path: str = "workflow",
) -> None:
    """Reject stale semantic version fields in newly authored capability selectors."""

    for key in ("tool", "skill"):
        node = payload.get(key)
        if isinstance(node, Mapping):
            _reject_identity_version_keys(
                node,
                _CAPABILITY_IDENTITY_VERSION_KEYS,
                field_path=f"{field_path}.{key}",
            )
    skills = payload.get("skills")
    if isinstance(skills, Mapping):
        for list_key, items in skills.items():
            if isinstance(items, list):
                for index, item in enumerate(items):
                    if isinstance(item, Mapping):
                        _reject_identity_version_keys(
                            item,
                            _SKILL_IDENTITY_VERSION_KEYS,
                            field_path=f"{field_path}.skills.{list_key}[{index}]",
                        )
    for key in (
        "taskTemplate",
        "task_template",
        "presetSchedule",
        "preset_schedule",
        "source",
    ):
        node = payload.get(key)
        if isinstance(node, Mapping):
            _reject_preset_identity_versions(node, field_path=f"{field_path}.{key}")
    for key in (
        "appliedStepTemplates",
        "applied_step_templates",
        "authoredPresets",
        "authored_presets",
    ):
        items = payload.get(key)
        if isinstance(items, list):
            for index, item in enumerate(items):
                if isinstance(item, Mapping):
                    _reject_preset_identity_versions(
                        item,
                        field_path=f"{field_path}.{key}[{index}]",
                    )
    steps = payload.get("steps")
    if isinstance(steps, list):
        for index, step in enumerate(steps):
            if isinstance(step, Mapping):
                reject_workflow_capability_identity_versions(
                    step,
                    field_path=f"{field_path}.steps[{index}]",
                )


def _skill_metadata_required_capabilities(skill_id: object) -> tuple[str, ...]:
    normalized = _clean_optional_str(skill_id)
    if not normalized:
        return ()
    cache_key = normalized.strip().lower()
    cached = _SKILL_METADATA_CAPABILITY_CACHE.get(cache_key)
    if cached is not None:
        return cached
    try:
        skill_file = resolve_skill_markdown_path(normalized)
    except (SkillResolutionError, OSError, ValueError):
        _SKILL_METADATA_CAPABILITY_CACHE[cache_key] = ()
        return ()
    if skill_file is None:
        _SKILL_METADATA_CAPABILITY_CACHE[cache_key] = ()
        return ()
    try:
        markdown = skill_file.read_text(encoding="utf-8")
        required = extract_required_capabilities_from_skill_markdown(
            markdown,
            skill_name=normalized,
            source_label=str(skill_file),
        )
    except (OSError, ValueError):
        required = ()
    _SKILL_METADATA_CAPABILITY_CACHE[cache_key] = tuple(required)
    return tuple(required)


def _contains_data_image_url(value: object) -> bool:
    if isinstance(value, str):
        return _DATA_IMAGE_URL_PATTERN.match(value.strip()) is not None
    if isinstance(value, Mapping):
        return any(_contains_data_image_url(item) for item in value.values())
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return any(_contains_data_image_url(item) for item in value)
    return False

def _attachment_validation_failed_diagnostic(
    *,
    attachment: Mapping[str, object] | None,
    target_kind: str,
    error: str,
    step_ref: str | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "event": "attachment_validation_failed",
        "status": "failed",
        "targetKind": target_kind,
        "error": error,
    }
    if step_ref:
        payload["stepRef"] = step_ref
    if attachment is not None:
        for source_keys, output_key in (
            (("artifactId", "artifact_id"), "artifactId"),
            (("filename",), "filename"),
            (("contentType", "content_type"), "contentType"),
            (("sizeBytes", "size_bytes"), "sizeBytes"),
        ):
            value = next(
                (
                    attachment.get(source_key)
                    for source_key in source_keys
                    if attachment.get(source_key) is not None
                    and attachment.get(source_key) != ""
                ),
                None,
            )
            if value is not None and value != "":
                payload[output_key] = value
    return payload

def _attachment_payload_value(
    payload: Mapping[str, object], *field_names: str
) -> object:
    for field_name in field_names:
        value = payload.get(field_name)
        if _clean_optional_str(value):
            return value
    return None

def _raise_attachment_validation_error(
    message: str,
    *,
    attachment: Mapping[str, object] | None,
    target_kind: str,
    step_ref: str | None = None,
) -> None:
    raise WorkflowContractError(
        message,
        diagnostic=_attachment_validation_failed_diagnostic(
            attachment=attachment,
            target_kind=target_kind,
            step_ref=step_ref,
            error=message,
        ),
    )

def _validate_input_attachment_payloads(
    value: object,
    *,
    list_error: str,
    target_kind: str,
    step_ref: str | None = None,
) -> object:
    if value is None or value == "":
        return []
    if not isinstance(value, list):
        _raise_attachment_validation_error(
            list_error,
            attachment=None,
            target_kind=target_kind,
            step_ref=step_ref,
        )
    for raw in value:
        if not isinstance(raw, Mapping):
            _raise_attachment_validation_error(
                "inputAttachments entries must be objects",
                attachment=None,
                target_kind=target_kind,
                step_ref=step_ref,
            )
        payload = dict(raw)
        blocked = sorted(
            key
            for key in payload
            if str(key).strip() in _EMBEDDED_ATTACHMENT_DATA_FIELDS
        )
        if blocked or _contains_data_image_url(payload):
            _raise_attachment_validation_error(
                "inputAttachments entries must not include embedded image data",
                attachment=payload,
                target_kind=target_kind,
                step_ref=step_ref,
            )
        missing = [
            field_name
            for field_name, aliases in (
                ("artifactId", ("artifactId", "artifact_id")),
                ("filename", ("filename",)),
                ("contentType", ("contentType", "content_type")),
            )
            if _attachment_payload_value(payload, *aliases) is None
        ]
        if missing:
            _raise_attachment_validation_error(
                "inputAttachments entries require artifactId, filename, and contentType",
                attachment=payload,
                target_kind=target_kind,
                step_ref=step_ref,
            )
    return value

def _default_publish_mode() -> str:
    mode = getattr(settings.workflow, "default_publish_mode", "pr") or "pr"
    normalized = str(mode).strip().lower()
    return normalized if normalized in SUPPORTED_PUBLISH_MODES else "pr"

def _default_propose_tasks() -> bool:
    """Default proposal generation to explicit workflow opt-in."""

    return False

def _normalize_runtime_value(value: object, *, field_name: str) -> str | None:
    candidate = _clean_optional_str(value)
    if candidate is None:
        return None
    lowered = candidate.lower()
    if lowered not in SUPPORTED_RUNTIME_MODES:
        supported = ", ".join(sorted(SUPPORTED_RUNTIME_MODES))
        raise WorkflowContractError(f"{field_name} must be one of: {supported}")
    return lowered


def _raw_instruction_string(value: object) -> str:
    if value is None:
        return ""
    return value if isinstance(value, str) else str(value)


def _first_line_and_body(raw_instructions: str) -> tuple[str, str]:
    lines = raw_instructions.splitlines()
    if not lines:
        return "", ""
    return lines[0].rstrip(), "\n".join(lines[1:])


def _runtime_mode_from_spec(
    task: Mapping[str, Any], *, target_runtime: object = None
) -> str | None:
    runtime = _safe_mapping(task.get("runtime"))
    candidate = (
        runtime.get("mode")
        or task.get("targetRuntime")
        or task.get("target_runtime")
        or target_runtime
    )
    candidate_str = _clean_optional_str(candidate)
    return candidate_str.lower() if candidate_str else None


def _runtime_supports_slash_passthrough(runtime_mode: str | None) -> bool:
    return (runtime_mode or DEFAULT_WORKFLOW_RUNTIME) in _SLASH_COMMAND_PASSTHROUGH_RUNTIMES


def build_runtime_command_preview_config() -> dict[str, Any]:
    """Return browser-safe slash-command preview capabilities and hints."""

    runtime_ids = sorted(_SLASH_COMMAND_PASSTHROUGH_RUNTIMES | {"codex_cloud"})
    runtimes: dict[str, dict[str, Any]] = {}
    for runtime_id in runtime_ids:
        supports_passthrough = _runtime_supports_slash_passthrough(runtime_id)
        runtimes[runtime_id] = {
            "slashCommandPassthrough": supports_passthrough,
            "renderMode": "prompt_prefix" if supports_passthrough else "plain_prompt",
            **(
                {"commandHintsRef": runtime_id}
                if supports_passthrough
                else {}
            ),
        }
    return {
        "hintCatalogVersion": _RUNTIME_COMMAND_HINT_CATALOG_VERSION,
        "runtimes": runtimes,
        "knownRuntimeCommandHints": {
            command: dict(_RUNTIME_COMMAND_HINT_DETAILS[command])
            for command in sorted(_KNOWN_RUNTIME_COMMAND_HINTS)
        },
    }


def _runtime_command_hint_status(command: str) -> str:
    return "hinted" if command in _KNOWN_RUNTIME_COMMAND_HINTS else "opaque"


def _looks_like_ordinary_path(first_line: str) -> bool:
    token = first_line.split(maxsplit=1)[0]
    if not token.startswith("/"):
        return False
    without_slash = token[1:]
    return "/" in without_slash or without_slash.startswith(".")


def _base_runtime_command_payload(
    *,
    source_path: str,
    target_runtime: str | None,
    target_step_id: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "kind": "slash_command",
        "source": "leading_slash",
        "sourcePath": source_path,
    }
    if target_runtime:
        payload["targetRuntime"] = target_runtime
    if target_step_id:
        payload["targetStepId"] = target_step_id
    return payload


def _build_runtime_command_metadata(
    *,
    raw_instructions_value: object,
    source_path: str,
    target_runtime: str | None,
    target_step_id: str | None = None,
) -> dict[str, Any] | None:
    raw_instructions = _raw_instruction_string(raw_instructions_value)
    if not raw_instructions:
        return None
    if raw_instructions.startswith("\\/"):
        first_line, _body = _first_line_and_body(raw_instructions)
        payload = _base_runtime_command_payload(
            source_path=source_path,
            target_runtime=target_runtime,
            target_step_id=target_step_id,
        )
        payload.update(
            {
                "command": "",
                "rawCommand": first_line,
                "args": "",
                "instructionBody": raw_instructions[1:],
                "detectionStatus": "escaped",
                "hintStatus": "opaque",
                "recognitionMode": "escaped_literal",
                "requiresRuntimeRecognition": False,
                "detectionPhase": "submit",
            }
        )
        return payload
    if not raw_instructions.startswith("/"):
        return None

    first_line, instruction_body = _first_line_and_body(raw_instructions)
    payload = _base_runtime_command_payload(
        source_path=source_path,
        target_runtime=target_runtime,
        target_step_id=target_step_id,
    )
    if _looks_like_ordinary_path(first_line):
        payload.update(
            {
                "command": "",
                "rawCommand": first_line,
                "args": "",
                "instructionBody": raw_instructions,
                "detectionStatus": "malformed",
                "hintStatus": "opaque",
                "recognitionMode": "escaped_literal",
                "requiresRuntimeRecognition": False,
                "detectionPhase": "submit",
            }
        )
        return payload

    match = _RUNTIME_COMMAND_TOKEN_PATTERN.fullmatch(first_line)
    if match:
        command = match.group(1)
        args = "" if "." in command else match.group(2) or ""
    else:
        payload.update(
            {
                "command": "",
                "rawCommand": first_line,
                "args": "",
                "instructionBody": raw_instructions,
                "detectionStatus": "malformed",
                "hintStatus": "opaque",
                "recognitionMode": "escaped_literal",
                "requiresRuntimeRecognition": False,
                "detectionPhase": "submit",
            }
        )
        return payload
    hint_status = _runtime_command_hint_status(command)
    passthrough = _runtime_supports_slash_passthrough(target_runtime)
    if passthrough:
        recognition_mode = (
            "hinted_runtime_passthrough"
            if hint_status == "hinted"
            else "runtime_passthrough"
        )
    else:
        recognition_mode = "runtime_does_not_support_slash_commands"
    payload.update(
        {
            "command": command,
            "rawCommand": first_line,
            "args": args,
            "instructionBody": instruction_body,
            "detectionStatus": "detected",
            "hintStatus": hint_status,
            "recognitionMode": recognition_mode,
            "requiresRuntimeRecognition": passthrough,
            "hintCatalogVersion": _RUNTIME_COMMAND_HINT_CATALOG_VERSION,
            "detectionPhase": "submit",
        }
    )
    return payload


def _validate_supplied_runtime_command(
    supplied: object,
    expected: Mapping[str, Any] | None,
    *,
    field_name: str,
) -> None:
    if supplied is None:
        return
    if not isinstance(supplied, Mapping):
        raise WorkflowContractError(f"{field_name} must be an object")
    if expected is None:
        raise WorkflowContractError(
            f"{field_name} conflicts with backend runtime command normalization"
        )
    marker_terms = ("runtime", "capability", "version")
    removed_marker = (
        marker_terms[0]
        + marker_terms[1][:1].upper()
        + marker_terms[1][1:]
        + marker_terms[2][:1].upper()
        + marker_terms[2][1:]
    )
    if supplied.get(removed_marker) is not None:
        raise WorkflowContractError(
            f"{field_name} uses name-only runtime command identity; remove removed runtime marker"
        )
    for key in (
        "kind",
        "sourcePath",
        "targetStepId",
        "command",
        "rawCommand",
        "detectionStatus",
        "recognitionMode",
    ):
        if key not in supplied:
            continue
        if supplied.get(key) != expected.get(key):
            raise WorkflowContractError(
                f"{field_name} conflicts with backend runtime command normalization"
            )

def _normalize_publish_mode(value: object) -> str:
    candidate = (_clean_optional_str(value) or _default_publish_mode()).lower()
    if candidate not in SUPPORTED_PUBLISH_MODES:
        supported = ", ".join(sorted(SUPPORTED_PUBLISH_MODES))
        raise WorkflowContractError(f"publish.mode must be one of: {supported}")
    return candidate


def _normalize_skill_id(value: object) -> str:
    return (_clean_optional_str(value) or "").lower()


def _load_skill_publish_metadata(skill_id: str) -> dict[str, Any] | None:
    normalized = _normalize_skill_id(skill_id)
    if not normalized:
        return None
    if normalized in _SKILL_PUBLISH_METADATA_CACHE:
        return _SKILL_PUBLISH_METADATA_CACHE[normalized]
    try:
        skill_path = resolve_skill_markdown_path(normalized)
    except SkillResolutionError:
        _SKILL_PUBLISH_METADATA_CACHE[normalized] = None
        return None
    if skill_path is None:
        _SKILL_PUBLISH_METADATA_CACHE[normalized] = None
        return None
    try:
        markdown = skill_path.read_text(encoding="utf-8")
        metadata = extract_publish_metadata_from_skill_markdown(
            markdown,
            skill_name=normalized,
            source_label=str(skill_path),
        )
    except (OSError, ValueError) as exc:
        raise WorkflowContractError(
            f"skill '{normalized}' publish metadata is invalid: {exc}"
        ) from exc
    _SKILL_PUBLISH_METADATA_CACHE[normalized] = metadata
    return metadata


def _is_agent_owned_auto_publish_metadata(metadata: Mapping[str, Any]) -> bool:
    return (
        _clean_str(metadata.get("mode")).lower() == "auto"
        and _clean_str(metadata.get("owner")).lower() == "agent"
        and metadata.get("requiresEvidence") is True
    )


def _auto_publish_capability_source(
    skill_id: object,
    *,
    publish_metadata: Mapping[str, Any] | None = None,
) -> str:
    normalized = _normalize_skill_id(skill_id)
    if not normalized:
        return "none"
    if publish_metadata is not None:
        return (
            "metadata"
            if _is_agent_owned_auto_publish_metadata(publish_metadata)
            else "none"
        )
    metadata = _load_skill_publish_metadata(normalized)
    if metadata is not None:
        return "metadata" if _is_agent_owned_auto_publish_metadata(metadata) else "none"
    if normalized in _AUTO_PUBLISH_MIGRATION_FALLBACK_SKILLS:
        return "fallback"
    return "none"


def is_auto_publish_capable_skill(skill_id: object) -> bool:
    """Return True when the selected skill owns auto publish evidence/actions."""

    return _auto_publish_capability_source(skill_id) in {"metadata", "fallback"}


def is_self_managed_publish_skill(skill_id: object) -> bool:
    """Return True when the selected skill handles commit/push/merge directly."""

    return is_auto_publish_capable_skill(skill_id)


def _auto_publish_legacy_none_diagnostic(skill_id: str) -> dict[str, object]:
    return {
        "code": "legacy_auto_publish_none_normalized",
        "skillId": skill_id,
        "requestedMode": "none",
        "resolvedMode": "auto",
        "message": (
            f"Legacy publish.mode='none' for auto-publish-capable skill "
            f"'{skill_id}' was normalized to 'auto'."
        ),
    }


def _auto_publish_fallback_diagnostic(skill_id: str) -> dict[str, object]:
    return {
        "code": "auto_publish_capability_migration_fallback",
        "skillId": skill_id,
        "message": (
            f"Auto publish capability for skill '{skill_id}' was resolved from "
            "the migration fallback because publish metadata was unavailable."
        ),
    }


def is_non_repository_side_effect_skill(skill_id: object) -> bool:
    """Return True when the selected skill performs side effects outside git publish."""

    return _normalize_skill_id(skill_id) in _NON_REPOSITORY_SIDE_EFFECT_SKILLS


def _is_agent_owned_side_effect_metadata(metadata: Mapping[str, Any]) -> bool:
    return (
        bool(_clean_str(metadata.get("kind")))
        and _clean_str(metadata.get("owner")).lower() == "agent"
    )


def _iter_applied_step_templates(value: object) -> list[object]:
    if isinstance(value, Mapping):
        return _safe_list(value.get("appliedStepTemplates"))
    model_extra = getattr(value, "model_extra", None)
    if isinstance(model_extra, Mapping):
        return _safe_list(model_extra.get("appliedStepTemplates"))
    return []


def _has_jira_orchestrate_preset_context(value: object) -> bool:
    for template in _iter_applied_step_templates(value):
        if not isinstance(template, Mapping):
            continue
        slug = _normalize_skill_id(template.get("slug") or template.get("presetSlug"))
        if slug in _JIRA_ORCHESTRATE_PRESET_SLUGS:
            return True
    return False


def allows_repository_publish_for_skill_context(value: object) -> bool:
    """Return True when task provenance represents a repository-publishing workflow."""

    return _has_jira_orchestrate_preset_context(value)


def resolve_publish_mode_for_skill(
    skill_id: object,
    requested_mode: object,
    *,
    allow_repository_publish: bool = False,
    diagnostics: list[dict[str, object]] | None = None,
    publish_metadata: Mapping[str, Any] | None = None,
    side_effect_metadata: Mapping[str, Any] | None = None,
) -> str:
    """Resolve publish mode for a skill while enforcing skill publish constraints."""

    normalized_skill_id = _normalize_skill_id(skill_id)
    auto_publish_source = _auto_publish_capability_source(
        normalized_skill_id,
        publish_metadata=publish_metadata,
    )
    is_auto_publish_capable = auto_publish_source in {"metadata", "fallback"}
    is_non_repository = is_non_repository_side_effect_skill(
        normalized_skill_id
    ) or (
        side_effect_metadata is not None
        and _is_agent_owned_side_effect_metadata(side_effect_metadata)
    )
    if is_auto_publish_capable:
        if auto_publish_source == "fallback" and diagnostics is not None:
            diagnostics.append(_auto_publish_fallback_diagnostic(normalized_skill_id))
        if requested_mode is None:
            return "auto"
        publish_mode = _normalize_publish_mode(requested_mode)
        if publish_mode == "auto":
            return "auto"
        if publish_mode == "none":
            if diagnostics is not None:
                diagnostics.append(
                    _auto_publish_legacy_none_diagnostic(normalized_skill_id)
                )
            return "auto"
        if publish_mode in {"branch", "pr"} and allow_repository_publish:
            return publish_mode
        if publish_mode in {"branch", "pr"}:
            raise WorkflowContractError(
                "task.publish.mode must be 'auto' when using auto-publish-capable "
                f"skill '{normalized_skill_id}'"
            )
        raise WorkflowContractError(
            f"task.publish.mode '{publish_mode}' is not supported for skill "
            f"'{normalized_skill_id}'"
        )
    if is_non_repository:
        if requested_mode is None:
            return "none"
        publish_mode = _normalize_publish_mode(requested_mode)
        if (
            allow_repository_publish
            or normalized_skill_id in _REPOSITORY_PUBLISH_COMPATIBLE_SIDE_EFFECT_SKILLS
        ):
            return publish_mode
        if publish_mode != "none":
            raise WorkflowContractError(
                "task.publish.mode must be 'none' when using non-repository skill "
                f"'{normalized_skill_id}'"
            )
        return "none"
    publish_mode = _normalize_publish_mode(requested_mode)
    if publish_mode == "auto":
        raise WorkflowContractError(
            "task.publish.mode='auto' requires an auto-publish-capable skill "
            "or an agent-owned publishing declaration"
        )
    return publish_mode


def _publish_mode_precedence(mode: str) -> int:
    if mode == "auto":
        return 3
    if mode in {"branch", "pr"}:
        return 2
    if mode == "none":
        return 1
    return 0


def _is_resolve_pr_objective(value: object) -> bool:
    """Return True when task instructions target PR resolution behavior."""

    text = _clean_optional_str(value)
    if not text:
        return False
    return _RESOLVE_PR_OBJECTIVE_PATTERN.search(text) is not None


def _contains_no_commit_push_constraint(value: object) -> bool:
    """Return True when text instructs the runtime not to commit/push."""

    text = _clean_optional_str(value)
    if not text:
        return False
    return _NO_COMMIT_PUSH_PATTERN.search(text) is not None

def _has_explicit_skill_selection(value: object) -> bool:
    """Return True when a skill identifier is explicitly set (not blank/auto)."""

    if isinstance(value, Mapping):
        skill_id = value.get("id")
    else:
        skill_id = getattr(value, "id", None)
    normalized = _clean_optional_str(skill_id)
    if normalized is None:
        return False
    return _normalize_skill_id(normalized) != "auto"

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
        raise WorkflowContractError(f"{field_name} exceeds max length")

    parsed = urlsplit(candidate)
    if parsed.scheme.lower() != "vault":
        raise WorkflowContractError(f"{field_name} must use vault:// secret references")

    mount = parsed.netloc.strip()
    path = parsed.path.lstrip("/").strip()
    field = parsed.fragment.strip()
    if not mount or not path or not field:
        raise WorkflowContractError(
            f"{field_name} must include mount/path and #field (vault://<mount>/<path>#<field>)"
        )
    if not _SECRET_REF_MOUNT_PATTERN.fullmatch(mount):
        raise WorkflowContractError(f"{field_name} mount contains invalid characters")
    if not _SECRET_REF_PATH_PATTERN.fullmatch(path):
        raise WorkflowContractError(f"{field_name} path contains invalid characters")
    if any(segment in {"..", "."} for segment in path.split("/")):
        raise WorkflowContractError(f"{field_name} path traversal is not allowed")
    if not _SECRET_REF_FIELD_PATTERN.fullmatch(field):
        raise WorkflowContractError(f"{field_name} field contains invalid characters")
    return f"vault://{mount}/{path}#{field}"

class WorkflowSkillSelectorExact(BaseModel):
    """Explicitly included skill by name."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    name: str = Field(..., alias="name")

    @model_validator(mode="before")
    @classmethod
    def _reject_removed_identity_versions(cls, value: object) -> object:
        if isinstance(value, Mapping):
            _reject_identity_version_keys(
                value,
                _SKILL_IDENTITY_VERSION_KEYS,
                field_path="workflow.skills.include[]",
            )
        return value

    @field_validator("name", mode="before")
    @classmethod
    def _normalize_optional_strings(cls, value: object) -> str | None:
        normalized = _clean_optional_str(value)
        if normalized and ":" in normalized:
            raise WorkflowContractError(
                "workflow.skills.include names must not include semantic versions"
            )
        return normalized

class WorkflowSkillSelectors(BaseModel):
    """Resolved definition for active skills during execution."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    sets: list[str] | None = Field(None, alias="sets")
    include: list[WorkflowSkillSelectorExact] | None = Field(None, alias="include")
    exclude: list[str] | None = Field(None, alias="exclude")
    materialization_mode: str | None = Field(None, alias="materializationMode")

    @field_validator("sets", mode="before")
    @classmethod
    def _normalize_sets(cls, value: object) -> list[str] | None:
        if value is None:
            return None
        if not isinstance(value, list):
            raise WorkflowContractError("workflow.skills.sets must be a list")
        normalized: list[str] = []
        seen: set[str] = set()
        for raw in value:
            item = _clean_optional_str(raw)
            if item and item not in seen:
                normalized.append(item)
                seen.add(item)
        return normalized or None

    @field_validator("exclude", mode="before")
    @classmethod
    def _normalize_exclude(cls, value: object) -> list[str] | None:
        if value is None:
            return None
        if not isinstance(value, list):
            raise WorkflowContractError("workflow.skills.exclude must be a list")
        normalized: list[str] = []
        seen: set[str] = set()
        for raw in value:
            item = _clean_optional_str(raw)
            if item and item not in seen:
                normalized.append(item)
                seen.add(item)
        return normalized or None

    @field_validator("include", mode="before")
    @classmethod
    def _normalize_include(cls, value: object) -> list[Any] | None:
        if value is None:
            return None
        if not isinstance(value, list):
            raise WorkflowContractError("workflow.skills.include must be a list")
        return value

    @field_validator("materialization_mode", mode="before")
    @classmethod
    def _normalize_materialization_mode(cls, value: object) -> str | None:
        candidate = _clean_optional_str(value)
        if candidate is None:
            return None
        lowered = candidate.lower()
        if lowered not in {"hybrid", "remote", "local", "none"}:
            raise WorkflowContractError(
                "workflow.skills.materializationMode must be hybrid, remote, local, or none"
            )
        return lowered

def _coerce_workflow_skill_selectors(
    value: WorkflowSkillSelectors | Mapping[str, Any] | None,
    *,
    field_name: str,
) -> WorkflowSkillSelectors | None:
    if value is None:
        return None
    if isinstance(value, WorkflowSkillSelectors):
        return value
    if isinstance(value, Mapping):
        return WorkflowSkillSelectors.model_validate(dict(value))
    raise WorkflowContractError(f"{field_name} must be an object")

def build_effective_workflow_skill_selectors(
    task_skills: WorkflowSkillSelectors | Mapping[str, Any] | None,
    step_skills: WorkflowSkillSelectors | Mapping[str, Any] | None,
) -> WorkflowSkillSelectors | None:
    """Merge task-level and step-level agent skill selectors for one step.

    Step selectors refine inherited task intent. Exclusions are retained in the
    effective selector and also remove matching includes so downstream
    resolution receives deterministic input without mutating the task selector.
    """

    task = _coerce_workflow_skill_selectors(
        task_skills, field_name="workflow.skills"
    )
    step = _coerce_workflow_skill_selectors(
        step_skills, field_name="workflow.steps[].skills"
    )
    if task is None and step is None:
        return None

    sets: list[str] = []
    seen_sets: set[str] = set()
    for source in (task, step):
        for item in (source.sets if source is not None else None) or []:
            if item not in seen_sets:
                sets.append(item)
                seen_sets.add(item)

    excludes: list[str] = []
    seen_excludes: set[str] = set()
    for source in (task, step):
        for item in (source.exclude if source is not None else None) or []:
            if item not in seen_excludes:
                excludes.append(item)
                seen_excludes.add(item)

    include_by_name: dict[str, WorkflowSkillSelectorExact] = {}
    for source in (task, step):
        for item in (source.include if source is not None else None) or []:
            if _normalize_skill_id(item.name) == "auto":
                continue
            include_by_name[item.name] = WorkflowSkillSelectorExact(
                name=item.name,
            )
    for item in excludes:
        include_by_name.pop(item, None)

    materialization_mode = (
        step.materialization_mode
        if step is not None and step.materialization_mode is not None
        else task.materialization_mode if task is not None else None
    )

    payload: dict[str, Any] = {}
    if sets:
        payload["sets"] = sets
    if include_by_name:
        payload["include"] = [
            item.model_dump(mode="json", by_alias=True, exclude_none=True)
            for item in include_by_name.values()
        ]
    if excludes:
        payload["exclude"] = excludes
    if materialization_mode is not None:
        payload["materializationMode"] = materialization_mode

    if not payload:
        return None
    return WorkflowSkillSelectors.model_validate(payload)

class WorkflowSkillSelection(BaseModel):
    """Selected skill and optional skill argument object."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    id: str = Field(
        "auto",
        alias="id",
        validation_alias=AliasChoices("id", "name"),
    )
    args: dict[str, Any] = Field(default_factory=dict, alias="args")
    required_capabilities: list[str] | None = Field(
        None,
        alias="requiredCapabilities",
    )
    publish: dict[str, Any] | None = Field(None, alias="publish")
    side_effect: dict[str, Any] | None = Field(None, alias="sideEffect")

    @model_validator(mode="before")
    @classmethod
    def _reject_removed_identity_versions(cls, value: object) -> object:
        if isinstance(value, Mapping):
            _reject_identity_version_keys(
                value,
                _SKILL_IDENTITY_VERSION_KEYS,
                field_path="workflow.skill",
            )
        return value

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
            raise WorkflowContractError("task.skill.requiredCapabilities must be a list")
        normalized = _normalize_capabilities(value)
        return normalized or None

    @field_validator("publish", "side_effect", mode="before")
    @classmethod
    def _normalize_metadata_mapping(cls, value: object) -> dict[str, Any] | None:
        if value is None:
            return None
        if not isinstance(value, Mapping):
            raise WorkflowContractError("task.skill metadata must be an object")
        return dict(value)

class WorkflowRuntimeSelection(BaseModel):
    """Runtime mode plus optional tier intent or hard model/effort overrides."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    mode: str | None = Field(
        None,
        alias="mode",
        validation_alias=AliasChoices("mode", "targetRuntime", "target_runtime"),
    )
    model: str | None = Field(None, alias="model")
    effort: str | None = Field(None, alias="effort")
    provider_profile: str | None = Field(
        None,
        alias="providerProfile",
        validation_alias=AliasChoices("providerProfile", "profileId"),
    )
    model_tier: int | None = Field(None, alias="modelTier", ge=1)
    tier_fallback: str | None = Field(None, alias="tierFallback")

    @field_validator("mode", mode="before")
    @classmethod
    def _normalize_mode(cls, value: object) -> str | None:
        return _normalize_runtime_value(value, field_name="task.runtime.mode")

    @field_validator("model", "effort", "provider_profile", mode="before")
    @classmethod
    def _normalize_optional_strings(cls, value: object) -> str | None:
        return _clean_optional_str(value)

    @field_validator("tier_fallback", mode="before")
    @classmethod
    def _normalize_tier_fallback(cls, value: object) -> str | None:
        normalized = _clean_optional_str(value)
        if normalized is None:
            return None
        normalized = normalized.lower()
        if normalized not in {"clamp", "strict"}:
            raise WorkflowContractError("task.runtime.tierFallback must be clamp or strict")
        return normalized

class WorkflowGitSelection(BaseModel):
    """Branch-selection values for task execution."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    branch: str | None = Field(None, alias="branch")
    starting_branch: str | None = Field(None, alias="startingBranch")

    @model_validator(mode="before")
    @classmethod
    def _normalize_target_branch(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        payload = dict(value)
        payload.pop("targetBranch", None)
        return payload

    @field_validator("branch", "starting_branch", mode="before")
    @classmethod
    def _normalize_branches(cls, value: object) -> str | None:
        return _clean_optional_str(value)

class WorkflowPublishSelection(BaseModel):
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

class WorkflowAuthSelection(BaseModel):
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

class WorkflowContainerCacheVolume(BaseModel):
    """Named volume mount requested by container-enabled task execution."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    name: str = Field(..., alias="name")
    target: str = Field(..., alias="target")

    @field_validator("name", mode="before")
    @classmethod
    def _normalize_name(cls, value: object) -> str:
        cleaned = _clean_optional_str(value)
        if not cleaned:
            raise WorkflowContractError("task.container.cacheVolumes[].name is required")
        if "," in cleaned or "=" in cleaned:
            raise WorkflowContractError(
                "task.container.cacheVolumes[].name contains invalid characters"
            )
        if not _CONTAINER_VOLUME_NAME_PATTERN.fullmatch(cleaned):
            raise WorkflowContractError(
                "task.container.cacheVolumes[].name has invalid format"
            )
        return cleaned

    @field_validator("target", mode="before")
    @classmethod
    def _normalize_target(cls, value: object) -> str:
        cleaned = _clean_optional_str(value)
        if not cleaned:
            raise WorkflowContractError("task.container.cacheVolumes[].target is required")
        if "," in cleaned:
            raise WorkflowContractError(
                "task.container.cacheVolumes[].target may not contain ','"
            )
        if not cleaned.startswith("/"):
            raise WorkflowContractError(
                "task.container.cacheVolumes[].target must be an absolute path"
            )
        return cleaned

class WorkflowContainerSelection(BaseModel):
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
    cache_volumes: list[WorkflowContainerCacheVolume] | None = Field(
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
        raise WorkflowContractError("task.container.enabled must be a boolean")

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
            raise WorkflowContractError("task.container.command must be a list")
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
            raise WorkflowContractError("task.container.env must be an object")
        normalized: dict[str, str] = {}
        for raw_key, raw_value in value.items():
            key = _clean_optional_str(raw_key)
            if key is None:
                continue
            if "=" in key:
                raise WorkflowContractError("task.container.env keys may not contain '='")
            if key.upper() in _CONTAINER_RESERVED_ENV_KEYS:
                raise WorkflowContractError(
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
            raise WorkflowContractError(
                "task.container.timeoutSeconds must be an integer"
            ) from exc
        if timeout < 1:
            raise WorkflowContractError(
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
            raise WorkflowContractError("task.container.pull must be if-missing or always")
        return lowered

    @model_validator(mode="after")
    def _validate_enabled_requirements(self) -> "WorkflowContainerSelection":
        if not self.enabled:
            return self
        if not self.image:
            raise WorkflowContractError(
                "task.container.image is required when task.container.enabled=true"
            )
        if not self.command:
            raise WorkflowContractError(
                "task.container.command is required when task.container.enabled=true"
            )
        return self

class WorkflowProposalProviderPolicy(BaseModel):
    """Provider-specific proposal delivery metadata."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    repository: str | None = Field(None, alias="repository")
    project_key: str | None = Field(None, alias="projectKey")
    issue_type: str | None = Field(None, alias="issueType")
    labels: list[str] | None = Field(None, alias="labels")
    components: list[str] | None = Field(None, alias="components")

    @field_validator("repository", "project_key", "issue_type", mode="before")
    @classmethod
    def _clean_optional(cls, value: object) -> str | None:
        return _clean_optional_str(value)

    @field_validator("labels", "components", mode="before")
    @classmethod
    def _normalize_string_list(cls, value: object) -> list[str] | None:
        if value is None or value == "":
            return None
        if not isinstance(value, list):
            raise WorkflowContractError(
                "workflow.proposalPolicy.delivery labels/components must be lists"
            )
        normalized: list[str] = []
        seen: set[str] = set()
        for raw in value:
            item = _clean_optional_str(raw)
            if not item or item in seen:
                continue
            normalized.append(item)
            seen.add(item)
        return normalized or None

class WorkflowProposalDeliveryPolicy(BaseModel):
    """Optional provider routing policy for proposal delivery."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    provider: str | None = Field(None, alias="provider")
    github: WorkflowProposalProviderPolicy | None = Field(None, alias="github")
    jira: WorkflowProposalProviderPolicy | None = Field(None, alias="jira")

    @field_validator("provider", mode="before")
    @classmethod
    def _normalize_provider(cls, value: object) -> str | None:
        cleaned = _clean_optional_str(value)
        if not cleaned:
            return None
        lowered = cleaned.lower()
        if lowered not in {"auto", "github", "jira"}:
            raise WorkflowContractError(
                "workflow.proposalPolicy.delivery.provider must be github, jira, or auto"
            )
        return lowered

class WorkflowProposalPolicy(BaseModel):
    """Optional policy block controlling worker proposal emission."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    targets: list[str] | None = Field(None, alias="targets")
    max_items: dict[str, int] | None = Field(None, alias="maxItems")
    min_severity_for_moonmind: str | None = Field(None, alias="minSeverityForMoonMind")
    default_runtime: str | None = Field(None, alias="defaultRuntime")
    delivery: WorkflowProposalDeliveryPolicy | None = Field(None, alias="delivery")

    @field_validator("default_runtime", mode="before")
    @classmethod
    def _normalize_default_runtime(cls, value: object) -> str | None:
        return _normalize_runtime_value(value, field_name="workflow.proposalPolicy.defaultRuntime")

    @field_validator("targets", mode="before")
    @classmethod
    def _normalize_targets(cls, value: object) -> list[str] | None:
        if value is None or value == "":
            return None
        if not isinstance(value, list):
            raise WorkflowContractError("workflow.proposalPolicy.targets must be a list")
        normalized: list[str] = []
        seen: set[str] = set()
        for raw in value:
            target = _clean_optional_str(raw)
            if not target:
                continue
            lowered = target.lower()
            if lowered not in _PROPOSAL_POLICY_TARGETS:
                raise WorkflowContractError(
                    "workflow.proposalPolicy.targets entries must be 'workflow_repo' or 'moonmind'"
                )
            if lowered in seen:
                continue
            normalized.append(lowered)
            seen.add(lowered)
        return normalized or None

    @field_validator("max_items", mode="before")
    @classmethod
    def _normalize_max_items(cls, value: object) -> dict[str, int] | None:
        if value is None or value == "":
            return None
        if not isinstance(value, Mapping):
            raise WorkflowContractError("workflow.proposalPolicy.maxItems must be an object")
        normalized: dict[str, int] = {}
        key_aliases = {
            "workflow_repo": ("workflowRepo", "workflow_repo"),
            "moonmind": ("moonmind",),
        }
        for key in _PROPOSAL_POLICY_TARGETS:
            raw = None
            for source_key in key_aliases[key]:
                if source_key in value:
                    raw = value.get(source_key)
                    break
            if raw is None:
                continue
            try:
                parsed = int(raw)
            except (TypeError, ValueError):
                continue
            if parsed <= 0:
                continue
            normalized[key] = parsed
        return normalized or None

    @field_validator("min_severity_for_moonmind", mode="before")
    @classmethod
    def _normalize_min_severity(cls, value: object) -> str | None:
        cleaned = _clean_optional_str(value)
        if not cleaned:
            return None
        lowered = cleaned.lower()
        if lowered not in _PROPOSAL_SEVERITIES:
            allowed = ", ".join(_PROPOSAL_SEVERITIES)
            raise WorkflowContractError(
                f"workflow.proposalPolicy.minSeverityForMoonMind must be one of: {allowed}"
            )
        return lowered

class WorkflowInputAttachmentRef(BaseModel):
    """Compact task input attachment reference for task-shaped submissions."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    artifact_id: str = Field(..., alias="artifactId")
    filename: str = Field(..., alias="filename")
    content_type: str = Field(..., alias="contentType")
    size_bytes: int = Field(..., alias="sizeBytes", ge=0)

    @model_validator(mode="before")
    @classmethod
    def _reject_embedded_image_data(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            _raise_attachment_validation_error(
                "inputAttachments entries must be objects",
                attachment=None,
                target_kind="unknown",
            )
        payload = dict(value)
        blocked = sorted(
            key
            for key in payload
            if str(key).strip() in _EMBEDDED_ATTACHMENT_DATA_FIELDS
        )
        if blocked or _contains_data_image_url(payload):
            _raise_attachment_validation_error(
                "inputAttachments entries must not include embedded image data",
                attachment=payload,
                target_kind=str(payload.get("targetKind") or "unknown"),
            )
        return payload

    @field_validator("artifact_id", "filename", "content_type", mode="before")
    @classmethod
    def _normalize_required_string(cls, value: object) -> str:
        cleaned = _clean_optional_str(value)
        if not cleaned:
            _raise_attachment_validation_error(
                "inputAttachments entries require artifactId, filename, and contentType",
                attachment=None,
                target_kind="unknown",
            )
        if _contains_data_image_url(cleaned):
            _raise_attachment_validation_error(
                "inputAttachments entries must not include embedded image data",
                attachment=None,
                target_kind="unknown",
            )
        return cleaned

class WorkflowStepSource(BaseModel):
    """Optional provenance for a step in a compiled workflow payload."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    kind: Literal["manual", "preset-derived", "preset-include", "detached"] | None = (
        Field(None, alias="kind")
    )
    preset_id: str | None = Field(None, alias="presetId")
    preset_slug: str | None = Field(None, alias="presetSlug")
    preset_digest: str | None = Field(None, alias="presetDigest")
    include_path: list[str] | None = Field(None, alias="includePath")
    original_step_id: str | None = Field(None, alias="originalStepId")

    @model_validator(mode="before")
    @classmethod
    def _reject_removed_identity_versions(cls, value: object) -> object:
        if isinstance(value, Mapping):
            _reject_identity_version_keys(
                value,
                _PRESET_IDENTITY_VERSION_KEYS,
                field_path="workflow.steps[].source",
            )
        return value

    @field_validator(
        "kind",
        "preset_id",
        "preset_slug",
        "preset_digest",
        "original_step_id",
        mode="before",
    )
    @classmethod
    def _normalize_optional_strings(cls, value: object) -> str | None:
        return _clean_optional_str(value)

    @field_validator("include_path", mode="before")
    @classmethod
    def _normalize_include_path(cls, value: object) -> list[str] | None:
        if value is None or value == "":
            return None
        if not isinstance(value, list):
            raise WorkflowContractError("workflow.steps[].source.includePath must be a list")
        normalized = [
            cleaned
            for item in value
            if (cleaned := _clean_optional_str(item)) is not None
        ]
        return normalized or None

class AuthoredPresetBinding(BaseModel):
    """Optional preset binding metadata used to compile a workflow payload."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    preset_id: str | None = Field(None, alias="presetId")
    preset_slug: str | None = Field(None, alias="presetSlug")
    preset_digest: str | None = Field(None, alias="presetDigest")
    alias: str | None = Field(None, alias="alias")
    include_path: list[str] | None = Field(None, alias="includePath")
    input_mapping: dict[str, Any] | None = Field(None, alias="inputMapping")
    scope: str | None = Field(None, alias="scope")

    @model_validator(mode="before")
    @classmethod
    def _reject_removed_identity_versions(cls, value: object) -> object:
        if isinstance(value, Mapping):
            _reject_preset_identity_versions(
                value,
                field_path="workflow.authoredPresets[]",
            )
        return value

    @field_validator(
        "preset_id",
        "preset_slug",
        "preset_digest",
        "alias",
        "scope",
        mode="before",
    )
    @classmethod
    def _normalize_optional_strings(cls, value: object) -> str | None:
        return _clean_optional_str(value)

    @field_validator("include_path", mode="before")
    @classmethod
    def _normalize_include_path(cls, value: object) -> list[str] | None:
        if value is None or value == "":
            return None
        if not isinstance(value, list):
            raise WorkflowContractError("workflow.authoredPresets[].includePath must be a list")
        normalized = [
            cleaned
            for item in value
            if (cleaned := _clean_optional_str(item)) is not None
        ]
        return normalized or None

    @field_validator("input_mapping", mode="before")
    @classmethod
    def _normalize_input_mapping(cls, value: object) -> dict[str, Any] | None:
        if value is None or value == "":
            return None
        if not isinstance(value, dict):
            raise WorkflowContractError(
                "workflow.authoredPresets[].inputMapping must be an object"
            )
        return dict(value)

class WorkflowStepSpec(BaseModel):
    """Optional execution step contained within a canonical workflow payload."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    id: str | None = Field(None, alias="id")
    title: str | None = Field(None, alias="title")
    instructions: str | None = Field(None, alias="instructions")
    runtime: WorkflowRuntimeSelection | None = Field(None, alias="runtime")
    skill: WorkflowSkillSelection | None = Field(None, alias="skill")
    skills: WorkflowSkillSelectors | None = Field(None, alias="skills")
    runtime: WorkflowRuntimeSelection | None = Field(None, alias="runtime")
    source: WorkflowStepSource | None = Field(None, alias="source")
    input_attachments: list[WorkflowInputAttachmentRef] = Field(
        default_factory=list, alias="inputAttachments"
    )

    @field_validator("id", "title", "instructions", mode="before")
    @classmethod
    def _normalize_optional_strings(cls, value: object) -> str | None:
        return _clean_optional_str(value)

    @field_validator("input_attachments", mode="before")
    @classmethod
    def _normalize_input_attachments(cls, value: object) -> object:
        return _validate_input_attachment_payloads(
            value,
            list_error="workflow.steps[].inputAttachments must be a list",
            target_kind="step",
        )

    @model_validator(mode="before")
    @classmethod
    def _reject_forbidden_step_overrides(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        reject_workflow_capability_identity_versions(value, field_path="workflow.steps[]")
        payload = dict(value)
        raw_kind = _clean_optional_str(payload.get("kind"))
        if raw_kind is not None and raw_kind.lower() == "include":
            raise WorkflowContractError(
                "workflow.steps entries must not contain unresolved preset include work"
            )
        raw_type = _clean_optional_str(payload.get("type"))
        if raw_type is not None:
            step_type = raw_type.lower()
            if step_type not in {"tool", "skill"}:
                raise WorkflowContractError("workflow.steps[].type must be one of: tool, skill")
            tool_payload = payload.get("tool")
            skill_payload = payload.get("skill")
            if step_type == "tool":
                if not isinstance(tool_payload, Mapping):
                    raise WorkflowContractError("Tool steps require a tool payload")
                tool_id = _clean_optional_str(
                    tool_payload.get("id") or tool_payload.get("name")
                )
                if not tool_id:
                    raise WorkflowContractError(
                        "workflow.steps[].tool.id or workflow.steps[].tool.name is required; "
                        "Tool steps require tool.id or tool.name"
                    )
                if skill_payload is not None:
                    raise WorkflowContractError(
                        "Tool steps must not include a skill payload"
                    )
            if step_type == "skill":
                legacy_skill_tool = False
                if isinstance(tool_payload, Mapping):
                    tool_type = str(
                        tool_payload.get("type") or tool_payload.get("kind") or ""
                    ).strip().lower()
                    legacy_skill_tool = tool_type in {"", "skill"}
                    if not legacy_skill_tool:
                        raise WorkflowContractError(
                            "Skill steps must not include a non-skill tool payload"
                        )
        if "inputAttachments" in payload:
            _validate_input_attachment_payloads(
                payload.get("inputAttachments"),
                list_error="workflow.steps[].inputAttachments must be a list",
                target_kind="step",
                step_ref=_clean_optional_str(payload.get("id")),
            )
        forbidden = {
            "targetRuntime",
            "target_runtime",
            "model",
            "effort",
            "providerProfile",
            "profileId",
            "repository",
            "repo",
            "git",
            "publish",
            "container",
            "command",
            "cmd",
            "script",
            "shell",
            "bash",
        }
        blocked = sorted(key for key in payload.keys() if str(key).strip() in forbidden)
        if blocked:
            formatted = ", ".join(blocked)
            raise WorkflowContractError(
                f"workflow.steps entries may not define workflow-scoped overrides: {formatted}"
            )
        for graph_key in ("dependsOn", "depends_on", "dependencies"):
            if graph_key not in payload:
                continue
            graph_value = payload.get(graph_key)
            if graph_value in (None, "", [], (), {}):
                continue
            raise WorkflowContractError(
                f"workflow.steps[].{graph_key} is no longer supported. "
                "Authored workflow steps are ordered by their steps[] position; use "
                "workflow.dependsOn only for dependencies between workflow executions"
            )
        return payload


# --- MM-638: Recovery / Resume contract types ---

WorkflowRecoveryKind = Literal["exact_full_rerun", "edited_full_retry", "recover_from_failed_step"]


class WorkflowRecoveryProvenance(BaseModel):
    """Recovery provenance attached to a task submission."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    kind: WorkflowRecoveryKind = Field(..., alias="kind")
    source_workflow_id: str = Field(..., alias="sourceWorkflowId")
    source_run_id: str = Field(..., alias="sourceRunId")
    requested_by: str | None = Field(None, alias="requestedBy")
    requested_at: str | None = Field(None, alias="requestedAt")

    @field_validator("source_workflow_id", "source_run_id", mode="before")
    @classmethod
    def _require_non_empty(cls, value: object) -> str:
        cleaned = _clean_str(value)
        if not cleaned:
            raise WorkflowContractError(
                "sourceWorkflowId and sourceRunId must be non-empty strings"
            )
        return cleaned

    @field_validator("requested_by", "requested_at", mode="before")
    @classmethod
    def _clean_optional(cls, value: object) -> str | None:
        return _clean_optional_str(value)


class ResumeFromFailedStepRef(BaseModel):
    """Pins a resume submission to a specific source run, failed step, and checkpoint."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    kind: Literal["recover_from_failed_step"] = Field(
        "recover_from_failed_step", alias="kind"
    )
    source_workflow_id: str = Field(..., alias="sourceWorkflowId")
    source_run_id: str = Field(..., alias="sourceRunId")
    failed_step_id: str = Field(..., alias="failedStepId")
    failed_step_execution: int | None = Field(None, alias="failedStepExecution")
    recovery_mode: Literal["selected_step"] | None = Field(None, alias="recoveryMode")
    selected_start_step_id: str | None = Field(None, alias="selectedStartStepId")
    selected_start_step_execution: int | None = Field(
        None, alias="selectedStartStepExecution"
    )
    recovery_checkpoint_ref: str = Field(..., alias="recoveryCheckpointRef")
    checkpoint_boundary: Literal[
        "after_prepare",
        "before_execution",
        "after_execution",
        "after_gate",
        "before_publication",
        "before_recovery_restoration",
    ] | None = Field(None, alias="checkpointBoundary")
    task_input_snapshot_ref: str = Field(..., alias="taskInputSnapshotRef")
    plan_ref: str | None = Field(None, alias="planRef")
    plan_digest: str | None = Field(None, alias="planDigest")
    preserved_step_refs: list[str] = Field(default_factory=list, alias="preservedStepRefs")
    dependency_signatures: dict[str, Any] = Field(
        default_factory=dict, alias="dependencySignatures"
    )
    workspace_policy: Literal[
        "restore_pre_execution",
        "continue_from_previous_execution",
        "apply_previous_execution_diff_to_clean_baseline",
        "start_from_last_passed_commit",
        "fresh_branch_from_source",
    ] | None = Field(None, alias="workspacePolicy")
    admitted_checkpoint_resume_decision: dict[str, Any] | None = Field(
        None, alias="admittedCheckpointResumeDecision"
    )

    @field_validator(
        "source_workflow_id",
        "source_run_id",
        "failed_step_id",
        "recovery_checkpoint_ref",
        "task_input_snapshot_ref",
        mode="before",
    )
    @classmethod
    def _require_non_empty(cls, value: object) -> str:
        cleaned = _clean_str(value)
        if not cleaned:
            raise WorkflowContractError(
                "ResumeFromFailedStepRef required fields must be non-empty strings"
            )
        return cleaned

    @field_validator("plan_ref", "plan_digest", mode="before")
    @classmethod
    def _clean_optional(cls, value: object) -> str | None:
        return _clean_optional_str(value)

    @field_validator("selected_start_step_id", mode="before")
    @classmethod
    def _clean_selected_start_step_id(cls, value: object) -> str | None:
        return _clean_optional_str(value)

    @field_validator("preserved_step_refs")
    @classmethod
    def _clean_preserved_step_refs(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for item in value:
            candidate = _clean_str(item)
            if not candidate or candidate in seen:
                continue
            seen.add(candidate)
            normalized.append(candidate)
        return normalized

    @field_validator("dependency_signatures", mode="before")
    @classmethod
    def _require_dependency_signatures_mapping(cls, value: object) -> dict[str, Any]:
        if value is None:
            return {}
        if not isinstance(value, Mapping):
            raise WorkflowContractError("dependencySignatures must be an object")
        return dict(value)

    @field_validator("admitted_checkpoint_resume_decision", mode="before")
    @classmethod
    def _require_admitted_checkpoint_decision(cls, value: object) -> dict[str, Any] | None:
        # Optional only for replay/validation of histories created before capability v2.
        if value is None:
            return None
        if not isinstance(value, Mapping):
            raise WorkflowContractError(
                "admittedCheckpointResumeDecision must be an immutable decision object"
            )
        from moonmind.workflows.executions.checkpoint_resume_admission import (
            AdmittedCheckpointResumeDecision,
        )

        decision = AdmittedCheckpointResumeDecision.model_validate(value)
        if not decision.admitted:
            raise WorkflowContractError(
                "admittedCheckpointResumeDecision must admit checkpoint Resume"
            )
        return decision.model_dump(by_alias=True, mode="json")


class WorkflowExecutionSpec(BaseModel):
    """Main task execution body."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    instructions: str | None = Field(
        None,
        alias="instructions",
        validation_alias=AliasChoices("instructions", "instruction"),
    )
    skill: WorkflowSkillSelection = Field(default_factory=WorkflowSkillSelection, alias="skill")
    skills: WorkflowSkillSelectors | None = Field(None, alias="skills")
    runtime: WorkflowRuntimeSelection = Field(
        default_factory=WorkflowRuntimeSelection, alias="runtime"
    )
    git: WorkflowGitSelection = Field(default_factory=WorkflowGitSelection, alias="git")
    publish: WorkflowPublishSelection = Field(
        default_factory=WorkflowPublishSelection, alias="publish"
    )
    propose_tasks: bool = Field(
        default_factory=_default_propose_tasks, alias="proposeTasks"
    )
    steps: list[WorkflowStepSpec] = Field(default_factory=list, alias="steps")
    input_attachments: list[WorkflowInputAttachmentRef] = Field(
        default_factory=list, alias="inputAttachments"
    )
    container: WorkflowContainerSelection | None = Field(None, alias="container")
    proposal_policy: WorkflowProposalPolicy | None = Field(None, alias="proposalPolicy")
    authored_presets: list[AuthoredPresetBinding] | None = Field(
        None, alias="authoredPresets"
    )
    recovery: WorkflowRecoveryProvenance | None = Field(None, alias="recovery")
    resume: ResumeFromFailedStepRef | None = Field(None, alias="resume")
    depends_on: list[str] | None = Field(None, alias="dependsOn")

    @field_validator("instructions", mode="before")
    @classmethod
    def _normalize_instructions(cls, value: object) -> str | None:
        return _clean_optional_str(value)

    @field_validator("input_attachments", mode="before")
    @classmethod
    def _normalize_input_attachments(cls, value: object) -> object:
        return _validate_input_attachment_payloads(
            value,
            list_error="workflow.inputAttachments must be a list",
            target_kind="objective",
        )

    @field_validator("authored_presets", mode="before")
    @classmethod
    def _normalize_authored_presets(cls, value: object) -> object:
        if value is None or value == "":
            return None
        if not isinstance(value, list):
            raise WorkflowContractError("workflow.authoredPresets must be a list")
        return value

    @field_validator("propose_tasks", mode="before")
    @classmethod
    def _normalize_propose_tasks(cls, value: object) -> bool:
        if value is None or value == "":
            return _default_propose_tasks()
        if isinstance(value, bool):
            return value
        lowered = str(value).strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
        raise WorkflowContractError("workflow.proposeTasks must be a boolean")

    @model_validator(mode="before")
    @classmethod
    def _lift_legacy_spec_shape(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        reject_workflow_capability_identity_versions(value, field_path="workflow")
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
            raise WorkflowContractError("workflow.steps must be a list")
        return list(value)

    @field_validator("depends_on", mode="before")
    @classmethod
    def _normalize_depends_on(cls, value: object) -> list[str] | None:
        if value is None or value == "":
            return None
        if isinstance(value, list) and len(value) == 0:
            return None
        return value

    @model_validator(mode="after")
    def _validate_recovery_recovery_consistency(self) -> "WorkflowExecutionSpec":
        recovery = self.recovery
        resume = self.resume

        if recovery is not None and recovery.kind == "recover_from_failed_step":
            if resume is None:
                raise WorkflowContractError(
                    "task.resume is required when task.recovery.kind is 'recover_from_failed_step'"
                )

        if resume is not None:
            if recovery is None:
                raise WorkflowContractError(
                    "task.recovery must be present with kind 'recover_from_failed_step' "
                    "when task.resume is provided"
                )
            if recovery.kind != "recover_from_failed_step":
                raise WorkflowContractError(
                    "task.resume is only valid when task.recovery.kind is "
                    f"'recover_from_failed_step'; got {recovery.kind!r}"
                )
            if recovery.source_workflow_id != resume.source_workflow_id:
                raise WorkflowContractError(
                    "task.recovery.sourceWorkflowId and task.resume.sourceWorkflowId "
                    "must match"
                )
            if recovery.source_run_id != resume.source_run_id:
                raise WorkflowContractError(
                    "task.recovery.sourceRunId and task.resume.sourceRunId must match"
                )

        return self

    @model_validator(mode="after")
    def _validate_container_steps_compatibility(self) -> "WorkflowExecutionSpec":
        if self.container is None:
            return self
        if self.container.enabled and self.steps:
            raise WorkflowContractError(
                "workflow.steps is not supported when task.container.enabled=true"
            )
        return self

    @model_validator(mode="after")
    def _validate_primary_objective_or_skill(self) -> "WorkflowExecutionSpec":
        if self.instructions:
            return self
        if _has_explicit_skill_selection(self.skill):
            return self
        primary_step = self.steps[0] if self.steps else None
        if primary_step and _has_explicit_skill_selection(primary_step.skill):
            return self
        raise WorkflowContractError(
            "task.instructions is required unless task.skill or the primary step "
            "selects an explicit skill"
        )

    @model_validator(mode="after")
    def _validate_skill_publish_compatibility(self) -> "WorkflowExecutionSpec":
        allow_repository_publish = allows_repository_publish_for_skill_context(self)
        skills_by_id: dict[str, WorkflowSkillSelection] = {}
        primary_skill_id = _normalize_skill_id(self.skill.id)
        if primary_skill_id:
            skills_by_id[primary_skill_id] = self.skill
        for step in self.steps:
            if step.skill is None:
                continue
            step_skill_id = _normalize_skill_id(step.skill.id)
            if step_skill_id:
                skills_by_id[step_skill_id] = step.skill

        requested_publish_mode = self.publish.mode if (
            allow_repository_publish or "mode" in self.publish.model_fields_set
        ) else None
        resolved_publish_mode: str | None = None
        for skill_id, skill_selection in skills_by_id.items():
            if not skill_id or skill_id == "auto":
                continue
            mode = resolve_publish_mode_for_skill(
                skill_id,
                requested_publish_mode,
                allow_repository_publish=allow_repository_publish,
                publish_metadata=skill_selection.publish,
                side_effect_metadata=skill_selection.side_effect,
            )
            if (
                resolved_publish_mode is None
                or _publish_mode_precedence(mode)
                > _publish_mode_precedence(resolved_publish_mode)
            ):
                resolved_publish_mode = mode
        if resolved_publish_mode is not None:
            self.publish.mode = resolved_publish_mode
        return self

    @model_validator(mode="after")
    def _validate_resolve_pr_constraints(self) -> "WorkflowExecutionSpec":
        """Reject conflicting constraints for resolve-PR task objectives."""

        if not _is_resolve_pr_objective(self.instructions):
            return self

        skill_ids: set[str] = {str(self.skill.id or "").strip().lower()}
        instruction_chunks: list[str] = [self.instructions]
        for step in self.steps:
            if step.skill is not None:
                skill_ids.add(str(step.skill.id or "").strip().lower())
            if step.instructions:
                instruction_chunks.append(step.instructions)

        if self.publish.mode == "none" and "pr-resolver" not in skill_ids:
            raise WorkflowContractError(
                "resolve-PR objectives with task.publish.mode='none' require "
                "skill 'pr-resolver' so commit/push/merge can be handled directly"
            )

        if any(
            _contains_no_commit_push_constraint(chunk) for chunk in instruction_chunks
        ):
            raise WorkflowContractError(
                "resolve-PR objectives cannot include 'Do NOT commit or push' constraints"
            )

        return self

@dataclass
class EffectiveProposalPolicy:
    """Resolved proposal policy derived from config and optional overrides."""

    allow_workflow_repo: bool
    allow_moonmind: bool
    max_items_workflow_repo: int
    max_items_moonmind: int
    min_severity_for_moonmind: str
    severity_rank: dict[str, int]
    delivery_provider: str
    provider_metadata: dict[str, Any] = field(default_factory=dict)
    remaining_workflow_repo_slots: int = field(init=False)
    remaining_moonmind_slots: int = field(init=False)

    def __post_init__(self) -> None:
        self.remaining_workflow_repo_slots = (
            self.max_items_workflow_repo if self.allow_workflow_repo else 0
        )
        self.remaining_moonmind_slots = (
            self.max_items_moonmind if self.allow_moonmind else 0
        )

    def consume_workflow_repo_slot(self) -> bool:
        if not self.allow_workflow_repo or self.remaining_workflow_repo_slots <= 0:
            return False
        self.remaining_workflow_repo_slots -= 1
        return True

    def consume_moonmind_slot(self) -> bool:
        if not self.allow_moonmind or self.remaining_moonmind_slots <= 0:
            return False
        self.remaining_moonmind_slots -= 1
        return True

    def has_workflow_repo_capacity(self) -> bool:
        return self.allow_workflow_repo and self.remaining_workflow_repo_slots > 0

    def has_moonmind_capacity(self) -> bool:
        return self.allow_moonmind and self.remaining_moonmind_slots > 0

    def severity_meets_floor(self, severity: str | None) -> bool:
        if not self.allow_moonmind:
            return False
        normalized = str(severity or "").strip().lower()
        if not normalized:
            return False
        candidate_rank = self.severity_rank.get(normalized)
        if candidate_rank is None:
            return False
        floor_rank = self.severity_rank.get(self.min_severity_for_moonmind, 0)
        return candidate_rank >= floor_rank

class CanonicalWorkflowExecutionPayload(BaseModel):
    """Top-level canonical queue payload for workflow jobs."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    repository: str | None = Field(
        None,
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
    auth: WorkflowAuthSelection | None = Field(
        None,
        alias="auth",
    )
    task: WorkflowExecutionSpec = Field(
        ...,
        alias="workflow",
        validation_alias=AliasChoices("workflow", "task"),
    )

    @field_validator("repository", mode="before")
    @classmethod
    def _normalize_repository(cls, value: object) -> str:
        cleaned = _clean_optional_str(value)
        if not cleaned:
            raise WorkflowContractError("repository is required")
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
            raise WorkflowContractError("requiredCapabilities must be a list")
        normalized = _normalize_capabilities(value)
        return normalized or None

    @model_validator(mode="before")
    @classmethod
    def _lift_legacy_top_level_shape(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        payload = dict(value)
        workflow_node = payload.get("workflow")
        task_node = payload.get("task")
        body_node = workflow_node if isinstance(workflow_node, Mapping) else task_node
        if not isinstance(body_node, Mapping):
            legacy_instruction = (
                payload.get("instructions") or payload.get("instruction") or ""
            )
            payload["workflow"] = {
                "instructions": legacy_instruction,
                "runtime": {
                    "mode": payload.get("targetRuntime")
                    or payload.get("target_runtime")
                    or payload.get("runtime")
                },
            }
        else:
            # Canonical queue payloads may be hydrated from pre-cutover stored
            # records. Preserve historical read tolerance here while keeping
            # direct WorkflowExecutionSpec validation strict for new submissions.
            body_node = strip_workflow_capability_identity_versions(body_node)
            if not body_node.get("instructions"):
                lifted_instruction = payload.get("instructions") or payload.get(
                    "instruction"
                )
                if lifted_instruction:
                    body_node["instructions"] = lifted_instruction
            payload["workflow"] = body_node
        payload.pop("task", None)
        return payload

def _assign_sequential_step_ids(steps: list[Any]) -> None:
    """Ensure every task step has a deterministic `step-{index}` identifier."""

    for index, raw in enumerate(steps):
        if not isinstance(raw, Mapping):
            steps[index] = {"id": f"step-{index + 1}"}
            continue
        if isinstance(raw, dict):
            target = raw
        else:
            target = dict(raw)
            steps[index] = target
        target["id"] = f"step-{index + 1}"

def _build_spec_from_codex_exec_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    publish_raw = payload.get("publish")
    publish = publish_raw if isinstance(publish_raw, Mapping) else {}
    codex_raw = payload.get("codex")
    codex = codex_raw if isinstance(codex_raw, Mapping) else {}
    publish_payload = {
        "mode": _normalize_publish_mode(publish.get("mode")),
        "prBaseBranch": _clean_optional_str(
            publish.get("prBaseBranch") or publish.get("baseBranch")
        ),
        "commitMessage": None,
        "prTitle": None,
        "prBody": None,
    }
    if "verificationSkipReason" in publish:
        publish_payload["verificationSkipReason"] = publish.get(
            "verificationSkipReason"
        )
    if "verification" in publish:
        publish_payload["verification"] = publish.get("verification")

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
            "targetBranch": None,
        },
        "publish": publish_payload,
    }

def _build_auth_from_payload(payload: Mapping[str, Any]) -> dict[str, Any] | None:
    """Normalize optional auth secret reference object from source payload."""

    raw_auth = payload.get("auth")
    if not isinstance(raw_auth, Mapping):
        return None
    try:
        auth = WorkflowAuthSelection.model_validate(dict(raw_auth))
    except (ValidationError, WorkflowContractError) as exc:
        raise WorkflowContractError(str(exc)) from exc
    return auth.model_dump(by_alias=True, exclude_none=False)

def _build_spec_from_codex_skill_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    raw_inputs = payload.get("inputs")
    inputs = dict(raw_inputs) if isinstance(raw_inputs, Mapping) else {}
    codex_raw = payload.get("codex")
    codex = codex_raw if isinstance(codex_raw, Mapping) else {}
    input_codex_raw = inputs.get("codex")
    input_codex = input_codex_raw if isinstance(input_codex_raw, Mapping) else {}

    skill_id = _clean_optional_str(payload.get("skillId")) or "auto"
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
    publish_mode = _clean_optional_str(
        inputs.get("publishMode")
    ) or _clean_optional_str(payload.get("publishMode"))
    publish_base = (
        _clean_optional_str(inputs.get("publishBaseBranch"))
        or _clean_optional_str(payload.get("publishBaseBranch"))
        or _clean_optional_str(
            (inputs.get("publish") or {}).get("prBaseBranch")
            if isinstance(inputs.get("publish"), Mapping)
            else None
        )
        or _clean_optional_str(
            (inputs.get("publish") or {}).get("baseBranch")
            if isinstance(inputs.get("publish"), Mapping)
            else None
        )
        or None
    )
    inputs_publish = inputs.get("publish")
    skill_publish_node = inputs_publish if isinstance(inputs_publish, Mapping) else {}
    if publish_mode is None and "mode" in skill_publish_node:
        publish_mode = _clean_optional_str(skill_publish_node.get("mode"))
    if publish_base is None and "prBaseBranch" in skill_publish_node:
        publish_base = _clean_optional_str(skill_publish_node.get("prBaseBranch"))
    if publish_base is None and "baseBranch" in skill_publish_node:
        publish_base = _clean_optional_str(skill_publish_node.get("baseBranch"))
    ref = _clean_optional_str(inputs.get("ref")) or _clean_optional_str(
        payload.get("ref")
    )
    publish_payload = {
        # Preserve an omitted publish mode as ``None`` so the auto-publish-capable
        # skill resolver applies the per-skill default from
        # docs/Workflows/WorkflowPublishing.md. Materializing the default ``pr``
        # here would make ``resolve_publish_mode_for_skill`` treat the absent mode
        # as an explicit forbidden mode and reject the request.
        "mode": _normalize_publish_mode(publish_mode)
        if publish_mode is not None
        else None,
        "prBaseBranch": publish_base,
        "commitMessage": None,
        "prTitle": None,
        "prBody": None,
    }
    if "verificationSkipReason" in skill_publish_node:
        publish_payload["verificationSkipReason"] = skill_publish_node.get(
            "verificationSkipReason"
        )
    if "verification" in skill_publish_node:
        publish_payload["verification"] = skill_publish_node.get("verification")

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
            "targetBranch": None,
        },
        "publish": publish_payload,
    }
    if repository:
        task["skill"]["args"].setdefault("repo", repository)
    return task

def build_canonical_workflow_view(
    *,
    job_type: str,
    payload: Mapping[str, Any] | None,
    default_runtime: str = DEFAULT_WORKFLOW_RUNTIME,
) -> dict[str, Any]:
    """Return a canonical task-view payload for queue processing."""

    source = dict(payload or {})
    normalized_type = _clean_str(job_type)
    resolved_default_runtime = _normalize_runtime_value(
        default_runtime, field_name="default runtime"
    )
    if resolved_default_runtime in {None, "universal"}:
        resolved_default_runtime = DEFAULT_WORKFLOW_RUNTIME

    if normalized_type == CANONICAL_WORKFLOW_JOB_TYPE:
        try:
            model = CanonicalWorkflowExecutionPayload.model_validate(source)
        except (ValidationError, WorkflowContractError) as exc:
            raise WorkflowContractError(str(exc)) from exc
        canonical = model.model_dump(by_alias=True, exclude_none=False)
    elif normalized_type == "codex_exec":
        repository = _clean_optional_str(source.get("repository")) or ""
        if not repository:
            raise WorkflowContractError("repository is required")
        canonical = {
            "repository": repository,
            "targetRuntime": "codex",
            "auth": _build_auth_from_payload(source),
            "workflow": _build_spec_from_codex_exec_payload(source),
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
            raise WorkflowContractError("repository is required")
        canonical = {
            "repository": repository,
            "targetRuntime": "codex",
            "auth": _build_auth_from_payload(source),
            "workflow": _build_spec_from_codex_skill_payload(source),
        }
    else:
        canonical = {
            "repository": _clean_optional_str(source.get("repository")) or "",
            "targetRuntime": resolved_default_runtime,
            "auth": _build_auth_from_payload(source),
            "workflow": {
                "instructions": _clean_optional_str(source.get("instruction"))
                or "Queue job",
                "skill": {"id": "auto", "args": {}},
                "runtime": {
                    "mode": resolved_default_runtime,
                    "model": None,
                    "effort": None,
                },
                "git": {"startingBranch": None, "targetBranch": None},
                "publish": {
                    "mode": _normalize_publish_mode(None),
                    "prBaseBranch": None,
                    "commitMessage": None,
                    "prTitle": None,
                    "prBody": None,
                },
            },
        }

    workflow_node = canonical.get("workflow")
    if not isinstance(workflow_node, dict):
        workflow_node = {}
        canonical["workflow"] = workflow_node

    target_runtime = (
        _normalize_runtime_value(
            canonical.get("targetRuntime"), field_name="targetRuntime"
        )
        or _normalize_runtime_value(
            (workflow_node.get("runtime") or {}).get("mode"),
            field_name="workflow.runtime.mode",
        )
        or resolved_default_runtime
    )
    if target_runtime == "universal":
        target_runtime = resolved_default_runtime

    runtime_node = workflow_node.get("runtime")
    if not isinstance(runtime_node, dict):
        runtime_node = {}
        workflow_node["runtime"] = runtime_node
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
    workflow_git_node = (
        workflow_node.get("git") if isinstance(workflow_node, Mapping) else None
    )
    workflow_git = workflow_git_node if isinstance(workflow_git_node, Mapping) else {}
    has_git_checkout_context = any(
        _clean_optional_str(workflow_git.get(key))
        for key in (
            "repository",
            "repo",
            "branch",
            "startingBranch",
            "targetBranch",
            "ref",
        )
    )
    if _clean_optional_str(canonical.get("repository")) or has_git_checkout_context:
        required.append("git")

    source_publish_mode = None
    if normalized_type == CANONICAL_WORKFLOW_JOB_TYPE:
        source_workflow = source.get("workflow")
        if not isinstance(source_workflow, Mapping):
            source_workflow = source.get("task")
        if isinstance(source_workflow, Mapping):
            source_publish = source_workflow.get("publish")
            if isinstance(source_publish, Mapping):
                source_publish_mode = source_publish.get("mode")

    publish_mode_candidate = (
        source_publish_mode
        if normalized_type == CANONICAL_WORKFLOW_JOB_TYPE
        else (workflow_node.get("publish") or {}).get("mode")
    )
    workflow_payload = workflow_node
    publish_node = workflow_payload.get("publish")
    if not isinstance(publish_node, dict):
        publish_node = {}
        workflow_payload["publish"] = publish_node
    skill_node = workflow_payload.get("skill") or {}
    skill = skill_node if isinstance(skill_node, Mapping) else {}
    skill_id = skill.get("id") or skill.get("name")
    skill_publish_metadata = (
        skill.get("publish") if isinstance(skill.get("publish"), Mapping) else None
    )
    skill_side_effect_metadata = (
        skill.get("sideEffect")
        if isinstance(skill.get("sideEffect"), Mapping)
        else None
    )
    publish_mode = resolve_publish_mode_for_skill(
        skill_id,
        publish_mode_candidate,
        allow_repository_publish=allows_repository_publish_for_skill_context(workflow_payload),
        publish_metadata=skill_publish_metadata,
        side_effect_metadata=skill_side_effect_metadata,
    )
    publish_node["mode"] = publish_mode
    if publish_mode == "pr":
        required.append("gh")

    skill_caps = skill_node.get("requiredCapabilities")
    required.extend(_skill_metadata_required_capabilities(skill_id))
    if isinstance(skill_caps, list):
        required.extend(skill_caps)

    steps_node = (canonical.get("workflow") or {}).get("steps")
    if isinstance(steps_node, list):
        _assign_sequential_step_ids(steps_node)
        for step_raw in steps_node:
            if not isinstance(step_raw, Mapping):
                continue
            step_skill_raw = step_raw.get("skill")
            step_skill = step_skill_raw if isinstance(step_skill_raw, Mapping) else {}
            step_skill_id = step_skill.get("id") or step_skill.get("name")
            required.extend(_skill_metadata_required_capabilities(step_skill_id))
            step_skill_caps = step_skill.get("requiredCapabilities")
            if isinstance(step_skill_caps, list):
                required.extend(step_skill_caps)
            step_tool_raw = step_raw.get("tool")
            step_tool = step_tool_raw if isinstance(step_tool_raw, Mapping) else {}
            step_tool_caps = step_tool.get("requiredCapabilities")
            if isinstance(step_tool_caps, list):
                required.extend(step_tool_caps)

    container_node = (canonical.get("workflow") or {}).get("container")
    container = container_node if isinstance(container_node, Mapping) else {}
    if bool(container.get("enabled")):
        required.append("docker")

    canonical["requiredCapabilities"] = _normalize_capabilities(tuple(required))
    return canonical

def build_effective_proposal_policy(
    *,
    policy: WorkflowProposalPolicy | None,
    default_targets: str,
    default_max_items_workflow_repo: int,
    default_max_items_moonmind: int,
    default_moonmind_severity_floor: str,
    severity_vocabulary: Sequence[str] | None = None,
) -> EffectiveProposalPolicy:
    """Merge defaults + overrides into a runtime proposal policy helper."""

    normalized_vocab = [
        str(token or "").strip().lower()
        for token in (severity_vocabulary or _PROPOSAL_SEVERITIES)
    ]
    filtered_vocab = [
        token for token in normalized_vocab if token in _PROPOSAL_SEVERITIES
    ]
    if not filtered_vocab:
        filtered_vocab = list(_PROPOSAL_SEVERITIES)

    # Preserve canonical severity progression regardless of operator-provided order.
    severity_rank = {token: index for index, token in enumerate(_PROPOSAL_SEVERITIES)}

    default_targets_normalized = str(default_targets or "").strip().lower()
    if default_targets_normalized == "both":
        default_target_list = list(_PROPOSAL_POLICY_TARGETS)
    elif default_targets_normalized in _PROPOSAL_POLICY_TARGETS:
        default_target_list = [default_targets_normalized]
    else:
        default_target_list = ["workflow_repo"]
    configured_targets = (
        list(policy.targets) if policy and policy.targets else default_target_list
    )
    allow_workflow_repo = "workflow_repo" in configured_targets
    allow_moonmind = "moonmind" in configured_targets
    if not allow_workflow_repo and not allow_moonmind:
        allow_workflow_repo = True

    max_items = dict(policy.max_items or {}) if policy and policy.max_items else {}
    max_items_workflow_repo = int(max_items.get("workflow_repo") or 0)
    if max_items_workflow_repo <= 0:
        max_items_workflow_repo = max(1, int(default_max_items_workflow_repo or 1))
    max_items_moonmind = int(max_items.get("moonmind") or 0)
    if max_items_moonmind <= 0:
        max_items_moonmind = max(1, int(default_max_items_moonmind or 1))

    severity_floor = (
        (policy.min_severity_for_moonmind or "").strip().lower()
        if policy and policy.min_severity_for_moonmind
        else str(default_moonmind_severity_floor or "").strip().lower()
    )
    if not severity_floor:
        severity_floor = "high"
    if severity_floor not in severity_rank:
        if "high" in severity_rank:
            severity_floor = "high"
        else:
            severity_floor = filtered_vocab[-1]

    return EffectiveProposalPolicy(
        allow_workflow_repo=allow_workflow_repo,
        allow_moonmind=allow_moonmind,
        max_items_workflow_repo=max_items_workflow_repo,
        max_items_moonmind=max_items_moonmind,
        min_severity_for_moonmind=severity_floor,
        severity_rank=severity_rank,
        delivery_provider=(
            policy.delivery.provider
            if (
                policy
                and policy.delivery
                and policy.delivery.provider
            )
            else "auto"
        ),
        provider_metadata=(
            policy.delivery.model_dump(by_alias=False, exclude_none=True)
            if policy and policy.delivery
            else {}
        ),
    )

def normalize_queue_job_payload(
    *,
    job_type: str,
    payload: Mapping[str, Any] | None,
    default_runtime: str = DEFAULT_WORKFLOW_RUNTIME,
) -> dict[str, Any]:
    """Normalize queue payloads while preserving legacy compatibility fields."""

    source = dict(payload or {})
    normalized_type = _clean_str(job_type)
    canonical = build_canonical_workflow_view(
        job_type=normalized_type,
        payload=source,
        default_runtime=default_runtime,
    )

    if normalized_type == CANONICAL_WORKFLOW_JOB_TYPE:
        return canonical

    if normalized_type in LEGACY_WORKFLOW_JOB_TYPES:
        source["repository"] = canonical.get("repository")
        source["targetRuntime"] = canonical.get("targetRuntime")
        source["auth"] = canonical.get("auth")
        source["requiredCapabilities"] = canonical.get("requiredCapabilities")
        source["workflow"] = canonical.get("workflow")
        return source

    required = source.get("requiredCapabilities")
    if isinstance(required, list):
        source["requiredCapabilities"] = _normalize_capabilities(tuple(required))
    return source

def has_attachment_mutation_fields(payload: Mapping[str, Any] | None) -> bool:
    """Return ``True`` when payload includes unsupported attachment edit fields."""

    source = payload if isinstance(payload, Mapping) else {}
    forbidden = {"attachments", "attachmentIds", "attachment_ids"}
    for key in forbidden:
        if key in source:
            return True
    task_node = source.get("task")
    if isinstance(task_node, Mapping):
        for key in forbidden:
            if key in task_node:
                return True
    return False


def _snapshot_safe(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _snapshot_safe(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_snapshot_safe(item) for item in value]
    return value


def _snapshot_mapping(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    safe = _snapshot_safe(value)
    return safe if isinstance(safe, dict) else {}


def _snapshot_list(value: object) -> list[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    safe = _snapshot_safe(value)
    return safe if isinstance(safe, list) else []


def _spec_steps(task_payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        _snapshot_mapping(step)
        for step in _snapshot_list(task_payload.get("steps"))
        if isinstance(step, Mapping)
    ]


def _safe_mapping(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _safe_list(value: object) -> list[Any]:
    return value if isinstance(value, list) else []


def _first_present_snapshot_list(
    source: Mapping[str, Any],
    *keys: str,
    fallback: object = None,
) -> list[Any]:
    for key in keys:
        if key in source:
            return _snapshot_list(source.get(key))
    return _snapshot_list(fallback)


def _detect_jira_issue_key(task_payload: Mapping[str, Any]) -> str | None:
    pattern = re.compile(r"\b[A-Z][A-Z0-9]+-\d+\b")
    primary_values: list[object] = [
        task_payload.get("title"),
        task_payload.get("instructions"),
        task_payload.get("description"),
        task_payload.get("objective"),
    ]
    for value in primary_values:
        if isinstance(value, str):
            match = pattern.search(value)
            if match:
                return match.group(0)

    stack: list[object] = [task_payload.get("steps")]
    while stack:
        value = stack.pop()
        if isinstance(value, str):
            match = pattern.search(value)
            if match:
                return match.group(0)
            continue
        if isinstance(value, Mapping):
            stack.extend(value.values())
            continue
        if isinstance(value, Sequence) and not isinstance(
            value, (str, bytes, bytearray)
        ):
            stack.extend(value)
    return None


def _step_identifier(step: Mapping[str, Any], ordinal: int) -> str:
    return (
        _clean_optional_str(step.get("id"))
        or _clean_optional_str(step.get("stepId"))
        or f"step-{ordinal + 1}"
    )


def _include_tree_summary(
    task_payload: Mapping[str, Any],
) -> list[dict[str, Any]]:
    summary: list[dict[str, Any]] = []
    for template in _safe_list(task_payload.get("appliedStepTemplates")):
        if not isinstance(template, Mapping):
            continue
        parent_slug = _clean_optional_str(
            template.get("slug") or template.get("presetSlug")
        )
        parent_digest = _clean_optional_str(template.get("presetDigest"))
        composition = template.get("composition")
        candidates: list[Any] = []
        if isinstance(composition, Mapping):
            candidates.extend(_safe_list(composition.get("includes")))
        candidates.extend(_safe_list(template.get("includes")))
        for include in candidates:
            if not isinstance(include, Mapping):
                continue
            summary.append(
                {
                    "presetSlug": parent_slug,
                    "presetDigest": parent_digest,
                    "includedSlug": _clean_optional_str(
                        include.get("slug") or include.get("presetSlug")
                    ),
                    "includedDigest": _clean_optional_str(include.get("presetDigest")),
                }
            )
    return summary


def build_authoritative_workflow_input_snapshot(
    *,
    task_payload: Mapping[str, Any],
    repository: object = None,
    target_runtime: object = None,
    required_capabilities: object = None,
    dependency_declarations: object = None,
    attachment_refs: object = None,
) -> dict[str, Any]:
    """Build explicit authored fields for durable task input reconstruction."""

    task = _snapshot_mapping(task_payload)
    git = _safe_mapping(task.get("git"))
    steps = [
        step for step in _safe_list(task.get("steps")) if isinstance(step, Mapping)
    ]
    runtime_mode = _runtime_mode_from_spec(task, target_runtime=target_runtime)
    repository_value = (
        _clean_optional_str(git.get("repository"))
        or _clean_optional_str(repository)
        or None
    )
    branch_value = (
        _clean_optional_str(task.get("branch"))
        or _clean_optional_str(git.get("branch"))
        or _clean_optional_str(git.get("startingBranch"))
        or None
    )
    authored_steps: list[dict[str, Any]] = []
    final_order: list[dict[str, Any]] = []
    provenance: list[dict[str, Any]] = []
    detachment: list[dict[str, Any]] = []
    for ordinal, step in enumerate(steps):
        step_id = _step_identifier(step, ordinal)
        step_runtime = _safe_mapping(step.get("runtime"))
        step_runtime_mode = _runtime_mode_from_spec(
            {"runtime": step_runtime},
            target_runtime=runtime_mode,
        )
        step_runtime_command = _build_runtime_command_metadata(
            raw_instructions_value=step.get("instructions"),
            source_path=f"steps[{ordinal}].instructions",
            target_runtime=step_runtime_mode or runtime_mode,
            target_step_id=step_id,
        )
        _validate_supplied_runtime_command(
            step.get("runtimeCommand"),
            step_runtime_command,
            field_name=f"workflow.steps[{ordinal}].runtimeCommand",
        )
        final_order.append({"stepId": step_id, "ordinal": ordinal})
        authored_step = {
            "id": step_id,
            "title": _clean_optional_str(step.get("title")),
            "instructions": _raw_instruction_string(step.get("instructions")),
            "inputAttachments": _safe_list(step.get("inputAttachments")),
            "templateStepId": _clean_optional_str(step.get("templateStepId")),
            "stepType": _clean_optional_str(step.get("type")),
            "presetProvenance": _safe_mapping(step.get("presetProvenance")),
        }
        if step_runtime:
            authored_step["runtime"] = step_runtime
        if step_runtime_command is not None:
            authored_step["runtimeCommand"] = step_runtime_command
        authored_steps.append(authored_step)
        if isinstance(step.get("presetProvenance"), Mapping):
            provenance.append(
                {
                    "stepId": step_id,
                    "ordinal": ordinal,
                    "presetProvenance": _safe_mapping(step.get("presetProvenance")),
                }
            )
        detached = bool(
            step.get("detachedFromPreset")
            or step.get("detached")
            or step.get("isDetached")
        )
        if detached:
            detachment.append(
                {"stepId": step_id, "ordinal": ordinal, "detached": True}
            )
    issue_key = _detect_jira_issue_key(task)
    objective_runtime_command = _build_runtime_command_metadata(
        raw_instructions_value=task.get("instructions"),
        source_path="objective.instructions",
        target_runtime=runtime_mode,
    )
    _validate_supplied_runtime_command(
        task.get("runtimeCommand"),
        objective_runtime_command,
        field_name="task.runtimeCommand",
    )
    objective = {
        "instructions": _raw_instruction_string(task.get("instructions")),
        "inputAttachments": _safe_list(task.get("inputAttachments")),
    }
    if objective_runtime_command is not None:
        objective["runtimeCommand"] = objective_runtime_command
    return {
        "traceability": {
            **({"jiraIssueKey": issue_key} if issue_key else {}),
        },
        "objective": objective,
        "steps": authored_steps,
        "runtime": _safe_mapping(task.get("runtime"))
        or (
            {"mode": runtime_mode}
            if runtime_mode
            else {}
        ),
        "publish": _safe_mapping(task.get("publish")),
        "repository": repository_value,
        "branch": branch_value,
        "singleAuthoredBranch": branch_value,
        "requiredCapabilities": _snapshot_list(required_capabilities),
        "dependencyDeclarations": _first_present_snapshot_list(
            task,
            "dependencies",
            "dependsOn",
            fallback=dependency_declarations,
        ),
        "presetApplicationMetadata": _safe_list(task.get("appliedStepTemplates")),
        "pinnedPresetBindings": _safe_list(task.get("authoredPresets")),
        "includeTreeSummary": _include_tree_summary(task),
        "perStepProvenance": provenance,
        "detachmentState": detachment,
        "finalSubmittedOrder": final_order,
        "attachmentRefs": _snapshot_list(attachment_refs),
    }


def build_workflow_stage_plan(canonical_payload: Mapping[str, Any]) -> list[str]:
    """Return ordered stage identifiers for canonical workflow execution."""

    task_node = canonical_payload.get("workflow")
    task = task_node if isinstance(task_node, Mapping) else {}
    publish_node = task.get("publish")
    publish = publish_node if isinstance(publish_node, Mapping) else {}
    publish_mode = _normalize_publish_mode(publish.get("mode"))

    # legacy_run contract — stage identifier values are persisted in workflow
    # state/history and rename at the MoonMind.UserWorkflow v2 cutover (MM-730).
    stages = ["moonmind.task.prepare", "moonmind.task.execute"]
    if publish_mode in {"branch", "pr"}:
        stages.append("moonmind.task.publish")
    return stages

__all__ = [
    "CANONICAL_WORKFLOW_JOB_TYPE",
    "DEFAULT_WORKFLOW_RUNTIME",
    "LEGACY_WORKFLOW_JOB_TYPES",
    "SUPPORTED_EXECUTION_RUNTIMES",
    "CanonicalWorkflowExecutionPayload",
    "AuthoredPresetBinding",
    "WorkflowContractError",
    "WorkflowInputAttachmentRef",
    "WorkflowRecoveryKind",
    "WorkflowRecoveryProvenance",
    "ResumeFromFailedStepRef",
    "WorkflowStepSource",
    "build_authoritative_workflow_input_snapshot",
    "build_runtime_command_preview_config",
    "build_workflow_stage_plan",
    "build_canonical_workflow_view",
    "allows_repository_publish_for_skill_context",
    "has_attachment_mutation_fields",
    "is_non_repository_side_effect_skill",
    "is_auto_publish_capable_skill",
    "is_self_managed_publish_skill",
    "normalize_queue_job_payload",
    "resolve_publish_mode_for_skill",
    "reject_workflow_capability_identity_versions",
    "strip_workflow_capability_identity_versions",
]
