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
        "entry": "run",
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
        "detailHref": "/tasks/mm:wf-1",
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
                        "sourceAttempt": 1,
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
