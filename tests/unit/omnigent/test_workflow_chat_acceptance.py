"""Protected rollout evidence tests for MoonLadderStudios/MoonMind#3632."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from moonmind.omnigent.conformance import ConformanceContractError
from moonmind.omnigent.native_ui_compat import compatibility_map
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


def _digest(value: object) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _record_data(record_type: str, row_name: str, index: int) -> dict[str, object]:
    request_ids = [f"request-{index}-{item}" for item in range(6)]
    common: dict[str, object] = {"requestIds": [request_ids[0]]}
    if record_type == "browserTrace":
        transports = ("html", "http", "sse", "websocket", "http", "http")
        events = []
        for item, (request_id, transport) in enumerate(
            zip(request_ids, transports, strict=True)
        ):
            events.append(
                {
                    "requestId": request_id,
                    "transport": transport,
                    "path": (
                        "/omnigent-ui/workflow-chat/binding-1"
                        if item == 0
                        else "/api/workflow-chat-bindings/binding-1/omnigent/health"
                    ),
                    "responseStatus": (
                        403
                        if row_name == "authority-and-security-denials" and item
                        else 200
                    ),
                    "moonmindScoped": True,
                    "browserOriginated": True,
                }
            )
        return {
            "requestIds": request_ids,
            "route": "/workflows/workflow-1/chat",
            "traceId": f"browser-trace-{index}",
            "directUpstreamRequestCount": 0,
            "exposedProviderFields": [],
            "screenshotSha256": "sha256:" + "3" * 64,
            "networkEvents": events,
        }
    if record_type == "bindingSnapshot":
        return {
            **common,
            "authoritative": True,
            "resolvedBindingId": "binding-1",
            "runId": "run-1",
            "state": "available",
            "readOnly": False,
            "capabilitiesDigest": "sha256:" + "4" * 64,
        }
    if record_type == "nativeConversation":
        return {
            **common,
            "renderer": "omnigent-native",
            "composerRequestId": request_ids[0],
            "transcriptMessageIds": ["message-1"],
            "queuedMessageIds": ["queued-1"],
            "nativeAppVersion": "omnigent.server.v1",
        }
    if record_type == "nativeControls":
        return {
            **common,
            "renderer": "omnigent-native",
            "customComposerCount": 0,
            "controlKinds": ["approval", "tool", "file", "terminal", "agent", "task"],
        }
    if record_type == "facadeRequests":
        facade_ids = request_ids[:4]
        transports = ("html", "http", "sse", "websocket")
        return {
            "requestIds": facade_ids,
            "compatibilityProfile": WORKFLOW_CHAT_COMPATIBILITY_PROFILE,
            "coveredRouteNames": [
                item["name"] for item in compatibility_map()["routes"]
            ],
            "reconnectRequestId": facade_ids[-1],
            "requests": [
                {
                    "requestId": request_id,
                    "transport": transport,
                    "bindingId": "binding-1",
                    "providerSessionId": "provider-session-1",
                    "authorized": True,
                    "serverResolvedTarget": True,
                    "reauthorized": request_id == facade_ids[-1],
                }
                for request_id, transport in zip(facade_ids, transports, strict=True)
            ],
        }
    if record_type == "resourceInventory":
        kinds = ["approval", "tool", "file", "terminal", "agent", "task"]
        return {
            "requestIds": request_ids,
            "resources": [
                {
                    "resourceId": f"{kind}-1",
                    "resourceType": kind,
                    "requestId": request_ids[item],
                }
                for item, kind in enumerate(kinds)
            ],
        }
    if record_type == "mutationReceipts":
        return {
            **common,
            "receipts": [
                {
                    "requestId": request_ids[0],
                    "actor": "user-1",
                    "idempotencyKey": "idempotency-1",
                    "expectedState": "available",
                    "outcome": "accepted",
                    "upstreamCorrelation": "upstream-1",
                    "auditRef": "artifact://audit-1",
                }
            ],
        }
    if record_type == "denialAudit":
        kinds = [
            "alternate_binding",
            "provider_session_substitution",
            "hidden_control",
            "immutable_policy",
        ]
        return {
            "requestIds": request_ids[:4],
            "denials": [
                {
                    "requestId": request_ids[item],
                    "kind": kind,
                    "upstreamForwarded": False,
                    "auditRef": f"artifact://denial-{item}",
                }
                for item, kind in enumerate(kinds)
            ],
        }
    if record_type == "capabilitySnapshot":
        inputs = {
            "upstream": {"sendMessage": True, "changeModel": True},
            "agentProfile": {"sendMessage": True, "changeModel": False},
            "providerPolicy": {"sendMessage": True, "changeModel": True},
            "workflowState": {"sendMessage": True, "changeModel": True},
            "callerPermission": {"sendMessage": True, "changeModel": True},
        }
        effective = {"sendMessage": True, "changeModel": False}
        return {
            **common,
            "inputs": inputs,
            "effective": effective,
            "snapshotDigest": _digest({"inputs": inputs, "effective": effective}),
        }
    if record_type == "scanAudit":
        scan_ids = request_ids[4:]
        return {
            "requestIds": scan_ids,
            "attempts": [
                {
                    "requestId": scan_ids[0],
                    "outcome": "blocked",
                    "forwarded": False,
                    "auditRef": "artifact://scan-blocked",
                },
                {
                    "requestId": scan_ids[1],
                    "outcome": "enforcement_unavailable",
                    "forwarded": False,
                    "auditRef": "artifact://scan-unavailable",
                },
            ],
        }
    if record_type == "credentialBoundary":
        return {
            "requestIds": request_ids,
            "verifiedRequestIds": request_ids,
            "browserExposedCredentialNames": [],
            "forwardedMoonMindCredentialNames": [],
            "serverInjectedCredentialRef": "managed-secret://omnigent/session",
        }
    if record_type == "terminalSnapshot":
        return {
            **common,
            "state": "completed",
            "readOnly": True,
            "deniedMutationRequestIds": [request_ids[0]],
        }
    if record_type == "capturedEvidence":
        return {
            **common,
            "artifactRefs": ["artifact://capture-manifest", "artifact://final"],
            "captureManifestRef": "artifact://capture-manifest",
            "allResolved": True,
        }
    if record_type == "continuationReceipt":
        return {
            **common,
            "sourceWorkflowId": "workflow-1",
            "destinationWorkflowId": "workflow-2",
            "continuationId": "continuation-1",
            "idempotencyKey": "continue-once",
            "sourceUnchanged": True,
            "separateWorkflowAction": True,
        }
    if record_type == "replaySnapshot":
        return {
            **common,
            "hostUnavailable": True,
            "replayedFromMoonMindArtifacts": True,
            "artifactRefs": ["artifact://final"],
        }
    raise AssertionError(f"missing test source record: {record_type}")


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
                    "observedAt": NOW.isoformat(),
                    "images": IMAGES,
                    "observed": True,
                    "correlation": {
                        "workflowId": "workflow-1",
                        "chatBindingId": "binding-1",
                        "bridgeSessionId": "bridge-1",
                        "providerSessionId": "provider-session-1",
                        "browserTraceId": f"browser-trace-{index}",
                    },
                    "data": _record_data(record_type, row_name, index),
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


def _rewrite_source(
    root: Path,
    *,
    row_name: str,
    record_type: str,
    update,
) -> None:
    row_names = list(REQUIRED_WORKFLOW_CHAT_ROWS)
    index = row_names.index(row_name)
    source_path = root / f"source-{index}-{record_type}.json"
    payload = json.loads(source_path.read_text(encoding="utf-8"))
    update(payload)
    _write(source_path, payload)
    case_path = root / f"case-{index}.json"
    case = json.loads(case_path.read_text(encoding="utf-8"))
    record = next(
        item for item in case["sourceRecords"] if item["type"] == record_type
    )
    record["sha256"] = hashlib.sha256(source_path.read_bytes()).hexdigest()
    _write(case_path, case)


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
    "record_type",
    sorted(
        {
            record_type
            for record_types in REQUIRED_WORKFLOW_CHAT_SOURCE_RECORDS.values()
            for record_type in record_types
        }
    ),
)
def test_vacuous_source_record_cannot_satisfy_any_typed_schema(
    record_type: str, tmp_path: Path
) -> None:
    source = _matrix(tmp_path)
    row_name = next(
        name
        for name, record_types in REQUIRED_WORKFLOW_CHAT_SOURCE_RECORDS.items()
        if record_type in record_types
    )
    _rewrite_source(
        tmp_path,
        row_name=row_name,
        record_type=record_type,
        update=lambda payload: payload.update({"data": {"bounded": True}}),
    )
    manifest = build_workflow_chat_acceptance_manifest(
        source, evidence_root=tmp_path
    )

    with pytest.raises(ConformanceContractError, match="source record"):
        validate_workflow_chat_acceptance_manifest(
            manifest, evidence_root=tmp_path, expected_commit="abc123", now=NOW
        )


def test_source_records_must_share_server_side_workflow_session_correlation(
    tmp_path: Path,
) -> None:
    source = _matrix(tmp_path)
    _rewrite_source(
        tmp_path,
        row_name="scoped-transports-and-resources",
        record_type="mutationReceipts",
        update=lambda payload: payload["correlation"].update(
            {"providerSessionId": "substituted-session"}
        ),
    )
    manifest = build_workflow_chat_acceptance_manifest(
        source, evidence_root=tmp_path
    )

    with pytest.raises(ConformanceContractError, match="not correlated"):
        validate_workflow_chat_acceptance_manifest(
            manifest, evidence_root=tmp_path, expected_commit="abc123", now=NOW
        )


def test_matrix_rows_must_bind_the_same_authoritative_workflow(tmp_path: Path) -> None:
    source = _matrix(tmp_path)

    def substitute_workflow(payload: dict[str, object]) -> None:
        payload["correlation"]["workflowId"] = "workflow-2"
        if payload["recordType"] == "browserTrace":
            payload["data"]["route"] = "/workflows/workflow-2/chat"

    for record_type in REQUIRED_WORKFLOW_CHAT_SOURCE_RECORDS[
        "scoped-transports-and-resources"
    ]:
        _rewrite_source(
            tmp_path,
            row_name="scoped-transports-and-resources",
            record_type=record_type,
            update=substitute_workflow,
        )
    manifest = build_workflow_chat_acceptance_manifest(
        source, evidence_root=tmp_path
    )

    with pytest.raises(
        ConformanceContractError, match="same workflow session"
    ):
        validate_workflow_chat_acceptance_manifest(
            manifest, evidence_root=tmp_path, expected_commit="abc123", now=NOW
        )


def test_source_record_request_ids_must_resolve_to_the_browser_trace(
    tmp_path: Path,
) -> None:
    source = _matrix(tmp_path)

    def add_unbound_request(payload: dict[str, object]) -> None:
        payload["data"]["requestIds"] = ["not-in-browser-trace"]
        payload["data"]["receipts"][0]["requestId"] = "not-in-browser-trace"

    _rewrite_source(
        tmp_path,
        row_name="scoped-transports-and-resources",
        record_type="mutationReceipts",
        update=add_unbound_request,
    )
    manifest = build_workflow_chat_acceptance_manifest(
        source, evidence_root=tmp_path
    )

    with pytest.raises(ConformanceContractError, match="request correlation"):
        validate_workflow_chat_acceptance_manifest(
            manifest, evidence_root=tmp_path, expected_commit="abc123", now=NOW
        )


@pytest.mark.parametrize("metadata", ["sourceCommit", "images"])
def test_source_records_must_bind_commit_and_immutable_images(
    metadata: str, tmp_path: Path
) -> None:
    source = _matrix(tmp_path)

    def replace_metadata(payload: dict[str, object]) -> None:
        if metadata == "sourceCommit":
            payload[metadata] = "different-commit"
        else:
            payload[metadata] = {
                **IMAGES,
                "host": "ghcr.io/omnigent/host@sha256:" + "9" * 64,
            }

    _rewrite_source(
        tmp_path,
        row_name="native-live-conversation",
        record_type="bindingSnapshot",
        update=replace_metadata,
    )
    manifest = build_workflow_chat_acceptance_manifest(
        source, evidence_root=tmp_path
    )

    with pytest.raises(ConformanceContractError, match="source record is invalid"):
        validate_workflow_chat_acceptance_manifest(
            manifest, evidence_root=tmp_path, expected_commit="abc123", now=NOW
        )


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
