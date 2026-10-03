"""Workflow Chat uses the live execution's durable runtime authority."""

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import Base, OmnigentBridgeSession
from moonmind.omnigent.bridge_store import (
    CHAT_BINDING_STATE_AVAILABLE,
    OmnigentBridgeSessionStore,
)
from moonmind.omnigent.harness_platform.runtime_binding import (
    runtime_binding_execution_scope,
)
from moonmind.omnigent.harness_platform.stores import (
    DbExecutionPlanStore,
    DbRuntimeBindingStore,
)
from moonmind.schemas.agent_runtime_models import OmnigentExecutionPlanBinding
from tests.unit.omnigent.test_bridge_store import _request
from tests.unit.omnigent.test_generic_plane_n_way_concurrency import (
    ZEN_PROFILE,
    _zen_plan,
)


@pytest_asyncio.fixture
async def store(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/chat.db")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        yield OmnigentBridgeSessionStore(factory)
    finally:
        await engine.dispose()


async def _seed_plan_chat(store, *, legacy_scope=False):
    request = _request("chat-step", with_step=True)
    plan = _zen_plan("workflow-chat")
    await DbExecutionPlanStore(store._session_factory).persist(plan)
    request.omnigent_execution_plan = OmnigentExecutionPlanBinding(
        planRef=plan.planRef,
        planDigest="sha256:" + plan.planRef.rsplit(":", 1)[-1],
        planArtifactRef="artifact:chat-plan",
        taskInputSnapshotRef="artifact:chat-request",
        taskInputSnapshotDigest="sha256:" + "1" * 64,
    )
    scope = (
        request.step_execution.workflow_id
        if legacy_scope
        else runtime_binding_execution_scope(request.idempotency_key)
    )
    bindings = DbRuntimeBindingStore(store._session_factory)
    binding = await bindings.create_initial(
        execution_plan_ref=plan.planRef,
        execution_scope_ref=scope,
        provider_leases={
            "primary-model": {
                "providerProfileRef": ZEN_PROFILE,
                "providerLeaseRef": "provider-lease:chat-step",
                "credentialGeneration": 3,
                "credentialRuntimeRef": "credential-runtime:chat-step",
            }
        },
    )
    binding = await bindings.update_with_host(
        binding.runtimeBindingRef,
        host_binding_ref="host-binding:chat-step",
        host_lease_ref="host-lease:chat-step",
        host_lease_generation=3,
        omnigent_host_id="host-chat-step",
        host_harness_attestation_ref="artifact:chat-host",
        exact_host_capability_decision_ref="artifact:chat-capabilities",
        workspace_resolution_ref="artifact:chat-workspace",
        model_option_attestation_ref="artifact:chat-model",
        skill_delivery_attestation_ref="artifact:chat-skills",
        cleanup_authority_refs=[],
        expected_revision=1,
        expected_fencing_generation=1,
    )
    await store.bind_profile_authorization(
        request=request,
        endpoint_ref="default",
        provider_profile_id=ZEN_PROFILE,
        provider_lease_id="provider-lease:chat-step",
        credential_generation=3,
        host_binding_ref="host-binding:chat-step",
        host_lease_ref="host-lease:chat-step",
        omnigent_host_id="host-chat-step",
        effective_launch_snapshot={
            "executionPlanRef": plan.planRef,
            "runtimeBindingRef": binding.runtimeBindingRef,
            "launchPolicyRef": plan.payload.launchPolicyRef,
            "agentProfileCapabilities": {"sendMessage": True, "viewTranscript": True},
            "capabilities": {"sendMessage": True, "viewTranscript": True},
        },
    )
    await store.attach_session(request.idempotency_key, "session-chat-step")
    row = await store.record_session_created(
        request.idempotency_key,
        session_id="session-chat-step",
        session_status="running",
        capabilities={"sendMessage": True, "viewTranscript": True},
    )
    await bindings.update_with_session(
        binding.runtimeBindingRef,
        omnigent_session_id="session-chat-step",
        omnigent_runner_ref=None,
        chat_binding_ref=row.chat_binding_id,
        expected_revision=2,
        expected_fencing_generation=1,
    )
    return row, plan, bindings, scope


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy_scope", [False, True], ids=["execution", "legacy"])
async def test_plan_backed_chat_preserves_live_send_authority(store, legacy_scope):
    row, _plan, _bindings, _scope = await _seed_plan_chat(
        store, legacy_scope=legacy_scope
    )

    resolution = await store.resolve_chat_binding(workflow_id=row.moonmind_workflow_id)

    assert resolution.state == CHAT_BINDING_STATE_AVAILABLE
    assert resolution.chat_binding_id == row.chat_binding_id
    assert resolution.read_only is False
    assert resolution.capabilities == {"sendMessage": True, "viewTranscript": True}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mismatch", ["plan", "plan_digest", "provider_lease", "host_lease", "session"]
)
async def test_plan_backed_chat_denies_mismatched_runtime_authority(store, mismatch):
    row, _plan, _bindings, _scope = await _seed_plan_chat(store)
    other_plan = _zen_plan("another-workflow-chat")
    await DbExecutionPlanStore(store._session_factory).persist(other_plan)
    async with store._session_factory() as session:
        stored = await session.get(OmnigentBridgeSession, row.bridge_session_id)
        if mismatch == "plan":
            stored.metadata_ = {
                **stored.metadata_,
                "executionPlanRef": other_plan.planRef,
                "executionPlanDigest": "sha256:"
                + other_plan.planRef.rsplit(":", 1)[-1],
            }
        elif mismatch == "plan_digest":
            stored.metadata_ = {
                **stored.metadata_,
                "executionPlanDigest": "sha256:" + "0" * 64,
            }
        elif mismatch == "provider_lease":
            stored.provider_lease_id = "provider-lease:another-step"
        elif mismatch == "host_lease":
            stored.host_lease_ref = "host-lease:another-step"
        else:
            stored.omnigent_session_id = "session-another-step"
        await session.commit()

    resolution = await store.resolve_chat_binding(workflow_id=row.moonmind_workflow_id)

    assert resolution.read_only is True
    assert resolution.capabilities == {}


@pytest.mark.asyncio
async def test_plan_backed_chat_denies_replaced_execution_acquisition(store):
    row, plan, bindings, scope = await _seed_plan_chat(store)
    current = await bindings.get_current_state(plan.planRef, scope)
    await bindings.reconcile_provider_leases(
        current.binding.runtimeBindingRef,
        provider_leases={
            "primary-model": {
                "providerProfileRef": ZEN_PROFILE,
                "providerLeaseRef": "provider-lease:replacement",
                "credentialGeneration": 4,
                "credentialRuntimeRef": "credential-runtime:replacement",
            }
        },
        expected_revision=current.revision,
        expected_fencing_generation=current.fencing_generation,
    )

    resolution = await store.resolve_chat_binding(workflow_id=row.moonmind_workflow_id)

    assert resolution.read_only is True
    assert resolution.capabilities == {}
