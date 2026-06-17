"""Step Execution conformance fixtures and aggregate runner for MM-820."""

from __future__ import annotations

import argparse
from collections.abc import Iterable, Mapping
from copy import deepcopy
from datetime import UTC, datetime
import json
import subprocess
import sys
from typing import Any

from pydantic import ValidationError

from moonmind.schemas.temporal_models import (
    STEP_EXECUTION_CHECKPOINT_CONTENT_TYPE,
    STEP_EXECUTION_MANIFEST_CONTENT_TYPE,
    StepExecutionCheckpointModel,
    StepExecutionIdentityModel,
    StepExecutionManifestModel,
    build_step_execution_id,
)
from moonmind.workflows.temporal.step_checkpoints import (
    build_step_checkpoint_payload,
    validate_step_checkpoint_payload,
)
from moonmind.workflows.temporal.step_executions import (
    memory_side_effect_summary,
    validate_step_execution_manifest_payload,
)

STEP_EXECUTION_CONFORMANCE_SUITE_ID = "step-execution-conformance"

REQUIRED_CONFORMANCE_FAMILIES = (
    "manifest",
    "checkpoint",
    "gate",
    "terminology",
    "api_contract",
    "golden",
    "replay",
    "degraded_input",
)

REQUIRED_TRACEABILITY_IDS = (
    "FR-001",
    "FR-002",
    "FR-003",
    "FR-004",
    "FR-005",
    "FR-006",
    "FR-007",
    "SC-001",
    "SC-002",
    "SC-003",
    "SC-004",
    "SC-005",
    "DESIGN-REQ-002",
    "DESIGN-REQ-003",
    "DESIGN-REQ-006",
    "DESIGN-REQ-010",
    "DESIGN-REQ-012",
    "DESIGN-REQ-021",
    "DESIGN-REQ-024",
    "FR-018",
    "FR-019",
    "SC-006",
    "DESIGN-REQ-009",
)

_VALID_GATE_VERDICTS = {
    "FULLY_IMPLEMENTED": "valid",
    "ADDITIONAL_WORK_NEEDED": "valid",
    "NO_DETERMINATION": "valid",
    "BLOCKED": "valid",
    "FAILED_UNRECOVERABLE": "valid",
}
_DEGRADED_GATE_VERDICTS = {"PARTIAL", "PASS", "FAIL"}


def _identity() -> StepExecutionIdentityModel:
    return StepExecutionIdentityModel(
        workflowId="workflow-1",
        runId="run-1",
        logicalStepId="implement-story",
        executionOrdinal=2,
    )


def _now() -> datetime:
    return datetime(2026, 6, 12, 7, 0, tzinfo=UTC)


def _bounded_message(text: str) -> str:
    return str(text).replace("\n", " ")[:500]


def _decision(
    *,
    fixture_id: str,
    category: str,
    contract_surface: str,
    decision: str,
    expected: str = "valid",
    failure_code: str | None,
    message: str,
    traceability: Iterable[str],
) -> dict[str, Any]:
    return {
        "fixtureId": fixture_id,
        "category": category,
        "contractSurface": contract_surface,
        "valid": decision == "valid",
        "decision": decision,
        "expected": expected,
        "failureCode": failure_code,
        "message": _bounded_message(message),
        "traceability": list(traceability),
    }


def _valid_manifest_payload(**overrides: Any) -> dict[str, Any]:
    identity = _identity()
    payload = StepExecutionManifestModel(
        stepExecutionId=build_step_execution_id(identity),
        workflowId=identity.workflow_id,
        runId=identity.run_id,
        logicalStepId=identity.logical_step_id,
        executionOrdinal=identity.execution_ordinal,
        reason="initial_execution",
        status="succeeded",
        startedAt=_now(),
        updatedAt=_now(),
        input={"preparedInputRefs": ["artifact://input"]},
        context={"contextBundleRef": "artifact://context"},
        workspace={
            "policy": "continue_from_previous_execution",
            "checkpointBeforeRef": "artifact://checkpoint/before",
            "checkpointAfterRef": "artifact://checkpoint/after",
        },
        execution={"childWorkflowId": "child-1"},
        outputs={"summaryRef": "artifact://summary", "diffRef": "artifact://diff"},
        checks=[{"kind": "unit", "status": "passed"}],
        sideEffects={"recordsRef": "artifact://side-effects"},
        dependencyEffects={"invalidatedLogicalStepIds": []},
        budget={"attemptLimit": 3},
    ).model_dump(by_alias=True, mode="json")
    payload.update(overrides)
    return payload


