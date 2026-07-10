from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

pytest.importorskip("temporalio")

from moonmind.memory.procedural import (
    EvidenceRun,
    FixPattern,
    extract_error_signature,
)
from moonmind.workflows.executions.prepared_context import (
    ExecutionContextBundle,
    build_memory_manifest,
)
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


def _build_request_for_step(
    step_id: str,
    *,
    runtime_mode: str = "jules",
    model: str | None = None,
    effort: str | None = None,
):
    wf = MoonMindRunWorkflow()
    with patch(
        "moonmind.workflows.temporal.workflows.run.workflow.info",
        return_value=_workflow_info(),
    ):
        return wf._build_agent_execution_request(
            node_inputs={
                "runtime": {
                    "mode": runtime_mode,
                    **({"model": model} if model else {}),
                    **({"effort": effort} if effort else {}),
                },
                "inputRefs": ["artifact://explicit-node-input"],
            },
            node_id=step_id,
            tool_name=runtime_mode,
            workflow_parameters={"task": _task_payload()},
        )


def test_run_request_preserves_model_tier_intent_for_launch_resolution() -> None:
    wf = MoonMindRunWorkflow()
    with patch(
        "moonmind.workflows.temporal.workflows.run.workflow.info",
        return_value=_workflow_info(),
    ):
        request = wf._build_agent_execution_request(
            node_inputs={"runtime": {"mode": "codex_cli"}},
            node_id="collect-evidence",
            tool_name="codex_cli",
            workflow_parameters={
                "task": _task_payload(),
                "model": "resolved-tier-model",
                "requestedModel": None,
                "modelTier": 2,
                "tierFallback": "strict",
            },
        )

    assert request.parameters["model"] == "resolved-tier-model"
    assert request.parameters["modelTier"] == 2
    assert request.parameters["tierFallback"] == "strict"


def test_run_request_records_prepared_manifest_before_step_dispatch() -> None:
    request = _build_request_for_step("collect-evidence")

    moonmind_metadata = request.parameters["metadata"]["moonmind"]
    prepared_context = moonmind_metadata["preparedContext"]
    attempt_context = moonmind_metadata["executionContext"]
    projection = moonmind_metadata["stepExecutionManifestProjection"]

    assert prepared_context["manifestRef"].startswith("prepared-context-manifest://")
    assert prepared_context["logicalStepId"] == "collect-evidence"
    assert prepared_context["targetCounts"] == {"objective": 1, "step": 1}
    assert attempt_context["workflowId"] == "run-target-aware"
    assert attempt_context["runId"] == "run-id-1"
    assert attempt_context["logicalStepId"] == "collect-evidence"
    assert attempt_context["executionOrdinal"] == 1
    assert attempt_context["preparedInputRefs"] == prepared_context["inputRefs"]
    assert attempt_context["workspacePolicy"] == "fresh_branch_from_source"
    assert attempt_context["priorEvidenceRefs"] == []
    assert attempt_context["qualityGateProfile"] == "repo-default"
    assert attempt_context["policyRefs"]["skillSourcePolicy"]["repoSkills"] == (
        "resolver_policy_enforced"
    )
    assert attempt_context["contextBundleDigest"].startswith("sha256:")
    assert attempt_context["contextBundleRef"] == (
        f"execution-context-bundle://{attempt_context['contextBundleDigest']}"
    )
    assert attempt_context["builderVersion"] == "execution-context-builder-v1"
    assert projection["context"]["contextBundleDigest"] == (
        attempt_context["contextBundleDigest"]
    )
    assert projection["context"]["workspacePolicy"] == (
        attempt_context["workspacePolicy"]
    )
    assert projection["context"]["priorEvidenceRefs"] == []
    assert projection["context"]["qualityGateProfile"] == "repo-default"
    assert "preparedInputRefs" not in projection["context"]


