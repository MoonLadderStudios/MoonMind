"""Story handoff validates parent authority; authenticated reads enforce scope."""

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

from temporalio.workflow import ParentInfo
from temporalio.api.common.v1 import WorkflowExecution
from temporalio.api.namespace.v1 import NamespaceInfo
from temporalio.api.workflow.v1 import WorkflowExecutionInfo
from temporalio.api.workflowservice.v1 import (
    DescribeNamespaceResponse,
    DescribeWorkflowExecutionResponse,
)

import pytest

from api_service.services.checkpoint_branch_turn_execution import (
    build_branch_turn_execution_identity,
)
from moonmind.schemas.agent_runtime_models import (
    AgentExecutionRequest,
    AgentRunResult,
    ManagedRunRecord,
)
from moonmind.workflows.temporal import worker_runtime
from moonmind.workflows.temporal.activity_runtime import (
    TemporalActivityRuntimeError,
    TemporalAgentRuntimeActivities,
)
from moonmind.workflows.temporal.artifacts import (
    TemporalArtifactAuthorizationError,
    TemporalArtifactRepository,
    TemporalArtifactService,
)
from moonmind.workflows.temporal.runtime.store import ManagedRunStore
from moonmind.workflows.temporal.workflows import agent_run
from tests.unit.omnigent.test_admitted_input_authority import (  # noqa: F401
    artifacts,
    auth_mode,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("auth_mode", ["accounts", "oidc", "header", "disabled"])
@pytest.mark.parametrize("scenario", [
    "current_history", "checkpoint_branch_turn", "retained_checkpoint_branch_turn",
    "retained_history", "forged_retained_parent",
    "missing_namespace", "foreign_namespace", "parent_run_mismatch",
    "child_workflow_mismatch", "child_run_mismatch", "describe_unavailable", "missing_output", "unsafe_output",
])
async def test_published_story_preserves_parent_authority_and_auth_read_scope(
    artifacts, tmp_path, monkeypatch, scenario, caplog, auth_mode,  # noqa: F811
):
    parent, child = "story-parent", "story-parent:agent:breakdown"
    semantic_run_id = "parent-run"
    if scenario in {"checkpoint_branch_turn", "retained_checkpoint_branch_turn"}:
        identity = build_branch_turn_execution_identity(
            branch_id="story-branch", branch_turn_id="story-turn",
            logical_step_id="breakdown", ordinal=1,
        )
        parent, child = identity.workflow_id, identity.agent_run_workflow_id
        semantic_run_id = identity.semantic_run_id
    request = AgentExecutionRequest.model_validate({
        "agentKind": "external", "agentId": "omnigent",
        "correlationId": parent, "idempotencyKey": "breakdown",
        "stepExecution": {
            "schemaVersion": "v1", "workflowId": parent, "runId": semantic_run_id,
            "logicalStepId": "breakdown", "executionOrdinal": 1,
            "stepExecutionId": f"{parent}:{semantic_run_id}:breakdown:execution:1",
            "runtimeContextPolicy": "fresh_agent_run",
        },
    })
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    story_bytes = b'{"stories":[{"title":"Preserve durable story input"}]}'
    if scenario != "missing_output":
        (workspace / "stories.json").write_bytes(story_bytes)
        (workspace / "stories.md").write_text("# Durable story input")
    if scenario == "unsafe_output":
        (workspace / "stories.json").unlink()
        (workspace / "stories.json").symlink_to(workspace / "stories.md")
    runs = ManagedRunStore(tmp_path / "runs")
    runs.save(ManagedRunRecord(
        runId="story-run", agentId="codex_cli", runtimeId="codex_cli",
        status="completed", startedAt=datetime.now(UTC), workspacePath=str(workspace),
    ))
    monkeypatch.setattr(
        agent_run.workflow, "patched",
        lambda _patch: scenario in {"current_history", "checkpoint_branch_turn"}
    )
    monkeypatch.setattr(agent_run.workflow, "info", lambda: SimpleNamespace(
        workflow_id=child, run_id="child-run", task_queue="mm.workflow.user.v2",
        namespace="default",
        parent=ParentInfo(namespace="default", workflow_id=parent, run_id="parent-run"),
    ))
    forged = scenario in {"current_history", "forged_retained_parent"}
    result = agent_run.MoonMindAgentRun()._enrich_result_metadata(
        request=request, include_parent_execution=True,
        result=AgentRunResult(metadata={
            "agentRunId": "story-run", "storyBreakdownPath": "stories.json",
            "storyBreakdownMarkdownPath": "stories.md",
            "parentWorkflowId": "forged-parent" if forged else parent,
            "parentRunId": "forged-run" if forged else semantic_run_id,
        }),
    )
    assert result.metadata["parentWorkflowId"] == (
        "forged-parent" if scenario == "forged_retained_parent" else parent
    )
    monkeypatch.setattr(worker_runtime.temporalio.activity, "info", lambda: SimpleNamespace(
        namespace="default", workflow_id=child, workflow_run_id="child-run",
    ))
    workflow_service = SimpleNamespace(
        describe_workflow_execution=AsyncMock(return_value=DescribeWorkflowExecutionResponse(
            workflow_execution_info=WorkflowExecutionInfo(
                execution=WorkflowExecution(
                    workflow_id="foreign-child" if scenario == "child_workflow_mismatch" else child,
                    run_id="foreign-child-run" if scenario == "child_run_mismatch" else "child-run",
                ),
                parent_execution=WorkflowExecution(
                    workflow_id=parent,
                    run_id="foreign-parent-run" if scenario == "parent_run_mismatch" else "parent-run",
                ),
                parent_namespace_id=(
                    "" if scenario == "missing_namespace" else
                    "foreign-namespace" if scenario == "foreign_namespace" else "namespace-id"
                ),
            ),
        )),
        describe_namespace=AsyncMock(return_value=DescribeNamespaceResponse(
            namespace_info=NamespaceInfo(id="namespace-id"),
        )),
    )
    if scenario in {"checkpoint_branch_turn", "retained_checkpoint_branch_turn"}:
        from temporalio.converter import DataConverter
        from tests.unit.workflows.temporal.test_source_brief_artifact_authority import (
            _recorded_server,
        )

        recorded, _ = _recorded_server(
            workflow_service.describe_workflow_execution.return_value,
            launch_request=request,
        )
        workflow_service.get_workflow_execution_history = recorded.get_workflow_execution_history
    if scenario == "describe_unavailable":
        workflow_service.describe_workflow_execution.side_effect = RuntimeError("Temporal unavailable")
    monkeypatch.setattr(worker_runtime.temporalio.activity, "client", lambda: SimpleNamespace(
        namespace="default", workflow_service=workflow_service,
        data_converter=DataConverter.default if scenario in {"checkpoint_branch_turn", "retained_checkpoint_branch_turn"} else None,
    ))
    async with artifacts() as session:
        service = TemporalArtifactService(TemporalArtifactRepository(session))
        publisher = TemporalAgentRuntimeActivities(artifact_service=service, run_store=runs)
        monkeypatch.setattr(publisher, "execution_notify_completion", AsyncMock(return_value={}))
        if scenario == "describe_unavailable":
            instruction, _ = await service.create(principal=f"workflow:{parent}")
            await service.write_complete(
                artifact_id=instruction.artifact_id, principal=f"workflow:{parent}",
                payload=b"Use the admitted story output", content_type="text/plain",
            )
            result.metadata["instructionRef"] = instruction.artifact_id
            quota_before = await TemporalArtifactRepository(session).quota_usage_for_scope(
                "system:agent_runtime"
            )
            route = agent_run.DEFAULT_ACTIVITY_CATALOG.resolve_activity("agent_runtime.publish_artifacts")
            options = agent_run.MoonMindAgentRun._execute_kwargs_for_route(route)
            assert options["retry_policy"].maximum_attempts == 3
            # Lookup exhaustion reaches the existing Activity retry owner.
            with pytest.raises(TemporalActivityRuntimeError, match="authority.*unavailable"):
                await publisher.agent_runtime_publish_artifacts(result)
            assert await service.list_for_execution(
                namespace="default", workflow_id=child, run_id="child-run",
                principal="system:agent_runtime",
            ) == []
            assert await TemporalArtifactRepository(session).quota_usage_for_scope(
                "system:agent_runtime"
            ) == quota_before
            workflow_service.describe_workflow_execution.side_effect = None
            workflow_service.describe_workflow_execution.reset_mock()
        published = await publisher.agent_runtime_publish_artifacts(result)
        if scenario in {"missing_output", "unsafe_output"}:
            assert "storyBreakdownArtifactRef" not in published.metadata
            assert "storyBreakdownMarkdownArtifactRef" not in published.metadata
            workflow_service.describe_workflow_execution.assert_not_awaited()
            return
        if scenario == "describe_unavailable":
            assert len(await service.list_for_execution(
                namespace="default", workflow_id=child, run_id="child-run",
                principal="system:agent_runtime", link_type="input.instructions",
            )) == 1
        ref = published.metadata["storyBreakdownArtifactRef"]
        assert published.metadata["storyBreakdownMarkdownArtifactRef"]
        workflow_service.describe_workflow_execution.assert_awaited_once()
        row = await TemporalArtifactRepository(session).get_artifact(ref)
        assert row.created_by_principal == "system:agent_runtime"
        linked_workflows = {
            link.workflow_id
            for link in await TemporalArtifactRepository(session).list_links(ref)
        }
        assert child in linked_workflows
        if scenario in {"checkpoint_branch_turn", "retained_checkpoint_branch_turn"}:
            parent_links = [
                link for link in await TemporalArtifactRepository(session).list_links(ref)
                if link.workflow_id == parent
            ]
            assert parent_links
            assert {link.run_id for link in parent_links} == {semantic_run_id}
            assert request.step_execution.run_id == semantic_run_id
        assert not {"sibling-parent", "forged-parent"} & linked_workflows
        if scenario in {"current_history", "checkpoint_branch_turn", "retained_checkpoint_branch_turn",
            "retained_history", "describe_unavailable"}:
            assert parent in linked_workflows
        else:
            assert parent not in linked_workflows
            assert "parent handoff authority could not be verified" in caplog.text

    @asynccontextmanager
    async def database():
        async with artifacts() as session:
            yield session

    captured = {}
    register = worker_runtime.register_story_output_tool_handlers

    def capture(dispatcher, **kwargs):
        captured["reader"] = kwargs["artifact_reader"]
        return register(dispatcher, **kwargs)

    monkeypatch.setattr(worker_runtime, "get_async_session_context", database)
    monkeypatch.setattr(worker_runtime, "register_story_output_tool_handlers", capture)
    resources, _handlers = await worker_runtime._build_runtime_activities(
        SimpleNamespace(fleet="artifacts", activity_types=[])
    )
    try:
        # The executing native tool's real Temporal context supplies scope.
        if auth_mode != "disabled":
            for workflow_id in ("sibling-parent", "forged-parent"):
                monkeypatch.setattr(worker_runtime.temporalio.activity, "info", lambda: SimpleNamespace(
                    workflow_id=workflow_id,
                ))
                with pytest.raises(TemporalArtifactAuthorizationError):
                    await captured["reader"](ref)
        monkeypatch.setattr(worker_runtime.temporalio.activity, "info", lambda: SimpleNamespace(
            workflow_id=parent,
        ))
        if auth_mode == "disabled" or scenario in {
            "current_history", "checkpoint_branch_turn", "retained_checkpoint_branch_turn",
            "retained_history", "describe_unavailable"
        }:
            assert await captured["reader"](ref) == story_bytes
        else:
            with pytest.raises(TemporalArtifactAuthorizationError):
                await captured["reader"](ref)
        # An unverifiable handoff preserves the child's completed artifact.
        monkeypatch.setattr(worker_runtime.temporalio.activity, "info", lambda: SimpleNamespace(
            workflow_id=child,
        ))
        assert await captured["reader"](ref) == story_bytes
        request = workflow_service.describe_workflow_execution.call_args.args[0]
        assert request.namespace == "default"
        assert request.execution.workflow_id == child
        assert request.execution.run_id == "child-run"
    finally:
        await resources.aclose()
