"""Normalize optional input contracts for selectable MoonMind capabilities."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any

import yaml

PARSER_VERSION = "skill-input-contract-v1"

_SECRET_LIKE_VALUE_PATTERN = re.compile(
    r"(?i)(?:ghp_|github_pat_|AIza|ATATT|AKIA|password\s*=|token\s*=|private key)"
)


def content_digest_for_text(content: str) -> str:
    """Return the content-addressed evidence digest for UTF-8 text."""

    return "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()


def extract_frontmatter(
    markdown: str, *, source_label: str
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Parse YAML frontmatter from Skill markdown, keeping discovery non-blocking."""

    lines = markdown.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, []

    frontmatter_lines: list[str] = []
    for line in lines[1:]:
        if line.strip() == "---":
            break
        frontmatter_lines.append(line)
    else:
        return {}, [
            {
                "code": "frontmatter_unclosed",
                "message": (
                    f"Skill frontmatter in {source_label} is missing a closing "
                    "delimiter."
                ),
                "severity": "warning",
            }
        ]

    try:
        parsed = yaml.safe_load("\n".join(frontmatter_lines)) or {}
    except yaml.YAMLError as exc:
        return {}, [
            {
                "code": "frontmatter_invalid_yaml",
                "message": (
                    f"Skill frontmatter in {source_label} could not be parsed: {exc}"
                ),
                "severity": "warning",
            }
        ]
    if not isinstance(parsed, dict):
        return {}, [
            {
                "code": "frontmatter_not_mapping",
                "message": f"Skill frontmatter in {source_label} must be a mapping.",
                "severity": "warning",
            }
        ]
    return parsed, []


def contract_from_skill_markdown(
    markdown: str,
    *,
    skill_id: str,
    source_label: str,
    content_digest: str | None = None,
) -> dict[str, Any]:
    """Return the normalized input contract parsed from one Skill markdown body."""

    digest = content_digest or content_digest_for_text(markdown)
    metadata, diagnostics = extract_frontmatter(markdown, source_label=source_label)
    return contract_from_metadata(
        metadata,
        skill_id=skill_id,
        content_digest=digest,
        diagnostics=diagnostics,
    )


def contract_from_metadata(
    metadata: Mapping[str, Any] | None,
    *,
    skill_id: str,
    content_digest: str | None,
    diagnostics: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Normalize contract fields from parsed Skill metadata."""

    metadata = metadata or {}
    diagnostics = list(diagnostics or [])
    input_schema = metadata.get("inputSchema") or metadata.get("input_schema")
    ui_schema = metadata.get("uiSchema") or metadata.get("ui_schema")
    defaults = metadata.get("defaults")

    normalized_schema: dict[str, Any] = {"type": "object", "properties": {}}
    if input_schema is not None:
        if not isinstance(input_schema, Mapping):
            diagnostics.append(
                {
                    "code": "input_schema_not_mapping",
                    "message": (
                        "Skill inputSchema must be a JSON object schema mapping."
                    ),
                    "severity": "warning",
                }
            )
        elif input_schema.get("type") not in (None, "object"):
            diagnostics.append(
                {
                    "code": "input_schema_root_not_object",
                    "message": "Skill inputSchema root type must be object.",
                    "severity": "warning",
                }
            )
        else:
            normalized_schema = dict(input_schema)
            normalized_schema.setdefault("type", "object")
            if not isinstance(normalized_schema.get("properties"), Mapping):
                normalized_schema["properties"] = {}

    normalized_ui_schema = dict(ui_schema) if isinstance(ui_schema, Mapping) else {}
    normalized_defaults = dict(defaults) if isinstance(defaults, Mapping) else {}
    if _contains_secret_like_value(normalized_defaults):
        normalized_defaults = {}
        diagnostics.append(
            {
                "code": "defaults_secret_like",
                "message": (
                    "Skill defaults contained a secret-like value and were omitted."
                ),
                "severity": "warning",
            }
        )

    contract_digest = _contract_digest(
        skill_id=skill_id,
        content_digest=content_digest,
        input_schema=normalized_schema,
        ui_schema=normalized_ui_schema,
        defaults=normalized_defaults,
    )
    return {
        "inputSchema": normalized_schema,
        "uiSchema": normalized_ui_schema,
        "defaults": normalized_defaults,
        "contractDigest": contract_digest,
        "diagnostics": diagnostics,
        "contentDigest": content_digest,
        "hasInputSchema": bool(normalized_schema.get("properties")),
    }


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
    """Load a normalized contract from persisted artifact metadata."""

    metadata = metadata or {}
    contract = {
        "inputSchema": dict(metadata.get("input_schema") or {}),
        "uiSchema": dict(metadata.get("ui_schema") or {}),
        "defaults": dict(metadata.get("defaults") or {}),
        "contractDigest": metadata.get("contract_digest"),
        "diagnostics": list(metadata.get("diagnostics") or []),
        "contentDigest": content_digest,
    }
    if not contract["contractDigest"]:
        contract = contract_from_metadata(
            {},
            skill_id=skill_id,
            content_digest=content_digest,
            diagnostics=contract["diagnostics"],
        )
    contract["hasInputSchema"] = bool(
        isinstance(contract.get("inputSchema"), Mapping)
        and contract["inputSchema"].get("properties")
    )
    return contract


def _contract_digest(
    *,
    skill_id: str,
    content_digest: str | None,
    input_schema: Mapping[str, Any],
    ui_schema: Mapping[str, Any],
    defaults: Mapping[str, Any],
) -> str:
    payload = {
        "parserVersion": PARSER_VERSION,
        "skillId": skill_id,
        "contentDigest": content_digest,
        "inputSchema": input_schema,
        "uiSchema": ui_schema,
        "defaults": defaults,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _contains_secret_like_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, Mapping):
        return any(
            _SECRET_LIKE_VALUE_PATTERN.search(str(key or ""))
            or _contains_secret_like_value(item)
            for key, item in value.items()
        )
    if isinstance(value, list | tuple | set):
        return any(_contains_secret_like_value(item) for item in value)
    return bool(_SECRET_LIKE_VALUE_PATTERN.search(str(value or "")))
