"""Compact agent results at Temporal workflow-history boundaries."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any

from moonmind.schemas.temporal_payload_policy import MAX_TEMPORAL_METADATA_BYTES
from moonmind.workflows.skills.acceptance_contract import acceptance_evidence
from moonmind.workflows.skills.approval_policy import step_gate_contract_violations

_VERIFY_GATE_METADATA_KEYS = (
    "moonSpecVerify",
    "moonspecVerify",
    "moonspec_verify",
    "verificationResult",
    "verification_result",
)


_ACCEPTANCE_PROJECTION_FIELDS = (
    "subject",
    "scope",
    "completionTarget",
    "freshness",
    "evidenceSetDigest",
)


def build_acceptance_projection(
    gate: Mapping[str, Any],
    *,
    report_ref: str,
    report_digest: str,
) -> dict[str, Any]:
    """Project a publisher-validated strict report after its immutable write.

    This descriptor deliberately is not acceptance/v1. Consumers must read the
    complete report with the owning workflow's authority and verify its bytes
    before interpreting its proof. Requirement IDs and bindings are never cut.
    Source validation remains owned by the artifact publisher.
    """
    refs = gate.get("validatedRefs")
    binding = refs.get("acceptance") if isinstance(refs, Mapping) else None
    if not isinstance(binding, Mapping):
        binding = gate
    if (
        not isinstance(report_ref, str)
        or not report_ref.strip()
        or not isinstance(report_digest, str)
        or re.fullmatch(r"sha256:[0-9a-f]{64}", report_digest) is None
        or binding.get("schemaVersion") != "acceptance/v1"
        or not isinstance(binding.get("verificationMap"), list)
        or not binding["verificationMap"]
        or any(key not in binding for key in _ACCEPTANCE_PROJECTION_FIELDS)
        or step_gate_contract_violations(gate, require_acceptance=True)
    ):
        raise ValueError("Only a validated strict acceptance report can be projected")
    return {
        "schemaVersion": "acceptance-projection/v1",
        "reportRef": report_ref,
        "reportDigest": report_digest,
        **{key: copy.deepcopy(binding[key]) for key in _ACCEPTANCE_PROJECTION_FIELDS},
    }


def hydrate_acceptance_projection(
    gate: Mapping[str, Any],
    *,
    report_bytes: bytes,
    report_ref: str,
) -> dict[str, Any]:
    """Verify a strict descriptor against bytes read under workflow authority."""
    refs = gate.get("validatedRefs")
    projection = refs.get("acceptanceProjection") if isinstance(refs, Mapping) else None
    if not isinstance(projection, Mapping):
        raise ValueError("A strict acceptance projection is required")
    expected_ref = projection.get("reportRef")
    if (
        not isinstance(expected_ref, str)
        or not isinstance(report_ref, str)
        or expected_ref.removeprefix("artifact://")
        != report_ref.removeprefix("artifact://")
        or not isinstance(report_bytes, bytes)
        or gate.get("invalid")
        or gate.get("degraded")
        or gate.get("contractViolations")
        or gate.get("contract_violations")
    ):
        raise ValueError(
            "Acceptance projection is invalid or references another report"
        )
    digest = "sha256:" + hashlib.sha256(report_bytes).hexdigest()
    if projection.get("reportDigest") != digest:
        raise ValueError("Acceptance report bytes do not match the published digest")
    try:
        report = json.loads(report_bytes.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValueError("Acceptance report bytes are not JSON") from exc
    if not isinstance(report, Mapping):
        raise ValueError("Acceptance report must be an object")
    expected = build_acceptance_projection(
        report, report_ref=expected_ref, report_digest=digest
    )
    if dict(projection) != expected:
        raise ValueError(
            "Acceptance report binding differs from its published projection"
        )
    for key in ("verdict", "recommendedNextAction"):
        if gate.get(key) != report.get(key):
            raise ValueError("Acceptance report decision differs from its projection")
    hydrated = dict(report)
    hydrated["validatedRefs"] = {
        **dict(report.get("validatedRefs") or {}),
        "acceptanceProjection": copy.deepcopy(dict(projection)),
    }
    hydrated["acceptanceScopeContract"] = "source-bound/v1"
    # Durable wrapper/source references are publisher-owned and may be added
    # after the immutable raw report was written.
    for key in (
        "gateResultRef",
        "sourceMoonSpecVerifyArtifactRef",
        "moonSpecVerifyArtifactRef",
        "remediationVerificationRef",
        "remainingWorkRef",
    ):
        if gate.get(key):
            hydrated[key] = gate[key]
    hydrated.setdefault("gateResultRef", expected_ref)
    return hydrated


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


def compact_moonspec_verify_metadata(
    value: Mapping[str, Any],
    *,
    acceptance_projection: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    compact: dict[str, Any] = {}
    scalar_keys = (
        "schemaVersion",
        "acceptanceScopeContract",
        "sourceMoonSpecVerifyArtifactRef",
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
        "rawRecommendedNextAction",
        "raw_recommended_next_action",
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

    validated_refs: dict[str, Any] = _compact_workflow_text_mapping(
        value.get("validatedRefs") or value.get("validated_refs")
    )
    # Both initial publication and size-triggered result compaction use this
    # projection. Nested control evidence is not a string-valued annotation.
    # Keep its exact identity/scope/freshness and reference the full published
    # report for detailed evidence; never reinterpret the Skill's verdict.
    raw_refs = value.get("validatedRefs")
    if acceptance_projection is None and isinstance(raw_refs, Mapping):
        existing = raw_refs.get("acceptanceProjection")
        if isinstance(existing, Mapping):
            acceptance_projection = existing
    acceptance = acceptance_evidence(value) if acceptance_projection is None else None
    if acceptance_projection is not None:
        validated_refs["acceptanceProjection"] = copy.deepcopy(
            dict(acceptance_projection)
        )
        compact["acceptanceScopeContract"] = "source-bound/v1"
    elif (
        acceptance is not None
        and value.get("acceptanceScopeContract") != "source-bound/v1"
    ):
        projected = acceptance.model_dump(mode="json", by_alias=True, exclude_none=True)
        evidence_ref = value.get("gateResultRef")
        if (
            not projected.get("evidenceSetDigest")
            and isinstance(evidence_ref, str)
            and evidence_ref.strip()
        ):
            for row in projected["evidence"]:
                row["evidenceRefs"] = [evidence_ref]
        validated_refs["acceptance"] = projected
    if validated_refs:
        compact["validatedRefs"] = validated_refs

    contract_violations = _compact_workflow_text_list(
        value.get("contractViolations") or value.get("contract_violations"),
        max_items=10,
        max_chars=700,
    )
    if contract_violations:
        compact["contractViolations"] = contract_violations

    # MoonLadderStudios/MoonMind#4491 (P1): the production native-verifier
    # path compacts the gate before the parent workflow constructs the
    # contract-repair input. Dropping ``issues`` here starves both the parsed
    # branch and the raw-source fallback, letting a corrected report approve
    # without accounting for known gaps. Preserve a bounded projection (the
    # parser's dict-only ceiling is 20 items) with truncated text so repair
    # keeps the evidence it must address. The full report remains behind
    # gateResultRef/artifact refs; this never authorizes advancement.
    issues_raw = value.get("issues")
    if isinstance(issues_raw, (list, tuple)):
        compacted_issues: list[dict[str, str]] = []
        for issue in issues_raw[:20]:
            if not isinstance(issue, Mapping):
                continue
            compacted_issue: dict[str, str] = {}
            severity = _compact_workflow_text(issue.get("severity"), max_chars=40)
            if severity:
                compacted_issue["severity"] = severity
            description = _compact_workflow_text(
                issue.get("description"), max_chars=700
            )
            if description:
                compacted_issue["description"] = description
            evidence = _compact_workflow_text(issue.get("evidence"), max_chars=700)
            if evidence:
                compacted_issue["evidence"] = evidence
            if compacted_issue:
                compacted_issues.append(compacted_issue)
            if len(compacted_issues) >= 20:
                break
        if compacted_issues:
            compact["issues"] = compacted_issues

    return compact


def _compact_omnigent_checkpoint_capture_for_workflow_history(
    value: Mapping[str, Any],
) -> dict[str, Any]:
    """Keep referenced egress evidence out of replay-sensitive metadata.

    Omnigent launch evidence is durably stored behind ``egressEvidenceRef``.
    Older histories may also contain the full inline attestation, including the
    compiled launch-policy boundary. Once the reference exists, retaining that
    duplicate object in an ``AgentRunResult`` only consumes workflow-history
    budget and can prevent a terminal result from being decoded on replay.
    """

    compact = dict(value)
    egress_evidence_ref = compact.get("egressEvidenceRef")
    if isinstance(egress_evidence_ref, str) and egress_evidence_ref.strip():
        compact.pop("egressAttestation", None)
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
    for key in _VERIFY_GATE_METADATA_KEYS:
        value = compact_metadata.get(key)
        if isinstance(value, Mapping):
            compact_metadata[key] = compact_moonspec_verify_metadata(value)

    queued_children = compact_metadata.get("queuedChildren")
    if isinstance(queued_children, (list, tuple)):
        compact_children: list[dict[str, str]] = []
        for child in queued_children:
            if not isinstance(child, Mapping):
                continue
            compact_child: dict[str, str] = {}
            workflow_id = _compact_workflow_text(child.get("workflowId"), max_chars=400)
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

    omnigent_capture = compact_metadata.get("omnigentCheckpointCapture")
    if isinstance(omnigent_capture, Mapping):
        compact_metadata["omnigentCheckpointCapture"] = (
            _compact_omnigent_checkpoint_capture_for_workflow_history(omnigent_capture)
        )
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
        *_VERIFY_GATE_METADATA_KEYS,
        "admissionRecovery",
        "agentId",
        "agentKind",
        "agentRunId",
        "childRunId",
        "childWorkflowId",
        "failureCode",
        "mergeAutomationDisposition",
        "prResolverTerminalVerdictApplied",
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