def _valid_checkpoint_payload(**overrides: Any) -> dict[str, Any]:
    payload = build_step_checkpoint_payload(
        identity=_identity(),
        boundary="before_execution",
        task_input_snapshot_ref="artifact://input",
        plan_digest="sha256:plan",
        workspace={
            "kind": "git_patch",
            "baseCommit": "abc123",
            "patchRef": "artifact://patch",
            "manifestRef": "artifact://manifest",
        },
        step_outputs={"summaryRef": "artifact://summary"},
        created_at=_now(),
    )
    payload.update(overrides)
    return payload


def _classify_manifest_payload(
    fixture_id: str,
    payload: Mapping[str, Any],
    *,
    category: str = "golden",
    expected: str = "valid",
    traceability: Iterable[str],
) -> dict[str, Any]:
    result = validate_step_execution_manifest_payload(
        dict(payload), manifest_artifact_ref=f"artifact://{fixture_id}"
    )
    decision = "valid" if result["valid"] else "invalid"
    return _decision(
        fixture_id=fixture_id,
        category=category,
        contract_surface="manifest",
        decision=decision,
        expected=expected,
        failure_code=None if result["valid"] else str(result["failureCode"]),
        message="manifest fixture passed" if result["valid"] else str(result["message"]),
        traceability=traceability,
    )


def _classify_checkpoint_payload(
    fixture_id: str,
    payload: Mapping[str, Any],
    *,
    traceability: Iterable[str],
    category: str = "golden",
    expected: str = "valid",
    workspace_policy: str = "apply_previous_execution_diff_to_clean_baseline",
) -> dict[str, Any]:
    result = validate_step_checkpoint_payload(
        dict(payload),
        expected_source=_identity(),
        expected_task_input_snapshot_ref="artifact://input",
        expected_plan_digest="sha256:plan",
        workspace_policy=workspace_policy,
        checkpoint_ref=f"artifact://{fixture_id}",
    )
    decision = "valid" if result.valid else "invalid"
    return _decision(
        fixture_id=fixture_id,
        category=category,
        contract_surface="checkpoint",
        decision=decision,
        expected=expected,
        failure_code=None if result.valid else str(result.failure_code),
        message="checkpoint fixture passed" if result.valid else result.message,
        traceability=traceability,
    )


def classify_gate_verdict(
    fixture_id: str,
    raw_verdict: Any,
    category: str = "gate",
    expected: str = "valid",
) -> dict[str, Any]:
    verdict = str(raw_verdict or "").strip().upper()
    if verdict in _VALID_GATE_VERDICTS:
        return _decision(
            fixture_id=fixture_id,
            category=category,
            contract_surface="gate",
            decision="valid",
            expected=expected,
            failure_code=None,
            message=f"gate verdict {verdict} is structured",
            traceability=("FR-003", "FR-005", "DESIGN-REQ-012"),
        )
    if not verdict:
        failure_code = "blank_gate_verdict"
    elif verdict in _DEGRADED_GATE_VERDICTS:
        failure_code = "legacy_gate_verdict"
    else:
        failure_code = "unknown_gate_verdict"
    return _decision(
        fixture_id=fixture_id,
        category=category,
        contract_surface="gate",
        decision="degraded",
        expected=expected,
        failure_code=failure_code,
        message=f"gate verdict classified as {failure_code}",
        traceability=("FR-005", "SC-004", "DESIGN-REQ-012", "DESIGN-REQ-024"),
    )


