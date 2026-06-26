"""Helpers for keeping Temporal payloads compact and intentionally serialized."""

from __future__ import annotations

import hashlib
import json
from typing import Any

MAX_TEMPORAL_METADATA_STRING_CHARS = 8192
MAX_TEMPORAL_METADATA_BYTES = 16 * 1024
MAX_TEMPORAL_METADATA_REF_CHARS = 1024

def compact_temporal_ref_metadata(
    field_name: str,
    value: Any,
    *,
    max_chars: int = MAX_TEMPORAL_METADATA_REF_CHARS,
) -> dict[str, Any]:
    """Return compact metadata for a value that should be a reference.

    Runtime request fields sometimes carry inline user content even when the
    contract name says "ref". Workflow result metadata must not echo those large
    values back into history, so non-compact values are represented by stable
    diagnostics instead of the original text.
    """

    normalized = str(value or "").strip()
    if not normalized:
        return {}
    if (
        len(normalized) <= max_chars
        and "\n" not in normalized
        and "\r" not in normalized
    ):
        return {field_name: normalized}
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return {
        f"{field_name}Omitted": True,
        f"{field_name}Sha256": digest,
        f"{field_name}LengthChars": len(normalized),
    }

def validate_compact_temporal_mapping(
    value: Any,
    *,
    field_name: str,
) -> dict[str, Any]:
    """Validate a bounded JSON mapping used inside Temporal boundary models.

    Metadata/provider-summary bags are approved escape hatches only for compact
    annotations. Large bodies, transcripts, diagnostics, checkpoints, and binary
    payloads must move through typed artifact refs or explicit serializers.
    """

    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be an object")

    _validate_json_value(value, path=field_name)
    try:
        encoded = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be JSON serializable") from exc
    if len(encoded) > MAX_TEMPORAL_METADATA_BYTES:
        raise ValueError(
            f"{field_name} must serialize to <= {MAX_TEMPORAL_METADATA_BYTES} bytes; "
            "store large payloads in artifacts and carry refs"
        )
    return dict(value)

def _validate_json_value(value: Any, *, path: str) -> None:
    if isinstance(value, bytes):
        raise ValueError(
            f"{path} must not contain raw bytes; use Base64Bytes or artifact refs"
        )
    if isinstance(value, str):
        if len(value) > MAX_TEMPORAL_METADATA_STRING_CHARS:
            raise ValueError(
                f"{path} must be <= {MAX_TEMPORAL_METADATA_STRING_CHARS} characters; "
                "store large payloads in artifacts and carry refs"
            )
        return
    if value is None or isinstance(value, (bool, int, float)):
        return
    if isinstance(value, dict):
        for raw_key, nested in value.items():
            key = str(raw_key).strip()
            if not key:
                raise ValueError(f"{path} contains a blank key")
            _validate_json_value(nested, path=f"{path}.{key}")
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _validate_json_value(item, path=f"{path}[{index}]")
        return
    raise ValueError(f"{path} contains unsupported value type {type(value).__name__}")
