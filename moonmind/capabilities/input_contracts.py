"""Normalize renderer-facing input contracts for MoonMind capabilities."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal
from urllib.parse import urlparse

import yaml

CapabilityKind = Literal["tool", "skill", "preset"]

PARSER_VERSION = "capability-input-contracts:v1"
_SECRET_LIKE_VALUE_PATTERN = re.compile(
    r"(token=|password=|bearer\s+|ghp_|github_pat_|akia[0-9a-z]{16}|aiza|atatt|-----begin [a-z ]*private key)",
    re.IGNORECASE,
)
_REGISTERED_WIDGETS = {
    "text",
    "textarea",
    "markdown",
    "number",
    "checkbox",
    "select",
    "multi-select",
    "json",
    "jira.issue-picker",
    "github.issue-picker",
    "github.repository-picker",
    "github.branch-picker",
    "provider.profile-picker",
    "model-picker",
    "file-reference-picker",
}
_CODE_LIKE_SCHEMA_KEYS = {
    "script",
    "scripts",
    "function",
    "functions",
    "eval",
    "expression",
    "x-code",
    "x-moonmind-code",
    "x-moonmind-transform",
}


@dataclass(frozen=True, slots=True)
class CapabilityInputOwner:
    """Identity and provenance for a capability input contract."""

    id: str
    kind: CapabilityKind
    label: str
    description: str | None = None
    content_digest: str | None = None
    source: Mapping[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class CapabilityInputContractParts:
    """Raw schema metadata before owner fields and digests are attached."""

    input_schema: Mapping[str, Any] | None = None
    ui_schema: Mapping[str, Any] | None = None
    defaults: Mapping[str, Any] | None = None
    diagnostics: list[dict[str, Any]] = field(default_factory=list)


def content_digest_for_text(content: str) -> str:
    """Return a deterministic content digest for text."""

    return "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()


def parse_skill_markdown_frontmatter(content: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Parse YAML frontmatter from a SKILL.md file with a safe loader."""

    if not content.startswith("---"):
        return {}, []
    marker = "\n---"
    end = content.find(marker, 3)
    if end < 0:
        return {}, [
            _diagnostic(
                code="frontmatter_unclosed",
                message="Skill frontmatter is not closed; structured metadata was ignored.",
                severity="warning",
                path="frontmatter",
            )
        ]
    raw_frontmatter = content[3:end]
    try:
        parsed = yaml.safe_load(raw_frontmatter) or {}
    except yaml.YAMLError as exc:
        return {}, [
            _diagnostic(
                code="frontmatter_yaml_invalid",
                message="Skill frontmatter YAML could not be parsed; structured metadata was ignored.",
                severity="warning",
                path="frontmatter",
                details={"error": str(exc)},
            )
        ]
    if not isinstance(parsed, Mapping):
        return {}, [
            _diagnostic(
                code="frontmatter_not_object",
                message="Skill frontmatter must be a YAML mapping; structured metadata was ignored.",
                severity="warning",
                path="frontmatter",
            )
        ]
    return dict(parsed), []


def parse_capability_input_contract(
    raw_metadata: Mapping[str, Any] | None,
) -> CapabilityInputContractParts:
    """Extract schema metadata using accepted source casing."""

    metadata = raw_metadata or {}
    input_schema = _first_mapping(metadata, "inputSchema", "input_schema")
    ui_schema = _first_mapping(metadata, "uiSchema", "ui_schema")
    defaults = _first_mapping(metadata, "defaults")
    return CapabilityInputContractParts(
        input_schema=_json_compatible_mapping(input_schema),
        ui_schema=_json_compatible_mapping(ui_schema),
        defaults=_json_compatible_mapping(defaults),
    )


