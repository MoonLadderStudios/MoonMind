"""Repository save handoffs retain commands with the production SDK runner."""

import json
from datetime import timedelta

import pytest
from temporalio import activity, workflow
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

with workflow.unsafe.imports_passed_through():
    from moonmind.omnigent import workspace_intent
    from moonmind.omnigent.workspace_sources import replace_repository_workspace_source
    from moonmind.schemas.agent_runtime_models import (
        AgentExecutionRequest,
        AgentRunResult,
    )
    from moonmind.workflows.executions import repository_contract
    from moonmind.workflows.temporal.workflows.agent_run import (
        AGENT_RUN_CANONICAL_REPOSITORY_WORKSPACE_PATCH_ID,
        MoonMindAgentRun,
    )
    from moonmind.workflows.temporal.workflows.run import (
        RUN_CANONICAL_REPOSITORY_WORKSPACE_PATCH,
        MoonMindUserWorkflow,
    )


def _request():
    info = workflow.info()
    return AgentExecutionRequest(
        agentKind="managed",
        agentId="codex_cli",
        profileSelector={"tagsAll": ["selected-pool"]},
        correlationId=info.workflow_id,
        idempotencyKey="next-step",
        workspaceSpec={
            "workspaceSource": {
                "kind": "repository",
                "repositoryTarget": {
                    "provider": "git",
                    "connectionRef": "selected-repository",
                    "repository": {"name": "owner/repo"},
                    "branch": {"name": "selected-base"},
                },
            }
        },
        stepExecution={
            "workflowId": info.workflow_id,
            "runId": info.run_id,
            "logicalStepId": "second",
            "executionOrdinal": 1,
            "stepExecutionId": f"{info.workflow_id}:{info.run_id}:second:execution:1",
            "runtimeContextPolicy": "reuse_session_same_epoch",
        },
    )


@activity.defn(name="repository.observe.retained")
async def _retained(payload):
    assert payload["workspaceSpec"]["workspaceSource"]["kind"] == "repository"
    return "retained"


@activity.defn(name="repository.observe.current")
async def _current(payload):
    from moonmind.omnigent.workspace_sources import compile_workspace_source

    source = compile_workspace_source(payload["workspaceSpec"])
    assert (source.kind, source.repository_ref, source.repository_branch) == (
        "checkpoint",
        "owner/repo",
        "selected-base",
    )
    request = AgentExecutionRequest.model_construct(
        workspace_spec=payload["workspaceSpec"], parameters={}
    )
    assert workspace_intent.authored_repository_source(request) == "owner/repo"
    assert repository_contract.authored_repository_access(
        {"workspaceSpec": payload["workspaceSpec"]}
    ) == (
        "selected-repository",
        False,
    )
    return "current"


@workflow.defn
class RepositoryHandoffFixture:
    @workflow.run
    async def run(self, action):
        request = _request()
        if action == "context":
            parent = MoonMindUserWorkflow()
            first = request.model_copy(deep=True)
            first.step_execution.logical_step_id = "first"
            first.step_execution.step_execution_id = (
                first.step_execution.step_execution_id.replace(":second:", ":first:")
            )
            parent._record_shared_runtime_context(
                first,
                {
                    "metadata": {
                        "runtimeSessionContext": {
                            "workflowId": first.step_execution.workflow_id,
                            "runId": first.step_execution.run_id,
                            "stepExecutionId": first.step_execution.step_execution_id,
                            "epoch": "accepted-epoch",
                            "runtimeBindingRef": "omnigent-runtime-binding:sha256:"
                            + "a" * 64,
                        },
                        "dispatchPoolContext": {
                            "executionProfileRef": "selected-profile",
                            "workspaceCheckpointRestoreRef": "artifact://verified-save",
                        },
                    }
                },
            )
            request = parent._bind_shared_runtime_context(request)
        elif action == "retry":
            parent = MoonMindUserWorkflow()
            request = parent._build_agent_execution_request(
                node_inputs={
                    "instructions": "Continue accepted work.",
                    "runtime": {"mode": "codex_cli"},
                },
                node_id="work",
                tool_name="codex_cli",
                workflow_parameters={"workspaceSpec": request.workspace_spec},
                trusted_remediation_checkpoint_restore_ref="artifact://verified-save",
            )
        else:
            child = MoonMindAgentRun()
            child._execution_budget = None
            child.final_result = AgentRunResult(
                metadata={
                    "savedWorkspaceCheckpoint": {
                        "archiveRef": "artifact://verified-save"
                    }
                }
            )
            request = MoonMindUserWorkflow()._bind_shared_runtime_context(request)
            request = child._pool_cooldown_retry_request(request)
        source = request.workspace_spec.get("workspaceSource") or {}
        operation = _retained if source.get("kind") == "repository" else _current
        return await workflow.execute_activity(
            operation,
            request.model_dump(mode="json", by_alias=True),
            start_to_close_timeout=timedelta(seconds=5),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["context", "retry", "cooldown"])
