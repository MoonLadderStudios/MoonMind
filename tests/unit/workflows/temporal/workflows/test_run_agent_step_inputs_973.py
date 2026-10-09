"""Resolved authored data must survive the tool-to-AgentRun request handoff."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from moonmind.workflows.temporal.workflows import run as run_module


@pytest.mark.parametrize("patched", [False, True])
def test_instructions_only_agent_receives_resolved_inputs_as_data(monkeypatch, patched):
    monkeypatch.setattr(
        run_module.workflow,
        "info",
        lambda: SimpleNamespace(
            task_queue="mm.workflow.user.v2",
            namespace="default",
            workflow_id="document-journey",
            run_id="run-973",
            parent=None,
        ),
    )
    monkeypatch.setattr(
        run_module.workflow,
        "patched",
        lambda name: patched and name == "run-agent-step-inputs-handoff-v1",
    )
    authored_data = {
        "documentPaths": ["docs/first.md", "docs/second.txt"],
        "artifact": "artifact://authorized-document",
        "runtime": {"mode": "untrusted-runtime"},
        "allowed_tools": ["untrusted-authority"],
    }
    node_inputs = {
        "instructions": "Summarize the discovered documents.",
        "runtime": {"mode": "omnigent"},
        "inputs": authored_data,
    }

    request = run_module.MoonMindRunWorkflow()._build_agent_execution_request(
        node_inputs=node_inputs,
        node_id="consume",
        tool_name="omnigent",
    )

    assert request.agent_id == "omnigent"
    assert "allowed_tools" not in request.parameters
    assert "documentPaths" not in request.parameters
    assert request.skill == {}
    assert node_inputs["instructions"] == "Summarize the discovered documents."
    if patched:
        assert request.instruction_ref.startswith(node_inputs["instructions"])
        assert (
            json.loads(request.instruction_ref.split("Resolved step inputs:\n", 1)[1])
            == authored_data
        )
    else:
        assert request.instruction_ref == node_inputs["instructions"]


@pytest.mark.parametrize("selected_skill", [False, True])
def test_agent_input_handoff_does_not_duplicate_existing_inputs(
    monkeypatch, selected_skill
):
    monkeypatch.setattr(
        run_module.workflow,
        "info",
        lambda: SimpleNamespace(
            task_queue="mm.workflow.user.v2",
            namespace="default",
            workflow_id="document-journey",
            run_id="run-973",
            parent=None,
        ),
    )
    monkeypatch.setattr(
        run_module.workflow,
        "patched",
        lambda name: name == "run-agent-step-inputs-handoff-v1",
    )
    authored_data = {"documentPaths": ["docs/first.md"]}
    instructions = "Summarize the discovered documents."
    node_inputs = {
        "instructions": instructions,
        "runtime": {"mode": "omnigent"},
        "inputs": authored_data,
    }
    if selected_skill:
        node_inputs["selectedSkill"] = "summarize-documents"
        node_inputs["skill"] = {"name": "summarize-documents", "inputs": authored_data}
    else:
        instructions += "\n\nSelected skill inputs:\n" + json.dumps(
            authored_data, indent=2, sort_keys=True
        )
        node_inputs["instructions"] = instructions

    request = run_module.MoonMindRunWorkflow()._build_agent_execution_request(
        node_inputs=node_inputs,
        node_id="consume",
        tool_name="omnigent",
    )

    assert request.instruction_ref == instructions
    if selected_skill:
        assert request.skill["inputs"] == authored_data


@pytest.mark.parametrize("source", ["instructionRef", "instructions", "checkpoint"])
def test_agent_input_handoff_preserves_opaque_instruction_refs(monkeypatch, source):
    monkeypatch.setattr(
        run_module.workflow,
        "info",
        lambda: SimpleNamespace(
            task_queue="mm.workflow.user.v2",
            namespace="default",
            workflow_id="document-journey",
            run_id="run-973",
            parent=None,
        ),
    )
    monkeypatch.setattr(
        run_module.workflow,
        "patched",
        lambda name: name
        in {
            run_module.RUN_AGENT_STEP_INPUTS_HANDOFF_PATCH,
            run_module.RUN_CHECKPOINT_BRANCH_TURN_CONTEXT_PATCH,
            run_module.RUN_OMNIGENT_CHECKPOINT_BRANCH_TURN_REQUEST_PATCH,
        },
    )
    instruction_ref = "artifact://instructions/consume"
    node_inputs = {
        "runtime": {"mode": "omnigent"},
        "inputs": {"documentPaths": ["docs/first.md"]},
    }
    if source == "checkpoint":
        branch_turn = {
            "branchId": "branch-1",
            "branchTurnId": "turn-1",
            "sourceWorkflowId": "source-workflow",
            "sourceRunId": "source-run",
            "sourceLogicalStepId": "source-step",
            "sourceCheckpointRef": "artifact://checkpoint/source",
            "sourceCheckpointDigest": "sha256:" + "a" * 64,
            "instructionArtifactRef": instruction_ref,
            "instructionDigest": "sha256:" + "b" * 64,
            "workspacePolicy": "fresh_branch_from_source",
            "runtimeContextPolicy": "fresh_agent_run",
        }
        node_inputs["instructions"] = "Ignored inline checkpoint instructions."
        node_inputs["runtime"]["metadata"] = {
            "moonmind": {"checkpointBranchTurn": branch_turn}
        }
    else:
        node_inputs[source] = instruction_ref

    request = run_module.MoonMindRunWorkflow()._build_agent_execution_request(
        node_inputs=node_inputs,
        node_id="consume",
        tool_name="omnigent",
    )

    if source == "checkpoint":
        assert request.instruction_ref is None
        assert request.parameters["omnigent"]["prompt"] == {
            "instructionRef": instruction_ref
        }
    else:
        assert request.instruction_ref == instruction_ref
