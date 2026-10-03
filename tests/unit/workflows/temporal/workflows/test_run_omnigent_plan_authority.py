"""Authored step inputs cannot replace the workflow's admitted launch plan."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from moonmind.workflows.temporal.workflows.run import MoonMindRunWorkflow

PATCH = "run-omnigent-execution-plan-binding-authority-v1"


def _binding(digit: str) -> dict[str, str]:
    return {
        "planRef": "omnigent-execution-plan:sha256:" + digit * 64,
        "planDigest": "sha256:" + digit * 64,
        "planArtifactRef": "art_plan_" + digit,
        "taskInputSnapshotRef": "art_task_" + digit,
        "taskInputSnapshotDigest": "sha256:" + digit * 64,
    }


def _build(*, source: str, supplied, admitted=True, current=True):
    node = {"runtime": {"mode": "omnigent"}}
    parameters = {"omnigentExecutionPlan": _binding("1")} if admitted else {}
    if source == "node":
        node["omnigentExecutionPlan"] = supplied
    elif source == "runtime":
        node["runtime"]["omnigentExecutionPlan"] = supplied
    elif source == "runtime_parameters":
        node["runtime"]["parameters"] = {"omnigentExecutionPlan": supplied}
    elif source == "workflow_runtime_parameters":
        parameters["workflow"] = {
            "runtime": {"parameters": {"omnigentExecutionPlan": supplied}}
        }
    info = SimpleNamespace(
        namespace="default", workflow_id="mm:admitted", run_id="run-1", parent=None
    )
    with (
        patch(
            "moonmind.workflows.temporal.workflows.run.workflow.info", return_value=info
        ),
        patch(
            "moonmind.workflows.temporal.workflows.run.workflow.patched",
            side_effect=lambda patch_id: current or patch_id != PATCH,
        ),
    ):
        return MoonMindRunWorkflow()._build_agent_execution_request(
            node_inputs=node,
            node_id="step-1",
            tool_name="auto",
            workflow_parameters=parameters,
        )


@pytest.mark.parametrize(
    "source", ["node", "runtime", "runtime_parameters", "workflow_runtime_parameters"]
)
@pytest.mark.parametrize("admitted", [True, False])
def test_authored_binding_cannot_replace_or_mint_admitted_plan(source, admitted):
    with pytest.raises(ValueError, match="omnigentExecutionPlan.*admitted"):
        _build(source=source, supplied=_binding("2"), admitted=admitted)


@pytest.mark.parametrize(
    "source", ["node", "runtime", "runtime_parameters", "workflow_runtime_parameters"]
)
def test_exact_admitted_binding_copy_preserves_all_evidence(source):
    request = _build(source=source, supplied=_binding("1"))
    assert request.omnigent_execution_plan.model_dump(
        by_alias=True, exclude_none=True
    ) == _binding("1")
    assert (
        request.step_execution.omnigent_execution_plan
        == request.omnigent_execution_plan
    )


def test_runtime_parameter_null_cannot_erase_admitted_binding():
    request = _build(source="runtime_parameters", supplied=None)
    assert request.omnigent_execution_plan is not None
    assert request.omnigent_execution_plan.plan_ref == _binding("1")["planRef"]


def test_pre_patch_plan_input_keeps_its_retained_request_shape():
    request = _build(
        source="runtime", supplied=_binding("2"), admitted=False, current=False
    )
    assert request.omnigent_execution_plan.plan_ref == _binding("2")["planRef"]


def test_no_plan_step_override_keeps_existing_admission_behavior():
    # Per-step Omnigent selection still has a separate existing admission path.
    # This binding guard must not replace it with a blanket missing-plan error.
    request = _build(source="node", supplied=None, admitted=False)
    assert request.omnigent_execution_plan is None


def test_retained_no_plan_request_remains_unbound():
    request = _build(source="node", supplied=None, admitted=False, current=False)
    assert request.omnigent_execution_plan is None
