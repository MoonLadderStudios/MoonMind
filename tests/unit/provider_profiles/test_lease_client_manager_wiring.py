"""The lease client and the manager agree on one capacity contract.

Source issue: MoonLadderStudios/MoonMind#3879 (AC7).

The capacity and lease-mode rules were previously pinned by calling the manager
workflow's handlers directly. That proves the manager, but not the seam: the
Update names the client sends, the payload it builds, the grant fields it reads
back, the fence it quotes on release, and the reattachment it performs when the
manager detaches an accepted Update to Continue-As-New are all client-side, and
none of them were exercised against a real manager.

These tests run the real ``ProviderProfileLeaseClient`` against a real
``MoonMindProviderProfileManagerWorkflow``. They are hermetic and belong to the
required unit suite: no Temporal server, no Docker, no credentials. Exact-image
and live-provider qualification stays with its own separately reported tiers.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, ClassVar
from unittest.mock import patch as mock_patch

import pytest
from temporalio import exceptions
from temporalio.client import WorkflowUpdateFailedError
from temporalio.service import RPCError, RPCStatusCode

from moonmind.provider_profiles.lease_client import (
    MANAGER_ROLLOVER_ERROR_TYPE,
    CredentialLease,
    CredentialLeaseMode,
    CredentialLeasePurpose,
    ProviderProfileLeaseClient,
    deterministic_lease_owner_id,
)
from moonmind.provider_profiles.manager_recovery import (
    MANAGER_HELD_LEASE_PRESENT,
    ProviderManagerUnavailableError,
)
from moonmind.workflows.temporal.workflows.provider_profile_manager import (
    MoonMindProviderProfileManagerWorkflow,
    ProfileSlotState,
    workflow_id_for_runtime,
)

CAPACITIES = [1, 2, 4, 8, 16]

NOW = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)
RUNTIME_ID = "opencode"
PROFILE_ID = "opencode-zen-free"
MANAGER_MODULE = (
    "moonmind.workflows.temporal.workflows.provider_profile_manager.workflow"
)


class _WorkflowStubs:
    """The workflow-module primitives the manager's handlers reach for."""

    def __init__(self) -> None:
        self._now = NOW
        self.logger = logging.getLogger(__name__)
        self._wake = asyncio.Event()

    def now(self) -> datetime:
        return self._now

    def patched(self, _patch_id: str) -> bool:
        return False

    def all_handlers_finished(self) -> bool:
        return True

    async def wait_condition(self, predicate, timeout=None):
        while not predicate():
            self._wake.clear()
            await self._wake.wait()
        return True

    async def execute_activity(self, *_args, **_kwargs):
        return {}

    def wake(self) -> None:
        self._wake.set()

    def advance(self, delta: timedelta) -> None:
        self._now = self._now + delta


class _DirectManagerAdapter:
    """Routes the client's Temporal RPCs to one in-process manager instance.

    Only the transport is stood in for. The Update names, payload shapes, and
    every handler that runs are the production ones, so a client that sends the
    wrong field or misreads a grant fails here.
    """

    _UPDATES: ClassVar[dict[str, str]] = {
        "AcquireSlotV2": "acquire_slot_v2",
        "AcquireSlot": "acquire_slot",
        "AcquireCredentialMaintenanceLease": "acquire_credential_maintenance_lease",
        "InspectCredentialLease": "inspect_credential_lease",
    }

    def __init__(self, manager: MoonMindProviderProfileManagerWorkflow) -> None:
        self.manager = manager
        self.stubs = _WorkflowStubs()
        self.started: list[str] = []
        self.updates: list[tuple[str, str, dict[str, Any]]] = []
        self.signals: list[tuple[str, str, dict[str, Any]]] = []
        #: Called with the 1-based attempt number before each Update is
        #: dispatched, so a test can move the manager between attempts.
        self.on_update: Any = None

    async def get_client(self):
        return self

    async def start_workflow(self, _name, _payload, *, id, task_queue):
        del task_queue
        self.started.append(id)
        return self

    async def update_workflow(self, workflow_id, update_name, payload):
        self.updates.append((workflow_id, update_name, dict(payload)))
        if self.on_update is not None:
            self.on_update(len(self.updates))
        handler = getattr(self.manager, self._UPDATES[update_name])
        try:
            with mock_patch(MANAGER_MODULE, self.stubs):
                result = handler(payload)
                if asyncio.iscoroutine(result):
                    result = await result
        except exceptions.ApplicationError as exc:
            # The server reports a failed Update to the client this way, so the
            # client's reattach decision runs against the real error shape.
            raise WorkflowUpdateFailedError(exc) from exc
        return result

    async def signal_workflow(self, workflow_id, signal_name, payload):
        self.signals.append((workflow_id, signal_name, dict(payload)))
        handler = getattr(self.manager, signal_name)
        with mock_patch(MANAGER_MODULE, self.stubs):
            result = handler(payload)
            if asyncio.iscoroutine(result):
                await result


