"""Normalize renderer-facing input contracts for MoonMind capabilities."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal
from urllib.parse import urlparse

import yaml

CapabilityKind = Literal["tool", "skill", "preset"]

PARSER_VERSION = "capability-input-contracts:v1"
MAX_FRONTMATTER_BYTES = 64 * 1024
MAX_INPUT_SCHEMA_BYTES = 128 * 1024
MAX_UI_SCHEMA_BYTES = 64 * 1024
MAX_DEFAULTS_BYTES = 64 * 1024

_SUPPORTED_SCHEMA_KEYWORDS = frozenset(
    {
        "$defs",
        "$schema",
        "additionalProperties",
        "anyOf",
        "const",
        "default",
        "description",
        "enum",
        "examples",
        "exclusiveMaximum",
        "exclusiveMinimum",
        "format",
        "items",
        "maxItems",
        "maxLength",
        "maximum",
        "minItems",
        "minLength",
        "minimum",
        "oneOf",
        "pattern",
        "properties",
        "required",
        "title",
        "type",
        "uniqueItems",
    }
)
_DIAGNOSED_SAFE_SCHEMA_KEYWORDS = frozenset(
    {
        "allOf",
        "contains",
        "dependentRequired",
        "dependentSchemas",
        "if",
        "not",
        "patternProperties",
        "prefixItems",
        "then",
        "unevaluatedProperties",
    }
)
_SUPPORTED_MOONMIND_HINTS = frozenset(
    {
        "x-moonmind-context-default",
        "x-moonmind-multiline",
        "x-moonmind-provider",
        "x-moonmind-semantic-type",
        "x-moonmind-widget",
    }
)
_SUPPORTED_WIDGETS = frozenset(
    {
        "checkbox",
        "file-reference-picker",
        "github.branch-picker",
        "github.issue-picker",
        "github.repository-picker",
        "jira.issue-picker",
        "json",
        "markdown",
        "model-picker",
        "multi-select",
        "number",
        "provider.profile-picker",
        "select",
        "text",
        "textarea",
    }
)
_SECRET_LIKE_VALUE_PATTERN = re.compile(
    (
        r"(token=|password=|bearer\s+|ghp_|github_pat_|akia[0-9a-z]{16}|"
        r"aiza|atatt|-----begin [a-z ]*private key)"
    ),
    re.IGNORECASE,
)
_SECRET_LIKE_KEY_PATTERN = re.compile(
    r"(token|password|secret|private[_-]?key|api[_-]?key)",
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
logger = logging.getLogger(__name__)


class CapabilityInputContractError(ValueError):
    """Raised when strict contract policy rejects untrusted metadata."""


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


def parse_skill_markdown_frontmatter(
    content: str,
    *,
    strict: bool = False,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Parse YAML frontmatter from a SKILL.md file with a safe loader."""

    if not content.startswith("---"):
        return {}, []
    marker = "\n---"
    end = content.find(marker, 3)
    if end < 0:
        diagnostic = _diagnostic(
            code="frontmatter_unclosed",
            message="Skill frontmatter is not closed; structured metadata was ignored.",
            severity="error" if strict else "warning",
            path="frontmatter",
        )
        if strict:
            _record_contract_event(
                "skill_input_schema_strict_policy_rejection",
                code=diagnostic["code"],
            )
            raise CapabilityInputContractError(diagnostic["message"])
        return {}, [diagnostic]
    raw_frontmatter = content[3:end]
    if len(raw_frontmatter.encode("utf-8")) > MAX_FRONTMATTER_BYTES:
        diagnostic = _diagnostic(
            code="frontmatter_too_large",
            message=(
                "Skill frontmatter exceeds the supported size; structured "
                "metadata was ignored."
            ),
            severity="error" if strict else "warning",
            path="frontmatter",
        )
        if strict:
            _record_contract_event(
                "skill_input_schema_strict_policy_rejection",
                code=diagnostic["code"],
            )
            raise CapabilityInputContractError(diagnostic["message"])
        return {}, [diagnostic]
    try:
        parsed = yaml.safe_load(raw_frontmatter) or {}
    except yaml.YAMLError as exc:
        diagnostic = _diagnostic(
            code="frontmatter_yaml_invalid",
            message=(
                "Skill frontmatter YAML could not be parsed; structured "
                "metadata was ignored."
            ),
            severity="error" if strict else "warning",
            path="frontmatter",
            details={"error": str(exc)},
        )
        if strict:
            _record_contract_event(
                "skill_input_schema_strict_policy_rejection",
                code=diagnostic["code"],
            )
            raise CapabilityInputContractError(diagnostic["message"]) from exc
        return {}, [diagnostic]
    if not isinstance(parsed, Mapping):
        diagnostic = _diagnostic(
            code="frontmatter_not_object",
            message=(
                "Skill frontmatter must be a YAML mapping; structured "
                "metadata was ignored."
            ),
            severity="error" if strict else "warning",
            path="frontmatter",
        )
        if strict:
            _record_contract_event(
                "skill_input_schema_strict_policy_rejection",
                code=diagnostic["code"],
            )
            raise CapabilityInputContractError(diagnostic["message"])
        return {}, [diagnostic]
    return dict(parsed), []


