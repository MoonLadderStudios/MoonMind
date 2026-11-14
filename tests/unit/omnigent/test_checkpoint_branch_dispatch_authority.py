"""Checkpoint branches retain their admitted plan before any runtime dispatch."""

from unittest.mock import AsyncMock

import pytest

from moonmind.schemas.agent_runtime_models import AgentExecutionRequest, AgentRunResult
from moonmind.workflows.temporal.activities import omnigent_activities as activities
from tests.unit.omnigent.test_oauth_profile_lifecycle import _checkpoint


def branch_request():
    checkpoint = _checkpoint().model_copy(
        update={
            "execution_plan_ref": "omnigent-execution-plan:sha256:" + "a" * 64,
            "runtime_binding_ref": "omnigent-runtime-binding:sha256:" + "b" * 64,
            "runtime_binding_revision": 1,
            "runtime_binding_fencing_generation": 1,
        }
    )
    return (
        AgentExecutionRequest(
            agentKind="external",
            agentId="omnigent",
            executionProfileRef=checkpoint.provider_profile_id,
            correlationId="branch",
            idempotencyKey="new-branch-turn",
            workspaceSpec={"checkoutCommit": checkpoint.baseline_commit},
            parameters={
                "executionPlanRef": checkpoint.execution_plan_ref,
                "maxBudgetUsd": 2.5,
            },
            checkpointRecovery={
                "recoveryAction": "branch_required",
                "omnigentCheckpoint": checkpoint.model_dump(by_alias=True, mode="json"),
            },
        ),
        checkpoint,
    )


def test_branch_accepts_matching_generic_plan_reference():
    request, checkpoint = branch_request()
    parsed, _ = activities._checkpoint_branch_from_request(request)
    assert parsed == checkpoint


@pytest.mark.asyncio
@pytest.mark.parametrize("credential_generation", [3, 4])
async def test_branch_restore_is_bound_before_generic_dispatch(
    monkeypatch, credential_generation
):
    request, checkpoint = branch_request()
    monkeypatch.setattr(
        activities,
        "_resolve_live_recovery_authority",
        AsyncMock(
            return_value={"current_credential_generation": credential_generation}
        ),
    )
    dispatch = AsyncMock(return_value=AgentRunResult(summary="done"))
    monkeypatch.setattr(activities, "_try_generic_realizer_dispatch", dispatch)
    if credential_generation != checkpoint.credential_generation:
        with pytest.raises(ValueError, match="credential generation"):
            await activities._omnigent_execute_activity(request)
        dispatch.assert_not_awaited()
        return
    await activities._omnigent_execute_activity(request)
    delivered = dispatch.await_args.args[0]
    assert (
        delivered.workspace_spec["workspaceCheckpointRestoreRef"]
        == checkpoint.workspace_checkpoint_ref
    )
    assert delivered.parameters["checkpointRestore"]["mode"] == "branch"
    assert delivered.parameters["executionPlanRef"] == checkpoint.execution_plan_ref
    assert delivered.parameters["maxBudgetUsd"] == 2.5
    assert delivered.idempotency_key == request.idempotency_key
    assert checkpoint.head_ref in delivered.input_refs


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["legacy", "specialized"])
async def test_retained_branch_dispatch_keeps_recorded_route(monkeypatch, route):
    from contextlib import asynccontextmanager
    from types import SimpleNamespace

    from moonmind.omnigent.harness_platform.execution_plan import (
        create_execution_plan_envelope,
    )
    from tests.unit.omnigent.test_generic_platform_production_services import (
        _exact_plan,
    )

    request, checkpoint = branch_request()
    delivered = []

    async def execute(bound, *_args):
        delivered.append(bound)
        return AgentRunResult(summary="retained route completed")

    monkeypatch.setattr(
        activities,
        "_resolve_live_recovery_authority",
        AsyncMock(
            return_value={
                "current_credential_generation": checkpoint.credential_generation
            }
        ),
    )
    gateway = SimpleNamespace(admit_execution_plan_inputs=AsyncMock())
    monkeypatch.setattr(
        "moonmind.omnigent.bridge_artifacts.TemporalOmnigentArtifactGateway",
        lambda *_args: gateway,
    )
    if route == "legacy":
        checkpoint = checkpoint.model_copy(
            update={
                "execution_plan_ref": None,
                "runtime_binding_ref": None,
                "runtime_binding_revision": None,
                "runtime_binding_fencing_generation": None,
            }
        )
        request = request.model_copy(
            update={
                "parameters": {"maxBudgetUsd": 2.5},
                "checkpoint_recovery": {
                    "recoveryAction": "branch_required",
                    "omnigentCheckpoint": checkpoint.model_dump(
                        by_alias=True, mode="json"
                    ),
                },
            }
        )

        @asynccontextmanager
        async def pool():
            yield SimpleNamespace()

        monkeypatch.setattr(
            "moonmind.workflows.adapters.omnigent_client.pooled_http_client", pool
        )
        monkeypatch.setattr(
            "moonmind.omnigent.profile_bound_execution.OmnigentProfileBoundExecutionCoordinator",
            lambda **_kwargs: SimpleNamespace(execute=execute),
        )
    else:
        payload = _exact_plan("gpt-5.4").payload.model_dump(by_alias=True, mode="json")
        payload["executionRealizerRef"] = "codex-profile-bound@1"
        payload["resolvedSkills"] = {}
        plan = create_execution_plan_envelope(payload)
        checkpoint = checkpoint.model_copy(update={"execution_plan_ref": plan.planRef})
        request = request.model_copy(
            update={
                "parameters": {"executionPlanRef": plan.planRef, "maxBudgetUsd": 2.5},
                "checkpoint_recovery": {
                    "recoveryAction": "branch_required",
                    "omnigentCheckpoint": checkpoint.model_dump(
                        by_alias=True, mode="json"
                    ),
                },
            }
        )
        monkeypatch.setattr(
            "moonmind.omnigent.harness_platform.stores.DbExecutionPlanStore",
            lambda *_args: SimpleNamespace(load=AsyncMock(return_value=plan)),
        )

        def require(ref):
            assert ref == "codex-profile-bound@1"
            return SimpleNamespace(execute=execute)

        monkeypatch.setattr(
            "moonmind.omnigent.realizers.registry.get_default_registry",
            lambda: SimpleNamespace(require=require),
        )

    result = await activities._omnigent_execute_activity(request)
    assert result.failure_class is None, result
    assert len(delivered) == 1
    assert (
        delivered[0].workspace_spec["workspaceCheckpointRestoreRef"]
        == checkpoint.workspace_checkpoint_ref
    )
    assert delivered[0].idempotency_key == "new-branch-turn"
    assert delivered[0].parameters["maxBudgetUsd"] == 2.5


@pytest.mark.asyncio
async def test_cancelled_branch_validation_never_dispatches_successor(monkeypatch):
    import asyncio

    request, _ = branch_request()
    monkeypatch.setattr(
        activities,
        "_resolve_live_recovery_authority",
        AsyncMock(side_effect=asyncio.CancelledError()),
    )
    dispatch = AsyncMock()
    monkeypatch.setattr(activities, "_try_generic_realizer_dispatch", dispatch)
    with pytest.raises(asyncio.CancelledError):
        await activities._omnigent_execute_activity(request)
    dispatch.assert_not_awaited()