def _manager(
    capacity: int, *, credential_source: str = "none"
) -> MoonMindProviderProfileManagerWorkflow:
    manager = MoonMindProviderProfileManagerWorkflow()
    manager._runtime_id = RUNTIME_ID
    manager._purpose_aware_capacity_ledger = True
    manager._durable_maintenance_queue = True
    manager._profiles[PROFILE_ID] = ProfileSlotState(
        profile_id=PROFILE_ID,
        max_parallel_runs=capacity,
        cooldown_after_429_seconds=300,
        rate_limit_policy="backoff",
        enabled=True,
        launch_ready=True,
        credential_source=credential_source,
        purpose_aware_capacity=True,
        capacity_scope_ref=f"provider-profile:{PROFILE_ID}",
        effective_limit=capacity,
    )
    return manager


def _wire(capacity: int, **kwargs):
    manager = _manager(capacity, **kwargs)
    adapter = _DirectManagerAdapter(manager)
    return manager, adapter, ProviderProfileLeaseClient(adapter)


def _validation_owner(identity: str) -> str:
    return deterministic_lease_owner_id(
        profile_id=PROFILE_ID,
        purpose=CredentialLeasePurpose.CREDENTIAL_VALIDATION,
        idempotency_key=identity,
    )


async def _settle() -> None:
    for _ in range(4):
        await asyncio.sleep(0)


@pytest.mark.asyncio
@pytest.mark.parametrize("declared_workflow", [None, "mm:parent"])
async def test_activity_owned_lease_pairs_its_actual_workflow_and_run(
    monkeypatch,
    declared_workflow,
) -> None:
    manager, adapter, client = _wire(1, credential_source="oauth_volume")
    monkeypatch.setattr(
        "temporalio.activity.info",
        lambda: SimpleNamespace(
            workflow_id="mm:parent:agent:node-1", workflow_run_id="child-run"
        ),
    )
    lease = await client.acquire_execution_lease(
        runtime_id=RUNTIME_ID,
        profile_id=PROFILE_ID,
        owner_id="profile-lease:attempt",
        purpose=CredentialLeasePurpose.EXECUTION_OMNIGENT,
        metadata={"workflowId": declared_workflow} if declared_workflow else {},
    )
    hints = manager._lease_holder_run_hints()
    assert hints == {"mm:parent:agent:node-1": "child-run"}

    async def verify_holders(name, payload, **_kwargs):
        assert name == "provider_profile.verify_lease_holders"
        return {
            workflow_id: {
                "running": (workflow_id, payload["run_ids"].get(workflow_id))
                == ("mm:parent:agent:node-1", "child-run"),
                "status": "RUNNING" if workflow_id.endswith(":node-1") else "NOT_FOUND",
            }
            for workflow_id in payload["workflow_ids"]
        }

    adapter.stubs.execute_activity = verify_holders
    with mock_patch(MANAGER_MODULE, adapter.stubs):
        statuses = await manager._verify_workflow_statuses(
            manager._lease_holder_workflow_ids(include_activity_owned=True), hints
        )
    assert (
        manager._terminal_lease_candidates(statuses, include_activity_owned=True) == []
    )
    assert manager._profiles[PROFILE_ID].current_leases == [lease.lease_id]
    assert adapter.updates[-1][2]["metadata"]["workflowId"] == "mm:parent:agent:node-1"


