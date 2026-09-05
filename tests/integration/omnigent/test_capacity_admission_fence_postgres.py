"""Database-backed evidence for the pre-Activity capacity admission fence.

Source: MoonLadderStudios/MoonMind#3880 (remaining implementation 1-3, 5;
AC2, AC3, AC5, AC6).

The admitted-capacity hand-off is only as strong as the durable rows it is
established from. Three of those reads are exercised here against a real
PostgreSQL cluster rather than a fake session:

* ``_plan_capacity_authority`` must resolve the Provider Profile, its capacity
  scope and the credential generation the ticket is fenced on from the stored
  profile row, so a rotation while the run queued is detectable.
* ``provider_profile.sync_slot_leases`` must round-trip the plan, step,
  request and credential-generation fence, and the ProviderProfileManager must
  restore it, so a manager restart between the grant and the execution Activity
  leaves the Activity something it can positively establish by inspection.
* ``omnigent.admit_generic_host_capacity`` must read this run's own host
  reservation from the authoritative ledger, keyed by the same stable runtime
  binding the realizer derives, so a retry or a requeue after a lost slot
  reuses the reservation instead of racing for a second host.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import (
    ManagedAgentProviderProfile,
    OmnigentExecutionPlanRecord,
    OmnigentHostBindingRecordV2,
    OmnigentHostLeaseRecordV2,
    ProviderProfileSlotLease,
)
from moonmind.omnigent.harness_platform.execution_plan import (
    compute_model_config_digest,
    create_execution_plan_envelope,
)
from moonmind.omnigent.host_leases import generic_host_lease_ref
from moonmind.omnigent.provider_leases import (
    OmnigentProviderLeaseCoordinator,
    _AdmittedLeaseFence,
)
from moonmind.omnigent.runtime_bindings import stable_binding_id
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.workflows.temporal.activities.omnigent_session_activities import (
    _plan_capacity_authority,
    _run_already_holds_generic_host,
)
from moonmind.workflows.temporal.artifacts import TemporalArtifactActivities
from moonmind.workflows.temporal.workflows import provider_profile_manager
from moonmind.workflows.temporal.workflows.agent_run import MoonMindAgentRun
from moonmind.workflows.temporal.workflows.provider_profile_manager import (
    MoonMindProviderProfileManagerWorkflow,
    ProfileSlotState,
)

pytestmark = [pytest.mark.integration, pytest.mark.integration_ci]

RUNTIME_ID = "opencode"
PROFILE_REF = "opencode-zen-free"
HOST_CLASS_REF = "omnigent-opencode@1"
PLAN_REF = "omnigent-execution-plan:sha256:" + "a" * 64
OTHER_PLAN_REF = "omnigent-execution-plan:sha256:" + "b" * 64
OWNER_ID = "agent-run-capacity-fence"
STEP_EXECUTION_ID = "mm:capacity-fence:step:1"
IDEMPOTENCY_KEY = "mm:capacity-fence:idem:1"
CREDENTIAL_GENERATION = 7

_FENCE_TABLES = [
    ManagedAgentProviderProfile.__table__,
    OmnigentExecutionPlanRecord.__table__,
    OmnigentHostBindingRecordV2.__table__,
    OmnigentHostLeaseRecordV2.__table__,
    ProviderProfileSlotLease.__table__,
]


def _create_tables(sync_conn) -> None:
    for table in _FENCE_TABLES:
        table.create(sync_conn, checkfirst=True)


def _drop_tables(sync_conn) -> None:
    for table in reversed(_FENCE_TABLES):
        table.drop(sync_conn, checkfirst=True)


@pytest_asyncio.fixture()
async def capacity_fence_session_maker(control_plane_postgres_url, monkeypatch):
    """Bind the production session factory to an ephemeral PostgreSQL cluster.

    Both code paths under test resolve ``api_service.db.base.async_session_maker``
    at call time, so rebinding it here exercises the real Activity and the real
    ledger read rather than a substituted session.
    """

    import api_service.db.base as db_base

    engine = create_async_engine(control_plane_postgres_url)
    async with engine.begin() as conn:
        await conn.run_sync(_create_tables)
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(db_base, "async_session_maker", maker)
    try:
        yield maker
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(_drop_tables)
        await engine.dispose()


def _slot_lease_activity() -> Any:
    """The real ``provider_profile.sync_slot_leases`` Activity implementation."""

    return TemporalArtifactActivities(service=None).provider_profile_sync_slot_leases


def _granted_lease_payload(
    *,
    execution_plan_ref: str = PLAN_REF,
    credential_generation: int = CREDENTIAL_GENERATION,
    expires_at: datetime | None = None,
) -> dict[str, Any]:
    """The payload ``_grant_lease_to_db`` sends for a workflow-admitted grant."""

    expiry = expires_at or (datetime.now(timezone.utc) + timedelta(hours=1))
    return {
        "lease_id": OWNER_ID,
        "workflow_id": OWNER_ID,
        "profile_id": PROFILE_REF,
        "owner_id": OWNER_ID,
        "owner_kind": "workflow",
        "purpose": "execution_omnigent",
        "fencing_generation": 1,
        "scope_generation": 1,
        "capacity_scope_ref": f"provider-profile:{PROFILE_REF}",
        "lease_state": "held",
        "stepExecutionId": STEP_EXECUTION_ID,
        "oauthSessionId": None,
        "idempotencyKey": IDEMPOTENCY_KEY,
        "executionPlanRef": execution_plan_ref,
        "credential_generation": credential_generation,
        "expiresAt": expiry.isoformat(),
        "ownerIsWorkflow": True,
    }


class _RestartedManager(MoonMindProviderProfileManagerWorkflow):
    """A manager restored from the durable ledger, with no external signals."""

    def __init__(self) -> None:
        super().__init__()
        self.reconnected: list[tuple[str, str, int | None]] = []

    async def _signal_slot_assigned(
        self,
        requester_workflow_id: str,
        profile_id: str,
        *,
        fencing_generation: int | None = None,
    ) -> None:
        # The orphaned-workflow reconnect is a Temporal side effect; the fence
        # this test is about is the restored lease metadata. The signature
        # mirrors the manager's own so a fenced assignment reaches this double
        # exactly as it reaches the real signal.
        self.reconnected.append(
            (requester_workflow_id, profile_id, fencing_generation)
        )


async def _restore_manager(monkeypatch: pytest.MonkeyPatch) -> _RestartedManager:
    """Drive the production restore path against the real durable rows."""

    activity = _slot_lease_activity()

    async def _execute_activity(name: str, payload: Any, **_kwargs: Any) -> Any:
        assert name == "provider_profile.sync_slot_leases"
        return await activity(**payload)

    monkeypatch.setattr(
        provider_profile_manager.workflow, "execute_activity", _execute_activity
    )
    monkeypatch.setattr(
        provider_profile_manager.workflow, "patched", lambda _patch_id: True
    )
    monkeypatch.setattr(
        provider_profile_manager.workflow,
        "info",
        lambda: SimpleNamespace(
            workflow_id="provider-profile-manager-opencode",
            run_id="run-1",
            task_queue="agent-runtime",
        ),
    )
    monkeypatch.setattr(
        provider_profile_manager.workflow,
        "logger",
        logging.getLogger("test.provider_profile_manager"),
    )

    manager = _RestartedManager()
    manager._runtime_id = RUNTIME_ID
    manager._profiles[PROFILE_REF] = ProfileSlotState(
        profile_id=PROFILE_REF,
        max_parallel_runs=8,
        cooldown_after_429_seconds=60,
        rate_limit_policy="cooldown",
        enabled=True,
        capacity_scope_ref=f"provider-profile:{PROFILE_REF}",
    )
    assert await manager._load_leases_from_db() is True
    return manager


def _admitted_fence(
    *,
    plan_ref: str = PLAN_REF,
    credential_generation: int | None = CREDENTIAL_GENERATION,
) -> _AdmittedLeaseFence:
    return _AdmittedLeaseFence(
        owner_id=OWNER_ID,
        plan_ref=plan_ref,
        step_execution_id=STEP_EXECUTION_ID,
        idempotency_key=IDEMPOTENCY_KEY,
        credential_generation=credential_generation,
    )


@pytest.mark.asyncio
async def test_the_lease_fence_survives_a_manager_restart(
    capacity_fence_session_maker, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC2/AC6: the durable row carries everything the Activity must establish.

    Grant through the real Activity, restart the manager from PostgreSQL, and
    require that the restored inspection payload positively satisfies the same
    fence ``OmnigentProviderLeaseCoordinator`` consumes. If any of the plan,
    step, request, owner, expiry or credential-generation fields failed to
    round-trip, consumption would fail closed and the run would be re-admitted
    for no reason.
    """

    granted = await _slot_lease_activity()(
        runtime_id=RUNTIME_ID, leases=[_granted_lease_payload()], action="grant"
    )
    assert granted == {"granted": True, "duplicate": False}

    manager = await _restore_manager(monkeypatch)
    # The restored owner and profile are what this test is about; the grant
    # generation the manager quotes is asserted by its own fencing tests.
    assert [item[:2] for item in manager.reconnected] == [(OWNER_ID, PROFILE_REF)]

    inspection = manager.inspect_credential_lease(
        {"lease_id": OWNER_ID, "owner_id": OWNER_ID}
    )

    assert inspection["active"] is True
    assert inspection["executionPlanRef"] == PLAN_REF
    assert inspection["stepExecutionId"] == STEP_EXECUTION_ID
    assert inspection["idempotencyKey"] == IDEMPOTENCY_KEY
    assert int(inspection["credentialGeneration"]) == CREDENTIAL_GENERATION
    # The consumption check that gates every host and credential side effect.
    OmnigentProviderLeaseCoordinator._assert_admitted_lease_matches(
        inspection, profile_ref=PROFILE_REF, fence=_admitted_fence()
    )