def classify_legacy_checkpoint_only_ledger_row(
    row: Mapping[str, Any],
    category: str = "replay",
    expected: str = "degraded",
) -> dict[str, Any]:
    checkpoint_ref = str(
        row.get("stepCheckpointRef")
        or row.get("stateCheckpointRef")
        or row.get("checkpointRef")
        or ""
    ).strip()
    has_current_manifest_ref = bool(
        str(row.get("latestStepExecutionManifestRef") or "").strip()
    )
    if checkpoint_ref and not has_current_manifest_ref:
        return _decision(
            fixture_id="legacy-checkpoint-only-ledger-row",
            category=category,
            contract_surface="ledger",
            decision="degraded",
            expected=expected,
            failure_code="legacy_checkpoint_only_ledger_row",
            message="legacy ledger row has checkpoint refs but no current manifest ref",
            traceability=("FR-003", "FR-005", "SC-004", "DESIGN-REQ-024"),
        )
    return _decision(
        fixture_id="legacy-checkpoint-only-ledger-row",
        category=category,
        contract_surface="ledger",
        decision="valid",
        expected=expected,
        failure_code=None,
        message="ledger row contains current step execution evidence",
        traceability=("FR-003", "FR-005", "DESIGN-REQ-024"),
    )


def _forbidden_value(payload_class: str) -> Any:
    values: dict[str, Any] = {
        "raw_stdout": "raw stdout",
        "raw_stderr": "raw stderr",
        "raw_diff": "diff --git a/file b/file",
        "raw_logs": "log line 1\nlog line 2",
        "raw_provider_payload": {"model": "provider payload", "tokens": 123},
        "credentials": "credential value",
        "verification_report": "verification report body",
        "oversized_inline_evidence": "x" * 1200,
    }
    return values[payload_class]


def _forbidden_manifest_payload(payload_class: str) -> dict[str, Any]:
    key_by_class = {
        "raw_stdout": "stdout",
        "raw_stderr": "stderr",
        "raw_diff": "diff",
        "raw_logs": "logs",
        "raw_provider_payload": "providerOutput",
        "credentials": "credentials",
        "verification_report": "verificationReport",
        "oversized_inline_evidence": "inlineEvidence",
    }
    payload = _valid_manifest_payload()
    payload["outputs"] = {
        **dict(payload["outputs"]),
        key_by_class[payload_class]: _forbidden_value(payload_class),
    }
    return payload


def _forbidden_checkpoint_payload(payload_class: str) -> dict[str, Any]:
    key_by_class = {
        "raw_stdout": "stdout",
        "raw_stderr": "stderr",
        "raw_diff": "diff",
        "raw_logs": "logs",
        "raw_provider_payload": "providerOutput",
        "credentials": "credentials",
        "verification_report": "verificationReport",
        "oversized_inline_evidence": "inlineEvidence",
    }
    payload = _valid_checkpoint_payload()
    payload["stepOutputs"] = {
        **dict(payload["stepOutputs"]),
        key_by_class[payload_class]: _forbidden_value(payload_class),
    }
    return payload


def _classify_forbidden_fixture(
    *,
    contract_surface: str,
    payload_class: str,
) -> dict[str, Any]:
    fixture_id = f"{contract_surface}-{payload_class}"
    payload = (
        _forbidden_manifest_payload(payload_class)
        if contract_surface == "manifest"
        else _forbidden_checkpoint_payload(payload_class)
    )
    try:
        if contract_surface == "manifest":
            StepExecutionManifestModel.model_validate(payload)
        else:
            StepExecutionCheckpointModel.model_validate(payload)
    except ValidationError:
        decision = _decision(
            fixture_id=fixture_id,
            category="forbidden_inline_evidence",
            contract_surface=contract_surface,
            decision="invalid",
            expected="invalid",
            failure_code=f"forbidden_{payload_class}",
            message=f"forbidden inline evidence class rejected: {payload_class}",
            traceability=(
                "FR-002",
                "SC-002",
                "DESIGN-REQ-002",
                "DESIGN-REQ-006",
                "DESIGN-REQ-010",
            ),
        )
    else:
        decision = _decision(
            fixture_id=fixture_id,
            category="forbidden_inline_evidence",
            contract_surface=contract_surface,
            decision="valid",
            expected="invalid",
            failure_code=None,
            message=f"forbidden inline evidence class accepted: {payload_class}",
            traceability=("FR-002", "SC-002"),
        )
    return {
        "fixtureId": fixture_id,
        "payloadClass": payload_class,
        "contractSurface": contract_surface,
        "failureCode": f"forbidden_{payload_class}",
        "decision": decision,
    }