@pytest.mark.asyncio
async def test_activity_lease_preserves_an_explicit_owner_pair(monkeypatch) -> None:
    manager, _adapter, client = _wire(1)
    monkeypatch.setattr(
        "temporalio.activity.info",
        lambda: SimpleNamespace(workflow_id="child", workflow_run_id="child-run"),
    )
    await client.acquire_execution_lease(
        runtime_id=RUNTIME_ID,
        profile_id=PROFILE_ID,
        owner_id="attempt",
        metadata={"workflowId": "parent", "runId": "parent-run"},
    )
    assert manager._lease_holder_run_hints() == {"parent": "parent-run"}


@pytest.mark.asyncio
async def test_delegated_workflow_lease_does_not_borrow_the_activity_run(
    monkeypatch,
) -> None:
    manager, adapter, client = _wire(1)
    monkeypatch.setattr(
        "temporalio.activity.info",
        lambda: SimpleNamespace(workflow_id="child", workflow_run_id="child-run"),
    )
    await client.acquire_execution_lease(
        runtime_id=RUNTIME_ID,
        profile_id=PROFILE_ID,
        owner_id="parent",
        owner_is_workflow=True,
        metadata={"workflowId": "parent"},
    )
    assert adapter.updates[-1][2]["metadata"]["workflowId"] == "parent"
    assert manager._lease_holder_run_hints() == {}


@pytest.mark.parametrize("capacity", CAPACITIES)
@pytest.mark.asyncio
async def test_the_client_acquires_execution_capacity_through_the_manager(
    capacity: int,
) -> None:
    manager, adapter, client = _wire(capacity)

    leases = [
        await client.acquire_execution_lease(
            runtime_id=RUNTIME_ID,
            profile_id=PROFILE_ID,
            owner_id=f"agent-run-{index}",
            purpose=CredentialLeasePurpose.EXECUTION_OMNIGENT,
            metadata={"workflowId": f"agent-run-{index}"},
        )
        for index in range(capacity)
    ]

    assert [lease.mode for lease in leases] == [
        CredentialLeaseMode.SHARED_EXECUTION
    ] * capacity
    assert manager._profiles[PROFILE_ID].execution_lease_count == capacity
    assert adapter.started == [workflow_id_for_runtime(RUNTIME_ID)] * capacity
    assert {name for _, name, _ in adapter.updates} == {"AcquireSlotV2"}
    # Every grant is fenced, and no two grants share a generation.
    fences = [lease.fencing_generation for lease in leases]
    assert all(fence is not None for fence in fences)
    assert len(set(fences)) == capacity


@pytest.mark.parametrize("capacity", CAPACITIES)
@pytest.mark.asyncio
async def test_a_second_validator_for_one_identity_stands_down_through_the_client(
    capacity: int,
) -> None:
    """The coalescing contract must hold across the real client boundary."""

    manager, _adapter, client = _wire(capacity)
    identity = "opencode-model-catalog:v2:deadbeef"
    owner = _validation_owner(identity)

    async def _acquire() -> CredentialLease:
        return await client.acquire_maintenance_lease(
            runtime_id=RUNTIME_ID,
            profile_id=PROFILE_ID,
            owner_id=owner,
            purpose=CredentialLeasePurpose.CREDENTIAL_VALIDATION,
            metadata={"evidenceIdentity": identity, "ownerIsWorkflow": False},
        )

    first = await _acquire()
    second = await _acquire()

    assert first.already_held is False
    assert second.already_held is True
    assert first.mode is CredentialLeaseMode.SINGLE_FLIGHT_VALIDATION
    assert second.evidence_identity == identity
    assert manager._profiles[PROFILE_ID].current_leases == [owner]
    # Validation consumed no execution slot, so the profile still admits N.
    assert manager._profiles[PROFILE_ID].available_slots == capacity


