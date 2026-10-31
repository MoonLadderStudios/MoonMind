"""The bridge and Workflow Chat retain the execution's runtime authority."""

import hashlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import Base, OmnigentBridgeSession
from moonmind.omnigent.bridge_store import (
    CHAT_BINDING_STATE_AVAILABLE,
    OmnigentBridgeSessionStore,
    OmnigentIdempotencyError,
)
from moonmind.omnigent.bridge_artifacts import TemporalOmnigentArtifactGateway
from moonmind.omnigent.control_plane.identities import canonical_omnigent_session_id
from moonmind.omnigent.control_plane.turn_commands import (
    CanonicalSessionBootstrap,
    CanonicalTurnCommandService,
)
from moonmind.omnigent.harness_platform.execution_plan import (
    create_execution_plan_envelope,
)
from moonmind.omnigent.harness_platform.failures import (
    HarnessPlatformError,
    HarnessPlatformFailure,
)
from moonmind.omnigent.harness_platform.runtime_binding import (
    runtime_binding_execution_scope,
)
from moonmind.omnigent.harness_platform.stores import (
    DbExecutionPlanStore,
    DbRuntimeBindingStore,
)
from moonmind.omnigent.provider_leases import AcquiredProviderLease
from moonmind.omnigent.runtime_bindings import (
    DbRuntimeBindingStore as StableRuntimeBindingStore,
    RuntimeBindingState,
)
from moonmind.omnigent.realizers.turn_delivery import canonical_turn_idempotency_key
from moonmind.omnigent.turn_authority import canonical_turn_authority
from moonmind.provider_profiles.lease_client import (
    CredentialLease,
    CredentialLeasePurpose,
)
from moonmind.schemas.agent_runtime_models import (
    AgentExecutionRequest,
    OmnigentExecutionPlanBinding,
)
from moonmind.workflows.temporal.activities.omnigent_activities import (
    _try_generic_realizer_dispatch,
)
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


def _generic_host_leases(shape: str) -> dict:
    """Stable (v2) provider leases, as the generic-host realizer persists them."""

    acquired = AcquiredProviderLease(
        slot="primary-model",
        provider_profile_ref=ZEN_PROFILE,
        capacity_scope_ref="opencode-zen:contributor-free",
        provider_lease_ref="provider-lease:generic-step",
        credential_generation=3,
        lease=CredentialLease(
            profile_id=ZEN_PROFILE,
            runtime_id="opencode",
            lease_id="lease-generic-step",
            owner_id="agent-run-generic-step",
            purpose=CredentialLeasePurpose.EXECUTION_OMNIGENT,
            fencing_generation=7,
        ),
        owned_by_workflow=True,
    )
    lease = {
        **acquired.runtime_binding_value(
            credential_runtime_ref="credential-runtime:generic-step"
        ),
        "materializerRef": "opencode-zen-api-key@1",
    }
    if shape == "historical":
        # Rows written before workflow-owned capacity and fenced grants.
        lease.pop("leaseOwnerIsWorkflow")
        lease.pop("leaseFencingGeneration")
    return {"primary-model": lease}


