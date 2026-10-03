"""#4644: retired targets cannot enter plan/child dispatch; history stays decodable."""

from types import SimpleNamespace

import pytest
from temporalio import workflow
from temporalio.exceptions import ApplicationError

from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.workflows.temporal.workflows.agent_run import resolve_adapter_metadata
from moonmind.workflows.temporal.workflows.run import MoonMindUserWorkflow


@pytest.mark.asyncio
async def test_removed_external_adapter_fails_without_activity_retries():
    with pytest.raises(ApplicationError) as error:
        await resolve_adapter_metadata("codex_cloud")
    assert error.value.non_retryable
    assert "unknown agent runtime" in str(error.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "parameters",
    [
        {
            "workflow": {
                "instructions": "Saved task",
                "runtime": {"mode": "codex_cloud", "model": "saved-model"},
            }
        },
        {"targetRuntime": "codex_cloud"},
        {
            "workflow": {
                "steps": [
                    {
                        "runtime": {"mode": "codex_cloud"},
                        "instructions": "Preserve step",
                    }
                ]
            }
        },
    ],
)
async def test_saved_schedule_plan_is_rejected_before_any_activity(
    monkeypatch, parameters
):
    calls = []

    async def execute(*args, **kwargs):
        calls.append(args)
        return {}

    monkeypatch.setattr(workflow, "patched", lambda _id: True)
    monkeypatch.setattr(
        workflow, "info", lambda: SimpleNamespace(continued_run_id=None)
    )
    monkeypatch.setattr(workflow, "execute_activity", execute)
    instance = MoonMindUserWorkflow()
    with pytest.raises(ApplicationError) as error:
        await instance._run_planning_stage(
            parameters=parameters, input_ref=None, plan_ref="immutable-old-plan"
        )
    assert error.value.non_retryable
    assert calls == []
    assert parameters  # validation preserves original authored inputs


def test_cloud_history_contract_keeps_original_identity():
    request = AgentExecutionRequest(
        agentKind="external",
        agentId="codex_cloud",
        correlationId="original",
        idempotencyKey="saved",
    )
    assert request.agent_id == "codex_cloud"
    assert request.model_dump(by_alias=True)["agentId"] == "codex_cloud"


def test_cloud_runtime_edit_is_rejected_before_mutating_the_current_request(
    monkeypatch,
):
    """#4644: an indirect session edit cannot retarget a healthy Codex request."""
    from moonmind.workflows.temporal.workflows.agent_run import MoonMindAgentRun

    monkeypatch.setattr(workflow, "patched", lambda _id: True)
    request = AgentExecutionRequest(
        agentKind="managed",
        agentId="codex",
        correlationId="saved-correlation",
        idempotencyKey="saved-request",
        parameters={
            "instructions": "Preserve task",
            "model": "saved-model",
            "effort": "high",
        },
    )
    saved = request.model_dump()
    with pytest.raises(ApplicationError) as error:
        MoonMindAgentRun()._apply_runtime_selection_update(
            request, {"targetRuntime": "codex_cloud", "model": "different"}
        )
    assert error.value.non_retryable
    assert request.model_dump() == saved
