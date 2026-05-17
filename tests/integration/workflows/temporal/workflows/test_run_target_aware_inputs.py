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
    attempt_context = dumped["parameters"]["metadata"]["moonmind"]["attemptContext"]
    projection = dumped["parameters"]["metadata"]["moonmind"][
        "attemptManifestProjection"
    ]
    assert attempt_context["workflowId"] == "run-target-aware-integration"
    assert attempt_context["logicalStepId"] == "first-step"
    assert attempt_context["preparedInputRefs"] == [
        "prepared-context://objective/objective-artifact",
        "prepared-context://steps/first-step/first-step-artifact",
        "artifact://objective-artifact",
        "artifact://first-step-artifact",
    ]
    assert attempt_context["contextBundleDigest"].startswith("sha256:")
    assert projection["context"]["contextBundleRef"] == (
        attempt_context["contextBundleRef"]
    )
    assert "preparedInputRefs" not in projection["context"]


def test_agent_run_child_input_scope_is_parent_selected() -> None:
    wf = MoonMindRunWorkflow()
    with patch(
        "moonmind.workflows.temporal.workflows.run.workflow.info",
        return_value=SimpleNamespace(
            workflow_id="run-target-aware-child",
            run_id="run-id-1",
            namespace="default",
        ),
    ):
        request = wf._build_agent_execution_request(
            node_inputs={"runtime": {"mode": "jules"}},
            node_id="second-step",
            tool_name="jules",
            workflow_parameters={
                "task": {
                    "inputAttachments": [{"artifactId": "objective-artifact"}],
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
    prepared_context = dumped["parameters"]["metadata"]["moonmind"]["preparedContext"]

    assert dumped["agentKind"] == "external"
    assert prepared_context["logicalStepId"] == "second-step"
    assert "objective-artifact" in str(dumped)
    assert "second-step-artifact" in str(dumped)
    assert "first-step-artifact" not in str(dumped)


def test_external_runtime_receives_raw_refs_without_context_refs() -> None:
    wf = MoonMindRunWorkflow()
    task_payload = {
        "inputAttachments": [{"artifactId": "objective-artifact"}],
        "steps": [
            {
                "id": "first-step",
                "inputAttachments": [{"artifactId": "first-step-artifact"}],
            },
            {
                "id": "second-step",
                "inputAttachments": [{"artifactId": "second-step-artifact"}],
            },
        ],
    }
    with patch(
        "moonmind.workflows.temporal.workflows.run.workflow.info",
        return_value=SimpleNamespace(
            workflow_id="run-target-aware-raw-refs",
            run_id="run-id-1",
            namespace="default",
        ),
    ):
        request = wf._build_agent_execution_request(
            node_inputs={
                "runtime": {"mode": "jules"},
                "inputRefs": ["artifact://explicit-node-input"],
            },
            node_id="second-step",
            tool_name="jules",
            workflow_parameters={"task": task_payload},
        )

    prepared_context = request.parameters["metadata"]["moonmind"]["preparedContext"]

    assert request.input_refs == [
        "artifact://explicit-node-input",
        "artifact://objective-artifact",
        "artifact://second-step-artifact",
    ]
    assert prepared_context["inputRefs"] == [
        "prepared-context://objective/objective-artifact",
        "prepared-context://steps/second-step/second-step-artifact",
        "artifact://objective-artifact",
        "artifact://second-step-artifact",
    ]
    assert "prepared-context://" not in str(request.input_refs)
    assert "first-step-artifact" not in str(request.model_dump(by_alias=True))


def test_same_workspace_materialization_excludes_sibling_step_refs() -> None:
    wf = MoonMindRunWorkflow()
    with patch(
        "moonmind.workflows.temporal.workflows.run.workflow.info",
        return_value=SimpleNamespace(
            workflow_id="run-target-aware-shared-workspace",
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
                    "steps": [
                        {
                            "id": "first-step",
                            "inputAttachments": [
                                {"artifactId": "shared-image", "filename": "image.png"}
                            ],
                        },
                        {
                            "id": "second-step",
                            "inputAttachments": [
                                {
                                    "artifactId": "shared-image-copy",
                                    "filename": "image.png",
                                }
                            ],
                        },
                    ],
                }
            },
        )

    dumped = request.model_dump(by_alias=True)
    prepared_context = dumped["parameters"]["metadata"]["moonmind"]["preparedContext"]

    assert request.input_refs == []
    assert prepared_context["logicalStepId"] == "first-step"
    assert "prepared-context://steps/first-step/shared-image" in str(prepared_context)
    assert "shared-image-copy" not in str(prepared_context)
    assert "second-step" not in str(prepared_context)


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