@pytest.mark.asyncio
async def test_a_restored_lease_still_fails_closed_on_a_mismatched_fence(
    capacity_fence_session_maker, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC2: restoring the fence must not weaken it."""

    await _slot_lease_activity()(
        runtime_id=RUNTIME_ID, leases=[_granted_lease_payload()], action="grant"
    )
    manager = await _restore_manager(monkeypatch)
    inspection = manager.inspect_credential_lease(
        {"lease_id": OWNER_ID, "owner_id": OWNER_ID}
    )

    for fence in (
        _admitted_fence(plan_ref=OTHER_PLAN_REF),
        _admitted_fence(credential_generation=CREDENTIAL_GENERATION + 1),
    ):
        with pytest.raises(Exception) as exc_info:
            OmnigentProviderLeaseCoordinator._assert_admitted_lease_matches(
                inspection, profile_ref=PROFILE_REF, fence=fence
            )
        assert "not usable" in str(exc_info.value)

    # An owner that never held this lease gets no evidence at all.
    assert manager.inspect_credential_lease(
        {"lease_id": "some-other-run", "owner_id": "some-other-run"}
    ) == {"active": False, "lease_id": "some-other-run"}


@pytest.mark.asyncio
async def test_an_expired_restored_lease_is_not_consumable(
    capacity_fence_session_maker, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC2: an expired grant must never be read as live capacity."""

    await _slot_lease_activity()(
        runtime_id=RUNTIME_ID,
        leases=[
            _granted_lease_payload(
                expires_at=datetime.now(timezone.utc) - timedelta(minutes=1)
            )
        ],
        action="grant",
    )
    manager = await _restore_manager(monkeypatch)
    inspection = manager.inspect_credential_lease(
        {"lease_id": OWNER_ID, "owner_id": OWNER_ID}
    )

    with pytest.raises(Exception, match="expired"):
        OmnigentProviderLeaseCoordinator._assert_admitted_lease_matches(
            inspection, profile_ref=PROFILE_REF, fence=_admitted_fence()
        )


def _generic_host_plan(*profile_refs: str) -> Any:
    """A real generic-host execution plan selecting the given Provider Profiles."""

    model = "opencode-go/model"
    return create_execution_plan_envelope(
        {
            "endpointRef": "default",
            "agentProfileSnapshotRef": "omnigent-agent-profile:sha256:" + "1" * 64,
            "harnessCatalogRef": "omnigent-harness-catalog:sha256:" + "2" * 64,
            "harnessId": "opencode-native",
            "harnessImplementationRef": (
                "omnigent-harness-implementation:sha256:" + "3" * 64
            ),
            "agentSource": {
                "kind": "upstream",
                "upstreamId": "opencode-native-ui",
                "upstreamVersion": "1",
                "upstreamSnapshotDigest": "sha256:" + "4" * 64,
            },
            "credentialBindingSetRef": (
                "omnigent-credential-bindings:primary@1#sha256:" + "5" * 64
            ),
            "credentialBindings": {
                f"slot-{index}": {
                    "providerProfileRef": profile_ref,
                    "materializerRef": "none@1",
                }
                for index, profile_ref in enumerate(profile_refs)
            },
            "hostClassRef": HOST_CLASS_REF,
            "launchPolicyRef": "omnigent-on-demand@1",
            "executionRealizerRef": "generic-omnigent-host@1",
            "model": {
                "qualifiedId": model,
                "effort": None,
                "routeRef": "opencode-go",
                "normalizedOptions": {},
                "modelConfigDigest": compute_model_config_digest(
                    qualifiedId=model,
                    effort=None,
                    routeRef="opencode-go",
                    normalizedOptions={},
                ),
            },
            "resolvedSkills": {
                "resolvedSkillSetRef": "artifact:skills",
                "resolvedSkillSetDigest": "sha256:" + "6" * 64,
                "skillDeliveryRef": "skill-delivery:sha256:" + "7" * 64,
            },
            "classAdmissionDecision": {
                "allowed": True,
                "requiredSatisfied": [],
                "preferredSatisfied": [],
                "preferredMissing": [],
                "reasons": [],
            },
            "runtimeValidationRequirements": ["live-model-option"],
            "workspaceIntentRef": "workspace-intent:sha256:" + "8" * 64,
            "workspaceMutation": "read_only",
            "capturePolicyRef": None,
            "capturePolicy": {"stream": False, "evidence": False},
            "policySnapshotRef": "omnigent-policy:sha256:" + "9" * 64,
            "supportCombinationKey": (
                "omnigent-support-combination:sha256:" + "0" * 64
            ),
        }
    )


@pytest.mark.asyncio
async def test_capacity_authority_reads_the_stored_credential_generation(
    capacity_fence_session_maker,
) -> None:
    """Impl 1: the generation the ticket fences on comes from the profile row.

    ``_plan_capacity_authority`` is the production admission read. Taking the
    generation from anywhere other than the durable profile row would leave the
    Activity unable to detect a rotation that happened while the run queued.
    """

    from api_service.db.models import (
        ProviderCredentialSource,
        RuntimeMaterializationMode,
    )

    async with capacity_fence_session_maker() as session:
        session.add(
            ManagedAgentProviderProfile(
                profile_id=PROFILE_REF,
                runtime_id=RUNTIME_ID,
                provider_id="opencode-zen",
                credential_source=ProviderCredentialSource.NONE,
                runtime_materialization_mode=RuntimeMaterializationMode.COMPOSITE,
                max_parallel_runs=8,
                credential_generation=CREDENTIAL_GENERATION,
            )
        )
        await session.commit()

    authority = await _plan_capacity_authority(
        _generic_host_plan(PROFILE_REF), execution_profile_ref=PROFILE_REF
    )

    assert authority["capacityAcquisitionOwner"] == "workflow"
    assert authority["providerProfileRef"] == PROFILE_REF
    assert authority["providerRuntimeId"] == RUNTIME_ID
    assert authority["hostClassRef"] == HOST_CLASS_REF
    assert authority["capacityProfiles"] == [
        {
            "providerProfileRef": PROFILE_REF,
            "providerRuntimeId": RUNTIME_ID,
            "capacityScopeRef": f"provider-profile:{PROFILE_REF}",
            "credentialGeneration": CREDENTIAL_GENERATION,
        }
    ]


@pytest.mark.asyncio
async def test_capacity_authority_fails_closed_on_a_missing_profile(
    capacity_fence_session_maker,
) -> None:
    """A plan may not admit capacity against a Provider Profile that is gone."""

    with pytest.raises(ValueError, match="no longer exists"):
        await _plan_capacity_authority(
            _generic_host_plan(PROFILE_REF), execution_profile_ref=PROFILE_REF
        )


def _plan_bound_request(
    *, idempotency_key: str = IDEMPOTENCY_KEY
) -> AgentExecutionRequest:
    return AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        executionProfileRef=PROFILE_REF,
        omnigentExecutionPlan={
            "planRef": PLAN_REF,
            "planDigest": "sha256:" + "a" * 64,
            "planArtifactRef": "artifact:omnigent-execution-plan",
            "taskInputSnapshotRef": "artifact:omnigent-task-input",
            "taskInputSnapshotDigest": "sha256:" + "c" * 64,
        },
        correlationId="corr-1",
        idempotencyKey=idempotency_key,
        instructionRef="artifact:instructions",
        parameters={"publishMode": "none"},
        workspaceSpec={},
    )


