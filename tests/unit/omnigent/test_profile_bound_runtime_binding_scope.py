"""Independent step executions must not reopen a parent workflow's binding."""

import hashlib
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import (
    Base,
    OmnigentExecutionPlanRecord,
    OmnigentRuntimeBindingRecord,
)
from moonmind.omnigent.execute import OmnigentSessionStillRunningError
from moonmind.omnigent.harness_platform.stores import (
    DbExecutionPlanStore,
    DbRuntimeBindingStore,
    InMemoryRuntimeBindingStore,
)
from moonmind.omnigent.profile_bound_execution import (
    OmnigentProfileBoundExecutionCoordinator,
)
from moonmind.provider_profiles.lease_client import (
    CredentialLease,
    CredentialLeasePurpose,
)
from moonmind.schemas.agent_runtime_models import (
    AgentExecutionRequest,
    AgentRunResult,
    AgentRuntimeStepExecutionLaunch,
)
from tests.unit.omnigent.test_generic_plane_n_way_concurrency import _zen_plan
from tests.unit.omnigent.test_oauth_profile_lifecycle import (
    _launch_ready_profile,
    _run_coordinator_failure_case,
)


@pytest_asyncio.fixture(params=["memory", "database"])
async def binding_store(request, tmp_path):
    if request.param == "memory":
        yield InMemoryRuntimeBindingStore()
        return
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/binding.db")
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda connection: Base.metadata.create_all(
                connection,
                tables=[
                    OmnigentExecutionPlanRecord.__table__,
                    OmnigentRuntimeBindingRecord.__table__,
                ],
            )
        )
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    await DbExecutionPlanStore(factory).persist(_zen_plan("workflow-1"))
    try:
        yield DbRuntimeBindingStore(factory)
    finally:
        await engine.dispose()


def _request(ordinal, *, typed_step=False):
    request = AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        executionProfileRef="codex",
        correlationId=f"workflow-1:agent:step-1:execution{ordinal}",
        idempotencyKey=f"workflow-1:step-1:execution{ordinal}:agent_execute",
        workspaceSpec={
            "workspaceLocator": {
                "kind": "sandbox",
                "workspaceId": hashlib.sha256(
                    f"execution{ordinal}".encode()
                ).hexdigest()[:24],
            }
        },
        parameters={
            "workflowId": "workflow-1",
            "omnigent": {"session": {"workspace": "https://example.com/repo.git"}},
        },
    )
    if typed_step:
        request.step_execution = AgentRuntimeStepExecutionLaunch(
            workflowId="workflow-1",
            runId="run-1",
            logicalStepId="step-1",
            executionOrdinal=ordinal,
            stepExecutionId=f"workflow-1:run-1:step-1:execution:{ordinal}",
            runtimeContextPolicy="fresh_agent_run",
        )
    return request


def _bind_test_execution(monkeypatch, store):
    plan = _zen_plan("workflow-1")
    results = []
    monkeypatch.setattr(
        "moonmind.omnigent.profile_bound_execution.DbRuntimeBindingStore",
        lambda _factory: store,
    )
    monkeypatch.setattr(
        OmnigentProfileBoundExecutionCoordinator,
        "_require_recorded_plan_request",
        lambda _self, _request: plan,
    )
    execute = OmnigentProfileBoundExecutionCoordinator.execute

    async def execute_with_acquired_lease(self, request):
        self._profile_authority.resolve = AsyncMock(
            return_value=_launch_ready_profile(credentialGeneration=3)
        )
        self._acquire_provider_capacity = AsyncMock(
            return_value=CredentialLease(
                runtime_id="codex_cli",
                profile_id="codex",
                lease_id=f"provider-lease:{request.correlation_id}",
                owner_id=request.correlation_id,
                purpose=CredentialLeasePurpose.EXECUTION_OMNIGENT,
            )
        )
        self._hosts.lease = self._hosts.lease.model_copy(
            update={
                "lease_id": f"host-lease:{request.correlation_id}",
                "idempotency_key": request.idempotency_key,
                "holder_workflow_id": "workflow-1",
            }
        )
        result = await execute(self, request)
        results.append(result)
        return result

    monkeypatch.setattr(
        OmnigentProfileBoundExecutionCoordinator, "execute", execute_with_acquired_lease
    )
    return plan, results


