"""Non-preempting idle usage-observation admission (MoonLadderStudios/MoonMind#5363).

A Settings refresh may read provider account usage only while nothing else
uses the credential home. These run the real manager handlers against a
deterministic stand-in for the workflow module.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import patch as mock_patch

import pytest
from temporalio import exceptions

from moonmind.provider_profiles.lease_client import (
    CredentialLeaseMode,
    CredentialLeasePurpose,
)
from moonmind.workflows.temporal.workflows.provider_profile_manager import (
    USAGE_OBSERVATION_PREEMPT_SIGNAL,
    MoonMindProviderProfileManagerWorkflow,
    PendingRequest,
    ProfileSlotState,
    _purpose_spends_scope,
)

NOW = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)
MANAGER_MODULE = (
    "moonmind.workflows.temporal.workflows.provider_profile_manager.workflow"
)
USAGE = CredentialLeasePurpose.USAGE_OBSERVATION.value


class _Handle:
    def __init__(self, fake: "_FakeWorkflow", workflow_id: str) -> None:
        self._fake = fake
        self._workflow_id = workflow_id

    async def signal(self, name: str, payload: Any = None) -> None:
        self._fake.signals.append((self._workflow_id, name, payload))


class _FakeWorkflow:
    def __init__(self) -> None:
        self._now = NOW
        self.logger = logging.getLogger(__name__)
        self.wait_calls = 0
        self.activity_calls: list[tuple[str, Any]] = []
        self.activity_results: list[Any] = []
        self.signals: list[tuple[str, str, Any]] = []

    def now(self) -> datetime:
        return self._now

    def patched(self, _patch_id: str) -> bool:
        return False

    async def wait_condition(self, predicate, timeout=None):
        self.wait_calls += 1
        raise AssertionError("usage observation admission must never wait")

    async def execute_activity(self, name, payload, **_kwargs):
        self.activity_calls.append((name, payload))
        if self.activity_results:
            result = self.activity_results.pop(0)
            if isinstance(result, Exception):
                raise result
            return result
        return {}

    def get_external_workflow_handle(self, workflow_id: str) -> _Handle:
        return _Handle(self, workflow_id)

    def advance(self, delta: timedelta) -> None:
        self._now += delta


def _manager() -> MoonMindProviderProfileManagerWorkflow:
    wf = MoonMindProviderProfileManagerWorkflow()
    wf._runtime_id = "codex_cli"
    wf._purpose_aware_capacity_ledger = True
    wf._durable_maintenance_queue = True
    wf._oauth_login_scope_exempt = True
    wf._startup_restored = True
    return wf


def _profile(wf, profile_id: str, **overrides: Any) -> ProfileSlotState:
    values: dict[str, Any] = dict(
        profile_id=profile_id,
        max_parallel_runs=1,
        cooldown_after_429_seconds=300,
        rate_limit_policy="backoff",
        enabled=True,
        launch_ready=True,
        credential_source="oauth_volume",
        provider_id="openai",
        purpose_aware_capacity=True,
        capacity_scope_ref=f"provider-profile:{profile_id}",
        effective_limit=1,
    )
    values.update(overrides)
    profile = ProfileSlotState(**values)
    wf._profiles[profile_id] = profile
    return profile


def _usage_payload(
    owner: str, profile_id: str = "codex-a", aliases: tuple[str, ...] = ()
) -> dict[str, Any]:
    return {
        "requester_workflow_id": owner,
        "owner_id": owner,
        "runtime_id": "codex_cli",
        "execution_profile_ref": profile_id,
        "purpose": USAGE,
        "metadata": {
            "workflowId": owner,
            "ownerIsWorkflow": True,
            "runId": f"run-{owner}",
            "credentialResourceKey": "credential-home:abc",
            "credentialAliasProfileIds": list(aliases or (profile_id,)),
        },
    }


async def _try(wf, fake, payload):
    with mock_patch(MANAGER_MODULE, fake):
        return await wf.try_acquire_usage_observation_lease(payload)


def test_usage_observation_creates_no_inference_turn() -> None:
    purpose = CredentialLeasePurpose.USAGE_OBSERVATION
    assert purpose.is_maintenance
    assert purpose.consumes_provider_capacity is False
    assert _purpose_spends_scope(USAGE, oauth_login_exempt=True) is False


@pytest.mark.asyncio
async def test_idle_profile_is_granted_an_exclusive_bounded_lease() -> None:
    wf, fake = _manager(), _FakeWorkflow()
    profile = _profile(wf, "codex-a")

    result = await _try(wf, fake, _usage_payload("usage-1"))

    assert result["granted"] is True
    assert result["lease_mode"] == CredentialLeaseMode.EXCLUSIVE_MAINTENANCE.value
    assert result["lease_fencing_generation"] >= 1
    metadata = profile.lease_metadata["usage-1"]
    assert metadata["purpose"] == USAGE
    expires = datetime.fromisoformat(metadata["expiresAt"])
    assert expires - NOW == timedelta(seconds=300)
    # The grant is durable before it is reported, and holds the home.
    assert [name for name, _ in fake.activity_calls] == [
        "provider_profile.sync_slot_leases"
    ]
    assert profile.is_available() is False
    assert profile.exclusive_maintenance_queue == []
    assert fake.wait_calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("setup", "reason"),
    [
        (
            lambda wf, p: p.reserve("agent-run-1", NOW, purpose="execution_omnigent"),
            "credential_consumer_active",
        ),
        (
            lambda wf, p: p.enqueue_maintenance_waiter(
                "oauth-session:1",
                purpose="oauth_reconnect",
                queue_order=1,
                queued_at=NOW.isoformat(),
            ),
            "maintenance_pending",
        ),
        (
            lambda wf, p: wf._pending_requests.append(
                PendingRequest(
                    requester_workflow_id="agent-run-2",
                    runtime_id="codex_cli",
                    execution_profile_ref="codex-a",
                )
            ),
            "execution_pending",
        ),
        (
            lambda wf, p: wf._pending_requests.append(
                PendingRequest(
                    requester_workflow_id="agent-run-3",
                    runtime_id="codex_cli",
                    profile_selector={"providerId": "openai"},
                )
            ),
            "execution_pending",
        ),
        (
            lambda wf, p: wf._execution_waiters.update(
                {"activity-owner": ("codex-a", None)}
            ),
            "execution_pending",
        ),
        (
            lambda wf, p: wf._unresolved_releases.update(
                {"old-lease": {"profile_id": "codex-a", "kind": "owner_release"}}
            ),
            "cleanup_unresolved",
        ),
    ],
)
async def test_busy_home_refuses_immediately_without_a_waiter(setup, reason) -> None:
    wf, fake = _manager(), _FakeWorkflow()
    profile = _profile(wf, "codex-a")
    setup(wf, profile)
    queue_before = list(profile.exclusive_maintenance_queue)

    result = await _try(wf, fake, _usage_payload("usage-1"))

    assert result == {"granted": False, "profile_id": "codex-a", "reason": reason}
    assert "usage-1" not in profile.current_leases
    assert profile.exclusive_maintenance_queue == queue_before
    assert fake.wait_calls == 0
    assert fake.activity_calls == []


@pytest.mark.asyncio
async def test_usage_observation_cannot_join_the_maintenance_queue() -> None:
    wf, fake = _manager(), _FakeWorkflow()
    profile = _profile(wf, "codex-a")
    profile.reserve("agent-run-1", NOW, purpose="execution_omnigent")

    with mock_patch(MANAGER_MODULE, fake), pytest.raises(
        exceptions.ApplicationError
    ) as refused:
        await wf.acquire_credential_maintenance_lease(_usage_payload("usage-1"))

    assert refused.value.non_retryable is True
    assert profile.exclusive_maintenance_queue == []
    assert fake.wait_calls == 0


@pytest.mark.asyncio
async def test_pending_work_on_another_provider_does_not_block() -> None:
    wf, fake = _manager(), _FakeWorkflow()
    _profile(wf, "codex-a")
    wf._pending_requests.append(
        PendingRequest(
            requester_workflow_id="agent-run-9",
            runtime_id="codex_cli",
            profile_selector={"providerId": "minimax"},
        )
    )

    assert (await _try(wf, fake, _usage_payload("usage-1")))["granted"] is True


@pytest.mark.asyncio
async def test_duplicate_requests_coalesce_and_competitors_are_refused() -> None:
    wf, fake = _manager(), _FakeWorkflow()
    profile = _profile(wf, "codex-a")

    first = await _try(wf, fake, _usage_payload("usage-1"))
    retry = await _try(wf, fake, _usage_payload("usage-1"))
    competitor = await _try(wf, fake, _usage_payload("usage-2"))

    assert retry["granted"] is True and retry["already_held"] is True
    assert retry["lease_fencing_generation"] == first["lease_fencing_generation"]
    assert competitor["granted"] is False
    assert profile.current_leases == ["usage-1"]


@pytest.mark.asyncio
async def test_owner_reuse_for_different_authority_fails_closed() -> None:
    wf, fake = _manager(), _FakeWorkflow()
    profile = _profile(wf, "codex-a")
    profile.reserve("shared-owner", NOW, purpose="execution_omnigent")
    wf._index_lease("codex-a", "shared-owner", "shared-owner")

    with pytest.raises(exceptions.ApplicationError) as conflict:
        await _try(wf, fake, _usage_payload("shared-owner"))
    assert conflict.value.non_retryable is True


@pytest.mark.asyncio
async def test_aliases_of_one_home_cannot_obtain_a_second_writer() -> None:
    wf, fake = _manager(), _FakeWorkflow()
    _profile(wf, "codex-a")
    alias = _profile(wf, "codex-b")
    aliases = ("codex-a", "codex-b")

    granted = await _try(wf, fake, _usage_payload("usage-a", "codex-a", aliases))
    second = await _try(wf, fake, _usage_payload("usage-b", "codex-b", aliases))

    assert granted["granted"] is True
    assert second["granted"] is False
    # Execution and maintenance through the alias wait for the observation.
    assert alias.is_available() is True
    assert wf._find_available_profile(execution_profile_ref="codex-b") is None
    assert wf._find_available_profile() is None
    assert (
        wf._maintenance_grant_blocker(
            profile_id="codex-b", requester_id="repair-b", scope_gated=False
        )
        == "draining_credential_consumers"
    )
    # An execution already on the alias makes the home busy for observation.
    wf2, fake2 = _manager(), _FakeWorkflow()
    _profile(wf2, "codex-a")
    _profile(wf2, "codex-b").reserve("agent-run-1", NOW, purpose="execution_omnigent")
    refused = await _try(wf2, fake2, _usage_payload("usage-a", "codex-a", aliases))
    assert refused["reason"] == "credential_consumer_active"


@pytest.mark.asyncio
async def test_arriving_execution_preempts_once_and_is_admitted_after_release() -> None:
    wf, fake = _manager(), _FakeWorkflow()
    profile = _profile(wf, "codex-a")
    grant = await _try(wf, fake, _usage_payload("usage-1"))
    wf._index_lease("codex-a", "usage-1", "usage-1")

    with mock_patch(MANAGER_MODULE, fake):
        await wf._preempt_usage_observations()
        assert fake.signals == []  # nothing is waiting yet
        wf._pending_requests.append(
            PendingRequest(
                requester_workflow_id="agent-run-1",
                runtime_id="codex_cli",
                execution_profile_ref="codex-a",
            )
        )
        await wf._preempt_usage_observations()
        await wf._preempt_usage_observations()
        # The observation keeps the home until its owner proves cleanup.
        assert wf._find_available_profile(execution_profile_ref="codex-a") is None
        await wf.release_slot(
            {
                "requester_workflow_id": "usage-1",
                "profile_id": "codex-a",
                "fencing_generation": grant["lease_fencing_generation"],
            }
        )

    assert fake.signals == [
        (
            "usage-1",
            USAGE_OBSERVATION_PREEMPT_SIGNAL,
            {
                "lease_id": "usage-1",
                "profile_id": "codex-a",
                "fencing_generation": grant["lease_fencing_generation"],
                "reason": "execution_pending",
            },
        )
    ]
    assert wf._find_available_profile(execution_profile_ref="codex-a") is profile


@pytest.mark.asyncio
async def test_maintenance_and_slot_waiters_also_preempt() -> None:
    wf, fake = _manager(), _FakeWorkflow()
    profile = _profile(wf, "codex-a")
    await _try(wf, fake, _usage_payload("usage-1"))
    profile.enqueue_maintenance_waiter(
        "oauth-session:1",
        purpose="oauth_reconnect",
        queue_order=1,
        queued_at=NOW.isoformat(),
    )
    with mock_patch(MANAGER_MODULE, fake):
        await wf._preempt_usage_observations()
    assert fake.signals[0][2]["reason"] == "maintenance_pending"

    wf, fake = _manager(), _FakeWorkflow()
    _profile(wf, "codex-a")
    await _try(wf, fake, _usage_payload("usage-1"))
    wf._execution_waiters["activity-owner"] = (None, {"providerId": "openai"})
    with mock_patch(MANAGER_MODULE, fake):
        await wf._preempt_usage_observations()
    assert fake.signals[0][2]["reason"] == "execution_pending"


@pytest.mark.asyncio
async def test_slot_waiter_wakes_the_loop_while_an_observation_is_held() -> None:
    wf, fake = _manager(), _FakeWorkflow()
    _profile(wf, "codex-a")
    await _try(wf, fake, _usage_payload("usage-1"))
    seen: list[Any] = []

    async def wait_condition(predicate, timeout=None):
        seen.append((dict(wf._execution_waiters), wf._has_new_events))
        raise asyncio.CancelledError

    fake.wait_condition = wait_condition
    wf._has_new_events = False
    with mock_patch(MANAGER_MODULE, fake), pytest.raises(asyncio.CancelledError):
        await wf._acquire_slot(
            {
                "requester_workflow_id": "activity-owner",
                "runtime_id": "codex_cli",
                "execution_profile_ref": "codex-a",
                "purpose": "execution_omnigent",
            },
            verify_activity_owner=False,
        )

    assert seen == [({"activity-owner": ("codex-a", None)}, True)]
    assert wf._execution_waiters == {}


@pytest.mark.asyncio
async def test_failed_grant_persistence_holds_nothing() -> None:
    wf, fake = _manager(), _FakeWorkflow()
    profile = _profile(wf, "codex-a")
    fake.activity_results = [RuntimeError("ledger down")]

    result = await _try(wf, fake, _usage_payload("usage-1"))

    assert result["granted"] is False and result["reason"] == "ledger_unavailable"
    assert profile.current_leases == []
    assert wf._profile_id_for_lease("usage-1") is None


@pytest.mark.asyncio
async def test_alias_set_is_persisted_with_the_grant_and_survives_rollover() -> None:
    wf, fake = _manager(), _FakeWorkflow()
    _profile(wf, "codex-a")
    _profile(wf, "codex-b")
    fake.activity_results = [{"outcome": "granted"}]
    with mock_patch(MANAGER_MODULE, fake), mock_patch.object(
        MoonMindProviderProfileManagerWorkflow,
        "_workflow_patch_enabled",
        staticmethod(lambda _patch: True),
    ):
        wf._lease_transition_contract = False
        lease = wf._profiles["codex-a"]
        lease.reserve(
            "usage-1",
            NOW,
            purpose=USAGE,
            metadata={
                "credentialAliasProfileIds": ["codex-a", "codex-b"],
                "credentialResourceKey": "credential-home:abc",
                "fencingGeneration": 1,
            },
            allow_unready=True,
        )
        await wf._grant_lease_to_db(profile=lease, lease_id="usage-1", purpose=USAGE)
    row = fake.activity_calls[0][1]["leases"][0]
    assert row["safe_metadata"]["credentialAliasProfileIds"] == ["codex-a", "codex-b"]

    successor = _manager()
    successor._restore_state(wf._build_continue_as_new_input())
    assert successor._usage_observation_guards("codex-b") is True


@pytest.mark.asyncio
async def test_orphaned_observation_is_reclaimed_only_after_owner_closed_and_lifetime() -> (
    None
):
    wf, fake = _manager(), _FakeWorkflow()
    profile = _profile(wf, "codex-a")
    await _try(wf, fake, _usage_payload("usage-1"))
    fence = profile.lease_fencing_generation("usage-1")
    claim = {
        "lease_id": "usage-1",
        "profile_id": "codex-a",
        "purpose": USAGE,
        "workflowId": "usage-1",
        "runId": "run-usage-1",
        "fencing_generation": fence,
    }
    statuses: dict[str, Any] = {"usage-1": {"running": True, "status": "RUNNING"}}
    released: list[str] = []

    async def verify(_ids, run_hints=None):
        assert run_hints == {"usage-1": "run-usage-1"}
        return statuses

    async def release(lease_id, **_kwargs):
        released.append(lease_id)
        return "released"

    wf._pending_cleanup_claims = lambda: [claim]
    wf._verify_workflow_statuses = verify
    wf._release_verified_cleanup = release
    with mock_patch(MANAGER_MODULE, fake):
        fake.advance(timedelta(seconds=600))
        await wf._complete_orphaned_usage_observation_cleanup()
        assert released == []  # a live owner must prove its own cleanup

        statuses["usage-1"] = {"running": False, "status": "TERMINATED"}
        fake._now = NOW + timedelta(seconds=120)
        await wf._complete_orphaned_usage_observation_cleanup()
        assert released == []  # the probe may still be inside its lifetime

        fake._now = NOW + timedelta(seconds=301)
        await wf._complete_orphaned_usage_observation_cleanup()

    assert released == ["usage-1"]
    assert "usage-1" not in profile.current_leases