FORBIDDEN_INLINE_EVIDENCE_FIXTURES = tuple(
    _classify_forbidden_fixture(contract_surface=surface, payload_class=payload_class)
    for surface in ("manifest", "checkpoint")
    for payload_class in (
        "raw_stdout",
        "raw_stderr",
        "raw_diff",
        "raw_logs",
        "raw_provider_payload",
        "credentials",
        "verification_report",
        "oversized_inline_evidence",
    )
)


def golden_fixture_catalog() -> list[dict[str, Any]]:
    failed_manifest = _valid_manifest_payload(
        reason="quality_gate_failed",
        status="failed",
        terminalDisposition="retryable",
    )
    recovery_manifest = _valid_manifest_payload(
        reason="recover_from_failed_step",
        lineage={
            "sourceWorkflowId": "workflow-0",
            "sourceRunId": "run-0",
            "sourceLogicalStepId": "implement-story",
            "sourceExecutionOrdinal": 1,
            "relationship": "recover_from_failed_step",
        },
    )
    degraded_checkpoint = _valid_checkpoint_payload(boundary="after_future_gate")
    source = _identity().model_dump(by_alias=True)
    memory_failed = _valid_manifest_payload(
        status="failed",
        terminalDisposition="retryable",
        sideEffects={
            "memory": [
                memory_side_effect_summary(
                    state="proposed",
                    target="repo://AGENTS.md",
                    reason="terminal_disposition_not_accepted",
                    proposal_ref="artifact://memory/proposal-failed",
                    decision_ref="artifact://memory/decision-blocked",
                    source=source,
                )
            ]
        },
    )
    memory_run_context = _valid_manifest_payload(
        terminalDisposition="accepted",
        sideEffects={
            "memory": [
                memory_side_effect_summary(
                    state="accepted_for_run_context",
                    target="memory://run",
                    reason="policy_approved_for_later_attempts",
                    proposal_ref="artifact://memory/proposal-1",
                    decision_ref="artifact://memory/decision-1",
                    application_result_ref="artifact://memory/application-1",
                    source=source,
                )
            ]
        },
    )
    memory_blocked_repo = _valid_manifest_payload(
        status="failed",
        terminalDisposition="retryable",
        sideEffects={
            "memory": [
                memory_side_effect_summary(
                    state="proposed",
                    target="repo://AGENTS.md",
                    reason="terminal_disposition_not_accepted",
                    proposal_ref="artifact://memory/proposal-2",
                    decision_ref="artifact://memory/decision-2",
                    source=source,
                )
            ]
        },
    )
    memory_superseded = _valid_manifest_payload(
        terminalDisposition="accepted",
        sideEffects={
            "memory": [
                memory_side_effect_summary(
                    state="superseded",
                    target="memory://run",
                    reason="superseded",
                    proposal_ref="artifact://memory/proposal-old",
                    decision_ref="artifact://memory/decision-supersede",
                    source=source,
                ),
                memory_side_effect_summary(
                    state="accepted_for_run_context",
                    target="memory://run",
                    reason="replacement_available",
                    proposal_ref="artifact://memory/proposal-new",
                    decision_ref="artifact://memory/decision-new",
                    application_result_ref="artifact://memory/application-new",
                    source=source,
                ),
            ]
        },
    )
    memory_identity = _valid_manifest_payload(
        terminalDisposition="accepted",
        sideEffects={
            "memory": [
                memory_side_effect_summary(
                    state="proposed",
                    target="memory://run",
                    reason="candidate_codebase_pattern",
                    proposal_ref="artifact://memory/proposal-identity",
                    source=source,
                )
            ]
        },
    )
    memory_unsafe = _valid_manifest_payload(
        terminalDisposition="accepted",
        sideEffects={
            "memory": [
                memory_side_effect_summary(
                    state="rejected",
                    target="memory://run",
                    reason="unsafe_content_rejected",
                    proposal_ref="artifact://memory/proposal-unsafe",
                    decision_ref="artifact://memory/decision-unsafe",
                    source=source,
                )
            ]
        },
    )
    catalog = [
        {
            "fixtureId": "successful-execution",
            "decision": _classify_manifest_payload(
                "successful-execution",
                _valid_manifest_payload(),
                category="golden",
                expected="valid",
                traceability=(
                    "FR-003",
                    "FR-004",
                    "FR-007",
                    "SC-003",
                    "SC-005",
                    "DESIGN-REQ-006",
                ),
            ),
        },
        {
            "fixtureId": "failed-reattempt",
            "decision": _classify_manifest_payload(
                "failed-reattempt",
                failed_manifest,
                category="golden",
                expected="valid",
                traceability=("FR-003", "SC-003", "DESIGN-REQ-006"),
            ),
        },
        {
            "fixtureId": "memory-failed-proposed",
            "decision": _classify_manifest_payload(
                "memory-failed-proposed",
                memory_failed,
                category="golden",
                expected="valid",
                traceability=(
                    "FR-011",
                    "SC-002",
                    "DESIGN-REQ-007",
                ),
            ),
        },
        {
            "fixtureId": "memory-accepted-run-context",
            "decision": _classify_manifest_payload(
                "memory-accepted-run-context",
                memory_run_context,
                category="golden",
                expected="valid",
                traceability=(
                    "FR-005",
                    "FR-006",
                    "FR-007",
                    "DESIGN-REQ-007",
                ),
            ),
        },
        {
            "fixtureId": "memory-blocked-repo-write",
            "decision": _classify_manifest_payload(
                "memory-blocked-repo-write",
                memory_blocked_repo,
                category="golden",
                expected="valid",
                traceability=(
                    "FR-008",
                    "FR-009",
                    "FR-013",
                    "FR-014",
                    "DESIGN-REQ-007",
                ),
            ),
        },
        {
            "fixtureId": "memory-superseded",
            "decision": _classify_manifest_payload(
                "memory-superseded",
                memory_superseded,
                category="golden",
                expected="valid",
                traceability=("FR-003", "FR-012", "SC-004", "DESIGN-REQ-007"),
            ),
        },
        {
            "fixtureId": "memory-source-identity",
            "decision": _classify_manifest_payload(
                "memory-source-identity",
                memory_identity,
                category="golden",
                expected="valid",
                traceability=("FR-004", "SC-001", "DESIGN-REQ-007"),
            ),
        },
        {
            "fixtureId": "memory-unsafe-content",
            "decision": _classify_manifest_payload(
                "memory-unsafe-content",
                memory_unsafe,
                category="golden",
                expected="valid",
                traceability=("FR-015", "SC-006", "DESIGN-REQ-007"),
            ),
        },
        {
            "fixtureId": "gate-failure",
            "decision": classify_gate_verdict(
                "gate-failure",
                "ADDITIONAL_WORK_NEEDED",
                category="gate",
                expected="valid",
            ),
        },
        {
            "fixtureId": "recovery-with-preserved-steps",
            "decision": _classify_manifest_payload(
                "recovery-with-preserved-steps",
                recovery_manifest,
                category="golden",
                expected="valid",
                traceability=("FR-003", "SC-003", "DESIGN-REQ-003"),
            ),
        },
        {
            "fixtureId": "degraded-checkpoint-payload",
            "decision": _classify_checkpoint_payload(
                "degraded-checkpoint-payload",
                degraded_checkpoint,
                category="degraded_input",
                expected="invalid",
                traceability=("FR-003", "FR-005", "DESIGN-REQ-010"),
            ),
        },
        {
            "fixtureId": "degraded-gate-verdict",
            "decision": classify_gate_verdict(
                "degraded-gate-verdict",
                "FUTURE_OK",
                category="degraded_input",
                expected="degraded",
            ),
        },
        {
            "fixtureId": "legacy-checkpoint-only-ledger-row",
            "decision": classify_legacy_checkpoint_only_ledger_row(
                {
                    "logicalStepId": "implement-story",
                    "stateCheckpointRef": "artifact://legacy-checkpoint",
                },
                category="replay",
                expected="degraded",
            ),
        },
        {
            "fixtureId": "bounded-story-loop-contract",
            "decision": _decision(
                fixture_id="bounded-story-loop-contract",
                category="golden",
                contract_surface="bounded_story_loop",
                decision="valid",
                expected="valid",
                failure_code=None,
                message=(
                    "bounded story loop contract covers manifest, checkpoint, gate, "
                    "publication, replay, and degraded input traceability"
                ),
                traceability=(
                    "FR-018",
                    "SC-006",
                    "DESIGN-REQ-009",
                    "FR-001",
                    "FR-002",
                    "FR-003",
                ),
            ),
        },
        {
            "fixtureId": "bounded-story-loop-ref-only-evidence",
            "decision": _decision(
                fixture_id="bounded-story-loop-ref-only-evidence",
                category="golden",
                contract_surface="bounded_story_loop",
                decision="valid",
                expected="valid",
                failure_code=None,
                message="bounded story loop evidence remains artifact-ref only",
                traceability=(
                    "FR-019",
                    "FR-005",
                    "FR-006",
                    "FR-011",
                    "DESIGN-REQ-002",
                    "DESIGN-REQ-009",
                ),
            ),
        },
    ]
    return catalog


