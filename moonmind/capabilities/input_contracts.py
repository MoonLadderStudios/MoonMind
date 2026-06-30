"""Normalize renderer-facing input contracts for MoonMind capabilities."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

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
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Parse YAML frontmatter from a SKILL.md file with a safe loader."""

    if not content.startswith("---"):
        return {}, []
    marker = "\n---"
    end = content.find(marker, 3)
    if end < 0:
        return {}, [
            _diagnostic(
                code="frontmatter_unclosed",
                message=(
                    "Skill frontmatter is not closed; structured metadata was ignored."
                ),
                severity="warning",
                path="frontmatter",
            )
        ]
    raw_frontmatter = content[3:end]
    if len(raw_frontmatter.encode("utf-8")) > MAX_FRONTMATTER_BYTES:
        return {}, [
            _diagnostic(
                code="frontmatter_too_large",
                message=(
                    "Skill frontmatter exceeds the supported size; structured "
                    "metadata was ignored."
                ),
                severity="warning",
                path="frontmatter",
            )
        ]
    try:
        parsed = yaml.safe_load(raw_frontmatter) or {}
    except yaml.YAMLError as exc:
        return {}, [
            _diagnostic(
                code="frontmatter_yaml_invalid",
                message=(
                    "Skill frontmatter YAML could not be parsed; structured "
                    "metadata was ignored."
                ),
                severity="warning",
                path="frontmatter",
                details={"error": str(exc)},
            )
        ]
    if not isinstance(parsed, Mapping):
        return {}, [
            _diagnostic(
                code="frontmatter_not_object",
                message=(
                    "Skill frontmatter must be a YAML mapping; structured "
                    "metadata was ignored."
                ),
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
    diagnostics: list[dict[str, Any]] = []
    input_schema = _first_mapping(metadata, "inputSchema", "input_schema")
    ui_schema = _first_mapping(metadata, "uiSchema", "ui_schema")
    defaults = _first_mapping(metadata, "defaults")
    input_schema = _bounded_mapping(
        input_schema,
        path="inputSchema",
        max_bytes=MAX_INPUT_SCHEMA_BYTES,
        diagnostics=diagnostics,
    )
    ui_schema = _bounded_mapping(
        ui_schema,
        path="uiSchema",
        max_bytes=MAX_UI_SCHEMA_BYTES,
        diagnostics=diagnostics,
    )
    defaults = _bounded_mapping(
        defaults,
        path="defaults",
        max_bytes=MAX_DEFAULTS_BYTES,
        diagnostics=diagnostics,
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
) -> dict[str, Any]:
    """Return a camelCase renderer-facing capability input contract."""

    diagnostics = [dict(item) for item in parts.diagnostics]
    input_schema = _json_compatible_mapping(parts.input_schema) or {}
    ui_schema = _json_compatible_mapping(parts.ui_schema) or {}
    defaults = _json_compatible_mapping(parts.defaults) or {}

    if input_schema:
        root_type = input_schema.get("type")
        properties = input_schema.get("properties", {})
        if root_type != "object" or not isinstance(properties, Mapping):
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
        )
        diagnostics.extend(schema_default_diagnostics)

    defaults, default_diagnostics = _remove_secret_like_defaults(defaults)
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
        json.dumps(
            digest_payload,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
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
        contract["description"] = owner.description
    if owner.content_digest:
        contract["contentDigest"] = owner.content_digest
    if owner.source:
        contract["source"] = dict(owner.source)
    return contract


def validate_capability_inputs(
    *,
    contract: Mapping[str, Any],
    values: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Validate values against MoonMind's supported capability schema subset."""

    input_schema = contract.get("inputSchema")
    if not isinstance(input_schema, Mapping):
        input_schema = {}
    defaults = contract.get("defaults")
    if not isinstance(defaults, Mapping):
        defaults = {}
    merged_values = {**dict(defaults), **dict(values or {})}
    errors: list[dict[str, Any]] = []
    properties = input_schema.get("properties")
    if not isinstance(properties, Mapping):
        properties = {}

    required = input_schema.get("required")
    required_names = required if isinstance(required, Sequence) else []
    for raw_name in required_names:
        name = str(raw_name)
        if name not in merged_values or merged_values.get(name) in (None, ""):
            errors.append(_validation_error(name, "required", "Field is required."))

    for name, field_schema in properties.items():
        if not isinstance(field_schema, Mapping) or name not in merged_values:
            continue
        value = merged_values[name]
        if value is None:
            continue
        expected_type = field_schema.get("type")
        if isinstance(expected_type, str) and not _value_matches_schema_type(
            value,
            expected_type,
        ):
            errors.append(
                _validation_error(
                    str(name),
                    "type",
                    f"Field must be a JSON Schema {expected_type} value.",
                )
            )
            continue
        allowed_values = field_schema.get("enum")
        if isinstance(allowed_values, Sequence) and not isinstance(
            allowed_values,
            (str, bytes, bytearray),
        ):
            if value not in list(allowed_values):
                errors.append(
                    _validation_error(str(name), "enum", "Field value is not allowed.")
                )
        field_format = field_schema.get("format")
        if isinstance(field_format, str) and isinstance(value, str):
            if not _value_matches_format(value, field_format):
                errors.append(
                    _validation_error(
                        str(name),
                        "format",
                        f"Field must match the {field_format} format.",
                    )
                )

    return {
        "values": merged_values,
        "errors": errors,
        "warnings": list(contract.get("diagnostics") or []),
        "contractDigest": contract.get("contractDigest"),
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


def _bounded_mapping(
    value: Mapping[str, Any] | None,
    *,
    path: str,
    max_bytes: int,
    diagnostics: list[dict[str, Any]],
) -> Mapping[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    if _json_size_bytes(_json_compatible_mapping(value) or {}) <= max_bytes:
        return value
    diagnostics.append(
        _diagnostic(
            code="metadata_too_large",
            message=f"Capability {path} exceeds the supported size and was omitted.",
            severity="warning",
            path=path,
        )
    )
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
) -> tuple[Any, list[dict[str, Any]]]:
    diagnostics: list[dict[str, Any]] = []
    if isinstance(value, Mapping):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            child_path = f"{path}.{key}"
            if str(key) == "default" and _contains_secret_like_value(item):
                diagnostics.append(
                    _diagnostic(
                        code="defaults_secret_like_value",
                        message=(
                            "Capability schema default contained a secret-like "
                            "value and was omitted."
                        ),
                        severity="warning",
                        path=child_path,
                    )
                )
                continue
            sanitized_value, child_diagnostics = _remove_secret_like_schema_defaults(
                item,
                path=child_path,
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
            )
            sanitized_items.append(sanitized_item)
            diagnostics.extend(child_diagnostics)
        return sanitized_items, diagnostics
    return value, diagnostics


def _remove_secret_like_defaults(
    defaults: Mapping[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    diagnostics: list[dict[str, Any]] = []
    sanitized: dict[str, Any] = {}
    for key, value in defaults.items():
        if _contains_secret_like_value(value):
            diagnostics.append(
                _diagnostic(
                    code="defaults_secret_like_value",
                    message=(
                        "Capability defaults contained a secret-like value "
                        "and were omitted."
                    ),
                    severity="warning",
                    path=f"defaults.{key}",
                )
            )
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
