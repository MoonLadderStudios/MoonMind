from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

pytest.importorskip("temporalio")

from moonmind.workflows.temporal.workflows.run import MoonMindRunWorkflow


def _workflow_info() -> SimpleNamespace:
    return SimpleNamespace(
        workflow_id="run-target-aware",
        run_id="run-id-1",
        namespace="default",
    )


def _task_payload() -> dict[str, object]:
    return {
        "inputAttachments": [
            {"artifactId": "objective-image", "contentType": "image/png"}
        ],
        "steps": [
            {
                "id": "collect-evidence",
                "inputAttachments": [{"artifactId": "collect-notes"}],
            },
            {
                "id": "write-report",
                "inputAttachments": [{"artifactId": "report-notes"}],
            },
        ],
    }


def _build_request_for_step(step_id: str, *, runtime_mode: str = "jules"):
    wf = MoonMindRunWorkflow()
    with patch(
        "moonmind.workflows.temporal.workflows.run.workflow.info",
        return_value=_workflow_info(),
    ):
        return wf._build_agent_execution_request(
            node_inputs={
                "runtime": {"mode": runtime_mode},
                "inputRefs": ["artifact://explicit-node-input"],
            },
            node_id=step_id,
            tool_name=runtime_mode,
            workflow_parameters={"task": _task_payload()},
        )


def test_run_request_records_prepared_manifest_before_step_dispatch() -> None:
    request = _build_request_for_step("collect-evidence")

    prepared_context = request.parameters["metadata"]["moonmind"]["preparedContext"]

    assert prepared_context["manifestRef"].startswith("prepared-context-manifest://")
    assert prepared_context["logicalStepId"] == "collect-evidence"
    assert prepared_context["targetCounts"] == {"objective": 1, "step": 1}


def test_run_request_filters_prepared_context_to_current_step() -> None:
    request = _build_request_for_step("collect-evidence")

    assert request.input_refs == [
        "artifact://explicit-node-input",
        "artifact://objective-image",
        "artifact://collect-notes",
    ]
    assert "report-notes" not in str(request.model_dump(by_alias=True))


def test_external_request_keeps_generated_context_out_of_adapter_input_refs() -> None:
    request = _build_request_for_step("collect-evidence", runtime_mode="jules")

    prepared_context = request.parameters["metadata"]["moonmind"]["preparedContext"]

    assert request.agent_kind == "external"
    assert request.input_refs == [
        "artifact://explicit-node-input",
        "artifact://objective-image",
        "artifact://collect-notes",
    ]
    assert prepared_context["inputRefs"] == [
        "prepared-context://objective/objective-image",
        "prepared-context://steps/collect-evidence/collect-notes",
        "artifact://objective-image",
        "artifact://collect-notes",
    ]


def test_child_agent_run_request_receives_only_represented_step_context() -> None:
    request = _build_request_for_step("write-report")

    assert "report-notes" in str(request.model_dump(by_alias=True))
    assert "collect-notes" not in str(request.model_dump(by_alias=True))


def test_parent_request_metadata_is_target_binding_authority() -> None:
    request = _build_request_for_step("collect-evidence")

    moonmind_metadata = request.parameters["metadata"]["moonmind"]
    prepared_context = moonmind_metadata["preparedContext"]

    assert prepared_context["logicalStepId"] == "collect-evidence"
    assert prepared_context["manifestRef"].startswith("prepared-context-manifest://")
    assert prepared_context["objectiveContextRefs"] == [
        "prepared-context://objective/objective-image"
    ]
    assert prepared_context["stepContextRefs"] == [
        "prepared-context://steps/collect-evidence/collect-notes"
    ]
    assert "report-notes" not in str(moonmind_metadata)
    assert "preparedContext" not in request.workspace_spec


def test_managed_codex_request_keeps_prepared_context_out_of_input_refs() -> None:
    wf = MoonMindRunWorkflow()
    with patch(
        "moonmind.workflows.temporal.workflows.run.workflow.info",
        return_value=_workflow_info(),
    ):
        request = wf._build_agent_execution_request(
            node_inputs={"runtime": {"mode": "codex_cli"}},
            node_id="collect-evidence",
            tool_name="codex_cli",
            workflow_parameters={"task": _task_payload()},
        )

    assert request.agent_kind == "managed"
    assert request.input_refs == []
    prepared_context = request.parameters["metadata"]["moonmind"]["preparedContext"]
    assert prepared_context["inputRefs"] == [
        "prepared-context://objective/objective-image",
        "prepared-context://steps/collect-evidence/collect-notes",
        "artifact://objective-image",
        "artifact://collect-notes",
    ]


def test_prepare_failure_prevents_unbounded_context_dispatch() -> None:
    wf = MoonMindRunWorkflow()
    with patch(
        "moonmind.workflows.temporal.workflows.run.workflow.info",
        return_value=_workflow_info(),
    ):
        with pytest.raises(ValueError, match="inline attachment content"):
            wf._build_agent_execution_request(
                node_inputs={"runtime": {"mode": "codex_cli"}},
                node_id="collect-evidence",
                tool_name="codex_cli",
                workflow_parameters={
                    "task": {
                        "steps": [
                            {
                                "id": "collect-evidence",
                                "inputAttachments": [
                                    {
                                        "artifactId": "inline-image",
                                        "dataUrl": "data:image/png;base64,AAAA",
                                    }
                                ],
                            }
                        ]
                    }
                },
            )