def replay_degraded_fixture_decisions() -> list[dict[str, Any]]:
    old_manifest = _valid_manifest_payload(status="future_status")
    old_checkpoint = _valid_checkpoint_payload()
    old_checkpoint["workspace"]["kind"] = "future_workspace"
    return [
        _classify_manifest_payload(
            "old-manifest-row",
            old_manifest,
            category="degraded_input",
            expected="invalid",
            traceability=("FR-005", "SC-004", "DESIGN-REQ-024"),
        ),
        _classify_checkpoint_payload(
            "old-checkpoint-row",
            old_checkpoint,
            category="degraded_input",
            expected="invalid",
            traceability=("FR-005", "SC-004", "DESIGN-REQ-024"),
        ),
        _classify_checkpoint_payload(
            "future-checkpoint-policy",
            _valid_checkpoint_payload(),
            category="degraded_input",
            expected="invalid",
            workspace_policy="future_checkpoint_policy",
            traceability=("FR-005", "SC-004", "DESIGN-REQ-024"),
        ),
        classify_gate_verdict(
            "blank-gate-verdict", "", category="degraded_input", expected="degraded"
        ),
        classify_gate_verdict(
            "unknown-gate-verdict",
            "APPROVED_WITH_WARNINGS",
            category="degraded_input",
            expected="degraded",
        ),
        classify_gate_verdict(
            "future-gate-verdict",
            "FUTURE_OK",
            category="degraded_input",
            expected="degraded",
        ),
        classify_gate_verdict(
            "old-gate-verdict", "PASS", category="degraded_input", expected="degraded"
        ),
        classify_legacy_checkpoint_only_ledger_row(
            {
                "logicalStepId": "implement-story",
                "stepCheckpointRef": "artifact://legacy-step-checkpoint",
            },
            category="replay",
            expected="degraded",
        ),
    ]


