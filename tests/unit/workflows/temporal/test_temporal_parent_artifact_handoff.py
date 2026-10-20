"""Semantic Step Execution identity stays separate from Temporal parent ancestry."""

from datetime import timedelta
from unittest.mock import patch

import pytest
from temporalio import activity, workflow
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

from api_service.services.checkpoint_branch_turn_execution import (
    build_branch_turn_execution_identity,
)
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest, AgentRunResult
from moonmind.workflows.temporal.activity_runtime import _verified_artifact_parent_scope
from moonmind.workflows.temporal.workflows.agent_run import (
    TEMPORAL_PARENT_ARTIFACT_HANDOFF_PATCH_ID,
    MoonMindAgentRun,
)


@activity.defn(name="test.parent_artifact_scope")
async def _parent_scope(metadata: dict) -> dict:
    scope = await _verified_artifact_parent_scope(
        info=activity.info(),
        parent_workflow_id=metadata["parentWorkflowId"],
        parent_run_id=metadata["parentRunId"],
        parent_temporal_run_id=metadata.get("parentTemporalRunId"),
    )
    return {**metadata, "verifiedScope": scope}


async def _publish(request_payload: dict, *, legacy: bool = False) -> dict:
    request = AgentExecutionRequest.model_validate(request_payload)
    original = request.step_execution.model_dump()
    real_patched = workflow.patched
    with patch.object(
        workflow,
        "patched",
        side_effect=lambda marker: (
            False if legacy and marker == TEMPORAL_PARENT_ARTIFACT_HANDOFF_PATCH_ID
            else real_patched(marker)
        ),
    ):
        result = MoonMindAgentRun()._enrich_result_metadata(
            request=request,
            result=AgentRunResult(),
            include_parent_execution=True,
        )
    assert request.step_execution.model_dump() == original
    return await workflow.execute_activity(
        _parent_scope,
        result.metadata,
        start_to_close_timeout=timedelta(seconds=30),
    )


@workflow.defn(name="MoonMind.AgentRun")
class _LegacyChild:
    @workflow.run
    async def run(self, request: dict) -> dict:
        return await _publish(request, legacy=True)


@workflow.defn(name="MoonMind.AgentRun")
class _CurrentChild:
    @workflow.run
    async def run(self, request: dict) -> dict:
        return await _publish(request)


@workflow.defn
class _BranchParent:
    @workflow.run
    async def run(self, request: dict) -> dict:
        if request.pop("ordinaryIdentity", False):
            step = request["stepExecution"]
            step["runId"] = workflow.info().run_id
            step["stepExecutionId"] = (
                f"{workflow.info().workflow_id}:{workflow.info().run_id}:verify:execution:1"
            )
        return await workflow.execute_child_workflow(
            "MoonMind.AgentRun", request,
            id=f"{workflow.info().workflow_id}:agent",
        )