def _host_binding_payload(
    request: AgentExecutionRequest, *, admission_epoch: int = 1
) -> dict[str, Any]:
    """The payload the AgentRun workflow sends to host pre-admission."""

    return MoonMindAgentRun._omnigent_host_binding_identity(
        request,
        plan_ref=MoonMindAgentRun._omnigent_execution_plan_ref(request),
        host_class_ref=HOST_CLASS_REF,
        admission_epoch=admission_epoch,
    )


async def _insert_host_lease(
    session_maker, *, runtime_binding_id: str, status: str
) -> str:
    lease_ref = generic_host_lease_ref(
        runtime_binding_id=runtime_binding_id, host_class_ref=HOST_CLASS_REF
    )
    async with session_maker() as session:
        session.add(
            OmnigentHostBindingRecordV2(
                binding_id=runtime_binding_id,
                host_class_ref=HOST_CLASS_REF,
                launch_policy_ref="omnigent-on-demand@1",
                harness_id="omnigent",
                harness_implementation_ref="omnigent-host-moonmind@1",
                provider_profile_refs_json=[PROFILE_REF],
            )
        )
        session.add(
            OmnigentHostLeaseRecordV2(
                lease_id=lease_ref,
                binding_id=runtime_binding_id,
                host_class_ref=HOST_CLASS_REF,
                runtime_binding_id=runtime_binding_id,
                status=status,
            )
        )
        await session.commit()
    return lease_ref


