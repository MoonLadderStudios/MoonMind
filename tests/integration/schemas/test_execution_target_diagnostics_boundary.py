"""Schema boundary coverage for execution target diagnostics."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from moonmind.schemas.temporal_models import ExecutionModel

pytestmark = [pytest.mark.integration, pytest.mark.integration_ci]


def _base_execution_payload() -> dict[str, object]:
    now = datetime.now(UTC)
    return {
        "taskId": "mm:wf-1",
        "namespace": "moonmind",
        "workflowId": "mm:wf-1",
        "runId": "run-1",
        "temporalRunId": "run-1",
        "workflowType": "MoonMind.Run",
        "entry": "user_workflow",
        "ownerType": "user",
        "ownerId": "user-1",
        "title": "Target diagnostics",
        "summary": "Execution updated.",
        "status": "failed",
        "dashboardStatus": "failed",
        "state": "failed",
        "rawState": "failed",
        "temporalStatus": "failed",
        "createdAt": now,
        "updatedAt": now,
        "detailHref": "/workflows/mm:wf-1",
    }


def test_execution_model_preserves_target_diagnostics_alias_contract() -> None:
    execution = ExecutionModel(
        **_base_execution_payload(),
        targetDiagnostics={
            "targets": [
                {
                    "targetKind": "objective",
                    "label": "Task objective",
                    "attachments": [
                        {
                            "artifactRef": "artifact://input/objective",
                            "filename": "objective.png",
                            "contentType": "image/png",
                            "previewAvailable": True,
                        }
                    ],
                    "refs": [
                        {
                            "refKind": "generated_context",
                            "artifactRef": "artifact://context/objective",
                        }
                    ],
                    "failures": [
                        {
                            "phase": "degraded",
                            "message": "Raw event used an unknown target phase.",
                        }
                    ],
                }
            ],
            "recovery": {
                "resumed": True,
                "sourceWorkflowId": "mm:source",
                "sourceRunId": "run-source",
                "checkpointRef": "artifact://resume/checkpoint",
                "preservedSteps": [
                    {
                        "logicalStepId": "prepare",
                        "title": "Prepare context",
                        "sourceExecutionOrdinal": 1,
                        "sourceWorkflowId": "mm:source",
                        "sourceRunId": "run-source",
                    }
                ],
                "failedResumePhase": "checkpoint_validation",
            },
            "degradedReason": None,
        },
    )

    payload = execution.model_dump(by_alias=True)

    assert payload["targetDiagnostics"]["targets"][0]["targetKind"] == "objective"
    assert (
        payload["targetDiagnostics"]["targets"][0]["attachments"][0]["artifactRef"]
        == "artifact://input/objective"
    )
    assert payload["targetDiagnostics"]["recovery"]["preservedSteps"][0][
        "logicalStepId"
    ] == "prepare"
    assert (
        payload["targetDiagnostics"]["targets"][0]["failures"][0]["phase"]
        == "degraded"
    )
    assert (
        payload["targetDiagnostics"]["recovery"]["failedResumePhase"]
        == "checkpoint_validation"
    )


def test_execution_model_preserves_alias_shaped_target_semantics() -> None:
    execution = ExecutionModel(
        **_base_execution_payload(),
        target_diagnostics={
            "targets": [
                {
                    "target_kind": "objective",
                    "label": "Task objective",
                    "attachments": [
                        {
                            "artifact_ref": "artifact://input/objective",
                            "filename": "objective.png",
                        }
                    ],
                },
                {
                    "target_kind": "step",
                    "step_id": "inspect",
                    "label": "Inspect screenshot",
                    "attachments": [
                        {
                            "artifact_ref": "artifact://input/step",
                            "filename": "step.png",
                        }
                    ],
                },
            ],
            "recovery": None,
            "degraded_reason": None,
        },
    )

    payload = execution.model_dump(by_alias=True)
    objective, step = payload["targetDiagnostics"]["targets"]

    assert objective["targetKind"] == "objective"
    assert objective["attachments"][0]["artifactRef"] == "artifact://input/objective"
    assert step["targetKind"] == "step"
    assert step["stepId"] == "inspect"
    assert step["attachments"][0]["artifactRef"] == "artifact://input/step"


def test_execution_model_preserves_failed_step_execution_recovery_phase() -> None:
    execution = ExecutionModel(
        **_base_execution_payload(),
        targetDiagnostics={
            "targets": [],
            "recovery": {
                "resumed": True,
                "sourceWorkflowId": "mm:source",
                "sourceRunId": "run-source",
                "checkpointRef": "artifact://resume/checkpoint",
                "preservedSteps": [
                    {
                        "logicalStepId": "prepare",
                        "title": "Prepare context",
                        "sourceExecutionOrdinal": 1,
                        "sourceWorkflowId": "mm:source",
                        "sourceRunId": "run-source",
                    }
                ],
                "failedResumePhase": "failed_step_execution",
            },
            "degradedReason": None,
        },
    )

    payload = execution.model_dump(by_alias=True)
    recovery = payload["targetDiagnostics"]["recovery"]

    assert recovery["failedResumePhase"] == "failed_step_execution"
    assert recovery["preservedSteps"][0]["logicalStepId"] == "prepare"


def test_execution_model_preserves_generated_context_and_bounded_failures() -> None:
    execution = ExecutionModel(
        **_base_execution_payload(),
        targetDiagnostics={
            "targets": [
                {
                    "targetKind": "step",
                    "stepId": "inspect",
                    "label": "Inspect screenshot",
                    "attachments": [],
                    "refs": [
                        {
                            "refKind": "generated_context",
                            "artifactRef": "artifact://context/inspect",
                        }
                    ],
                    "failures": [
                        {
                            "phase": "context_generation",
                            "message": "Context generation failed.",
                            "evidenceRef": "artifact://diagnostics/context",
                        }
                    ],
                }
            ],
            "recovery": None,
            "degradedReason": None,
        },
    )

    payload = execution.model_dump(by_alias=True)
    target = payload["targetDiagnostics"]["targets"][0]

    assert target["refs"][0]["refKind"] == "generated_context"
    assert target["refs"][0]["artifactRef"] == "artifact://context/inspect"
    assert target["failures"][0]["phase"] == "context_generation"
