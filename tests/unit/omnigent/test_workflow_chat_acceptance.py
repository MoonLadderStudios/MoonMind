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
SCREENSHOT_EVIDENCE = b"secret-free protected screenshot evidence"


def _write(path: Path, value: object) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def _digest(value: object) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _route_path(route: dict[str, object]) -> str:
    path = str(route["pathPattern"]).removeprefix("^").removesuffix("$")
    for placeholder, value in (
        ("(?P<session_id>[^/]+)", "provider-session-1"),
        ("(?P<terminal_id>[^/]+)", "terminal-1"),
        ("(?P<res_path>.+)", "README.md"),
        ("(?P<file_id>[^/]+)", "file-1"),
        ("(?P<runner_id>[^/]+)", "runner-1"),
        ("(?P<elicitation_id>[^/]+)", "elicitation-1"),
        ("(?:/.*)?", ""),
    ):
        path = path.replace(placeholder, value)
    return path


def _request_ids(row_name: str, index: int) -> list[str]:
    count = (
        len(compatibility_map()["routes"]) + 1
        if row_name == "scoped-transports-and-resources"
        else 6
    )
    return [f"request-{index}-{item}" for item in range(count)]


def _record_data(
    record_type: str, row_name: str, index: int, root: Path
) -> dict[str, object]:
    request_ids = _request_ids(row_name, index)
    common: dict[str, object] = {"requestIds": [request_ids[0]]}
    if record_type == "browserTrace":
        if row_name == "scoped-transports-and-resources":
            transports = ["html"] + [
                str(route["transport"])
                for route in compatibility_map()["routes"]
            ]
            route_paths = [
                "/omnigent-ui/workflow-chat/binding-1"
            ] + [
                "/api/workflow-chat-bindings/binding-1/omnigent/"
                + _route_path(route)
                for route in compatibility_map()["routes"]
            ]
            methods = ["GET"] + [
                str(route["methods"][0])
                for route in compatibility_map()["routes"]
            ]
        else:
            transports = ["html", "http", "sse", "websocket", "http", "http"]
            route_paths = [
                "/omnigent-ui/workflow-chat/binding-1"
                if item == 0
                else "/api/workflow-chat-bindings/binding-1/omnigent/health"
                for item in range(len(request_ids))
            ]
            methods = ["GET"] * len(request_ids)
        events = []
        for item, (request_id, transport) in enumerate(
            zip(request_ids, transports, strict=True)
        ):
            events.append(
                {
                    "requestId": request_id,
                    "transport": transport,
                    "method": methods[item],
                    "path": route_paths[item],
                    "responseStatus": (
                        503
                        if row_name == "authority-and-security-denials" and item == 5
                        else 403
                        if row_name == "authority-and-security-denials"
                        or row_name == "terminal-evidence-and-continuation"
                        and item == 0
                        else 101
                        if transport == "websocket"
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
            "screenshotSha256": "sha256:"
            + hashlib.sha256(SCREENSHOT_EVIDENCE).hexdigest(),
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
        routes = compatibility_map()["routes"]
        route_observations = [
            {
                "routeName": "native_ui_document",
                "transport": "html",
                "method": "GET",
                "routePath": "omnigent-ui/workflow-chat/binding-1",
            }
        ] + [
            {
                "routeName": route["name"],
                "transport": route["transport"],
                "method": route["methods"][0],
                "routePath": _route_path(route),
            }
            for route in routes
        ]
        reconnect_index = next(
            item
            for item, route in enumerate(route_observations)
            if route["routeName"] == "session_reconnect"
        )
        reconnect_id = request_ids[reconnect_index]
        return {
            "requestIds": request_ids,
            "compatibilityProfile": WORKFLOW_CHAT_COMPATIBILITY_PROFILE,
            "reconnectRequestId": reconnect_id,
            "requests": [
                {
                    **route,
                    "requestId": request_id,
                    "bindingId": "binding-1",
                    "providerSessionId": "provider-session-1",
                    "authorized": True,
                    "serverResolvedTarget": True,
                    "reauthorized": request_id == reconnect_id,
                }
                for request_id, route in zip(
                    request_ids, route_observations, strict=True
                )
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
        mutating_route_names = {
            str(route["name"])
            for route in compatibility_map()["routes"]
            if route["mutation"] is True
        }
        mutation_ids = [
            request_ids[item + 1]
            for item, route in enumerate(compatibility_map()["routes"])
            if route["name"] in mutating_route_names
        ]
        return {
            "requestIds": mutation_ids,
            "receipts": [
                {
                    "requestId": request_id,
                    "actor": "user-1",
                    "idempotencyKey": f"idempotency-{item}",
                    "expectedState": "available",
                    "outcome": "accepted",
                    "upstreamCorrelation": f"upstream-{item}",
                    "auditRef": f"artifact://audit-{item}",
                }
                for item, request_id in enumerate(mutation_ids)
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
        artifacts = []
        for ref in ("capture-manifest.json", "captured-final.json"):
            artifacts.append(
                {
                    "ref": ref,
                    "sha256": hashlib.sha256((root / ref).read_bytes()).hexdigest(),
                }
            )
        return {
            **common,
            "artifacts": artifacts,
            "captureManifestRef": "capture-manifest.json",
        }
    if record_type == "continuationReceipt":
        relationship_identity = {
            "relationshipType": "linked_continuation",
            "sourceWorkflowId": "workflow-1",
            "sourceRunId": "run-1",
            "destinationWorkflowId": "workflow-2",
            "destinationRunId": "run-2",
        }
        return {
            "requestIds": request_ids[1:3],
            "idempotencyKey": "continue-once",
            "sourceStateBeforeSha256": "sha256:" + "5" * 64,
            "sourceStateAfterSha256": "sha256:" + "5" * 64,
            "destinationCreationReceipt": {
                "requestId": request_ids[1],
                "created": True,
                "relationshipType": "linked_continuation",
                "sourceWorkflowId": "workflow-1",
                "sourceRunId": "run-1",
                "destinationWorkflowId": "workflow-2",
            },
            "durableRelationship": {
                **relationship_identity,
                "requestId": request_ids[2],
                "direction": "outbound",
                "relationshipDigest": _digest(relationship_identity),
                "createdAt": NOW.isoformat(),
            },
        }
    if record_type == "replaySnapshot":
        return {
            **common,
            "hostUnavailable": True,
            "replayedFromMoonMindArtifacts": True,
            "artifactRefs": ["captured-final.json"],
        }
    raise AssertionError(f"missing test source record: {record_type}")


def _matrix(root: Path) -> dict[str, object]:
    _write(
        root / "capture-manifest.json",
        {"schemaVersion": "moonmind.capture-manifest/v1", "artifacts": ["final"]},
    )
    _write(root / "captured-final.json", {"status": "completed"})
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
                    "data": _record_data(record_type, row_name, index, root),
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
        if channel == "screenshots":
            (root / raw_ref).write_bytes(SCREENSHOT_EVIDENCE)
        else:
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
        unbound_ids = [
            f"not-in-browser-trace-{index}"
            for index in range(len(payload["data"]["receipts"]))
        ]
        payload["data"]["requestIds"] = unbound_ids
        for receipt, request_id in zip(
            payload["data"]["receipts"], unbound_ids, strict=True
        ):
            receipt["requestId"] = request_id

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


def test_facade_route_coverage_must_be_observed_and_path_bound(
    tmp_path: Path,
) -> None:
    source = _matrix(tmp_path)

    def remove_observed_route(payload: dict[str, object]) -> None:
        removed = payload["data"]["requests"].pop()
        payload["data"]["requestIds"].remove(removed["requestId"])

    _rewrite_source(
        tmp_path,
        row_name="scoped-transports-and-resources",
        record_type="facadeRequests",
        update=remove_observed_route,
    )
    manifest = build_workflow_chat_acceptance_manifest(source, evidence_root=tmp_path)

    with pytest.raises(ConformanceContractError, match="coverage"):
        validate_workflow_chat_acceptance_manifest(
            manifest, evidence_root=tmp_path, expected_commit="abc123", now=NOW
        )


def test_facade_route_name_must_match_the_observed_route_path(tmp_path: Path) -> None:
    source = _matrix(tmp_path)

    def substitute_route_path(payload: dict[str, object]) -> None:
        request = next(
            item
            for item in payload["data"]["requests"]
            if item["routeName"] == "get_session"
        )
        request["routePath"] = "health"

    _rewrite_source(
        tmp_path,
        row_name="scoped-transports-and-resources",
        record_type="facadeRequests",
        update=substitute_route_path,
    )
    manifest = build_workflow_chat_acceptance_manifest(source, evidence_root=tmp_path)

    with pytest.raises(ConformanceContractError, match="unauthorized request"):
        validate_workflow_chat_acceptance_manifest(
            manifest, evidence_root=tmp_path, expected_commit="abc123", now=NOW
        )


def test_every_observed_mutation_requires_one_receipt(tmp_path: Path) -> None:
    source = _matrix(tmp_path)

    def omit_receipt(payload: dict[str, object]) -> None:
        removed = payload["data"]["receipts"].pop()
        payload["data"]["requestIds"].remove(removed["requestId"])

    _rewrite_source(
        tmp_path,
        row_name="scoped-transports-and-resources",
        record_type="mutationReceipts",
        update=omit_receipt,
    )
    manifest = build_workflow_chat_acceptance_manifest(source, evidence_root=tmp_path)

    with pytest.raises(ConformanceContractError, match="mutation receipt"):
        validate_workflow_chat_acceptance_manifest(
            manifest, evidence_root=tmp_path, expected_commit="abc123", now=NOW
        )


@pytest.mark.parametrize(
    ("row_name", "request_index", "status"),
    [
        ("native-live-conversation", 1, 500),
        ("authority-and-security-denials", 0, 200),
        ("authority-and-security-denials", 5, 403),
    ],
)
def test_browser_trace_requires_success_or_the_exact_denial_status(
    row_name: str, request_index: int, status: int, tmp_path: Path
) -> None:
    source = _matrix(tmp_path)

    def replace_status(payload: dict[str, object]) -> None:
        payload["data"]["networkEvents"][request_index]["responseStatus"] = status

    _rewrite_source(
        tmp_path,
        row_name=row_name,
        record_type="browserTrace",
        update=replace_status,
    )
    manifest = build_workflow_chat_acceptance_manifest(source, evidence_root=tmp_path)

    with pytest.raises(ConformanceContractError, match="response status"):
        validate_workflow_chat_acceptance_manifest(
            manifest, evidence_root=tmp_path, expected_commit="abc123", now=NOW
        )


def test_browser_trace_scope_requires_a_path_segment_boundary(tmp_path: Path) -> None:
    source = _matrix(tmp_path)

    def replace_path(payload: dict[str, object]) -> None:
        payload["data"]["networkEvents"][0]["path"] = (
            "/omnigent-ui/workflow-chat/binding-1-attacker"
        )

    _rewrite_source(
        tmp_path,
        row_name="native-live-conversation",
        record_type="browserTrace",
        update=replace_path,
    )
    manifest = build_workflow_chat_acceptance_manifest(source, evidence_root=tmp_path)

    with pytest.raises(ConformanceContractError, match="unscoped"):
        validate_workflow_chat_acceptance_manifest(
            manifest, evidence_root=tmp_path, expected_commit="abc123", now=NOW
        )


def test_credential_boundary_must_cover_every_browser_request(tmp_path: Path) -> None:
    source = _matrix(tmp_path)

    def keep_one_request(payload: dict[str, object]) -> None:
        request_id = payload["data"]["requestIds"][0]
        payload["data"]["requestIds"] = [request_id]
        payload["data"]["verifiedRequestIds"] = [request_id]

    _rewrite_source(
        tmp_path,
        row_name="authority-and-security-denials",
        record_type="credentialBoundary",
        update=keep_one_request,
    )
    manifest = build_workflow_chat_acceptance_manifest(source, evidence_root=tmp_path)

    with pytest.raises(ConformanceContractError, match="browser trace"):
        validate_workflow_chat_acceptance_manifest(
            manifest, evidence_root=tmp_path, expected_commit="abc123", now=NOW
        )


def test_captured_artifacts_are_resolved_and_digest_bound(tmp_path: Path) -> None:
    source = _matrix(tmp_path)

    def forge_digest(payload: dict[str, object]) -> None:
        payload["data"]["artifacts"][0]["sha256"] = "9" * 64

    _rewrite_source(
        tmp_path,
        row_name="terminal-evidence-and-continuation",
        record_type="capturedEvidence",
        update=forge_digest,
    )
    manifest = build_workflow_chat_acceptance_manifest(source, evidence_root=tmp_path)

    with pytest.raises(ConformanceContractError, match="artifact is unresolved"):
        validate_workflow_chat_acceptance_manifest(
            manifest, evidence_root=tmp_path, expected_commit="abc123", now=NOW
        )


def test_browser_trace_screenshot_must_match_scanned_screenshot(tmp_path: Path) -> None:
    source = _matrix(tmp_path)

    def replace_screenshot(payload: dict[str, object]) -> None:
        payload["data"]["screenshotSha256"] = "sha256:" + "9" * 64

    _rewrite_source(
        tmp_path,
        row_name="native-live-conversation",
        record_type="browserTrace",
        update=replace_screenshot,
    )
    manifest = build_workflow_chat_acceptance_manifest(source, evidence_root=tmp_path)

    with pytest.raises(ConformanceContractError, match="scanned evidence"):
        validate_workflow_chat_acceptance_manifest(
            manifest, evidence_root=tmp_path, expected_commit="abc123", now=NOW
        )


def test_continuation_requires_creation_and_durable_relationship_evidence(
    tmp_path: Path,
) -> None:
    source = _matrix(tmp_path)

    def remove_relationship(payload: dict[str, object]) -> None:
        payload["data"].pop("durableRelationship")

    _rewrite_source(
        tmp_path,
        row_name="terminal-evidence-and-continuation",
        record_type="continuationReceipt",
        update=remove_relationship,
    )
    manifest = build_workflow_chat_acceptance_manifest(source, evidence_root=tmp_path)

    with pytest.raises(ConformanceContractError, match="durableRelationship"):
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
