"""A second pool turn restores the accepted account, context and saved work."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import shutil
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from api_service.db.models import ProviderProfileSlotLease
from api_service.services.native_pool_execution_plan import prepare_selected_pool_plan
from moonmind.omnigent.harness_platform.stores import DbExecutionPlanStore
from moonmind.omnigent.runtime_bindings import RuntimeBindingState
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.schemas.omnigent_session_models import OmnigentSessionAdmissionDecision
from moonmind.workflows.temporal.activities.omnigent_session_activities import (
    _plan_capacity_authority,
)
from moonmind.workflows.temporal.artifacts import TemporalArtifactActivities
from moonmind.workflows.temporal.workflows.agent_run import MoonMindAgentRun
from moonmind.workflows.temporal.workflows.run import MoonMindUserWorkflow
from tests.integration.omnigent import test_dispatch_pool_shared_admission as pool
from tests.integration.omnigent import test_generic_native_durable_journey as native
from tests.integration.omnigent import test_shared_runtime_context_journey as context

native_journey = native.native_journey
session_factory = native.session_factory
context_provider = context.context_provider

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]


@pytest.mark.parametrize("native_journey", ["codex-native"], indirect=True)
async def test_pool_context_survives_source_host_and_worker_cleanup(
    native_journey, monkeypatch
):
    journey = native_journey
    first = await pool.execute_selected_pool_source(
        journey, monkeypatch, include_successor=True
    )
    child = MoonMindAgentRun()
    child._pool_native_request = first.selected
    delivered = child._with_dispatch_pool_context(first.result)
    parent = MoonMindUserWorkflow()
    parent._record_shared_runtime_context(first.original, delivered)
    successor = pool.agent_requests(
        first.parameters, workflow_id=first.parent_id, run_id=first.parent_run
    )["second"]
    # The controller chooses this policy for an admitted same-epoch turn.
    # No author-supplied runtime authority is introduced by the API fixture.
    successor.step_execution.runtime_context_policy = "reuse_session_same_epoch"
    successor.resolved_skillset_ref = first.selected.resolved_skillset_ref
    successor.step_execution.resolved_skillset_ref = (
        first.selected.resolved_skillset_ref
    )
    successor = parent._bind_shared_runtime_context(successor)
    assert successor.execution_profile_ref == native.PROFILE
    assert successor.step_execution.runtime_session_context is not None
    assert (
        successor.workspace_spec["workspaceCheckpointRestoreRef"]
        == (first.result.metadata["savedWorkspaceCheckpoint"]["archiveRef"])
    )
    source_ref = first.result.metadata["runtimeSessionContext"]["runtimeBindingRef"]
    source = await journey.services.runtime_binding_store.get(source_ref)
    source_items = deepcopy(journey.server.frozen[source.omnigentSessionId]["items"])
    assert source.state is RuntimeBindingState.cleaned
    assert not journey.server.host_alive and not journey.server.workspace.exists()
    if journey.worker_root.exists():
        shutil.rmtree(journey.worker_root)
    assert not journey.worker_root.exists()

    activities = TemporalArtifactActivities(None)
    released = await activities.provider_profile_sync_slot_leases(
        runtime_id="codex_cli",
        action="release_one",
        leases=[{"lease_id": first.owner, "fencing_generation": 7}],
    )
    assert released["released"] is True
    owner, owner_run = "pool-context-successor", "pool-context-successor-run"
    granted = await activities.provider_profile_sync_slot_leases(
        runtime_id="codex_cli",
        action="grant",
        leases=[
            {
                "lease_id": owner,
                "workflow_id": owner,
                "owner_id": owner,
                "profile_id": native.PROFILE,
                "owner_kind": "workflow",
                "ownerIsWorkflow": True,
                "purpose": "execution_direct",
                "compatibility_class": "shared_execution",
                "fencing_generation": 8,
                "scope_generation": 3,
                "capacity_scope_ref": f"provider-profile:{native.PROFILE}",
                "credential_generation": 3,
                "expiresAt": (
                    datetime.now(timezone.utc) + timedelta(hours=1)
                ).isoformat(),
                "safe_metadata": {"runId": owner_run},
            }
        ],
    )
    assert granted["granted"] is True
    transport = SimpleNamespace(
        describe_workflow=AsyncMock(
            return_value=SimpleNamespace(
                raw_description=SimpleNamespace(
                    workflow_execution_info=SimpleNamespace(
                        execution=SimpleNamespace(workflow_id=owner, run_id=owner_run),
                        status=1,
                        type=SimpleNamespace(name="MoonMind.AgentRun"),
                        parent_execution=SimpleNamespace(
                            workflow_id=first.parent_id, run_id=first.parent_run
                        ),
                    )
                )
            )
        )
    )
    async with journey.session_factory() as session:
        compiled = await prepare_selected_pool_plan(
            session,
            runtime_id="codex_cli",
            payload={
                "owner_id": owner,
                "profile_id": native.PROFILE,
                "runId": owner_run,
                "fencing_generation": 8,
                "originalProfileRef": successor.execution_profile_ref,
                "request": successor.model_dump(mode="json", by_alias=True),
            },
            client=transport,
        )
        assert compiled["disposition"] == "shared", compiled
        row = (
            await session.scalars(
                select(ProviderProfileSlotLease).where(
                    ProviderProfileSlotLease.lease_id == owner
                )
            )
        ).one()
        successor_lease_id = row.id
        assert row.fencing_generation == 8 and row.lease_state == "held"
    shared = AgentExecutionRequest.model_validate(compiled["request"])
    plan = await DbExecutionPlanStore(journey.session_factory).load(
        shared.omnigent_execution_plan.plan_ref
    )
    monkeypatch.setattr(
        "temporalio.workflow.info",
        lambda: SimpleNamespace(
            workflow_id=owner,
            run_id=owner_run,
            namespace="default",
            task_queue="test",
            parent=None,
        ),
    )
    run = MoonMindAgentRun()

    async def ensure(manager_id, runtime_id, *, request_slot=True, **kwargs):
        if request_slot:
            run.slot_assigned({"profile_id": native.PROFILE, "fencing_generation": 8})
        return SimpleNamespace(signal=AsyncMock())

    run._ensure_manager_and_signal = ensure
    run._sync_manager_profiles = AsyncMock()
    run._await_omnigent_host_capacity = AsyncMock()
    authority = await _plan_capacity_authority(
        plan, execution_profile_ref=native.PROFILE
    )
    admitted = await run._admit_omnigent_capacity_before_execution(
        request=shared,
        parent_info=None,
        admission=OmnigentSessionAdmissionDecision(
            admitted=False,
            reasonCode="realizer_managed_lifecycle",
            admissionMode="enabled",
            admittedFeatureGeneration="omnigent-session-v1",
            executionRealizerRef=plan.payload.executionRealizerRef,
            **authority,
        ),
    )
    assert admitted.admitted_provider_capacity.lease_owner_id == owner
    journey.plan, journey.request, journey.provider_lease_id = (
        plan,
        admitted,
        successor_lease_id,
    )
    journey.manager.plan, journey.manager.request = plan, admitted
    await journey.gateway.admit_execution_plan_inputs(request=admitted, plan=plan)
    journey.docker.host_class, _ = await journey.services.planned_host_resolver(
        plan, request=admitted
    )
    terminal = await journey.realizer.execute(admitted, plan)
    assert terminal.failure_class is None, terminal.summary
    assert journey.server.restored_report == native.GENERATED
    assert journey.server.forks[0][0] == source.omnigentSessionId
    assert journey.server.items[: len(source_items)] == source_items
    assert len(journey.server.items) > len(source_items)
    assert (
        terminal.metadata["runtimeSessionContext"]["epoch"]
        == (first.result.metadata["runtimeSessionContext"]["epoch"])
    )
    target = await journey.services.runtime_binding_store.get(
        terminal.metadata["runtimeSessionContext"]["runtimeBindingRef"]
    )
    assert target.state is RuntimeBindingState.cleaned
    assert target.hostLeaseRef != source.hostLeaseRef
    assert target.omnigentSessionId != source.omnigentSessionId
    assert await journey.services.runtime_binding_store.get(source_ref) == source
    assert not journey.server.host_alive