@pytest.mark.asyncio
async def test_host_pre_admission_reuses_this_runs_own_reservation(
    capacity_fence_session_maker,
) -> None:
    """AC5/Impl 5: the precheck reads the ledger, not a caller-supplied flag.

    The lease key the workflow's host-binding payload resolves to must be the
    one the realizer derives from the plan it loads, or a retry would be
    counted as new demand and refused by the capacity it is already inside.
    """

    request = _plan_bound_request()
    payload = _host_binding_payload(request)
    binding_id = stable_binding_id(
        execution_plan_ref=PLAN_REF,
        idempotency_key=IDEMPOTENCY_KEY,
        admission_epoch=1,
    )
    assert payload == {
        "executionPlanRef": PLAN_REF,
        "idempotencyKey": IDEMPOTENCY_KEY,
        "hostClassRef": HOST_CLASS_REF,
        "admissionEpoch": 1,
    }

    # No ledger row yet: this run holds nothing.
    assert await _run_already_holds_generic_host(payload) is False

    await _insert_host_lease(
        capacity_fence_session_maker,
        runtime_binding_id=binding_id,
        status="ready",
    )

    assert await _run_already_holds_generic_host(payload) is True
    # A different request identity is different demand, not the same host.
    assert (
        await _run_already_holds_generic_host(
            _host_binding_payload(_plan_bound_request(idempotency_key="other-idem"))
        )
        is False
    )
    # A re-admission released the reservation above with the attempt that held
    # it, so the next attempt must not be exempted by a lease it no longer has.
    assert (
        await _run_already_holds_generic_host(
            _host_binding_payload(request, admission_epoch=2)
        )
        is False
    )


@pytest.mark.asyncio
async def test_a_released_host_lease_is_not_a_reservation(
    capacity_fence_session_maker,
) -> None:
    """A finished lease must not exempt the next attempt from host admission."""

    request = _plan_bound_request()
    binding_id = stable_binding_id(
        execution_plan_ref=PLAN_REF,
        idempotency_key=IDEMPOTENCY_KEY,
        admission_epoch=1,
    )
    await _insert_host_lease(
        capacity_fence_session_maker,
        runtime_binding_id=binding_id,
        status="released",
    )

    payload = _host_binding_payload(request)
    assert await _run_already_holds_generic_host(payload) is False


@pytest.mark.asyncio
async def test_an_unnamed_binding_falls_back_to_the_caller_flag(
    capacity_fence_session_maker,
) -> None:
    """A caller that cannot name its binding has no reservation to reuse."""

    assert await _run_already_holds_generic_host({}) is False
    assert await _run_already_holds_generic_host({"alreadyAllocated": True}) is True