@pytest.mark.parametrize("capacity", CAPACITIES)
@pytest.mark.asyncio
async def test_a_joiner_release_cannot_revoke_the_owners_authority(
    capacity: int,
) -> None:
    """Releasing after standing down must not cancel the in-flight probe."""

    manager, _adapter, client = _wire(capacity)
    identity = "opencode-model-catalog:v2:deadbeef"
    owner = _validation_owner(identity)
    metadata = {"evidenceIdentity": identity, "ownerIsWorkflow": False}

    holder = await client.acquire_maintenance_lease(
        runtime_id=RUNTIME_ID,
        profile_id=PROFILE_ID,
        owner_id=owner,
        purpose=CredentialLeasePurpose.CREDENTIAL_VALIDATION,
        metadata=metadata,
    )
    joiner = await client.acquire_maintenance_lease(
        runtime_id=RUNTIME_ID,
        profile_id=PROFILE_ID,
        owner_id=owner,
        purpose=CredentialLeasePurpose.CREDENTIAL_VALIDATION,
        metadata=metadata,
    )

    # The holder finishes and releases; a late joiner release replays after the
    # identity was granted again for the next refresh.
    await client.release_lease(holder)
    regranted = await client.acquire_maintenance_lease(
        runtime_id=RUNTIME_ID,
        profile_id=PROFILE_ID,
        owner_id=owner,
        purpose=CredentialLeasePurpose.CREDENTIAL_VALIDATION,
        metadata=metadata,
    )
    await client.release_lease(joiner)

    assert regranted.already_held is False
    assert regranted.fencing_generation != holder.fencing_generation
    assert manager._profiles[PROFILE_ID].current_leases == [owner]


@pytest.mark.parametrize("capacity", CAPACITIES)
@pytest.mark.asyncio
async def test_a_conflicting_identity_for_one_owner_fails_closed_at_the_client(
    capacity: int,
) -> None:
    _manager_wf, _adapter, client = _wire(capacity)
    owner = _validation_owner("opencode-model-catalog:v2:deadbeef")

    await client.acquire_maintenance_lease(
        runtime_id=RUNTIME_ID,
        profile_id=PROFILE_ID,
        owner_id=owner,
        purpose=CredentialLeasePurpose.CREDENTIAL_VALIDATION,
        metadata={"evidenceIdentity": "opencode-model-catalog:v2:deadbeef"},
    )

    with pytest.raises(WorkflowUpdateFailedError) as conflict:
        await client.acquire_maintenance_lease(
            runtime_id=RUNTIME_ID,
            profile_id=PROFILE_ID,
            owner_id=owner,
            purpose=CredentialLeasePurpose.CREDENTIAL_VALIDATION,
            metadata={"evidenceIdentity": "opencode-model-catalog:v2:cafebabe"},
        )

    assert conflict.value.cause.type == "ProviderProfileLeaseIdentityConflict"


@pytest.mark.parametrize("capacity", CAPACITIES)
@pytest.mark.asyncio
async def test_the_client_reattaches_when_the_manager_rolls_over(
    capacity: int,
) -> None:
    """The rollover detach is only durable if the client resubmits its request."""

    manager, adapter, client = _wire(capacity, credential_source="oauth_volume")
    profile = manager._profiles[PROFILE_ID]
    profile.reserve("agent-run-0", NOW, purpose="execution_omnigent")
    manager._rollover_requested = True

    def _successor_run_is_live(attempt: int) -> None:
        # The first attempt reaches a manager that is rolling over. By the time
        # the client resubmits, the successor run is serving requests.
        if attempt == 2:
            manager._rollover_requested = False

    adapter.on_update = _successor_run_is_live

    acquire = asyncio.ensure_future(
        client.acquire_maintenance_lease(
            runtime_id=RUNTIME_ID,
            profile_id=PROFILE_ID,
            owner_id="repair-a",
            purpose=CredentialLeasePurpose.CREDENTIAL_REPAIR,
            metadata={"workflowId": "repair-a", "ownerIsWorkflow": False},
        )
    )
    await _settle()

    # The detached request survived the rollover, so the resubmission is the
    # same queued request rather than a new one at the back of the line.
    assert profile.maintenance_queue_position("repair-a") == 0
    assert profile.exclusive_maintenance_waiters == 1

    profile.release("agent-run-0")
    adapter.stubs.wake()
    lease = await acquire

    assert lease.already_held is False
    assert lease.mode is CredentialLeaseMode.EXCLUSIVE_MAINTENANCE
    attempts = [name for _, name, _ in adapter.updates]
    assert attempts == ["AcquireCredentialMaintenanceLease"] * 2
    assert profile.exclusive_maintenance_queue == []


