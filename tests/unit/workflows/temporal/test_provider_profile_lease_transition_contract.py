"""The durable lease transition contract owns whether a slot is spent.

Source issue: MoonLadderStudios/MoonMind#3883.

The incremental lease row landed first (MoonLadderStudios/MoonMind#3878) and
the grant fence after it (#3879, #3880). What remained was the *ordering*
contract around those rows: a grant that is only reserved in memory is not
authority, an ambiguous write outcome is reconciled rather than guessed at, a
release keeps capacity unavailable until the ledger answers, expiry and
terminal ownership request resource cleanup instead of freeing a credential
consumer that may still be running, and neither path rewrites the runtime-wide
snapshot.

These tests pin the manager side of that contract at the activity boundary the
worker actually invokes. The durable half runs against a real PostgreSQL
cluster in
``tests/integration/omnigent/test_provider_lease_incremental_contract_postgres.py``.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import patch

import pytest
from temporalio import exceptions

from moonmind.provider_profiles.lease_client import (
    CredentialLeaseMode,
    DurableLeaseState,
    LeaseTransitionOutcome,
)
from moonmind.workflows.temporal.workflows.provider_profile_manager import (
    DB_LEASE_PERSISTENCE_PATCH,
    DURABLE_LEASE_GRANT_PATCH,
    LEASE_TRANSITION_CONTRACT_PATCH,
    PROVIDER_INCREMENTAL_LEASE_PATCH,
    CapacityScopeState,
    MoonMindProviderProfileManagerWorkflow,
    PendingRequest,
    ProfileSlotState,
    _CLEANUP_DELIVERY_PER_PASS,
    _LEASE_CLEANUP_ESCALATION_SECONDS,
)

NOW = datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc)
PROFILE_ID = "opencode-zen-free"
SCOPE_REF = "provider-scope:opencode-zen"


def _profile(**overrides: Any) -> ProfileSlotState:
    fields: dict[str, Any] = {
        "profile_id": PROFILE_ID,
        "max_parallel_runs": 2,
        "cooldown_after_429_seconds": 300,
        "rate_limit_policy": "backoff",
        "enabled": True,
        "launch_ready": True,
        "credential_source": "api_key",
        "purpose_aware_capacity": True,
        "capacity_scope_ref": SCOPE_REF,
    }
    fields.update(overrides)
    return ProfileSlotState(**fields)


def _manager(
    *,
    scope_generation: int = 1,
    profile: ProfileSlotState | None = None,
) -> MoonMindProviderProfileManagerWorkflow:
    wf = MoonMindProviderProfileManagerWorkflow()
    wf._runtime_id = "opencode"
    wf._durable_maintenance_queue = True
    wf._lease_transition_contract = True
    wf._purpose_aware_capacity_ledger = True
    resolved = profile if profile is not None else _profile()
    wf._profiles = {resolved.profile_id: resolved}
    wf._scopes = {
        SCOPE_REF: CapacityScopeState(
            scope_ref=SCOPE_REF,
            runtime_id="opencode",
            generation=scope_generation,
            configured_limit=resolved.max_parallel_runs,
            effective_limit=resolved.max_parallel_runs,
        )
    }
    return wf


class _Ledger:
    """A recording stand-in for ``provider_profile.sync_slot_leases``."""

    def __init__(self, responses: dict[str, Any] | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.responses = responses or {}

    async def __call__(self, name: str, payload: dict[str, Any], **_kwargs: Any):
        assert name == "provider_profile.sync_slot_leases"
        self.calls.append(payload)
        response = self.responses.get(payload["action"])
        if isinstance(response, list):
            response = response.pop(0) if response else None
        if isinstance(response, Exception):
            raise response
        if callable(response):
            return response(payload)
        if response is None:
            return {}
        return response

    def actions(self) -> list[str]:
        return [call["action"] for call in self.calls]

    def rows_for(self, action: str) -> list[dict[str, Any]]:
        return [
            row
            for call in self.calls
            if call["action"] == action
            for row in call.get("leases") or []
        ]


@contextlib.contextmanager
def _patched(ledger: _Ledger, *, enabled: set[str] | None = None, now=NOW):
    """Run manager code with a controllable patch set and ledger."""

    active = enabled if enabled is not None else {
        DB_LEASE_PERSISTENCE_PATCH,
        DURABLE_LEASE_GRANT_PATCH,
        PROVIDER_INCREMENTAL_LEASE_PATCH,
        LEASE_TRANSITION_CONTRACT_PATCH,
    }
    async def _dispatch(name: str, payload: dict[str, Any], **kwargs: Any):
        # A real coroutine function, so ``AsyncMock`` awaits it rather than
        # handing the caller an un-awaited coroutine.
        return await ledger(name, payload, **kwargs)

    with patch(
        "temporalio.workflow.patched", side_effect=lambda name: name in active
    ), patch(
        "temporalio.workflow.execute_activity", side_effect=_dispatch
    ), patch(
        "temporalio.workflow.now", return_value=now
    ):
        yield


# ---------------------------------------------------------------------------
# Item 1: the grant carries the identity it actually acquired
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_grant_records_the_scope_generation_it_was_admitted_against() -> None:
    """A hard-coded scope generation asserts authority the grant never got."""

    ledger = _Ledger({"grant": {"granted": True, "duplicate": False}})
    wf = _manager(scope_generation=7)
    profile = wf._profiles[PROFILE_ID]
    metadata = wf._grant_metadata(
        {"evidenceIdentity": "evidence-1"},
        fencing_generation=3,
        profile=profile,
        lease_mode=CredentialLeaseMode.SHARED_EXECUTION,
    )
    profile.reserve("agent-run-1", NOW, purpose="execution_omnigent", metadata=metadata)

    with _patched(ledger):
        assert await wf._persist_lease_grant(
            profile, "agent-run-1", purpose="execution_omnigent", metadata=metadata
        )

    row = ledger.rows_for("grant")[0]
    assert row["scope_generation"] == 7
    assert row["capacity_scope_ref"] == SCOPE_REF
    assert row["compatibility_class"] == CredentialLeaseMode.SHARED_EXECUTION.value
    assert row["fencing_generation"] == 3
    assert row["lease_state"] == DurableLeaseState.HELD.value


@pytest.mark.asyncio
async def test_a_maintenance_grant_records_its_own_compatibility_class() -> None:
    """Two modes on one profile are not the same compatibility identity."""

    ledger = _Ledger({"grant": {"granted": True, "duplicate": False}})
    wf = _manager(scope_generation=2)
    profile = wf._profiles[PROFILE_ID]
    metadata = wf._grant_metadata(
        {},
        fencing_generation=4,
        profile=profile,
        lease_mode=CredentialLeaseMode.EXCLUSIVE_MAINTENANCE,
    )
    profile.reserve("owner-1", NOW, purpose="oauth_reconnect", metadata=metadata)

    with _patched(ledger):
        await wf._persist_lease_grant(
            profile, "owner-1", purpose="oauth_reconnect", metadata=metadata
        )

    row = ledger.rows_for("grant")[0]
    assert row["compatibility_class"] == CredentialLeaseMode.EXCLUSIVE_MAINTENANCE.value
    assert row["scope_generation"] == 2


def test_no_credential_value_can_reach_a_lease_row() -> None:
    """The allowlist is the boundary: unknown caller keys never persist."""

    wf = _manager()
    safe = wf._safe_lease_metadata(
        {
            "metadata": {
                "workflowId": "agent-run-1",
                "evidenceIdentity": "evidence-1",
                "apiKey": "sk-live-not-a-real-key",
                "authorization": "Bearer nope",
                "oauthRefreshToken": "rt-nope",
            }
        }
    )

    assert safe == {"workflowId": "agent-run-1", "evidenceIdentity": "evidence-1"}


# ---------------------------------------------------------------------------
# Item 2: a pending reservation is not a usable grant
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_concurrent_caller_never_joins_an_uncommitted_reservation() -> None:
    """``already_held`` for a pending grant publishes authority nobody holds."""

    grant_started = asyncio.Event()
    release_grant = asyncio.Event()

    async def _slow_grant(_name: str, payload: dict[str, Any], **_kwargs: Any):
        if payload["action"] != "grant":
            return {}
        grant_started.set()
        await release_grant.wait()
        return {"granted": True, "duplicate": False}

    wf = _manager()
    with patch(
        "temporalio.workflow.patched",
        side_effect=lambda name: name
        in {
            DB_LEASE_PERSISTENCE_PATCH,
            DURABLE_LEASE_GRANT_PATCH,
            PROVIDER_INCREMENTAL_LEASE_PATCH,
            LEASE_TRANSITION_CONTRACT_PATCH,
        },
    ), patch(
        "temporalio.workflow.execute_activity", side_effect=_slow_grant
    ), patch(
        "temporalio.workflow.now", return_value=NOW
    ), patch(
        "temporalio.workflow.wait_condition", new=_immediate_wait_condition
    ):
        first = asyncio.create_task(
            wf.acquire_slot(
                {"requester_workflow_id": "agent-run-1", "runtime_id": "opencode"}
            )
        )
        await asyncio.wait_for(grant_started.wait(), timeout=5)

        # The reservation exists in memory but has no durable outcome.
        assert wf._lease_grant_is_pending("agent-run-1") is True
        assert wf._profile_id_for_lease("agent-run-1") == PROFILE_ID

        second = asyncio.create_task(
            wf.acquire_slot(
                {"requester_workflow_id": "agent-run-1", "runtime_id": "opencode"}
            )
        )
        await asyncio.sleep(0)
        assert not second.done(), "a pending reservation was returned as a grant"

        release_grant.set()
        first_result = await asyncio.wait_for(first, timeout=5)
        second_result = await asyncio.wait_for(second, timeout=5)

    assert first_result["already_held"] is False
    # The second caller joins the committed result, not the pending one.
    assert second_result["already_held"] is True
    assert second_result["profile_id"] == PROFILE_ID
    assert (
        second_result["lease_fencing_generation"]
        == first_result["lease_fencing_generation"]
    )


async def _immediate_wait_condition(predicate, timeout=None):
    """Yield until the predicate holds, without Temporal's event loop."""

    while not predicate():
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_a_rolled_back_reservation_is_not_left_marked_pending() -> None:
    """A failed grant must clear the handoff, or the manager deadlocks itself."""

    ledger = _Ledger({"grant": {"error": "lease identity conflict"}})
    wf = _manager()

    with _patched(ledger):
        with pytest.raises(exceptions.ApplicationError) as excinfo:
            await wf.acquire_slot(
                {"requester_workflow_id": "agent-run-1", "runtime_id": "opencode"}
            )

    assert excinfo.value.type == "ProviderProfileLeasePersistenceFailed"
    assert wf._lease_grant_is_pending("agent-run-1") is False
    assert wf._profile_id_for_lease("agent-run-1") is None
    assert wf._profiles[PROFILE_ID].current_leases == []


