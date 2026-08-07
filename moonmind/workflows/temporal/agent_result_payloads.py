"""Compact agent results at Temporal workflow-history boundaries."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from moonmind.schemas.temporal_payload_policy import MAX_TEMPORAL_METADATA_BYTES


def _compact_workflow_text(value: Any, *, max_chars: int = 700) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if len(text) > max_chars:
        return text[: max_chars - 3].rstrip() + "..."
    return text


def _compact_workflow_scalar(value: Any) -> Any:
    if isinstance(value, str):
        return _compact_workflow_text(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return None


def _compact_workflow_text_list(
    value: Any,
    *,
    max_items: int = 20,
    max_chars: int = 400,
) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    compact: list[str] = []
    for item in value:
        text = _compact_workflow_text(item, max_chars=max_chars)
        if text:
            compact.append(text)
        if len(compact) >= max_items:
            break
    return compact


def _compact_workflow_text_mapping(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    compact: dict[str, str] = {}
    for raw_key, raw_value in value.items():
        key = _compact_workflow_text(str(raw_key), max_chars=120)
        text = _compact_workflow_text(raw_value, max_chars=400)
        if key and text:
            compact[key] = text
        if len(compact) >= 20:
            break
    return compact


def _compact_moonspec_verify_for_workflow_history(
    value: Mapping[str, Any],
) -> dict[str, Any]:
    compact: dict[str, Any] = {}
    scalar_keys = (
        "schemaVersion",
        "verdict",
        "gateVerdict",
        "gate_verdict",
        "moonSpecVerdict",
        "moonspecVerdict",
        "verificationVerdict",
        "verification_verdict",
        "confidence",
        "recommendedNextAction",
        "recommended_next_action",
        "targetLogicalStepId",
        "target_logical_step_id",
        "workspacePolicyRecommendation",
        "workspace_policy_recommendation",
        "recoverableInCurrentRuntime",
        "recoverable_in_current_runtime",
        "invalid",
        "degraded",
        "remainingWorkRef",
        "remaining_work_ref",
        "diagnosticsRef",
        "diagnostics_ref",
        "verificationReportRef",
        "verification_report_ref",
        "reportRef",
        "report_ref",
        "gateResultRef",
        "gate_result_ref",
        "artifactRef",
        "artifact_ref",
    )
    for key in scalar_keys:
        field_value = _compact_workflow_scalar(value.get(key))
        if field_value is not None:
            compact[key] = field_value

    for key in ("feedback", "summary", "message", "downgradeReason"):
        text = _compact_workflow_text(value.get(key), max_chars=900)
        if text:
            compact[key] = text

    for key in ("invalidatedRefs", "invalidated_refs"):
        refs = _compact_workflow_text_list(value.get(key))
        if refs:
            compact[key] = refs
            break
    for key in ("blockingEvidenceRefs", "blocking_evidence_refs"):
        refs = _compact_workflow_text_list(value.get(key))
        if refs:
            compact[key] = refs
            break

    validated_refs = _compact_workflow_text_mapping(
        value.get("validatedRefs") or value.get("validated_refs")
    )
    if validated_refs:
        compact["validatedRefs"] = validated_refs

    contract_violations = _compact_workflow_text_list(
        value.get("contractViolations") or value.get("contract_violations"),
        max_items=10,
        max_chars=700,
    )
    if contract_violations:
        compact["contractViolations"] = contract_violations

    return compact


def compact_agent_run_result_payload_for_workflow_history(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Project known large result metadata to compact workflow-safe evidence."""

    compact_payload = dict(payload)
    metadata = compact_payload.get("metadata")
    if not isinstance(metadata, Mapping):
        return compact_payload

    compact_metadata = dict(metadata)
    for key in (
        "moonSpecVerify",
        "moonspecVerify",
        "moonspec_verify",
        "verificationResult",
        "verification_result",
    ):
        value = compact_metadata.get(key)
        if isinstance(value, Mapping):
            compact_metadata[key] = _compact_moonspec_verify_for_workflow_history(
                value
            )

    queued_children = compact_metadata.get("queuedChildren")
    if isinstance(queued_children, (list, tuple)):
        compact_children: list[dict[str, str]] = []
        for child in queued_children:
            if not isinstance(child, Mapping):
                continue
            compact_child: dict[str, str] = {}
            workflow_id = _compact_workflow_text(
                child.get("workflowId"), max_chars=400
            )
            execution_id = _compact_workflow_text(
                child.get("executionId"), max_chars=400
            )
            if workflow_id:
                compact_child["workflowId"] = workflow_id
            if execution_id and execution_id != workflow_id:
                compact_child["executionId"] = execution_id
            reference = _compact_workflow_text(
                child.get("ref") or child.get("targetRef"), max_chars=400
            )
            if reference:
                compact_child["ref"] = reference
            if compact_child:
                compact_children.append(compact_child)
        compact_metadata["queuedChildren"] = compact_children
    compact_payload["metadata"] = compact_metadata
    return compact_payload