@pytest.mark.parametrize("capacity", CAPACITIES)
@pytest.mark.asyncio
async def test_a_rollover_that_never_resolves_surfaces_to_the_caller(
    capacity: int,
) -> None:
    """Reattachment is bounded: it must not retry a manager that never settles."""

    manager, adapter, client = _wire(capacity, credential_source="oauth_volume")
    manager._rollover_requested = True

    with pytest.raises(WorkflowUpdateFailedError) as rollover:
        await client.acquire_maintenance_lease(
            runtime_id=RUNTIME_ID,
            profile_id=PROFILE_ID,
            owner_id="repair-a",
            purpose=CredentialLeasePurpose.CREDENTIAL_REPAIR,
            metadata={"workflowId": "repair-a", "ownerIsWorkflow": False},
        )

    assert rollover.value.cause.type == MANAGER_ROLLOVER_ERROR_TYPE
    assert len(adapter.updates) == 3


@pytest.mark.parametrize("capacity", CAPACITIES)
@pytest.mark.asyncio
async def test_the_clients_release_frees_capacity_the_manager_can_reassign(
    capacity: int,
) -> None:
    manager, _adapter, client = _wire(capacity)

    leases = [
        await client.acquire_execution_lease(
            runtime_id=RUNTIME_ID,
            profile_id=PROFILE_ID,
            owner_id=f"agent-run-{index}",
            purpose=CredentialLeasePurpose.EXECUTION_OMNIGENT,
            metadata={"workflowId": f"agent-run-{index}"},
        )
        for index in range(capacity)
    ]
    assert manager._profiles[PROFILE_ID].available_slots == 0

    await client.release_lease(leases[0])

    assert manager._profiles[PROFILE_ID].available_slots == 1
    replacement = await client.acquire_execution_lease(
        runtime_id=RUNTIME_ID,
        profile_id=PROFILE_ID,
        owner_id="agent-run-replacement",
        purpose=CredentialLeasePurpose.EXECUTION_OMNIGENT,
        metadata={"workflowId": "agent-run-replacement"},
    )
    assert replacement.already_held is False
    assert manager._profiles[PROFILE_ID].available_slots == 0


@pytest.mark.parametrize("capacity", CAPACITIES)
@pytest.mark.asyncio
async def test_inspecting_a_lease_reports_the_identity_it_was_granted_for(
    capacity: int,
) -> None:
    _manager_wf, _adapter, client = _wire(capacity)
    identity = "opencode-model-catalog:v2:deadbeef"
    lease = await client.acquire_maintenance_lease(
        runtime_id=RUNTIME_ID,
        profile_id=PROFILE_ID,
        owner_id=_validation_owner(identity),
        purpose=CredentialLeasePurpose.CREDENTIAL_VALIDATION,
        metadata={"evidenceIdentity": identity, "ownerIsWorkflow": False},
    )

    inspected = await client.inspect_lease(lease)

    assert inspected["active"] is True
    assert inspected["profile_id"] == PROFILE_ID
    assert inspected["evidenceIdentity"] == identity
    assert inspected["purpose"] == CredentialLeasePurpose.CREDENTIAL_VALIDATION.value