def test_mm786_runtime_selection_projects_cost_and_portability_metadata() -> None:
    request = _build_request_for_step(
        "collect-evidence",
        runtime_mode="claude_code",
        model="gemini-2.5-pro",
        effort="high",
    )

    attempt_context = request.parameters["metadata"]["moonmind"]["executionContext"]
    projection = request.parameters["metadata"]["moonmind"][
        "stepExecutionManifestProjection"
    ]

    assert attempt_context["runtimeSelection"]["runtimeId"] == "claude_code"
    assert attempt_context["runtimeSelection"]["model"] == "gemini-2.5-pro"
    assert attempt_context["runtimeSelection"]["effort"] == "high"
    assert attempt_context["costPolicy"]["billingAwareRouting"] is True
    assert attempt_context["costPolicy"]["routingBasis"] == "step_runtime_selection"
    assert attempt_context["costPolicy"]["runtimeId"] == "claude_code"
    assert attempt_context["costPolicy"]["model"] == "gemini-2.5-pro"
    assert attempt_context["costPolicy"]["effort"] == "high"
    assert attempt_context["costPolicy"]["estimatedCostUnits"] >= 1
    assert attempt_context["portabilityProvenance"]["artifactPortability"] == (
        "model_agnostic_refs"
    )
    assert attempt_context["portabilityProvenance"]["memoryPortability"] == (
        "model_provenance_attached"
    )
    assert attempt_context["portabilityProvenance"]["model"] == "gemini-2.5-pro"
    assert projection["context"]["costPolicy"] == attempt_context["costPolicy"]
    assert projection["context"]["portabilityProvenance"] == (
        attempt_context["portabilityProvenance"]
    )


def test_run_request_records_retrieval_and_memory_refs_in_attempt_projection() -> None:
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

    attempt_context = request.parameters["metadata"]["moonmind"]["executionContext"]
    projection = request.parameters["metadata"]["moonmind"][
        "stepExecutionManifestProjection"
    ]

    assert attempt_context["retrievalManifestRef"].startswith(
        "artifact://retrieval-manifests/sha256:"
    )
    assert attempt_context["retrievalManifestRef"] == (
        projection["context"]["retrievalManifestRef"]
    )
    assert attempt_context["memoryManifestRef"].startswith(
        "attempt-memory-manifest://sha256:"
    )
    assert projection["context"]["retrievalManifestRef"] == (
        attempt_context["retrievalManifestRef"]
    )
    assert projection["context"]["memoryManifestRef"] == (
        attempt_context["memoryManifestRef"]
    )
    assert "Relevant design summary." not in str(projection)


