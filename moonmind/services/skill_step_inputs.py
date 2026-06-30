"""Backend validation for authored Skill-step input payloads."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy.ext.asyncio import AsyncSession

from moonmind.schemas.agent_skill_models import ResolvedSkillEntry, SkillSelector
from moonmind.services.skill_resolution import (
    AgentSkillResolver,
    SkillResolutionContext,
)


@dataclass(slots=True)
class SkillInputValidationError:
    path: str
    message: str
    code: str
    recoverable: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "message": self.message,
            "code": self.code,
            "recoverable": self.recoverable,
        }


@dataclass(slots=True)
class SkillStepInputValidationResult:
    parameters: dict[str, Any]
    errors: list[SkillInputValidationError] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return not self.errors

    def error_dicts(self) -> list[dict[str, Any]]:
        return [error.as_dict() for error in self.errors]


@dataclass(slots=True)
class _SkillInputContract:
    input_schema: dict[str, Any]
    defaults: dict[str, Any]
    contract_digest: str


async def validate_skill_step_inputs(
    *,
    initial_parameters: Mapping[str, Any] | None,
    session: AsyncSession | None = None,
    workflow_context: Mapping[str, Any] | None = None,
) -> SkillStepInputValidationResult:
    """Validate Skill-step inputs and return a normalized parameters copy.

    This is the backend-owned boundary required by MM-1052. It resolves the
    selected Skill evidence before loading the input contract, maps legacy
    ``args`` payloads to ``inputs``, applies safe defaults, and emits errors at
    ``steps[n].skill.inputs`` paths.
    """

    normalized = deepcopy(dict(initial_parameters or {}))
    workflow_payload = _workflow_payload(normalized)
    steps = workflow_payload.get("steps")
    if not isinstance(steps, list):
        return SkillStepInputValidationResult(parameters=normalized)

    context = dict(workflow_context or {})
    context.update(_workflow_context_from_parameters(normalized, workflow_payload))

    errors: list[SkillInputValidationError] = []
    for index, step in enumerate(steps):
        if not isinstance(step, dict):
            continue
        skill_payload = step.get("skill")
        if not isinstance(skill_payload, dict):
            continue
        skill_name = str(
            skill_payload.get("name") or skill_payload.get("id") or ""
        ).strip()
        if not skill_name or skill_name.lower() == "auto":
            continue

        entry = await _resolve_skill_entry(
            skill_name=skill_name,
            skill_payload=skill_payload,
            session=session,
        )
        if entry is None:
            errors.append(
                SkillInputValidationError(
                    path=f"steps[{index}].skill",
                    message=f"Selected Skill '{skill_name}' could not be resolved.",
                    code="skill_not_found",
                )
            )
            continue

        contract = await _load_input_contract(
            entry=entry,
            skill_payload=skill_payload,
            session=session,
        )
        raw_inputs = _skill_inputs(skill_payload)
        values = _apply_defaults(
            schema=contract.input_schema,
            defaults=contract.defaults,
            values=raw_inputs,
            context=context,
        )
        errors.extend(
            _validate_schema_object(
                schema=contract.input_schema,
                values=values,
                path=f"steps[{index}].skill.inputs",
            )
        )
        skill_payload["name"] = skill_name
        skill_payload.pop("id", None)
        skill_payload.pop("args", None)
        skill_payload["inputs"] = values
        if entry.content_ref:
            skill_payload["contentRef"] = entry.content_ref
        if entry.content_digest:
            skill_payload["contentDigest"] = entry.content_digest
        skill_payload["inputContractDigest"] = contract.contract_digest

    return SkillStepInputValidationResult(parameters=normalized, errors=errors)


def _workflow_payload(parameters: dict[str, Any]) -> dict[str, Any]:
    workflow = parameters.get("workflow")
    if isinstance(workflow, dict):
        return workflow
    task = parameters.get("task")
    if isinstance(task, dict):
        return task
    return {}


def _workflow_context_from_parameters(
    parameters: Mapping[str, Any],
    workflow_payload: Mapping[str, Any],
) -> dict[str, Any]:
    context: dict[str, Any] = {}
    for key in ("repository", "repo", "branch", "startingBranch"):
        value = workflow_payload.get(key, parameters.get(key))
        if value is not None:
            context[key] = value
    git_payload = workflow_payload.get("git")
    if isinstance(git_payload, Mapping):
        for key in ("branch", "startingBranch"):
            if git_payload.get(key) is not None:
                context[key] = git_payload[key]
    return context


def _skill_inputs(skill_payload: Mapping[str, Any]) -> dict[str, Any]:
    for key in ("inputs", "args", "selectedSkillArgs", "selected_skill_args"):
        value = skill_payload.get(key)
        if isinstance(value, Mapping):
            return deepcopy(dict(value))
    return {}


async def _resolve_skill_entry(
    *,
    skill_name: str,
    skill_payload: Mapping[str, Any],
    session: AsyncSession | None,
) -> ResolvedSkillEntry | None:
    content_ref = str(skill_payload.get("contentRef") or "").strip() or None
    content_digest = str(skill_payload.get("contentDigest") or "").strip() or None
    if content_ref or content_digest:
        from moonmind.schemas.agent_skill_models import (
            AgentSkillProvenance,
            AgentSkillSourceKind,
        )

        return ResolvedSkillEntry(
            skill_name=skill_name,
            content_ref=content_ref,
            content_digest=content_digest,
            provenance=AgentSkillProvenance(
                source_kind=AgentSkillSourceKind.DEPLOYMENT
            ),
        )

    context = SkillResolutionContext(
        snapshot_id=f"validate-{skill_name}",
        async_session_maker=None,
    )
    try:
        resolved = await AgentSkillResolver().resolve(
            selector=SkillSelector.model_validate({"include": [{"name": skill_name}]}),
            context=context,
        )
    except ValueError:
        return None
    for entry in resolved.skills:
        if entry.skill_name == skill_name:
            return entry

    return None


async def _load_input_contract(
    *,
    entry: ResolvedSkillEntry,
    skill_payload: Mapping[str, Any],
    session: AsyncSession | None,
) -> _SkillInputContract:
    metadata: Mapping[str, Any] = {}
    markdown: str | None = None
    if entry.content_ref and session is not None:
        from api_service.db.models import TemporalArtifact

        artifact = await session.get(TemporalArtifact, entry.content_ref)
        if artifact is not None and isinstance(artifact.metadata_json, Mapping):
            metadata = artifact.metadata_json
    if not metadata and entry.provenance.source_path:
        skill_path = Path(entry.provenance.source_path) / "SKILL.md"
        try:
            markdown = skill_path.read_text(encoding="utf-8")
        except OSError:
            markdown = None
        if markdown is not None and not entry.content_digest:
            entry = entry.model_copy(
                update={
                    "content_digest": "sha256:"
                    + hashlib.sha256(markdown.encode("utf-8")).hexdigest()
                }
            )

    input_schema = _mapping(metadata.get("input_schema") or metadata.get("inputSchema"))
    defaults = _mapping(metadata.get("defaults"))
    digest = str(
        metadata.get("input_contract_digest")
        or metadata.get("inputContractDigest")
        or skill_payload.get("inputContractDigest")
        or ""
    ).strip()
    if not input_schema and markdown is not None:
        frontmatter = _frontmatter(markdown)
        input_schema = _mapping(
            frontmatter.get("inputSchema") or frontmatter.get("input_schema")
        )
        defaults = _mapping(frontmatter.get("defaults"))
    if not digest:
        digest = _contract_digest(
            input_schema=input_schema,
            defaults=defaults,
            content_digest=str(entry.content_digest or ""),
        )
    return _SkillInputContract(
        input_schema=input_schema,
        defaults=defaults,
        contract_digest=digest,
    )


def _mapping(value: Any) -> dict[str, Any]:
    return deepcopy(dict(value)) if isinstance(value, Mapping) else {}


def _frontmatter(markdown: str) -> dict[str, Any]:
    lines = markdown.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    collected: list[str] = []
    for line in lines[1:]:
        if line.strip() == "---":
            break
        collected.append(line)
    else:
        return {}
    parsed = yaml.safe_load("\n".join(collected)) or {}
    return dict(parsed) if isinstance(parsed, Mapping) else {}


def _contract_digest(
    *,
    input_schema: Mapping[str, Any],
    defaults: Mapping[str, Any],
    content_digest: str,
) -> str:
    payload = {
        "parser": "moonmind.skill-input-contract.v1",
        "contentDigest": content_digest,
        "inputSchema": input_schema,
        "defaults": defaults,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _apply_defaults(
    *,
    schema: Mapping[str, Any],
    defaults: Mapping[str, Any],
    values: Mapping[str, Any],
    context: Mapping[str, Any],
) -> dict[str, Any]:
    result = deepcopy(dict(values))
    properties = schema.get("properties")
    if not isinstance(properties, Mapping):
        return result
    for name, raw_property_schema in properties.items():
        if name in result:
            continue
        property_schema = (
            raw_property_schema if isinstance(raw_property_schema, Mapping) else {}
        )
        context_key = property_schema.get("x-moonmind-context-default")
        if isinstance(context_key, str) and context_key in context:
            result[name] = deepcopy(context[context_key])
            continue
        if name in defaults:
            result[name] = deepcopy(defaults[name])
            continue
        if "default" in property_schema:
            result[name] = deepcopy(property_schema["default"])
    return result


def _validate_schema_object(
    *,
    schema: Mapping[str, Any],
    values: Mapping[str, Any],
    path: str,
) -> list[SkillInputValidationError]:
    if not schema:
        return []
    errors: list[SkillInputValidationError] = []
    if schema.get("type", "object") != "object":
        errors.append(
            SkillInputValidationError(
                path=path,
                message="Skill input schema root must be an object.",
                code="invalid_schema",
            )
        )
        return errors
    required = schema.get("required")
    if isinstance(required, list):
        for field_name in required:
            if not isinstance(field_name, str):
                continue
            if field_name not in values or values.get(field_name) in (None, ""):
                errors.append(
                    SkillInputValidationError(
                        path=f"{path}.{field_name}",
                        message=f"{field_name} is required.",
                        code="required",
                    )
                )
    properties = schema.get("properties")
    if not isinstance(properties, Mapping):
        return errors
    for field_name, raw_property_schema in properties.items():
        if field_name not in values or not isinstance(field_name, str):
            continue
        property_schema = (
            raw_property_schema if isinstance(raw_property_schema, Mapping) else {}
        )
        errors.extend(
            _validate_value(
                schema=property_schema,
                value=values[field_name],
                path=f"{path}.{field_name}",
            )
        )
    return errors


def _validate_value(
    *,
    schema: Mapping[str, Any],
    value: Any,
    path: str,
) -> list[SkillInputValidationError]:
    errors: list[SkillInputValidationError] = []
    enum = schema.get("enum")
    if isinstance(enum, list) and value not in enum:
        errors.append(
            SkillInputValidationError(
                path=path,
                message="Value must be one of the allowed options.",
                code="enum",
            )
        )
    expected_type = schema.get("type")
    if isinstance(expected_type, list):
        expected_types = [item for item in expected_type if isinstance(item, str)]
    elif isinstance(expected_type, str):
        expected_types = [expected_type]
    else:
        expected_types = []
    if not expected_types or ("null" in expected_types and value is None):
        return errors
    if not any(_matches_json_type(value, type_name) for type_name in expected_types):
        errors.append(
            SkillInputValidationError(
                path=path,
                message=f"Value must be {', '.join(expected_types)}.",
                code="type",
            )
        )
        return errors
    if isinstance(value, Mapping) and schema.get("type") == "object":
        nested_schema = schema.get("properties")
        if isinstance(nested_schema, Mapping):
            required = schema.get("required")
            if isinstance(required, list):
                for nested_name in required:
                    if (
                        isinstance(nested_name, str)
                        and value.get(nested_name) in (None, "")
                    ):
                        errors.append(
                            SkillInputValidationError(
                                path=f"{path}.{nested_name}",
                                message=f"{nested_name} is required.",
                                code="required",
                            )
                        )
            for nested_name, raw_nested_schema in nested_schema.items():
                if isinstance(nested_name, str) and nested_name in value:
                    errors.extend(
                        _validate_value(
                            schema=raw_nested_schema
                            if isinstance(raw_nested_schema, Mapping)
                            else {},
                            value=value[nested_name],
                            path=f"{path}.{nested_name}",
                        )
                    )
    return errors


def _matches_json_type(value: Any, type_name: str) -> bool:
    if type_name == "string":
        return isinstance(value, str)
    if type_name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if type_name == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if type_name == "boolean":
        return isinstance(value, bool)
    if type_name == "object":
        return isinstance(value, Mapping)
    if type_name == "array":
        return isinstance(value, list)
    if type_name == "null":
        return value is None
    return True