@pytest.mark.parametrize("capacity", CAPACITIES)
@pytest.mark.asyncio
async def test_the_client_withdraws_its_waiter_when_reattachment_runs_out(
    capacity: int,
) -> None:
    """An exhausted caller must not leave its queue entry blocking the head.

    Every rollover preserves the request's queue entry with nobody left to
    reattach it, so the client withdraws the entry explicitly once its
    bounded reattach budget is spent.
    """

    manager, adapter, client = _wire(capacity, credential_source="oauth_volume")
    profile = manager._profiles[PROFILE_ID]
    manager._rollover_requested = True

    with pytest.raises(WorkflowUpdateFailedError):
        await client.acquire_maintenance_lease(
            runtime_id=RUNTIME_ID,
            profile_id=PROFILE_ID,
            owner_id="repair-a",
            purpose=CredentialLeasePurpose.CREDENTIAL_REPAIR,
            metadata={"workflowId": "repair-a", "ownerIsWorkflow": False},
        )

    assert len(adapter.updates) == 3
    assert [name for _, name, _ in adapter.signals] == [
        "withdraw_maintenance_waiter"
    ]
    _, _, withdrawal = adapter.signals[0]
    assert withdrawal["profile_id"] == PROFILE_ID
    assert withdrawal["requester_workflow_id"] == "repair-a"
    assert profile.maintenance_queue_position("repair-a") == -1
    assert profile.exclusive_maintenance_waiters == 0


@pytest.mark.asyncio
async def test_the_client_completes_a_cleanup_claim_through_verified_teardown() -> None:
    """The executing owner reports positive teardown with the acquired fence.

    MoonLadderStudios/MoonMind#1089 R4: the janitor-shaped consumer reads the
    manager's stable claim and completes it through ``report_cleanup_verified``
    quoting the fence and admitted identity with ``consumer_stopped=True``.
    """

    manager, adapter, client = _wire(2)
    lease = await client.acquire_execution_lease(
        runtime_id=RUNTIME_ID,
        profile_id=PROFILE_ID,
        owner_id="agent-run-0",
        purpose=CredentialLeasePurpose.EXECUTION_OMNIGENT,
        metadata={
            "workflowId": "agent-run-0",
            "runId": "run-admitted-0",
            "evidenceIdentity": "evidence-admitted-0",
        },
    )
    assert lease.fencing_generation is not None

    await client.report_cleanup_verified(
        lease,
        run_id="run-admitted-0",
        verified_by="omnigent-oauth-host-janitor",
    )

    assert [name for _, name, _ in adapter.signals] == ["report_cleanup_verified"]
    _, _, payload = adapter.signals[0]
    assert payload["lease_id"] == lease.lease_id
    assert payload["profile_id"] == PROFILE_ID
    assert payload["fencing_generation"] == lease.fencing_generation
    evidence = payload["teardown_evidence"]
    assert evidence["consumer_stopped"] is True
    assert evidence["verified_by"] == "omnigent-oauth-host-janitor"
    assert evidence["run_id"] == "run-admitted-0"
    assert evidence["evidence_identity"] == "evidence-admitted-0"


@pytest.mark.asyncio
async def test_the_client_reads_stable_cleanup_claims_from_the_manager() -> None:
    """Claims polled by the owner carry the stable ID, fence, and identities."""

    manager, adapter, client = _wire(2)
    lease = await client.acquire_execution_lease(
        runtime_id=RUNTIME_ID,
        profile_id=PROFILE_ID,
        owner_id="agent-run-0",
        purpose=CredentialLeasePurpose.EXECUTION_OMNIGENT,
        metadata={
            "workflowId": "agent-run-0",
            "runId": "run-admitted-0",
            "evidenceIdentity": "evidence-admitted-0",
        },
    )
    manager._cleanup_requested_leases.add(lease.lease_id)
    manager._cleanup_request_reasons[lease.lease_id] = "owner_terminal"

    claims = await client.get_cleanup_obligations(runtime_id=RUNTIME_ID)

    assert len(claims) == 1
    assert claims[0]["claim_id"] == f"{lease.lease_id}:{lease.fencing_generation}"
    assert claims[0]["runId"] == "run-admitted-0"
    assert claims[0]["evidenceIdentity"] == "evidence-admitted-0"


