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
_SECRET_LIKE_VALUE_PATTERN = re.compile(
    r"(token=|password=|bearer\s+|ghp_|github_pat_|akia[0-9a-z]{16}|aiza|atatt|-----begin [a-z ]*private key)",
    re.IGNORECASE,
)
_SECRET_LIKE_KEY_PATTERN = re.compile(
    r"(token|password|secret|private[_-]?key|api[_-]?key)",
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
    input_schema = _json_compatible_mapping(parts.input_schema) or {}
    ui_schema = _json_compatible_mapping(parts.ui_schema) or {}
    defaults = _json_compatible_mapping(parts.defaults) or {}

    if input_schema:
        root_type = input_schema.get("type")
        if root_type != "object" or not isinstance(
            input_schema.get("properties", {}), Mapping
        ):
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
        contract["description"] = owner.description
    if owner.content_digest:
        contract["contentDigest"] = owner.content_digest
    if owner.source:
        contract["source"] = dict(owner.source)
    return contract


def contract_from_skill_markdown(
    markdown: str,
    *,
    skill_id: str,
    source_label: str,
    content_digest: str | None = None,
) -> dict[str, Any]:
    """Return the normalized input contract parsed from one Skill markdown body."""

    contract = parse_skill_capability_input_contract(
        skill_id=skill_id,
        label=skill_id,
        markdown=markdown,
        source={"kind": "file", "path": source_label},
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
