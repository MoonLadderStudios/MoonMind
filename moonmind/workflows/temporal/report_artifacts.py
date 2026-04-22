"""Report artifact link-type and metadata contract helpers."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
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
        "sensitivity",
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
        "scope",
    }
)
REPORT_INTERNAL_METADATA_KEYS = frozenset(
    {
        "preview_artifact_id",
    }
)

REPORT_BUNDLE_VERSION = 1
REPORT_BUNDLE_REF_KEYS = frozenset(
    {
        "primary_report_ref",
        "summary_ref",
        "structured_ref",
    }
)
REPORT_BUNDLE_ALLOWED_KEYS = frozenset(
    {
        "report_bundle_v",
        "primary_report_ref",
        "summary_ref",
        "structured_ref",
        "evidence_refs",
        "report_type",
        "report_scope",
        "sensitivity",
        "counts",
    }
)
GENERIC_OUTPUT_LINK_TYPES = frozenset(
    {
        "output.primary",
        "output.summary",
        "output.agent_result",
    }
)
REPORT_WORKFLOW_ROLLOUT_PHASES = (
    "metadata_conventions",
    "report_links_and_ui_surfacing",
    "compact_report_bundle_contract",
    "optional_projections_filters_retention_pinning",
)
REPORT_PROJECTION_ALLOWED_KEYS = frozenset(
    {
        "has_report",
        "latest_report_ref",
        "latest_report_summary_ref",
        "report_type",
        "report_status",
        "finding_counts",
        "severity_counts",
    }
)
_UNSAFE_REPORT_BUNDLE_KEY_PATTERN = re.compile(
    r"(?i)(body|blob|payload|raw_?download_?url|presigned|url|log|screenshot|"
    r"transcript|finding_?details|evidence_?body)"
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


@dataclass(frozen=True, slots=True)
class ReportWorkflowMapping:
    """Executable rollout example for one report-producing workflow family."""

    workflow_family: str
    report_type: str
    report_link_types: tuple[str, ...]
    observability_link_types: tuple[str, ...]
    recommended_metadata_keys: tuple[str, ...]


REPORT_WORKFLOW_MAPPINGS: dict[str, ReportWorkflowMapping] = {
    "unit_test": ReportWorkflowMapping(
        workflow_family="unit_test",
        report_type="unit_test_report",
        report_link_types=(
            "report.primary",
            "report.summary",
            "report.structured",
            "report.evidence",
        ),
        observability_link_types=(
            "runtime.stdout",
            "runtime.stderr",
            "runtime.diagnostics",
        ),
        recommended_metadata_keys=(
            "artifact_type",
            "producer",
            "subject",
            "finding_counts",
        ),
    ),
    "coverage": ReportWorkflowMapping(
        workflow_family="coverage",
        report_type="coverage_report",
        report_link_types=(
            "report.primary",
            "report.structured",
            "report.evidence",
        ),
        observability_link_types=(),
        recommended_metadata_keys=(
            "artifact_type",
            "subject",
            "finding_counts",
        ),
    ),
    "security_pentest": ReportWorkflowMapping(
        workflow_family="security_pentest",
        report_type="security_pentest_report",
        report_link_types=(
            "report.primary",
            "report.summary",
            "report.structured",
            "report.evidence",
        ),
        observability_link_types=(
            "runtime.stdout",
            "runtime.stderr",
            "runtime.diagnostics",
        ),
        recommended_metadata_keys=(
            "artifact_type",
            "producer",
            "subject",
            "severity_counts",
            "sensitivity",
        ),
    ),
    "benchmark": ReportWorkflowMapping(
        workflow_family="benchmark",
        report_type="benchmark_report",
        report_link_types=(
            "report.primary",
            "report.structured",
            "report.evidence",
        ),
        observability_link_types=(),
        recommended_metadata_keys=(
            "artifact_type",
            "subject",
            "finding_counts",
        ),
    ),
}


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


def get_report_workflow_mapping(workflow_family: str) -> ReportWorkflowMapping:
    """Return the executable MM-464 rollout mapping for a workflow family."""

    normalized_family = str(workflow_family or "").strip().lower().replace("-", "_")
    try:
        return REPORT_WORKFLOW_MAPPINGS[normalized_family]
    except KeyError as exc:
        raise TemporalArtifactValidationError(
            f"unsupported report workflow family '{workflow_family}'"
        ) from exc


def classify_report_rollout_artifacts(
    link_types: Sequence[str],
) -> dict[str, Any]:
    """Classify artifact links for report rollout without local report guessing."""

    normalized = tuple(str(link_type or "").strip() for link_type in link_types)
    report_links = tuple(
        link_type for link_type in normalized if link_type.startswith("report.")
    )
    generic_links = tuple(
        link_type for link_type in normalized if link_type in GENERIC_OUTPUT_LINK_TYPES
    )
    has_primary_report = "report.primary" in report_links
    if has_primary_report:
        mode = "report"
    elif report_links:
        mode = "invalid"
    elif generic_links:
        mode = "generic_fallback"
    else:
        mode = "none"
    return {
        "mode": mode,
        "has_canonical_report": has_primary_report,
        "report_link_types": report_links,
        "generic_output_link_types": generic_links,
    }


def validate_report_workflow_artifact_classes(
    workflow_family: str,
    link_types: Sequence[str],
    *,
    allow_generic_fallback: bool = False,
) -> None:
    """Validate MM-464 rollout artifact classes for a report-producing workflow."""

    mapping = get_report_workflow_mapping(workflow_family)
    classification = classify_report_rollout_artifacts(link_types)
    if classification["mode"] == "generic_fallback":
        if allow_generic_fallback:
            return
        raise TemporalArtifactValidationError(
            "report-producing workflows must publish report.primary; "
            "output.primary is only a generic fallback"
        )
    if classification["mode"] != "report":
        raise TemporalArtifactValidationError(
            "report-producing workflows must publish report.primary"
        )

    normalized = {str(link_type or "").strip() for link_type in link_types}
    allowed = set(mapping.report_link_types) | set(mapping.observability_link_types)
    unsupported_report_links = sorted(
        link_type
        for link_type in normalized
        if link_type.startswith("report.") and link_type not in mapping.report_link_types
    )
    if unsupported_report_links:
        raise TemporalArtifactValidationError(
            "unsupported report workflow link type "
            + ", ".join(unsupported_report_links)
        )
    if "report.primary" not in normalized:
        raise TemporalArtifactValidationError(
            "report-producing workflows must publish report.primary"
        )
    unexpected_observability = sorted(
        link_type
        for link_type in normalized
        if link_type.startswith("runtime.") and link_type not in allowed
    )
    if unexpected_observability:
        raise TemporalArtifactValidationError(
            "unsupported report workflow observability link type "
            + ", ".join(unexpected_observability)
        )


def build_report_bundle_result(
    *,
    primary_report_ref: Mapping[str, Any] | None = None,
    summary_ref: Mapping[str, Any] | None = None,
    structured_ref: Mapping[str, Any] | None = None,
    evidence_refs: Sequence[Mapping[str, Any]] = (),
    report_type: str | None = None,
    report_scope: str | None = None,
    sensitivity: str | None = None,
    counts: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build and validate the compact MM-461 report bundle result shape."""

    result: dict[str, Any] = {"report_bundle_v": REPORT_BUNDLE_VERSION}
    if primary_report_ref is not None:
        result["primary_report_ref"] = _compact_artifact_ref(primary_report_ref)
    if summary_ref is not None:
        result["summary_ref"] = _compact_artifact_ref(summary_ref)
    if structured_ref is not None:
        result["structured_ref"] = _compact_artifact_ref(structured_ref)
    result["evidence_refs"] = [
        _compact_artifact_ref(ref) for ref in tuple(evidence_refs or ())
    ]
    if report_type is not None:
        result["report_type"] = str(report_type)
    if report_scope is not None:
        result["report_scope"] = str(report_scope)
    if sensitivity is not None:
        result["sensitivity"] = str(sensitivity)
    if counts is not None:
        result["counts"] = dict(counts)
    validate_report_bundle_result(result)
    return result


