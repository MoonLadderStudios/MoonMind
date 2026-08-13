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
from moonmind.omnigent.native_ui_compat import compatibility_map

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
_SHA256_REF = re.compile(r"^sha256:[0-9a-f]{64}$")
_CORRELATION_FIELDS = (
    "workflowId",
    "chatBindingId",
    "bridgeSessionId",
    "providerSessionId",
    "browserTraceId",
)
_GLOBAL_CORRELATION_FIELDS = _CORRELATION_FIELDS[:-1]
_TRANSPORTS = frozenset({"html", "http", "sse", "websocket"})
_NATIVE_UI_DOCUMENT_ROUTE = "native_ui_document"
_LINKED_CONTINUATION_RELATIONSHIP = "linked_continuation"
_NATIVE_CONTROL_KINDS = frozenset(
    {"approval", "tool", "file", "terminal", "agent", "task"}
)
_DENIAL_KINDS = frozenset(
    {
        "alternate_binding",
        "provider_session_substitution",
        "hidden_control",
        "immutable_policy",
    }
)
_TERMINAL_STATES = frozenset(
    {"completed", "failed", "canceled", "cancelled", "timed_out", "stopped"}
)


def _require_mapping(value: Any, *, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ConformanceContractError(
            f"workflow Chat source record {field} must be an object"
        )
    return value


def _require_string(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConformanceContractError(
            f"workflow Chat source record {field} must be a non-empty string"
        )
    return value


def _require_string_list(value: Any, *, field: str) -> list[str]:
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, str) or not item.strip() for item in value)
        or len(set(value)) != len(value)
    ):
        raise ConformanceContractError(
            f"workflow Chat source record {field} must contain unique strings"
        )
    return value


def _require_sha256_ref(value: Any, *, field: str) -> str:
    result = _require_string(value, field=field)
    if _SHA256_REF.fullmatch(result) is None:
        raise ConformanceContractError(
            f"workflow Chat source record {field} must be an immutable digest"
        )
    return result


