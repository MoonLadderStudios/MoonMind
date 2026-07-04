"""Hermetic integration coverage for Omnigent checkpoint branch launch."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from unittest.mock import patch

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import (
    Base,
    TemporalExecutionCanonicalRecord,
    TemporalWorkflowType,
)
from api_service.services.checkpoint_branch_service import (
    CheckpointBranchService,
    build_branch_turn_launch_idempotency_key,
    build_omnigent_checkpoint_branch_idempotency_key,
)
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.workflows.temporal.workflows.run import (
    RUN_CHECKPOINT_BRANCH_TURN_CONTEXT_PATCH,
    RUN_OMNIGENT_CHECKPOINT_BRANCH_TURN_REQUEST_PATCH,
    MoonMindRunWorkflow,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]


@pytest.fixture()
def branch_turn_payload() -> dict[str, Any]:
    return {
        "branchId": "cbr-omnigent-it",
        "branchTurnId": "cbt-omnigent-it",
        "sourceWorkflowId": "wf-omnigent-it",
        "sourceRunId": "run-source",
        "sourceLogicalStepId": "implement",
        "sourceExecutionOrdinal": 2,
        "sourceCheckpointRef": "artifact://checkpoint/source",
        "sourceCheckpointDigest": "sha256:" + "a" * 64,
        "instructionArtifactRef": "artifact://instructions/turn-omnigent",
        "instructionDigest": "sha256:" + "b" * 64,
        "workspacePolicy": "fresh_branch_from_source",
        "runtimeContextPolicy": "fresh_agent_run",
        "gitWorkBranch": "mm/wf-omnigent-it/implement/cbr-omnigent-it",
        "priorEvidenceRefs": ["artifact://omnigent/prior-session"],
        "omnigentPriorSessionRefs": ["artifact://omnigent/prior-session"],
    }


async def _session_factory(tmp_path: Any) -> sessionmaker[AsyncSession]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/branch.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    factory.engine = engine  # type: ignore[attr-defined]
    return factory


def _branch_create_payload() -> dict[str, Any]:
    return {
        "branchId": "cbr-omnigent-it",
        "branchTurnId": "cbt-omnigent-it",
        "source": {
            "workflowId": "wf-omnigent-it",
            "runId": "run-source",
            "logicalStepId": "implement",
            "sourceExecutionOrdinal": 2,
            "checkpointBoundary": "after_execution",
            "checkpointRef": "artifact://checkpoint/source",
            "checkpointDigest": "sha256:" + "a" * 64,
        },
        "label": "Omnigent checkpoint branch",
        "workspacePolicy": "fresh_branch_from_source",
        "runtimeContextPolicy": "fresh_agent_run",
        "gitRepository": "repo://moonmind",
        "gitBaseBranch": "feature/source",
        "gitBaseCommit": "abc123",
        "gitWorkBranch": "mm/wf-omnigent-it/implement/cbr-omnigent-it",
        "instructionRef": "artifact://instructions/turn-omnigent",
        "instructionDigest": "sha256:" + "b" * 64,
        "idempotencyKey": "wf-omnigent-it:cbr-omnigent-it:create",
    }


def _build_omnigent_branch_request(
    branch_turn: Mapping[str, Any],
) -> AgentExecutionRequest:
    wf = MoonMindRunWorkflow()

    class MockInfo:
        namespace = "default"
        workflow_id = "wf-omnigent-it"
        run_id = "run-branch"

    with patch(
        "moonmind.workflows.temporal.workflows.run.workflow.info",
        return_value=MockInfo(),
    ), patch(
        "moonmind.workflows.temporal.workflows.run.workflow.patched",
        side_effect=lambda patch_id: patch_id
        in {
            RUN_CHECKPOINT_BRANCH_TURN_CONTEXT_PATCH,
            RUN_OMNIGENT_CHECKPOINT_BRANCH_TURN_REQUEST_PATCH,
        },
    ):
        return wf._build_agent_execution_request(
            node_inputs={
                "runtime": {
                    "mode": "omnigent",
                    "workspaceSpec": {"repository": "repo://moonmind"},
                    "omnigent": {
                        "endpointRef": "default",
                        "prompt": {"text": "stale inline prompt"},
                        "capture": {"workspaceFiles": True},
                    },
                    "metadata": {"moonmind": {"checkpointBranchTurn": branch_turn}},
                },
            },
            node_id="implement",
            tool_name="omnigent",
            attempt_reason="runtime_recovered",
        )


async def test_omnigent_checkpoint_branch_launch_contract_is_composed(
    tmp_path,
    branch_turn_payload: dict[str, Any],
) -> None:
    factory = await _session_factory(tmp_path)
    async with factory() as session:
        session.add(
            TemporalExecutionCanonicalRecord(
                workflow_id="wf-omnigent-it",
                run_id="run-source",
                workflow_type=TemporalWorkflowType.USER_WORKFLOW,
                entry="api",
            )
        )
        await session.commit()
        service = CheckpointBranchService(session)
        graph = await service.create_branch_graph(_branch_create_payload())

        request = _build_omnigent_branch_request(branch_turn_payload)
        turn_id = graph.turns[0].branch_turn_id
        launch_key = build_branch_turn_launch_idempotency_key(
            workflow_id="wf-omnigent-it",
            branch_id="cbr-omnigent-it",
            branch_turn_id=turn_id,
        )
        omnigent_key = build_omnigent_checkpoint_branch_idempotency_key(
            workflow_id="wf-omnigent-it",
            branch_id="cbr-omnigent-it",
            branch_turn_id=turn_id,
        )

        launched = await service.launch_turn(
            workflow_id="wf-omnigent-it",
            branch_id="cbr-omnigent-it",
            branch_turn_id=turn_id,
            context_bundle_ref="artifact://context/cbr-omnigent-it",
            step_execution_manifest_ref="artifact://manifest/cbr-omnigent-it",
            checkpoint_ref="artifact://checkpoint/cbr-omnigent-it",
            diagnostics_ref="artifact://diagnostics/cbr-omnigent-it",
            agent_request_ref="artifact://agent-request/cbr-omnigent-it",
            agent_result_ref="artifact://agent-result/cbr-omnigent-it",
            created_step_execution_id=request.step_execution.step_execution_id,
            provider_session_id="omnigent-session-child",
            idempotency_key=launch_key,
            omnigent_prior_session_refs=["artifact://omnigent/prior-session"],
            omnigent_capture_artifact_refs={
                "stdout": "artifact://omnigent/capture/stdout",
                "diagnostics": "artifact://omnigent/capture/diagnostics",
            },
        )
        await session.commit()

        stored_graph = await service.read_branch_graph(
            workflow_id="wf-omnigent-it",
            branch_id="cbr-omnigent-it",
        )

    await factory.engine.dispose()  # type: ignore[attr-defined]

    omnigent = request.parameters["omnigent"]
    binding = omnigent["checkpointBranch"]
    assert request.agent_id == "omnigent"
    assert request.idempotency_key == omnigent_key
    assert binding["mode"] == "fresh_omnigent_session_from_checkpoint"
    assert binding["idempotencyKey"] == omnigent_key
    assert binding["sourceCheckpointRef"] == "artifact://checkpoint/source"
    assert binding["sourceCheckpointDigest"] == "sha256:" + "a" * 64
    assert binding["instructionRef"] == "artifact://instructions/turn-omnigent"
    assert binding["priorSessionRefs"] == ["artifact://omnigent/prior-session"]
    assert omnigent["prompt"] == {
        "instructionRef": "artifact://instructions/turn-omnigent"
    }
    assert request.workspace_spec["branch"] == (
        "mm/wf-omnigent-it/implement/cbr-omnigent-it"
    )
    assert request.workspace_spec["startingBranch"] == (
        "mm/wf-omnigent-it/implement/cbr-omnigent-it"
    )
    assert request.step_execution.runtime_context_policy == "fresh_agent_run"
    assert launched.provider_session_id == "omnigent-session-child"
    assert launched.diagnostics["omnigentCheckpointBranch"]["idempotencyKey"] == (
        omnigent_key
    )
    artifact_by_kind = {
        artifact.artifact_kind: artifact.artifact_ref
        for artifact in stored_graph.artifacts
    }
    assert artifact_by_kind["runtime.branch_turn.agent_request.json"] == (
        "artifact://agent-request/cbr-omnigent-it"
    )
    assert artifact_by_kind["runtime.branch_turn.omnigent_prior_session.1"] == (
        "artifact://omnigent/prior-session"
    )
    assert artifact_by_kind["runtime.branch_turn.omnigent_capture.stdout"] == (
        "artifact://omnigent/capture/stdout"
    )


async def test_omnigent_checkpoint_branch_launch_rejects_invalid_checkpoint_before_launch(
    tmp_path,
    branch_turn_payload: dict[str, Any],
) -> None:
    factory = await _session_factory(tmp_path)
    async with factory() as session:
        session.add(
            TemporalExecutionCanonicalRecord(
                workflow_id="wf-omnigent-it",
                run_id="run-source",
                workflow_type=TemporalWorkflowType.USER_WORKFLOW,
                entry="api",
            )
        )
        await session.commit()
        service = CheckpointBranchService(session)
        await service.create_branch_graph(_branch_create_payload())

        invalid_turn = dict(branch_turn_payload)
        invalid_turn["sourceCheckpointDigest"] = "not-a-digest"
        with pytest.raises(ValueError, match="checkpointDigest must be a sha256"):
            _build_omnigent_branch_request(invalid_turn)

        stored_graph = await service.read_branch_graph(
            workflow_id="wf-omnigent-it",
            branch_id="cbr-omnigent-it",
        )

    await factory.engine.dispose()  # type: ignore[attr-defined]

    assert stored_graph.turns[0].created_step_execution_id is None
    assert {
        artifact.artifact_kind for artifact in stored_graph.artifacts
    }.isdisjoint(
        {
            "runtime.branch_turn.context_bundle.json",
            "runtime.branch_turn.agent_request.json",
            "runtime.branch_turn.agent_result.json",
            "runtime.branch_turn.omnigent_prior_session.1",
        }
    )


async def test_omnigent_same_session_continuation_fails_without_lifecycle_activities(
    branch_turn_payload: dict[str, Any],
) -> None:
    request = _build_omnigent_branch_request(branch_turn_payload)
    payload = request.model_dump(by_alias=True)
    payload["idempotencyKey"] = "wf-omnigent-it:cbr-omnigent-it:continuation"
    payload["stepExecution"]["runtimeContextPolicy"] = "external_provider_continuation"
    payload["stepExecution"]["externalProviderContinuation"] = {
        "checkpointEvidence": {"available": True}
    }
    payload["parameters"]["omnigent"].pop("checkpointBranch", None)
    payload["parameters"]["omnigent"]["continuation"] = {
        "sourceSessionId": "omnigent-session-parent",
        "continuationMode": "send_message",
    }

    with pytest.raises(ValidationError, match="typed lifecycle activities"):
        AgentExecutionRequest.model_validate(payload)
