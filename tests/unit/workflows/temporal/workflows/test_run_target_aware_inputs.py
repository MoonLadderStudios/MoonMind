from __future__ import annotations

import json
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

    moonmind_metadata = request.parameters["metadata"]["moonmind"]
    prepared_context = moonmind_metadata["preparedContext"]
    execution_context = moonmind_metadata["executionContext"]
    projection = moonmind_metadata["executionManifestProjection"]

    assert prepared_context["manifestRef"].startswith("prepared-context-manifest://")
    assert prepared_context["logicalStepId"] == "collect-evidence"
    assert prepared_context["targetCounts"] == {"objective": 1, "step": 1}
    assert execution_context["workflowId"] == "run-target-aware"
    assert execution_context["runId"] == "run-id-1"
    assert execution_context["logicalStepId"] == "collect-evidence"
    assert execution_context["executionOrdinal"] == 1
    assert execution_context["preparedInputRefs"] == prepared_context["inputRefs"]
    assert execution_context["contextBundleDigest"].startswith("sha256:")
    assert execution_context["contextBundleRef"] == (
        f"execution-context-bundle://{execution_context['contextBundleDigest']}"
    )
    assert execution_context["builderVersion"] == "execution-context-builder-v1"
    assert projection["context"]["contextBundleDigest"] == (
        execution_context["contextBundleDigest"]
    )
    assert "preparedInputRefs" not in projection["context"]


def test_run_request_records_retrieval_and_memory_refs_in_execution_projection() -> None:
    wf = MoonMindRunWorkflow()
    task_payload = {
        **_task_payload(),
        "retrieval": {
            "query": "execution context",
            "indexVersion": "rag-index-1",
            "returnedRefs": ["artifact://retrieved-doc"],
            "filters": {"kind": "docs"},
            "compactSummaries": ["Relevant design summary."],
        },
        "memoryProposals": [
            {
                "proposalRef": "memory://proposal-1",
                "state": "proposed",
                "summary": "Candidate memory.",
            }
        ],
    }
    with patch(
        "moonmind.workflows.temporal.workflows.run.workflow.info",
        return_value=_workflow_info(),
    ):
        request = wf._build_agent_execution_request(
            node_inputs={"runtime": {"mode": "codex_cli"}},
            node_id="collect-evidence",
            tool_name="codex_cli",
            workflow_parameters={"task": task_payload},
        )

    execution_context = request.parameters["metadata"]["moonmind"]["executionContext"]
    projection = request.parameters["metadata"]["moonmind"][
        "executionManifestProjection"
    ]

    assert execution_context["retrievalManifestRef"].startswith(
        "execution-retrieval-manifest://sha256:"
    )
    assert execution_context["memoryManifestRef"].startswith(
        "execution-memory-manifest://sha256:"
    )
    assert projection["context"]["retrievalManifestRef"] == (
        execution_context["retrievalManifestRef"]
    )
    assert projection["context"]["memoryManifestRef"] == (
        execution_context["memoryManifestRef"]
    )
    assert "Relevant design summary." not in str(projection)


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


def test_agent_request_includes_trusted_jira_previous_outputs_in_instruction_ref() -> None:
    wf = MoonMindRunWorkflow()
    with patch(
        "moonmind.workflows.temporal.workflows.run.workflow.info",
        return_value=_workflow_info(),
    ):
        request = wf._build_agent_execution_request(
            node_inputs={
                "runtime": {"mode": "claude_code"},
                "instructions": "Classify request and resume point.",
                "previousOutputs": {
                    "trustedSource": "moonmind.jira.get_issue",
                    "jiraIssueKey": "MM-657",
                    "jiraPresetBrief": "MM-657: Settings HTTP API surface",
                    "jiraIssue": {"status": "In Progress"},
                },
            },
            node_id="classify",
            tool_name="claude_code",
            workflow_parameters={"task": {}},
        )

    assert request.instruction_ref is not None
    assert request.instruction_ref.startswith("Classify request and resume point.")
    assert "MoonMind trusted previous step context:" in request.instruction_ref
    assert "moonmind.jira.get_issue" in request.instruction_ref
    assert "MM-657: Settings HTTP API surface" in request.instruction_ref
    assert "Do not use provider-native Jira/Atlassian connectors" in request.instruction_ref


def test_trusted_jira_context_persists_after_intermediate_step_output() -> None:
    wf = MoonMindRunWorkflow()
    wf._record_trusted_jira_context(
        {
            "trustedSource": "moonmind.jira.get_issue",
            "jiraIssueKey": "MM-657",
            "jiraPresetBrief": "MM-657: Settings HTTP API surface",
        }
    )

    merged = wf._merge_trusted_jira_context({"storyOutput": {"status": "COMPLETED"}})

    assert merged["storyOutput"] == {"status": "COMPLETED"}
    assert merged["trustedSource"] == "moonmind.jira.get_issue"
    assert merged["jiraIssueKey"] == "MM-657"
    assert merged["jiraPresetBrief"] == "MM-657: Settings HTTP API surface"


def test_trusted_jira_context_uses_valid_json_after_truncation() -> None:
    instruction = MoonMindRunWorkflow._trusted_previous_outputs_instruction(
        {
            "trustedSource": "moonmind.jira.get_issue",
            "jiraIssueKey": "MM-657",
            "jiraPresetBrief": "A" * 30000,
        }
    )

    assert instruction is not None
    payload = instruction.split("```json\n", 1)[1].split("\n```", 1)[0]
    parsed = json.loads(payload)
    assert parsed["trustedSource"] == "moonmind.jira.get_issue"
    assert parsed["jiraPresetBrief"].endswith("...[truncated]")


def test_trusted_jira_context_ignores_unconsumed_instruction_aliases() -> None:
    wf = MoonMindRunWorkflow()
    merged = wf._append_trusted_previous_outputs_to_agent_inputs(
        {
            "runtime": {"mode": "claude_code"},
            "instruction": "Unused alias",
            "previousOutputs": {
                "trustedSource": "moonmind.jira.get_issue",
                "jiraIssueKey": "MM-657",
                "jiraPresetBrief": "MM-657: Settings HTTP API surface",
            },
        }
    )

    assert merged["instruction"] == "Unused alias"
    assert "MoonMind trusted previous step context:" in merged["instructions"]


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