def capability_contract_from_legacy_inputs(
    *,
    inputs_schema: Sequence[Mapping[str, Any]],
    annotations: Mapping[str, Any] | None,
) -> CapabilityInputContractParts:
    """Build contract parts from preset annotations or legacy input rows."""

    parts = parse_capability_input_contract(annotations)
    if isinstance(parts.input_schema, Mapping):
        return parts

    properties: dict[str, Any] = {}
    required: list[str] = []
    ui_schema: dict[str, Any] = {}
    defaults: dict[str, Any] = {}
    for definition in inputs_schema:
        name = str(definition.get("name") or "").strip()
        if not name:
            continue
        properties[name] = _legacy_input_to_json_schema(definition)
        if bool(definition.get("required", False)):
            required.append(name)
        default = definition.get("default")
        if default not in (None, ""):
            defaults[name] = default
        field_ui_schema = definition.get("uiSchema") or definition.get("ui_schema")
        if isinstance(field_ui_schema, Mapping):
            ui_schema[name] = dict(field_ui_schema)
    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
    }
    if required:
        schema["required"] = required
    return CapabilityInputContractParts(
        input_schema=schema,
        ui_schema=ui_schema,
        defaults=defaults,
    )


def normalize_capability_input_contract(
    *,
    owner: CapabilityInputOwner,
    parts: CapabilityInputContractParts,
) -> dict[str, Any]:
    """Return a camelCase renderer-facing capability input contract."""

    diagnostics = [dict(item) for item in parts.diagnostics]
    input_schema = _sanitize_schema_descriptions(
        _json_compatible_mapping(parts.input_schema) or {}
    )
    ui_schema = _json_compatible_mapping(parts.ui_schema) or {}
    defaults = _json_compatible_mapping(parts.defaults) or {}

    if input_schema:
        root_type = input_schema.get("type")
        if root_type != "object" or not isinstance(input_schema.get("properties", {}), Mapping):
            diagnostics.append(
                _diagnostic(
                    code="input_schema_root_not_object",
                    message=(
                        "Capability inputSchema must be a root object schema; "
                        "generated fields were omitted."
                    ),
                    severity="warning",
                    path="inputSchema",
                )
            )
            input_schema = {}

    if _contains_secret_like_value(defaults):
        diagnostics.append(
            _diagnostic(
                code="defaults_secret_like_value",
                message="Capability defaults contained a secret-like value and were omitted.",
                severity="warning",
                path="defaults",
            )
        )
        defaults = {}
    diagnostics.extend(_schema_trust_diagnostics(input_schema))
    diagnostics.extend(_ui_schema_widget_diagnostics(ui_schema))

    digest_payload = {
        "parserVersion": PARSER_VERSION,
        "owner": {"id": owner.id, "kind": owner.kind},
        "contentDigest": owner.content_digest,
        "inputSchema": input_schema,
        "uiSchema": ui_schema,
        "defaults": defaults,
    }
    contract_digest = "sha256:" + hashlib.sha256(
        json.dumps(digest_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()

    contract: dict[str, Any] = {
        "id": owner.id,
        "kind": owner.kind,
        "label": owner.label,
        "inputSchema": input_schema,
        "uiSchema": ui_schema,
        "defaults": defaults,
        "contractDigest": contract_digest,
        "diagnostics": diagnostics,
    }
    if owner.description:
        contract["description"] = _sanitize_markdown_description(owner.description)
    if owner.content_digest:
        contract["contentDigest"] = owner.content_digest
    if owner.source:
        contract["source"] = dict(owner.source)
    return contract


def validate_capability_inputs(
    *,
    contract: Mapping[str, Any],
    values: Mapping[str, Any] | None,
    workflow_context: Mapping[str, Any] | None = None,
    path_prefix: str = "inputs",
) -> dict[str, Any]:
    """Validate capability values against inputSchema and safe backend defaults."""

    input_schema = _json_compatible_mapping(
        contract.get("inputSchema") if isinstance(contract, Mapping) else None
    ) or {}
    defaults = _json_compatible_mapping(
        contract.get("defaults") if isinstance(contract, Mapping) else None
    ) or {}
    submitted = _json_compatible_mapping(values) or {}
    context = workflow_context or {}
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    if _contains_secret_like_value(defaults):
        warnings.append(
            _validation_issue(
                path=f"{path_prefix}.defaults",
                message="Capability defaults contained a secret-like value and were ignored.",
                code="secret_like_default",
            )
        )
        defaults = {}

    schema_warnings = _schema_trust_diagnostics(input_schema)
    for warning in schema_warnings:
        warnings.append(
            _validation_issue(
                path=_join_path(
                    path_prefix,
                    _schema_path_to_input_path(warning.get("path")),
                ),
                message=str(warning.get("message") or "Schema metadata was ignored."),
                code=str(warning.get("code") or "schema_metadata_ignored"),
            )
        )

    ui_schema = _json_compatible_mapping(contract.get("uiSchema")) or {}
    for warning in _ui_schema_widget_diagnostics(ui_schema):
        warnings.append(
            _validation_issue(
                path=_join_path(
                    path_prefix,
                    _schema_path_to_input_path(warning.get("path")),
                ),
                message=str(warning.get("message") or "Unsupported widget was ignored."),
                code=str(warning.get("code") or "unsupported_widget"),
            )
        )

    effective = _apply_backend_defaults(
        schema=input_schema,
        submitted=submitted,
        defaults=defaults,
        workflow_context=context,
    )
    _validate_schema_value(
        schema=input_schema or {"type": "object", "properties": {}},
        value=effective,
        path=path_prefix,
        errors=errors,
    )

    source = contract.get("source")
    source_content_ref = (
        source.get("contentRef") if isinstance(source, Mapping) else None
    )
    return {
        "values": effective,
        "errors": errors,
        "warnings": warnings,
        "contractDigest": contract.get("contractDigest"),
        "contentDigest": contract.get("contentDigest"),
        "contentRef": contract.get("contentRef") or source_content_ref,
    }


def parse_skill_capability_input_contract(
    *,
    skill_id: str,
    label: str,
    markdown: str,
    source: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Parse SKILL.md frontmatter into a normalized kind=skill contract."""

    frontmatter, diagnostics = parse_skill_markdown_frontmatter(markdown)
    description = str(frontmatter.get("description") or "").strip() or None
    parts = parse_capability_input_contract(frontmatter)
    parts = CapabilityInputContractParts(
        input_schema=parts.input_schema,
        ui_schema=parts.ui_schema,
        defaults=parts.defaults,
        diagnostics=[*diagnostics, *parts.diagnostics],
    )
    content_digest = content_digest_for_text(markdown)
    return normalize_capability_input_contract(
        owner=CapabilityInputOwner(
            id=skill_id,
            kind="skill",
            label=str(frontmatter.get("name") or label or skill_id).strip() or skill_id,
            description=description,
            content_digest=content_digest,
            source={
                **dict(source or {}),
                "contentDigest": content_digest,
            },
        ),
        parts=parts,
    )


def _legacy_input_to_json_schema(definition: Mapping[str, Any]) -> dict[str, Any]:
    explicit_schema = definition.get("schema")
    if isinstance(explicit_schema, Mapping):
        return dict(explicit_schema)

    input_type = str(definition.get("type") or "text").strip().lower()
    title = str(definition.get("label") or definition.get("name") or "").strip()
    if input_type == "boolean":
        schema: dict[str, Any] = {"type": "boolean"}
    elif input_type == "enum":
        schema = {
            "type": "string",
            "enum": [str(item) for item in definition.get("options") or []],
        }
    else:
        schema = {"type": "string"}
    if title:
        schema["title"] = title
    placeholder = str(definition.get("placeholder") or "").strip()
    if placeholder:
        schema["description"] = placeholder
    return schema



def _json_compatible_mapping(value: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    return {str(key): _json_compatible_value(item) for key, item in value.items()}


def _json_compatible_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_compatible_value(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_json_compatible_value(item) for item in value]
    if isinstance(value, (dt.date, dt.datetime, dt.time)):
        return value.isoformat()
    return value


def _sanitize_schema_descriptions(value: Any) -> Any:
    if isinstance(value, Mapping):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            if key in {"description", "markdownDescription"} and isinstance(item, str):
                sanitized[str(key)] = _sanitize_markdown_description(item)
            else:
                sanitized[str(key)] = _sanitize_schema_descriptions(item)
        return sanitized
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_sanitize_schema_descriptions(item) for item in value]
    return value


def _sanitize_markdown_description(value: str) -> str:
    stripped = re.sub(r"<[^>]*>", "", value)
    stripped = re.sub(r"(?i)javascript\s*:", "", stripped)
    return stripped.strip()


def _schema_trust_diagnostics(
    schema: Any,
    path: str = "inputSchema",
) -> list[dict[str, Any]]:
    diagnostics: list[dict[str, Any]] = []
    if isinstance(schema, Mapping):
        ref = schema.get("$ref")
        if isinstance(ref, str) and urlparse(ref).scheme in {"http", "https"}:
            diagnostics.append(
                _diagnostic(
                    code="remote_ref_ignored",
                    message="Remote schema references are not fetched during validation.",
                    severity="warning",
                    path=f"{path}.$ref",
                )
            )
        for key, item in schema.items():
            key_text = str(key)
            if key_text.lower() in _CODE_LIKE_SCHEMA_KEYS:
                diagnostics.append(
                    _diagnostic(
                        code="schema_code_ignored",
                        message="Schema-provided code or expressions are ignored.",
                        severity="warning",
                        path=f"{path}.{key_text}",
                    )
                )
            diagnostics.extend(_schema_trust_diagnostics(item, f"{path}.{key_text}"))
    elif isinstance(schema, Sequence) and not isinstance(schema, (str, bytes, bytearray)):
        for index, item in enumerate(schema):
            diagnostics.extend(_schema_trust_diagnostics(item, f"{path}[{index}]"))
    return diagnostics


def _ui_schema_widget_diagnostics(
    ui_schema: Mapping[str, Any],
) -> list[dict[str, Any]]:
    diagnostics: list[dict[str, Any]] = []
    for field_name, field_ui in ui_schema.items():
        if not isinstance(field_ui, Mapping):
            continue
        widget = field_ui.get("widget")
        if widget is None:
            continue
        widget_name = str(widget).strip()
        if widget_name and widget_name not in _REGISTERED_WIDGETS:
            diagnostics.append(
                _diagnostic(
                    code="unsupported_widget",
                    message=f"Unsupported widget '{widget_name}' was ignored.",
                    severity="warning",
                    path=f"uiSchema.{field_name}.widget",
                )
            )
    return diagnostics


def _apply_backend_defaults(
    *,
    schema: Mapping[str, Any],
    submitted: Mapping[str, Any],
    defaults: Mapping[str, Any],
    workflow_context: Mapping[str, Any],
) -> dict[str, Any]:
    properties = schema.get("properties")
    if not isinstance(properties, Mapping):
        return dict(submitted)
    effective = dict(submitted)
    for name, raw_field_schema in properties.items():
        field_name = str(name)
        if field_name in effective and effective[field_name] not in (None, ""):
            continue
        field_schema = raw_field_schema if isinstance(raw_field_schema, Mapping) else {}
        context_key = field_schema.get("x-moonmind-context-default")
        if isinstance(context_key, str) and context_key:
            context_value = workflow_context.get(context_key)
            if context_value not in (None, ""):
                effective[field_name] = _json_compatible_value(context_value)
                continue
        if field_name in defaults and defaults[field_name] not in (None, ""):
            effective[field_name] = defaults[field_name]
            continue
        if "default" in field_schema and field_schema.get("default") not in (None, ""):
            effective[field_name] = _json_compatible_value(field_schema.get("default"))
    return effective


def _validate_schema_value(
    *,
    schema: Mapping[str, Any],
    value: Any,
    path: str,
    errors: list[dict[str, Any]],
) -> None:
    schema_type = schema.get("type")
    if schema_type == "object" or isinstance(schema.get("properties"), Mapping):
        if not isinstance(value, Mapping):
            errors.append(
                _validation_issue(
                    path=path,
                    message="Value must be an object.",
                    code="type",
                )
            )
            return
        required = (
            schema.get("required") if isinstance(schema.get("required"), list) else []
        )
        for required_name in required:
            key = str(required_name)
            if key not in value or value.get(key) in (None, ""):
                errors.append(
                    _validation_issue(
                        path=f"{path}.{key}",
                        message=f"{key} is required.",
                        code="required",
                    )
                )
        properties = schema.get("properties")
        if isinstance(properties, Mapping):
            for key, field_schema in properties.items():
                if key not in value:
                    continue
                if isinstance(field_schema, Mapping):
                    _validate_schema_value(
                        schema=field_schema,
                        value=value.get(key),
                        path=f"{path}.{key}",
                        errors=errors,
                    )
        return
    if schema_type == "string" and not isinstance(value, str):
        errors.append(
            _validation_issue(path=path, message="Value must be a string.", code="type")
        )
    elif schema_type == "integer" and not (
        isinstance(value, int) and not isinstance(value, bool)
    ):
        errors.append(
            _validation_issue(
                path=path,
                message="Value must be an integer.",
                code="type",
            )
        )
    elif schema_type == "number" and not (
        isinstance(value, (int, float)) and not isinstance(value, bool)
    ):
        errors.append(
            _validation_issue(path=path, message="Value must be a number.", code="type")
        )
    elif schema_type == "boolean" and not isinstance(value, bool):
        errors.append(
            _validation_issue(
                path=path,
                message="Value must be a boolean.",
                code="type",
            )
        )
    elif schema_type == "array" and not isinstance(value, list):
        errors.append(
            _validation_issue(path=path, message="Value must be an array.", code="type")
        )

    enum = schema.get("enum")
    if isinstance(enum, list) and value not in enum:
        errors.append(
            _validation_issue(
                path=path,
                message="Value must be one of the allowed options.",
                code="enum",
            )
        )
    fmt = str(schema.get("format") or "").strip()
    if fmt == "uri" and isinstance(value, str) and not urlparse(value).scheme:
        errors.append(
            _validation_issue(path=path, message="Value must be a URI.", code="format")
        )
    if fmt == "email" and isinstance(value, str) and "@" not in value:
        errors.append(
            _validation_issue(
                path=path,
                message="Value must be an email address.",
                code="format",
            )
        )


def _schema_path_to_input_path(path: Any) -> str:
    text = str(path or "")
    text = text.replace("inputSchema.properties.", "")
    text = text.replace("uiSchema.", "")
    text = text.replace(".widget", "")
    return text


def _join_path(prefix: str, suffix: str) -> str:
    suffix = suffix.strip(".")
    return f"{prefix}.{suffix}" if suffix else prefix


def _validation_issue(*, path: str, message: str, code: str) -> dict[str, Any]:
    return {
        "path": path,
        "message": message,
        "code": code,
        "recoverable": True,
    }


def _first_mapping(metadata: Mapping[str, Any], *keys: str) -> dict[str, Any] | None:
    for key in keys:
        value = metadata.get(key)
        if isinstance(value, Mapping):
            return dict(value)
    return None


def _contains_secret_like_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, Mapping):
        return any(_contains_secret_like_value(item) for item in value.values())
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return any(_contains_secret_like_value(item) for item in value)
    return bool(_SECRET_LIKE_VALUE_PATTERN.search(str(value or "")))


def _diagnostic(
    *,
    code: str,
    message: str,
    severity: str,
    path: str,
    details: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    diagnostic: dict[str, Any] = {
        "code": code,
        "message": message,
        "severity": severity,
        "path": path,
    }
    if details:
        diagnostic["details"] = dict(details)
    return diagnostic