# ---------------------------------------------------------------------------
# Item 3: an ambiguous write outcome is reconciled, not guessed at
# ---------------------------------------------------------------------------


def _committed_row(**overrides: Any) -> dict[str, Any]:
    """The complete durable identity of the grant the manager attempted.

    A reconciliation that compares fewer fields than this would accept a
    migrated or conflicting row that merely shares the lease ID, profile,
    state and generation.
    """

    row = {
        "lease_id": "agent-run-1",
        "workflow_id": "agent-run-1",
        "profile_id": PROFILE_ID,
        "owner_id": "agent-run-1",
        "owner_kind": "workflow",
        "purpose": "execution_direct",
        "compatibility_class": CredentialLeaseMode.SHARED_EXECUTION.value,
        "capacity_scope_ref": SCOPE_REF,
        "scope_generation": 1,
        "credential_generation": None,
        "execution_plan_ref": None,
        "evidence_identity": "",
        "lease_state": DurableLeaseState.HELD.value,
        "fencing_generation": 5,
    }
    row.update(overrides)
    return row


@pytest.mark.asyncio
async def test_a_lost_commit_acknowledgment_is_reconciled_as_committed() -> None:
    """A timeout after commit must not abandon a live grant."""

    ledger = _Ledger(
        {
            "grant": RuntimeError("database timeout"),
            "describe": {"found": True, "lease": _committed_row()},
        }
    )
    wf = _manager()
    profile = wf._profiles[PROFILE_ID]
    metadata = wf._grant_metadata(
        {}, fencing_generation=5, profile=profile,
        lease_mode=CredentialLeaseMode.SHARED_EXECUTION,
    )
    profile.reserve("agent-run-1", NOW, purpose="execution_direct", metadata=metadata)

    with _patched(ledger):
        persisted = await wf._persist_lease_grant(
            profile, "agent-run-1", metadata=metadata
        )

    assert persisted is True
    assert ledger.actions() == ["grant", "describe"]


@pytest.mark.asyncio
async def test_reconciliation_refuses_a_row_the_ledger_does_not_hold() -> None:
    """A different fence, profile or state is not this caller's grant."""

    for lease in (
        _committed_row(fencing_generation=4),
        _committed_row(profile_id="other"),
        _committed_row(lease_state=DurableLeaseState.RELEASED.value),
    ):
        ledger = _Ledger(
            {
                "grant": RuntimeError("database timeout"),
                "describe": {"found": True, "lease": lease},
            }
        )
        wf = _manager()
        profile = wf._profiles[PROFILE_ID]
        metadata = wf._grant_metadata(
            {}, fencing_generation=5, profile=profile,
            lease_mode=CredentialLeaseMode.SHARED_EXECUTION,
        )
        profile.reserve("agent-run-1", NOW, metadata=metadata)

        with _patched(ledger):
            assert (
                await wf._persist_lease_grant(profile, "agent-run-1", metadata=metadata)
                is False
            )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "difference",
    [
        {"owner_id": "someone-else"},
        {"owner_kind": "activity"},
        {"purpose": "credential_validation"},
        {"compatibility_class": CredentialLeaseMode.EXCLUSIVE_MAINTENANCE.value},
        {"capacity_scope_ref": "provider-scope:other"},
        {"scope_generation": 9},
        {"credential_generation": 4},
        {"execution_plan_ref": "omnigent-execution-plan:sha256:other"},
        {"evidence_identity": "evidence-other"},
    ],
)
async def test_reconciliation_refuses_a_row_with_another_grant_identity(
    difference: dict[str, Any],
) -> None:
    """A migrated or conflicting row can share the lease ID, fence and state.

    MoonLadderStudios/MoonMind#3883: inheriting it would hand the caller
    authority — an owner, a purpose, a plan, a credential generation — that the
    ledger never granted, so every immutable grant field has to agree.
    """

    ledger = _Ledger(
        {
            "grant": RuntimeError("database timeout"),
            "describe": {"found": True, "lease": _committed_row(**difference)},
        }
    )
    wf = _manager()
    profile = wf._profiles[PROFILE_ID]
    metadata = wf._grant_metadata(
        {
            "credentialGeneration": None,
            "executionPlanRef": None,
        },
        fencing_generation=5,
        profile=profile,
        lease_mode=CredentialLeaseMode.SHARED_EXECUTION,
    )
    profile.reserve("agent-run-1", NOW, purpose="execution_direct", metadata=metadata)

    with _patched(ledger):
        assert (
            await wf._persist_lease_grant(profile, "agent-run-1", metadata=metadata)
            is False
        )


@pytest.mark.asyncio
async def test_an_unreadable_ledger_leaves_capacity_blocked() -> None:
    """Still ambiguous is still failure; never announce unconfirmed authority."""

    ledger = _Ledger(
        {
            "grant": RuntimeError("database timeout"),
            "describe": RuntimeError("database still unreachable"),
        }
    )
    wf = _manager()
    profile = wf._profiles[PROFILE_ID]
    metadata = wf._grant_metadata(
        {}, fencing_generation=5, profile=profile,
        lease_mode=CredentialLeaseMode.SHARED_EXECUTION,
    )
    profile.reserve("agent-run-1", NOW, metadata=metadata)

    with _patched(ledger):
        assert (
            await wf._persist_lease_grant(profile, "agent-run-1", metadata=metadata)
            is False
        )


@pytest.mark.asyncio
async def test_a_pre_contract_history_does_not_reconcile() -> None:
    """Replay safety: an older history keeps its exact recorded commands."""

    ledger = _Ledger({"grant": RuntimeError("database timeout")})
    wf = _manager()
    wf._lease_transition_contract = False
    profile = wf._profiles[PROFILE_ID]
    profile.reserve("agent-run-1", NOW)

    with _patched(
        ledger,
        enabled={
            DB_LEASE_PERSISTENCE_PATCH,
            DURABLE_LEASE_GRANT_PATCH,
            PROVIDER_INCREMENTAL_LEASE_PATCH,
        },
    ):
        assert await wf._persist_lease_grant(profile, "agent-run-1") is False

    assert ledger.actions() == ["grant"]


# ---------------------------------------------------------------------------
# Item 4 / acceptance 4: a release keeps capacity unavailable until it resolves
# ---------------------------------------------------------------------------


def _held_manager(ledger_generation: int = 3) -> MoonMindProviderProfileManagerWorkflow:
    wf = _manager()
    profile = wf._profiles[PROFILE_ID]
    metadata = wf._grant_metadata(
        {}, fencing_generation=ledger_generation, profile=profile,
        lease_mode=CredentialLeaseMode.SHARED_EXECUTION,
    )
    profile.reserve("agent-run-1", NOW, metadata=metadata)
    wf._index_lease(PROFILE_ID, "agent-run-1", "agent-run-1")
    wf._lease_grant_sequence = ledger_generation
    return wf


@pytest.mark.parametrize(
    "response",
    [
        RuntimeError("artifacts worker unavailable"),
        {"error": "lease identity conflict"},
        {"released": False, "outcome": LeaseTransitionOutcome.STALE.value},
        {"released": False, "outcome": LeaseTransitionOutcome.CONFLICT.value},
    ],
    ids=["activity-failed", "structured-error", "stale", "conflict"],
)
@pytest.mark.asyncio
async def test_an_unresolved_release_never_makes_capacity_reusable(response) -> None:
    """A warning is not a release. The slot stays spent until the ledger says so."""

    ledger = _Ledger({"release_one": response})
    wf = _held_manager()

    with _patched(ledger):
        await wf.release_slot(
            {"profile_id": PROFILE_ID, "requester_workflow_id": "agent-run-1"}
        )

    assert wf._profiles[PROFILE_ID].current_leases == ["agent-run-1"]
    assert wf._profile_id_for_lease("agent-run-1") == PROFILE_ID
    assert "agent-run-1" in wf._unresolved_releases
    # The release quotes the generation the manager actually holds.
    assert ledger.rows_for("release_one")[0]["fencing_generation"] == 3


@pytest.mark.asyncio
async def test_a_released_lease_frees_capacity_and_quotes_its_fence() -> None:
    ledger = _Ledger(
        {"release_one": {"released": True, "outcome": LeaseTransitionOutcome.RELEASED.value}}
    )
    wf = _held_manager(ledger_generation=9)

    with _patched(ledger):
        await wf.release_slot(
            {"profile_id": PROFILE_ID, "requester_workflow_id": "agent-run-1"}
        )

    assert wf._profiles[PROFILE_ID].current_leases == []
    assert wf._unresolved_releases == {}
    row = ledger.rows_for("release_one")[0]
    assert row["fencing_generation"] == 9
    assert row["profile_id"] == PROFILE_ID
    # The ordering contract: the ledger is asked before capacity is published.
    assert ledger.actions() == ["release_one"]


@pytest.mark.asyncio
async def test_a_duplicate_release_is_idempotent_not_a_second_free() -> None:
    ledger = _Ledger(
        {
            "release_one": [
                {"released": True, "outcome": LeaseTransitionOutcome.RELEASED.value},
                {
                    "released": True,
                    "duplicate": True,
                    "outcome": LeaseTransitionOutcome.ALREADY_RELEASED.value,
                },
            ]
        }
    )
    wf = _held_manager()

    with _patched(ledger):
        await wf.release_slot(
            {"profile_id": PROFILE_ID, "requester_workflow_id": "agent-run-1"}
        )
        await wf.release_slot(
            {"profile_id": PROFILE_ID, "requester_workflow_id": "agent-run-1"}
        )

    assert wf._profiles[PROFILE_ID].current_leases == []
    assert wf._profiles[PROFILE_ID].execution_lease_count == 0
    assert wf._unresolved_releases == {}