class _WedgedManagerAdapter:
    """A manager whose Update RPC fails the way a replay wedge fails.

    Temporal keeps a wedged singleton ``RUNNING`` while its workflow task
    retries, so ``UpdateWorkflowExecution`` returns ``FAILED_PRECONDITION``
    rather than a workflow-level Update failure. This double reproduces that
    exact seam, including the describe and history evidence recovery reads.
    """

    def __init__(
        self,
        *,
        heal_after: int | None = 1,
        nondeterminism: int = 3,
    ) -> None:
        #: ``None`` never heals, so a reattach cannot mask the original error.
        self.heal_after = heal_after
        self.nondeterminism = nondeterminism
        self.update_calls = 0
        self.describe_calls = 0
        self.terminated: list[str] = []
        self.started: list[str] = []
        self.healed = False

    async def get_client(self):
        return self

    async def start_workflow(self, _name, _payload, *, id, task_queue):
        del task_queue
        self.started.append(id)
        return self

    async def update_workflow(self, workflow_id, update_name, payload):
        del workflow_id, update_name, payload
        self.update_calls += 1
        if self.healed:
            return {"profile_id": PROFILE_ID, "lease_id": "lease-after-recovery"}
        if self.heal_after is not None and self.update_calls >= self.heal_after + 1:
            return {"profile_id": PROFILE_ID, "lease_id": "lease-after-recovery"}
        raise RPCError(
            "Unable to perform workflow execution update",
            RPCStatusCode.FAILED_PRECONDITION,
            b"",
        )

    async def describe_workflow(self, workflow_id, **_kwargs):
        del workflow_id
        self.describe_calls += 1
        return SimpleNamespace(
            status=SimpleNamespace(name="RUNNING"), run_id="wedged-run"
        )

    async def get_workflow_handle(self, workflow_id, *, run_id=None):
        del workflow_id, run_id
        outer = self

        class _Handle:
            async def fetch_history_events(self, **_kwargs):
                for _ in range(outer.nondeterminism):
                    yield _wedge_event()

            async def query(self, name):
                assert name == "get_state"
                # The replacement reports it finished restoring the ledger, so
                # the resubmission admits against real state.
                return {"startup_restored": True}

        return _Handle()

    async def terminate_workflow(self, workflow_id, *, reason, run_id=None):
        del reason, run_id
        self.terminated.append(workflow_id)
        self.healed = True


def _wedge_event():
    """A WorkflowTaskFailed event naming a nondeterminism cause."""

    class _Event:
        def __init__(self) -> None:
            self.workflow_task_failed_event_attributes = SimpleNamespace(
                cause=SimpleNamespace(
                    name="WORKFLOW_TASK_FAILED_CAUSE_NON_DETERMINISTIC_ERROR"
                ),
                failure=SimpleNamespace(message="[TMPRL1100] Nondeterminism error"),
            )

        def HasField(self, name: str) -> bool:
            return name == "workflow_task_failed_event_attributes"

    return _Event()


async def _free_ledger(_runtime_id: str) -> int:
    """A ledger with nothing left spending capacity."""

    return 0


@pytest.mark.asyncio
async def test_a_wedged_manager_is_replaced_and_the_update_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A blocked caller recovers the ledger instead of reporting an outage."""

    monkeypatch.setattr(
        "moonmind.provider_profiles.manager_recovery.count_unreleased_provider_leases",
        _free_ledger,
    )
    adapter = _WedgedManagerAdapter()
    client = ProviderProfileLeaseClient(adapter)

    result = await client._update_manager(RUNTIME_ID, "AcquireSlotV2", {"a": 1})

    assert result["lease_id"] == "lease-after-recovery"
    assert adapter.terminated == [workflow_id_for_runtime(RUNTIME_ID)]
    # One failed Update, one recovery, one retry. Recovery is not a loop.
    assert adapter.update_calls == 2
    assert adapter.describe_calls == 1


@pytest.mark.asyncio
async def test_a_wedge_that_cannot_be_recovered_reports_why(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A held lease keeps the manager; the caller learns that, not a timeout."""

    async def _held(_runtime: str) -> int:
        return 1

    monkeypatch.setattr(
        "moonmind.provider_profiles.manager_recovery.count_unreleased_provider_leases",
        _held,
    )
    adapter = _WedgedManagerAdapter()
    client = ProviderProfileLeaseClient(adapter)

    with pytest.raises(ProviderManagerUnavailableError) as excinfo:
        await client._update_manager(RUNTIME_ID, "AcquireSlotV2", {"a": 1})

    assert excinfo.value.recovery.refusal == MANAGER_HELD_LEASE_PRESENT
    assert excinfo.value.recovery.held_leases == 1
    assert adapter.terminated == []
    # The original RPC failure is preserved as the cause.
    assert isinstance(excinfo.value.__cause__, RPCError)


