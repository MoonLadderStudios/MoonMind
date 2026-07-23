from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pydantic import ValidationError
from temporalio import activity, workflow
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from moonmind.schemas.agent_runtime_models import (
    AgentExecutionRequest,
    AgentRunHandle,
    AgentRunResult,
    AgentRunStatus,
)
from moonmind.schemas.temporal_activity_models import (
    AcceptedRepositoryEvidence,
    AgentRuntimeFetchResultInput,
    ExternalAgentRunInput,
)
from moonmind.workflows.temporal.data_converter import MOONMIND_TEMPORAL_DATA_CONVERTER
from moonmind.workflows.temporal import typed_execution as typed_execution_module
from moonmind.workflows.temporal.typed_execution import (
    execute_external_cancel_activity,
    execute_external_fetch_result_activity,
    execute_external_start_activity,
    execute_external_status_activity,
    execute_external_streaming_activity,
    execute_typed_activity,
)

def test_external_agent_run_input_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        ExternalAgentRunInput.model_validate(
            {"runId": "run-1", "providerPayload": {"raw": True}}
        )

def test_external_agent_run_input_validates_legacy_alias_at_boundary() -> None:
    request = ExternalAgentRunInput.model_validate({"external_id": "ext-1"})

    assert request.run_id == "ext-1"
    assert request.model_dump(mode="json", by_alias=True) == {"runId": "ext-1"}

def test_agent_runtime_fetch_result_input_rejects_unsupported_publish_mode() -> None:
    with pytest.raises(ValidationError):
        AgentRuntimeFetchResultInput.model_validate(
            {"runId": "managed-1", "publishMode": "side-channel"}
        )

    request = AgentRuntimeFetchResultInput.model_validate(
        {"runId": "managed-1", "publishMode": "auto"}
    )
    assert request.publish_mode == "auto"

def test_agent_runtime_fetch_result_input_normalizes_parent_and_fetch_fields() -> None:
    request = AgentRuntimeFetchResultInput.model_validate(
        {
            "runId": " managed-1 ",
            "agentId": " codex ",
            "commitMessage": " Use typed payloads ",
            "targetBranch": " main ",
            "headBranch": "   ",
        }
    )

    assert request.run_id == "managed-1"
    assert request.agent_id == "codex"
    assert request.commit_message == "Use typed payloads"
    assert request.target_branch == "main"
    assert request.head_branch is None


def test_accepted_repository_evidence_rejects_inconsistent_push_state() -> None:
    with pytest.raises(ValidationError, match="requires commits over base"):
        AcceptedRepositoryEvidence(
            pushStatus="pushed",
            branch="partial-work",
            baseBranch="main",
            headSha="abc123",
            commitsAheadOfBase=0,
            repositoryChanged=True,
        )

    with pytest.raises(ValidationError, match="repositoryChanged disagree"):
        AcceptedRepositoryEvidence(
            pushStatus="no_commits",
            branch="partial-work",
            baseBranch="main",
            headSha="abc123",
            commitsAheadOfBase=0,
            repositoryChanged=True,
        )


@pytest.mark.asyncio
async def test_external_lifecycle_helper_accepts_provider_neutral_activity_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, object]] = []

    async def fake_execute_typed_activity(
        activity: str,
        arg: object,
        **_kwargs: object,
    ) -> dict[str, object]:
        calls.append((activity, arg))
        assert isinstance(arg, ExternalAgentRunInput)
        return {
            "runId": arg.run_id,
            "agentKind": "external",
            "agentId": "future_provider",
            "status": "running",
        }

    monkeypatch.setattr(
        typed_execution_module,
        "execute_typed_activity",
        fake_execute_typed_activity,
    )

    result = await execute_external_status_activity(
        "integration.future_provider.status",
        ExternalAgentRunInput(runId="future-run-1"),
    )

    assert calls == [
        (
            "integration.future_provider.status",
            ExternalAgentRunInput(runId="future-run-1"),
        )
    ]
    assert result.agent_id == "future_provider"
    assert result.status == "running"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("helper", "arg", "payload", "expected_type"),
    [
        (
            execute_external_start_activity,
            AgentExecutionRequest(
                agentKind="external",
                agentId="future_provider",
                correlationId="corr-1",
                idempotencyKey="idem-1",
                instructionRef="Run it",
                workspaceSpec={},
            ),
            {
                "runId": "future-run-1",
                "agentKind": "external",
                "agentId": "future_provider",
                "status": "running",
                "startedAt": datetime(2026, 1, 1, tzinfo=UTC),
            },
            AgentRunHandle,
        ),
        (
            execute_external_cancel_activity,
            ExternalAgentRunInput(runId="future-run-1"),
            {
                "runId": "future-run-1",
                "agentKind": "external",
                "agentId": "future_provider",
                "status": "canceled",
            },
            AgentRunStatus,
        ),
        (
            execute_external_fetch_result_activity,
            ExternalAgentRunInput(runId="future-run-1"),
            {"summary": "done", "metadata": {"provider": "future_provider"}},
            AgentRunResult,
        ),
        (
            execute_external_streaming_activity,
            AgentExecutionRequest(
                agentKind="external",
                agentId="future_provider",
                correlationId="corr-1",
                idempotencyKey="idem-1",
                instructionRef="Run it",
                workspaceSpec={},
            ),
            {"summary": "streamed", "metadata": {"provider": "future_provider"}},
            AgentRunResult,
        ),
    ],
)
async def test_external_lifecycle_helpers_convert_raw_dict_results(
    monkeypatch: pytest.MonkeyPatch,
    helper: Any,
    arg: object,
    payload: dict[str, object],
    expected_type: type[object],
) -> None:
    async def fake_execute_typed_activity(
        _activity: str,
        _arg: object,
        **_kwargs: object,
    ) -> dict[str, object]:
        return payload

    monkeypatch.setattr(
        typed_execution_module,
        "execute_typed_activity",
        fake_execute_typed_activity,
    )

    result = await helper("integration.future_provider.execute", arg)

    assert isinstance(result, expected_type)

@activity.defn(name="typed.boundary.status")
async def _typed_status_activity(request: ExternalAgentRunInput) -> AgentRunStatus:
    assert isinstance(request, ExternalAgentRunInput)
    return AgentRunStatus(
        runId=request.run_id,
        agentKind="external",
        agentId="jules",
        status="completed",
        observedAt=datetime.now(tz=UTC),
    )

@workflow.defn(name="TypedActivityBoundaryWorkflow")
class _TypedActivityBoundaryWorkflow:
    @workflow.run
    async def run(self, run_id: str) -> AgentRunStatus:
        return await execute_typed_activity(
            "typed.boundary.status",
            ExternalAgentRunInput(runId=run_id),
            start_to_close_timeout=timedelta(seconds=30),
        )

@pytest.mark.asyncio
async def test_typed_activity_request_round_trips_through_temporal_worker() -> None:
    async with await WorkflowEnvironment.start_time_skipping(
        data_converter=MOONMIND_TEMPORAL_DATA_CONVERTER
    ) as env:
        async with Worker(
            env.client,
            task_queue="typed-boundary-test",
            workflows=[_TypedActivityBoundaryWorkflow],
            activities=[_typed_status_activity],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            result = await env.client.execute_workflow(
                _TypedActivityBoundaryWorkflow.run,
                "round-trip-1",
                id="typed-boundary-round-trip",
                task_queue="typed-boundary-test",
            )

    assert isinstance(result, AgentRunStatus)
    assert result.run_id == "round-trip-1"
    assert result.status == "completed"