def _serialized_metadata_size(metadata: Mapping[str, Any]) -> int:
    return len(
        json.dumps(
            metadata,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    )


_INLINE_METADATA_BODIES = (
    "lastAssistantText",
    "assistantText",
    "operator_summary",
)

_ESSENTIAL_PUBLISHED_METADATA_KEYS = frozenset(
    {
        "agentId",
        "agentKind",
        "agentRunId",
        "childRunId",
        "childWorkflowId",
        "failureCode",
        "mergeAutomationDisposition",
        "providerErrorCode",
        "providerFailure",
        "queuedChildCount",
        "queuedChildren",
        "status",
        "terminalContractAuthority",
        "terminalContractEvidencePath",
        "terminalContractEvidenceRef",
        "terminalContractExecutionRef",
        "terminalContractId",
        "terminalContractMissingEvidence",
        "terminalContractOutcome",
        "terminalContractRecoveryOutcome",
        "terminalContractSatisfied",
    }
)

_PUBLISHED_ARTIFACT_REF_KEYS = frozenset(
    {
        "inputInstructionsRef",
        "inputSkillSnapshotRef",
        "outputAgentResultRef",
        "outputSummaryRef",
        "primaryReportRef",
        "publishEvidence",
        "publishEvidenceRef",
        "reportBundle",
    }
)


def _published_metadata_key_is_durable(key: str) -> bool:
    normalized = key.lower()
    return (
        key in _ESSENTIAL_PUBLISHED_METADATA_KEYS
        or key in _PUBLISHED_ARTIFACT_REF_KEYS
        or normalized.endswith("ref")
        or normalized.endswith("refs")
    )


def compact_published_agent_run_result_payload(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Bound an artifact-backed result after publication adds metadata refs.

    ``outputAgentResultRef`` points to the full pre-enrichment result. Once that
    artifact exists, inline prose and other non-authoritative annotations may be
    omitted from workflow history while terminal evidence and durable refs stay
    directly linkable.
    """

    compact_payload = compact_agent_run_result_payload_for_workflow_history(payload)
    metadata = compact_payload.get("metadata")
    if not isinstance(metadata, Mapping):
        return compact_payload
    compact_metadata = dict(metadata)
    compact_metadata["workflowHistoryMetadataCompacted"] = True
    for key in _INLINE_METADATA_BODIES:
        if _serialized_metadata_size(compact_metadata) <= MAX_TEMPORAL_METADATA_BYTES:
            break
        compact_metadata.pop(key, None)

    if _serialized_metadata_size(compact_metadata) > MAX_TEMPORAL_METADATA_BYTES:
        removable = sorted(
            (
                (_serialized_metadata_size({key: value}), key)
                for key, value in compact_metadata.items()
                if not _published_metadata_key_is_durable(key)
                and key != "workflowHistoryMetadataCompacted"
            ),
            reverse=True,
        )
        for _size, key in removable:
            compact_metadata.pop(key, None)
            if (
                _serialized_metadata_size(compact_metadata)
                <= MAX_TEMPORAL_METADATA_BYTES
            ):
                break

    if _serialized_metadata_size(compact_metadata) > MAX_TEMPORAL_METADATA_BYTES:
        compact_metadata = {
            key: value
            for key, value in compact_metadata.items()
            if key in _ESSENTIAL_PUBLISHED_METADATA_KEYS
            or key in _PUBLISHED_ARTIFACT_REF_KEYS
        }
        compact_metadata["workflowHistoryMetadataCompacted"] = True

    if _serialized_metadata_size(compact_metadata) > MAX_TEMPORAL_METADATA_BYTES:
        compact_metadata.pop("queuedChildren", None)
        compact_metadata.pop("providerFailure", None)
        compact_metadata.pop("terminalContractMissingEvidence", None)

    compact_payload["metadata"] = compact_metadata
    return compact_payload
