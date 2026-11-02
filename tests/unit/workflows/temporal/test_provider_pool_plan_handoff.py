"""A selected pool grant can bind one plan without spending another slot."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from tests.unit.workflows.temporal.test_provider_profile_incremental_lease_persistence import (
    _LeaseRow,
    _run_table_action,
)
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.workflows.temporal.workflows.agent_run import MoonMindAgentRun

NOW = datetime.now(timezone.utc)


@pytest.mark.asyncio
async def test_selected_pool_rejects_conflicting_recorded_skill_manifests():
    from api_service.services.native_pool_execution_plan import (
        prepare_selected_pool_plan,
    )

    request = AgentExecutionRequest(
        agentKind="managed",
        agentId="codex_cli",
        executionProfileRef="selected-account",
        correlationId="parent",
        idempotencyKey="request-1",
        instructionRef="Preserve the accepted task.",
        resolvedSkillsetRef="artifact:request-skills",
        stepExecution={
            "workflowId": "parent",
            "runId": "parent-run",
            "logicalStepId": "work",
            "executionOrdinal": 1,
            "stepExecutionId": "parent:parent-run:work:execution:1",
            "runtimeContextPolicy": "fresh_agent_run",
            "resolvedSkillsetRef": "artifact:different-step-skills",
        },
    )
    transport = SimpleNamespace(
        describe_workflow=AsyncMock(
            side_effect=AssertionError("must reject before owner lookup")
        )
    )
    with pytest.raises(ValueError, match="conflicting recorded Skill"):
        await prepare_selected_pool_plan(
            Mock(),
            runtime_id="codex_cli",
            payload={
                "request": request.model_dump(mode="json", by_alias=True),
                "owner_id": "agent-pool",
                "runId": "agent-run-1",
                "profile_id": "selected-account",
            },
            client=transport,
        )
    transport.describe_workflow.assert_not_awaited()


def grant(**changes):
    values = dict(
        lease_id="agent-pool",
        workflow_id="agent-pool",
        owner_id="agent-pool",
        profile_id="selected-account",
        runtime_id="opencode",
        owner_kind="workflow",
        owner_is_workflow=True,
        purpose="execution_direct",
        lease_state="held",
        fencing_generation=7,
        scope_generation=3,
        capacity_scope_ref="pool-scope",
        credential_generation=2,
        expires_at=NOW + timedelta(hours=1),
        safe_metadata_json={"runId": "agent-run-1"},
    )
    values.update(changes)
    return _LeaseRow(**values)


def binding(**changes):
    values = dict(
        lease_id="agent-pool",
        owner_id="agent-pool",
        profile_id="selected-account",
        fencing_generation=7,
        runId="agent-run-1",
        credential_generation=2,
        executionPlanRef="omnigent-execution-plan:sha256:" + "a" * 64,
        stepExecutionId="workflow:step:attempt-1",
        idempotencyKey="step-request-1",
    )
    values.update(changes)
    return values


@pytest.mark.asyncio
async def test_selected_pool_plan_binds_once_without_changing_capacity_or_siblings():
    selected, sibling = grant(), grant(lease_id="sibling", owner_id="sibling")
    before = (
        selected.fencing_generation,
        selected.scope_generation,
        selected.expires_at,
    )
    result = await _run_table_action("bind_plan", [binding()], [selected, sibling])
    assert result.result == {"bound": True, "duplicate": False}
    assert selected.execution_plan_ref == binding()["executionPlanRef"]
    assert selected.step_execution_id == binding()["stepExecutionId"]
    assert selected.idempotency_key == binding()["idempotencyKey"]
    assert (
        selected.fencing_generation,
        selected.scope_generation,
        selected.expires_at,
    ) == before
    assert result.deleted == []
    assert set(result.table) == {"agent-pool", "sibling"}
    duplicate = await _run_table_action("bind_plan", [binding()], [selected, sibling])
    assert duplicate.result == {"bound": True, "duplicate": True}
    assert duplicate.deleted == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        {"fencing_generation": 6},
        {"owner_id": "another-owner"},
        {"profile_id": "another-account"},
        {"runId": "another-run"},
        {"credential_generation": 1},
        {"stepExecutionId": ""},
    ],
)
async def test_pool_plan_handoff_rejects_a_different_grant_identity(change):
    selected = grant()
    result = await _run_table_action("bind_plan", [binding(**change)], [selected])
    assert result.result.get("bound") is not True
    assert selected.execution_plan_ref is None
    assert result.deleted == []


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["cleanup_requested", "released"])
async def test_pool_plan_handoff_never_revives_retired_or_cleaning_capacity(state):
    selected = grant(lease_state=state)
    result = await _run_table_action("bind_plan", [binding()], [selected])
    assert result.result.get("bound") is not True
    assert selected.lease_state == state
    assert selected.execution_plan_ref is None


@pytest.mark.asyncio
async def test_pool_plan_handoff_cannot_replace_an_already_bound_plan():
    selected = grant(execution_plan_ref="another-plan")
    result = await _run_table_action("bind_plan", [binding()], [selected])
    assert result.result.get("bound") is not True
    assert selected.execution_plan_ref == "another-plan"


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["shared", "native", "failed", "released"])
async def test_manager_hands_off_the_existing_grant_only_after_durable_preparation(
    monkeypatch, outcome
):
    from tests.unit.workflows.temporal.test_provider_profile_lease_transition_contract import (
        _manager,
        _Ledger,
        _patched,
    )

    manager = _manager()
    manager._await_restored_ledger = AsyncMock()
    manager._get_logger = lambda: Mock()
    profile = next(iter(manager._profiles.values()))
    profile.reserve(
        "agent-pool",
        NOW,
        purpose="execution_direct",
        metadata={
            "fencingGeneration": 7,
            "runId": "agent-run-1",
            "ownerIsWorkflow": True,
        },
    )
    manager._lease_profile_index["agent-pool"] = profile.profile_id
    before = dict(profile.lease_metadata["agent-pool"])
    callback = SimpleNamespace(signal=AsyncMock())
    handle = Mock(return_value=callback)
    monkeypatch.setattr("temporalio.workflow.get_external_workflow_handle", handle)
    prepared = {
        "disposition": "shared",
        "request": {"agentId": "omnigent"},
        "leaseMetadata": {
            "executionPlanRef": "own-plan",
            "stepExecutionId": "own-step",
            "idempotencyKey": "request-1",
            "credentialGeneration": 2,
        },
    }

    def prepare(_payload):
        if outcome == "failed":
            raise RuntimeError("commit acknowledgement unavailable")
        if outcome == "released":
            profile.release("agent-pool")
        return (
            {"disposition": "native", "reason": "unsupported_option"}
            if outcome == "native"
            else prepared
        )

    ledger = _Ledger({"prepare_pool_plan": prepare})
    with _patched(ledger, now=NOW):
        await manager.prepare_shared_execution_plan(
            {
                "owner_id": "agent-pool",
                "profile_id": profile.profile_id,
                "runId": "agent-run-1",
                "fencing_generation": 7,
                "request": {"idempotencyKey": "request-1"},
            }
        )
    assert ledger.actions() == ["prepare_pool_plan"]
    if outcome == "released":
        assert profile.current_leases == []
        assert "agent-pool" not in profile.lease_metadata
    else:
        assert profile.current_leases == ["agent-pool"]
        if outcome == "shared":
            assert (
                profile.lease_metadata["agent-pool"]["executionPlanRef"] == "own-plan"
            )
        else:
            assert "executionPlanRef" not in profile.lease_metadata["agent-pool"]
        for key in ("fencingGeneration", "expiresAt", "purpose", "runId"):
            assert profile.lease_metadata["agent-pool"][key] == before[key]
    assert not manager._pending_grant_handoffs
    handle.assert_called_once_with("agent-pool", run_id="agent-run-1")
    callback.signal.assert_awaited_once()
    assert callback.signal.await_args.args[0] == "pool_plan_prepared"
    expected = outcome if outcome in {"native", "shared"} else "failed"
    assert callback.signal.await_args.args[1]["disposition"] == expected


@pytest.mark.asyncio
async def test_agent_uses_only_its_fenced_selected_pool_response(monkeypatch):
    from tests.unit.workflows.temporal.workflows.test_agent_run_omnigent_capacity_admission import (
        _plan_binding,
        PLAN_REF,
    )

    request = AgentExecutionRequest(
        agentKind="managed",
        agentId="codex_cli",
        executionProfileRef="selected-account",
        profileSelector={"tagsAll": ["pool"]},
        correlationId="parent",
        idempotencyKey="request-1",
        instructionRef="Preserve this task.",
        stepExecution={
            "workflowId": "parent",
            "runId": "parent-run",
            "logicalStepId": "work",
            "executionOrdinal": 1,
            "stepExecutionId": "parent:parent-run:work:execution:1",
            "runtimeContextPolicy": "fresh_agent_run",
        },
    )
    run = MoonMindAgentRun()
    run._assigned_profile_id = "selected-account"
    run._assigned_slot_fencing_generation = 7
    monkeypatch.setattr(
        "temporalio.workflow.info",
        lambda: SimpleNamespace(workflow_id="agent-pool", run_id="agent-run-1"),
    )
    payload = request.model_dump(mode="json", by_alias=True)
    payload.update(
        agentKind="external",
        agentId="omnigent",
        omnigentExecutionPlan=_plan_binding(PLAN_REF),
    )
    payload["stepExecution"]["omnigentExecutionPlan"] = _plan_binding(PLAN_REF)
    prepared = AgentExecutionRequest.model_validate(payload)

    async def signal(name, value):
        assert name == "prepare_shared_execution_plan"
        assert value["request"]["agentId"] == "codex_cli"
        run.pool_plan_prepared(
            {
                "requestId": "request-1",
                "fencingGeneration": 6,
                "disposition": "shared",
                "request": {},
            }
        )
        assert run._pool_plan_response is None
        run.pool_plan_prepared(
            {
                "requestId": "request-1",
                "fencingGeneration": 7,
                "disposition": "shared",
                "request": prepared.model_dump(mode="json", by_alias=True),
            }
        )

    handle = SimpleNamespace(signal=AsyncMock(side_effect=signal))

    async def wait(predicate, **kwargs):
        assert predicate()

    monkeypatch.setattr("temporalio.workflow.wait_condition", wait)
    result = await run._prepare_selected_pool_shared_request(
        request=request,
        runtime_id="codex_cli",
        manager_handle=handle,
        original_profile_ref=None,
    )
    assert result.agent_id == "omnigent"
    assert result.idempotency_key == request.idempotency_key
    assert result.instruction_ref == request.instruction_ref
    assert result.execution_profile_ref == request.execution_profile_ref
    assert request.agent_kind == "managed"