async def test_pre_and_post_repository_handoffs_replay_on_production_runner(
    monkeypatch, action
):
    patched = workflow.patched
    async with await WorkflowEnvironment.start_time_skipping() as environment:
        for enabled in (False, True):
            queue = f"repository-handoff-{action}-{enabled}"
            with monkeypatch.context() as recorded:
                if not enabled:
                    recorded.setattr(
                        workflow,
                        "patched",
                        lambda marker: (
                            False
                            if marker
                            in {
                                RUN_CANONICAL_REPOSITORY_WORKSPACE_PATCH,
                                AGENT_RUN_CANONICAL_REPOSITORY_WORKSPACE_PATCH_ID,
                            }
                            else patched(marker)
                        ),
                    )
                async with Worker(
                    environment.client,
                    task_queue=queue,
                    workflows=[RepositoryHandoffFixture],
                    activities=[_retained, _current],
                    workflow_runner=UnsandboxedWorkflowRunner(),
                    workflow_failure_exception_types=[Exception],
                ):
                    handle = await environment.client.start_workflow(
                        RepositoryHandoffFixture.run,
                        action,
                        id=queue,
                        task_queue=queue,
                        execution_timeout=timedelta(seconds=30),
                    )
                    assert await handle.result() == (
                        "current" if enabled else "retained"
                    )
                    history = await handle.fetch_history()
            commands = [
                event.activity_task_scheduled_event_attributes
                for event in history.events
                if event.HasField("activity_task_scheduled_event_attributes")
            ]
            assert len(commands) == 1
            payload = json.loads(commands[0].input.payloads[0].data)
            assert (
                payload["workspaceSpec"]["workspaceCheckpointRestoreRef"]
                == "artifact://verified-save"
            )
            await Replayer(
                workflows=[RepositoryHandoffFixture],
                workflow_runner=UnsandboxedWorkflowRunner(),
            ).replay_workflow(history)


@workflow.defn
class RepositoryProjectionSandboxFixture:
    @workflow.run
    async def run(self):
        spec = {
            "workspaceSource": {
                "kind": "repository",
                "repository": "owner/repo",
                "branch": "selected-base",
                "connectionRef": "selected-repository",
            }
        }
        replaced = replace_repository_workspace_source(spec, "artifact://verified-save")
        return await workflow.execute_activity(
            _current,
            {"workspaceSpec": replaced},
            start_to_close_timeout=timedelta(seconds=5),
        )


@pytest.mark.asyncio
async def test_repository_projection_executes_and_replays_in_sdk_sandbox():
    async with await WorkflowEnvironment.start_time_skipping() as environment:
        queue = "repository-projection-sandbox"
        async with Worker(
            environment.client,
            task_queue=queue,
            workflows=[RepositoryProjectionSandboxFixture],
            activities=[_current],
            workflow_failure_exception_types=[Exception],
        ):
            handle = await environment.client.start_workflow(
                RepositoryProjectionSandboxFixture.run,
                id=queue,
                task_queue=queue,
                execution_timeout=timedelta(seconds=20),
            )
            assert await handle.result() == "current"
            history = await handle.fetch_history()
        await Replayer(workflows=[RepositoryProjectionSandboxFixture]).replay_workflow(
            history
        )


@activity.defn(name="repository.observe.pinned")
async def _pinned_observe(payload):
    from moonmind.omnigent.workspace_sources import compile_workspace_source

    request = AgentExecutionRequest.model_validate(payload)
    source = compile_workspace_source(request.workspace_spec)
    assert source.repository_commit_sha == "d" * 40
    if request.pinned_repository_workspace_handoff:
        assert source.kind == "checkpoint"
        assert source.checkpoint_ref == "artifact://verified-save"
        return "shared-save"
    assert source.kind == "repository"
    assert "pinnedRepositoryWorkspaceHandoff" not in payload
    return "retained-native"


@workflow.defn
class PinnedRepositoryHandoffFixture:
    @workflow.run
    async def run(self):
        request = _request()
        request.step_execution.runtime_context_policy = "fresh_agent_run"
        request.workspace_spec["workspaceSource"]["repositoryTarget"]["revision"] = {
            "kind": "git_commit", "commitSha": "d" * 40,
        }
        request = MoonMindUserWorkflow()._bind_shared_runtime_context(request)
        if request.pinned_repository_workspace_handoff:
            child = MoonMindAgentRun()
            child._execution_budget = None
            child.final_result = AgentRunResult(metadata={
                "savedWorkspaceCheckpoint": {"archiveRef": "artifact://verified-save"},
            })
            request = child._pool_cooldown_retry_request(request)
        return await workflow.execute_activity(
            _pinned_observe, request.model_dump(mode="json", by_alias=True),
            start_to_close_timeout=timedelta(seconds=5),
        )


@pytest.mark.asyncio
async def test_pinned_repository_owner_boundary_replays_retained_and_current(monkeypatch):
    from moonmind.workflows.temporal.workflows.run import RUN_PINNED_REPOSITORY_WORKSPACE_PATCH

    patched = workflow.patched
    async with await WorkflowEnvironment.start_time_skipping() as environment:
        for enabled in (False, True):
            queue = f"pinned-owner-{enabled}"
            with monkeypatch.context() as recorded:
                if not enabled:
                    recorded.setattr(workflow, "patched", lambda marker: (
                        False if marker == RUN_PINNED_REPOSITORY_WORKSPACE_PATCH else patched(marker)
                    ))
                async with Worker(
                    environment.client, task_queue=queue,
                    workflows=[PinnedRepositoryHandoffFixture], activities=[_pinned_observe],
                    workflow_runner=UnsandboxedWorkflowRunner(),
                    workflow_failure_exception_types=[Exception],
                ):
                    handle = await environment.client.start_workflow(
                        PinnedRepositoryHandoffFixture.run,
                        id=queue, task_queue=queue, execution_timeout=timedelta(seconds=30),
                    )
                    assert await handle.result() == ("shared-save" if enabled else "retained-native")
                    history = await handle.fetch_history()
            await Replayer(
                workflows=[PinnedRepositoryHandoffFixture],
                workflow_runner=UnsandboxedWorkflowRunner(),
            ).replay_workflow(history)