def extract_frontmatter(
    markdown: str, *, source_label: str
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Parse YAML frontmatter from Skill markdown, keeping discovery non-blocking."""

    metadata, diagnostics = parse_skill_markdown_frontmatter(markdown)
    if source_label:
        diagnostics = [dict(item) for item in diagnostics]
    return metadata, diagnostics


def parse_capability_input_contract(
    raw_metadata: Mapping[str, Any] | None,
    *,
    strict: bool = False,
) -> CapabilityInputContractParts:
    """Extract schema metadata using accepted source casing."""

    metadata = raw_metadata or {}
    diagnostics: list[dict[str, Any]] = []
    input_schema = _first_mapping(metadata, "inputSchema", "input_schema")
    ui_schema = _first_mapping(metadata, "uiSchema", "ui_schema")
    defaults = _first_mapping(metadata, "defaults")
    input_schema = _bounded_mapping(
        input_schema,
        path="inputSchema",
        max_bytes=MAX_INPUT_SCHEMA_BYTES,
        diagnostics=diagnostics,
        strict=strict,
    )
    ui_schema = _bounded_mapping(
        ui_schema,
        path="uiSchema",
        max_bytes=MAX_UI_SCHEMA_BYTES,
        diagnostics=diagnostics,
        strict=strict,
    )
    defaults = _bounded_mapping(
        defaults,
        path="defaults",
        max_bytes=MAX_DEFAULTS_BYTES,
        diagnostics=diagnostics,
        strict=strict,
    )
    return CapabilityInputContractParts(
        input_schema=_json_compatible_mapping(input_schema),
        ui_schema=_json_compatible_mapping(ui_schema),
        defaults=_json_compatible_mapping(defaults),
        diagnostics=diagnostics,
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
    strict: bool = False,
) -> dict[str, Any]:
    """Return a camelCase renderer-facing capability input contract."""

    diagnostics = [dict(item) for item in parts.diagnostics]
    if strict:
        for path, value, max_bytes in (
            ("inputSchema", parts.input_schema, MAX_INPUT_SCHEMA_BYTES),
            ("uiSchema", parts.ui_schema, MAX_UI_SCHEMA_BYTES),
            ("defaults", parts.defaults, MAX_DEFAULTS_BYTES),
        ):
            if isinstance(value, Mapping) and _json_size_bytes(value) > max_bytes:
                _record_contract_event(
                    "skill_input_schema_strict_policy_rejection",
                    code="metadata_too_large",
                )
                raise CapabilityInputContractError(
                    f"Capability {path} exceeds the supported size and was omitted."
                )
    input_schema = _sanitize_schema_descriptions(
        _json_compatible_mapping(parts.input_schema) or {}
    )
    ui_schema = _json_compatible_mapping(parts.ui_schema) or {}
    defaults = _json_compatible_mapping(parts.defaults) or {}

    if input_schema:
        trust_diagnostics = _schema_trust_diagnostics(input_schema)
        if strict:
            for diagnostic in trust_diagnostics:
                if diagnostic.get("code") == "remote_ref_ignored":
                    _record_contract_event(
                        "skill_input_schema_strict_policy_rejection",
                        code="remote_ref_disabled",
                    )
                    raise CapabilityInputContractError(
                        "Remote schema references are disabled for Skill input schemas."
                    )
        diagnostics.extend(trust_diagnostics)
        root_type = input_schema.get("type")
        if root_type != "object" or not isinstance(
            input_schema.get("properties", {}), Mapping
        ):
            diagnostic = _diagnostic(
                code="input_schema_root_not_object",
                message=(
                    "Capability inputSchema must be a root object schema; "
                    "generated fields were omitted."
                ),
                severity="error" if strict else "warning",
                path="inputSchema",
            )
            diagnostics.append(diagnostic)
            if strict:
                _record_contract_event(
                    "skill_input_schema_strict_policy_rejection",
                    code=diagnostic["code"],
                )
                raise CapabilityInputContractError(diagnostic["message"])
            input_schema = {}
        else:
            input_schema, schema_diagnostics = _normalize_schema_metadata(
                input_schema,
                path="inputSchema",
            )
            diagnostics.extend(schema_diagnostics)

    if input_schema:
        input_schema, schema_default_diagnostics = _remove_secret_like_schema_defaults(
            input_schema,
            path="inputSchema",
            strict=strict,
        )
        diagnostics.extend(schema_default_diagnostics)

    defaults, default_diagnostics = _remove_secret_like_defaults(
        defaults,
        strict=strict,
    )
    diagnostics.extend(default_diagnostics)

    if ui_schema:
        ui_schema, ui_diagnostics = _normalize_ui_schema_metadata(ui_schema)
        diagnostics.extend(ui_diagnostics)

    digest_payload = {
        "parserVersion": PARSER_VERSION,
        "owner": {"id": owner.id, "kind": owner.kind},
        "contentDigest": owner.content_digest,
        "inputSchema": input_schema,
        "uiSchema": ui_schema,
        "defaults": defaults,
    }
    contract_digest = "sha256:" + hashlib.sha256(
        json.dumps(digest_payload, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
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
        "hasInputSchema": bool(input_schema.get("properties")),
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
    warnings: list[dict[str, Any]] = [
        dict(item) for item in contract.get("diagnostics") or []
    ]

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
    _validate_integration_references(
        schema=input_schema,
        ui_schema=ui_schema,
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


def contract_from_skill_markdown(
    markdown: str,
    *,
    skill_id: str,
    source_label: str,
    content_digest: str | None = None,
    strict: bool = False,
) -> dict[str, Any]:
    """Return the normalized input contract parsed from one Skill markdown body."""

    contract = parse_skill_capability_input_contract(
        skill_id=skill_id,
        label=skill_id,
        markdown=markdown,
        source={"kind": "file", "path": source_label},
        strict=strict,
    )
    if content_digest:
        contract["contentDigest"] = content_digest
        source = contract.get("source")
        if isinstance(source, dict):
            source["contentDigest"] = content_digest
        contract["contractDigest"] = _contract_digest(
            owner_id=skill_id,
            owner_kind="skill",
            content_digest=content_digest,
            input_schema=contract.get("inputSchema") or {},
            ui_schema=contract.get("uiSchema") or {},
            defaults=contract.get("defaults") or {},
        )
    return contract


def contract_metadata_for_artifact(contract: Mapping[str, Any]) -> dict[str, Any]:
    """Return compact artifact metadata fields for a normalized input contract."""

    return {
        "input_schema": dict(contract.get("inputSchema") or {}),
        "ui_schema": dict(contract.get("uiSchema") or {}),
        "defaults": dict(contract.get("defaults") or {}),
        "contract_digest": contract.get("contractDigest"),
        "diagnostics": list(contract.get("diagnostics") or []),
        "has_input_schema": bool(contract.get("hasInputSchema")),
        "parser_version": PARSER_VERSION,
    }


def contract_from_artifact_metadata(
    metadata: Mapping[str, Any] | None,
    *,
    skill_id: str,
    content_digest: str | None,
) -> dict[str, Any]:
    """Load a normalized Skill input contract from persisted artifact metadata."""

    metadata = metadata or {}
    input_schema = dict(metadata.get("input_schema") or {})
    ui_schema = dict(metadata.get("ui_schema") or {})
    defaults = dict(metadata.get("defaults") or {})
    diagnostics = list(metadata.get("diagnostics") or [])
    contract_digest = metadata.get("contract_digest")
    if not contract_digest:
        contract_digest = _contract_digest(
            owner_id=skill_id,
            owner_kind="skill",
            content_digest=content_digest,
            input_schema=input_schema,
            ui_schema=ui_schema,
            defaults=defaults,
        )
    return {
        "id": skill_id,
        "kind": "skill",
        "label": skill_id,
        "inputSchema": input_schema,
        "uiSchema": ui_schema,
        "defaults": defaults,
        "contractDigest": contract_digest,
        "diagnostics": diagnostics,
        "contentDigest": content_digest,
        "hasInputSchema": bool(
            isinstance(input_schema.get("properties"), Mapping)
            and input_schema.get("properties")
        ),
    }


def parse_skill_capability_input_contract(
    *,
    skill_id: str,
    label: str,
    markdown: str,
    source: Mapping[str, Any] | None = None,
    strict: bool = False,
) -> dict[str, Any]:
    """Parse SKILL.md frontmatter into a normalized kind=skill contract."""

    frontmatter, diagnostics = parse_skill_markdown_frontmatter(
        markdown,
        strict=strict,
    )
    description = str(frontmatter.get("description") or "").strip() or None
    parts = parse_capability_input_contract(frontmatter, strict=strict)
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
            label=str(frontmatter.get("name") or label or skill_id).strip()
            or skill_id,
            description=description,
            content_digest=content_digest,
            source={
                **dict(source or {}),
                "contentDigest": content_digest,
            },
        ),
        parts=parts,
        strict=strict,
    )


def _contract_digest(
    *,
    owner_id: str,
    owner_kind: CapabilityKind,
    content_digest: str | None,
    input_schema: Mapping[str, Any],
    ui_schema: Mapping[str, Any],
    defaults: Mapping[str, Any],
) -> str:
    payload = {
        "parserVersion": PARSER_VERSION,
        "owner": {"id": owner_id, "kind": owner_kind},
        "contentDigest": content_digest,
        "inputSchema": input_schema,
        "uiSchema": ui_schema,
        "defaults": defaults,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


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
        additional_properties = schema.get("additionalProperties")
        if additional_properties is False:
            allowed = (
                {str(key) for key in properties}
                if isinstance(properties, Mapping)
                else set()
            )
            for key in value:
                if str(key) not in allowed:
                    errors.append(
                        _validation_issue(
                            path=f"{path}.{key}",
                            message="Additional properties are not allowed.",
                            code="additionalProperties",
                        )
                    )
        return

    if schema_type == "string" and not isinstance(value, str):
        errors.append(
            _validation_issue(path=path, message="Value must be a string.", code="type")
        )
        return
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
        return
    elif schema_type == "number" and not (
        isinstance(value, (int, float)) and not isinstance(value, bool)
    ):
        errors.append(
            _validation_issue(path=path, message="Value must be a number.", code="type")
        )
        return
    elif schema_type == "boolean" and not isinstance(value, bool):
        errors.append(
            _validation_issue(
                path=path,
                message="Value must be a boolean.",
                code="type",
            )
        )
        return
    elif schema_type == "array" and not isinstance(value, list):
        errors.append(
            _validation_issue(path=path, message="Value must be an array.", code="type")
        )
        return

    enum = schema.get("enum")
    if isinstance(enum, list) and value not in enum:
        errors.append(
            _validation_issue(
                path=path,
                message="Value must be one of the allowed options.",
                code="enum",
            )
        )
    if schema_type == "string" and isinstance(value, str):
        min_length = schema.get("minLength")
        if isinstance(min_length, int) and len(value) < min_length:
            errors.append(
                _validation_issue(
                    path=path,
                    message=f"Value must be at least {min_length} characters.",
                    code="minLength",
                )
            )
        max_length = schema.get("maxLength")
        if isinstance(max_length, int) and len(value) > max_length:
            errors.append(
                _validation_issue(
                    path=path,
                    message=f"Value must be at most {max_length} characters.",
                    code="maxLength",
                )
            )
        pattern = schema.get("pattern")
        if isinstance(pattern, str):
            try:
                matches = re.search(pattern, value) is not None
            except re.error:
                matches = True
            if not matches:
                errors.append(
                    _validation_issue(
                        path=path,
                        message="Value must match the required pattern.",
                        code="pattern",
                    )
                )
    if schema_type in {"integer", "number"} and isinstance(value, (int, float)):
        minimum = schema.get("minimum")
        if isinstance(minimum, (int, float)) and value < minimum:
            errors.append(
                _validation_issue(
                    path=path,
                    message=f"Value must be greater than or equal to {minimum}.",
                    code="minimum",
                )
            )
        maximum = schema.get("maximum")
        if isinstance(maximum, (int, float)) and value > maximum:
            errors.append(
                _validation_issue(
                    path=path,
                    message=f"Value must be less than or equal to {maximum}.",
                    code="maximum",
                )
            )
    if schema_type == "array" and isinstance(value, list):
        min_items = schema.get("minItems")
        if isinstance(min_items, int) and len(value) < min_items:
            errors.append(
                _validation_issue(
                    path=path,
                    message=f"Value must contain at least {min_items} items.",
                    code="minItems",
                )
            )
        max_items = schema.get("maxItems")
        if isinstance(max_items, int) and len(value) > max_items:
            errors.append(
                _validation_issue(
                    path=path,
                    message=f"Value must contain at most {max_items} items.",
                    code="maxItems",
                )
            )
        if schema.get("uniqueItems") is True:
            encoded_items = [
                json.dumps(item, sort_keys=True, default=str) for item in value
            ]
            if len(set(encoded_items)) != len(encoded_items):
                errors.append(
                    _validation_issue(
                        path=path,
                        message="Array items must be unique.",
                        code="uniqueItems",
                    )
                )
        item_schema = schema.get("items")
        if isinstance(item_schema, Mapping):
            for index, item in enumerate(value):
                _validate_schema_value(
                    schema=item_schema,
                    value=item,
                    path=f"{path}[{index}]",
                    errors=errors,
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
    if fmt in {"date", "date-time"} and isinstance(value, str):
        if not _value_matches_format(value, fmt):
            errors.append(
                _validation_issue(
                    path=path,
                    message=f"Value must match the {fmt} format.",
                    code="format",
                )
            )


def _validate_integration_references(
    *,
    schema: Mapping[str, Any],
    ui_schema: Mapping[str, Any],
    value: Mapping[str, Any],
    path: str,
    errors: list[dict[str, Any]],
) -> None:
    properties = schema.get("properties")
    if not isinstance(properties, Mapping):
        return
    for field_name, field_schema in properties.items():
        name = str(field_name)
        if name not in value or not isinstance(field_schema, Mapping):
            continue
        field_ui_schema = ui_schema.get(name)
        widget = (
            field_ui_schema.get("widget")
            if isinstance(field_ui_schema, Mapping)
            else None
        )
        semantic_type = field_schema.get("x-moonmind-semantic-type")
        field_value = value.get(name)
        field_path = f"{path}.{name}"
        if (
            widget in {"jira.issue-picker", "github.issue-picker"}
            or semantic_type == "issue-reference"
        ):
            _validate_issue_reference(field_value, field_path, errors)
        elif widget == "github.repository-picker" or semantic_type == "repository":
            _validate_repository_reference(field_value, field_path, errors)
        elif widget == "github.branch-picker" or semantic_type == "branch":
            if not isinstance(field_value, str) or not field_value.strip():
                errors.append(
                    _validation_issue(
                        path=field_path,
                        message="Branch reference must be a non-empty string.",
                        code="reference",
                    )
                )


def _validate_issue_reference(
    value: Any,
    path: str,
    errors: list[dict[str, Any]],
) -> None:
    if isinstance(value, str):
        if value.strip():
            return
    elif isinstance(value, Mapping):
        key = str(
            value.get("key") or value.get("id") or value.get("url") or ""
        ).strip()
        number = value.get("number")
        if key or (isinstance(number, int) and not isinstance(number, bool)):
            return
    errors.append(
        _validation_issue(
            path=path,
            message="Issue reference must include a key, URL, or numeric issue number.",
            code="reference",
        )
    )


def _validate_repository_reference(
    value: Any,
    path: str,
    errors: list[dict[str, Any]],
) -> None:
    if isinstance(value, str) and re.fullmatch(r"[^/\s]+/[^/\s]+", value.strip()):
        return
    errors.append(
        _validation_issue(
            path=path,
            message="Repository reference must use owner/name format.",
            code="reference",
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
        return any(
            _SECRET_LIKE_KEY_PATTERN.search(str(key or ""))
            or _contains_secret_like_value(item)
            for key, item in value.items()
        )
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return any(_contains_secret_like_value(item) for item in value)
    return bool(_SECRET_LIKE_VALUE_PATTERN.search(str(value or "")))


def _bounded_mapping(
    value: Mapping[str, Any] | None,
    *,
    path: str,
    max_bytes: int,
    diagnostics: list[dict[str, Any]],
    strict: bool = False,
) -> Mapping[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    if _json_size_bytes(_json_compatible_mapping(value) or {}) <= max_bytes:
        return value
    diagnostic = _diagnostic(
        code="metadata_too_large",
        message=f"Capability {path} exceeds the supported size and was omitted.",
        severity="error" if strict else "warning",
        path=path,
    )
    diagnostics.append(diagnostic)
    if strict:
        _record_contract_event(
            "skill_input_schema_strict_policy_rejection",
            code=diagnostic["code"],
        )
        raise CapabilityInputContractError(diagnostic["message"])
    return None


def _json_size_bytes(value: Any) -> int:
    return len(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )


def _normalize_schema_metadata(
    schema: Mapping[str, Any],
    *,
    path: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    diagnostics: list[dict[str, Any]] = []
    normalized: dict[str, Any] = {}
    for key, value in schema.items():
        key_str = str(key)
        child_path = f"{path}.{key_str}"
        if _is_preserved_schema_key(key_str):
            if key_str == "default":
                normalized[key_str] = _json_compatible_value(value)
            elif isinstance(value, Mapping):
                if key_str in {"properties", "$defs"}:
                    child_properties: dict[str, Any] = {}
                    for child_key, child_value in value.items():
                        if isinstance(child_value, Mapping):
                            normalized_child, child_diagnostics = (
                                _normalize_schema_metadata(
                                    child_value,
                                    path=f"{child_path}.{child_key}",
                                )
                            )
                            child_properties[str(child_key)] = normalized_child
                            diagnostics.extend(child_diagnostics)
                        else:
                            child_properties[str(child_key)] = _json_compatible_value(
                                child_value
                            )
                    normalized[key_str] = child_properties
                else:
                    normalized[key_str], nested = _normalize_schema_metadata(
                        value,
                        path=child_path,
                    )
                    diagnostics.extend(nested)
            elif isinstance(value, Sequence) and not isinstance(
                value,
                (str, bytes, bytearray),
            ):
                items: list[Any] = []
                for item in value:
                    if isinstance(item, Mapping):
                        normalized_item, item_diagnostics = _normalize_schema_metadata(
                            item,
                            path=f"{child_path}[]",
                        )
                        items.append(normalized_item)
                        diagnostics.extend(item_diagnostics)
                    else:
                        items.append(_json_compatible_value(item))
                normalized[key_str] = items
            else:
                normalized[key_str] = _json_compatible_value(value)
        elif key_str.startswith("x-moonmind-"):
            normalized[key_str] = _json_compatible_value(value)
            diagnostics.append(
                _diagnostic(
                    code="ignored_hint",
                    message=(
                        f"Unsupported MoonMind input hint '{key_str}' was "
                        "preserved as metadata only."
                    ),
                    severity="warning",
                    path=child_path,
                )
            )
        elif key_str in _DIAGNOSED_SAFE_SCHEMA_KEYWORDS:
            normalized[key_str] = _json_compatible_value(value)
            diagnostics.append(
                _diagnostic(
                    code="unsupported_keyword",
                    message=(
                        f"JSON Schema keyword '{key_str}' is preserved but not "
                        "interpreted by generated fields."
                    ),
                    severity="warning",
                    path=child_path,
                )
            )
        else:
            diagnostics.append(
                _diagnostic(
                    code="unsupported_keyword",
                    message=f"Unsupported schema metadata '{key_str}' was ignored.",
                    severity="warning",
                    path=child_path,
                )
            )
    return normalized, diagnostics


def _is_preserved_schema_key(key: str) -> bool:
    return key in _SUPPORTED_SCHEMA_KEYWORDS or key in _SUPPORTED_MOONMIND_HINTS


def _remove_secret_like_schema_defaults(
    value: Any,
    *,
    path: str,
    strict: bool = False,
) -> tuple[Any, list[dict[str, Any]]]:
    diagnostics: list[dict[str, Any]] = []
    if isinstance(value, Mapping):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            child_path = f"{path}.{key}"
            if str(key) == "default" and _contains_secret_like_value(item):
                diagnostic = _diagnostic(
                    code="defaults_secret_like_value",
                    message=(
                        "Capability schema default contained a secret-like "
                        "value and was omitted."
                    ),
                    severity="error" if strict else "warning",
                    path=child_path,
                )
                diagnostics.append(diagnostic)
                if strict:
                    _record_contract_event(
                        "skill_input_schema_strict_policy_rejection",
                        code=diagnostic["code"],
                    )
                    raise CapabilityInputContractError(diagnostic["message"])
                continue
            sanitized_value, child_diagnostics = _remove_secret_like_schema_defaults(
                item,
                path=child_path,
                strict=strict,
            )
            sanitized[str(key)] = sanitized_value
            diagnostics.extend(child_diagnostics)
        return sanitized, diagnostics
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        sanitized_items: list[Any] = []
        for index, item in enumerate(value):
            sanitized_item, child_diagnostics = _remove_secret_like_schema_defaults(
                item,
                path=f"{path}[{index}]",
                strict=strict,
            )
            sanitized_items.append(sanitized_item)
            diagnostics.extend(child_diagnostics)
        return sanitized_items, diagnostics
    return value, diagnostics


def _remove_secret_like_defaults(
    defaults: Mapping[str, Any],
    *,
    strict: bool = False,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    diagnostics: list[dict[str, Any]] = []
    sanitized: dict[str, Any] = {}
    for key, value in defaults.items():
        if _SECRET_LIKE_KEY_PATTERN.search(
            str(key or "")
        ) or _contains_secret_like_value(value):
            diagnostic = _diagnostic(
                code="defaults_secret_like_value",
                message=(
                    "Capability defaults contained a secret-like value "
                    "and were omitted."
                ),
                severity="error" if strict else "warning",
                path=f"defaults.{key}",
            )
            diagnostics.append(diagnostic)
            if strict:
                _record_contract_event(
                    "skill_input_schema_strict_policy_rejection",
                    code=diagnostic["code"],
                )
                raise CapabilityInputContractError(diagnostic["message"])
            continue
        sanitized[str(key)] = value
    return sanitized, diagnostics


def _normalize_ui_schema_metadata(
    ui_schema: Mapping[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    diagnostics: list[dict[str, Any]] = []
    normalized = _json_compatible_mapping(ui_schema) or {}
    for field_name, field_ui_schema in normalized.items():
        if not isinstance(field_ui_schema, Mapping):
            diagnostics.append(
                _diagnostic(
                    code="unsupported_widget",
                    message="uiSchema field metadata must be an object.",
                    severity="warning",
                    path=f"uiSchema.{field_name}",
                )
            )
            continue
        widget = field_ui_schema.get("widget")
        if isinstance(widget, str) and widget not in _SUPPORTED_WIDGETS:
            diagnostics.append(
                _diagnostic(
                    code="unsupported_widget",
                    message=(
                        f"Unsupported widget '{widget}' will use a safe renderer "
                        "fallback."
                    ),
                    severity="warning",
                    path=f"uiSchema.{field_name}.widget",
                )
            )
    return normalized, diagnostics


def _value_matches_schema_type(value: Any, expected_type: str) -> bool:
    if expected_type == "string":
        return isinstance(value, str)
    if expected_type == "boolean":
        return isinstance(value, bool)
    if expected_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected_type == "array":
        return isinstance(value, Sequence) and not isinstance(
            value,
            (str, bytes, bytearray),
        )
    if expected_type == "object":
        return isinstance(value, Mapping)
    return True


def _value_matches_format(value: str, field_format: str) -> bool:
    if field_format == "email":
        return "@" in value and "." in value.rsplit("@", 1)[-1]
    if field_format == "uri":
        return value.startswith(("http://", "https://"))
    if field_format == "date":
        try:
            dt.date.fromisoformat(value)
        except ValueError:
            return False
        return True
    if field_format == "date-time":
        try:
            dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return False
        return True
    return True


def _validation_error(path: str, code: str, message: str) -> dict[str, Any]:
    return {
        "path": path,
        "code": code,
        "message": message,
        "recoverable": True,
    }


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


def _record_contract_event(event: str, **attributes: Any) -> None:
    safe_attributes = {
        key: value
        for key, value in attributes.items()
        if value is not None
        and key not in {"default", "defaults", "input", "inputs", "value", "values"}
    }
    logger.info(
        "capability_input_contract.%s",
        event,
        extra={"event": event, "attributes": safe_attributes},
    )