@pytest.mark.asyncio
@pytest.mark.temporal_boundary
@pytest.mark.parametrize("semantic_identity", [False, True])
async def test_parent_uuid_and_semantic_identity_replay_without_ancestry_relaxation(
    semantic_identity, tmp_path,
):
    histories = []
    async with await WorkflowEnvironment.start_local() as env:
        for generation, child in (("retained", _LegacyChild), ("current", _CurrentChild)):
            identity = build_branch_turn_execution_identity(
                branch_id="branch", branch_turn_id=f"{generation}-{semantic_identity}",
                logical_step_id="verify", ordinal=1,
            )
            queue = f"parent-artifact-{generation}-{semantic_identity}"
            # The parent workflow constructs ordinary Temporal identities at
            # execution time; branch turns already own a semantic ID at admission.
            request = {
                "agentKind": "external", "agentId": "omnigent",
                "correlationId": identity.workflow_id, "idempotencyKey": "verify",
                "stepExecution": {
                    "workflowId": identity.workflow_id,
                    "runId": identity.semantic_run_id,
                    "logicalStepId": identity.logical_step_id,
                    "executionOrdinal": 1, "stepExecutionId": identity.step_execution_id,
                    "runtimeContextPolicy": "fresh_agent_run",
                },
            }
            async with Worker(
                env.client, task_queue=queue,
                workflows=[_BranchParent, child], activities=[_parent_scope],
                workflow_runner=UnsandboxedWorkflowRunner(),
            ):
                if not semantic_identity:
                    # Use the same parent harness but materialize its actual run
                    # ID before dispatch, as ordinary Run requests do.
                    request["ordinaryIdentity"] = True
                handle = await env.client.start_workflow(
                    _BranchParent.run, request, id=identity.workflow_id, task_queue=queue,
                )
                result = await handle.result()
                actual_run_id = handle.first_execution_run_id
                expected_semantic = identity.semantic_run_id if semantic_identity else actual_run_id
                assert result["parentRunId"] == expected_semantic
                if generation == "current":
                    assert result["parentTemporalRunId"] == actual_run_id
                    assert result["verifiedScope"] == f"workflow:{identity.workflow_id}"
                else:
                    assert "parentTemporalRunId" not in result
                    assert result["verifiedScope"] == f"workflow:{identity.workflow_id}"
                child_handle = env.client.get_workflow_handle(f"{identity.workflow_id}:agent")
                history = await child_handle.fetch_history()
                (tmp_path / f"{generation}-child.json").write_text(history.to_json())
                histories.append(history)
                if semantic_identity and generation == "retained":
                    from temporalio.api.workflowservice.v1 import ResetWorkflowExecutionRequest

                    finish_id = next(
                        event.event_id for event in history.events
                        if event.HasField("workflow_task_completed_event_attributes")
                    )
                    original_run = history.events[0].workflow_execution_started_event_attributes.original_execution_run_id
                    reset = await env.client.workflow_service.reset_workflow_execution(
                        ResetWorkflowExecutionRequest(
                            namespace=env.client.namespace,
                            workflow_execution={"workflow_id": history.workflow_id, "run_id": original_run},
                            reason="Verify retained branch parent after reset",
                            workflow_task_finish_event_id=finish_id,
                            request_id="retained-branch-parent-reset",
                        ),
                    )
                    reset_handle = env.client.get_workflow_handle(history.workflow_id, run_id=reset.run_id)
                    reset_result = await reset_handle.result()
                    assert reset_result["verifiedScope"] == f"workflow:{identity.workflow_id}"
                    assert reset_result["parentRunId"] == identity.semantic_run_id
                    reset_history = await reset_handle.fetch_history()
                    assert reset_history.events[0].workflow_execution_started_event_attributes.original_execution_run_id != reset.run_id
                    (tmp_path / "retained-reset-child.json").write_text(reset_history.to_json())
                    histories.append(reset_history)
    replayer = Replayer(
        workflows=[_CurrentChild], workflow_runner=UnsandboxedWorkflowRunner(),
    )
    for history in histories:
        await replayer.replay_workflow(history)


