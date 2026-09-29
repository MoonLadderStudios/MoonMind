"""Trusted briefs cross the parent, artifact service, and managed workspace."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from moonmind.workflows.temporal.activity_runtime import (
    TemporalAgentRuntimeActivities,
    _write_json_artifact,
)
from moonmind.workflows.temporal.artifacts import (
    ExecutionRef,
    LocalTemporalArtifactStore,
    TemporalArtifactRepository,
    TemporalArtifactService,
)
from moonmind.workflows.temporal.workflows.run import MoonMindRunWorkflow
from tests.unit.workflows.temporal.test_activity_runtime import temporal_db


@pytest.mark.parametrize("new_history", [False, True])
def test_managed_handoff_declares_durable_brief_with_replay_compatibility(new_history):
    parent = MoonMindRunWorkflow()
    parent._assessment_context = {"briefArtifactRef": "art_original_brief"}
    with patch(
        "moonmind.workflows.temporal.workflows.run.workflow.patched",
        side_effect=lambda name: (
            new_history if name == "run-managed-handoff-attachments-v1" else True
        ),
    ):
        refs = parent._append_durable_handoff_attachment_refs([], agent_kind="managed")
    assert refs == (["artifact://art_original_brief"] if new_history else [])


@pytest.mark.asyncio
@pytest.mark.parametrize("linked_workflow", ["parent-workflow", "unrelated-workflow"])
async def test_managed_turn_stages_exact_brief_and_preserves_candidate(
    tmp_path: Path, monkeypatch, linked_workflow
):
    root = tmp_path / "agent_jobs"
    workspace = root / "parent-workflow" / "repo"
    workspace.mkdir(parents=True)
    candidate = workspace / "candidate.py"
    candidate.write_text("saved work\n")
    monkeypatch.setenv("MOONMIND_AGENT_RUNTIME_STORE", str(root))
    brief = {
        "trustedSource": "moonmind.github.get_issue",
        "issue": {"number": 2739, "body": "complete original scope"},
    }
    async with temporal_db(tmp_path) as sessions:
        async with sessions() as session:
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )
            ref = await _write_json_artifact(
                service,
                principal="system:tool",
                payload=brief,
                execution_ref=ExecutionRef(
                    namespace="default",
                    workflow_id=linked_workflow,
                    run_id="run-1",
                    link_type="input.issue_brief",
                ),
            )
            activities = TemporalAgentRuntimeActivities(artifact_service=service)
            request = {
                "agentKind": "managed",
                "agentId": "codex_cli",
                "correlationId": "parent-workflow",
                "idempotencyKey": "turn-1",
                "inputRefs": [f"artifact://{ref.artifact_id}"],
                "instructionRef": "Assess the original brief in .moonmind/attachments.",
            }
            payload = {
                "request": request,
                "workspacePath": str(workspace),
                "skipSkillMaterialization": True,
            }
            if linked_workflow != "parent-workflow":
                with pytest.raises(Exception, match="workflow"):
                    await activities.agent_runtime_prepare_turn_instructions(payload)
                assert not list(workspace.glob(".moonmind/attachments/*"))
            else:
                # Cold launch metadata and subsequent turns use the same owner;
                # retry restores missing input bytes without resetting candidate work.
                await activities.agent_runtime_prepare_turn_instructions(
                    {**payload, "metadataOnly": True}
                )
                assert not (workspace / ".moonmind" / "attachments").exists()
                await activities.agent_runtime_prepare_turn_instructions(payload)
                paths = list((workspace / ".moonmind" / "attachments").iterdir())
                assert len(paths) == 1
                assert json.loads(paths[0].read_bytes()) == brief
                paths[0].unlink()
                prepared = await activities.agent_runtime_prepare_turn_instructions(
                    payload
                )
                assert json.loads(paths[0].read_bytes()) == brief
                assert ".moonmind/attachments" in prepared
            assert candidate.read_text() == "saved work\n"


@pytest.mark.parametrize("new_history", [False, True])
def test_preserved_draft_defers_merge_but_retains_required_report(new_history):
    parent = MoonMindRunWorkflow()
    parent._moonspec_draft_publication_reason = "Required verification is unavailable."
    parent._publish_status = "published"
    parent._pull_request_url = "https://github.com/org/repo/pull/7"
    parameters = {"publishMode": "pr", "mergeAutomation": {"enabled": True}}
    with patch(
        "moonmind.workflows.temporal.workflows.run.workflow.patched",
        return_value=new_history,
    ):
        reason = parent._missing_required_outcome_reason(
            parameters=parameters, publish_mode="pr"
        )
        assert reason == (
            None if new_history else "merge automation requested but PR was not merged"
        )
        if new_history:
            parameters["reportOutput"] = {"enabled": True}
            assert (
                parent._missing_required_outcome_reason(
                    parameters=parameters, publish_mode="pr"
                )
                == "reportOutput requested but no final report was created"
            )
