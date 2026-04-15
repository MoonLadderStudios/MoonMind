"""Helpers for keeping Temporal payloads compact and intentionally serialized."""

from __future__ import annotations

import json
from typing import Any

MAX_TEMPORAL_METADATA_STRING_CHARS = 8192
MAX_TEMPORAL_METADATA_BYTES = 16 * 1024


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
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
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
