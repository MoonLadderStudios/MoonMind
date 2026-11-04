"""Replay the verifier artifact -> parent gate -> remediation admission boundary."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from temporalio.testing import ActivityEnvironment

from api_service.db.models import Base
from moonmind.schemas.agent_runtime_models import AgentRunResult, ManagedRunRecord
from moonmind.workflows.agent_skills.agent_skills_activities import (
    AgentSkillsActivities,
)
from moonmind.workflows.temporal.activity_catalog import (
    AGENT_RUNTIME_FLEET,
    build_default_activity_catalog,
)
from moonmind.workflows.temporal.activity_runtime import (
    TemporalAgentRuntimeActivities,
    build_activity_bindings,
)
from moonmind.workflows.temporal.artifacts import (
    LocalTemporalArtifactStore,
    TemporalArtifactRepository,
    TemporalArtifactService,
)
from moonmind.workflows.temporal.remediation_loop import (
    RemediationLoopState,
    capture_remediation_candidate,
)
from moonmind.workflows.temporal.runtime.store import ManagedRunStore
from moonmind.workflows.temporal.runtime.workspace_locators import (
    SandboxWorkspaceRecord,
    SandboxWorkspaceRecordStore,
)
from moonmind.workflows.temporal.workflows import run as run_module
from tests.artifact_access import access_for_test
from tests.integration.reliability.helpers import load_replay

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.integration,
    pytest.mark.reliability_journey,
]


@asynccontextmanager
async def _published_verifier_gate(tmp_path, monkeypatch, *, runtime, payload):
    """Publish a verifier artifact through the real Activity; yield its gate."""

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/artifacts.db")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            workspace = tmp_path / "workspace"
            run_store = ManagedRunStore(tmp_path / "runs")
            metadata = {"agentRunId": "verify"}
            if runtime == "omnigent":
                workspace = tmp_path / "temporal_sandbox" / "verify" / "repo"
                SandboxWorkspaceRecordStore(tmp_path).ensure(
                    SandboxWorkspaceRecord(
                        workspace_id="verify",
                        workflow_id="parent",
                        step_execution_id="parent:run:verify:execution:1",
                        relative_path="repo",
                    )
                )
                metadata = {
                    "correlationId": "parent",
                    "idempotencyKey": "parent:run:verify:execution:1:agent_execute",
                    "workspaceLocator": {
                        "kind": "sandbox",
                        "workspaceId": "verify",
                        "relativePath": "repo",
                    },
                }
            else:
                run_store.save(
                    ManagedRunRecord(
                        runId="verify",
                        agentId=runtime,
                        runtimeId=runtime,
                        status="completed",
                        startedAt=datetime.now(UTC),
                        workspacePath=str(workspace),
                    )
                )
            verify_file = workspace / "artifacts/verify.json"
            verify_file.parent.mkdir(parents=True)
            verify_file.write_text(json.dumps(payload))
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "storage"),
            )
            activities = TemporalAgentRuntimeActivities(
                artifact_service=service,
                run_store=run_store,
                workspace_root=tmp_path,
            )
            monkeypatch.setattr(activities, "execution_notify_completion", AsyncMock())
            bindings = build_activity_bindings(
                build_default_activity_catalog(),
                agent_runtime_activities=activities,
                agent_skills_activities=AgentSkillsActivities(),
                fleets=(AGENT_RUNTIME_FLEET,),
            )
            publish = next(
                b.handler
                for b in bindings
                if b.activity_type == "agent_runtime.publish_artifacts"
            )
            result = await ActivityEnvironment().run(
                publish,
                AgentRunResult(
                    summary="Verification completed.",
                    metadata={
                        **metadata,
                        "verify_artifact_path": "artifacts/verify.json",
                    },
                ),
            )
            # Round-trip the same compact contract the child returns to its parent.
            result = AgentRunResult.model_validate_json(result.model_dump_json())
            yield service, result.metadata["moonSpecVerify"]
    finally:
        await engine.dispose()


def _controller_node(runtime: str, **budgets) -> dict:
    return {
        "id": "controller",
        "inputs": {"runtime": {"mode": runtime}},
        "annotations": {
            "remediationLoop": {
                "loopId": "repair",
                "remediationTool": {
                    "name": "remediate-issue",
                    "inputs": {"instructions": "Fix the gaps."},
                },
                "verificationTool": {
                    "name": "moonspec-verify",
                    "inputs": {"instructions": "Verify the candidate."},
                },
                "workspacePolicy": "continue_from_loop_head",
                "budgets": {"hardMaxAttempts": 6, **budgets},
                "terminalPolicy": {
                    "fullyImplemented": "advance",
                    "additionalWorkNeeded": "continue_when_allowed",
                    "blocked": "stop",
                    "noDetermination": "retry_evidence_or_stop",
                    "failedUnrecoverable": "stop",
                },
                "sideEffectPolicy": "workflow_owned",
                "publicationPolicy": "evaluate_after_terminal",
            }
        },
    }


def _decision_writer(service, decisions):
    async def write_decision(*, name, payload, **_kwargs):
        artifact, _ = await service.put_content_addressed_payload_complete(
            principal="system:agent_runtime",
            payload=json.dumps(payload).encode(),
            content_type="application/json",
            scope="replay-decision",
            metadata_json={"name": name},
        )
        decisions.append(artifact.artifact_id)
        return artifact.artifact_id

    return write_decision


@pytest.mark.parametrize("runtime", ["codex_cli", "claude_code", "omnigent"])
@pytest.mark.parametrize("action", ["needs_human", "blocked"])
@pytest.mark.parametrize("verdict", ["ADDITIONAL_WORK_NEEDED", "NO_DETERMINATION"])
async def test_verifier_stop_survives_publication_and_both_controllers(
    tmp_path, monkeypatch, runtime, action, verdict
):
    replay = load_replay("verifier-remediation-stop-authority", "manifest.json")
    payload = replay["verifierPayload"]
    payload["recommendedNextAction"] = action
    payload["verdict"] = verdict
    async with _published_verifier_gate(
        tmp_path, monkeypatch, runtime=runtime, payload=payload
    ) as (service, projected):
        assert projected["recommendedNextAction"] == action
        assert projected["recoverableInCurrentRuntime"] is False
        assert "remainingWork" not in projected
        _, persisted = await service.read(
            artifact_id=projected["gateResultRef"],
            access=access_for_test("system:agent_runtime"),
        )
        assert json.loads(persisted)["recommendedNextAction"] == action
        assert json.loads(persisted)["remainingWork"] == payload["remainingWork"]
        assert projected["remainingWorkRef"] == projected["gateResultRef"]
        _, remaining_work_bytes = await service.read(
            artifact_id=projected["remainingWorkRef"],
            access=access_for_test("system:agent_runtime"),
        )
        assert (
            json.loads(remaining_work_bytes)["remainingWork"]
            == payload["remainingWork"]
        )

        monkeypatch.setattr(run_module.workflow, "patched", lambda _patch: True)
        monkeypatch.setattr(
            run_module.workflow, "now", lambda: datetime(2026, 9, 8, tzinfo=UTC)
        )
        monkeypatch.setattr(
            run_module.workflow,
            "info",
            lambda: SimpleNamespace(
                task_queue="mm.workflow.user.v2", workflow_id="parent", run_id="run"
            ),
        )
        parent = run_module.MoonMindRunWorkflow()
        parent._initialize_remediation_loop_controller(
            ordered_nodes=[_controller_node(runtime)]
        )
        decisions = []
        monkeypatch.setattr(
            parent, "_write_json_artifact", _decision_writer(service, decisions)
        )
        outputs = {"moonSpecVerify": projected}
        parent._record_moonspec_verify_gate(node_id="verify", outputs=outputs)
        gate = parent._moonspec_verify_gate_result(outputs)
        nodes = []
        admitted = await parent._evaluate_dynamic_remediation_verification(
            ordered_nodes=nodes,
            verdict=gate.verdict,
            gate_result_ref=projected["gateResultRef"],
            remaining_work_ref=gate.remaining_work_ref,
            recommended_next_action=gate.recommended_next_action,
            recoverable_evidence=gate.recoverable_in_current_runtime,
        )
        assert not admitted
        assert nodes == []
        state = RemediationLoopState.model_validate_json(
            parent._remediation_loop_state.model_dump_json()
        )
        assert state.phase == action
        assert state.consumed_budgets.attempts == 0
        assert (
            state.latest_verification_ref == "artifact://" + projected["gateResultRef"]
        )
        _, decision_bytes = await service.read(
            artifact_id=decisions[0], access=access_for_test("system:agent_runtime")
        )
        decision = json.loads(decision_bytes)
        assert decision["reason"] == f"verification_requested_{action}"
        assert decision["remainingWorkRef"] == "artifact://" + gate.remaining_work_ref

        # Even an existing planned successor must not override the stop.
        bounded = parent._bounded_story_loop_continuation_decision(
            logical_step_id="verify",
            gate_result=gate,
            gate_result_ref=projected["gateResultRef"],
            current_index=0,
            ordered_nodes=[
                {"id": "verify", "inputs": {"selectedSkill": "moonspec-verify"}},
                {
                    "id": "repair",
                    "inputs": {
                        "annotations": {"jiraOrchestrateRole": "moonspec-remediation"}
                    },
                },
                {
                    "id": "verify-next",
                    "inputs": {"selectedSkill": "moonspec-verify"},
                },
            ],
        )
        assert bounded["continueLoop"] is False
        assert bounded["state"] == action
        assert bounded["reason"] == decision["reason"]
        assert bounded["remainingWorkRef"] == "artifact://" + gate.remaining_work_ref
        assert parent._blocking_moonspec_gate_reason()
        if verdict == "ADDITIONAL_WORK_NEEDED":
            parent._activate_moonspec_draft_publication(
                "Remaining work", policy="draft_pr_on_additional_work_needed"
            )
            body = parent._moonspec_draft_publication_body_section()
            assert "verifier requested a stop" in body
            assert "budget was exhausted" not in body


@pytest.mark.parametrize("runtime", ["codex_cli", "omnigent"])
async def test_wall_clock_stop_survives_publication_with_candidate_and_evidence(
    tmp_path, monkeypatch, runtime
):
    payload = load_replay("verifier-remediation-stop-authority", "manifest.json")[
        "verifierPayload"
    ]
    # This verifier asks for more remediation; only the loop's clock stops it.
    payload.pop("recommendedNextAction")
    async with _published_verifier_gate(
        tmp_path, monkeypatch, runtime=runtime, payload=payload
    ) as (service, projected):
        clock = [datetime(2026, 11, 1, 9, tzinfo=UTC)]
        monkeypatch.setattr(run_module.workflow, "patched", lambda _patch: True)
        monkeypatch.setattr(run_module.workflow, "now", lambda: clock[0])
        monkeypatch.setattr(
            run_module.workflow,
            "info",
            lambda: SimpleNamespace(
                task_queue="mm.workflow.user.v2", workflow_id="parent", run_id="run"
            ),
        )
        parent = run_module.MoonMindRunWorkflow()
        parent._initialize_remediation_loop_controller(
            ordered_nodes=[_controller_node(runtime, maxWallClockSeconds=7200)]
        )
        decisions = []
        monkeypatch.setattr(
            parent, "_write_json_artifact", _decision_writer(service, decisions)
        )
        outputs = {"moonSpecVerify": projected}
        parent._record_moonspec_verify_gate(node_id="verify", outputs=outputs)
        gate = parent._moonspec_verify_gate_result(outputs)
        assert gate.recommended_next_action not in {"needs_human", "blocked"}
        nodes = []

        async def decide() -> bool:
            return await parent._evaluate_dynamic_remediation_verification(
                ordered_nodes=nodes,
                verdict=gate.verdict,
                gate_result_ref=projected["gateResultRef"],
                remaining_work_ref=gate.remaining_work_ref,
                recommended_next_action=gate.recommended_next_action,
                recoverable_evidence=gate.recoverable_in_current_runtime,
                headless_workspace_spec={"repository": "org/repo", "branch": "work"},
            )

        assert await decide()
        # Attempt 1 remediates until the preset's two-hour loop limit passes.
        parent._remediation_loop_state = capture_remediation_candidate(
            parent._remediation_loop_state
        )
        clock[0] += timedelta(hours=2)
        assert not await decide()

        assert len(nodes) == 2
        state = parent._remediation_loop_state
        assert state.phase == "stopped_remaining_work"
        assert state.consumed_budgets.attempts == 1
        _, decision_bytes = await service.read(
            artifact_id=decisions[-1], access=access_for_test("system:agent_runtime")
        )
        decision = json.loads(decision_bytes)
        assert decision["reason"] == "wall_clock_budget_exhausted"
        assert decision["exhaustedDimension"] == "wall_clock"
        assert decision["gateResultRef"] == "artifact://" + projected["gateResultRef"]
        assert decision["remainingWorkRef"] == "artifact://" + gate.remaining_work_ref
        projection = parent._publish_context["remediationLoop"]
        assert projection["wallClockBudget"]["exhausted"] is True
        assert projection["latestVerificationRef"] == decision["gateResultRef"]
        assert parent._blocking_moonspec_gate_reason()
        parent._activate_moonspec_draft_publication(
            "Remaining work", policy="draft_pr_on_additional_work_needed"
        )
        body = parent._moonspec_draft_publication_body_section()
        assert "wall-clock budget was exhausted" in body
        assert "verifier requested a stop" not in body
