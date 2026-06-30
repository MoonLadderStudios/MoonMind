"""Normalize renderer-facing input contracts for MoonMind capabilities."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

import yaml

CapabilityKind = Literal["tool", "skill", "preset"]

PARSER_VERSION = "capability-input-contracts:v1"
_SECRET_LIKE_VALUE_PATTERN = re.compile(
    r"(token=|password=|bearer\s+|ghp_|github_pat_|akia[0-9a-z]{16}|aiza|atatt|-----begin [a-z ]*private key)",
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
        input_schema=input_schema,
        ui_schema=ui_schema,
        defaults=defaults,
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
    input_schema = dict(parts.input_schema) if isinstance(parts.input_schema, Mapping) else {}
    ui_schema = dict(parts.ui_schema) if isinstance(parts.ui_schema, Mapping) else {}
    defaults = dict(parts.defaults) if isinstance(parts.defaults, Mapping) else {}

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
    return contract


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