@pytest.mark.asyncio
@pytest.mark.parametrize("shape", ["current", "historical"])
async def test_generic_host_binding_resolves_chat_read_only(store, shape):
    """A stable (v2) binding shares the table but is not harness authority.

    Chat finds runtime authority by the session's host lease. A generic-host
    session's lease belongs to a stable binding, which records no chat or
    runner authority, so the session resolves read-only instead of failing
    to parse that row as a harness binding.
    """

    request = _request("generic-step", with_step=True)
    plan = _zen_plan("generic-chat")
    await DbExecutionPlanStore(store._session_factory).persist(plan)
    request.omnigent_execution_plan = OmnigentExecutionPlanBinding(
        planRef=plan.planRef,
        planDigest="sha256:" + plan.planRef.rsplit(":", 1)[-1],
        planArtifactRef="artifact:generic-plan",
        taskInputSnapshotRef="artifact:generic-request",
        taskInputSnapshotDigest="sha256:" + "1" * 64,
    )
    stable = StableRuntimeBindingStore(store._session_factory)
    leases = _generic_host_leases(shape)
    binding = await stable.create_initial(
        execution_plan_ref=plan.planRef,
        idempotency_key=request.idempotency_key,
        provider_leases=leases,
    )
    binding = await stable.update(
        binding.bindingId,
        expected_revision=binding.revision,
        expected_fencing_generation=binding.fencingGeneration,
        updates={
            "hostBindingRef": "host-binding:generic-step",
            "hostLeaseRef": "host-lease:generic-step",
            "hostLeaseGeneration": 1,
            "omnigentHostId": "host-generic-step",
            "omnigentSessionId": "session-generic-step",
            "attestationRefs": {"modelOptionAttestationRef": "artifact:model"},
            "cleanupAuthorityRefs": [{"kind": "host", "ref": "host-lease:generic-step"}],
        },
    )
    await store.bind_profile_authorization(
        request=request,
        endpoint_ref="default",
        provider_profile_id=ZEN_PROFILE,
        provider_lease_id="provider-lease:generic-step",
        credential_generation=3,
        host_binding_ref="host-binding:generic-step",
        host_lease_ref="host-lease:generic-step",
        omnigent_host_id="host-generic-step",
        effective_launch_snapshot={
            "executionPlanRef": plan.planRef,
            "runtimeBindingRef": binding.bindingId,
            "launchPolicyRef": plan.payload.launchPolicyRef,
            "capabilities": {"sendMessage": True, "viewTranscript": True},
        },
    )
    await store.attach_session(request.idempotency_key, "session-generic-step")
    row = await store.record_session_created(
        request.idempotency_key,
        session_id="session-generic-step",
        session_status="running",
        capabilities={"sendMessage": True, "viewTranscript": True},
    )

    resolution = await store.resolve_chat_binding(workflow_id=row.moonmind_workflow_id)

    assert resolution.state == CHAT_BINDING_STATE_AVAILABLE
    assert resolution.chat_binding_id == row.chat_binding_id
    assert resolution.read_only is True
    assert resolution.capabilities == {}
    # The stable aggregate still verifies through its own store, with its
    # lease ownership, fencing and cleanup handles intact.
    reloaded = await stable.get(binding.bindingId)
    assert reloaded == binding
    assert reloaded.providerLeases == leases
    assert reloaded.cleanupAuthorityRefs == (
        {"kind": "host", "ref": "host-lease:generic-step"},
    )
    assert (
        await DbRuntimeBindingStore(store._session_factory).get_state_for_host_lease(
            "host-lease:generic-step"
        )
        is None
    )


