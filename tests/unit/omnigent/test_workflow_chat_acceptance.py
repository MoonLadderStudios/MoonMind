"""Protected rollout evidence tests for MoonLadderStudios/MoonMind#3632."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from moonmind.omnigent.conformance import ConformanceContractError
from moonmind.omnigent.workflow_chat_acceptance import (
    REQUIRED_WORKFLOW_CHAT_ROWS,
    REQUIRED_WORKFLOW_CHAT_SOURCE_RECORDS,
    WORKFLOW_CHAT_ACCEPTANCE_ISSUE,
    WORKFLOW_CHAT_CASE_EVIDENCE_VERSION,
    WORKFLOW_CHAT_COMPATIBILITY_PROFILE,
    WORKFLOW_CHAT_PARENT_ISSUE,
    WORKFLOW_CHAT_SOURCE_RECORD_VERSION,
    build_workflow_chat_acceptance_manifest,
    validate_workflow_chat_acceptance_manifest,
)

NOW = datetime(2026, 8, 13, tzinfo=timezone.utc)
IMAGES = {
    "server": "ghcr.io/omnigent/server@sha256:" + "1" * 64,
    "host": "ghcr.io/omnigent/host@sha256:" + "2" * 64,
}


def _write(path: Path, value: object) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def _matrix(root: Path) -> dict[str, object]:
    rows: dict[str, object] = {}
    for index, (row_name, assertions) in enumerate(
        REQUIRED_WORKFLOW_CHAT_ROWS.items()
    ):
        source_records = []
        for record_type in REQUIRED_WORKFLOW_CHAT_SOURCE_RECORDS[row_name]:
            record_ref = f"source-{index}-{record_type}.json"
            _write(
                root / record_ref,
                {
                    "schemaVersion": WORKFLOW_CHAT_SOURCE_RECORD_VERSION,
                    "recordType": record_type,
                    "row": row_name,
                    "sourceCommit": "abc123",
                    "observed": True,
                    "data": {"bounded": True},
                },
            )
            source_records.append(
                {
                    "type": record_type,
                    "ref": record_ref,
                    "sha256": hashlib.sha256(
                        (root / record_ref).read_bytes()
                    ).hexdigest(),
                }
            )
        ref = f"case-{index}.json"
        _write(
            root / ref,
            {
                "schemaVersion": WORKFLOW_CHAT_CASE_EVIDENCE_VERSION,
                "issue": WORKFLOW_CHAT_ACCEPTANCE_ISSUE,
                "parentIssue": WORKFLOW_CHAT_PARENT_ISSUE,
                "row": row_name,
                "status": "passed",
                "sourceCommit": "abc123",
                "images": IMAGES,
                "stockHostUnmodified": True,
                "browserOriginated": True,
                "moonmindScopedOnly": True,
                "assertions": {name: True for name in assertions},
                "sourceRecords": source_records,
                "observations": ["bounded protected observation"],
            },
        )
        rows[row_name] = {
            "status": "passed",
            "assertions": {name: True for name in assertions},
            "evidenceRefs": [ref],
        }
    _write(
        root / "report.json",
        {
            "schemaVersion": "moonmind.omnigent.conformance-report/v1",
            "generatedAt": NOW.isoformat(),
            "images": IMAGES,
            "cases": [
                {
                    "caseId": f"workflow-chat-{index}",
                    "status": "passed",
                    "evidenceRefs": [f"case-{index}.json"],
                }
                for index in range(len(REQUIRED_WORKFLOW_CHAT_ROWS))
            ],
            "summary": {"passed": 4, "failed": 0, "skipped": 0},
        },
    )
    scans: dict[str, object] = {}
    for channel in ("logs", "temporalHistory", "screenshots", "archives"):
        ref = f"scan-{channel}.json"
        raw_ref = f"raw-{channel}.txt"
        (root / raw_ref).write_text(
            f"secret-free protected evidence for {channel}", encoding="utf-8"
        )
        _write(
            root / ref,
            {
                "status": "passed",
                "channel": channel,
                "files": [
                    {
                        "ref": raw_ref,
                        "sha256": hashlib.sha256(
                            (root / raw_ref).read_bytes()
                        ).hexdigest(),
                    }
                ],
            },
        )
        scans[channel] = {"status": "passed", "evidenceRef": ref}
    return {
        "generatedAt": NOW.isoformat(),
        "expiresAt": (NOW + timedelta(days=7)).isoformat(),
        "sourceCommit": "abc123",
        "compatibilityProfile": WORKFLOW_CHAT_COMPATIBILITY_PROFILE,
        "images": IMAGES,
        "rows": rows,
        "reports": ["report.json"],
        "evidenceScans": scans,
    }


def test_complete_native_chat_matrix_builds_and_validates(tmp_path: Path) -> None:
    manifest = build_workflow_chat_acceptance_manifest(
        _matrix(tmp_path), evidence_root=tmp_path
    )

    validate_workflow_chat_acceptance_manifest(
        manifest,
        evidence_root=tmp_path,
        expected_commit="abc123",
        now=NOW,
    )

    assert set(manifest["rows"]) == set(REQUIRED_WORKFLOW_CHAT_ROWS)
    assert manifest["issue"] == WORKFLOW_CHAT_ACCEPTANCE_ISSUE
    assert all(item["sha256"] for item in manifest["evidenceManifest"])


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_row",
        "failed_assertion",
        "stale",
        "wrong_commit",
        "mutable_image",
        "unresolved_ref",
        "tampered_ref",
        "failed_report",
        "missing_scan",
        "missing_source_record",
    ],
)
def test_native_chat_rollout_gate_fails_closed(
    mutation: str, tmp_path: Path
) -> None:
    source = _matrix(tmp_path)
    manifest = build_workflow_chat_acceptance_manifest(
        source, evidence_root=tmp_path
    )
    if mutation == "missing_row":
        manifest["rows"].pop("native-live-conversation")
    elif mutation == "failed_assertion":
        row = manifest["rows"]["native-live-conversation"]
        row["assertions"]["native_ui_primary"] = False
    elif mutation == "stale":
        manifest["generatedAt"] = (NOW - timedelta(days=8)).isoformat()
    elif mutation == "wrong_commit":
        manifest["sourceCommit"] = "old"
    elif mutation == "mutable_image":
        manifest["images"]["host"] = "ghcr.io/omnigent/host:latest"
    elif mutation == "unresolved_ref":
        manifest["rows"]["native-live-conversation"]["evidenceRefs"] = [
            "missing.json"
        ]
    elif mutation == "tampered_ref":
        (tmp_path / "case-0.json").write_text("{}", encoding="utf-8")
    elif mutation == "failed_report":
        (tmp_path / "report.json").write_text(
            json.dumps(
                {
                    "schemaVersion": "moonmind.omnigent.conformance-report/v1",
                    "generatedAt": NOW.isoformat(),
                    "images": IMAGES,
                    "cases": [
                        {
                            "caseId": f"workflow-chat-{index}",
                            "status": "failed" if index == 0 else "passed",
                            "evidenceRefs": [f"case-{index}.json"],
                        }
                        for index in range(4)
                    ],
                    "summary": {"passed": 3, "failed": 1},
                }
            ),
            encoding="utf-8",
        )
        for item in manifest["evidenceManifest"]:
            if item["ref"] == "report.json":
                item["sha256"] = hashlib.sha256(
                    (tmp_path / "report.json").read_bytes()
                ).hexdigest()
    elif mutation == "missing_scan":
        manifest["evidenceScans"].pop("archives")
    else:
        case = json.loads((tmp_path / "case-0.json").read_text(encoding="utf-8"))
        case["sourceRecords"].pop()
        _write(tmp_path / "case-0.json", case)
        for item in manifest["evidenceManifest"]:
            if item["ref"] == "case-0.json":
                item["sha256"] = hashlib.sha256(
                    (tmp_path / "case-0.json").read_bytes()
                ).hexdigest()

    with pytest.raises(ConformanceContractError):
        validate_workflow_chat_acceptance_manifest(
            manifest,
            evidence_root=tmp_path,
            expected_commit="abc123",
            now=NOW,
        )


def test_native_chat_evidence_is_secret_scanned(tmp_path: Path) -> None:
    source = _matrix(tmp_path)
    case = json.loads((tmp_path / "case-0.json").read_text(encoding="utf-8"))
    case["observations"] = ["Authorization: Bearer exposed-value"]
    _write(tmp_path / "case-0.json", case)
    manifest = build_workflow_chat_acceptance_manifest(
        source, evidence_root=tmp_path
    )

    with pytest.raises(ConformanceContractError, match="secret-like"):
        validate_workflow_chat_acceptance_manifest(
            manifest, evidence_root=tmp_path, now=NOW
        )
