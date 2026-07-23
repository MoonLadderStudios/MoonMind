"""Contract and admission coverage for MoonLadderStudios/MoonMind#3478."""

from __future__ import annotations

import pytest

from moonmind.schemas.workflow_recovery_models import (
    WorkflowRecoveryTargetModel,
    deterministic_recovery_creation_key,
)
from moonmind.workflows.executions.runtime_capabilities import (
    resolve_runtime_execution_capabilities,
)


def _payload(kind: str = "failed_step") -> dict:
    capability = resolve_runtime_execution_capabilities("omnigent").model_dump(
        by_alias=True, mode="json"
    )
    boundary_phase = {
        "failed_step": ("before_execution", "rerun_failed_step"),
        "control_stop": ("after_gate", "continue_after_gate"),
        "publication": ("before_publication", "resume_publication"),
        "restoration_failure": (
            "before_recovery_restoration",
            "retry_restoration",
        ),
    }
    boundary, phase = boundary_phase[kind]
    capability["checkpointBoundarySupport"][boundary] = [phase]
    capability["workspaceState"]["boundarySupport"][boundary] = [phase]
    # Recompute the frozen snapshot after the test-specific promoted capability.
    from moonmind.workflows.executions.runtime_capabilities import (
        RuntimeExecutionCapabilities,
    )

    capability["capabilityDigest"] = ""
    capability = RuntimeExecutionCapabilities.model_validate(capability).with_digest(
    ).model_dump(by_alias=True, mode="json")
    target = {
        "failed_step": {
            "kind": kind,
            "logicalStepId": "implement",
            "sourceStepExecutionId": "step-1",
        },
        "control_stop": {
            "kind": kind,
            "logicalStepId": "verify-remediation",
            "sourceStepExecutionId": "step-2",
            "controlStopKind": "workflow_gate",
            "reasonCode": "semantic_no_progress_exhausted",
            "gateResultRef": "artifact://gate",
        },
        "publication": {
            "kind": kind,
            "acceptedCandidateRef": "artifact://candidate",
            "publicationObservationRef": "artifact://publication-observation",
            "publicationIdempotencyKey": "publish-1",
        },
        "restoration_failure": {
            "kind": kind,
            "restoreOperationId": "restore-1",
            "restoreIdempotencyKey": "restore-key-1",
            "partialRestorationRef": "artifact://partial-restore",
        },
    }[kind]
    checkpoint_digest = "sha256:checkpoint"
    creation_key = deterministic_recovery_creation_key(
        "source-workflow", "source-run", kind, checkpoint_digest
    )
    return {
        "schemaVersion": "workflow-recovery-target/v1",
        "recoveryAction": "recover",
        "target": target,
        "source": {
            "workflowId": "source-workflow",
            "runId": "source-run",
            "planRef": "artifact://plan",
            "planDigest": "sha256:plan",
            "taskInputSnapshotRef": "artifact://task-input",
        },
        "checkpoint": {
            "ref": "artifact://checkpoint",
            "boundary": boundary,
            "kind": "worktree_archive",
            "digest": checkpoint_digest,
            "validationRef": "artifact://checkpoint-validation",
            "sourceWorkspaceRef": "workspace://source",
        },
        "continuation": {
            "phase": phase,
            "remainingWorkRef": (
                "artifact://remaining" if kind == "control_stop" else None
            ),
            "workspaceHeadRef": (
                "artifact://checkpoint" if kind == "control_stop" else None
            ),
            "priorBudgetRef": (
                "artifact://prior-budget" if kind == "control_stop" else None
            ),
        },
        "capabilitySnapshot": capability,
        "preservedStepRefs": ["artifact://preserved-step"],
        "sideEffectDispositionRef": "artifact://side-effects",
        "sideEffectSafe": True,
        "destination": {
            "workflowId": "recovery-workflow",
            "creationKey": creation_key,
            "runtimeId": "omnigent",
            "executionProfileRef": "provider-profile:primary",
            "workspaceReservationId": "workspace-reservation-1",
        },
    }


