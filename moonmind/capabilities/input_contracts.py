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

import yaml

CapabilityKind = Literal["tool", "skill", "preset"]

PARSER_VERSION = "capability-input-contracts:v1"
MAX_FRONTMATTER_BYTES = 64 * 1024
MAX_INPUT_SCHEMA_BYTES = 128 * 1024
MAX_UI_SCHEMA_BYTES = 64 * 1024
MAX_DEFAULTS_BYTES = 32 * 1024
MAX_DESCRIPTION_BYTES = 8 * 1024
REGISTERED_WIDGETS = frozenset(
    {
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
)
_SECRET_LIKE_VALUE_PATTERN = re.compile(
    r"(token=|password=|bearer\s+|ghp_|github_pat_|akia[0-9a-z]{16}|aiza|atatt|-----begin [a-z ]*private key)",
    re.IGNORECASE,
)
_EXECUTABLE_METADATA_KEYS = frozenset(
    {
        "$dynamicref",
        "component",
        "components",
        "dangerouslysetinnerhtml",
        "eval",
        "expression",
        "function",
        "import",
        "onchange",
        "onclick",
        "onload",
        "remotecomponent",
        "script",
        "srcdoc",
    }
)
_UNSUPPORTED_SCHEMA_KEYWORDS = frozenset(
    {
        "$defs",
        "$id",
        "$schema",
        "$vocabulary",
        "contentencoding",
        "contentmediatype",
        "contentschema",
        "dependentrequired",
        "dependentschemas",
        "if",
        "then",
        "else",
        "patternproperties",
        "propertynames",
        "unevaluateditems",
        "unevaluatedproperties",
    }
)
_DESCRIPTION_SCRIPT_BLOCK_RE = re.compile(
    r"<(script|style)\b[^>]*>.*?</\1>",
    re.IGNORECASE | re.DOTALL,
)
_DESCRIPTION_MARKDOWN_RE = re.compile(
    r"(<[^>]+>|!\[[^\]]*]\([^)]+\)|\[[^\]]+]\((?:javascript:|data:)[^)]+\))",
    re.IGNORECASE,
)
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
        _record_contract_event("skill_input_schema_omitted", source_kind="unknown")
        return {}, []
    marker = "\n---"
    end = content.find(marker, 3)
    if end < 0:
        diagnostic = _diagnostic(
            code="frontmatter_unclosed",
            message="Skill frontmatter is not closed; structured metadata was ignored.",
            severity="error" if strict else "warning",
            path="frontmatter",
            recoverable=not strict,
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
            code="frontmatter_size_limit",
            message="Skill frontmatter exceeds the supported metadata size limit.",
            severity="error" if strict else "warning",
            path="frontmatter",
            recoverable=not strict,
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
            message="Skill frontmatter YAML could not be parsed; structured metadata was ignored.",
            severity="error" if strict else "warning",
            path="frontmatter",
            details={"errorType": type(exc).__name__},
            recoverable=not strict,
        )
        if strict:
            _record_contract_event(
                "skill_input_schema_strict_policy_rejection",
                code=diagnostic["code"],
            )
            raise CapabilityInputContractError(diagnostic["message"]) from exc
        return {}, [
            diagnostic
        ]
    if not isinstance(parsed, Mapping):
        diagnostic = _diagnostic(
            code="frontmatter_not_object",
            message=(
                "Skill frontmatter must be a YAML mapping; structured metadata "
                "was ignored."
            ),
            severity="error" if strict else "warning",
            path="frontmatter",
            recoverable=not strict,
        )
        if strict:
            _record_contract_event(
                "skill_input_schema_strict_policy_rejection",
                code=diagnostic["code"],
            )
            raise CapabilityInputContractError(diagnostic["message"])
        return {}, [diagnostic]
    return dict(parsed), []


def parse_capability_input_contract(
    raw_metadata: Mapping[str, Any] | None,
    *,
    strict: bool = False,
) -> CapabilityInputContractParts:
    """Extract schema metadata using accepted source casing."""

    metadata = raw_metadata or {}
    input_schema = _first_mapping(metadata, "inputSchema", "input_schema")
    ui_schema = _first_mapping(metadata, "uiSchema", "ui_schema")
    defaults = _first_mapping(metadata, "defaults")
    parts = CapabilityInputContractParts(
        input_schema=_json_compatible_mapping(input_schema),
        ui_schema=_json_compatible_mapping(ui_schema),
        defaults=_json_compatible_mapping(defaults),
    )
    return _enforce_contract_size_limits(parts, strict=strict)


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
    bounded_parts = _enforce_contract_size_limits(parts, strict=strict)
    diagnostics = [dict(item) for item in bounded_parts.diagnostics]
    input_schema = _json_compatible_mapping(bounded_parts.input_schema) or {}
    ui_schema = _json_compatible_mapping(bounded_parts.ui_schema) or {}
    defaults = _json_compatible_mapping(bounded_parts.defaults) or {}

    if input_schema:
        input_schema = _sanitize_schema_metadata(input_schema, diagnostics, strict=strict)
        root_type = input_schema.get("type")
        if root_type != "object" or not isinstance(input_schema.get("properties", {}), Mapping):
            diagnostic = _diagnostic(
                code="input_schema_root_not_object",
                message=(
                    "Capability inputSchema must be a root object schema; "
                    "generated fields were omitted."
                ),
                severity="error" if strict else "warning",
                path="inputSchema",
                recoverable=not strict,
            )
            diagnostics.append(diagnostic)
            if strict:
                _record_contract_event("skill_input_schema_strict_policy_rejection", owner_kind=owner.kind, source_kind=_source_kind(owner), code=diagnostic["code"])
                raise CapabilityInputContractError(diagnostic["message"])
            input_schema = {}
        else:
            properties = input_schema.get("properties", {})
            if isinstance(properties, Mapping):
                _record_contract_event(
                    "skill_input_schema_generated_field_count",
                    owner_kind=owner.kind,
                    source_kind=_source_kind(owner),
                    field_count=len(properties),
                )

    if _contains_secret_like_value(defaults):
        diagnostic = _diagnostic(
            code="defaults_secret_like_value",
            message="Capability defaults contained a secret-like value and were omitted.",
            severity="error" if strict else "warning",
            path="defaults",
            recoverable=not strict,
        )
        diagnostics.append(diagnostic)
        if strict:
            _record_contract_event("skill_input_schema_strict_policy_rejection", owner_kind=owner.kind, source_kind=_source_kind(owner), code=diagnostic["code"])
            raise CapabilityInputContractError(diagnostic["message"])
        defaults = {}
    ui_schema = _sanitize_ui_schema(ui_schema, diagnostics, strict=strict, owner=owner)

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
        contract["description"] = owner.description
    if owner.content_digest:
        contract["contentDigest"] = owner.content_digest
    if owner.source:
        contract["source"] = dict(owner.source)
    _record_contract_event(
        "skill_input_schema_parse_result",
        owner_kind=owner.kind,
        source_kind=_source_kind(owner),
        status="success" if input_schema else "omitted",
        diagnostic_codes=sorted({str(item.get("code", "")) for item in diagnostics if item.get("code")}),
    )
    return contract


def parse_skill_capability_input_contract(
    *,
    skill_id: str,
    label: str,
    markdown: str,
    source: Mapping[str, Any] | None = None,
    strict: bool = False,
) -> dict[str, Any]:
    """Parse SKILL.md frontmatter into a normalized kind=skill contract."""

    frontmatter, diagnostics = parse_skill_markdown_frontmatter(markdown, strict=strict)
    raw_description = str(frontmatter.get("description") or "").strip()
    description, description_diagnostics = _sanitize_description(raw_description)
    parts = parse_capability_input_contract(frontmatter, strict=strict)
    parts = CapabilityInputContractParts(
        input_schema=parts.input_schema,
        ui_schema=parts.ui_schema,
        defaults=parts.defaults,
        diagnostics=[*diagnostics, *description_diagnostics, *parts.diagnostics],
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
        strict=strict,
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


def _diagnostic(
    *,
    code: str,
    message: str,
    severity: str,
    path: str,
    details: Mapping[str, Any] | None = None,
    recoverable: bool = True,
) -> dict[str, Any]:
    diagnostic: dict[str, Any] = {
        "code": code,
        "message": message,
        "severity": severity,
        "path": path,
        "recoverable": recoverable,
    }
    if details:
        diagnostic["details"] = dict(details)
    return diagnostic


def _enforce_contract_size_limits(
    parts: CapabilityInputContractParts,
    *,
    strict: bool,
) -> CapabilityInputContractParts:
    diagnostics = [dict(item) for item in parts.diagnostics]
    values: dict[str, Mapping[str, Any] | None] = {
        "input_schema": parts.input_schema,
        "ui_schema": parts.ui_schema,
        "defaults": parts.defaults,
    }
    limits = {
        "input_schema": MAX_INPUT_SCHEMA_BYTES,
        "ui_schema": MAX_UI_SCHEMA_BYTES,
        "defaults": MAX_DEFAULTS_BYTES,
    }
    for field_name, limit in limits.items():
        value = values[field_name]
        if value is None:
            continue
        size = len(json.dumps(_json_compatible_mapping(value) or {}, sort_keys=True).encode("utf-8"))
        if size <= limit:
            continue
        api_path = {"input_schema": "inputSchema", "ui_schema": "uiSchema", "defaults": "defaults"}[field_name]
        diagnostic = _diagnostic(
            code=f"{field_name}_size_limit",
            message=f"Capability {api_path} exceeds the supported size limit.",
            severity="error" if strict else "warning",
            path=api_path,
            recoverable=not strict,
        )
        diagnostics.append(diagnostic)
        if strict:
            _record_contract_event("skill_input_schema_strict_policy_rejection", code=diagnostic["code"])
            raise CapabilityInputContractError(diagnostic["message"])
        values[field_name] = None
    return CapabilityInputContractParts(
        input_schema=values["input_schema"],
        ui_schema=values["ui_schema"],
        defaults=values["defaults"],
        diagnostics=diagnostics,
    )


def _sanitize_schema_metadata(
    schema: dict[str, Any],
    diagnostics: list[dict[str, Any]],
    *,
    strict: bool,
    path: str = "inputSchema",
) -> dict[str, Any]:
    sanitized: dict[str, Any] = {}
    for key, value in schema.items():
        key_text = str(key)
        key_lower = key_text.lower()
        current_path = f"{path}.{key_text}"
        if key_text == "$ref" and _is_remote_ref(value):
            diagnostic = _diagnostic(
                code="remote_ref_disabled",
                message="Remote schema references are disabled for Skill input schemas.",
                severity="error" if strict else "warning",
                path=current_path,
                recoverable=not strict,
            )
            diagnostics.append(diagnostic)
            if strict:
                _record_contract_event(
                    "skill_input_schema_strict_policy_rejection",
                    code=diagnostic["code"],
                )
                raise CapabilityInputContractError(diagnostic["message"])
            continue
        if key_lower in _EXECUTABLE_METADATA_KEYS:
            diagnostics.append(
                _diagnostic(
                    code="executable_metadata_ignored",
                    message="Executable schema metadata was ignored.",
                    severity="warning",
                    path=current_path,
                )
            )
            continue
        if key_lower in _UNSUPPORTED_SCHEMA_KEYWORDS:
            diagnostics.append(
                _diagnostic(
                    code="unsupported_keyword",
                    message="Unsupported JSON Schema keyword was preserved as inert metadata.",
                    severity="info",
                    path=current_path,
                )
            )
        if key_text == "default" and _contains_secret_like_value(value):
            diagnostic = _diagnostic(
                code="secret_like_default",
                message="Secret-like schema default was ignored.",
                severity="error" if strict else "warning",
                path=current_path,
                recoverable=not strict,
            )
            diagnostics.append(diagnostic)
            if strict:
                _record_contract_event(
                    "skill_input_schema_strict_policy_rejection",
                    code=diagnostic["code"],
                )
                raise CapabilityInputContractError(diagnostic["message"])
            continue
        if key_text == "x-moonmind-widget":
            widget = str(value or "").strip()
            if widget and widget not in REGISTERED_WIDGETS:
                diagnostics.extend(
                    [
                        _diagnostic(
                            code="unsupported_widget",
                            message=(
                                "Unsupported widget metadata was ignored; the "
                                "renderer will use a safe fallback."
                            ),
                            severity="warning",
                            path=current_path,
                        ),
                        _diagnostic(
                            code="fallback_renderer",
                            message="A safe fallback renderer will be used.",
                            severity="info",
                            path=path,
                        ),
                    ]
                )
                continue
        if key_text.startswith("x-") and not key_text.startswith("x-moonmind-"):
            diagnostics.append(
                _diagnostic(
                    code="ignored_hint",
                    message="Unknown non-namespaced schema hint was ignored.",
                    severity="warning",
                    path=current_path,
                )
            )
            continue
        if key_text == "description" and isinstance(value, str):
            description, description_diagnostics = _sanitize_description(value, path=current_path)
            diagnostics.extend(description_diagnostics)
            sanitized[key_text] = description
            continue
        if isinstance(value, Mapping):
            sanitized[key_text] = _sanitize_schema_metadata(dict(value), diagnostics, strict=strict, path=current_path)
            continue
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            sanitized[key_text] = [
                _sanitize_schema_metadata(dict(item), diagnostics, strict=strict, path=f"{current_path}[{index}]")
                if isinstance(item, Mapping)
                else item
                for index, item in enumerate(value)
            ]
            continue
        sanitized[key_text] = value
    return sanitized


def _sanitize_ui_schema(
    ui_schema: dict[str, Any],
    diagnostics: list[dict[str, Any]],
    *,
    strict: bool,
    owner: CapabilityInputOwner,
) -> dict[str, Any]:
    sanitized: dict[str, Any] = {}
    for field_name, raw_field in ui_schema.items():
        if not isinstance(raw_field, Mapping):
            continue
        field_ui: dict[str, Any] = {}
        for key, value in raw_field.items():
            key_text = str(key)
            key_lower = key_text.lower()
            path = f"uiSchema.{field_name}.{key_text}"
            if key_lower in _EXECUTABLE_METADATA_KEYS or _is_remote_ref(value):
                diagnostics.append(
                    _diagnostic(
                        code="executable_metadata_ignored",
                        message="Executable or remote UI metadata was ignored.",
                        severity="warning",
                        path=path,
                    )
                )
                continue
            if key_text == "widget":
                widget = str(value or "").strip()
                if widget and widget not in REGISTERED_WIDGETS:
                    diagnostics.append(
                        _diagnostic(
                            code="unsupported_widget",
                            message="Unsupported widget metadata was ignored; the renderer will use a safe fallback.",
                            severity="warning",
                            path=path,
                        )
                    )
                    diagnostics.append(
                        _diagnostic(
                            code="fallback_renderer",
                            message="A safe fallback renderer will be used.",
                            severity="info",
                            path=f"uiSchema.{field_name}",
                        )
                    )
                    _record_contract_event("skill_input_schema_unsupported_widget", owner_kind=owner.kind, source_kind=_source_kind(owner))
                    continue
            field_ui[key_text] = _json_compatible_value(value)
        sanitized[str(field_name)] = field_ui
    return sanitized


def _sanitize_description(
    value: str,
    *,
    path: str = "description",
) -> tuple[str | None, list[dict[str, Any]]]:
    if not value:
        return None, []
    diagnostics: list[dict[str, Any]] = []
    encoded = value.encode("utf-8")
    if len(encoded) > MAX_DESCRIPTION_BYTES:
        value = encoded[:MAX_DESCRIPTION_BYTES].decode("utf-8", errors="ignore")
        diagnostics.append(
            _diagnostic(
                code="description_size_limit",
                message="Capability description exceeded the supported size limit and was truncated.",
                severity="warning",
                path=path,
            )
        )
    sanitized = _DESCRIPTION_SCRIPT_BLOCK_RE.sub("", value)
    sanitized = _DESCRIPTION_MARKDOWN_RE.sub("", sanitized).strip()
    if sanitized != value.strip():
        diagnostics.append(
            _diagnostic(
                code="unsafe_markdown_ignored",
                message="Unsafe markdown or HTML in a capability description was ignored.",
                severity="warning",
                path=path,
            )
        )
    return sanitized or None, diagnostics


def _is_remote_ref(value: Any) -> bool:
    text = str(value or "").strip().lower()
    return text.startswith(("http://", "https://", "javascript:", "data:"))


def _source_kind(owner: CapabilityInputOwner) -> str:
    source = owner.source or {}
    return str(source.get("kind") or source.get("source_kind") or "unknown")


def _record_contract_event(event: str, **attributes: Any) -> None:
    safe_attributes = {
        key: value
        for key, value in attributes.items()
        if value is not None and key not in {"default", "defaults", "input", "inputs", "value", "values"}
    }
    logger.info("capability_input_contract.%s", event, extra={"event": event, "attributes": safe_attributes})