@pytest.mark.asyncio
@pytest.mark.parametrize("typed_step", [False, True])
async def test_new_step_after_never_started_turn_has_independent_runtime_authority(
    monkeypatch, binding_store, typed_step
):
    plan, results = _bind_test_execution(monkeypatch, binding_store)
    first_request = _request(1, typed_step=typed_step)
    _, first_actions, _ = await _run_coordinator_failure_case(
        fail_at="none",
        code="OMNIGENT_CURRENT_TURN_NOT_STARTED",
        request=first_request,
        injected_result=AgentRunResult(
            summary="The accepted turn never started",
            failureClass="integration_error",
            providerErrorCode="OMNIGENT_CURRENT_TURN_NOT_STARTED",
            retryRecommendation="retry_step_execution",
        ),
    )
    assert "host_stopped" in first_actions
    assert "provider_released" in first_actions
    first = await binding_store.get_state_for_host_lease(
        f"host-lease:{first_request.correlation_id}"
    )
    assert first.state == "cleanup_complete"

    second_request = _request(2, typed_step=typed_step)
    _, second_actions, _ = await _run_coordinator_failure_case(
        fail_at="none", code="unused", request=second_request
    )
    second = await binding_store.get_state_for_host_lease(
        f"host-lease:{second_request.correlation_id}"
    )
    assert results[-1].summary == "done"
    assert "provider_released" in second_actions
    assert second.state == "cleanup_complete"
    assert (
        second.binding.executionPlanRef
        == first.binding.executionPlanRef
        == plan.planRef
    )
    assert second.execution_scope_ref != first.execution_scope_ref
    assert second.binding.runtimeBindingRef != first.binding.runtimeBindingRef
    assert second.binding.providerLeases["primary-model"].credentialGeneration == 3
    assert second.binding.providerLeases["primary-model"].providerLeaseRef != (
        first.binding.providerLeases["primary-model"].providerLeaseRef
    )
    assert await binding_store.get_state(first.binding.runtimeBindingRef) == first


@pytest.mark.asyncio
async def test_activity_redelivery_reuses_the_execution_binding(
    monkeypatch, binding_store
):
    plan, _results = _bind_test_execution(monkeypatch, binding_store)
    request = _request(1)
    states = []
    for _attempt in range(2):
        _, actions, _ = await _run_coordinator_failure_case(
            fail_at="resource_harvest",
            code="OMNIGENT_CURRENT_TURN_TERMINAL_AMBIGUOUS",
            request=request,
            injected_error=OmnigentSessionStillRunningError(
                "The turn is still running"
            ),
        )
        assert "provider_released" not in actions
        states.append(
            await binding_store.get_state_for_host_lease(
                f"host-lease:{request.correlation_id}"
            )
        )
    first, second = states
    assert first.state == second.state == "host_attested"
    assert first.execution_scope_ref == second.execution_scope_ref
    assert first.binding.providerLeases == second.binding.providerLeases
    assert first.fencing_generation == second.fencing_generation
    assert (
        await binding_store.get_current_state(plan.planRef, first.execution_scope_ref)
        == second
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy_cleaned", [False, True])
@pytest.mark.parametrize("legacy_lease_matches", [False, True])
async def test_legacy_binding_is_retained_only_for_an_active_matching_acquisition(
    monkeypatch, binding_store, legacy_cleaned, legacy_lease_matches
):
    plan, _results = _bind_test_execution(monkeypatch, binding_store)
    request = _request(1)
    legacy = await binding_store.create_initial(
        execution_plan_ref=plan.planRef,
        execution_scope_ref="workflow-1",
        provider_leases={
            "primary-model": {
                "providerProfileRef": "codex",
                "providerLeaseRef": (
                    f"provider-lease:{request.correlation_id}"
                    if legacy_lease_matches
                    else "provider-lease:earlier-step"
                ),
                "credentialGeneration": 3,
                "credentialRuntimeRef": "credential://provider-profile/codex/generation/3",
            }
        },
    )
    if legacy_cleaned:
        await binding_store.mark_cleanup_complete(
            legacy.runtimeBindingRef, expected_revision=1, expected_fencing_generation=1
        )
    original = await binding_store.get_state(legacy.runtimeBindingRef)

    await _run_coordinator_failure_case(fail_at="none", code="unused", request=request)
    current = await binding_store.get_state_for_host_lease(
        f"host-lease:{request.correlation_id}"
    )
    assert current.state == "cleanup_complete"
    if legacy_cleaned or not legacy_lease_matches:
        assert current.execution_scope_ref != "workflow-1"
        assert await binding_store.get_state(legacy.runtimeBindingRef) == original
    else:
        assert current.execution_scope_ref == "workflow-1"
        assert (
            await binding_store.get_current_state(plan.planRef, "workflow-1") == current
        )
