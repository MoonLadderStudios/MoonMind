from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

pytest.importorskip("temporalio")

pytestmark = [pytest.mark.integration, pytest.mark.integration_ci]

from moonmind.workflows.temporal.workflows.run import MoonMindRunWorkflow
from moonmind.workflows.temporal.activity_runtime import (
    build_target_aware_prepared_context_payload,
)


def test_run_boundary_prepares_objective_and_current_step_context_only() -> None:
    wf = MoonMindRunWorkflow()
    with patch(
        "moonmind.workflows.temporal.workflows.run.workflow.info",
        return_value=SimpleNamespace(
            workflow_id="run-target-aware-integration",
            run_id="run-id-1",
            namespace="default",
        ),
    ):
        request = wf._build_agent_execution_request(
            node_inputs={"runtime": {"mode": "codex_cli"}},
            node_id="first-step",
            tool_name="codex_cli",
            workflow_parameters={
                "task": {
                    "inputAttachments": [
                        {"artifactId": "objective-artifact", "contentType": "image/png"}
                    ],
                    "steps": [
                        {
                            "id": "first-step",
                            "inputAttachments": [{"artifactId": "first-step-artifact"}],
                        },
                        {
                            "id": "second-step",
                            "inputAttachments": [
                                {"artifactId": "second-step-artifact"}
                            ],
                        },
                    ],
                }
            },
        )

    dumped = request.model_dump(by_alias=True)

    assert "objective-artifact" in str(dumped)
    assert "first-step-artifact" in str(dumped)
    assert "second-step-artifact" not in str(dumped)
    assert dumped["parameters"]["metadata"]["moonmind"]["preparedContext"][
        "targetCounts"
    ] == {"objective": 1, "step": 1}


def test_prepare_boundary_reports_target_for_invalid_step_attachment() -> None:
    payload = build_target_aware_prepared_context_payload(
        {
            "steps": [
                {
                    "id": "first-step",
                    "inputAttachments": [{"filename": "missing-ref.png"}],
                }
            ]
        },
        logical_step_id="first-step",
    )

    assert payload["ok"] is False
    failure = payload["failure"]
    assert failure["logicalStepId"] == "first-step"
    assert failure["reason"] == "ValueError"
    assert "targetKind=step" in failure["message"]
    assert "stepRef=first-step" in failure["message"]
    assert "missing artifactId" in failure["message"]