@pytest.mark.asyncio
async def test_an_unresolved_release_is_retried_and_then_frees_capacity() -> None:
    """Capacity is neither leaked nor published early: it is reconciled."""

    ledger = _Ledger(
        {
            "release_one": [
                RuntimeError("artifacts worker unavailable"),
                {"released": True, "outcome": LeaseTransitionOutcome.RELEASED.value},
            ]
        }
    )
    wf = _held_manager()

    with _patched(ledger):
        await wf.release_slot(
            {"profile_id": PROFILE_ID, "requester_workflow_id": "agent-run-1"}
        )
        assert wf._profiles[PROFILE_ID].current_leases == ["agent-run-1"]
        await wf._retry_unresolved_releases()

    assert wf._profiles[PROFILE_ID].current_leases == []
    assert wf._unresolved_releases == {}
    assert ledger.actions() == ["release_one", "release_one"]


@pytest.mark.asyncio
async def test_a_conflicting_release_becomes_evidence_not_an_endless_retry() -> None:
    """A ledger that describes a different authority cannot converge by retry."""

    ledger = _Ledger(
        {"release_one": {"released": False, "outcome": LeaseTransitionOutcome.CONFLICT.value}}
    )
    wf = _held_manager()

    with _patched(ledger):
        await wf.release_slot(
            {"profile_id": PROFILE_ID, "requester_workflow_id": "agent-run-1"}
        )
        await wf._retry_unresolved_releases()
        await wf._retry_unresolved_releases()

    assert ledger.actions() == ["release_one"], "a conflict was retried"
    assert wf._profiles[PROFILE_ID].current_leases == ["agent-run-1"]
    assert wf._unresolved_releases["agent-run-1"]["retryable"] is False
    assert [entry["kind"] for entry in wf._lease_index_conflicts] == [
        "unresolved_release"
    ]


@pytest.mark.asyncio
async def test_a_release_without_a_known_fence_asserts_none(
) -> None:
    """A hard-coded fence of 1 asserts authority the manager never acquired."""

    ledger = _Ledger(
        {"release_one": {"released": True, "outcome": LeaseTransitionOutcome.RELEASED.value}}
    )
    wf = _manager()
    # The profile was removed while the owner still held a durable row.
    wf._profiles = {}

    with _patched(ledger):
        await wf.release_slot(
            {"profile_id": PROFILE_ID, "requester_workflow_id": "agent-run-1"}
        )

    assert ledger.rows_for("release_one")[0]["fencing_generation"] == 0


def test_every_release_outcome_maps_onto_the_typed_contract() -> None:
    wf = _manager()
    assert wf._release_outcome({"released": True}, "x") == "released"
    assert wf._release_outcome({"released": True, "duplicate": True}, "x") == (
        "already_released"
    )
    assert wf._release_outcome({"released": False, "stale": True}, "x") == "stale"
    assert wf._release_outcome({"error": "boom"}, "x") == "conflict"
    assert wf._release_outcome({"outcome": "nonsense"}, "x") == "retryable"
    assert wf._release_outcome(None, "x") == "retryable"


# ---------------------------------------------------------------------------
# Item 5 / item 8: expiry and terminal ownership request cleanup, per lease
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_expiry_requests_cleanup_instead_of_freeing_a_live_consumer() -> None:
    """MoonLadderStudios/MoonMind#1089: expiry is not proof the holder stopped."""

    ledger = _Ledger(
        {
            "request_cleanup": {
                "outcome": LeaseTransitionOutcome.CLEANUP_REQUESTED.value,
                "cleanup_requested": True,
            }
        }
    )
    wf = _held_manager()
    profile = wf._profiles[PROFILE_ID]
    profile.max_lease_duration_seconds = 60
    profile.lease_granted_at["agent-run-1"] = (NOW - timedelta(hours=3)).isoformat()

    with _patched(ledger):
        await wf._request_cleanup_for_expired_leases()

    assert ledger.actions() == ["request_cleanup"]
    assert profile.current_leases == ["agent-run-1"], "an expired slot was freed"
    assert "agent-run-1" in wf._cleanup_requested_leases

    # A second pass must not re-request the same durable cleanup.
    with _patched(ledger):
        await wf._request_cleanup_for_expired_leases()
    assert ledger.actions() == ["request_cleanup"]


@pytest.mark.asyncio
async def test_expiry_never_rewrites_the_runtime_wide_snapshot() -> None:
    ledger = _Ledger(
        {
            "request_cleanup": {
                "outcome": LeaseTransitionOutcome.CLEANUP_REQUESTED.value
            }
        }
    )
    wf = _held_manager()
    profile = wf._profiles[PROFILE_ID]
    profile.max_lease_duration_seconds = 60
    profile.lease_granted_at["agent-run-1"] = (NOW - timedelta(hours=3)).isoformat()

    with _patched(ledger):
        await wf._request_cleanup_for_expired_leases()

    assert "save" not in ledger.actions()


@pytest.mark.asyncio
async def test_terminal_reclamation_requests_cleanup_without_freeing_the_slot() -> None:
    """MoonLadderStudios/MoonMind#1089: terminal state is not teardown proof.

    Reclamation costs one fenced ``request_cleanup`` transition, not a
    snapshot rewrite and not a capacity-freeing release. The slot stays
    spent until the designated cleanup owner confirms verified teardown.
    """

    ledger = _Ledger(
        {
            "request_cleanup": {
                "outcome": LeaseTransitionOutcome.CLEANUP_REQUESTED.value,
                "cleanup_requested": True,
            }
        }
    )
    wf = _held_manager(ledger_generation=6)
    # A second, still-running holder must not be touched.
    profile = wf._profiles[PROFILE_ID]
    profile.max_parallel_runs = 4
    other_metadata = wf._grant_metadata(
        {}, fencing_generation=7, profile=profile,
        lease_mode=CredentialLeaseMode.SHARED_EXECUTION,
    )
    profile.reserve("agent-run-2", NOW, metadata=other_metadata)
    wf._index_lease(PROFILE_ID, "agent-run-2", "agent-run-2")

    with _patched(ledger):
        await wf._reclaim_terminal_leases_durably(
            {
                "agent-run-1": {"running": False, "status": "TERMINATED"},
                "agent-run-2": {"running": True, "status": "RUNNING"},
            }
        )

    assert ledger.actions() == ["request_cleanup"]
    assert "release_one" not in ledger.actions()
    rows = ledger.rows_for("request_cleanup")
    assert rows[0]["lease_id"] == "agent-run-1"
    assert rows[0]["fencing_generation"] == 6
    assert rows[0]["reason"] == "owner_terminal"
    assert profile.current_leases == ["agent-run-1", "agent-run-2"]
    assert "agent-run-1" in wf._cleanup_requested_leases


@pytest.mark.asyncio
async def test_terminal_not_found_keeps_profile_and_scope_capacity_spent() -> None:
    """MoonLadderStudios/MoonMind#1089: a missing record frees no capacity.

    A lease whose workflow is terminal or NOT_FOUND while its exact runtime
    consumer remains live must keep both its profile slot and its
    shared-scope unit unavailable before verified teardown.
    """

    ledger = _Ledger(
        {
            "request_cleanup": {
                "outcome": LeaseTransitionOutcome.CLEANUP_REQUESTED.value,
                "cleanup_requested": True,
            }
        }
    )
    wf = _held_manager(ledger_generation=6)
    profile = wf._profiles[PROFILE_ID]
    profile.max_parallel_runs = 1
    wf._scopes[SCOPE_REF].configured_limit = 1
    wf._scopes[SCOPE_REF].effective_limit = 1

    with _patched(ledger):
        await wf._reclaim_terminal_leases_durably(
            {"agent-run-1": {"running": False, "status": "NOT_FOUND"}}
        )

    assert ledger.actions() == ["request_cleanup"]
    assert profile.current_leases == ["agent-run-1"]
    assert wf._scope_active_units(SCOPE_REF) == 1
    assert wf._scope_is_available(wf._scopes[SCOPE_REF]) is False
    assert wf._profile_effective_available(profile) is False


@pytest.mark.asyncio
async def test_verified_teardown_release_frees_each_unit_once() -> None:
    """MoonLadderStudios/MoonMind#1089: release only from positive evidence.

    A terminal-owner lease first moves to the capacity-consuming
    cleanup-requested state. Only the designated cleanup owner's verified
    teardown report frees it, quoting the acquired fence — exactly once.
    """

    ledger = _Ledger(
        {
            "request_cleanup": {
                "outcome": LeaseTransitionOutcome.CLEANUP_REQUESTED.value,
                "cleanup_requested": True,
            },
            "release_verified": {
                "released": True,
                "outcome": LeaseTransitionOutcome.RELEASED.value,
            },
        }
    )
    wf = _held_manager(ledger_generation=6)
    profile = wf._profiles[PROFILE_ID]

    with _patched(ledger):
        await wf._reclaim_terminal_leases_durably(
            {"agent-run-1": {"running": False, "status": "TERMINATED"}}
        )
        assert profile.current_leases == ["agent-run-1"]
        await wf.report_cleanup_verified(
            {
                "lease_id": "agent-run-1",
                "profile_id": PROFILE_ID,
                "fencing_generation": 6,
                "teardown_evidence": {
                    "consumer_stopped": True,
                    "verified_by": "runtime-janitor",
                },
            }
        )

    assert ledger.actions() == ["request_cleanup", "release_verified"]
    row = ledger.rows_for("release_verified")[0]
    assert row["lease_id"] == "agent-run-1"
    assert row["fencing_generation"] == 6
    assert row["reason"] == "cleanup_verified"
    assert row["teardown_evidence"]["consumer_stopped"] is True
    assert profile.current_leases == []
    assert wf._cleanup_requested_leases == set()
    assert wf._unresolved_releases == {}


