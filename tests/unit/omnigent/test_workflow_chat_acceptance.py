"""Protected rollout evidence tests for MoonLadderStudios/MoonMind#3632."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from moonmind.omnigent.conformance import ConformanceContractError
from moonmind.omnigent.harness_platform.support import (
    SupportKeyPayload,
    compute_support_combination_key,
)
from moonmind.omnigent.native_ui_compat import compatibility_map
from moonmind.omnigent.workflow_chat_acceptance import (
    REQUIRED_BUNDLE_DIGESTS,
    REQUIRED_WORKFLOW_CHAT_ROWS,
    REQUIRED_WORKFLOW_CHAT_SOURCE_RECORDS,
    WORKFLOW_CHAT_ACCEPTANCE_ISSUE,
    WORKFLOW_CHAT_CASE_EVIDENCE_VERSION,
    WORKFLOW_CHAT_COMBINATION_VERSION,
    WORKFLOW_CHAT_COMPATIBILITY_PROFILE,
    WORKFLOW_CHAT_PARENT_ISSUE,
    WORKFLOW_CHAT_SCENARIO_VERSION,
    WORKFLOW_CHAT_SOURCE_RECORD_VERSION,
    WorkflowChatCombination,
    build_workflow_chat_acceptance_manifest,
    validate_workflow_chat_acceptance_manifest,
    workflow_chat_case_id,
    workflow_chat_combinations,
)

NOW = datetime(2026, 8, 13, tzinfo=timezone.utc)
IMAGES = {
    "server": "ghcr.io/omnigent/server@sha256:" + "1" * 64,
    "host": "ghcr.io/omnigent/host@sha256:" + "2" * 64,
    "opencodeHost": "ghcr.io/omnigent/host-opencode@sha256:" + "3" * 64,
}
BUNDLE_DIGESTS = {
    "dashboard": "sha256:" + "7" * 64,
    "omnigentUi": "sha256:" + "8" * 64,
}
SCREENSHOT_EVIDENCE = b"secret-free protected screenshot evidence"
ROW_NAMES = list(REQUIRED_WORKFLOW_CHAT_ROWS)
PRIMARY_COMBINATION = "codex-on-demand-through-omnigent"


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


def _correlation(combination_id: str, row_index: int) -> dict[str, str]:
    return {
        "workflowId": f"workflow-{combination_id}",
        "chatBindingId": f"binding-{combination_id}",
        "bridgeSessionId": f"bridge-{combination_id}",
        "providerSessionId": "provider-session-1",
        "browserTraceId": f"browser-trace-{combination_id}-{row_index}",
    }


def _binding_identity(combination: WorkflowChatCombination) -> dict[str, object]:
    fields: dict[str, object] = {
        "omnigentServerBuildRef": "sha256:" + "b" * 64,
        "omnigentHostBuildRef": "sha256:" + "b" * 64,
        "harnessImplementationRef": (
            "omnigent-harness-implementation:sha256:" + "a" * 64
        ),
        "vendorRuntimeRefs": ["opencode@1.18.11#sha256:" + "d" * 64],
        "agentSourceRef": "agent-source:sha256:" + "c" * 64,
        "materializerRefs": [combination.credential_materializer_ref],
        "providerCompatibilityClass": "omnigent-provider-binding-set@1",
        "hostClassRef": combination.host_class_ref,
        "architecture": "linux/amd64",
        "launchPolicyRef": combination.launch_policy_ref,
        "modelConfigDigest": "sha256:" + "e" * 64,
        "executionRealizerRef": combination.execution_realizer_ref,
        "requiredCapabilitiesDigest": "sha256:" + "f" * 64,
    }
    return {
        **fields,
        "supportCombinationKey": compute_support_combination_key(
            SupportKeyPayload.model_validate(fields)
        ),
        "providerProfileClass": combination.provider_profile_class,
    }


def _request_ids(
    row_name: str, combination_id: str, capability_count: int
) -> list[str]:
    if row_name == "scoped-transports-and-resources":
        count = len(compatibility_map()["routes"]) + 1
    elif row_name == "authority-and-security-denials":
        # 8 scoped denial/scan requests, one independent capability-enforcement
        # denial, then one allowed observation per advertised capability.
        count = 9 + capability_count
    else:
        count = 6
    return [f"request-{combination_id}-{row_name}-{item}" for item in range(count)]


def _browser_events(
    row_name: str, combination_id: str, request_ids: list[str]
) -> list[dict[str, object]]:
    binding_id = f"binding-{combination_id}"
    routes = compatibility_map()["routes"]
    if row_name == "scoped-transports-and-resources":
        transports = ["html"] + [str(route["transport"]) for route in routes]
        paths = [f"/omnigent-ui/workflow-chat/{binding_id}"] + [
            f"/api/workflow-chat-bindings/{binding_id}/omnigent/" + _route_path(route)
            for route in routes
        ]
        methods = ["GET"] + [str(route["methods"][0]) for route in routes]
        statuses = [200] + [
            101 if str(route["transport"]) == "websocket" else 200 for route in routes
        ]
    else:
        transports = ["html", "http", "sse", "websocket"] + [
            "http" for _ in request_ids[4:]
        ]
        paths = [f"/omnigent-ui/workflow-chat/{binding_id}"] + [
            f"/api/workflow-chat-bindings/{binding_id}/omnigent/health"
            for _ in request_ids[1:]
        ]
        methods = ["GET"] * len(request_ids)
        if row_name == "native-live-conversation":
            paths[1] = "/api/executions"
            methods[1] = "POST"
            statuses = [201 if item == 1 else 101 if item == 3 else 200
                        for item in range(len(request_ids))]
        elif row_name == "authority-and-security-denials":
            statuses = [
                403 if item <= 6 or item == 8 else 503 if item == 7 else 200
                for item in range(len(request_ids))
            ]
        else:
            statuses = [
                403 if item == 0 else 101 if item == 3 else 200
                for item in range(len(request_ids))
            ]
    return [
        {
            "requestId": request_id,
            "transport": transports[item],
            "method": methods[item],
            "path": paths[item],
            "responseStatus": statuses[item],
            "moonmindScoped": True,
            "browserOriginated": True,
        }
        for item, request_id in enumerate(request_ids)
    ]


def _record_data(
    record_type: str,
    row_name: str,
    row_index: int,
    combination: WorkflowChatCombination,
    root: Path,
) -> dict[str, object]:
    combination_id = combination.combination_id
    correlation = _correlation(combination_id, row_index)
    binding_id = correlation["chatBindingId"]
    advertised = sorted(combination.advertised_capabilities)
    request_ids = _request_ids(row_name, combination_id, len(advertised))
    common: dict[str, object] = {"requestIds": [request_ids[0]]}
    if record_type == "browserTrace":
        return {
            "requestIds": request_ids,
            "route": f"/workflows/{correlation['workflowId']}/chat",
            "traceId": correlation["browserTraceId"],
            "directUpstreamRequestCount": 0,
            "exposedProviderFields": [],
            "screenshotSha256": "sha256:"
            + hashlib.sha256(SCREENSHOT_EVIDENCE).hexdigest(),
            "networkEvents": _browser_events(row_name, combination_id, request_ids),
        }
    if record_type == "executionCreation":
        return {
            "requestIds": [request_ids[1]],
            "createRequestId": request_ids[1],
            "method": "POST",
            "path": "/api/executions",
            "createdThroughPublicApi": True,
            "workflowId": correlation["workflowId"],
            "resolvedBridgeSessionId": correlation["bridgeSessionId"],
            "harnessId": combination.harness_id,
            "executionRealizerRef": combination.execution_realizer_ref,
            "launchPolicyRef": combination.launch_policy_ref,
            "temporalWorkflowId": f"temporal-{combination_id}",
            "temporalRunId": f"temporal-run-{combination_id}",
            "temporalTaskQueue": "moonmind-omnigent",
            "agentProfileSnapshotRef": "agent-profile-snapshot:sha256:" + "3" * 64,
            "executionPlanRef": "omnigent-execution-plan:sha256:" + "4" * 64,
            "providerProfileRef": "provider-profile:omnigent-codex",
        }
    if record_type == "bindingSnapshot":
        return {
            **common,
            "authoritative": True,
            "resolvedBindingId": binding_id,
            "runId": f"run-{combination_id}",
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
                "routePath": f"omnigent-ui/workflow-chat/{binding_id}",
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
                    "bindingId": binding_id,
                    "providerSessionId": correlation["providerSessionId"],
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
        routes = compatibility_map()["routes"]
        mutation_ids = [
            request_ids[item + 1]
            for item, route in enumerate(routes)
            if route["mutation"] is True
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
            "cross_user",
            "cross_workflow",
        ]
        authorized_scope = {
            "authorizedUserId": "user-1",
            "attemptedUserId": "user-1",
            "authorizedWorkflowId": correlation["workflowId"],
            "attemptedWorkflowId": correlation["workflowId"],
        }
        cross_scope = {
            "cross_user": {**authorized_scope, "attemptedUserId": "user-2"},
            "cross_workflow": {
                **authorized_scope,
                "attemptedWorkflowId": f"other-{correlation['workflowId']}",
            },
        }
        return {
            "requestIds": request_ids[: len(kinds)],
            "denials": [
                {
                    "requestId": request_ids[item],
                    "kind": kind,
                    "upstreamForwarded": False,
                    "auditRef": f"artifact://denial-{item}",
                    **(
                        {"scopeIdentity": cross_scope[kind]}
                        if kind in cross_scope
                        else {}
                    ),
                }
                for item, kind in enumerate(kinds)
            ],
        }
    if record_type == "capabilitySnapshot":
        sources = (
            "upstream",
            "agentProfile",
            "providerPolicy",
            "workflowState",
            "callerPermission",
        )
        inputs = {
            name: {
                capability: not (name == "agentProfile" and capability == "changeModel")
                for capability in advertised
            }
            for name in sources
        }
        effective = {
            capability: capability != "changeModel" for capability in advertised
        }
        enforcement = {}
        for item, capability in enumerate(advertised):
            if capability == "changeModel":
                # An independent enforcement request, not a request id already
                # present in denialAudit, so the denied status is derived from
                # the capability observation itself.
                enforcement[capability] = {
                    "requestId": request_ids[8],
                    "outcome": "denied",
                }
            else:
                enforcement[capability] = {
                    "requestId": request_ids[9 + item],
                    "outcome": "allowed",
                }
        return {
            "requestIds": request_ids,
            "inputs": inputs,
            "effective": effective,
            "advertised": advertised,
            "enforcement": enforcement,
            "snapshotDigest": _digest(
                {
                    "inputs": inputs,
                    "effective": effective,
                    "advertised": advertised,
                }
            ),
        }
    if record_type == "scanAudit":
        return {
            "requestIds": request_ids[6:8],
            "attempts": [
                {
                    "requestId": request_ids[6],
                    "outcome": "blocked",
                    "forwarded": False,
                    "auditRef": "artifact://scan-blocked",
                },
                {
                    "requestId": request_ids[7],
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
        artifacts = [
            {
                "ref": ref,
                "sha256": hashlib.sha256((root / ref).read_bytes()).hexdigest(),
            }
            for ref in ("capture-manifest.json", "captured-final.json")
        ]
        return {
            **common,
            "artifacts": artifacts,
            "captureManifestRef": "capture-manifest.json",
        }
    if record_type == "continuationReceipt":
        relationship_identity = {
            "relationshipType": "linked_continuation",
            "sourceWorkflowId": correlation["workflowId"],
            "sourceRunId": f"run-{combination_id}",
            "destinationWorkflowId": f"continuation-{combination_id}",
            "destinationRunId": f"continuation-run-{combination_id}",
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
                "sourceWorkflowId": correlation["workflowId"],
                "sourceRunId": f"run-{combination_id}",
                "destinationWorkflowId": f"continuation-{combination_id}",
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
    if record_type == "cleanupReceipt":
        return {
            **common,
            "steps": [
                {
                    "order": item + 1,
                    "kind": kind,
                    "outcome": "completed",
                    "auditRef": f"artifact://cleanup-{item}",
                }
                for item, kind in enumerate(combination.required_cleanup_steps)
            ],
            "liveResourcesRemoved": combination.live_resources_removed_expected,
            "providerProfileReleasedLast": True,
            "outcome": "released",
            "hostMode": combination.host_mode,
            "cleanupMode": combination.cleanup_mode,
            "cleanupState": "cleaned",
        }
    raise AssertionError(f"missing test source record: {record_type}")


def _source_ref(combination_id: str, row_index: int, record_type: str) -> str:
    return f"source-{combination_id}-{row_index}-{record_type}.json"


def _case_ref(combination_id: str, row_index: int) -> str:
    return f"case-{combination_id}-{row_index}.json"


def _matrix(root: Path) -> dict[str, object]:
    _write(
        root / "capture-manifest.json",
        {"schemaVersion": "moonmind.capture-manifest/v1", "artifacts": ["final"]},
    )
    _write(root / "captured-final.json", {"status": "completed"})
    combinations: dict[str, object] = {}
    for combination_id, combination in workflow_chat_combinations().items():
        declared: dict[str, object] = {
            "schemaVersion": WORKFLOW_CHAT_COMBINATION_VERSION,
            "combinationId": combination_id,
            "harnessId": combination.harness_id,
            "hostClassRef": combination.host_class_ref,
            "launchPolicyRef": combination.launch_policy_ref,
            "executionRealizerRef": combination.execution_realizer_ref,
            "hostMode": combination.host_mode,
            "advertisedCapabilities": sorted(combination.advertised_capabilities),
        }
        if not combination.native_chat_claimed:
            combinations[combination_id] = {
                **declared,
                "status": "unsupported",
                "unsupportedReason": combination.unsupported_reason,
            }
            continue
        rows: dict[str, object] = {}
        cases: list[dict[str, object]] = []
        for row_index, row_name in enumerate(ROW_NAMES):
            assertions = {
                name: True for name in REQUIRED_WORKFLOW_CHAT_ROWS[row_name]
            }
            source_records = []
            for record_type in REQUIRED_WORKFLOW_CHAT_SOURCE_RECORDS[row_name]:
                record_ref = _source_ref(combination_id, row_index, record_type)
                _write(
                    root / record_ref,
                    {
                        "schemaVersion": WORKFLOW_CHAT_SOURCE_RECORD_VERSION,
                        "recordType": record_type,
                        "row": row_name,
                        "combination": combination_id,
                        "sourceCommit": "abc123",
                        "observedAt": NOW.isoformat(),
                        "images": IMAGES,
                        "observed": True,
                        "correlation": _correlation(combination_id, row_index),
                        "data": _record_data(
                            record_type, row_name, row_index, combination, root
                        ),
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
            case_ref = _case_ref(combination_id, row_index)
            _write(
                root / case_ref,
                {
                    "schemaVersion": WORKFLOW_CHAT_CASE_EVIDENCE_VERSION,
                    "issue": WORKFLOW_CHAT_ACCEPTANCE_ISSUE,
                    "parentIssue": WORKFLOW_CHAT_PARENT_ISSUE,
                    "combination": combination_id,
                    "row": row_name,
                    "status": "passed",
                    "sourceCommit": "abc123",
                    "images": IMAGES,
                    "stockHostUnmodified": True,
                    "browserOriginated": True,
                    "moonmindScopedOnly": True,
                    "assertions": assertions,
                    "sourceRecords": source_records,
                    "observations": ["bounded protected observation"],
                },
            )
            rows[row_name] = {
                "status": "passed",
                "assertions": assertions,
                "evidenceRefs": [case_ref],
            }
            cases.append(
                {
                    "caseId": workflow_chat_case_id(combination_id, row_name),
                    "status": "passed",
                    "durationMs": 1000 + row_index,
                    "evidenceRefs": [case_ref],
                }
            )
        report_ref = f"report-{combination_id}.json"
        _write(
            root / report_ref,
            {
                "schemaVersion": "moonmind.omnigent.conformance-report/v1",
                "generatedAt": NOW.isoformat(),
                "images": IMAGES,
                "authMode": combination.auth_mode,
                "cases": cases,
                "summary": {"passed": len(cases), "failed": 0, "skipped": 0},
            },
        )
        timeline_ref = f"timeline-{combination_id}.json"
        _write(
            root / timeline_ref,
            {
                "sessionId": _correlation(combination_id, 0)["bridgeSessionId"],
                "terminal": {"state": "completed"},
                "cleanup": {"state": "cleaned"},
            },
        )
        combinations[combination_id] = {
            **declared,
            "status": "passed",
            "unsupportedReason": None,
            "hostImageRef": IMAGES[combination.host_image_key],
            "bindingIdentity": _binding_identity(combination),
            "rows": rows,
            "reports": [report_ref],
            "timelineRef": timeline_ref,
            "cleanupOutcome": {
                "liveResourcesRemoved": (
                    combination.live_resources_removed_expected
                ),
                "providerProfileReleasedLast": True,
                "cleanupState": "cleaned",
            },
        }
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
        "bundleDigests": dict(BUNDLE_DIGESTS),
        "supersededReportRef": "previous/workflow-chat-acceptance.json",
        "combinations": combinations,
        "evidenceScans": scans,
    }


def _rewrite_source(
    root: Path,
    *,
    row_name: str,
    record_type: str,
    update,
    combination_id: str = PRIMARY_COMBINATION,
) -> None:
    row_index = ROW_NAMES.index(row_name)
    source_path = root / _source_ref(combination_id, row_index, record_type)
    payload = json.loads(source_path.read_text(encoding="utf-8"))
    update(payload)
    _write(source_path, payload)
    case_path = root / _case_ref(combination_id, row_index)
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

    assert set(manifest["combinations"]) == set(workflow_chat_combinations())
    assert manifest["issue"] == WORKFLOW_CHAT_ACCEPTANCE_ISSUE
    assert manifest["scenarioVersion"] == WORKFLOW_CHAT_SCENARIO_VERSION
    assert manifest["routeInventoryVersion"] == compatibility_map()["version"]
    assert set(manifest["bundleDigests"]) == set(REQUIRED_BUNDLE_DIGESTS)
    assert all(item["sha256"] for item in manifest["evidenceManifest"])
    for entry in manifest["combinations"].values():
        assert set(entry["rows"]) == set(REQUIRED_WORKFLOW_CHAT_ROWS)


def test_every_claimed_combination_appears_in_the_protected_matrix() -> None:
    inventory = workflow_chat_combinations()

    assert {
        "codex-on-demand-through-omnigent",
        "codex-static-connected-through-omnigent",
        "opencode-through-generic-omnigent-host",
    } <= set(inventory)
    assert inventory["opencode-through-generic-omnigent-host"].execution_realizer_ref == (
        "generic-omnigent-host@1"
    )
    host_modes = {
        combination.host_mode
        for combination in inventory.values()
        if combination.native_chat_claimed
    }
    assert {"on-demand", "static-connected"} <= host_modes
    # A materially different cleanup mode must yield a different capability
    # contract rather than reusing one constant row set.
    on_demand = inventory["codex-on-demand-through-omnigent"].advertised_capabilities
    static = inventory[
        "codex-static-connected-through-omnigent"
    ].advertised_capabilities
    assert on_demand != static
    assert "cleanupSession" in on_demand and "cleanupSession" not in static


def test_missing_claimed_combination_fails_closed(tmp_path: Path) -> None:
    manifest = build_workflow_chat_acceptance_manifest(
        _matrix(tmp_path), evidence_root=tmp_path
    )
    manifest["combinations"].pop("opencode-through-generic-omnigent-host")

    with pytest.raises(ConformanceContractError, match="combination coverage"):
        validate_workflow_chat_acceptance_manifest(
            manifest, evidence_root=tmp_path, expected_commit="abc123", now=NOW
        )


def test_unclaimed_combination_needs_its_stable_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inventory = workflow_chat_combinations()
    declined = WorkflowChatCombination(
        combination_id="declined-combination",
        harness_id="opencode-native",
        host_class_ref="omnigent-opencode@1",
        launch_policy_ref="omnigent-on-demand@1",
        execution_realizer_ref="generic-omnigent-host@1",
        compose_profile="omnigent-host-codex",
        compose_services=("omnigent",),
        native_chat_claimed=False,
        unsupported_reason="native_chat_not_advertised",
    )
    monkeypatch.setattr(
        "moonmind.omnigent.workflow_chat_acceptance.WORKFLOW_CHAT_COMBINATIONS",
        (inventory[PRIMARY_COMBINATION], declined),
    )
    source = _matrix(tmp_path)
    manifest = build_workflow_chat_acceptance_manifest(
        source, evidence_root=tmp_path
    )
    validate_workflow_chat_acceptance_manifest(
        manifest, evidence_root=tmp_path, expected_commit="abc123", now=NOW
    )
    assert manifest["combinations"]["declined-combination"]["status"] == "unsupported"

    manifest["combinations"]["declined-combination"]["unsupportedReason"] = None
    with pytest.raises(ConformanceContractError, match="unsupported"):
        validate_workflow_chat_acceptance_manifest(
            manifest, evidence_root=tmp_path, expected_commit="abc123", now=NOW
        )


@pytest.mark.parametrize(
    "field",
    ["harnessImplementationRef", "executionRealizerRef", "modelConfigDigest"],
)
def test_binding_identity_must_recompute_the_support_combination_key(
    field: str, tmp_path: Path
) -> None:
    manifest = build_workflow_chat_acceptance_manifest(
        _matrix(tmp_path), evidence_root=tmp_path
    )
    identity = manifest["combinations"][PRIMARY_COMBINATION]["bindingIdentity"]
    if field == "modelConfigDigest":
        identity[field] = "sha256:" + "9" * 64
    elif field == "executionRealizerRef":
        identity[field] = "generic-omnigent-host@1"
    else:
        identity[field] = "omnigent-harness-implementation:sha256:" + "9" * 64

    with pytest.raises(ConformanceContractError, match="binding identity|support combination key"):
        validate_workflow_chat_acceptance_manifest(
            manifest, evidence_root=tmp_path, expected_commit="abc123", now=NOW
        )


def test_binding_identity_must_be_complete(tmp_path: Path) -> None:
    manifest = build_workflow_chat_acceptance_manifest(
        _matrix(tmp_path), evidence_root=tmp_path
    )
    manifest["combinations"][PRIMARY_COMBINATION]["bindingIdentity"].pop(
        "providerProfileClass"
    )

    with pytest.raises(ConformanceContractError, match="binding identity is incomplete"):
        validate_workflow_chat_acceptance_manifest(
            manifest, evidence_root=tmp_path, expected_commit="abc123", now=NOW
        )


def test_bundle_digests_are_required(tmp_path: Path) -> None:
    manifest = build_workflow_chat_acceptance_manifest(
        _matrix(tmp_path), evidence_root=tmp_path
    )
    manifest["bundleDigests"].pop("omnigentUi")

    with pytest.raises(ConformanceContractError, match="bundle digests"):
        validate_workflow_chat_acceptance_manifest(
            manifest, evidence_root=tmp_path, expected_commit="abc123", now=NOW
        )


def test_route_inventory_version_must_match_the_served_surface(
    tmp_path: Path,
) -> None:
    manifest = build_workflow_chat_acceptance_manifest(
        _matrix(tmp_path), evidence_root=tmp_path
    )
    manifest["routeInventoryVersion"] = "omnigent.native-ui-compat/v0"

    with pytest.raises(ConformanceContractError, match="route-inventory"):
        validate_workflow_chat_acceptance_manifest(
            manifest, evidence_root=tmp_path, expected_commit="abc123", now=NOW
        )


def test_report_cases_require_a_measured_duration(tmp_path: Path) -> None:
    source = _matrix(tmp_path)
    report_path = tmp_path / f"report-{PRIMARY_COMBINATION}.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["cases"][0].pop("durationMs")
    _write(report_path, report)
    manifest = build_workflow_chat_acceptance_manifest(
        source, evidence_root=tmp_path
    )

    with pytest.raises(ConformanceContractError, match="report cases are malformed"):
        validate_workflow_chat_acceptance_manifest(
            manifest, evidence_root=tmp_path, expected_commit="abc123", now=NOW
        )


def test_operator_timeline_must_bind_the_terminal_cleaned_session(
    tmp_path: Path,
) -> None:
    source = _matrix(tmp_path)
    timeline_path = tmp_path / f"timeline-{PRIMARY_COMBINATION}.json"
    _write(
        timeline_path,
        {
            "sessionId": "unrelated-session",
            "terminal": {"state": "completed"},
            "cleanup": {"state": "cleaned"},
        },
    )
    manifest = build_workflow_chat_acceptance_manifest(
        source, evidence_root=tmp_path
    )

    with pytest.raises(ConformanceContractError, match="operator timeline"):
        validate_workflow_chat_acceptance_manifest(
            manifest, evidence_root=tmp_path, expected_commit="abc123", now=NOW
        )


def test_execution_creation_must_use_the_public_executions_api(
    tmp_path: Path,
) -> None:
    source = _matrix(tmp_path)
    _rewrite_source(
        tmp_path,
        row_name="native-live-conversation",
        record_type="executionCreation",
        update=lambda payload: payload["data"].update(
            {"createdThroughPublicApi": False}
        ),
    )
    manifest = build_workflow_chat_acceptance_manifest(
        source, evidence_root=tmp_path
    )

    with pytest.raises(ConformanceContractError, match="/api/executions"):
        validate_workflow_chat_acceptance_manifest(
            manifest, evidence_root=tmp_path, expected_commit="abc123", now=NOW
        )


def test_execution_creation_must_resolve_the_live_session(tmp_path: Path) -> None:
    source = _matrix(tmp_path)
    _rewrite_source(
        tmp_path,
        row_name="native-live-conversation",
        record_type="executionCreation",
        update=lambda payload: payload["data"].update(
            {"resolvedBridgeSessionId": "other-session"}
        ),
    )
    manifest = build_workflow_chat_acceptance_manifest(
        source, evidence_root=tmp_path
    )

    with pytest.raises(ConformanceContractError, match="/api/executions"):
        validate_workflow_chat_acceptance_manifest(
            manifest, evidence_root=tmp_path, expected_commit="abc123", now=NOW
        )


@pytest.mark.parametrize("kind", ["cross_user", "cross_workflow"])
def test_cross_user_and_cross_workflow_denials_are_required(
    kind: str, tmp_path: Path
) -> None:
    source = _matrix(tmp_path)

    def drop_denial(payload: dict[str, object]) -> None:
        removed = next(
            item for item in payload["data"]["denials"] if item["kind"] == kind
        )
        payload["data"]["denials"].remove(removed)
        payload["data"]["requestIds"].remove(removed["requestId"])

    _rewrite_source(
        tmp_path,
        row_name="authority-and-security-denials",
        record_type="denialAudit",
        update=drop_denial,
    )
    manifest = build_workflow_chat_acceptance_manifest(
        source, evidence_root=tmp_path
    )

    with pytest.raises(ConformanceContractError, match="denialAudit coverage"):
        validate_workflow_chat_acceptance_manifest(
            manifest, evidence_root=tmp_path, expected_commit="abc123", now=NOW
        )


def test_capability_coverage_is_derived_from_the_advertised_contract(
    tmp_path: Path,
) -> None:
    source = _matrix(tmp_path)

    def drop_capability(payload: dict[str, object]) -> None:
        payload["data"]["enforcement"].pop("readResources")

    _rewrite_source(
        tmp_path,
        row_name="authority-and-security-denials",
        record_type="capabilitySnapshot",
        update=drop_capability,
    )
    manifest = build_workflow_chat_acceptance_manifest(
        source, evidence_root=tmp_path
    )

    with pytest.raises(
        ConformanceContractError, match="every advertised capability"
    ):
        validate_workflow_chat_acceptance_manifest(
            manifest, evidence_root=tmp_path, expected_commit="abc123", now=NOW
        )


def test_capability_enforcement_must_match_the_effective_decision(
    tmp_path: Path,
) -> None:
    source = _matrix(tmp_path)

    def allow_denied_capability(payload: dict[str, object]) -> None:
        payload["data"]["enforcement"]["changeModel"]["outcome"] = "allowed"

    _rewrite_source(
        tmp_path,
        row_name="authority-and-security-denials",
        record_type="capabilitySnapshot",
        update=allow_denied_capability,
    )
    manifest = build_workflow_chat_acceptance_manifest(
        source, evidence_root=tmp_path
    )

    with pytest.raises(
        ConformanceContractError, match="does not match the effective decision"
    ):
        validate_workflow_chat_acceptance_manifest(
            manifest, evidence_root=tmp_path, expected_commit="abc123", now=NOW
        )


def test_advertised_capability_namespace_must_match_the_combination(
    tmp_path: Path,
) -> None:
    source = _matrix(tmp_path)

    def widen_namespace(payload: dict[str, object]) -> None:
        payload["data"]["advertised"] = payload["data"]["advertised"] + ["invented"]

    _rewrite_source(
        tmp_path,
        row_name="authority-and-security-denials",
        record_type="capabilitySnapshot",
        update=widen_namespace,
    )
    manifest = build_workflow_chat_acceptance_manifest(
        source, evidence_root=tmp_path
    )

    with pytest.raises(
        ConformanceContractError, match="advertised capability contract"
    ):
        validate_workflow_chat_acceptance_manifest(
            manifest, evidence_root=tmp_path, expected_commit="abc123", now=NOW
        )


def test_cleanup_must_release_the_provider_profile_last(tmp_path: Path) -> None:
    source = _matrix(tmp_path)

    def release_profile_first(payload: dict[str, object]) -> None:
        steps = payload["data"]["steps"]
        release = next(
            item for item in steps if item["kind"] == "provider_profile_release"
        )
        release["order"] = 1
        for step in steps:
            if step is not release:
                step["order"] += 1

    _rewrite_source(
        tmp_path,
        row_name="terminal-evidence-and-continuation",
        record_type="cleanupReceipt",
        update=release_profile_first,
    )
    manifest = build_workflow_chat_acceptance_manifest(
        source, evidence_root=tmp_path
    )

    with pytest.raises(ConformanceContractError, match="release-last cleanup"):
        validate_workflow_chat_acceptance_manifest(
            manifest, evidence_root=tmp_path, expected_commit="abc123", now=NOW
        )


def test_cleanup_receipt_must_cover_live_resource_removal(tmp_path: Path) -> None:
    source = _matrix(tmp_path)

    def drop_host_stop(payload: dict[str, object]) -> None:
        steps = [
            step
            for step in payload["data"]["steps"]
            if step["kind"] != "live_host_stopped"
        ]
        for order, step in enumerate(steps, start=1):
            step["order"] = order
        payload["data"]["steps"] = steps

    _rewrite_source(
        tmp_path,
        row_name="terminal-evidence-and-continuation",
        record_type="cleanupReceipt",
        update=drop_host_stop,
    )
    manifest = build_workflow_chat_acceptance_manifest(
        source, evidence_root=tmp_path
    )

    with pytest.raises(ConformanceContractError, match="release-last cleanup"):
        validate_workflow_chat_acceptance_manifest(
            manifest, evidence_root=tmp_path, expected_commit="abc123", now=NOW
        )


def test_combination_cleanup_outcome_is_required(tmp_path: Path) -> None:
    manifest = build_workflow_chat_acceptance_manifest(
        _matrix(tmp_path), evidence_root=tmp_path
    )
    manifest["combinations"][PRIMARY_COMBINATION]["cleanupOutcome"][
        "providerProfileReleasedLast"
    ] = False

    with pytest.raises(ConformanceContractError, match="cleanup"):
        validate_workflow_chat_acceptance_manifest(
            manifest, evidence_root=tmp_path, expected_commit="abc123", now=NOW
        )


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

    with pytest.raises(ConformanceContractError, match="source record|Chat"):
        validate_workflow_chat_acceptance_manifest(
            manifest, evidence_root=tmp_path, expected_commit="abc123", now=NOW
        )


def test_source_records_must_declare_the_combination_under_test(
    tmp_path: Path,
) -> None:
    source = _matrix(tmp_path)
    _rewrite_source(
        tmp_path,
        row_name="native-live-conversation",
        record_type="bindingSnapshot",
        update=lambda payload: payload.update({"combination": "other-combination"}),
    )
    manifest = build_workflow_chat_acceptance_manifest(
        source, evidence_root=tmp_path
    )

    with pytest.raises(ConformanceContractError, match="not bound to the combination"):
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
        payload["correlation"]["workflowId"] = "workflow-substituted"
        if payload["recordType"] == "browserTrace":
            payload["data"]["route"] = "/workflows/workflow-substituted/chat"

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
        ("authority-and-security-denials", 7, 403),
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
            f"/omnigent-ui/workflow-chat/binding-{PRIMARY_COMBINATION}-attacker"
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
        "absolute_superseded_ref",
    ],
)
def test_native_chat_rollout_gate_fails_closed(
    mutation: str, tmp_path: Path
) -> None:
    source = _matrix(tmp_path)
    manifest = build_workflow_chat_acceptance_manifest(
        source, evidence_root=tmp_path
    )
    entry = manifest["combinations"][PRIMARY_COMBINATION]
    primary_case = _case_ref(PRIMARY_COMBINATION, 0)
    if mutation == "missing_row":
        entry["rows"].pop("native-live-conversation")
    elif mutation == "failed_assertion":
        entry["rows"]["native-live-conversation"]["assertions"][
            "native_ui_primary"
        ] = False
    elif mutation == "stale":
        manifest["generatedAt"] = (NOW - timedelta(days=8)).isoformat()
    elif mutation == "wrong_commit":
        manifest["sourceCommit"] = "old"
    elif mutation == "mutable_image":
        manifest["images"]["host"] = "ghcr.io/omnigent/host:latest"
    elif mutation == "unresolved_ref":
        entry["rows"]["native-live-conversation"]["evidenceRefs"] = ["missing.json"]
    elif mutation == "tampered_ref":
        (tmp_path / primary_case).write_text("{}", encoding="utf-8")
    elif mutation == "failed_report":
        report_ref = f"report-{PRIMARY_COMBINATION}.json"
        report = json.loads((tmp_path / report_ref).read_text(encoding="utf-8"))
        report["cases"][0]["status"] = "failed"
        report["summary"] = {
            "passed": len(report["cases"]) - 1,
            "failed": 1,
            "skipped": 0,
        }
        _write(tmp_path / report_ref, report)
        for item in manifest["evidenceManifest"]:
            if item["ref"] == report_ref:
                item["sha256"] = hashlib.sha256(
                    (tmp_path / report_ref).read_bytes()
                ).hexdigest()
    elif mutation == "missing_scan":
        manifest["evidenceScans"].pop("archives")
    elif mutation == "absolute_superseded_ref":
        manifest["supersededReportRef"] = "/etc/passwd"
    else:
        case = json.loads((tmp_path / primary_case).read_text(encoding="utf-8"))
        case["sourceRecords"] = [
            record
            for record in case["sourceRecords"]
            if record["type"] != "browserTrace"
        ]
        _write(tmp_path / primary_case, case)
        for item in manifest["evidenceManifest"]:
            if item["ref"] == primary_case:
                item["sha256"] = hashlib.sha256(
                    (tmp_path / primary_case).read_bytes()
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
    case_ref = _case_ref(PRIMARY_COMBINATION, 0)
    case = json.loads((tmp_path / case_ref).read_text(encoding="utf-8"))
    case["observations"] = ["Authorization: Bearer exposed-value"]
    _write(tmp_path / case_ref, case)
    manifest = build_workflow_chat_acceptance_manifest(
        source, evidence_root=tmp_path
    )

    with pytest.raises(ConformanceContractError, match="secret-like"):
        validate_workflow_chat_acceptance_manifest(
            manifest, evidence_root=tmp_path, now=NOW
        )


def test_retirement_guard_consumes_the_manifest_without_interpretation(
    tmp_path: Path,
) -> None:
    from moonmind.omnigent.legacy_retirement import (
        RETIREMENT_INVENTORY,
        RetirementCriterion,
        criteria_from_native_chat_acceptance,
        evaluate_retirement,
    )

    manifest = build_workflow_chat_acceptance_manifest(
        _matrix(tmp_path), evidence_root=tmp_path
    )

    passed = criteria_from_native_chat_acceptance(
        manifest, evidence_root=tmp_path, expected_commit="abc123", now=NOW
    )

    assert passed == frozenset({RetirementCriterion.NATIVE_CHAT_ACCEPTANCE_PASSED})
    native_chat_path = next(
        path
        for path in RETIREMENT_INVENTORY
        if path.path_id == "omnigent.legacy.native_ui_compat"
    )
    decision = evaluate_retirement(native_chat_path, passed)
    assert RetirementCriterion.NATIVE_CHAT_ACCEPTANCE_PASSED not in (
        decision.unmet_criteria
    )


@pytest.mark.parametrize("failure", ["missing", "expired", "incomplete"])
def test_retirement_guard_gets_no_criterion_from_unusable_evidence(
    failure: str, tmp_path: Path
) -> None:
    from moonmind.omnigent.legacy_retirement import (
        criteria_from_native_chat_acceptance,
    )

    if failure == "missing":
        manifest = None
    else:
        manifest = build_workflow_chat_acceptance_manifest(
            _matrix(tmp_path), evidence_root=tmp_path
        )
        if failure == "expired":
            manifest["expiresAt"] = (NOW - timedelta(days=1)).isoformat()
        else:
            manifest["combinations"].pop(PRIMARY_COMBINATION)

    assert (
        criteria_from_native_chat_acceptance(
            manifest, evidence_root=tmp_path, expected_commit="abc123", now=NOW
        )
        == frozenset()
    )


def test_cleanup_outcome_must_match_the_typed_cleanup_receipt(tmp_path: Path) -> None:
    manifest = build_workflow_chat_acceptance_manifest(
        _matrix(tmp_path), evidence_root=tmp_path
    )
    entry = manifest["combinations"][PRIMARY_COMBINATION]
    entry["cleanupOutcome"]["cleanupState"] = "claimed-clean"
    _write(
        tmp_path / f"timeline-{PRIMARY_COMBINATION}.json",
        {
            "sessionId": _correlation(PRIMARY_COMBINATION, 0)["bridgeSessionId"],
            "terminal": {"state": "completed"},
            "cleanup": {"state": "claimed-clean"},
        },
    )
    for item in manifest["evidenceManifest"]:
        if item["ref"] == f"timeline-{PRIMARY_COMBINATION}.json":
            item["sha256"] = hashlib.sha256(
                (tmp_path / item["ref"]).read_bytes()
            ).hexdigest()

    with pytest.raises(
        ConformanceContractError, match="does not match the typed cleanup receipt"
    ):
        validate_workflow_chat_acceptance_manifest(
            manifest, evidence_root=tmp_path, expected_commit="abc123", now=NOW
        )


STATIC_COMBINATION = "codex-static-connected-through-omnigent"
OPENCODE_COMBINATION = "opencode-through-generic-omnigent-host"


def _validate(manifest: dict, root: Path) -> None:
    validate_workflow_chat_acceptance_manifest(
        manifest, evidence_root=root, expected_commit="abc123", now=NOW
    )


def test_cross_scope_denial_without_identities_fails_closed(tmp_path: Path) -> None:
    source = _matrix(tmp_path)

    def drop_identity(payload: dict) -> None:
        for denial in payload["data"]["denials"]:
            denial.pop("scopeIdentity", None)

    _rewrite_source(
        tmp_path,
        row_name="authority-and-security-denials",
        record_type="denialAudit",
        update=drop_identity,
    )
    manifest = build_workflow_chat_acceptance_manifest(
        source, evidence_root=tmp_path
    )

    with pytest.raises(ConformanceContractError, match="scopeIdentity"):
        _validate(manifest, tmp_path)


def test_relabelled_cross_scope_denial_fails_closed(tmp_path: Path) -> None:
    source = _matrix(tmp_path)

    def reuse_authorized_scope(payload: dict) -> None:
        for denial in payload["data"]["denials"]:
            if denial["kind"] == "cross_user":
                # A denial that never left the authorized user proves nothing
                # about user isolation.
                denial["scopeIdentity"]["attemptedUserId"] = denial[
                    "scopeIdentity"
                ]["authorizedUserId"]

    _rewrite_source(
        tmp_path,
        row_name="authority-and-security-denials",
        record_type="denialAudit",
        update=reuse_authorized_scope,
    )
    manifest = build_workflow_chat_acceptance_manifest(
        source, evidence_root=tmp_path
    )

    with pytest.raises(ConformanceContractError, match="vary exactly that scope"):
        _validate(manifest, tmp_path)


def test_cross_scope_denial_must_bind_the_workflow_under_test(
    tmp_path: Path,
) -> None:
    source = _matrix(tmp_path)

    def borrow_another_session(payload: dict) -> None:
        for denial in payload["data"]["denials"]:
            if denial["kind"] == "cross_workflow":
                denial["scopeIdentity"]["authorizedWorkflowId"] = "other-workflow"

    _rewrite_source(
        tmp_path,
        row_name="authority-and-security-denials",
        record_type="denialAudit",
        update=borrow_another_session,
    )
    manifest = build_workflow_chat_acceptance_manifest(
        source, evidence_root=tmp_path
    )

    with pytest.raises(ConformanceContractError, match="workflow under test"):
        _validate(manifest, tmp_path)


def test_denied_capability_enforcement_expects_a_denied_browser_status(
    tmp_path: Path,
) -> None:
    source = _matrix(tmp_path)
    row_index = ROW_NAMES.index("authority-and-security-denials")
    snapshot = json.loads(
        (
            tmp_path
            / _source_ref(PRIMARY_COMBINATION, row_index, "capabilitySnapshot")
        ).read_text(encoding="utf-8")
    )
    denied_request_id = next(
        observation["requestId"]
        for observation in snapshot["data"]["enforcement"].values()
        if observation["outcome"] == "denied"
    )
    # An independent enforcement request, not one already listed in denialAudit.
    assert denied_request_id not in {
        item["requestId"]
        for item in json.loads(
            (
                tmp_path / _source_ref(PRIMARY_COMBINATION, row_index, "denialAudit")
            ).read_text(encoding="utf-8")
        )["data"]["denials"]
    }

    def succeed_denied_enforcement(payload: dict) -> None:
        for event in payload["data"]["networkEvents"]:
            if event["requestId"] == denied_request_id:
                event["responseStatus"] = 200

    _rewrite_source(
        tmp_path,
        row_name="authority-and-security-denials",
        record_type="browserTrace",
        update=succeed_denied_enforcement,
    )
    manifest = build_workflow_chat_acceptance_manifest(
        source, evidence_root=tmp_path
    )

    with pytest.raises(ConformanceContractError, match="unexpected response status"):
        _validate(manifest, tmp_path)


def test_execution_creation_must_match_the_observed_browser_call(
    tmp_path: Path,
) -> None:
    source = _matrix(tmp_path)

    def rewrite_create_event(payload: dict) -> None:
        create_event = payload["data"]["networkEvents"][1]
        create_event["method"] = "GET"
        create_event["path"] = f"/api/executions/workflow-{PRIMARY_COMBINATION}"

    _rewrite_source(
        tmp_path,
        row_name="native-live-conversation",
        record_type="browserTrace",
        update=rewrite_create_event,
    )
    manifest = build_workflow_chat_acceptance_manifest(
        source, evidence_root=tmp_path
    )

    with pytest.raises(
        ConformanceContractError, match="observed browser create request"
    ):
        _validate(manifest, tmp_path)


def test_cleanup_step_order_is_enforced(tmp_path: Path) -> None:
    source = _matrix(tmp_path)

    def swap_middle_steps(payload: dict) -> None:
        steps = payload["data"]["steps"]
        steps[1]["order"], steps[2]["order"] = steps[2]["order"], steps[1]["order"]

    _rewrite_source(
        tmp_path,
        row_name="terminal-evidence-and-continuation",
        record_type="cleanupReceipt",
        update=swap_middle_steps,
    )
    manifest = build_workflow_chat_acceptance_manifest(
        source, evidence_root=tmp_path
    )

    with pytest.raises(ConformanceContractError, match="release-last cleanup"):
        _validate(manifest, tmp_path)


def test_static_connected_cleanup_is_a_drain_not_a_stop() -> None:
    inventory = workflow_chat_combinations()
    static = inventory[STATIC_COMBINATION]
    on_demand = inventory[PRIMARY_COMBINATION]

    assert static.required_cleanup_steps[0] == "live_host_drained"
    assert static.live_resources_removed_expected is False
    assert on_demand.required_cleanup_steps[0] == "live_host_stopped"
    assert on_demand.live_resources_removed_expected is True


def test_static_connected_host_may_not_claim_a_stopped_host(
    tmp_path: Path,
) -> None:
    source = _matrix(tmp_path)

    def claim_a_stopped_host(payload: dict) -> None:
        payload["data"]["steps"][0]["kind"] = "live_host_stopped"
        payload["data"]["liveResourcesRemoved"] = True

    _rewrite_source(
        tmp_path,
        row_name="terminal-evidence-and-continuation",
        record_type="cleanupReceipt",
        update=claim_a_stopped_host,
        combination_id=STATIC_COMBINATION,
    )
    source["combinations"][STATIC_COMBINATION]["cleanupOutcome"][
        "liveResourcesRemoved"
    ] = True
    manifest = build_workflow_chat_acceptance_manifest(
        source, evidence_root=tmp_path
    )

    with pytest.raises(ConformanceContractError, match="cleanup outcome"):
        _validate(manifest, tmp_path)


def test_provider_profile_class_is_pinned_by_the_claimed_inventory(
    tmp_path: Path,
) -> None:
    manifest = build_workflow_chat_acceptance_manifest(
        _matrix(tmp_path), evidence_root=tmp_path
    )
    manifest["combinations"][OPENCODE_COMBINATION]["bindingIdentity"][
        "providerProfileClass"
    ] = workflow_chat_combinations()[PRIMARY_COMBINATION].provider_profile_class

    with pytest.raises(ConformanceContractError, match="combination under test"):
        _validate(manifest, tmp_path)


def test_credential_materializer_must_match_the_combination(tmp_path: Path) -> None:
    manifest = build_workflow_chat_acceptance_manifest(
        _matrix(tmp_path), evidence_root=tmp_path
    )
    manifest["combinations"][OPENCODE_COMBINATION]["bindingIdentity"][
        "materializerRefs"
    ] = ["codex-oauth-home@1"]

    with pytest.raises(ConformanceContractError, match="combination under test"):
        _validate(manifest, tmp_path)


def test_opencode_combination_pins_its_own_host_image(tmp_path: Path) -> None:
    manifest = build_workflow_chat_acceptance_manifest(
        _matrix(tmp_path), evidence_root=tmp_path
    )
    manifest["combinations"][OPENCODE_COMBINATION]["hostImageRef"] = manifest[
        "images"
    ]["host"]

    with pytest.raises(ConformanceContractError, match="host image digest"):
        _validate(manifest, tmp_path)


def test_report_from_another_combination_cannot_qualify_this_one(
    tmp_path: Path,
) -> None:
    manifest = build_workflow_chat_acceptance_manifest(
        _matrix(tmp_path), evidence_root=tmp_path
    )
    manifest["combinations"][OPENCODE_COMBINATION]["reports"] = [
        f"report-{PRIMARY_COMBINATION}.json"
    ]

    with pytest.raises(ConformanceContractError, match="row evidence"):
        _validate(manifest, tmp_path)


def test_report_case_must_reference_its_own_row_evidence(tmp_path: Path) -> None:
    source = _matrix(tmp_path)
    report_path = tmp_path / f"report-{PRIMARY_COMBINATION}.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["cases"][0]["evidenceRefs"] = report["cases"][1]["evidenceRefs"]
    _write(report_path, report)
    manifest = build_workflow_chat_acceptance_manifest(
        source, evidence_root=tmp_path
    )

    with pytest.raises(ConformanceContractError, match="row evidence"):
        _validate(manifest, tmp_path)


def test_report_must_publish_the_combination_auth_mode(tmp_path: Path) -> None:
    source = _matrix(tmp_path)
    report_path = tmp_path / f"report-{OPENCODE_COMBINATION}.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["authMode"] = workflow_chat_combinations()[
        PRIMARY_COMBINATION
    ].auth_mode
    _write(report_path, report)
    manifest = build_workflow_chat_acceptance_manifest(
        source, evidence_root=tmp_path
    )

    with pytest.raises(ConformanceContractError, match="authentication mode"):
        _validate(manifest, tmp_path)
