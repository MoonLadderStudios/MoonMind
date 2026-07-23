"""Unit tests for the failed-run recovery manifest contract and builder (MM-881).

Covers the acceptance criteria for
"Make failed runs always emit a recovery manifest before terminal failure":

- Every failed run produces a recovery manifest naming the last accepted step,
  failed logical step, execution ordinal, checkpoint refs, validation result,
  side-effect dispositions, resume allowance, and blocked reason.
- Resume cannot silently degrade to a full rerun when checkpoint validation or
  restoration fails (contract is fail-closed).
- Side effects are classified accepted / discarded / blocked / needs_compensation.
- Negative checkpoint evidence: missing, corrupted, unauthorized, and
  workspace-policy-incompatible.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from moonmind.schemas.temporal_models import (
    FAILED_RUN_RECOVERY_MANIFEST_CONTENT_TYPE,
    FailedRunRecoveryManifestModel,
    RecoveryCheckpointValidationModel,
    RecoveryEligibilityDiagnosticModel,
)
from moonmind.workflows.temporal.recovery_manifest import (
    build_failed_run_recovery_manifest,
)
from moonmind.workflows.executions.runtime_capabilities import (
    resolve_runtime_execution_capabilities,
)

_NOW = datetime(2026, 6, 24, 12, 0, tzinfo=UTC)


def _rows() -> list[dict]:
    return [
        {
            "logicalStepId": "prepare",
            "order": 1,
            "status": "succeeded",
            "executionOrdinal": 1,
            "terminalDisposition": "accepted",
            "title": "Prepare workspace",
        },
        {
            "logicalStepId": "run-tests",
            "order": 2,
            "status": "failed",
            "executionOrdinal": 2,
            "title": "Run tests",
        },
    ]


def _build(**overrides):
    kwargs: dict = dict(
        workflow_id="wf-1",
        run_id="run-1",
        created_at=_NOW,
        step_ledger_rows=_rows(),
        terminal_dispositions={"prepare": "accepted"},
        checkpoint_refs_by_boundary={
            "run-tests": {"before_execution": "artifact://checkpoint/before"}
        },
        side_effect_records={},
        checkpoint_kind="worktree_archive",
        runtime_capabilities=resolve_runtime_execution_capabilities("omnigent"),
        restore_route_registered=True,
        failure_diagnostic={
            "stepId": "run-tests",
            "stage": "executing",
            "category": "execution_error",
            "message": "tests failed",
        },
    )
    kwargs.update(overrides)
    return build_failed_run_recovery_manifest(**kwargs)


def test_manifest_names_all_required_fields_and_allows_resume() -> None:
    manifest = _build()

    assert manifest.content_type == FAILED_RUN_RECOVERY_MANIFEST_CONTENT_TYPE
    # last accepted step, failed step + execution ordinal
    assert manifest.last_accepted_step is not None
    assert manifest.last_accepted_step.logical_step_id == "prepare"
    assert manifest.failed_logical_step_id == "run-tests"
    assert manifest.failed_execution_ordinal == 2
    # checkpoint refs + validation result
    assert manifest.validation.result == "valid"
    assert manifest.validation.checkpoint_ref == "artifact://checkpoint/before"
    assert [ref.artifact_ref for ref in manifest.checkpoint_refs] == [
        "artifact://checkpoint/before"
    ]
    # resume allowance, blocked reason absent
    assert manifest.resume_allowed is True
    assert manifest.blocked_reason is None
    assert manifest.recovery_eligibility.eligible is True
    assert manifest.recovery_eligibility.default_action == (
        "resume_from_workspace_checkpoint"
    )
    # failure provenance
    assert manifest.failure_stage == "executing"
    assert manifest.failure_category == "execution_error"


def test_missing_checkpoint_evidence_blocks_resume() -> None:
    manifest = _build(checkpoint_refs_by_boundary={})

    assert manifest.validation.result == "missing"
    assert manifest.resume_allowed is False
    assert manifest.blocked_reason == "no_checkpoint_evidence"
    assert manifest.recovery_eligibility.eligible is False
    assert manifest.recovery_eligibility.default_action == "full_retry"


@pytest.mark.parametrize(
    ("failure_code", "expected_result", "expected_reason", "expected_status"),
    [
        ("artifact_corrupted", "corrupted", "checkpoint_corrupted", "invalid"),
        ("artifact_unauthorized", "unauthorized", "checkpoint_unauthorized", "unauthorized"),
        ("policy_incompatible", "incompatible", "workspace_policy_incompatible", "incompatible"),
        ("workspace_incompatible", "incompatible", "workspace_policy_incompatible", "incompatible"),
        ("artifact_missing", "missing", "no_checkpoint_evidence", "missing"),
    ],
)
def test_degraded_checkpoint_evidence_blocks_resume(
    failure_code: str,
    expected_result: str,
    expected_reason: str,
    expected_status: str,
) -> None:
    manifest = _build(
        checkpoint_validation_failure={
            "failureCode": failure_code,
            "checkpointRef": "artifact://checkpoint/before",
        }
    )

    assert manifest.validation.result == expected_result
    assert manifest.validation.failure_code == failure_code
    assert manifest.resume_allowed is False
    assert manifest.blocked_reason == expected_reason
    assert manifest.recovery_eligibility.eligible is False
    # The degraded checkpoint ref is surfaced with its degraded evidence status.
    statuses = {ref.status for ref in manifest.checkpoint_refs}
    assert expected_status in statuses


def test_unknown_checkpoint_failure_code_falls_back_to_invalid_and_blocks() -> None:
    # A newly introduced / unrecognized failure code must fail closed.
    manifest = _build(
        checkpoint_validation_failure={
            "failureCode": "some_future_failure_mode",
            "checkpointRef": "artifact://checkpoint/before",
        }
    )

    assert manifest.validation.result == "invalid"
    assert manifest.validation.failure_code == "some_future_failure_mode"
    assert manifest.resume_allowed is False
    assert manifest.blocked_reason == "checkpoint_invalid"


def test_planning_failure_without_steps_still_emits_blocked_manifest() -> None:
    manifest = build_failed_run_recovery_manifest(
        workflow_id="wf-1",
        run_id="run-1",
        created_at=_NOW,
        step_ledger_rows=[],
        failure_diagnostic={"stage": "planning", "category": "execution_error"},
    )

    assert manifest.failed_logical_step_id is None
    assert manifest.resume_allowed is False
    assert manifest.blocked_reason == "no_failed_step_execution_to_resume"
    assert manifest.validation.result == "missing"


def test_environment_failure_routes_to_fix_environment_guidance() -> None:
    manifest = _build(
        checkpoint_refs_by_boundary={},
        failure_diagnostic={
            "stepId": "run-tests",
            "stage": "executing",
            "category": "system_error",
        },
    )

    assert manifest.resume_allowed is False
    assert manifest.recovery_eligibility.default_action == "fix_environment"
    assert manifest.recovery_eligibility.operator_guidance == "fix_environment"


def test_side_effects_are_classified_for_recovery() -> None:
    manifest = _build(
        side_effect_records={
            "prepare": [
                {
                    "class": "workspace_mutation",
                    "operation": "edit-file",
                    "disposition": "accepted",
                }
            ],
            "run-tests": [
                {
                    "class": "publication",
                    "operation": "repo.merge",
                    "disposition": "accepted",
                    "kind": "normal",
                },
                {
                    "class": "external_non_idempotent",
                    "operation": "jira.transition",
                    "disposition": "blocked",
                    "reason": "workflow_state_not_gate_approved",
                },
                {
                    "class": "external_idempotent",
                    "operation": "artifact.write",
                    "disposition": "candidate",
                },
            ],
        }
    )

    classified = {
        (item.effect_class, item.operation): item.disposition
        for item in manifest.side_effect_dispositions
    }
    assert classified[("workspace_mutation", "edit-file")] == "accepted"
    # An accepted, already-occurred non-idempotent publication must be
    # compensated before resume reuses the step.
    assert classified[("publication", "repo.merge")] == "needs_compensation"
    assert classified[("external_non_idempotent", "jira.transition")] == "blocked"
    # Candidate (not yet accepted) effects are discarded on failure.
    assert classified[("external_idempotent", "artifact.write")] == "discarded"
    assert {item.disposition for item in manifest.side_effect_dispositions} <= {
        "accepted",
        "discarded",
        "blocked",
        "needs_compensation",
    }


def test_uncompensated_side_effect_blocks_resume_despite_valid_checkpoint() -> None:
    # A failed step with a valid checkpoint but an accepted, already-occurred
    # non-idempotent external mutation must not advertise resume until the
    # mutation has been compensated (StepExecutionsAndCheckpointing.md §11).
    manifest = _build(
        side_effect_records={
            "run-tests": [
                {
                    "class": "publication",
                    "operation": "repo.merge",
                    "disposition": "accepted",
                    "kind": "normal",
                }
            ]
        }
    )

    assert manifest.validation.result == "valid"
    assert manifest.resume_allowed is False
    assert manifest.blocked_reason == "side_effect_needs_compensation"
    assert manifest.recovery_eligibility.eligible is False
    assert manifest.recovery_eligibility.disabled_reason_code == "CHECKPOINT_SIDE_EFFECT_UNSAFE"
    assert manifest.recovery_eligibility.default_action == "full_retry"


def test_blocked_side_effect_blocks_resume_despite_valid_checkpoint() -> None:
    manifest = _build(
        side_effect_records={
            "run-tests": [
                {
                    "class": "external_non_idempotent",
                    "operation": "jira.transition",
                    "disposition": "blocked",
                    "reason": "workflow_state_not_gate_approved",
                }
            ]
        }
    )

    assert manifest.validation.result == "valid"
    assert manifest.resume_allowed is False
    assert manifest.blocked_reason == "side_effect_blocked"
    assert manifest.recovery_eligibility.eligible is False


def test_compensated_side_effect_allows_resume() -> None:
    # Once a completed compensation ref is recorded, the non-idempotent effect
    # is accounted for and checkpoint resume is allowed again.
    manifest = _build(
        side_effect_records={
            "run-tests": [
                {
                    "class": "publication",
                    "operation": "repo.merge",
                    "disposition": "accepted",
                    "kind": "normal",
                    "compensationRef": "artifact://compensation/repo-merge",
                }
            ]
        }
    )

    assert manifest.validation.result == "valid"
    assert manifest.resume_allowed is True
    assert manifest.blocked_reason is None
    assert manifest.recovery_eligibility.eligible is True
    disposition = next(
        item
        for item in manifest.side_effect_dispositions
        if item.operation == "repo.merge"
    )
    assert disposition.disposition == "needs_compensation"
    assert disposition.compensation_ref == "artifact://compensation/repo-merge"


def test_idempotent_accepted_side_effect_does_not_block_resume() -> None:
    # Idempotent / workspace-scoped accepted effects can safely repeat, so they
    # must not block checkpoint resume.
    manifest = _build(
        side_effect_records={
            "run-tests": [
                {
                    "class": "workspace_mutation",
                    "operation": "edit-file",
                    "disposition": "accepted",
                }
            ]
        }
    )

    assert manifest.resume_allowed is True
    assert manifest.blocked_reason is None


def test_recovery_source_failed_step_used_when_no_failed_ledger_row() -> None:
    rows = [
        {
            "logicalStepId": "implement",
            "order": 1,
            "status": "running",
            "executionOrdinal": 1,
        }
    ]
    manifest = build_failed_run_recovery_manifest(
        workflow_id="wf-1",
        run_id="run-1",
        created_at=_NOW,
        step_ledger_rows=rows,
        recovery_failed_step_id="implement",
        checkpoint_refs_by_boundary={},
    )
    assert manifest.failed_logical_step_id == "implement"


def test_manifest_payload_is_compact_and_round_trips() -> None:
    manifest = _build()
    payload = manifest.model_dump(by_alias=True, mode="json")

    assert payload["contentType"] == FAILED_RUN_RECOVERY_MANIFEST_CONTENT_TYPE
    assert payload["resumeAllowed"] is True
    assert payload["failedLogicalStepId"] == "run-tests"
    # Reparse to confirm the serialized form is a valid contract instance.
    reparsed = FailedRunRecoveryManifestModel.model_validate(payload)
    assert reparsed.resume_allowed == manifest.resume_allowed
    assert reparsed.validation.checkpoint_ref == manifest.validation.checkpoint_ref


def test_contract_is_fail_closed_against_silent_full_rerun() -> None:
    """Resume cannot be allowed unless checkpoint validation is valid."""

    eligible = RecoveryEligibilityDiagnosticModel(
        eligible=True,
        defaultAction="resume_from_checkpoint",
        checkpointRef="artifact://checkpoint/before",
        operatorGuidance="resume",
    )
    with pytest.raises(ValidationError):
        FailedRunRecoveryManifestModel(
            workflowId="wf-1",
            runId="run-1",
            failedLogicalStepId="run-tests",
            failedExecutionOrdinal=1,
            validation=RecoveryCheckpointValidationModel(
                result="corrupted",
                failureCode="artifact_corrupted",
                checkpointRef="artifact://checkpoint/before",
            ),
            resumeAllowed=True,
            recoveryEligibility=eligible,
            createdAt=_NOW,
        )


def test_blocked_manifest_requires_blocked_reason() -> None:
    ineligible = RecoveryEligibilityDiagnosticModel(
        eligible=False,
        defaultAction="full_retry",
        disabledReasonCode="CHECKPOINT_ARTIFACT_INVALID",
        operatorGuidance="full_retry",
    )
    with pytest.raises(ValidationError):
        FailedRunRecoveryManifestModel(
            workflowId="wf-1",
            runId="run-1",
            validation=RecoveryCheckpointValidationModel(result="missing"),
            resumeAllowed=False,
            blockedReason=None,
            recoveryEligibility=ineligible,
            createdAt=_NOW,
        )