@pytest.mark.asyncio
async def test_a_manager_rpc_failure_that_is_not_a_wedge_reports_temporals_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without nondeterminism evidence, nothing is terminated or reclassified.

    Looking for a wedge and not finding one teaches the caller nothing Temporal
    had not already said, so the original RPC failure reaches it unchanged.
    """

    monkeypatch.setattr(
        "moonmind.provider_profiles.manager_recovery.count_unreleased_provider_leases",
        _free_ledger,
    )
    adapter = _WedgedManagerAdapter(nondeterminism=0, heal_after=None)
    client = ProviderProfileLeaseClient(adapter)

    with pytest.raises(RPCError) as excinfo:
        await client._update_manager(RUNTIME_ID, "AcquireSlotV2", {"a": 1})

    assert excinfo.value.status == RPCStatusCode.FAILED_PRECONDITION
    assert adapter.terminated == []
    # One bounded reattach was attempted before the original error stood.
    assert adapter.update_calls == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "purpose",
    [
        CredentialLeasePurpose.EXECUTION_OMNIGENT,
        CredentialLeasePurpose.CREDENTIAL_REPAIR,
    ],
)
@pytest.mark.parametrize("recover_on_attempt", [2, 3, None])
async def test_client_reattaches_while_owner_release_transition_is_pending(
    purpose: CredentialLeasePurpose,
    recover_on_attempt: int | None,
) -> None:
    """A bounded transition wait must not terminate an otherwise valid admission."""
    manager, adapter, client = _wire(1, credential_source="oauth_volume")
    manager._lease_transition_contract = True
    manager._owner_release_ordering = True
    manager._lease_grant_sequence = 1
    owner = "release-then-readmit"
    profile = manager._profiles[PROFILE_ID]
    profile.reserve(
        owner, NOW, purpose=purpose.value, metadata={"fencingGeneration": 1}
    )
    manager._index_lease(PROFILE_ID, owner, owner)
    manager._unresolved_releases[owner] = {
        "profile_id": PROFILE_ID,
        "fencing_generation": 1,
        "kind": "owner_release",
        "outcome": "retryable",
        "retryable": True,
    }

    async def pending_transition_timeout(predicate, timeout=None):
        assert not predicate()
        assert timeout == timedelta(seconds=60)
        adapter.stubs.advance(timeout)
        raise TimeoutError

    adapter.stubs.wait_condition = pending_transition_timeout

    def release_commits(attempt: int) -> None:
        if attempt == recover_on_attempt:
            profile.release(owner)
            manager._unindex_lease(owner)
            manager._unresolved_releases.pop(owner)

    adapter.on_update = release_commits
    acquire = (
        client.acquire_maintenance_lease
        if purpose.is_maintenance
        else client.acquire_execution_lease
    )
    arguments = {
        "runtime_id": RUNTIME_ID,
        "profile_id": PROFILE_ID,
        "owner_id": owner,
        "purpose": purpose,
        "metadata": {"workflowId": owner},
    }
    if recover_on_attempt is None:
        with pytest.raises(WorkflowUpdateFailedError) as pending:
            await acquire(**arguments)
        assert pending.value.cause.type == "ProviderProfileLeaseTransitionPending"
        assert profile.lease_fencing_generation(owner) == 1
        assert owner in manager._unresolved_releases
    else:
        lease = await acquire(**arguments)
        assert lease.already_held is False
        assert lease.fencing_generation == 2
        assert profile.lease_fencing_generation(owner) == 2
        assert owner not in manager._unresolved_releases

    expected_attempts = recover_on_attempt or 3
    assert len(adapter.updates) == expected_attempts
    assert all(update == adapter.updates[0] for update in adapter.updates)
    assert len(adapter.started) == expected_attempts
    assert adapter.signals == []
