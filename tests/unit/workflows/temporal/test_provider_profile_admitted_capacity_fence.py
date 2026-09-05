"""The admitted-capacity fence survives persistence and a manager restart.

Source issue: MoonLadderStudios/MoonMind#3880 (remaining implementation 1-3,
AC2, AC3).

The execution Activity consumes workflow-admitted provider capacity by
inspecting the manager's ledger. That only works if the manager actually
recorded the fence the Activity checks — the committed plan, the step and
request identity, and the credential generation admitted against — and still
has it after a grant-before-schedule crash, a worker loss, or any other restart
that reloads leases from the database.

If the fence were in-memory only, every restart would turn a live, correctly
admitted run into an unusable ticket. If it were absent altogether, inspection
would have nothing to establish and the Activity would be back to acquiring
capacity inside its own execution slot.
"""

from __future__ import annotations

import contextlib
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import patch

import pytest

from moonmind.omnigent.provider_leases import OmnigentProviderLeaseCoordinator
from moonmind.workflows.temporal.workflows.provider_profile_manager import (
    PROVIDER_INCREMENTAL_LEASE_PATCH,
    MoonMindProviderProfileManagerWorkflow,
    ProfileSlotState,
)

NOW = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)
OWNER = "agent-run-1"
PLAN_REF = "omnigent-execution-plan:sha256:" + "1" * 64

#: Exactly what the AgentRun workflow sends with ``request_slot``.
ADMISSION_METADATA = {
    "workflowId": OWNER,
    "ownerIsWorkflow": True,
    "stepExecutionId": "step-1",
    "idempotencyKey": "idem-1",
    "executionPlanRef": PLAN_REF,
    "credentialGeneration": 7,
}


def _profile() -> ProfileSlotState:
    return ProfileSlotState(
        profile_id="opencode-zen-free",
        max_parallel_runs=8,
        cooldown_after_429_seconds=300,
        rate_limit_policy="backoff",
        enabled=True,
        launch_ready=True,
        credential_source="none",
        purpose_aware_capacity=True,
        capacity_scope_ref="opencode-zen:contributor-free",
    )


def _workflow(profile: ProfileSlotState) -> MoonMindProviderProfileManagerWorkflow:
    wf = MoonMindProviderProfileManagerWorkflow()
    wf._runtime_id = "opencode"
    wf._profiles = {profile.profile_id: profile}
    return wf


@contextlib.contextmanager
def _patched(activity):
    with patch(
        "temporalio.workflow.patched",
        side_effect=lambda name: name == PROVIDER_INCREMENTAL_LEASE_PATCH,
    ), patch("temporalio.workflow.execute_activity", side_effect=activity):
        yield


def test_request_slot_keeps_the_fence_and_still_rejects_unknown_metadata() -> None:
    """The allowlist admits compact identity, never arbitrary caller state."""

    safe = MoonMindProviderProfileManagerWorkflow._safe_lease_metadata(
        {"metadata": {**ADMISSION_METADATA, "apiKey": "secret", "extra": "no"}}
    )

    assert safe == ADMISSION_METADATA
    assert "apiKey" not in safe


def test_an_inspected_grant_carries_the_whole_admitted_identity() -> None:
    """AC2: inspection must be able to establish the fence, not just liveness."""

    profile = _profile()
    profile.reserve(
        OWNER, NOW, purpose="execution_omnigent", metadata=dict(ADMISSION_METADATA)
    )
    wf = _workflow(profile)
    wf._rebuild_lease_indexes()

    inspection = wf.inspect_credential_lease({"lease_id": OWNER})

    assert inspection["active"] is True
    assert inspection["profile_id"] == "opencode-zen-free"
    assert inspection["ownerId"] == OWNER
    assert inspection["ownerIsWorkflow"] is True
    assert inspection["executionPlanRef"] == PLAN_REF
    assert inspection["stepExecutionId"] == "step-1"
    assert inspection["idempotencyKey"] == "idem-1"
    assert inspection["credentialGeneration"] == 7
    assert inspection["expiresAt"]


@pytest.mark.asyncio
async def test_the_persisted_row_records_the_fence() -> None:
    """A fence that never reaches the database cannot survive a restart."""

    profile = _profile()
    profile.reserve(
        OWNER, NOW, purpose="execution_omnigent", metadata=dict(ADMISSION_METADATA)
    )
    wf = _workflow(profile)
    calls: list[dict[str, Any]] = []

    async def activity(_name, payload, **_kwargs):
        calls.append(payload)
        return {"granted": True}

    with _patched(activity):
        assert await wf._persist_lease_grant(
            profile,
            OWNER,
            purpose="execution_omnigent",
            metadata=dict(ADMISSION_METADATA),
        )

    row = calls[0]["leases"][0]
    assert calls[0]["action"] == "grant"
    assert row["executionPlanRef"] == PLAN_REF
    assert row["stepExecutionId"] == "step-1"
    assert row["idempotencyKey"] == "idem-1"
    assert row["credential_generation"] == 7
    assert row["expiresAt"]


@pytest.mark.asyncio
async def test_a_restored_lease_is_still_consumable_by_the_execution_activity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC3: a grant-before-schedule crash keeps one usable authority.

    The manager reloads the lease from the database and the run's ticket must
    still inspect as the same admitted identity — otherwise a restart would
    fail an otherwise valid, already-granted run.
    """

    # A live lease: the restart happened while the grant was still valid.
    expires_at = (
        datetime.now(timezone.utc) + timedelta(hours=1)
    ).isoformat()
    persisted = {
        "workflow_id": OWNER,
        "profile_id": "opencode-zen-free",
        "granted_at": NOW.isoformat(),
        "leaseId": OWNER,
        "ownerId": OWNER,
        "purpose": "execution_omnigent",
        "ownerIsWorkflow": True,
        "stepExecutionId": "step-1",
        "oauthSessionId": None,
        "idempotencyKey": "idem-1",
        "executionPlanRef": PLAN_REF,
        "credentialGeneration": 7,
        "expiresAt": expires_at,
    }
    profile = _profile()
    wf = _workflow(profile)
    signalled: list[tuple[str, str]] = []

    async def activity(_name, payload, **_kwargs):
        assert payload["action"] == "load"
        return {"leases": [persisted]}

    async def _signal(wf_id, profile_id):
        signalled.append((wf_id, profile_id))

    monkeypatch.setattr(wf, "_signal_slot_assigned", _signal)
    with _patched(activity):
        assert await wf._load_leases_from_db()

    assert signalled == [(OWNER, "opencode-zen-free")]
    inspection = wf.inspect_credential_lease({"lease_id": OWNER})

    # The restored evidence is complete enough for the consuming coordinator.
    OmnigentProviderLeaseCoordinator._assert_admitted_lease_matches(
        inspection,
        profile_ref="opencode-zen-free",
        fence=_fence(),
    )


def _fence():
    from moonmind.omnigent.provider_leases import _AdmittedLeaseFence

    return _AdmittedLeaseFence(
        owner_id=OWNER,
        plan_ref=PLAN_REF,
        step_execution_id="step-1",
        idempotency_key="idem-1",
        credential_generation=7,
    )
