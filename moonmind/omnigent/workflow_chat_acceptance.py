"""Protected native Workflow Chat acceptance evidence contract.

MoonLadderStudios/MoonMind#3632 is complete only when the #3642
browser-to-stock-host matrix produces fresh, immutable, independently
resolvable, secret-scanned evidence.  Repository tests and a bare ``passed``
flag are not that authority.  This module defines the compact release artifact
and validates every referenced observation before it can be used as rollout
evidence.
"""

from __future__ import annotations

import hashlib
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from moonmind.omnigent.conformance import (
    REPORT_VERSION,
    REQUIRED_EVIDENCE_CHANNELS,
    ConformanceContractError,
    assert_secret_free,
    require_pinned_images,
)

WORKFLOW_CHAT_ACCEPTANCE_VERSION = (
    "moonmind.omnigent.workflow-chat-acceptance/v1"
)
WORKFLOW_CHAT_CASE_EVIDENCE_VERSION = (
    "moonmind.omnigent.workflow-chat-case-evidence/v1"
)
WORKFLOW_CHAT_SOURCE_RECORD_VERSION = (
    "moonmind.omnigent.workflow-chat-source-record/v1"
)
WORKFLOW_CHAT_ACCEPTANCE_ISSUE = "MoonLadderStudios/MoonMind#3642"
WORKFLOW_CHAT_PARENT_ISSUE = "MoonLadderStudios/MoonMind#3632"
WORKFLOW_CHAT_COMPATIBILITY_PROFILE = "omnigent.server.v1"
MAX_ACCEPTANCE_AGE = timedelta(days=7)

