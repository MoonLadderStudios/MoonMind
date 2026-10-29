"""Trusted briefs cross the parent, artifact service, and managed workspace."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from moonmind.omnigent.workspace_artifacts import WorkspaceArtifactProjectionError
from moonmind.schemas.agent_runtime_models import AgentRunResult, ManagedRunRecord
from moonmind.workflows.temporal.activity_runtime import (
    TemporalAgentRuntimeActivities,
    _write_json_artifact,
)
from moonmind.workflows.temporal.artifacts import (
    ExecutionRef,
    LocalTemporalArtifactStore,
    TemporalArtifactAuthorizationError,
    TemporalArtifactRepository,
    TemporalArtifactService,
)
from moonmind.workflows.temporal.runtime.store import ManagedRunStore
from moonmind.workflows.temporal.workflows.run import MoonMindRunWorkflow
from tests.unit.workflows.temporal.test_activity_runtime import temporal_db


@pytest.mark.asyncio
@pytest.mark.parametrize("reference_field", ["remainingWorkRef", "remaining_work_ref"])
@pytest.mark.parametrize("auth_mode", ["accounts", "oidc", "header", "disabled"])
async def test_separate_remaining_work_survives_publication_and_next_turn(
    tmp_path: Path, monkeypatch, reference_field, auth_mode
):
    monkeypatch.setenv("AUTH_PROVIDER", str(auth_mode or ""))
    root = tmp_path / "agent_jobs"
    workspace = root / "parent-workflow" / "repo"
    verify_path = workspace / "var/artifacts/moonspec-verify/final.json"
    verify_path.parent.mkdir(parents=True)
    monkeypatch.setenv("MOONMIND_AGENT_RUNTIME_STORE", str(root))
    run_store = ManagedRunStore(tmp_path / "runs")
    run_store.save(
        ManagedRunRecord(
            runId="verify-run",
            agentId="codex_cli",
            runtimeId="codex_cli",
            status="completed",
            startedAt=datetime.now(timezone.utc),
            workspacePath=str(workspace),
        )
    )
    remaining_work = {"remainingWork": [{"requirement": "Preserve the candidate"}]}
    async with temporal_db(tmp_path) as sessions:
        async with sessions() as session:
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )
            ref = await _write_json_artifact(
                service,
                principal="system:tool",
                payload=remaining_work,
                execution_ref=ExecutionRef(
                    namespace="default",
                    workflow_id="parent-workflow",
                    run_id="run-1",
                    link_type="output.remaining_work",
                ),
            )
            remaining_ref = f"artifact://{ref.artifact_id}"
            verify_path.write_text(
                json.dumps(
                    {
                        "schemaVersion": "moonspec-verify.issue_brief.v1",
                        "verdict": "ADDITIONAL_WORK_NEEDED",
                        "recommendedNextAction": "reattempt_current_step",
                        "recoverableInCurrentRuntime": True,
                        reference_field: remaining_ref,
                    }
                )
            )
            activities = TemporalAgentRuntimeActivities(
                artifact_service=service,
                run_store=run_store,
            )
            monkeypatch.setattr(activities, "execution_notify_completion", AsyncMock())
            with patch(
                "moonmind.workflows.temporal.activity_runtime.temporal_activity.info",
                return_value=SimpleNamespace(
                    namespace="default",
                    workflow_id="parent-workflow:agent:verify",
                    workflow_run_id="child-run",
                ),
            ):
                result = await activities.agent_runtime_publish_artifacts(
                    AgentRunResult(
                        summary="Completed.",
                        metadata={
                            "agentRunId": "verify-run",
                            "verify_artifact_path": "var/artifacts/moonspec-verify/final.json",
                        },
                    )
                )
            parent = MoonMindRunWorkflow()
            gate = parent._moonspec_verify_gate_result(
                {"moonSpecVerify": result.metadata["moonSpecVerify"]}
            )
            assert gate.remaining_work_ref == remaining_ref
            # The production Run owner builds the Step envelope before the
            # managed adapter forwards the full request to turn preparation.
            with patch(
                "moonmind.workflows.temporal.workflows.run.workflow.info",
                return_value=SimpleNamespace(
                    namespace="default",
                    workflow_id="parent-workflow",
                    run_id="run-1",
                    task_queue="mm.workflow.user.v2",
                ),
            ), patch(
                "moonmind.workflows.temporal.workflows.run.workflow.patched",
                return_value=True,
            ):
                request = parent._build_agent_execution_request(
                    node_inputs={
                        "instructions": "Repair the remaining gaps.",
                        "remainingWorkRef": gate.remaining_work_ref,
                    },
                    node_id="remediate",
                    tool_name="codex_cli",
                )
            assert request.step_execution is not None
            assert request.step_execution.workflow_id == "parent-workflow"
            assert request.parameters["remainingWorkRef"] == remaining_ref
            prepared = await activities.agent_runtime_prepare_turn_instructions(
                {
                    "request": request.model_dump(by_alias=True, exclude_none=True),
                    "workspacePath": str(workspace),
                    "skipSkillMaterialization": True,
                }
            )
            evidence_files = list(
                (workspace.parent / "artifacts/remediation-inputs").glob(
                    "remaining-work-*.json"
                )
            )
            assert len(evidence_files) == 1
            assert json.loads(evidence_files[0].read_bytes()) == remaining_work
            assert str(evidence_files[0]) in prepared


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
                # Creator provenance must not authorize metadata reads when
                # the artifact is linked only to an unrelated workflow.
                principal="workflow:parent-workflow",
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
                with pytest.raises(
                    WorkspaceArtifactProjectionError,
                    match="source artifact metadata is unavailable",
                ) as denied:
                    await activities.agent_runtime_prepare_turn_instructions(payload)
                assert denied.value.code == "WORKSPACE_AUTHORITY_MISMATCH"
                assert isinstance(
                    denied.value.__cause__, TemporalArtifactAuthorizationError
                )
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


@pytest.mark.asyncio
@pytest.mark.parametrize("replace_input", [False, True])
async def test_managed_turn_reconciles_current_inputs_without_resetting_work(
    tmp_path: Path, monkeypatch, replace_input
):
    root = tmp_path / "agent_jobs"
    workspace = root / "parent-workflow" / "repo"
    workspace.mkdir(parents=True)
    candidate = workspace / "candidate.py"
    candidate.write_text("saved candidate")
    restore = workspace / ".moonmind" / "restore" / "saved-input"
    restore.parent.mkdir(parents=True)
    restore.write_text("retained restore input")
    monkeypatch.setenv("MOONMIND_AGENT_RUNTIME_STORE", str(root))
    async with temporal_db(tmp_path) as sessions:
        async with sessions() as session:
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )
            refs = []
            for revision in ("old", "current"):
                ref = await _write_json_artifact(
                    service,
                    principal="system:tool",
                    payload={"revision": revision},
                    execution_ref=ExecutionRef(
                        namespace="default",
                        workflow_id="parent-workflow",
                        run_id="run-1",
                        link_type="input.issue_brief",
                    ),
                )
                refs.append(f"artifact://{ref.artifact_id}")
            activities = TemporalAgentRuntimeActivities(artifact_service=service)
            request = {
                "agentKind": "managed",
                "agentId": "codex_cli",
                "correlationId": "parent-workflow",
                "idempotencyKey": "turn-1",
                "inputRefs": refs[:1],
                "instructionRef": "Use the current attachments.",
            }
            payload = {
                "request": request,
                "workspacePath": str(workspace),
                "skipSkillMaterialization": True,
            }
            await activities.agent_runtime_prepare_turn_instructions(payload)
            attachments = workspace / ".moonmind" / "attachments"
            request["inputRefs"] = refs[1:] if replace_input else []
            request["idempotencyKey"] = "turn-2"
            await activities.agent_runtime_prepare_turn_instructions(
                {**payload, "metadataOnly": True}
            )
            assert [json.loads(p.read_bytes()) for p in attachments.iterdir()] == [
                {"revision": "old"}
            ]
            await activities.agent_runtime_prepare_turn_instructions(payload)
            assert [json.loads(p.read_bytes()) for p in attachments.iterdir()] == (
                [{"revision": "current"}] if replace_input else []
            )
            assert candidate.read_text() == "saved candidate"
            assert restore.read_text() == "retained restore input"
