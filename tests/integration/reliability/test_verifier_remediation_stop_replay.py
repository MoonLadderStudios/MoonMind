"""Replay the verifier artifact -> parent gate -> remediation admission boundary."""

from __future__ import annotations

import json
from datetime import UTC, datetime
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
from moonmind.workflows.temporal.remediation_loop import RemediationLoopState
from moonmind.workflows.temporal.runtime.store import ManagedRunStore
from moonmind.workflows.temporal.runtime.workspace_locators import (
    SandboxWorkspaceRecord,
    SandboxWorkspaceRecordStore,
)
from moonmind.workflows.temporal.workflows import run as run_module
from tests.integration.reliability.helpers import load_replay

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.integration,
    pytest.mark.integration_ci,
    pytest.mark.reliability_journey,
]


@pytest.mark.parametrize("runtime", ["codex_cli", "claude_code", "omnigent"])
@pytest.mark.parametrize("action", ["needs_human", "blocked"])
async def test_verifier_stop_survives_publication_and_both_controllers(
    tmp_path, monkeypatch, runtime, action
):
    replay = load_replay("verifier-remediation-stop-authority", "manifest.json")
    payload = replay["verifierPayload"]
    payload["recommendedNextAction"] = action
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
            projected = result.metadata["moonSpecVerify"]
            assert projected["recommendedNextAction"] == action
            assert projected["recoverableInCurrentRuntime"] is False
            assert "remainingWork" not in projected
            _, persisted = await service.read(
                artifact_id=projected["gateResultRef"],
                principal="system:agent_runtime",
            )
            assert json.loads(persisted)["recommendedNextAction"] == action
            assert json.loads(persisted)["remainingWork"] == payload["remainingWork"]

            monkeypatch.setattr(run_module.workflow, "patched", lambda _patch: True)
            monkeypatch.setattr(
                run_module.workflow, "now", lambda: datetime(2026, 9, 8, tzinfo=UTC)
            )
            monkeypatch.setattr(
                run_module.workflow,
                "info",
                lambda: SimpleNamespace(workflow_id="parent", run_id="run"),
            )
            parent = run_module.MoonMindRunWorkflow()
            parent._initialize_remediation_loop_controller(
                ordered_nodes=[
                    {
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
                                "budgets": {"hardMaxAttempts": 6},
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
                ]
            )
            decisions = []

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

            monkeypatch.setattr(parent, "_write_json_artifact", write_decision)
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
                state.latest_verification_ref
                == "artifact://" + projected["gateResultRef"]
            )
            _, decision_bytes = await service.read(
                artifact_id=decisions[0], principal="system:agent_runtime"
            )
            decision = json.loads(decision_bytes)
            assert decision["reason"] == f"verification_requested_{action}"
            assert (
                decision["remainingWorkRef"] == "artifact://" + gate.remaining_work_ref
            )

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
                            "annotations": {
                                "jiraOrchestrateRole": "moonspec-remediation"
                            }
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
            assert parent._blocking_moonspec_gate_reason()
            parent._activate_moonspec_draft_publication(
                "Remaining work", policy="draft_pr_on_additional_work_needed"
            )
            body = parent._moonspec_draft_publication_body_section()
            assert "verifier requested a stop" in body
            assert "budget was exhausted" not in body
    finally:
        await engine.dispose()