def _canonical_digest(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _is_scoped_path(path: str, allowed_bases: tuple[str, ...]) -> bool:
    parsed_path = urllib.parse.urlsplit(path).path
    return any(
        parsed_path == base or parsed_path.startswith(base + "/")
        for base in allowed_bases
    )


def _require_evidence_items(value: Any, *, field: str) -> list[dict[str, str]]:
    if not isinstance(value, list) or not value:
        raise ConformanceContractError(
            f"workflow Chat source record {field} must contain evidence items"
        )
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw_item in value:
        item = _require_mapping(raw_item, field=f"{field}[]")
        ref = _require_string(item.get("ref"), field=f"{field}[].ref")
        parsed = urllib.parse.urlparse(ref)
        if (
            ref in seen
            or parsed.scheme not in {"", "https"}
            or (not parsed.scheme and Path(ref).is_absolute())
        ):
            raise ConformanceContractError(
                f"workflow Chat source record {field} contains an unpackaged ref"
            )
        digest = _require_string(item.get("sha256"), field=f"{field}[].sha256")
        if _SHA256.fullmatch(digest) is None:
            raise ConformanceContractError(
                f"workflow Chat source record {field} contains a malformed digest"
            )
        seen.add(ref)
        result.append({"ref": ref, "sha256": digest})
    return result


def _validate_record_data(
    record_type: str,
    data: Mapping[str, Any],
    *,
    correlation: Mapping[str, str],
) -> None:
    request_ids = _require_string_list(
        data.get("requestIds"), field=f"{record_type}.data.requestIds"
    )
    request_id_set = set(request_ids)
    workflow_id = correlation["workflowId"]
    binding_id = correlation["chatBindingId"]
    provider_session_id = correlation["providerSessionId"]

    if record_type == "browserTrace":
        events = data.get("networkEvents")
        if not isinstance(events, list) or not events:
            raise ConformanceContractError(
                "workflow Chat browserTrace requires observed network events"
            )
        event_ids: set[str] = set()
        allowed_prefixes = (
            f"/omnigent-ui/workflow-chat/{binding_id}",
            f"/api/workflow-chat-bindings/{binding_id}/omnigent",
            f"/api/executions/{workflow_id}",
        )
        for event in events:
            item = _require_mapping(event, field="browserTrace.networkEvents[]")
            request_id = _require_string(
                item.get("requestId"), field="browserTrace.networkEvents[].requestId"
            )
            transport = _require_string(
                item.get("transport"), field="browserTrace.networkEvents[].transport"
            )
            path = _require_string(
                item.get("path"), field="browserTrace.networkEvents[].path"
            )
            _require_string(
                item.get("method"), field="browserTrace.networkEvents[].method"
            )
            status = item.get("responseStatus")
            if (
                request_id in event_ids
                or transport not in _TRANSPORTS
                or not _is_scoped_path(path, allowed_prefixes)
                or not isinstance(status, int)
                or not 100 <= status <= 599
                or item.get("moonmindScoped") is not True
                or item.get("browserOriginated") is not True
                or "providerSessionId" in item
                or "upstreamUrl" in item
            ):
                raise ConformanceContractError(
                    "workflow Chat browserTrace contains an unscoped or "
                    "malformed request"
                )
            event_ids.add(request_id)
        if (
            event_ids != request_id_set
            or data.get("route") != f"/workflows/{workflow_id}/chat"
            or data.get("traceId") != correlation["browserTraceId"]
            or data.get("directUpstreamRequestCount") != 0
            or data.get("exposedProviderFields") != []
        ):
            raise ConformanceContractError(
                "workflow Chat browserTrace does not prove the scoped product route"
            )
        _require_sha256_ref(
            data.get("screenshotSha256"), field="browserTrace.data.screenshotSha256"
        )
        return

    if record_type == "bindingSnapshot":
        if (
            data.get("authoritative") is not True
            or data.get("resolvedBindingId") != binding_id
            or data.get("state") not in {"starting", "available", "ended"}
            or not isinstance(data.get("readOnly"), bool)
        ):
            raise ConformanceContractError(
                "workflow Chat bindingSnapshot is not authoritative"
            )
        _require_string(data.get("runId"), field="bindingSnapshot.data.runId")
        _require_sha256_ref(
            data.get("capabilitiesDigest"),
            field="bindingSnapshot.data.capabilitiesDigest",
        )
        return

    if record_type == "nativeConversation":
        if (
            data.get("renderer") != "omnigent-native"
            or data.get("composerRequestId") not in request_id_set
        ):
            raise ConformanceContractError(
                "workflow Chat nativeConversation is not the native send path"
            )
        _require_string_list(
            data.get("transcriptMessageIds"),
            field="nativeConversation.data.transcriptMessageIds",
        )
        _require_string_list(
            data.get("queuedMessageIds"),
            field="nativeConversation.data.queuedMessageIds",
        )
        _require_string(
            data.get("nativeAppVersion"),
            field="nativeConversation.data.nativeAppVersion",
        )
        return

    if record_type == "nativeControls":
        controls = set(
            _require_string_list(
                data.get("controlKinds"), field="nativeControls.data.controlKinds"
            )
        )
        if (
            not _NATIVE_CONTROL_KINDS.issubset(controls)
            or data.get("renderer") != "omnigent-native"
            or data.get("customComposerCount") != 0
        ):
            raise ConformanceContractError(
                "workflow Chat nativeControls do not prove native-primary controls"
            )
        return

    if record_type == "facadeRequests":
        requests = data.get("requests")
        if not isinstance(requests, list) or not requests:
            raise ConformanceContractError(
                "workflow Chat facadeRequests requires resolved requests"
            )
        observed_ids: set[str] = set()
        transports: set[str] = set()
        observed_route_names: set[str] = set()
        reconnect_id = data.get("reconnectRequestId")
        reconnect_reauthorized = False
        routes = compatibility_map()["routes"]
        expected_routes = {str(item["name"]): item for item in routes}
        for request in requests:
            item = _require_mapping(request, field="facadeRequests.data.requests[]")
            request_id = _require_string(
                item.get("requestId"), field="facadeRequests.data.requests[].requestId"
            )
            transport = _require_string(
                item.get("transport"), field="facadeRequests.data.requests[].transport"
            )
            route_name = _require_string(
                item.get("routeName"), field="facadeRequests.data.requests[].routeName"
            )
            method = _require_string(
                item.get("method"), field="facadeRequests.data.requests[].method"
            )
            route_path = _require_string(
                item.get("routePath"), field="facadeRequests.data.requests[].routePath"
            )
            route = expected_routes.get(route_name)
            route_matches = (
                transport == "html"
                and route_name == _NATIVE_UI_DOCUMENT_ROUTE
                and method == "GET"
                and route_path == f"omnigent-ui/workflow-chat/{binding_id}"
            ) or (
                route is not None
                and transport == route.get("transport")
                and method in route.get("methods", [])
                and re.fullmatch(str(route.get("pathPattern") or ""), route_path)
                is not None
            )
            if (
                request_id in observed_ids
                or request_id not in request_id_set
                or transport not in _TRANSPORTS
                or not route_matches
                or item.get("bindingId") != binding_id
                or item.get("providerSessionId") != provider_session_id
                or item.get("authorized") is not True
                or item.get("serverResolvedTarget") is not True
            ):
                raise ConformanceContractError(
                    "workflow Chat facadeRequests contains an unauthorized request"
                )
            observed_ids.add(request_id)
            transports.add(transport)
            observed_route_names.add(route_name)
            if request_id == reconnect_id:
                reconnect_reauthorized = item.get("reauthorized") is True
        if (
            observed_ids != request_id_set
            or transports != set(_TRANSPORTS)
            or observed_route_names
            != set(expected_routes) | {_NATIVE_UI_DOCUMENT_ROUTE}
            or data.get("compatibilityProfile")
            != WORKFLOW_CHAT_COMPATIBILITY_PROFILE
            or reconnect_id not in request_id_set
            or not reconnect_reauthorized
        ):
            raise ConformanceContractError(
                "workflow Chat facadeRequests coverage or reconnect evidence is "
                "incomplete"
            )
        return

    if record_type == "resourceInventory":
        resources = data.get("resources")
        if not isinstance(resources, list) or not resources:
            raise ConformanceContractError(
                "workflow Chat resourceInventory requires observed resources"
            )
        resource_types: set[str] = set()
        for resource in resources:
            item = _require_mapping(
                resource, field="resourceInventory.data.resources[]"
            )
            _require_string(
                item.get("resourceId"),
                field="resourceInventory.data.resources[].resourceId",
            )
            resource_types.add(
                _require_string(
                    item.get("resourceType"),
                    field="resourceInventory.data.resources[].resourceType",
                )
            )
            if item.get("requestId") not in request_id_set:
                raise ConformanceContractError(
                    "workflow Chat resourceInventory is not request-correlated"
                )
        if not _NATIVE_CONTROL_KINDS.issubset(resource_types):
            raise ConformanceContractError(
                "workflow Chat resourceInventory is incomplete"
            )
        return

    if record_type == "mutationReceipts":
        receipts = data.get("receipts")
        if not isinstance(receipts, list) or not receipts:
            raise ConformanceContractError(
                "workflow Chat mutationReceipts requires durable receipts"
            )
        receipt_ids: set[str] = set()
        for receipt in receipts:
            item = _require_mapping(
                receipt, field="mutationReceipts.data.receipts[]"
            )
            request_id = item.get("requestId")
            if request_id not in request_id_set or request_id in receipt_ids:
                raise ConformanceContractError(
                    "workflow Chat mutation receipt is not request-correlated"
                )
            receipt_ids.add(str(request_id))
            for field in (
                "actor",
                "idempotencyKey",
                "expectedState",
                "outcome",
                "upstreamCorrelation",
                "auditRef",
            ):
                _require_string(
                    item.get(field),
                    field=f"mutationReceipts.data.receipts[].{field}",
                )
        if receipt_ids != request_id_set:
            raise ConformanceContractError(
                "workflow Chat mutation receipt coverage is incomplete"
            )
        return

    if record_type == "denialAudit":
        denials = data.get("denials")
        if not isinstance(denials, list) or not denials:
            raise ConformanceContractError(
                "workflow Chat denialAudit requires observed denials"
            )
        kinds: set[str] = set()
        denial_ids: set[str] = set()
        for denial in denials:
            item = _require_mapping(denial, field="denialAudit.data.denials[]")
            kinds.add(
                _require_string(
                    item.get("kind"), field="denialAudit.data.denials[].kind"
                )
            )
            if (
                item.get("requestId") not in request_id_set
                or item.get("requestId") in denial_ids
                or item.get("upstreamForwarded") is not False
            ):
                raise ConformanceContractError(
                    "workflow Chat denialAudit is not fail-closed"
                )
            denial_ids.add(str(item["requestId"]))
            _require_string(
                item.get("auditRef"), field="denialAudit.data.denials[].auditRef"
            )
        if not _DENIAL_KINDS.issubset(kinds):
            raise ConformanceContractError(
                "workflow Chat denialAudit coverage is incomplete"
            )
        return

    if record_type == "capabilitySnapshot":
        inputs = _require_mapping(
            data.get("inputs"), field="capabilitySnapshot.data.inputs"
        )
        expected_inputs = (
            "upstream",
            "agentProfile",
            "providerPolicy",
            "workflowState",
            "callerPermission",
        )
        if set(inputs) != set(expected_inputs):
            raise ConformanceContractError(
                "workflow Chat capabilitySnapshot inputs are incomplete"
            )
        typed_inputs: list[Mapping[str, Any]] = []
        for name in expected_inputs:
            values = _require_mapping(
                inputs[name], field=f"capabilitySnapshot.data.inputs.{name}"
            )
            if not values or any(
                not isinstance(value, bool) for value in values.values()
            ):
                raise ConformanceContractError(
                    "workflow Chat capabilitySnapshot inputs must be boolean maps"
                )
            typed_inputs.append(values)
        effective = _require_mapping(
            data.get("effective"), field="capabilitySnapshot.data.effective"
        )
        capability_names = set().union(*(set(value) for value in typed_inputs))
        computed = {
            name: all(values.get(name) is True for values in typed_inputs)
            for name in capability_names
        }
        digest_payload = {"inputs": inputs, "effective": computed}
        if (
            dict(effective) != computed
            or data.get("snapshotDigest") != _canonical_digest(digest_payload)
        ):
            raise ConformanceContractError(
                "workflow Chat capabilitySnapshot is not the effective intersection"
            )
        return

    if record_type == "scanAudit":
        attempts = data.get("attempts")
        if not isinstance(attempts, list) or not attempts:
            raise ConformanceContractError(
                "workflow Chat scanAudit requires observed scan attempts"
            )
        outcomes: set[str] = set()
        attempt_ids: set[str] = set()
        for attempt in attempts:
            item = _require_mapping(attempt, field="scanAudit.data.attempts[]")
            outcomes.add(
                _require_string(
                    item.get("outcome"), field="scanAudit.data.attempts[].outcome"
                )
            )
            if (
                item.get("requestId") not in request_id_set
                or item.get("requestId") in attempt_ids
                or item.get("forwarded") is not False
            ):
                raise ConformanceContractError(
                    "workflow Chat scanAudit did not fail closed"
                )
            attempt_ids.add(str(item["requestId"]))
            _require_string(
                item.get("auditRef"), field="scanAudit.data.attempts[].auditRef"
            )
        if not {"blocked", "enforcement_unavailable"}.issubset(outcomes):
            raise ConformanceContractError(
                "workflow Chat scanAudit coverage is incomplete"
            )
        return

    if record_type == "credentialBoundary":
        verified = set(
            _require_string_list(
                data.get("verifiedRequestIds"),
                field="credentialBoundary.data.verifiedRequestIds",
            )
        )
        if (
            verified != request_id_set
            or data.get("browserExposedCredentialNames") != []
            or data.get("forwardedMoonMindCredentialNames") != []
        ):
            raise ConformanceContractError(
                "workflow Chat credentialBoundary detected credential crossover"
            )
        _require_string(
            data.get("serverInjectedCredentialRef"),
            field="credentialBoundary.data.serverInjectedCredentialRef",
        )
        return

    if record_type == "terminalSnapshot":
        denied_ids = set(
            _require_string_list(
                data.get("deniedMutationRequestIds"),
                field="terminalSnapshot.data.deniedMutationRequestIds",
            )
        )
        if (
            data.get("state") not in _TERMINAL_STATES
            or data.get("readOnly") is not True
            or not denied_ids.issubset(request_id_set)
        ):
            raise ConformanceContractError(
                "workflow Chat terminalSnapshot is not read-only"
            )
        return

    if record_type == "capturedEvidence":
        artifacts = _require_evidence_items(
            data.get("artifacts"), field="capturedEvidence.data.artifacts"
        )
        refs = {item["ref"] for item in artifacts}
        if data.get("captureManifestRef") not in refs:
            raise ConformanceContractError(
                "workflow Chat capturedEvidence is not independently resolvable"
            )
        return

    if record_type == "continuationReceipt":
        creation = _require_mapping(
            data.get("destinationCreationReceipt"),
            field="continuationReceipt.data.destinationCreationReceipt",
        )
        relationship = _require_mapping(
            data.get("durableRelationship"),
            field="continuationReceipt.data.durableRelationship",
        )
        source_run_id = _require_string(
            creation.get("sourceRunId"),
            field="continuationReceipt.data.destinationCreationReceipt.sourceRunId",
        )
        destination = _require_string(
            creation.get("destinationWorkflowId"),
            field=(
                "continuationReceipt.data.destinationCreationReceipt."
                "destinationWorkflowId"
            ),
        )
        destination_run_id = _require_string(
            relationship.get("destinationRunId"),
            field="continuationReceipt.data.durableRelationship.destinationRunId",
        )
        relationship_identity = {
            "relationshipType": _LINKED_CONTINUATION_RELATIONSHIP,
            "sourceWorkflowId": workflow_id,
            "sourceRunId": source_run_id,
            "destinationWorkflowId": destination,
            "destinationRunId": destination_run_id,
        }
        if (
            creation.get("requestId") not in request_id_set
            or creation.get("created") is not True
            or creation.get("relationshipType")
            != _LINKED_CONTINUATION_RELATIONSHIP
            or creation.get("sourceWorkflowId") != workflow_id
            or destination == workflow_id
            or relationship.get("requestId") not in request_id_set
            or relationship.get("requestId") == creation.get("requestId")
            or relationship.get("direction") != "outbound"
            or any(
                relationship.get(field) != value
                for field, value in relationship_identity.items()
            )
            or relationship.get("relationshipDigest")
            != _canonical_digest(relationship_identity)
            or data.get("sourceStateBeforeSha256")
            != data.get("sourceStateAfterSha256")
        ):
            raise ConformanceContractError(
                "workflow Chat continuationReceipt does not prove linked continuation"
            )
        _require_sha256_ref(
            data.get("sourceStateBeforeSha256"),
            field="continuationReceipt.data.sourceStateBeforeSha256",
        )
        _require_string(data.get("idempotencyKey"), field="continuationReceipt.data.idempotencyKey")
        _timestamp(
            relationship.get("createdAt"),
            field="continuationReceipt.data.durableRelationship.createdAt",
        )
        return

    if record_type == "replaySnapshot":
        replay_refs = _require_string_list(
            data.get("artifactRefs"), field="replaySnapshot.data.artifactRefs"
        )
        if (
            data.get("hostUnavailable") is not True
            or data.get("replayedFromMoonMindArtifacts") is not True
            or any(
                urllib.parse.urlparse(ref).scheme not in {"", "https"}
                or (
                    not urllib.parse.urlparse(ref).scheme
                    and Path(ref).is_absolute()
                )
                for ref in replay_refs
            )
        ):
            raise ConformanceContractError(
                "workflow Chat replaySnapshot does not prove host-independent replay"
            )
        return


def validate_workflow_chat_source_records(
    sources: Mapping[str, Mapping[str, Any]],
    *,
    row_name: str,
    source_commit: str,
    images: Mapping[str, Any],
    generated_at: datetime,
    expected_correlation: Mapping[str, str] | None = None,
) -> tuple[dict[str, bool], dict[str, str]]:
    """Validate and correlate one row's production-owned source records.

    The returned assertions are derived from typed observations. Callers never
    trust an adapter-authored pass boolean as the semantic result.
    """

    required = REQUIRED_WORKFLOW_CHAT_SOURCE_RECORDS.get(row_name)
    if required is None or set(sources) != set(required):
        raise ConformanceContractError(
            f"workflow Chat source record coverage is incomplete: {row_name}"
        )
    row_correlation: dict[str, str] | None = None
    request_ids_by_type: dict[str, set[str]] = {}
    for record_type, source in sources.items():
        if (
            source.get("schemaVersion") != WORKFLOW_CHAT_SOURCE_RECORD_VERSION
            or source.get("recordType") != record_type
            or source.get("row") != row_name
            or source.get("sourceCommit") != source_commit
            or source.get("images") != images
            or source.get("observed") is not True
        ):
            raise ConformanceContractError(
                f"workflow Chat source record is invalid: {row_name}/{record_type}"
            )
        observed_at = _timestamp(
            source.get("observedAt"), field=f"{record_type}.observedAt"
        )
        if observed_at > generated_at or generated_at - observed_at > timedelta(days=1):
            raise ConformanceContractError(
                f"workflow Chat source record is stale: {row_name}/{record_type}"
            )
        raw_correlation = _require_mapping(
            source.get("correlation"), field=f"{record_type}.correlation"
        )
        if set(raw_correlation) != set(_CORRELATION_FIELDS):
            raise ConformanceContractError(
                f"workflow Chat source record correlation is incomplete: {record_type}"
            )
        correlation = {
            field: _require_string(
                raw_correlation[field], field=f"{record_type}.correlation.{field}"
            )
            for field in _CORRELATION_FIELDS
        }
        if row_correlation is None:
            row_correlation = correlation
        elif correlation != row_correlation:
            raise ConformanceContractError(
                f"workflow Chat source records are not correlated: {row_name}"
            )
        data = _require_mapping(source.get("data"), field=f"{record_type}.data")
        _validate_record_data(record_type, data, correlation=correlation)
        request_ids_by_type[record_type] = set(data["requestIds"])

    assert row_correlation is not None
    if expected_correlation is not None and any(
        row_correlation[field] != expected_correlation.get(field)
        for field in _GLOBAL_CORRELATION_FIELDS
    ):
        raise ConformanceContractError(
            "workflow Chat acceptance rows do not bind the same workflow session"
        )
    browser_request_ids = request_ids_by_type["browserTrace"]
    for record_type, request_ids in request_ids_by_type.items():
        if record_type != "browserTrace" and not request_ids.issubset(
            browser_request_ids
        ):
            raise ConformanceContractError(
                f"workflow Chat source record request correlation failed: {record_type}"
            )
    browser_events = {
        str(item["requestId"]): item
        for item in sources["browserTrace"]["data"]["networkEvents"]
    }
    expected_denial_statuses: dict[str, int] = {}
    denial_source = sources.get("denialAudit")
    if denial_source is not None:
        expected_denial_statuses.update(
            {
                str(item["requestId"]): 403
                for item in denial_source["data"]["denials"]
            }
        )
    scan_source = sources.get("scanAudit")
    if scan_source is not None:
        expected_denial_statuses.update(
            {
                str(item["requestId"]): (
                    503 if item["outcome"] == "enforcement_unavailable" else 403
                )
                for item in scan_source["data"]["attempts"]
            }
        )
    terminal_source = sources.get("terminalSnapshot")
    if terminal_source is not None:
        expected_denial_statuses.update(
            {
                str(request_id): 403
                for request_id in terminal_source["data"][
                    "deniedMutationRequestIds"
                ]
            }
        )
    for request_id, event in browser_events.items():
        status = event["responseStatus"]
        expected_denial = expected_denial_statuses.get(request_id)
        unexpected_denial = (
            expected_denial is not None and status != expected_denial
        )
        unsuccessful_positive = (
            expected_denial is None
            and status != 101
            and not 200 <= status < 300
        )
        if unexpected_denial or unsuccessful_positive:
            raise ConformanceContractError(
                "workflow Chat browserTrace contains an unexpected response status"
            )

    facade_source = sources.get("facadeRequests")
    mutation_source = sources.get("mutationReceipts")
    if facade_source is not None and mutation_source is not None:
        route_mutations = {
            str(item["name"]): item.get("mutation") is True
            for item in compatibility_map()["routes"]
        }
        facade_requests = facade_source["data"]["requests"]
        observed_mutation_ids = {
            str(item["requestId"])
            for item in facade_requests
            if route_mutations.get(str(item["routeName"]), False)
        }
        receipt_ids = {
            str(item["requestId"])
            for item in mutation_source["data"]["receipts"]
        }
        def _request_matches_browser_trace(item: Mapping[str, Any]) -> bool:
            request_id = str(item["requestId"])
            event = browser_events[request_id]
            observed_path = urllib.parse.urlsplit(str(event["path"])).path
            expected_path = (
                "/" + str(item["routePath"])
                if item["routeName"] == _NATIVE_UI_DOCUMENT_ROUTE
                else f"/api/workflow-chat-bindings/{row_correlation['chatBindingId']}"
                f"/omnigent/{item['routePath']}"
            )
            return (
                event["transport"] == item["transport"]
                and event["method"] == item["method"]
                and observed_path == expected_path
            )

        if (
            observed_mutation_ids != receipt_ids
            or set(mutation_source["data"]["requestIds"])
            != observed_mutation_ids
            or any(not _request_matches_browser_trace(item) for item in facade_requests)
        ):
            raise ConformanceContractError(
                "workflow Chat mutation receipt or transport coverage is incomplete"
            )

    credential_source = sources.get("credentialBoundary")
    if credential_source is not None and (
        set(credential_source["data"]["verifiedRequestIds"])
        != browser_request_ids
        or request_ids_by_type["credentialBoundary"] != browser_request_ids
    ):
        raise ConformanceContractError(
            "workflow Chat credentialBoundary does not cover the browser trace"
        )
    if row_name == "terminal-evidence-and-continuation":
        captured_refs = {
            str(item["ref"])
            for item in sources["capturedEvidence"]["data"]["artifacts"]
        }
        replay_refs = set(sources["replaySnapshot"]["data"]["artifactRefs"])
        if not replay_refs.issubset(captured_refs):
            raise ConformanceContractError(
                "workflow Chat replay evidence is not bound to captured evidence"
            )

    assertions = {name: True for name in REQUIRED_WORKFLOW_CHAT_ROWS[row_name]}
    return assertions, row_correlation


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
                    record_ref = str(record["ref"])
                    refs.append(record_ref)
                    source_record = _resolve_json(record_ref, evidence_root)
                    if source_record.get("recordType") == "capturedEvidence":
                        data = source_record.get("data")
                        artifacts = (
                            data.get("artifacts")
                            if isinstance(data, Mapping)
                            else None
                        )
                        if isinstance(artifacts, list):
                            for artifact in artifacts:
                                if isinstance(artifact, Mapping) and artifact.get(
                                    "ref"
                                ):
                                    refs.append(str(artifact["ref"]))
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
    trace_screenshot_digests: set[str] = set()
    global_correlation: dict[str, str] | None = None
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
            sources_by_type: dict[str, Mapping[str, Any]] = {}
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
                    or record_type in sources_by_type
                ):
                    raise ConformanceContractError(
                        f"workflow Chat source record is malformed: {row_name}"
                    )
                source = resolved.get(record_ref)
                manifest_digest = manifest_digests.get(record_ref, "")
                if (
                    not isinstance(source, Mapping)
                    or manifest_digest != record_digest
                ):
                    raise ConformanceContractError(
                        "workflow Chat source record is invalid: "
                        f"{row_name}/{record_type}"
                    )
                sources_by_type[record_type] = source
                used_refs.add(record_ref)
            derived_assertions, correlation = validate_workflow_chat_source_records(
                sources_by_type,
                row_name=row_name,
                source_commit=source_commit,
                images=images,
                generated_at=generated_at,
                expected_correlation=global_correlation,
            )
            browser_data = sources_by_type["browserTrace"].get("data")
            if isinstance(browser_data, Mapping):
                trace_screenshot_digests.add(
                    _require_sha256_ref(
                        browser_data.get("screenshotSha256"),
                        field="browserTrace.data.screenshotSha256",
                    ).removeprefix("sha256:")
                )
            captured_source = sources_by_type.get("capturedEvidence")
            if captured_source is not None:
                captured_data = _require_mapping(
                    captured_source.get("data"), field="capturedEvidence.data"
                )
                for artifact in _require_evidence_items(
                    captured_data.get("artifacts"),
                    field="capturedEvidence.data.artifacts",
                ):
                    if manifest_digests.get(artifact["ref"]) != artifact["sha256"]:
                        raise ConformanceContractError(
                            "workflow Chat capturedEvidence artifact is unresolved"
                        )
                    used_refs.add(artifact["ref"])
            if any(
                evidence_assertions.get(name) is not value
                or assertions.get(name) is not value
                for name, value in derived_assertions.items()
            ):
                raise ConformanceContractError(
                    "workflow Chat row assertions do not match typed source evidence: "
                    f"{row_name}"
                )
            if global_correlation is None:
                global_correlation = correlation

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
    scanned_screenshot_digests: set[str] = set()
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
            if channel == "screenshots":
                scanned_screenshot_digests.add(digest)

    if not trace_screenshot_digests.issubset(scanned_screenshot_digests):
        raise ConformanceContractError(
            "workflow Chat browserTrace screenshot is not scanned evidence"
        )

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
    "validate_workflow_chat_source_records",
    "validate_workflow_chat_acceptance_manifest",
]