@pytest.mark.parametrize(
    "kind", ["failed_step", "control_stop", "publication", "restoration_failure"]
)
def test_all_typed_targets_are_admitted(kind: str) -> None:
    payload = _payload(kind)
    if kind == "control_stop":
        payload["continuation"]["newBudgetRef"] = "artifact://new-budget"
    contract = WorkflowRecoveryTargetModel.model_validate(payload)

    contract.require_admitted()
    assert all(item.admitted for item in contract.admission())


def test_control_stop_remediation_requires_explicit_new_budget() -> None:
    payload = _payload("control_stop")
    payload["continuation"]["phase"] = "continue_to_remediation"
    payload["capabilitySnapshot"]["checkpointBoundarySupport"]["after_gate"] = [
        "continue_to_remediation"
    ]
    payload["capabilitySnapshot"]["workspaceState"]["boundarySupport"]["after_gate"] = [
        "continue_to_remediation"
    ]
    from moonmind.workflows.executions.runtime_capabilities import (
        RuntimeExecutionCapabilities,
    )

    snapshot = payload["capabilitySnapshot"]
    snapshot["capabilityDigest"] = ""
    payload["capabilitySnapshot"] = RuntimeExecutionCapabilities.model_validate(
        snapshot
    ).with_digest().model_dump(by_alias=True, mode="json")
    contract = WorkflowRecoveryTargetModel.model_validate(payload)

    reasons = {
        item.reason_code for item in contract.admission() if not item.admitted
    }
    assert reasons == {"RECOVERY_BUDGET_GRANT_REQUIRED"}


def test_publication_requires_reconciliation_without_invalidating_checkpoint() -> None:
    payload = _payload("publication")
    payload["target"].pop("publicationObservationRef")
    contract = WorkflowRecoveryTargetModel.model_validate(payload)
    dimensions = {item.dimension: item for item in contract.admission()}

    assert dimensions["checkpoint"].admitted is True
    assert dimensions["target"].reason_code == (
        "RECOVERY_PUBLICATION_RECONCILIATION_REQUIRED"
    )


@pytest.mark.parametrize(
    ("kind", "phase"),
    [
        ("failed_step", "resume_publication"),
        ("control_stop", "rerun_failed_step"),
        ("publication", "retry_restoration"),
        ("restoration_failure", "continue_after_gate"),
    ],
)
def test_target_phase_mismatches_fail_closed(kind: str, phase: str) -> None:
    payload = _payload(kind)
    payload["continuation"]["phase"] = phase
    contract = WorkflowRecoveryTargetModel.model_validate(payload)

    phase_result = next(
        item for item in contract.admission() if item.dimension == "phase"
    )
    assert phase_result.reason_code == "RECOVERY_PHASE_UNSUPPORTED"


def test_workflow_input_rejects_raw_paths_and_payloads() -> None:
    payload = _payload()
    payload["checkpoint"]["validationRef"] = "/tmp/validation.json"
    with pytest.raises(ValueError, match="raw filesystem paths"):
        WorkflowRecoveryTargetModel.model_validate(payload)

    payload = _payload()
    payload["capabilitySnapshot"]["providerPayload"] = {"secret": "raw"}
    with pytest.raises(ValueError, match="bounded refs"):
        WorkflowRecoveryTargetModel.model_validate(payload)


def test_duplicate_requests_have_one_deterministic_destination_key() -> None:
    first = WorkflowRecoveryTargetModel.model_validate(_payload())
    second = WorkflowRecoveryTargetModel.model_validate(_payload())

    assert first.destination.creation_key == second.destination.creation_key


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("workflowId", "source-workflow"),
        ("runId", "already-started-run"),
        ("runtimeId", "codex_cli"),
        ("workspaceReservationId", "workspace://source"),
    ],
)
def test_destination_must_be_new_and_match_frozen_capability(
    field: str, value: str
) -> None:
    payload = _payload()
    payload["destination"][field] = value
    contract = WorkflowRecoveryTargetModel.model_validate(payload)
    dimensions = {item.dimension: item for item in contract.admission()}

    assert dimensions["checkpoint"].admitted is True
    assert dimensions["destination"].reason_code == (
        "RECOVERY_DESTINATION_IDENTITY_MISMATCH"
    )
