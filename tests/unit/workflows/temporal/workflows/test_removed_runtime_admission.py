"""Retired targets cannot enter plan/child dispatch; history stays decodable.

Codex Cloud and Jules were removed. Saved schedules, presets, plans, and
recorded requests may still name them, so admission must reject those
references fail-fast and non-retryably instead of retrying or silently
retargeting them.
"""

from types import SimpleNamespace

import pytest
from temporalio import workflow
from temporalio.exceptions import ApplicationError

from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.workflows.temporal.workflows.agent_run import resolve_adapter_metadata
from moonmind.workflows.temporal.workflows.run import MoonMindUserWorkflow

REMOVED_RUNTIMES = ("codex_cloud", "jules", "jules_api")


@pytest.mark.asyncio
@pytest.mark.parametrize("runtime_id", REMOVED_RUNTIMES)
async def test_removed_external_adapter_fails_without_activity_retries(runtime_id):
    with pytest.raises(ApplicationError) as error:
        await resolve_adapter_metadata(runtime_id)
    assert error.value.non_retryable
    assert "unknown agent runtime" in str(error.value)


def _saved_parameter_shapes(runtime_id: str) -> list[dict]:
    return [
        {
            "workflow": {
                "instructions": "Saved task",
                "runtime": {"mode": runtime_id, "model": "saved-model"},
            }
        },
        {"targetRuntime": runtime_id},
        {
            "workflow": {
                "steps": [
                    {
                        "runtime": {"mode": runtime_id},
                        "instructions": "Preserve step",
                    }
                ]
            }
        },
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "parameters",
    [
        parameters
        for runtime_id in REMOVED_RUNTIMES
        for parameters in _saved_parameter_shapes(runtime_id)
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


@pytest.mark.parametrize("runtime_id", REMOVED_RUNTIMES)
def test_removed_runtime_history_contract_keeps_original_identity(runtime_id):
    request = AgentExecutionRequest(
        agentKind="external",
        agentId=runtime_id,
        correlationId="original",
        idempotencyKey="saved",
    )
    assert request.agent_id == runtime_id
    assert request.model_dump(by_alias=True)["agentId"] == runtime_id


@pytest.mark.parametrize("runtime_id", REMOVED_RUNTIMES)
def test_removed_runtime_edit_is_rejected_before_mutating_the_current_request(
    monkeypatch, runtime_id
):
    """An indirect session edit cannot retarget a healthy Codex request."""
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
            request, {"targetRuntime": runtime_id, "model": "different"}
        )
    assert error.value.non_retryable
    assert request.model_dump() == saved