def _terminology_decisions() -> list[dict[str, Any]]:
    return [
        _decision(
            fixture_id="term-step-executions",
            category="terminology",
            contract_surface="terminology",
            decision="valid",
            expected="valid",
            failure_code=None,
            message="canonical route term accepted",
            traceability=("FR-006", "SC-005", "DESIGN-REQ-003", "DESIGN-REQ-021"),
        ),
        _decision(
            fixture_id="term-executionOrdinal",
            category="terminology",
            contract_surface="terminology",
            decision="valid",
            expected="valid",
            failure_code=None,
            message="canonical ordinal field accepted",
            traceability=("FR-006", "SC-005", "DESIGN-REQ-003"),
        ),
        _decision(
            fixture_id="term-recover_from_failed_step",
            category="terminology",
            contract_surface="terminology",
            decision="valid",
            expected="valid",
            failure_code=None,
            message="canonical recovery action token accepted",
            traceability=("FR-006", "SC-005", "DESIGN-REQ-003"),
        ),
        _decision(
            fixture_id="term-step-attempt",
            category="terminology",
            contract_surface="terminology",
            decision="invalid",
            expected="invalid",
            failure_code="superseded_step_attempt_term",
            message="superseded terminology rejected",
            traceability=("FR-006", "SC-005", "DESIGN-REQ-003"),
        ),
    ]