@pytest.mark.asyncio
async def test_a_duplicate_verified_report_is_idempotent_not_a_second_free() -> None:
    """A retried teardown report reconciles; it never frees a replacement."""

    ledger = _Ledger(
        {
            "release_verified": [
                {
                    "released": True,
                    "outcome": LeaseTransitionOutcome.RELEASED.value,
                },
                {
                    "released": True,
                    "duplicate": True,
                    "outcome": LeaseTransitionOutcome.ALREADY_RELEASED.value,
                },
            ]
        }
    )
    wf = _held_manager(ledger_generation=6)

    evidence = {"consumer_stopped": True, "verified_by": "runtime-janitor"}
    with _patched(ledger):
        await wf.report_cleanup_verified(
            {
                "lease_id": "agent-run-1",
                "profile_id": PROFILE_ID,
                "fencing_generation": 6,
                "teardown_evidence": evidence,
            }
        )
        assert wf._profiles[PROFILE_ID].current_leases == []
        # Redelivery after the free: nothing left to free, no conflict raised.
        await wf.report_cleanup_verified(
            {
                "lease_id": "agent-run-1",
                "profile_id": PROFILE_ID,
                "fencing_generation": 6,
                "teardown_evidence": evidence,
            }
        )

    assert ledger.actions() == ["release_verified"]
    assert wf._unresolved_releases == {}


@pytest.mark.asyncio
async def test_a_stale_verified_report_cannot_free_replacement_authority() -> None:
    """A delayed report for a superseded generation is ignored in memory."""

    ledger = _Ledger({"release_verified": {"released": True, "outcome": "released"}})
    wf = _held_manager(ledger_generation=6)
    # The lease was granted again under a newer generation.
    wf._profiles[PROFILE_ID].lease_metadata["agent-run-1"]["fencingGeneration"] = 9
    wf._lease_grant_sequence = 9

    with _patched(ledger):
        await wf.report_cleanup_verified(
            {
                "lease_id": "agent-run-1",
                "profile_id": PROFILE_ID,
                "fencing_generation": 6,
                "teardown_evidence": {"consumer_stopped": True},
            }
        )

    assert ledger.actions() == [], "a stale report reached the ledger"
    assert wf._profiles[PROFILE_ID].current_leases == ["agent-run-1"]


@pytest.mark.asyncio
async def test_a_verified_report_without_positive_evidence_frees_nothing() -> None:
    """A missing consumer_stopped flag is not teardown evidence."""

    ledger = _Ledger({"release_verified": {"released": True, "outcome": "released"}})
    wf = _held_manager(ledger_generation=6)

    with _patched(ledger):
        await wf.report_cleanup_verified(
            {
                "lease_id": "agent-run-1",
                "profile_id": PROFILE_ID,
                "fencing_generation": 6,
                "teardown_evidence": {"verified_by": "runtime-janitor"},
            }
        )

    assert ledger.actions() == []
    assert wf._profiles[PROFILE_ID].current_leases == ["agent-run-1"]


@pytest.mark.asyncio
async def test_a_verified_release_without_requested_cleanup_stays_spent() -> None:
    """The ledger refuses to skip the cleanup-request ordering step."""

    ledger = _Ledger(
        {
            "release_verified": {
                "released": False,
                "outcome": LeaseTransitionOutcome.CONFLICT.value,
                "error": "cleanup not requested",
            }
        }
    )
    wf = _held_manager(ledger_generation=6)

    with _patched(ledger):
        await wf.report_cleanup_verified(
            {
                "lease_id": "agent-run-1",
                "profile_id": PROFILE_ID,
                "fencing_generation": 6,
                "teardown_evidence": {"consumer_stopped": True},
            }
        )

    assert wf._profiles[PROFILE_ID].current_leases == ["agent-run-1"]
    pending = wf._unresolved_releases["agent-run-1"]
    assert pending["kind"] == "verified_cleanup"
    assert pending["retryable"] is False


@pytest.mark.asyncio
async def test_a_retryable_verified_release_is_retried_with_its_evidence() -> None:
    """Lost acknowledgments re-drive the gated transition, not owner release."""

    ledger = _Ledger(
        {
            "release_verified": [
                RuntimeError("artifacts worker unavailable"),
                {
                    "released": True,
                    "outcome": LeaseTransitionOutcome.RELEASED.value,
                },
            ]
        }
    )
    wf = _held_manager(ledger_generation=6)
    evidence = {"consumer_stopped": True, "verified_by": "agent-run"}

    with _patched(ledger):
        await wf.report_cleanup_verified(
            {
                "lease_id": "agent-run-1",
                "profile_id": PROFILE_ID,
                "fencing_generation": 6,
                "teardown_evidence": evidence,
            }
        )
        assert wf._profiles[PROFILE_ID].current_leases == ["agent-run-1"]
        assert wf._unresolved_releases["agent-run-1"]["kind"] == "verified_cleanup"
        # The obligation survives Continue-As-New with its evidence intact.
        successor = _held_manager(ledger_generation=6)
        successor._restore_lease_obligations(wf._build_continue_as_new_input())
        assert successor._unresolved_releases["agent-run-1"]["kind"] == (
            "verified_cleanup"
        )
        assert successor._unresolved_releases["agent-run-1"]["teardown_evidence"] == (
            evidence
        )
        await successor._retry_unresolved_releases()

    assert successor._profiles[PROFILE_ID].current_leases == []
    assert successor._unresolved_releases == {}
    assert ledger.actions() == ["release_verified", "release_verified"]


def test_consumer_classification_names_reconciliation_needed() -> None:
    """Missing identity is reconciliation-needed, never proof of no process."""

    wf = _held_manager()
    profile = wf._profiles[PROFILE_ID]

    assert wf._lease_consumer_class(profile, "agent-run-1") == "reservation"

    profile.lease_metadata["agent-run-1"] = {
        **profile.lease_metadata["agent-run-1"],
        "workflowId": "agent-run-1",
    }
    assert wf._lease_consumer_class(profile, "agent-run-1") == "workflow_owned"

    profile.lease_metadata["agent-run-1"] = {
        "ownerIsWorkflow": False,
        "workflowId": "agent-run-1",
    }
    assert wf._lease_consumer_class(profile, "agent-run-1") == "activity_owned"

    profile.lease_metadata["agent-run-1"] = {"ownerIsWorkflow": False}
    assert wf._lease_consumer_class(profile, "agent-run-1") == "unverifiable"


@pytest.mark.asyncio
async def test_an_activity_owned_terminal_owner_requests_cleanup_not_release() -> None:
    """A non-workflow owner with a verifiable workflow still owes cleanup."""

    ledger = _Ledger(
        {
            "request_cleanup": {
                "outcome": LeaseTransitionOutcome.CLEANUP_REQUESTED.value,
                "cleanup_requested": True,
            }
        }
    )
    wf = _held_manager()
    profile = wf._profiles[PROFILE_ID]
    profile.lease_metadata["agent-run-1"] = {
        **profile.lease_metadata["agent-run-1"],
        "ownerIsWorkflow": False,
        "workflowId": "agent-run-1",
    }

    with _patched(ledger):
        await wf._reclaim_terminal_leases_durably(
            {"agent-run-1": {"running": False, "status": "TERMINATED"}},
            include_activity_owned=True,
        )

    assert ledger.actions() == ["request_cleanup"]
    assert profile.current_leases == ["agent-run-1"]
    assert wf._lease_index_conflicts == []


def test_an_unverifiable_lease_is_no_terminal_candidate() -> None:
    """Without an owning workflow there is no liveness to observe.

    The lease is skipped by the terminal path — never released — and stays
    spent for the expiry path, which names it ``cleanup_owner_unverifiable``.
    """

    wf = _expired_cleanup_manager(owner_is_workflow=False)
    candidates = wf._terminal_lease_candidates(
        {"agent-run-1": {"running": False, "status": "NOT_FOUND"}},
        include_activity_owned=True,
    )

    assert candidates == []
    assert wf._profiles[PROFILE_ID].current_leases == ["agent-run-1"]


@pytest.mark.asyncio
async def test_liveness_observations_carry_the_admitted_run_id() -> None:
    """The verify activity binds each observation to the admitted run."""

    seen: list[dict[str, Any]] = []

    async def _verify(name: str, payload: dict[str, Any], **_kwargs: Any):
        seen.append(payload)
        return {"agent-run-1": {"running": False, "status": "TERMINATED"}}

    wf = _held_manager()
    profile = wf._profiles[PROFILE_ID]
    profile.lease_metadata["agent-run-1"] = {
        **profile.lease_metadata["agent-run-1"],
        "workflowId": "agent-run-1",
        "runId": "run-admitted-1",
    }

    with patch(
        "temporalio.workflow.patched",
        side_effect=lambda name: name
        in {
            DB_LEASE_PERSISTENCE_PATCH,
            DURABLE_LEASE_GRANT_PATCH,
            PROVIDER_INCREMENTAL_LEASE_PATCH,
        },
    ), patch(
        "temporalio.workflow.execute_activity", side_effect=_verify
    ), patch(
        "temporalio.workflow.now", return_value=NOW
    ):
        statuses = await wf._verify_workflow_statuses(
            wf._lease_holder_workflow_ids(),
            run_hints=wf._lease_holder_run_hints(),
        )

    assert seen[0]["run_ids"] == {"agent-run-1": "run-admitted-1"}
    assert statuses == {"agent-run-1": {"running": False, "status": "TERMINATED"}}


@pytest.mark.asyncio
async def test_a_failed_terminal_cleanup_request_keeps_the_slot_reserved() -> None:
    ledger = _Ledger({"request_cleanup": RuntimeError("artifacts worker unavailable")})
    wf = _held_manager()

    with _patched(ledger):
        await wf._reclaim_terminal_leases_durably(
            {"agent-run-1": {"running": False, "status": "TERMINATED"}}
        )

    assert wf._profiles[PROFILE_ID].current_leases == ["agent-run-1"]
    assert "agent-run-1" in wf._unresolved_releases


@pytest.mark.asyncio
async def test_the_retained_snapshot_writer_carries_its_own_generation() -> None:
    """A patch marker cannot fence a stale database writer; a generation can."""

    ledger = _Ledger({"save": {"saved": 1}})
    wf = _held_manager(ledger_generation=4)

    with _patched(ledger):
        assert await wf._sync_leases_to_db() is True

    assert ledger.calls[0]["action"] == "save"
    assert ledger.calls[0]["writer_generation"] == 4