def test_run_request_records_durable_retrieval_manifest_artifact_metadata() -> None:
    wf = MoonMindRunWorkflow()
    task_payload = {
        **_task_payload(),
        "retrieval": {
            "status": "unavailable",
            "selector": {"reason": "index_offline"},
        },
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

    moonmind_metadata = request.parameters["metadata"]["moonmind"]
    retrieval_artifact = moonmind_metadata["retrievalManifestArtifact"]
    attempt_context = moonmind_metadata["executionContext"]
    projection = moonmind_metadata["stepExecutionManifestProjection"]

    assert retrieval_artifact["artifactRef"] == attempt_context["retrievalManifestRef"]
    assert retrieval_artifact["payload"]["status"] == "unavailable"
    assert retrieval_artifact["payload"]["selector"] == {"reason": "index_offline"}
    assert projection["context"]["retrievalManifestRef"] == (
        retrieval_artifact["artifactRef"]
    )


@pytest.mark.asyncio
async def test_run_request_refreshes_runtime_metadata_after_retrieval_persistence() -> None:
    wf = MoonMindRunWorkflow()
    task_payload = {
        **_task_payload(),
        "retrieval": {
            "query": "execution context",
            "returnedRefs": ["artifact://retrieved-doc"],
        },
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

    original_context = request.parameters["metadata"]["moonmind"]["executionContext"]
    original_digest = original_context["contextBundleDigest"]

    cached_artifact = wf._step_execution_retrieval_manifest_artifacts[
        ("collect-evidence", 1)
    ]
    cached_artifact["persistedArtifactRef"] = "art_retrieval_1"
    refreshed = await wf._request_with_persisted_retrieval_ref(
        request,
        logical_step_id="collect-evidence",
        attempt=1,
    )

    moonmind_metadata = refreshed.parameters["metadata"]["moonmind"]
    refreshed_context = moonmind_metadata["executionContext"]
    assert refreshed_context["retrievalManifestRef"] == "art_retrieval_1"
    assert moonmind_metadata["stepExecutionManifestProjection"]["context"][
        "retrievalManifestRef"
    ] == "art_retrieval_1"
    assert moonmind_metadata["retrievalManifestArtifact"]["persistedArtifactRef"] == (
        "art_retrieval_1"
    )
    assert refreshed.step_execution is not None
    assert refreshed.step_execution.retrieval_manifest_ref == "art_retrieval_1"

    # The context-bundle digest must be recomputed after swapping the retrieval
    # ref, not left stale: it changes and stays self-consistent between the
    # execution context and its manifest projection.
    refreshed_digest = refreshed_context["contextBundleDigest"]
    assert refreshed_digest != original_digest
    assert refreshed_context["contextBundleRef"] == (
        f"execution-context-bundle://{refreshed_digest}"
    )
    assert moonmind_metadata["stepExecutionManifestProjection"]["context"][
        "contextBundleDigest"
    ] == refreshed_digest
    assert moonmind_metadata["stepExecutionManifestProjection"]["context"][
        "contextBundleRef"
    ] == refreshed_context["contextBundleRef"]
    # Re-deriving the digest from the swapped content reproduces it, proving the
    # digest addresses the current bundle rather than the pre-swap content.
    rebuilt = ExecutionContextBundle.model_validate(
        refreshed_context
    ).with_retrieval_manifest_ref("art_retrieval_1")
    assert rebuilt.context_bundle_digest == refreshed_digest


def test_run_request_projects_matched_fix_patterns_into_attempt_memory() -> None:
    signature = extract_error_signature("RuntimeError: qdrant collection missing")
    assert signature is not None
    fix_pattern = FixPattern.from_successful_run(
        signature=signature,
        summary="Bootstrap the Qdrant namespace before indexing.",
        steps=["Run the namespace bootstrap activity before retrieval writes."],
        evidence=EvidenceRun(
            workflowId="successful-workflow-1",
            artifactRefs=["artifact://fix-pattern/evidence"],
            outcome="succeeded",
        ),
    )
    expected_memory = build_memory_manifest(
        [
            {
                "proposalRef": fix_pattern.pattern_ref,
                "state": "accepted_for_run_context",
                "summary": (
                    "Bootstrap the Qdrant namespace before indexing. Steps: "
                    "Run the namespace bootstrap activity before retrieval writes."
                ),
            }
        ]
    )
    wf = MoonMindRunWorkflow()

    with patch(
        "moonmind.workflows.temporal.workflows.run.workflow.info",
        return_value=_workflow_info(),
    ):
        request = wf._build_agent_execution_request(
            node_inputs={"runtime": {"mode": "codex_cli"}},
            node_id="collect-evidence",
            tool_name="codex_cli",
            workflow_parameters={
                "task": {
                    **_task_payload(),
                    "matchedFixPatterns": [
                        fix_pattern.model_dump(by_alias=True, exclude_none=True)
                    ],
                }
            },
        )

    attempt_context = request.parameters["metadata"]["moonmind"]["executionContext"]
    projection = request.parameters["metadata"]["moonmind"][
        "stepExecutionManifestProjection"
    ]

    assert attempt_context["memoryManifestRef"] == expected_memory.memory_manifest_ref
    assert projection["context"]["memoryManifestRef"] == (
        expected_memory.memory_manifest_ref
    )
    assert "artifact://fix-pattern/evidence" not in str(projection)


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
                    "resolvedSourceDesignPath": "docs/Designs/RuntimeTypes.md",
                    "sourceResolution": {
                        "status": "resolved",
                        "selectedPath": "docs/Designs/RuntimeTypes.md",
                    },
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
    assert "docs/Designs/RuntimeTypes.md" in request.instruction_ref
    assert (
        "Do not use provider-native Jira/Atlassian or GitHub connectors"
        in request.instruction_ref
    )


def test_agent_request_includes_trusted_github_previous_outputs_in_instruction_ref() -> None:
    wf = MoonMindRunWorkflow()
    with patch(
        "moonmind.workflows.temporal.workflows.run.workflow.info",
        return_value=_workflow_info(),
    ):
        request = wf._build_agent_execution_request(
            node_inputs={
                "runtime": {"mode": "claude_code"},
                "instructions": "Assess existing implementation state.",
                "previousOutputs": {
                    "trustedSource": "moonmind.github.get_issue",
                    "issue": {
                        "repository": "MoonLadderStudios/MoonMind",
                        "number": 123,
                        "title": "Persist issue briefs",
                    },
                    "presetBrief": (
                        "MoonLadderStudios/MoonMind#123: Persist issue briefs"
                    ),
                    "artifactPath": "artifacts/github-issue-implement-brief.json",
                    "summary": "Loaded GitHub issue preset brief.",
                },
            },
            node_id="assess",
            tool_name="claude_code",
            workflow_parameters={"task": {}},
        )

    assert request.instruction_ref is not None
    assert request.instruction_ref.startswith(
        "Assess existing implementation state."
    )
    assert "MoonMind trusted previous step context:" in request.instruction_ref
    assert "moonmind.github.get_issue" in request.instruction_ref
    assert (
        "MoonLadderStudios/MoonMind#123: Persist issue briefs"
        in request.instruction_ref
    )
    assert "artifacts/github-issue-implement-brief.json" in request.instruction_ref


def test_trusted_issue_context_persists_after_intermediate_step_output() -> None:
    wf = MoonMindRunWorkflow()
    wf._record_trusted_issue_context(
        {
            "trustedSource": "moonmind.jira.get_issue",
            "jiraIssueKey": "MM-657",
            "jiraPresetBrief": "MM-657: Settings HTTP API surface",
        }
    )

    merged = wf._merge_trusted_issue_context({"storyOutput": {"status": "COMPLETED"}})

    assert merged["storyOutput"] == {"status": "COMPLETED"}
    assert merged["trustedSource"] == "moonmind.jira.get_issue"
    assert merged["jiraIssueKey"] == "MM-657"
    assert merged["jiraPresetBrief"] == "MM-657: Settings HTTP API surface"


def test_trusted_issue_context_uses_valid_json_after_truncation() -> None:
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
    assert parsed["contextTruncation"] == {
        "truncated": True,
        "truncatedKeys": ["jiraPresetBrief"],
    }
    assert "recover the full content" in instruction
    assert "MoonMind trusted tool surfaces" in instruction


def test_trusted_issue_context_omits_truncation_disclosure_when_complete() -> None:
    instruction = MoonMindRunWorkflow._trusted_previous_outputs_instruction(
        {
            "trustedSource": "moonmind.jira.get_issue",
            "jiraIssueKey": "MM-657",
            "jiraPresetBrief": "MM-657: Settings HTTP API surface",
        }
    )

    assert instruction is not None
    payload = instruction.split("```json\n", 1)[1].split("\n```", 1)[0]
    parsed = json.loads(payload)
    assert "contextTruncation" not in parsed
    assert "recover the full content" not in instruction


def test_trusted_issue_context_discloses_truncation_in_essential_fallback() -> None:
    instruction = MoonMindRunWorkflow._trusted_previous_outputs_instruction(
        {
            "trustedSource": "moonmind.jira.get_issue",
            "jiraIssueKey": "MM-657",
            "jiraPresetBrief": "A" * 30000,
            "presetBrief": "B" * 30000,
            "jiraStepInstructions": "C" * 30000,
            "summary": "D" * 30000,
            "jiraIssue": {"descriptionText": "E" * 30000},
        }
    )

    assert instruction is not None
    payload = instruction.split("```json\n", 1)[1].split("\n```", 1)[0]
    parsed = json.loads(payload)
    assert parsed["jiraPresetBrief"].endswith("...[truncated]")
    assert len(parsed["jiraPresetBrief"]) == 3000
    truncation = parsed["contextTruncation"]
    assert truncation["truncated"] is True
    assert "jiraPresetBrief" in truncation["truncatedKeys"]
    assert "jiraIssue.descriptionText" not in truncation["truncatedKeys"]
    assert "recover the full content" in instruction


def test_trusted_issue_context_ignores_unconsumed_instruction_aliases() -> None:
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