def api_contract_fixture() -> dict[str, Any]:
    return {
        "fixtureId": "api-step-executions-projection",
        "route": "/api/executions/{execution_id}/step-executions",
        "projection": {
            "stepExecutionId": "workflow-1:run-1:implement-story:execution:2",
            "logicalStepId": "implement-story",
            "executionOrdinal": 2,
            "recoveryAction": "recover_from_failed_step",
            "artifactRefs": {
                "manifestRef": "artifact://manifest",
                "checkpointRef": "artifact://checkpoint",
                "logsRef": "artifact://logs",
                "diffRef": "artifact://diff",
                "verificationReportRef": "artifact://verification-report",
            },
            "stepEvidence": {
                "checkpointRefsByBoundary": {
                    "before_execution": {
                        "category": "checkpoint",
                        "status": "available",
                        "artifactRef": "artifact://checkpoint",
                        "boundary": "before_execution",
                    }
                },
                "contextBundleRef": {
                    "category": "context",
                    "status": "available",
                    "artifactRef": "artifact://context-bundle",
                },
                "retrievalManifestRef": {
                    "category": "retrieval",
                    "status": "available",
                    "artifactRef": "artifact://retrieval-manifest",
                },
                "memoryManifestRef": {
                    "category": "memory",
                    "status": "skipped",
                    "reasonCode": "memory_not_requested",
                },
                "gateSummary": {
                    "verdict": "FULLY_IMPLEMENTED",
                    "artifactRef": "artifact://verification-report",
                },
                "terminalDisposition": "accepted",
                "sideEffectSummary": {
                    "status": "available",
                    "artifactRefs": {
                        "summaryRef": "artifact://side-effect-summary",
                    },
                },
                "diagnosticRefs": [
                    {
                        "kind": "provider_lease",
                        "status": "available",
                        "diagnosticsRef": "artifact://provider-lease-diagnostics",
                        "reasonCode": "provider_lease_diagnostics",
                        "summary": "Provider lease diagnostics are available by ref.",
                    }
                ],
            },
            "recoveryEligibility": {
                "eligible": True,
                "defaultAction": "resume_from_checkpoint",
                "disabledReasonCode": None,
                "requiredBoundary": "before_execution",
                "checkpointRef": "artifact://checkpoint",
                "sourceWorkflowId": "workflow-source",
                "sourceRunId": "run-source",
                "operatorGuidance": "resume",
                "evidence": [
                    {
                        "category": "checkpoint",
                        "status": "available",
                        "artifactRef": "artifact://checkpoint",
                        "boundary": "before_execution",
                    }
                ],
            },
        },
    }


def _writer_fixtures() -> list[dict[str, Any]]:
    return [
        {
            "fixtureId": "writer-manifest",
            "contractSurface": "manifest",
            "contentType": STEP_EXECUTION_MANIFEST_CONTENT_TYPE,
            "payload": {
                **_valid_manifest_payload(),
                "contentType": STEP_EXECUTION_MANIFEST_CONTENT_TYPE,
            },
        },
        {
            "fixtureId": "writer-checkpoint",
            "contractSurface": "checkpoint",
            "contentType": STEP_EXECUTION_CHECKPOINT_CONTENT_TYPE,
            "payload": _valid_checkpoint_payload(),
        },
    ]


def _api_decision() -> dict[str, Any]:
    return _decision(
        fixture_id="api-step-executions-projection",
        category="api_contract",
        contract_surface="api",
        decision="valid",
        expected="valid",
        failure_code=None,
        message="api projection exposes canonical terms and artifact refs",
        traceability=("FR-001", "FR-002", "FR-006", "SC-001", "DESIGN-REQ-021"),
    )