class _Signals:
    """Records the external ``slot_assigned`` signals the drain sends."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str, dict[str, Any]]] = []

    def handle_for(self, workflow_id: str, **_kwargs: Any) -> Any:
        recorder = self

        class _Handle:
            async def signal(self, name: str, payload: dict[str, Any]) -> None:
                recorder.sent.append((workflow_id, name, payload))

        return _Handle()


def _re_signal_manager() -> MoonMindProviderProfileManagerWorkflow:
    """A manager holding a lease whose owner asked for its slot again.

    This is the production shape of ``AgentRun._recover_and_request_slot``: a
    slot wait times out and the run re-sends ``request_slot`` to a manager that
    still holds its lease, so the next drain takes the existing-lease branch.
    """

    wf = _held_manager(ledger_generation=5)
    wf._pending_requests = [
        PendingRequest(requester_workflow_id="agent-run-1", runtime_id="opencode")
    ]
    return wf


@pytest.mark.asyncio
async def test_a_re_signal_drain_never_rewrites_the_runtime_wide_snapshot() -> None:
    """Re-signalling a held lease is not a lease change, so it writes nothing.

    The snapshot rewrite deletes and re-inserts every row for the runtime, so
    an unrelated maintenance lease would come back with the model defaults and
    lose its compatibility class, scope generation, capacity scope and any
    requested cleanup.
    """

    ledger = _Ledger({"save": {"saved": 1}})
    wf = _re_signal_manager()
    signals = _Signals()

    with _patched(ledger), patch(
        "temporalio.workflow.get_external_workflow_handle",
        side_effect=signals.handle_for,
    ):
        await wf._drain_queue()

    assert ledger.actions() == [], "the ordinary drain touched the durable ledger"
    assert signals.sent == [
        (
            "agent-run-1",
            "slot_assigned",
            {"profile_id": PROFILE_ID, "fencing_generation": 5},
        )
    ]
    assert wf._pending_requests == []
    assert wf._profiles[PROFILE_ID].current_leases == ["agent-run-1"]


@pytest.mark.asyncio
async def test_a_pre_contract_re_signal_drain_keeps_its_snapshot_save() -> None:
    """Histories recorded before the contract keep the exact recorded command."""

    ledger = _Ledger({"save": {"saved": 1}})
    wf = _re_signal_manager()
    wf._lease_transition_contract = False
    signals = _Signals()

    with _patched(
        ledger,
        enabled={
            DB_LEASE_PERSISTENCE_PATCH,
            DURABLE_LEASE_GRANT_PATCH,
            PROVIDER_INCREMENTAL_LEASE_PATCH,
        },
    ), patch(
        "temporalio.workflow.get_external_workflow_handle",
        side_effect=signals.handle_for,
    ):
        await wf._drain_queue()

    assert ledger.actions() == ["save"]
    assert [name for _, name, _ in signals.sent] == ["slot_assigned"]


@pytest.mark.asyncio
async def test_a_pre_contract_re_signal_drain_still_blocks_on_a_failed_save() -> None:
    """The retained branch keeps its guard: no signal without a durable write."""

    ledger = _Ledger({"save": RuntimeError("artifacts worker unavailable")})
    wf = _re_signal_manager()
    wf._lease_transition_contract = False
    signals = _Signals()

    with _patched(
        ledger,
        enabled={
            DB_LEASE_PERSISTENCE_PATCH,
            DURABLE_LEASE_GRANT_PATCH,
            PROVIDER_INCREMENTAL_LEASE_PATCH,
        },
    ), patch(
        "temporalio.workflow.get_external_workflow_handle",
        side_effect=signals.handle_for,
    ):
        await wf._drain_queue()

    assert ledger.actions() == ["save"]
    assert signals.sent == []
    assert [req.requester_workflow_id for req in wf._pending_requests] == [
        "agent-run-1"
    ]


# ---------------------------------------------------------------------------
# A retained reservation is not authority until its grant commits
# ---------------------------------------------------------------------------


def _new_request_manager() -> MoonMindProviderProfileManagerWorkflow:
    """A manager with free capacity and one queued signal-based requester."""

    wf = _manager()
    wf._pending_requests = [
        PendingRequest(requester_workflow_id="agent-run-1", runtime_id="opencode")
    ]
    return wf


@pytest.mark.asyncio
async def test_a_failed_signal_grant_is_retried_before_its_slot_is_announced() -> None:
    """The drain keeps the reservation to retry persistence — so it must retry.

    MoonLadderStudios/MoonMind#3883: the next pass sees a held lease and takes
    the re-signal branch, which deliberately writes nothing. Without the retry
    it would hand a signal-based AgentRun a slot that has no durable lease row,
    and a manager restart could grant the same capacity again.
    """

    ledger = _Ledger(
        {
            "grant": [RuntimeError("database timeout"), {"granted": True}],
            "describe": {"found": False},
        }
    )
    wf = _new_request_manager()
    signals = _Signals()

    with _patched(ledger), patch(
        "temporalio.workflow.get_external_workflow_handle",
        side_effect=signals.handle_for,
    ):
        await wf._drain_queue()

    assert signals.sent == [], "a slot was announced before its row existed"
    assert wf._profiles[PROFILE_ID].current_leases == ["agent-run-1"]
    assert "agent-run-1" in wf._uncommitted_lease_grants
    assert [req.requester_workflow_id for req in wf._pending_requests] == [
        "agent-run-1"
    ]

    with _patched(ledger), patch(
        "temporalio.workflow.get_external_workflow_handle",
        side_effect=signals.handle_for,
    ):
        await wf._drain_queue()

    assert ledger.actions() == ["grant", "describe", "grant"]
    assert wf._uncommitted_lease_grants == {}
    assert [name for _, name, _ in signals.sent] == ["slot_assigned"]
    assert wf._pending_requests == []


@pytest.mark.asyncio
async def test_a_grant_that_keeps_failing_never_announces_its_slot() -> None:
    ledger = _Ledger(
        {
            "grant": RuntimeError("database timeout"),
            "describe": {"found": False},
        }
    )
    wf = _new_request_manager()
    signals = _Signals()

    for _ in range(3):
        with _patched(ledger), patch(
            "temporalio.workflow.get_external_workflow_handle",
            side_effect=signals.handle_for,
        ):
            await wf._drain_queue()

    assert signals.sent == []
    assert "save" not in ledger.actions()
    assert wf._uncommitted_lease_grants["agent-run-1"]["profile_id"] == PROFILE_ID
    assert [req.requester_workflow_id for req in wf._pending_requests] == [
        "agent-run-1"
    ]


@pytest.mark.asyncio
async def test_a_committed_signal_grant_is_never_re_persisted() -> None:
    """The ordinary re-signal path still costs no durable write."""

    ledger = _Ledger({"grant": {"granted": True}})
    wf = _new_request_manager()
    signals = _Signals()

    with _patched(ledger), patch(
        "temporalio.workflow.get_external_workflow_handle",
        side_effect=signals.handle_for,
    ):
        await wf._drain_queue()
    wf._pending_requests = [
        PendingRequest(requester_workflow_id="agent-run-1", runtime_id="opencode")
    ]
    with _patched(ledger), patch(
        "temporalio.workflow.get_external_workflow_handle",
        side_effect=signals.handle_for,
    ):
        await wf._drain_queue()

    assert ledger.actions() == ["grant"]
    assert [name for _, name, _ in signals.sent] == ["slot_assigned", "slot_assigned"]


# ---------------------------------------------------------------------------
# Continue-As-New carries the obligations attached to the lease snapshot
# ---------------------------------------------------------------------------


def _manager_with_obligations() -> MoonMindProviderProfileManagerWorkflow:
    wf = _held_manager(ledger_generation=6)
    wf._unresolved_releases = {
        "agent-run-9": {
            "profile_id": PROFILE_ID,
            "fencing_generation": 4,
            "outcome": LeaseTransitionOutcome.RETRYABLE.value,
            "retryable": True,
            "kind": "owner_release",
        }
    }
    wf._cleanup_requested_leases = {"agent-run-1"}
    wf._uncommitted_lease_grants = {
        "agent-run-2": {
            "profile_id": PROFILE_ID,
            "purpose": "execution_direct",
            "metadata": {"fencingGeneration": 7},
        }
    }
    return wf


def test_a_rollover_carries_every_obligation_on_the_lease_snapshot() -> None:
    """MoonLadderStudios/MoonMind#3883: the successor inherits the debts too.

    A continued run keeps the in-memory lease snapshot and deliberately does
    not reload the durable ledger, so an obligation left only in memory would
    disappear at the history rollover: the successor would advertise an
    ``already_held`` lease whose row is released, strand capacity behind a
    release nobody retries, or announce a reservation that never committed.
    """

    wf = _manager_with_obligations()
    payload = wf._build_continue_as_new_input()

    successor = _manager()
    successor._restore_lease_obligations(payload)

    assert successor._unresolved_releases == wf._unresolved_releases
    assert successor._cleanup_requested_leases == {"agent-run-1"}
    assert successor._uncommitted_lease_grants == wf._uncommitted_lease_grants


def test_a_pre_contract_rollover_payload_is_unchanged() -> None:
    """Replay safety: an older history keeps the exact command it recorded."""

    wf = _manager_with_obligations()
    wf._lease_transition_contract = False
    payload = wf._build_continue_as_new_input()

    assert "unresolved_releases" not in payload
    assert "cleanup_requested_leases" not in payload
    assert "uncommitted_lease_grants" not in payload


@pytest.mark.asyncio
async def test_a_restored_release_obligation_is_retried_after_rollover() -> None:
    ledger = _Ledger(
        {"release_one": {"released": True, "outcome": LeaseTransitionOutcome.RELEASED.value}}
    )
    wf = _manager_with_obligations()
    successor = _held_manager(ledger_generation=6)
    successor._restore_lease_obligations(wf._build_continue_as_new_input())

    with _patched(ledger):
        await successor._retry_unresolved_releases()

    assert ledger.rows_for("release_one")[0]["lease_id"] == "agent-run-9"
    assert successor._unresolved_releases == {}


# ---------------------------------------------------------------------------
# A cleanup request nobody can resolve becomes actionable evidence
# ---------------------------------------------------------------------------


def _expired_cleanup_manager(
    *, owner_is_workflow: bool = True
) -> MoonMindProviderProfileManagerWorkflow:
    wf = _held_manager()
    profile = wf._profiles[PROFILE_ID]
    profile.max_lease_duration_seconds = 60
    metadata = dict(profile.lease_metadata.get("agent-run-1") or {})
    if not owner_is_workflow:
        metadata["ownerIsWorkflow"] = False
        metadata.pop("workflowId", None)
    profile.lease_metadata["agent-run-1"] = metadata
    profile.lease_granted_at["agent-run-1"] = (NOW - timedelta(hours=3)).isoformat()
    return wf


@pytest.mark.asyncio
async def test_a_cleanup_request_no_owner_resolves_becomes_evidence() -> None:
    """The slot stays spent, but the stuck request stops looking handled.

    MoonLadderStudios/MoonMind#1089: the durable owner of a cleanup request is
    the terminal-ownership release. Once the request has outlived its
    escalation deadline with the holder still live, the manager publishes the
    stuck slot instead of skipping it on every later pass.
    """

    ledger = _Ledger(
        {
            "request_cleanup": {
                "outcome": LeaseTransitionOutcome.CLEANUP_REQUESTED.value
            }
        }
    )
    wf = _expired_cleanup_manager()

    with _patched(ledger):
        await wf._request_cleanup_for_expired_leases()
    assert wf._lease_index_conflicts == []

    later = NOW + timedelta(seconds=_LEASE_CLEANUP_ESCALATION_SECONDS + 60)
    with _patched(ledger, now=later):
        await wf._request_cleanup_for_expired_leases()

    assert ledger.actions() == ["request_cleanup"], "cleanup was re-requested"
    assert wf._profiles[PROFILE_ID].current_leases == ["agent-run-1"]
    assert [entry["kind"] for entry in wf._lease_index_conflicts] == [
        "cleanup_unresolved"
    ]
    assert wf.get_state()["cleanup_requested_leases"] == ["agent-run-1"]


@pytest.mark.asyncio
async def test_a_cleanup_request_with_no_verifiable_owner_is_named_as_such() -> None:
    """An Activity-owned lease with no owning workflow can never be verified."""

    ledger = _Ledger(
        {
            "request_cleanup": {
                "outcome": LeaseTransitionOutcome.CLEANUP_REQUESTED.value
            }
        }
    )
    wf = _expired_cleanup_manager(owner_is_workflow=False)

    with _patched(ledger):
        await wf._request_cleanup_for_expired_leases()
    later = NOW + timedelta(seconds=_LEASE_CLEANUP_ESCALATION_SECONDS + 60)
    with _patched(ledger, now=later):
        await wf._request_cleanup_for_expired_leases()

    assert [entry["kind"] for entry in wf._lease_index_conflicts] == [
        "cleanup_owner_unverifiable"
    ]
    assert wf._profiles[PROFILE_ID].current_leases == ["agent-run-1"]


# ---------------------------------------------------------------------------
# Item 7: the index is a complete conflict and performance boundary
# ---------------------------------------------------------------------------


def test_a_duplicate_lease_identity_is_recorded_not_silently_skipped() -> None:
    wf = _manager()
    wf._index_lease(PROFILE_ID, "agent-run-1", "agent-run-1")
    wf._index_lease("other-profile", "agent-run-1", "agent-run-1")

    assert wf._lease_profile_index["agent-run-1"] == PROFILE_ID
    assert wf._lease_index_conflicts == [
        {
            "kind": "lease_profile",
            "leaseId": "agent-run-1",
            "profileId": "other-profile",
            "existing": PROFILE_ID,
        }
    ]


def test_two_leases_claiming_one_owner_are_reported_not_merged() -> None:
    wf = _manager()
    wf._index_lease(PROFILE_ID, "lease-a", "owner-1")
    wf._index_lease(PROFILE_ID, "lease-b", "owner-1")

    assert wf._owner_lease_index["owner-1"] == "lease-a"
    assert wf._lease_index_conflicts == [
        {
            "kind": "owner_lease",
            "leaseId": "lease-b",
            "ownerId": "owner-1",
            "existing": "lease-a",
        }
    ]


def test_unindexing_uses_the_reverse_index_not_a_scan() -> None:
    wf = _manager()
    for index in range(50):
        wf._index_lease(PROFILE_ID, f"lease-{index}", f"owner-{index}")

    wf._unindex_lease("lease-7")

    assert "lease-7" not in wf._lease_profile_index
    assert "owner-7" not in wf._owner_lease_index
    assert wf._lease_owner_index.get("lease-7") is None
    assert len(wf._owner_lease_index) == 49


def test_rebuilding_the_index_reports_a_duplicate_across_profiles() -> None:
    wf = _manager()
    first = wf._profiles[PROFILE_ID]
    second = _profile(profile_id="opencode-zen-paid")
    wf._profiles[second.profile_id] = second
    first.reserve("agent-run-1", NOW)
    second.reserve("agent-run-1", NOW)

    wf._rebuild_lease_indexes()

    assert [entry["kind"] for entry in wf._lease_index_conflicts] == ["lease_profile"]


def test_manager_state_surfaces_reconciliation_evidence() -> None:
    wf = _manager()
    wf._index_lease(PROFILE_ID, "agent-run-1", "agent-run-1")
    wf._index_lease("other-profile", "agent-run-1", "agent-run-1")
    wf._unresolved_releases["agent-run-9"] = {
        "profile_id": PROFILE_ID,
        "fencing_generation": 2,
        "outcome": "retryable",
    }
    wf._cleanup_requested_leases.add("agent-run-8")

    state = wf.get_state()

    assert state["lease_index_conflicts"][0]["kind"] == "lease_profile"
    assert state["unresolved_releases"]["agent-run-9"]["outcome"] == "retryable"
    assert state["cleanup_requested_leases"] == ["agent-run-8"]


# ---------------------------------------------------------------------------
# Item 6 / acceptance 5: recovery restores identity and blocks on the unknown
# ---------------------------------------------------------------------------


def _restore_ledger(result: dict[str, Any]) -> _Ledger:
    return _Ledger({"load": result})


@pytest.mark.asyncio
async def test_recovery_restores_the_mode_and_generations_it_granted() -> None:
    ledger = _restore_ledger(
        {
            "leases": [
                {
                    "workflow_id": "agent-run-1",
                    "profile_id": PROFILE_ID,
                    "leaseId": "agent-run-1",
                    "ownerId": "agent-run-1",
                    "ownerKind": "workflow",
                    "purpose": "execution_omnigent",
                    "fencingGeneration": 12,
                    "compatibilityClass": (
                        CredentialLeaseMode.SHARED_EXECUTION.value
                    ),
                    "capacityScopeRef": SCOPE_REF,
                    "scopeGeneration": 5,
                    "credentialGeneration": 3,
                    "executionPlanRef": "omnigent-execution-plan:sha256:abc",
                    "leaseState": DurableLeaseState.HELD.value,
                    "safeMetadata": {"evidenceIdentity": "evidence-1"},
                }
            ],
            "max_fencing_generation": 12,
        }
    )
    wf = _manager()
    with _patched(ledger), patch.object(
        MoonMindProviderProfileManagerWorkflow, "_signal_slot_assigned"
    ):
        assert await wf._load_leases_from_db() is True

    metadata = wf._profiles[PROFILE_ID].lease_metadata["agent-run-1"]
    assert metadata["compatibilityClass"] == CredentialLeaseMode.SHARED_EXECUTION.value
    assert metadata["scopeGeneration"] == 5
    assert metadata["capacityScopeRef"] == SCOPE_REF
    assert metadata["credentialGeneration"] == 3
    assert metadata["evidenceIdentity"] == "evidence-1"
    assert metadata["fencingGeneration"] == 12
    assert wf._lease_grant_sequence == 12


@pytest.mark.asyncio
async def test_recovery_keeps_a_cleanup_requested_lease_spending_its_slot() -> None:
    ledger = _restore_ledger(
        {
            "leases": [
                {
                    "workflow_id": "agent-run-1",
                    "profile_id": PROFILE_ID,
                    "leaseId": "agent-run-1",
                    "ownerId": "agent-run-1",
                    "purpose": "execution_direct",
                    "fencingGeneration": 4,
                    "leaseState": DurableLeaseState.CLEANUP_REQUESTED.value,
                }
            ],
            "max_fencing_generation": 4,
        }
    )
    wf = _manager()
    with _patched(ledger), patch.object(
        MoonMindProviderProfileManagerWorkflow, "_signal_slot_assigned"
    ):
        assert await wf._load_leases_from_db() is True

    assert wf._profiles[PROFILE_ID].current_leases == ["agent-run-1"]
    assert "agent-run-1" in wf._cleanup_requested_leases


@pytest.mark.asyncio
async def test_an_unknown_lease_state_blocks_new_admission() -> None:
    ledger = _restore_ledger(
        {
            "leases": [],
            "max_fencing_generation": 3,
            "unreconciled": [
                {"lease_id": "agent-run-9", "lease_state": "quarantined"}
            ],
        }
    )
    wf = _manager()
    with _patched(ledger):
        assert await wf._load_leases_from_db() is False

    assert wf._lease_index_conflicts[0]["kind"] == "unreconciled_state"


@pytest.mark.asyncio
async def test_two_durable_leases_sharing_an_owner_block_admission() -> None:
    ledger = _restore_ledger(
        {
            "leases": [],
            "max_fencing_generation": 3,
            "conflicts": [
                {"field": "owner_id", "value": "agent-run-1", "leases": []}
            ],
        }
    )
    wf = _manager()
    with _patched(ledger):
        assert await wf._load_leases_from_db() is False

    assert wf._lease_index_conflicts[0]["kind"] == "durable_identity"


@pytest.mark.asyncio
async def test_a_pre_contract_history_ignores_the_new_recovery_evidence():
    """Replay safety: an older history keeps admitting exactly as it recorded."""

    ledger = _restore_ledger(
        {
            "leases": [],
            "max_fencing_generation": 3,
            "unreconciled": [{"lease_id": "agent-run-9", "lease_state": "weird"}],
        }
    )
    wf = _manager()
    wf._lease_transition_contract = False
    with _patched(
        ledger,
        enabled={DB_LEASE_PERSISTENCE_PATCH, PROVIDER_INCREMENTAL_LEASE_PATCH},
    ):
        assert await wf._load_leases_from_db() is True


# ---------------------------------------------------------------------------
# MoonLadderStudios/MoonMind#1089 R4: cleanup obligations reach an executing
# owner with a stable claim and bounded retry
# ---------------------------------------------------------------------------
#
# These tests pin the manager side of the delivery handoff at the same
# activity boundary the worker actually invokes (a recording
# ``provider_profile.sync_slot_leases`` substitute, labeled here). The
# durable half runs against a real PostgreSQL cluster in
# ``tests/integration/omnigent/test_provider_lease_incremental_contract_postgres.py``.
# The executing consumer in production is the existing AgentRun, runtime
# realizer, janitor, or operator polling ``get_state``/``manager_state`` for
# ``cleanup_obligations`` and completing each claim through
# ``report_cleanup_verified`` — no new cleanup engine is added.


def _admitted_manager(
    *, ledger_generation: int = 6, run_id: str = "run-admitted-1"
) -> MoonMindProviderProfileManagerWorkflow:
    """One held lease carrying its exact admitted consumer identity."""

    wf = _held_manager(ledger_generation=ledger_generation)
    profile = wf._profiles[PROFILE_ID]
    metadata = dict(profile.lease_metadata.get("agent-run-1") or {})
    metadata.update(
        {
            "workflowId": "agent-run-1",
            "runId": run_id,
            "evidenceIdentity": "evidence-admitted-1",
            "ownerIsWorkflow": True,
        }
    )
    profile.lease_metadata["agent-run-1"] = metadata
    return wf


@pytest.mark.asyncio
async def test_cleanup_redrive_reissues_the_same_stable_claim() -> None:
    """A terminal-owner obligation is re-driven, never re-decided.

    The first pass requests cleanup with the admitted fence and reason; the
    redrive re-issues the same lease ID, fence, and original reason while the
    slot stays spent. The published claim ID is stable — only ``attempt``
    advances — and it carries the exact admitted run and evidence identity
    the executing owner must quote back.
    """

    ledger = _Ledger(
        {
            "request_cleanup": {
                "outcome": LeaseTransitionOutcome.CLEANUP_REQUESTED.value,
                "cleanup_requested": True,
            }
        }
    )
    wf = _admitted_manager()

    with _patched(ledger):
        await wf._reclaim_terminal_leases_durably(
            {"agent-run-1": {"running": False, "status": "NOT_FOUND"}}
        )
    assert ledger.actions() == ["request_cleanup"]
    first = ledger.rows_for("request_cleanup")[0]
    assert first["lease_id"] == "agent-run-1"
    assert first["fencing_generation"] == 6
    assert first["reason"] == "owner_terminal"
    assert wf._profiles[PROFILE_ID].current_leases == ["agent-run-1"]

    claims = wf.get_state()["cleanup_obligations"]
    assert len(claims) == 1
    assert claims[0]["claim_id"] == "agent-run-1:6"
    assert claims[0]["reason"] == "owner_terminal"
    assert claims[0]["runId"] == "run-admitted-1"
    assert claims[0]["evidenceIdentity"] == "evidence-admitted-1"
    assert claims[0]["attempt"] == 0

    with _patched(ledger):
        await wf._deliver_cleanup_requests()

    assert ledger.actions() == ["request_cleanup", "request_cleanup"]
    redrive = ledger.rows_for("request_cleanup")[1]
    assert redrive["lease_id"] == "agent-run-1"
    assert redrive["fencing_generation"] == 6
    assert redrive["reason"] == "owner_terminal"
    # The slot stays spent: a redrive never frees capacity.
    assert wf._profiles[PROFILE_ID].current_leases == ["agent-run-1"]
    claims = wf.get_state()["cleanup_obligations"]
    assert claims[0]["claim_id"] == "agent-run-1:6"
    assert claims[0]["attempt"] == 1


@pytest.mark.asyncio
async def test_cleanup_redrive_recovers_a_lost_request_ack() -> None:
    """A cleanup request that never resolved retries as a cleanup — not a release.

    The first ``request_cleanup`` activity fails after the ledger may already
    have committed, so the outcome is RETRYABLE. The obligation must be kept
    as a cleanup request with its original reason and re-driven through the
    same fenced transition. Re-driving it as an owner release would free a
    consumer that may still be running.
    """

    ledger = _Ledger(
        {
            "request_cleanup": [
                RuntimeError("artifacts worker unavailable"),
                {
                    "outcome": LeaseTransitionOutcome.CLEANUP_REQUESTED.value,
                    "cleanup_requested": True,
                },
            ]
        }
    )
    wf = _admitted_manager()

    with _patched(ledger):
        await wf._reclaim_terminal_leases_durably(
            {"agent-run-1": {"running": False, "status": "TERMINATED"}}
        )

    assert ledger.actions() == ["request_cleanup"]
    assert "agent-run-1" not in wf._cleanup_requested_leases
    pending = wf._unresolved_releases.get("agent-run-1")
    assert pending is not None
    assert pending["kind"] == "cleanup_request"
    assert pending["reason"] == "owner_terminal"
    assert wf._profiles[PROFILE_ID].current_leases == ["agent-run-1"]

    with _patched(ledger):
        await wf._retry_unresolved_releases()

    assert ledger.actions() == ["request_cleanup", "request_cleanup"]
    assert "release_one" not in ledger.actions()
    retry = ledger.rows_for("request_cleanup")[1]
    assert retry["lease_id"] == "agent-run-1"
    assert retry["fencing_generation"] == 6
    assert retry["reason"] == "owner_terminal"
    assert "agent-run-1" in wf._cleanup_requested_leases
    assert wf._cleanup_request_reasons["agent-run-1"] == "owner_terminal"
    assert wf._unresolved_releases == {}
    assert wf._profiles[PROFILE_ID].current_leases == ["agent-run-1"]


@pytest.mark.asyncio
async def test_cleanup_redrive_resolves_across_restart_and_continue_as_new() -> None:
    """The successor re-issues the same stable claim after a rollover."""

    ledger = _Ledger(
        {
            "request_cleanup": {
                "outcome": LeaseTransitionOutcome.CLEANUP_REQUESTED.value,
                "cleanup_requested": True,
            }
        }
    )
    wf = _admitted_manager()

    with _patched(ledger):
        await wf._reclaim_terminal_leases_durably(
            {"agent-run-1": {"running": False, "status": "NOT_FOUND"}}
        )
        await wf._deliver_cleanup_requests()
    assert wf._cleanup_delivery_attempts["agent-run-1"] == 1

    successor = _manager()
    successor._profiles = wf._profiles
    successor._scopes = wf._scopes
    successor._restore_lease_obligations(wf._build_continue_as_new_input())

    assert successor._cleanup_requested_leases == {"agent-run-1"}
    assert successor._cleanup_request_reasons["agent-run-1"] == "owner_terminal"
    assert successor._cleanup_delivery_attempts["agent-run-1"] == 1
    assert successor.get_state()["cleanup_obligations"][0]["claim_id"] == (
        "agent-run-1:6"
    )

    with _patched(ledger):
        await successor._deliver_cleanup_requests()

    redrive = ledger.rows_for("request_cleanup")[-1]
    assert redrive["lease_id"] == "agent-run-1"
    assert redrive["fencing_generation"] == 6
    assert redrive["reason"] == "owner_terminal"
    assert successor.get_state()["cleanup_obligations"][0]["attempt"] == 2
    assert successor._profiles[PROFILE_ID].current_leases == ["agent-run-1"]


@pytest.mark.asyncio
async def test_cleanup_redrive_completes_through_verified_teardown() -> None:
    """A redriven obligation completes only via the verified-teardown handoff."""

    ledger = _Ledger(
        {
            "request_cleanup": {
                "outcome": LeaseTransitionOutcome.CLEANUP_REQUESTED.value,
                "cleanup_requested": True,
            },
            "release_verified": {
                "released": True,
                "outcome": LeaseTransitionOutcome.RELEASED.value,
            },
        }
    )
    wf = _admitted_manager()

    with _patched(ledger):
        await wf._reclaim_terminal_leases_durably(
            {"agent-run-1": {"running": False, "status": "NOT_FOUND"}}
        )
        await wf._deliver_cleanup_requests()
        assert wf.get_state()["cleanup_obligations"][0]["attempt"] == 1
        await wf.report_cleanup_verified(
            {
                "lease_id": "agent-run-1",
                "profile_id": PROFILE_ID,
                "fencing_generation": 6,
                "teardown_evidence": {
                    "consumer_stopped": True,
                    "verified_by": "runtime-janitor",
                    "run_id": "run-admitted-1",
                    "evidence_identity": "evidence-admitted-1",
                },
            }
        )

    assert ledger.actions() == [
        "request_cleanup",
        "request_cleanup",
        "release_verified",
    ]
    assert wf._profiles[PROFILE_ID].current_leases == []
    assert wf.get_state()["cleanup_obligations"] == []
    assert wf.get_state()["cleanup_requested_leases"] == []


@pytest.mark.asyncio
async def test_cleanup_redrive_work_is_bounded_per_pass() -> None:
    """One pass re-drives at most a bounded number of owed slots."""

    ledger = _Ledger(
        {
            "request_cleanup": {
                "outcome": LeaseTransitionOutcome.CLEANUP_REQUESTED.value,
                "cleanup_requested": True,
            }
        }
    )
    wf = _manager()
    profile = wf._profiles[PROFILE_ID]
    profile.max_parallel_runs = _CLEANUP_DELIVERY_PER_PASS + 4
    total = _CLEANUP_DELIVERY_PER_PASS + 2
    for index in range(total):
        lease_id = f"agent-run-{index}"
        metadata = wf._grant_metadata(
            {"workflowId": lease_id},
            fencing_generation=10 + index,
            profile=profile,
            lease_mode=CredentialLeaseMode.SHARED_EXECUTION,
        )
        profile.reserve(lease_id, NOW, metadata=metadata)
        wf._index_lease(PROFILE_ID, lease_id, lease_id)
        wf._cleanup_requested_leases.add(lease_id)
        wf._cleanup_request_reasons[lease_id] = "owner_terminal"

    with _patched(ledger):
        await wf._deliver_cleanup_requests()

    assert len(ledger.rows_for("request_cleanup")) == _CLEANUP_DELIVERY_PER_PASS
    assert len(wf._cleanup_requested_leases) == total
    assert len(profile.current_leases) == total


@pytest.mark.asyncio
async def test_cleanup_redrive_carries_a_non_workflow_consumer_claim() -> None:
    """Validation/enrollment owners get the same stable-claim redrive.

    MoonLadderStudios/MoonMind#1089: exact consumer identity covers
    non-workflow validation/enrollment owners too. An activity-owned lease
    with a verifiable owning workflow is classified as such in its published
    claim and its redrive re-issues the same fenced request while the slot
    stays spent.
    """

    ledger = _Ledger(
        {
            "request_cleanup": {
                "outcome": LeaseTransitionOutcome.CLEANUP_REQUESTED.value,
                "cleanup_requested": True,
            }
        }
    )
    wf = _held_manager(ledger_generation=6)
    profile = wf._profiles[PROFILE_ID]
    profile.lease_metadata["agent-run-1"] = {
        **profile.lease_metadata["agent-run-1"],
        "ownerIsWorkflow": False,
        "workflowId": "agent-run-1",
        "runId": "run-validation-1",
    }

    with _patched(ledger):
        await wf._reclaim_terminal_leases_durably(
            {"agent-run-1": {"running": False, "status": "TERMINATED"}},
            include_activity_owned=True,
        )
        await wf._deliver_cleanup_requests()

    assert ledger.actions() == ["request_cleanup", "request_cleanup"]
    assert "release_one" not in ledger.actions()
    claims = wf.get_state()["cleanup_obligations"]
    assert len(claims) == 1
    assert claims[0]["consumer"] == "activity_owned"
    assert claims[0]["claim_id"] == "agent-run-1:6"
    assert claims[0]["runId"] == "run-validation-1"
    assert claims[0]["attempt"] == 1
    assert profile.current_leases == ["agent-run-1"]


@pytest.mark.asyncio
async def test_terminal_obligation_reaches_executing_owner_and_completes_verified() -> None:
    """The manager-to-janitor handoff frees capacity only from teardown evidence.

    MoonLadderStudios/MoonMind#1089 R4: the existing executing owner polls the
    published ``cleanup_obligations`` claim, stops the exact admitted consumer
    with its own machinery, and completes the stable claim through
    ``report_cleanup_verified`` quoting the acquired fence and admitted
    identity. A late duplicate is idempotent; a stale fence for a replacement
    generation frees nothing.
    """

    ledger = _Ledger(
        {
            "request_cleanup": {
                "outcome": LeaseTransitionOutcome.CLEANUP_REQUESTED.value,
                "cleanup_requested": True,
            },
            "release_verified": {
                "released": True,
                "outcome": LeaseTransitionOutcome.RELEASED.value,
            },
        }
    )
    wf = _admitted_manager()

    with _patched(ledger):
        await wf._reclaim_terminal_leases_durably(
            {"agent-run-1": {"running": False, "status": "NOT_FOUND"}}
        )
        # The existing owner polls exactly this surface; no new engine.
        claims = wf.get_state()["cleanup_obligations"]
        assert [claim["claim_id"] for claim in claims] == ["agent-run-1:6"]
        claim = claims[0]
        # The owner stops its host/session with its own machinery here; the
        # test models the stop as done and completes the same stable claim.
        await wf.report_cleanup_verified(
            {
                "lease_id": claim["lease_id"],
                "profile_id": claim["profile_id"],
                "fencing_generation": claim["fencing_generation"],
                "teardown_evidence": {
                    "consumer_stopped": True,
                    "verified_by": "omnigent-oauth-host-janitor",
                    "run_id": claim["runId"],
                    "evidence_identity": claim["evidenceIdentity"],
                },
            }
        )
        assert wf._profiles[PROFILE_ID].current_leases == []
        assert wf.get_state()["cleanup_obligations"] == []
        # Late duplicate delivery of the same stable claim frees nothing new.
        await wf.report_cleanup_verified(
            {
                "lease_id": claim["lease_id"],
                "profile_id": claim["profile_id"],
                "fencing_generation": claim["fencing_generation"],
                "teardown_evidence": {
                    "consumer_stopped": True,
                    "verified_by": "omnigent-oauth-host-janitor",
                    "run_id": claim["runId"],
                    "evidence_identity": claim["evidenceIdentity"],
                },
            }
        )

    assert ledger.actions() == ["request_cleanup", "release_verified"]
    assert wf._profiles[PROFILE_ID].current_leases == []

    # A stale claim for a replacement generation must not free the new owner.
    replacement_ledger = _Ledger(
        {
            "release_verified": {
                "released": False,
                "outcome": LeaseTransitionOutcome.STALE.value,
            },
        }
    )
    successor = _admitted_manager()
    with _patched(replacement_ledger):
        await successor.report_cleanup_verified(
            {
                "lease_id": "agent-run-1",
                "profile_id": PROFILE_ID,
                "fencing_generation": 5,
                "teardown_evidence": {
                    "consumer_stopped": True,
                    "verified_by": "omnigent-oauth-host-janitor",
                    "run_id": "run-admitted-1",
                    "evidence_identity": "evidence-admitted-1",
                },
            }
        )
    assert successor._profiles[PROFILE_ID].current_leases == ["agent-run-1"]


@pytest.mark.asyncio
async def test_capacity_one_oauth_and_credentialless_n_way_terminal_owners_request_cleanup() -> None:
    """Capacity-one OAuth plus credentialless N-way terminal owners free nothing.

    MoonLadderStudios/MoonMind#1089 R7: one capacity-one OAuth execution lease
    and two credentialless execution leases share one upstream scope. All
    three owners go terminal (TERMINATED / NOT_FOUND / FAILED) while their
    exact runtime consumers remain live. Reclamation must cost one fenced
    ``request_cleanup`` per lease, publish one stable claim per lease quoting
    the admitted run and evidence identity, and keep every profile slot and
    every shared-scope unit spent until verified teardown.

    Boundary labels: the manager side runs here against a recording
    ``provider_profile.sync_slot_leases`` substitute (``_Ledger``); there is
    no real PostgreSQL ledger, no real Temporal server, and no Docker
    container in this test. The durable half of this ordering — the same
    transitions against a real PostgreSQL cluster running the real Alembic
    migration and the real Activity — lives in
    ``tests/integration/omnigent/test_provider_lease_incremental_contract_postgres.py``
    (``test_cleanup_is_requested_without_freeing_the_slot`` plus
    restore/Continue-As-New coverage) and was NOT executed in a host without
    a Docker-capable runtime.
    """

    oauth_profile_id = "codex-oauth-free"
    less_profile_id = "opencode-zen-credentialless"
    shared_scope = "provider-scope:n-way-1089"

    ledger = _Ledger(
        {
            "request_cleanup": {
                "outcome": LeaseTransitionOutcome.CLEANUP_REQUESTED.value,
                "cleanup_requested": True,
            }
        }
    )
    wf = MoonMindProviderProfileManagerWorkflow()
    wf._runtime_id = "opencode"
    wf._durable_maintenance_queue = True
    wf._lease_transition_contract = True
    wf._purpose_aware_capacity_ledger = True
    oauth = _profile(
        profile_id=oauth_profile_id,
        max_parallel_runs=1,
        credential_source="oauth_volume",
        runtime_materialization_mode="oauth_home",
        capacity_scope_ref=shared_scope,
    )
    less = _profile(
        profile_id=less_profile_id,
        max_parallel_runs=4,
        credential_source="none",
        capacity_scope_ref=shared_scope,
    )
    wf._profiles = {oauth.profile_id: oauth, less.profile_id: less}
    wf._scopes = {
        shared_scope: CapacityScopeState(
            scope_ref=shared_scope,
            runtime_id="opencode",
            generation=2,
            configured_limit=3,
            effective_limit=3,
        )
    }
    wf._lease_grant_sequence = 13

    # The profiles under test really are capacity-one OAuth and credentialless.
    assert oauth.max_parallel_runs == 1
    assert oauth.credential_source == "oauth_volume"
    assert less.credentialless is True

    def _admit(
        profile: ProfileSlotState,
        lease_id: str,
        *,
        fence: int,
        run_id: str,
        evidence: str,
    ) -> None:
        metadata = wf._grant_metadata(
            {},
            fencing_generation=fence,
            profile=profile,
            lease_mode=CredentialLeaseMode.SHARED_EXECUTION,
        )
        assert profile.reserve(lease_id, NOW, metadata=metadata) is True
        merged = dict(profile.lease_metadata[lease_id])
        merged.update(
            {
                "workflowId": lease_id,
                "runId": run_id,
                "evidenceIdentity": evidence,
                "ownerIsWorkflow": True,
            }
        )
        profile.lease_metadata[lease_id] = merged
        wf._index_lease(profile.profile_id, lease_id, lease_id)

    _admit(
        oauth, "oauth-run-1", fence=11, run_id="run-oauth-1",
        evidence="evidence-oauth-1",
    )
    _admit(
        less, "less-run-1", fence=12, run_id="run-less-1",
        evidence="evidence-less-1",
    )
    _admit(
        less, "less-run-2", fence=13, run_id="run-less-2",
        evidence="evidence-less-2",
    )

    with _patched(ledger):
        await wf._reclaim_terminal_leases_durably(
            {
                "oauth-run-1": {"running": False, "status": "TERMINATED"},
                "less-run-1": {"running": False, "status": "NOT_FOUND"},
                "less-run-2": {"running": False, "status": "FAILED"},
            }
        )

    assert ledger.actions() == ["request_cleanup"] * 3
    assert "release_one" not in ledger.actions()
    assert "save" not in ledger.actions()
    rows = ledger.rows_for("request_cleanup")
    assert [
        (row["lease_id"], row["fencing_generation"], row["reason"])
        for row in rows
    ] == [
        ("oauth-run-1", 11, "owner_terminal"),
        ("less-run-1", 12, "owner_terminal"),
        ("less-run-2", 13, "owner_terminal"),
    ]

    # No profile slot became reusable: the OAuth unit stays full and both
    # credentialless holders stay recorded.
    assert oauth.current_leases == ["oauth-run-1"]
    assert less.current_leases == ["less-run-1", "less-run-2"]
    assert wf._profile_effective_available(oauth) is False

    # No shared-scope unit became reusable either.
    assert wf._scope_active_units(shared_scope) == 3
    assert wf._scope_is_available(wf._scopes[shared_scope]) is False

    # Each obligation is a stable claim quoting its exact admitted identity.
    claims = wf.get_state()["cleanup_obligations"]
    assert sorted(
        (claim["claim_id"], claim["runId"], claim["evidenceIdentity"])
        for claim in claims
    ) == [
        ("less-run-1:12", "run-less-1", "evidence-less-1"),
        ("less-run-2:13", "run-less-2", "evidence-less-2"),
        ("oauth-run-1:11", "run-oauth-1", "evidence-oauth-1"),
    ]