def build_report_projection_summary(
    bundle: Mapping[str, Any],
    *,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a report convenience projection over compact artifact refs only."""

    validate_report_bundle_result(bundle)
    for key in bundle:
        normalized_key = str(key or "").strip()
        if normalized_key not in REPORT_BUNDLE_ALLOWED_KEYS:
            raise TemporalArtifactValidationError(
                f"unsupported projection key '{normalized_key}'"
            )

    safe_metadata = dict(metadata or {})
    for key, value in safe_metadata.items():
        normalized_key = str(key or "").strip()
        if normalized_key not in {"finding_counts", "severity_counts"}:
            raise TemporalArtifactValidationError(
                f"unsafe report projection metadata key '{normalized_key}'"
            )
        _validate_report_metadata_value(value, path=normalized_key, depth=0)

    primary_ref = bundle.get("primary_report_ref")
    summary_ref = bundle.get("summary_ref")
    projection: dict[str, Any] = {"has_report": primary_ref is not None}
    if primary_ref is not None:
        projection["latest_report_ref"] = _compact_artifact_ref(primary_ref)
    if summary_ref is not None:
        projection["latest_report_summary_ref"] = _compact_artifact_ref(summary_ref)
    if bundle.get("report_type") is not None:
        projection["report_type"] = str(bundle["report_type"])
    if bundle.get("report_scope") is not None:
        projection["report_status"] = str(bundle["report_scope"])
    for key in ("finding_counts", "severity_counts"):
        if key in safe_metadata:
            if not isinstance(safe_metadata[key], Mapping):
                raise TemporalArtifactValidationError(
                    f"unsafe report projection metadata value at '{key}'"
                )
            projection[key] = dict(safe_metadata[key])

    for key, value in projection.items():
        if key not in REPORT_PROJECTION_ALLOWED_KEYS:
            raise TemporalArtifactValidationError(
                f"unsupported projection key '{key}'"
            )
        _validate_report_bundle_value(value, path=key, depth=0)
    return projection


def validate_report_bundle_result(bundle: Mapping[str, Any]) -> None:
    """Fail fast when workflow-facing report bundle data is not compact."""

    version = bundle.get("report_bundle_v")
    if version != REPORT_BUNDLE_VERSION:
        raise TemporalArtifactValidationError("report_bundle_v must be 1")
    for key, value in bundle.items():
        normalized_key = str(key or "").strip()
        if normalized_key not in REPORT_BUNDLE_ALLOWED_KEYS:
            if _UNSAFE_REPORT_BUNDLE_KEY_PATTERN.search(normalized_key):
                raise TemporalArtifactValidationError(
                    f"unsafe report bundle key '{normalized_key}'"
                )
            raise TemporalArtifactValidationError(
                f"unsupported report bundle key '{normalized_key}'"
            )
        _validate_report_bundle_value(value, path=normalized_key, depth=0)

    for ref_key in REPORT_BUNDLE_REF_KEYS:
        ref = bundle.get(ref_key)
        if ref is not None:
            _validate_compact_artifact_ref(ref, path=ref_key)
    evidence_refs = bundle.get("evidence_refs", [])
    if not isinstance(evidence_refs, Sequence) or isinstance(
        evidence_refs, (str, bytes, bytearray)
    ):
        raise TemporalArtifactValidationError("evidence_refs must be a list")
    for index, ref in enumerate(evidence_refs):
        _validate_compact_artifact_ref(ref, path=f"evidence_refs[{index}]")


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


def _compact_artifact_ref(ref: Mapping[str, Any]) -> dict[str, Any]:
    artifact_id = str(ref.get("artifact_id") or ref.get("artifactId") or "").strip()
    if not artifact_id:
        raise TemporalArtifactValidationError("artifact_ref.artifact_id is required")
    raw_version = ref.get("artifact_ref_v", ref.get("artifactRefV", 1))
    try:
        version = int(raw_version)
    except (TypeError, ValueError) as exc:
        raise TemporalArtifactValidationError("artifact_ref_v must be 1") from exc
    if version != 1:
        raise TemporalArtifactValidationError("artifact_ref_v must be 1")
    return {"artifact_ref_v": 1, "artifact_id": artifact_id}


def _validate_compact_artifact_ref(value: Any, *, path: str) -> None:
    if not isinstance(value, Mapping):
        raise TemporalArtifactValidationError(f"{path} must be an artifact ref")
    _compact_artifact_ref(value)


def _validate_report_bundle_value(value: Any, *, path: str, depth: int) -> None:
    if depth > _MAX_REPORT_METADATA_DEPTH:
        raise TemporalArtifactValidationError(
            f"unsafe report bundle value is too large or deeply nested at '{path}'"
        )
    if value is None or isinstance(value, (bool, int, float)):
        return
    if isinstance(value, str):
        if len(value) > _MAX_REPORT_METADATA_STRING_CHARS:
            raise TemporalArtifactValidationError(
                f"unsafe report bundle value is too large at '{path}'"
            )
        if value.startswith(("http://", "https://")):
            raise TemporalArtifactValidationError(
                f"unsafe report bundle value at '{path}'"
            )
        if _SECRET_VALUE_PATTERN.search(value):
            raise TemporalArtifactValidationError(
                f"unsafe report bundle value at '{path}'"
            )
        return
    if isinstance(value, Mapping):
        if len(value) > _MAX_REPORT_METADATA_ITEMS:
            raise TemporalArtifactValidationError(
                f"unsafe report bundle value is too large at '{path}'"
            )
        for nested_key, nested_value in value.items():
            nested_path = f"{path}.{nested_key}"
            if _UNSAFE_REPORT_BUNDLE_KEY_PATTERN.search(str(nested_key or "")):
                raise TemporalArtifactValidationError(
                    f"unsafe report bundle key '{nested_path}'"
                )
            _validate_report_bundle_value(
                nested_value,
                path=nested_path,
                depth=depth + 1,
            )
        return
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        if len(value) > _MAX_REPORT_METADATA_ITEMS:
            raise TemporalArtifactValidationError(
                f"unsafe report bundle value is too large at '{path}'"
            )
        for index, item in enumerate(value):
            _validate_report_bundle_value(
                item,
                path=f"{path}[{index}]",
                depth=depth + 1,
            )
        return
    raise TemporalArtifactValidationError(
        f"unsafe report bundle value at '{path}'"
    )
