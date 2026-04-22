"""Report artifact link-type and metadata contract helpers."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from moonmind.workflows.temporal.artifacts import TemporalArtifactValidationError


REPORT_ARTIFACT_LINK_TYPES = frozenset(
    {
        "report.primary",
        "report.summary",
        "report.structured",
        "report.evidence",
        "report.appendix",
        "report.findings_index",
        "report.export",
    }
)

REPORT_METADATA_KEYS = frozenset(
    {
        "artifact_type",
        "report_type",
        "report_scope",
        "title",
        "description",
        "producer",
        "subject",
        "render_hint",
        "name",
        "is_final_report",
        "finding_counts",
        "severity_counts",
        "counts",
        "step_id",
        "attempt",
    }
)
REPORT_INTERNAL_METADATA_KEYS = frozenset(
    {
        "preview_artifact_id",
    }
)

_MAX_REPORT_METADATA_STRING_CHARS = 2048
_MAX_REPORT_METADATA_DEPTH = 4
_MAX_REPORT_METADATA_ITEMS = 64
_SECRET_KEY_PATTERN = re.compile(
    r"(?i)(token|password|secret|cookie|session|credential|grant|authorization|api[_-]?key)"
)
_SECRET_VALUE_PATTERN = re.compile(
    r"(?i)(ghp_|github_pat_|AIza|ATATT|AKIA|token\s*[=:]|password\s*[=:]|"
    r"authorization\s*:|-----BEGIN [A-Z ]*PRIVATE KEY-----)"
)


def is_report_artifact_link_type(link_type: str | None) -> bool:
    """Return whether the link type is one of the supported report classes."""

    return str(link_type or "").strip() in REPORT_ARTIFACT_LINK_TYPES


def validate_report_artifact_contract(
    *,
    link_type: str | None,
    metadata: Mapping[str, Any] | None,
    allow_internal_metadata: bool = False,
) -> None:
    """Validate report-specific link-type and metadata invariants.

    Non-report link types are intentionally ignored so generic artifact flows retain
    their existing metadata behavior.
    """

    normalized_link_type = str(link_type or "").strip()
    if not normalized_link_type.startswith("report."):
        return
    if normalized_link_type not in REPORT_ARTIFACT_LINK_TYPES:
        raise TemporalArtifactValidationError(
            f"unsupported report artifact link_type '{normalized_link_type}'"
        )

    _validate_report_metadata(
        dict(metadata or {}),
        allow_internal_metadata=allow_internal_metadata,
    )


def _validate_report_metadata(
    metadata: Mapping[str, Any],
    *,
    allow_internal_metadata: bool,
) -> None:
    for key, value in metadata.items():
        normalized_key = str(key or "").strip()
        if (
            allow_internal_metadata
            and normalized_key in REPORT_INTERNAL_METADATA_KEYS
        ):
            continue
        if normalized_key not in REPORT_METADATA_KEYS:
            if _SECRET_KEY_PATTERN.search(normalized_key):
                raise TemporalArtifactValidationError(
                    f"unsafe report metadata key '{normalized_key}'"
                )
            raise TemporalArtifactValidationError(
                f"unsupported report metadata key '{normalized_key}'"
            )
        _validate_report_metadata_value(value, path=normalized_key, depth=0)


def _validate_report_metadata_value(value: Any, *, path: str, depth: int) -> None:
    if depth > _MAX_REPORT_METADATA_DEPTH:
        raise TemporalArtifactValidationError(
            f"report metadata value is too large or deeply nested at '{path}'"
        )
    if value is None or isinstance(value, (bool, int, float)):
        return
    if isinstance(value, str):
        if len(value) > _MAX_REPORT_METADATA_STRING_CHARS:
            raise TemporalArtifactValidationError(
                f"report metadata value is too large at '{path}'"
            )
        if _SECRET_VALUE_PATTERN.search(value):
            raise TemporalArtifactValidationError(
                f"unsafe report metadata value at '{path}'"
            )
        return
    if isinstance(value, Mapping):
        if len(value) > _MAX_REPORT_METADATA_ITEMS:
            raise TemporalArtifactValidationError(
                f"report metadata value is too large at '{path}'"
            )
        for nested_key, nested_value in value.items():
            nested_path = f"{path}.{nested_key}"
            if _SECRET_KEY_PATTERN.search(str(nested_key or "")):
                raise TemporalArtifactValidationError(
                    f"unsafe report metadata key '{nested_path}'"
                )
            _validate_report_metadata_value(
                nested_value,
                path=nested_path,
                depth=depth + 1,
            )
        return
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        if len(value) > _MAX_REPORT_METADATA_ITEMS:
            raise TemporalArtifactValidationError(
                f"report metadata value is too large at '{path}'"
            )
        for index, item in enumerate(value):
            _validate_report_metadata_value(
                item,
                path=f"{path}[{index}]",
                depth=depth + 1,
            )
        return
    raise TemporalArtifactValidationError(
        f"unsupported report metadata value at '{path}'"
    )
