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
from typing import Any, ClassVar
from unittest.mock import patch as mock_patch

import pytest
from temporalio import exceptions
from temporalio.client import WorkflowUpdateFailedError

from moonmind.provider_profiles.lease_client import (
    MANAGER_ROLLOVER_ERROR_TYPE,
    CredentialLease,
    CredentialLeaseMode,
    CredentialLeasePurpose,
    ProviderProfileLeaseClient,
    deterministic_lease_owner_id,
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