REQUIRED_WORKFLOW_CHAT_ROWS: Mapping[str, frozenset[str]] = {
    "native-live-conversation": frozenset(
        {
            "workflow_chat_route_opened",
            "authoritative_binding_only",
            "native_transcript_composer_queue",
            "native_ui_primary",
            "no_custom_composer",
        }
    ),
    "scoped-transports-and-resources": frozenset(
        {
            "html_http_sse_websocket_authorized",
            "reconnect_reauthorized",
            "resources_terminals_approvals_tools_available",
            "stock_routes_exactly_covered",
            "mutation_receipts_complete",
        }
    ),
    "authority-and-security-denials": frozenset(
        {
            "alternate_binding_denied",
            "provider_session_substitution_denied",
            "hidden_control_direct_invocation_denied",
            "immutable_policy_enforced",
            "high_security_send_blocked",
            "scan_unavailable_failed_closed",
            "credentials_separated",
        }
    ),
    "terminal-evidence-and-continuation": frozenset(
        {
            "terminal_chat_read_only",
            "captured_evidence_resolved",
            "linked_continuation_created",
            "source_workflow_unchanged",
            "replay_after_stock_host_unavailable",
        }
    ),
}
REQUIRED_WORKFLOW_CHAT_SOURCE_RECORDS: Mapping[str, frozenset[str]] = {
    "native-live-conversation": frozenset(
        {
            "browserTrace",
            "bindingSnapshot",
            "nativeConversation",
            "nativeControls",
        }
    ),
    "scoped-transports-and-resources": frozenset(
        {
            "browserTrace",
            "facadeRequests",
            "resourceInventory",
            "mutationReceipts",
        }
    ),
    "authority-and-security-denials": frozenset(
        {
            "browserTrace",
            "denialAudit",
            "capabilitySnapshot",
            "scanAudit",
            "credentialBoundary",
        }
    ),
    "terminal-evidence-and-continuation": frozenset(
        {
            "browserTrace",
            "terminalSnapshot",
            "capturedEvidence",
            "continuationReceipt",
            "replaySnapshot",
        }
    ),
}

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _timestamp(value: Any, *, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ConformanceContractError(
            f"workflow Chat acceptance {field} is missing or malformed"
        ) from exc
    if parsed.tzinfo is None:
        raise ConformanceContractError(
            f"workflow Chat acceptance {field} requires a timezone"
        )
    return parsed


def _resolve_bytes(ref: str, evidence_root: Path) -> bytes:
    parsed = urllib.parse.urlparse(str(ref))
    try:
        if parsed.scheme == "https":
            with urllib.request.urlopen(ref, timeout=15) as response:
                return response.read()
        if parsed.scheme not in {"", "file"}:
            raise ConformanceContractError(
                f"unsupported workflow Chat evidence scheme: {parsed.scheme}"
            )
        candidate = Path(urllib.request.url2pathname(parsed.path))
        if not candidate.is_absolute():
            candidate = evidence_root / candidate
        candidate = candidate.resolve()
        root = evidence_root.resolve()
        if candidate != root and root not in candidate.parents:
            raise ConformanceContractError(
                "workflow Chat evidence path escapes its run artifact"
            )
        return candidate.read_bytes()
    except (OSError, urllib.error.URLError) as exc:
        raise ConformanceContractError(
            f"workflow Chat evidence is unresolved: {ref}"
        ) from exc


def _resolve_json(ref: str, evidence_root: Path) -> dict[str, Any]:
    try:
        value = json.loads(_resolve_bytes(ref, evidence_root))
    except json.JSONDecodeError as exc:
        raise ConformanceContractError(
            f"workflow Chat evidence is malformed: {ref}"
        ) from exc
    if not isinstance(value, dict):
        raise ConformanceContractError(
            f"workflow Chat evidence must be an object: {ref}"
        )
    return value


def build_workflow_chat_acceptance_manifest(
    source: Mapping[str, Any],
    *,
    evidence_root: Path,
) -> dict[str, Any]:
    """Bind a protected-run matrix to immutable evidence digests.

    ``source`` is the run-local matrix produced by the protected controller. It
    carries rows and refs, never raw credentials.  This function resolves every
    referenced file before emitting a candidate manifest; validation remains a
    separate mandatory step.
    """

    rows = source.get("rows")
    reports = source.get("reports")
    scans = source.get("evidenceScans")
    if not isinstance(rows, Mapping) or not isinstance(reports, list) or not isinstance(
        scans, Mapping
    ):
        raise ConformanceContractError(
            "workflow Chat acceptance source lacks rows, reports, or evidence scans"
        )
    refs: list[str] = []
    case_refs: list[str] = []
    for row in rows.values():
        if isinstance(row, Mapping) and isinstance(row.get("evidenceRefs"), list):
            row_refs = [str(ref) for ref in row["evidenceRefs"]]
            refs.extend(row_refs)
            case_refs.extend(row_refs)
    refs.extend(str(ref) for ref in reports)
    scan_refs: list[str] = []
    for channel in REQUIRED_EVIDENCE_CHANNELS:
        scan = scans.get(channel)
        if isinstance(scan, Mapping) and scan.get("evidenceRef"):
            scan_ref = str(scan["evidenceRef"])
            refs.append(scan_ref)
            scan_refs.append(scan_ref)
    for case_ref in dict.fromkeys(case_refs):
        case = _resolve_json(case_ref, evidence_root)
        source_records = case.get("sourceRecords")
        if isinstance(source_records, list):
            for record in source_records:
                if isinstance(record, Mapping) and record.get("ref"):
                    refs.append(str(record["ref"]))
    for scan_ref in dict.fromkeys(scan_refs):
        scan_evidence = _resolve_json(scan_ref, evidence_root)
        files = scan_evidence.get("files")
        if isinstance(files, list):
            for item in files:
                if isinstance(item, Mapping) and item.get("ref"):
                    refs.append(str(item["ref"]))
    unique_refs = list(dict.fromkeys(refs))
    evidence_manifest = []
    for ref in unique_refs:
        raw = _resolve_bytes(ref, evidence_root)
        evidence_manifest.append(
            {"ref": ref, "sha256": hashlib.sha256(raw).hexdigest()}
        )
    manifest = {
        "schemaVersion": WORKFLOW_CHAT_ACCEPTANCE_VERSION,
        "issue": WORKFLOW_CHAT_ACCEPTANCE_ISSUE,
        "parentIssue": WORKFLOW_CHAT_PARENT_ISSUE,
        "status": "passed",
        "generatedAt": source.get("generatedAt"),
        "expiresAt": source.get("expiresAt"),
        "sourceCommit": source.get("sourceCommit"),
        "compatibilityProfile": source.get("compatibilityProfile"),
        "images": dict(source.get("images") or {}),
        "rows": {str(key): dict(value) for key, value in rows.items()},
        "reports": list(reports),
        "evidenceScans": {
            str(key): dict(value)
            for key, value in scans.items()
            if isinstance(value, Mapping)
        },
        "evidenceManifest": evidence_manifest,
    }
    assert_secret_free(manifest)
    return manifest


def validate_workflow_chat_acceptance_manifest(
    manifest: Mapping[str, Any],
    *,
    evidence_root: Path,
    expected_commit: str | None = None,
    now: datetime | None = None,
) -> None:
    """Fail closed unless the complete protected #3642 matrix is authoritative."""

    if (
        manifest.get("schemaVersion") != WORKFLOW_CHAT_ACCEPTANCE_VERSION
        or manifest.get("issue") != WORKFLOW_CHAT_ACCEPTANCE_ISSUE
        or manifest.get("parentIssue") != WORKFLOW_CHAT_PARENT_ISSUE
        or manifest.get("status") != "passed"
        or manifest.get("compatibilityProfile")
        != WORKFLOW_CHAT_COMPATIBILITY_PROFILE
    ):
        raise ConformanceContractError(
            "workflow Chat acceptance identity or status is invalid"
        )
    observed_at = now or datetime.now(timezone.utc)
    generated_at = _timestamp(manifest.get("generatedAt"), field="generatedAt")
    expires_at = _timestamp(manifest.get("expiresAt"), field="expiresAt")
    if generated_at > observed_at:
        raise ConformanceContractError("workflow Chat acceptance is future-dated")
    if observed_at - generated_at > MAX_ACCEPTANCE_AGE or expires_at <= observed_at:
        raise ConformanceContractError("workflow Chat acceptance is stale or expired")
    if expires_at <= generated_at:
        raise ConformanceContractError(
            "workflow Chat acceptance validity interval is invalid"
        )
    source_commit = str(manifest.get("sourceCommit") or "")
    if not source_commit or (
        expected_commit is not None and source_commit != expected_commit
    ):
        raise ConformanceContractError(
            "workflow Chat acceptance source commit does not match"
        )
    images = manifest.get("images")
    if not isinstance(images, Mapping):
        raise ConformanceContractError("workflow Chat acceptance images are missing")
    require_pinned_images({str(key): str(value) for key, value in images.items()})

    evidence_manifest = manifest.get("evidenceManifest")
    if not isinstance(evidence_manifest, list) or not evidence_manifest:
        raise ConformanceContractError(
            "workflow Chat acceptance evidence manifest is missing"
        )
    resolved_raw: dict[str, bytes] = {}
    resolved: dict[str, dict[str, Any]] = {}
    manifest_digests: dict[str, str] = {}
    for item in evidence_manifest:
        ref = item.get("ref") if isinstance(item, Mapping) else None
        digest = item.get("sha256") if isinstance(item, Mapping) else None
        if (
            not isinstance(ref, str)
            or not ref.strip()
            or not isinstance(digest, str)
            or _SHA256.fullmatch(digest) is None
            or ref in resolved
        ):
            raise ConformanceContractError(
                "workflow Chat acceptance evidence manifest is malformed"
            )
        raw = _resolve_bytes(ref, evidence_root)
        if hashlib.sha256(raw).hexdigest() != digest:
            raise ConformanceContractError(
                f"workflow Chat acceptance evidence digest mismatch: {ref}"
            )
        resolved_raw[ref] = raw
        manifest_digests[ref] = digest
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            value = None
        if isinstance(value, dict):
            resolved[ref] = value

    rows = manifest.get("rows")
    if not isinstance(rows, Mapping) or set(rows) != set(REQUIRED_WORKFLOW_CHAT_ROWS):
        raise ConformanceContractError(
            "workflow Chat acceptance matrix coverage is incomplete"
        )
    used_refs: set[str] = set()
    for row_name, required_assertions in REQUIRED_WORKFLOW_CHAT_ROWS.items():
        row = rows[row_name]
        if not isinstance(row, Mapping) or row.get("status") != "passed":
            raise ConformanceContractError(
                f"workflow Chat acceptance row did not pass: {row_name}"
            )
        assertions = row.get("assertions")
        refs = row.get("evidenceRefs")
        if (
            not isinstance(assertions, Mapping)
            or any(assertions.get(name) is not True for name in required_assertions)
            or not isinstance(refs, list)
            or not refs
        ):
            raise ConformanceContractError(
                f"workflow Chat acceptance row lacks controlling evidence: {row_name}"
            )
        for ref_value in refs:
            ref = str(ref_value)
            evidence = resolved.get(ref)
            evidence_assertions = (
                evidence.get("assertions")
                if isinstance(evidence, Mapping)
                else None
            )
            source_records = (
                evidence.get("sourceRecords")
                if isinstance(evidence, Mapping)
                else None
            )
            if (
                not isinstance(evidence, Mapping)
                or evidence.get("schemaVersion")
                != WORKFLOW_CHAT_CASE_EVIDENCE_VERSION
                or evidence.get("issue") != WORKFLOW_CHAT_ACCEPTANCE_ISSUE
                or evidence.get("parentIssue") != WORKFLOW_CHAT_PARENT_ISSUE
                or evidence.get("row") != row_name
                or evidence.get("status") != "passed"
                or evidence.get("sourceCommit") != source_commit
                or evidence.get("images") != images
                or evidence.get("stockHostUnmodified") is not True
                or evidence.get("browserOriginated") is not True
                or evidence.get("moonmindScopedOnly") is not True
                or not isinstance(evidence_assertions, Mapping)
                or any(
                    evidence_assertions.get(name) is not True
                    for name in required_assertions
                )
                or not isinstance(source_records, list)
            ):
                raise ConformanceContractError(
                    f"workflow Chat acceptance case evidence is invalid: {row_name}"
                )
            used_refs.add(ref)
            records_by_type: dict[str, Mapping[str, Any]] = {}
            for record in source_records:
                if not isinstance(record, Mapping):
                    raise ConformanceContractError(
                        f"workflow Chat source record is malformed: {row_name}"
                    )
                record_type = str(record.get("type") or "")
                record_ref = str(record.get("ref") or "")
                record_digest = str(record.get("sha256") or "")
                if (
                    not record_type
                    or not record_ref
                    or _SHA256.fullmatch(record_digest) is None
                    or record_type in records_by_type
                ):
                    raise ConformanceContractError(
                        f"workflow Chat source record is malformed: {row_name}"
                    )
                source = resolved.get(record_ref)
                manifest_digest = manifest_digests.get(record_ref, "")
                if (
                    not isinstance(source, Mapping)
                    or manifest_digest != record_digest
                    or source.get("schemaVersion")
                    != WORKFLOW_CHAT_SOURCE_RECORD_VERSION
                    or source.get("recordType") != record_type
                    or source.get("row") != row_name
                    or source.get("sourceCommit") != source_commit
                    or source.get("observed") is not True
                ):
                    raise ConformanceContractError(
                        "workflow Chat source record is invalid: "
                        f"{row_name}/{record_type}"
                    )
                records_by_type[record_type] = record
                used_refs.add(record_ref)
            if set(records_by_type) != set(
                REQUIRED_WORKFLOW_CHAT_SOURCE_RECORDS[row_name]
            ):
                raise ConformanceContractError(
                    f"workflow Chat source record coverage is incomplete: {row_name}"
                )

    reports = manifest.get("reports")
    if not isinstance(reports, list) or not reports:
        raise ConformanceContractError("workflow Chat acceptance report is missing")
    for report_ref in reports:
        ref = str(report_ref)
        report = resolved.get(ref)
        summary = report.get("summary") if isinstance(report, Mapping) else None
        cases = report.get("cases") if isinstance(report, Mapping) else None
        try:
            failed = (
                int(summary.get("failed", 1))
                if isinstance(summary, Mapping)
                else 1
            )
            passed = (
                int(summary.get("passed", 0))
                if isinstance(summary, Mapping)
                else 0
            )
            skipped = (
                int(summary.get("skipped", 0))
                if isinstance(summary, Mapping)
                else 0
            )
        except (TypeError, ValueError) as exc:
            raise ConformanceContractError(
                "workflow Chat acceptance report summary is malformed"
            ) from exc
        try:
            report_generated_at = _timestamp(
                report.get("generatedAt") if isinstance(report, Mapping) else None,
                field="report.generatedAt",
            )
        except ConformanceContractError as exc:
            raise ConformanceContractError(
                "workflow Chat acceptance references a stale or malformed report"
            ) from exc
        case_statuses: list[str] = []
        case_ids: set[str] = set()
        if isinstance(cases, list):
            for case in cases:
                case_id = case.get("caseId") if isinstance(case, Mapping) else None
                case_status = (
                    case.get("status") if isinstance(case, Mapping) else None
                )
                case_refs = (
                    case.get("evidenceRefs")
                    if isinstance(case, Mapping)
                    else None
                )
                if (
                    not isinstance(case_id, str)
                    or not case_id
                    or case_id in case_ids
                    or case_status not in {"passed", "failed", "skipped"}
                    or not isinstance(case_refs, list)
                    or not case_refs
                    or any(str(item) not in resolved_raw for item in case_refs)
                ):
                    raise ConformanceContractError(
                        "workflow Chat acceptance report cases are malformed"
                    )
                case_ids.add(case_id)
                case_statuses.append(str(case_status))
        computed_summary = {
            status_name: sum(value == status_name for value in case_statuses)
            for status_name in ("passed", "failed", "skipped")
        }
        if (
            not isinstance(report, Mapping)
            or report.get("schemaVersion") != REPORT_VERSION
            or report.get("images") != images
            or not isinstance(summary, Mapping)
            or not case_statuses
            or computed_summary
            != {"passed": passed, "failed": failed, "skipped": skipped}
            or report_generated_at > generated_at
            or generated_at - report_generated_at > timedelta(days=1)
            or failed != 0
            or passed < 1
        ):
            raise ConformanceContractError(
                "workflow Chat acceptance references a non-passing report"
            )
        used_refs.add(ref)

    scans = manifest.get("evidenceScans")
    if not isinstance(scans, Mapping) or set(scans) != set(
        REQUIRED_EVIDENCE_CHANNELS
    ):
        raise ConformanceContractError(
            "workflow Chat acceptance evidence-channel scans are incomplete"
        )
    for channel in REQUIRED_EVIDENCE_CHANNELS:
        scan = scans[channel]
        ref = scan.get("evidenceRef") if isinstance(scan, Mapping) else None
        evidence = resolved.get(str(ref)) if ref else None
        files = evidence.get("files") if isinstance(evidence, Mapping) else None
        if (
            not isinstance(scan, Mapping)
            or scan.get("status") != "passed"
            or not isinstance(ref, str)
            or not isinstance(evidence, Mapping)
            or evidence.get("status") != "passed"
            or evidence.get("channel") != channel
            or not isinstance(files, list)
            or not files
        ):
            raise ConformanceContractError(
                f"workflow Chat acceptance secret scan did not pass: {channel}"
            )
        used_refs.add(ref)
        for item in files:
            file_ref = item.get("ref") if isinstance(item, Mapping) else None
            digest = item.get("sha256") if isinstance(item, Mapping) else None
            if (
                not isinstance(file_ref, str)
                or not isinstance(digest, str)
                or _SHA256.fullmatch(digest) is None
                or manifest_digests.get(file_ref) != digest
            ):
                raise ConformanceContractError(
                    f"workflow Chat raw evidence scan is invalid: {channel}"
                )
            used_refs.add(file_ref)

    if used_refs != set(resolved_raw):
        raise ConformanceContractError(
            "workflow Chat acceptance contains unowned or missing evidence refs"
        )
    assert_secret_free(manifest)
    for raw in resolved_raw.values():
        assert_secret_free(raw.decode("utf-8", errors="replace"))


__all__ = [
    "MAX_ACCEPTANCE_AGE",
    "REQUIRED_WORKFLOW_CHAT_ROWS",
    "REQUIRED_WORKFLOW_CHAT_SOURCE_RECORDS",
    "WORKFLOW_CHAT_ACCEPTANCE_ISSUE",
    "WORKFLOW_CHAT_ACCEPTANCE_VERSION",
    "WORKFLOW_CHAT_CASE_EVIDENCE_VERSION",
    "WORKFLOW_CHAT_COMPATIBILITY_PROFILE",
    "WORKFLOW_CHAT_PARENT_ISSUE",
    "WORKFLOW_CHAT_SOURCE_RECORD_VERSION",
    "build_workflow_chat_acceptance_manifest",
    "validate_workflow_chat_acceptance_manifest",
]