async def _admitted_bridge_attempt(store, request, plan, epoch):
    payload = request.model_dump(by_alias=True, mode="json")
    payload["admittedProviderCapacity"] = {
        "schemaVersion": "admitted-provider-capacity/v2",
        "leaseOwnerId": "agent-run-generic-step",
        "profiles": [
            {
                "providerProfileRef": ZEN_PROFILE,
                "providerRuntimeId": "opencode",
                "capacityScopeRef": "opencode-zen:contributor-free",
                "credentialGeneration": 3,
            }
        ],
        "executionPlanRef": plan.planRef,
        "agentRunWorkflowId": "agent-run-generic-step",
        "agentRunRunId": "run-7",
        "stepExecutionId": request.step_execution.step_execution_id,
        "idempotencyKey": request.idempotency_key,
        "admissionEpoch": epoch,
    }
    request = AgentExecutionRequest.model_validate(payload)
    commands = CanonicalTurnCommandService(store._control_plane_store)
    command_key = canonical_turn_idempotency_key(request)
    await commands.claim(
        workflow_id=request.step_execution.workflow_id,
        provider_session_ref="",
        chat_binding_id=None,
        command_type="execute_admitted_plan",
        turn_source="initial",
        idempotency_key=command_key,
        payload_digest=plan.planRef,
        step_execution_id=request.step_execution.step_execution_id,
        bootstrap=CanonicalSessionBootstrap(
            provider="omnigent",
            step_execution_id=request.step_execution.step_execution_id,
            agent_run_id=request.correlation_id,
            source_idempotency_key=command_key,
            execution_plan_ref=plan.planRef,
            admission_epoch=epoch,
        ),
        requested_authority=canonical_turn_authority(request, plan),
    )
    stable = StableRuntimeBindingStore(store._session_factory)
    leases = _generic_host_leases("current")
    lease_ref = f"provider-lease:admission-{epoch}"
    leases["primary-model"]["providerLeaseRef"] = lease_ref
    binding = await stable.create_initial(
        execution_plan_ref=plan.planRef,
        idempotency_key=request.idempotency_key,
        provider_leases=leases,
        admission_epoch=epoch,
    )
    for state in (
        RuntimeBindingState.credentials_materialized,
        RuntimeBindingState.host_allocating,
        RuntimeBindingState.host_ready,
    ):
        binding = await stable.update(
            binding.bindingId,
            expected_revision=binding.revision,
            expected_fencing_generation=binding.fencingGeneration,
            state=state,
            updates={
                "hostBindingRef": f"host-binding:admission-{epoch}",
                "hostLeaseRef": f"host-lease:admission-{epoch}",
                "hostLeaseGeneration": 1,
                "omnigentHostId": f"host-admission-{epoch}",
            },
        )
    authorization = {
        "request": request,
        "endpoint_ref": "default",
        "provider_profile_id": ZEN_PROFILE,
        "provider_lease_id": lease_ref,
        "credential_generation": 3,
        "host_binding_ref": binding.hostBindingRef,
        "host_lease_ref": binding.hostLeaseRef,
        "omnigent_host_id": binding.omnigentHostId,
        "effective_launch_snapshot": {
            "executionPlanRef": plan.planRef,
            "runtimeBindingRef": binding.bindingId,
            "executionRealizerRef": "generic-omnigent-host@1",
            "launchPolicyRef": plan.payload.launchPolicyRef,
        },
    }
    return stable, binding, authorization


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "prior_state",
    [
        "cleaned",
        "live",
        "settled",
        "foreign-host",
        "completed-bridge",
        "canceled-bridge",
    ],
)
async def test_posted_admission_recovery_preserves_prior_authority(
    store,
    monkeypatch,
    prior_state,
):
    base = _zen_plan("admission-recovery")
    launch = {
        "harness": base.payload.harnessId,
        "launchPolicyRef": base.payload.launchPolicyRef,
        "hostImageRef": "example.test/host@sha256:" + "1" * 64,
    }
    canonical = json.dumps(launch, sort_keys=True, separators=(",", ":"))
    launch["snapshotRef"] = (
        "omnigent-launch:sha256:" + hashlib.sha256(canonical.encode()).hexdigest()
    )
    raw = json.dumps(launch, sort_keys=True, separators=(",", ":")).encode()
    payload = base.payload.model_dump(by_alias=True, mode="json")
    payload.update(
        hostImageRef=launch["hostImageRef"],
        hostArchitecture="linux/amd64",
        omnigentHostBuildDigest="sha256:" + "2" * 64,
        policySnapshotDigest="sha256:" + "3" * 64,
        effectiveLaunchSnapshotRef="artifact:admission-launch",
        effectiveLaunchSnapshotDigest="sha256:" + hashlib.sha256(raw).hexdigest(),
    )
    plan = create_execution_plan_envelope(payload)
    await DbExecutionPlanStore(store._session_factory).persist(plan)
    monkeypatch.setattr(
        TemporalOmnigentArtifactGateway,
        "read_admitted_input",
        AsyncMock(return_value=raw),
    )
    request = _request("posted-capacity-recovery", with_step=True)
    request.omnigent_execution_plan = OmnigentExecutionPlanBinding(
        planRef=plan.planRef,
        planDigest="sha256:" + plan.planRef.rsplit(":", 1)[-1],
        planArtifactRef="artifact:admission-plan",
        taskInputSnapshotRef="artifact:admission-request",
        taskInputSnapshotDigest="sha256:" + "4" * 64,
    )
    stable, binding, first = await _admitted_bridge_attempt(store, request, plan, 1)
    initial = await store.bind_profile_authorization(**first)
    replayed = await store.bind_profile_authorization(**first)
    assert replayed.bridge_session_id == initial.bridge_session_id
    assert (
        replayed.metadata_["runtimeAuthority"] == initial.metadata_["runtimeAuthority"]
    )
    await store.attach_session(request.idempotency_key, "session-first-admission")
    await store.record_session_created(
        request.idempotency_key,
        session_id="session-first-admission",
        session_status="running",
        capabilities={},
    )
    await store.mark_prepared(
        request.idempotency_key, digest="original-digest", marker="original-marker"
    )
    await store.mark_posting(request.idempotency_key)
    await store.mark_posted(request.idempotency_key, item_id="original-message")
    if prior_state != "live":
        for state in (RuntimeBindingState.failed, RuntimeBindingState.cleaned):
            binding = await stable.update(
                binding.bindingId,
                expected_revision=binding.revision,
                expected_fencing_generation=binding.fencingGeneration,
                state=state,
                updates={
                    "terminalResult": {
                        "executionState": "completed",
                        "objectiveOutcome": "succeeded",
                    }
                }
                if prior_state == "settled"
                else {},
            )
    if prior_state == "foreign-host":
        async with store._session_factory() as session:
            row = await session.get(
                OmnigentBridgeSession,
                (await store.get_existing(request.idempotency_key)).bridge_session_id,
            )
            row.host_lease_ref = "another-owner-host-lease"
            await session.commit()
    if prior_state in {"completed-bridge", "canceled-bridge"}:
        await store.mark_terminal(
            request.idempotency_key,
            status=prior_state.removesuffix("-bridge"),
            terminal_refs={"captureManifestRef": "artifact:prior-terminal-capture"},
        )
    before = await store.get_existing(request.idempotency_key)
    old_session_id = canonical_omnigent_session_id(
        workflow_id=request.step_execution.workflow_id,
        step_execution_id=request.step_execution.step_execution_id,
        agent_run_id=request.correlation_id,
        admission_epoch=1,
    )
    async with store._control_plane_store.transaction() as repos:
        old_session = await repos.sessions.get(old_session_id)
    _stable, _binding, second = await _admitted_bridge_attempt(store, request, plan, 2)

    if prior_state == "cleaned":
        with pytest.raises(HarnessPlatformError) as exc:
            await store.bind_profile_authorization(**second)
        assert exc.value.code == HarnessPlatformFailure.OMNIGENT_SESSION_HOST_LOST

        async def dispatch(_request, _plan):
            return await store.bind_profile_authorization(**second)

        result = await _try_generic_realizer_dispatch(
            second["request"],
            plan_store=DbExecutionPlanStore(store._session_factory),
            realizer_registry=SimpleNamespace(
                require=lambda _ref: SimpleNamespace(execute=dispatch)
            ),
            artifact_gateway=SimpleNamespace(admit_execution_plan_inputs=AsyncMock()),
        )
        assert (
            result.provider_error_code
            == HarnessPlatformFailure.OMNIGENT_SESSION_HOST_LOST
        )
        assert result.retry_recommendation == "retry_step_execution"
        assert result.retry_disposition == "retry"
    else:
        with pytest.raises(
            OmnigentIdempotencyError, match="runtime authority is already bound"
        ):
            await store.bind_profile_authorization(**second)

    after = await store.get_existing(request.idempotency_key)
    assert after.metadata_ == before.metadata_
    assert after.omnigent_session_id == before.omnigent_session_id
    assert after.host_lease_ref == before.host_lease_ref
    assert after.first_message_item_id == "original-message"
    assert after.first_message_digest == "original-digest"
    assert after.first_message_posted_at == before.first_message_posted_at
    assert after.terminal_refs == before.terminal_refs
    async with store._control_plane_store.transaction() as repos:
        assert await repos.sessions.get(old_session_id) == old_session
