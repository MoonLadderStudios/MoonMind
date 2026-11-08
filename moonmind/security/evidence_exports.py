"""Bounded, explicit disclosure projections of retained assessment evidence.

Native evidence and its identities stay immutable. This module prepares copies,
not read grants, a findings store, or a general-purpose anonymizer.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import zipfile
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator
from pydantic.alias_generators import to_camel

from moonmind.utils.logging import (
    EVIDENCE_TRANSFORMATION_LIMITATIONS,
    EVIDENCE_TRANSFORMATION_VERSION,
    redact_evidence_payload,
    redact_evidence_text,
)

MAX_EXPORT_REFS = 32
MAX_EXPORT_BYTES = 16 * 1024 * 1024
MAX_MEMBER_BYTES = 4 * 1024 * 1024
MAX_MANIFEST_BYTES = 128 * 1024
ArtifactId = Annotated[
    str, StringConstraints(pattern=r"^art_[A-Za-z0-9_-]+$", max_length=128)
]


class EvidenceExportSelection(BaseModel):
    model_config = ConfigDict(
        extra="forbid", populate_by_name=True, alias_generator=to_camel
    )

    artifact_refs: Annotated[
        list[ArtifactId], Field(min_length=1, max_length=MAX_EXPORT_REFS)
    ]
    max_total_bytes: Annotated[
        int, Field(strict=True, ge=1024, le=MAX_EXPORT_BYTES)
    ] = 8 * 1024 * 1024
    max_member_bytes: Annotated[int, Field(strict=True, ge=1, le=MAX_MEMBER_BYTES)] = (
        MAX_MEMBER_BYTES
    )

    @model_validator(mode="after")
    def unique_refs(self):
        if len(set(self.artifact_refs)) != len(self.artifact_refs):
            raise ValueError("duplicate selected evidence reference")
        return self


def json_bytes(payload: Any) -> bytes:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def prepare_evidence_payload(payload: Any) -> Any:
    """A separate readable projection; never mutate native model/identity data."""
    return redact_evidence_payload(payload)


def validation_summary(error, model: type[BaseModel]) -> str:
    """Expose declared field names and error types, never values or authored keys."""
    fields = {name for name in model.model_fields} | {
        field.alias for field in model.model_fields.values() if field.alias
    }
    details = []
    for item in error.errors(
        include_input=False, include_context=False, include_url=False
    )[:8]:
        location = item.get("loc", ())
        field = location[0] if location and location[0] in fields else "input"
        kind = item.get("type", "invalid_input")
        if not re.fullmatch(r"[a-z_]{1,64}", kind):
            kind = "invalid_input"
        details.append(f"{field}: {kind}")
    return "; ".join(details)


def presentation_provenance() -> str:
    return (
        "Presentation transformation: "
        + EVIDENCE_TRANSFORMATION_VERSION
        + ". "
        + " ".join(EVIDENCE_TRANSFORMATION_LIMITATIONS)
    )


class WithheldEvidence(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _validate_json(payload, *, hint: str):
    # Import lazily: AssessmentRequest owns the optional selection contract.
    from moonmind.security.assessments import (
        AdvisoryFeed,
        Comparison,
        ControlAssessment,
        Inventory,
        Observation,
        SecurityReview,
    )
    from moonmind.security.cloud_assessments import CloudAssessmentSummary
    from moonmind.security.image_assessments import ImageAssessmentSummary
    from moonmind.security.repository_scanners import ScannerSummary, _document
    from moonmind.security.tls_assessments import TLSAssessmentSummary
    from moonmind.security.web_assessments import WebAssessmentSummary

    models = {
        "security-inventory/v1": Inventory,
        "security-feed/v1": AdvisoryFeed,
        "security-comparison/v1": Comparison,
        "security-control-assessment/v1": ControlAssessment,
        "security-review/v1": SecurityReview,
        "repository-scanner/v1": ScannerSummary,
        "image-assessment/v1": ImageAssessmentSummary,
        "tls-assessment/v1": TLSAssessmentSummary,
        "web-assessment/v1": WebAssessmentSummary,
        "cloud-posture/v1": CloudAssessmentSummary,
    }
    if hint in {"native:gitleaks", "native:opengrep", "native:trivy"}:
        scanner = hint.split(":", 1)[1]
        _document(scanner, json_bytes(payload), MAX_MEMBER_BYTES)
        if scanner == "gitleaks" and any(
            not isinstance(row, dict)
            or not {"RuleID", "File", "StartLine"} <= row.keys()
            for row in payload
        ):
            raise WithheldEvidence("malformed_evidence")
        return hint
    if not isinstance(payload, dict):
        raise WithheldEvidence("unsupported_schema")
    schema = payload.get("schemaVersion")
    if schema in models:
        models[schema].model_validate(payload)
        return schema
    if hint == "scan":
        if (
            not {"observation", "scanOutcome", "originalOutcomes", "diagnostics"}
            <= payload.keys()
        ):
            raise WithheldEvidence("malformed_evidence")
        Observation.model_validate(payload["observation"])
        return "security-scan-projection/v1"
    if {
        "scope",
        "observedAt",
        "expiresAt",
        "findings",
        "coverage",
        "provenance",
    } <= payload.keys():
        Observation.model_validate(payload)
        return "security-observation/v1"
    if payload.get("toolRef") == "moonmind.security.passive_repo_scan.v1":
        from moonmind.security.passive_repo_scan import parse_scan_report_json

        report = parse_scan_report_json(json_bytes(payload))
        if payload.keys() != report.to_payload().keys() or (
            report.incomplete_reason or ""
        ).startswith("retained output"):
            raise WithheldEvidence("malformed_evidence")
        return "passive-repository-scan/v1"
    if hint.startswith("oscal:"):
        model = hint.split(":", 1)[1]
        root = "plan-of-action-and-milestones" if model == "poam" else model
        if not isinstance(payload.get(root), dict) or "metadata" not in payload[root]:
            raise WithheldEvidence("malformed_evidence")
        return "oscal-1.2.3:" + model
    raise WithheldEvidence("unsupported_schema")


def prepare_evidence_member(
    body: bytes, *, content_type: str, hint: str
) -> tuple[bytes, str, str]:
    """Withhold opaque, malformed, and unsupported evidence instead of guessing."""
    media = content_type.partition(";")[0].strip().lower()
    try:
        if (
            media == "application/json"
            or media.endswith("+json")
            or hint.startswith("native:")
        ):
            # Duplicate keys and non-finite numbers are not faithfully exportable JSON.
            def pairs(items):
                value = {}
                for key, item in items:
                    if key in value:
                        raise WithheldEvidence("malformed_evidence")
                    value[key] = item
                return value

            def invalid_constant(_):
                raise WithheldEvidence("malformed_evidence")

            payload = json.loads(
                body.decode("utf-8"),
                object_pairs_hook=pairs,
                parse_constant=invalid_constant,
            )
            schema = _validate_json(payload, hint=hint)
            return json_bytes(prepare_evidence_payload(payload)), "json", schema
        if media in {"text/plain", "text/markdown"}:
            return (
                redact_evidence_text(body.decode("utf-8")).encode("utf-8"),
                "txt",
                "utf8-text/v1",
            )
    except WithheldEvidence:
        raise
    except (ValueError, TypeError, KeyError, RecursionError, UnicodeError):
        raise WithheldEvidence("malformed_evidence") from None
    raise WithheldEvidence("unsupported_schema")


def build_evidence_archive(
    manifest: dict, members: dict[str, bytes], *, limit: int
) -> tuple[bytes, bytes]:
    """Only generated member names enter a deterministic, bounded stored ZIP."""
    if len(members) > MAX_EXPORT_REFS or any(
        not re.fullmatch(r"evidence/[0-9]{3}-[a-f0-9]{16}\.(?:json|txt)", name)
        for name in members
    ):
        raise WithheldEvidence("invalid_member_name")
    manifest_body = json_bytes(manifest)
    if len(manifest_body) > MAX_MANIFEST_BYTES:
        raise WithheldEvidence("export_bound_exceeded")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, body in {"manifest.json": manifest_body, **members}.items():
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.external_attr = 0o100600 << 16
            archive.writestr(info, body)
            if buffer.tell() > limit:
                raise WithheldEvidence("export_bound_exceeded")
    result = buffer.getvalue()
    if len(result) > limit:
        raise WithheldEvidence("export_bound_exceeded")
    return result, manifest_body


def member_name(index: int, body: bytes, extension: str) -> str:
    return f"evidence/{index:03d}-{hashlib.sha256(body).hexdigest()[:16]}.{extension}"