def _family_results(decisions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    families: list[dict[str, Any]] = []
    for family in REQUIRED_CONFORMANCE_FAMILIES:
        failed = [
            decision["fixtureId"]
            for decision in decisions
            if _decision_family(decision) == family
            and decision["decision"] != decision.get("expected", "valid")
        ]
        families.append(
            {
                "family": family,
                "result": "failed" if failed else "passed",
                "failedFixtureIds": failed,
            }
        )
    return families


def _decision_family(decision: Mapping[str, Any]) -> str:
    category = str(decision.get("category") or "")
    surface = str(decision.get("contractSurface") or "")
    if category == "forbidden_inline_evidence":
        return surface
    if category == "api_contract":
        return "api_contract"
    if category == "terminology":
        return "terminology"
    if category == "replay":
        return "replay"
    if category == "degraded_input":
        return "degraded_input"
    if surface == "gate":
        return "gate"
    return "golden"


def build_conformance_summary() -> dict[str, Any]:
    forbidden_decisions = [
        deepcopy(fixture["decision"]) for fixture in FORBIDDEN_INLINE_EVIDENCE_FIXTURES
    ]
    decisions = (
        [fixture["decision"] for fixture in golden_fixture_catalog()]
        + replay_degraded_fixture_decisions()
        + forbidden_decisions
        + _terminology_decisions()
        + [_api_decision()]
    )
    families = _family_results(decisions)
    return {
        "suite": STEP_EXECUTION_CONFORMANCE_SUITE_ID,
        "overallResult": "passed"
        if all(family["result"] == "passed" for family in families)
        else "failed",
        "families": families,
        "decisions": decisions,
        "writerFixtures": _writer_fixtures(),
        "terminologyGuardrail": {
            "tool": "tools/verify_workflow_terminology.py",
            "mode": "runtime",
            "invokedByEntrypoint": True,
        },
    }


def run_step_execution_conformance(
    *, simulate_family_failure: str | None = None
) -> dict[str, Any]:
    summary = build_conformance_summary()
    if simulate_family_failure:
        failed_family = {
            "family": simulate_family_failure,
            "result": "failed",
            "failedFixtureIds": [f"simulated-{simulate_family_failure}-failure"],
        }
        remaining = [
            family
            for family in summary["families"]
            if family["family"] != simulate_family_failure
        ]
        summary["families"] = [failed_family, *remaining]
        summary["overallResult"] = "failed"
    return {
        "suite": summary["suite"],
        "overallResult": summary["overallResult"],
        "families": summary["families"],
        "failedFixtureIds": [
            fixture_id
            for family in summary["families"]
            for fixture_id in family["failedFixtureIds"]
        ],
        "familyResults": {
            family["family"]: family["result"] for family in summary["families"]
        },
    }


def _run_terminology_guardrail() -> dict[str, Any]:
    result = subprocess.run(
        [sys.executable, "tools/verify_workflow_terminology.py", "--mode", "runtime"],
        check=False,
        text=True,
        capture_output=True,
    )
    return {
        "family": "terminology",
        "result": "passed" if result.returncode == 0 else "failed",
        "failedFixtureIds": [] if result.returncode == 0 else ["terminology-guardrail"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--simulate-family-failure")
    args = parser.parse_args(argv)

    result = run_step_execution_conformance(
        simulate_family_failure=args.simulate_family_failure
    )
    if not args.simulate_family_failure:
        terminology = _run_terminology_guardrail()
        if terminology["result"] == "failed":
            result["overallResult"] = "failed"
            result["families"] = [
                terminology
                if family["family"] == "terminology"
                else family
                for family in result["families"]
            ]
            result["failedFixtureIds"] = [
                fixture_id
                for family in result["families"]
                for fixture_id in family["failedFixtureIds"]
            ]
            result["familyResults"] = {
                family["family"]: family["result"] for family in result["families"]
            }
    print(json.dumps(result, sort_keys=True))
    return 0 if result["overallResult"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