@pytest.mark.asyncio
@pytest.mark.parametrize("case", [
    "own", "forged_semantic_run", "forged_step", "wrong_parent", "wrong_parent_run",
    "wrong_namespace", "wrong_child_run", "missing_history", "unavailable",
    "wrong_workflow_type", "missing_step", "explicit_temporal_mismatch", "reset_origin",
])
@pytest.mark.parametrize("explicit_uuid", [False, True])
async def test_retained_semantic_parent_requires_exact_recorded_step(monkeypatch, case, explicit_uuid):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from temporalio.api.history.v1 import History, HistoryEvent
    from temporalio.api.workflowservice.v1 import (
        DescribeNamespaceResponse, DescribeWorkflowExecutionResponse,
        GetWorkflowExecutionHistoryResponse,
    )
    from temporalio.converter import DataConverter

    from moonmind.workflows.temporal.activity_runtime import ArtifactParentAuthorityUnavailable

    identity = build_branch_turn_execution_identity(
        branch_id="branch", branch_turn_id="retained", logical_step_id="verify", ordinal=1,
    )
    request = {
        "agentKind": "external", "agentId": "omnigent", "correlationId": identity.workflow_id,
        "idempotencyKey": "verify", "stepExecution": {
            "workflowId": identity.workflow_id, "runId": identity.semantic_run_id,
            "logicalStepId": "verify", "executionOrdinal": 1,
            "stepExecutionId": identity.step_execution_id,
            "runtimeContextPolicy": "fresh_agent_run",
        },
    }
    if case == "forged_semantic_run":
        request["stepExecution"].update(
            runId="branch-turn-unrelated",
            stepExecutionId=f"{identity.workflow_id}:branch-turn-unrelated:verify:execution:1",
        )
    if case == "forged_step":
        request["stepExecution"]["stepExecutionId"] = "unrelated-step"
    if case == "missing_step":
        request.pop("stepExecution")
    converter = DataConverter.default
    event = HistoryEvent(event_id=1, workflow_execution_started_event_attributes={
        "workflow_type": {"name": "OtherWorkflow" if case == "wrong_workflow_type" else "MoonMind.AgentRun"},
        "original_execution_run_id": "pre-reset-run" if case == "reset_origin" else "child-run",
        "parent_workflow_namespace": "other" if case == "wrong_namespace" else "default",
        "parent_workflow_namespace_id": "namespace-default",
        "parent_workflow_execution": {
            "workflow_id": "unrelated" if case == "wrong_parent" else identity.workflow_id,
            "run_id": "other-parent-run" if case == "wrong_parent_run" else "parent-uuid",
        },
        "input": {"payloads": await converter.encode([request])},
    })
    service = SimpleNamespace(
        describe_workflow_execution=AsyncMock(return_value=DescribeWorkflowExecutionResponse(
            workflow_execution_info={
                "execution": {
                    "workflow_id": identity.agent_run_workflow_id,
                    "run_id": "wrong-child-run" if case == "wrong_child_run" else "child-run",
                },
                "parent_execution": {"workflow_id": identity.workflow_id, "run_id": "parent-uuid"},
                "parent_namespace_id": "namespace-default",
            },
        )),
        describe_namespace=AsyncMock(return_value=DescribeNamespaceResponse(
            namespace_info={"id": "namespace-default"},
        )),
        get_workflow_execution_history=AsyncMock(return_value=GetWorkflowExecutionHistoryResponse(
            history=History(events=[] if case == "missing_history" else [event]),
        )),
    )
    if case == "unavailable":
        service.get_workflow_execution_history.side_effect = RuntimeError("history unavailable")
    monkeypatch.setattr(activity, "client", lambda: SimpleNamespace(
        namespace="default", workflow_service=service, data_converter=converter,
    ))
    async def lookup():
        return await _verified_artifact_parent_scope(
            info=SimpleNamespace(namespace="default", workflow_id=identity.agent_run_workflow_id,
                                 workflow_run_id="child-run"),
            parent_workflow_id=identity.workflow_id, parent_run_id=identity.semantic_run_id,
            parent_temporal_run_id=(
                "wrong-temporal-run" if case == "explicit_temporal_mismatch"
                else "parent-uuid" if explicit_uuid else None
            ),
        )
    if case == "unavailable":
        with pytest.raises(ArtifactParentAuthorityUnavailable):
            await lookup()
    else:
        assert await lookup() == (
            f"workflow:{identity.workflow_id}" if case in {"own", "reset_origin"} else None
        )
    if case == "explicit_temporal_mismatch":
        service.get_workflow_execution_history.assert_not_awaited()
    if case == "own":
        history_request = service.get_workflow_execution_history.call_args.args[0]
        assert history_request.maximum_page_size == 1
        assert history_request.execution.workflow_id == identity.agent_run_workflow_id
        assert history_request.execution.run_id == "child-run"


@pytest.mark.parametrize("parent_case", ["own", "different_workflow", "different_namespace", "none"])
def test_temporal_parent_metadata_comes_only_from_workflow_history(monkeypatch, parent_case):
    from types import SimpleNamespace
    from temporalio.workflow import ParentInfo

    request = AgentExecutionRequest.model_validate({
        "agentKind": "external", "agentId": "omnigent", "correlationId": "parent",
        "idempotencyKey": "verify", "stepExecution": {
            "workflowId": "parent", "runId": "semantic-run", "logicalStepId": "verify",
            "executionOrdinal": 1, "stepExecutionId": "parent:semantic-run:verify:execution:1",
            "runtimeContextPolicy": "fresh_agent_run",
        },
    })
    monkeypatch.setattr(workflow, "patched", lambda _: True)
    parent = None if parent_case == "none" else ParentInfo(
        namespace="other" if parent_case == "different_namespace" else "default",
        workflow_id="other" if parent_case == "different_workflow" else "parent",
        run_id="actual-parent-uuid",
    )
    monkeypatch.setattr(workflow, "info", lambda: SimpleNamespace(
        namespace="default", workflow_id="child", run_id="child-uuid", parent=parent,
    ))
    result = MoonMindAgentRun()._enrich_result_metadata(
        request=request, include_parent_execution=True,
        result=AgentRunResult(metadata={"parentTemporalRunId": "provider-forged"}),
    )
    assert result.metadata["parentRunId"] == "semantic-run"
    if parent_case == "own":
        assert result.metadata["parentTemporalRunId"] == "actual-parent-uuid"
    else:
        assert "parentTemporalRunId" not in result.metadata
