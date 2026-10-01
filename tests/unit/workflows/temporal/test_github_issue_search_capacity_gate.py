"""Issue search defers without claiming when the run's provider cannot start work.

A scheduled search that claims an issue its deployment cannot start only
announces, waits, backs off with ``ISSUE_CLAIM_CAPACITY_BLOCKED`` and fails.
Checking the run's provider profile first leaves the backlog untouched and
ends the run idle. Only the GitHub HTTP boundary and the ProviderProfileManager
query are replaced.
"""

from datetime import UTC, datetime, timedelta

import pytest

from moonmind.workflows.temporal import story_output_tools as tools
from tests.unit.workflows.temporal import test_issue_claim_journey as claim_journey

# Reuse the claim journey's real GitHub HTTP and claim-store fixture.
journey = claim_journey.journey

SEARCH = {"repository": "example/repo", "issueSearch": "", "includeAllAuthors": False}
SELECTION = {
    "targetRuntime": "codex_cli",
    "profileId": "codex_openai_oauth",
    "workflow": {
        "runtime": {"mode": "codex_cli", "executionProfileRef": "codex_openai_oauth"}
    },
}


def _in(minutes):
    return (datetime.now(UTC) + timedelta(minutes=minutes)).isoformat()


def _profile(profile_id="codex_openai_oauth", *, leases=0, capacity=1, **extra):
    return {
        "profile_id": profile_id,
        "enabled": True,
        "launch_ready": True,
        "is_default": profile_id == "codex_openai_oauth",
        "current_leases": [f"mm:{profile_id}-{index}:agent:node-1" for index in range(leases)],
        "max_parallel_runs": capacity,
        "effective_capacity": capacity,
        "effective_limit": capacity,
        "execution_lease_count": leases,
        "cooldown_until": None,
        "capacity_scope_ref": f"provider-profile:{profile_id}",
        "pending_capacity_scope_ref": "",
        "exclusive_maintenance_waiters": 0,
        **extra,
    }


def _manager_state(*profiles, pending=0, scope=None):
    profiles = profiles or (_profile(),)
    return {
        "profiles": {profile["profile_id"]: profile for profile in profiles},
        "scopes": [
            {
                "scope_ref": profile["capacity_scope_ref"],
                "effective_limit": profile["effective_limit"],
                "cooldown_until": None,
                "backpressure_state": "healthy",
                **(scope or {}),
            }
            for profile in profiles
        ],
        "pending_requests": [
            {"requester_workflow_id": f"mm:queued-{index}:agent:node-1"}
            for index in range(pending)
        ],
    }


@pytest.fixture
def manager(monkeypatch):
    observed = {"state": None, "runtimes": []}

    async def query(runtime_id):
        observed["runtimes"].append(runtime_id)
        if isinstance(observed["state"], Exception):
            raise observed["state"]
        return observed["state"]

    monkeypatch.setattr(tools, "_provider_profile_manager_state", query)
    return observed


async def _search(service, owner, *, selection=SELECTION):
    context = {"execution_owner": owner}
    if selection is not None:
        context["runtime_selection"] = selection
    return await tools.load_github_issue_preset_brief(
        SEARCH, context, github_service_factory=lambda: service
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state",
    [
        _manager_state(_profile(leases=1), pending=6),
        _manager_state(_profile(leases=1)),
        _manager_state(_profile(cooldown_until=_in(10))),
        _manager_state(_profile(exclusive_maintenance_waiters=1)),
        _manager_state(_profile(pending_capacity_scope_ref="provider-scope:other")),
        _manager_state(_profile(launch_ready=False)),
        # A released 429 lease leaves its shared scope cooling down.
        _manager_state(_profile(), scope={"cooldown_until": _in(10)}),
        _manager_state(_profile(), scope={"backpressure_state": "disabled"}),
        # A sibling profile on the same scope uses the scope's only unit.
        _manager_state(
            _profile(capacity=2),
            _profile("sibling", leases=1, capacity_scope_ref="provider-profile:codex_openai_oauth"),
            scope={"effective_limit": 1},
        ),
    ],
    ids=[
        "saturated_with_queue",
        "slot_in_use",
        "profile_cooling_down",
        "maintenance_waiting",
        "scope_move_pending",
        "not_launch_ready",
        "scope_cooling_down",
        "scope_disabled",
        "scope_full",
    ],
)
async def test_search_defers_without_claiming_when_profile_cannot_start_work(
    journey, manager, state
):
    github, service, _sessions = journey
    manager["state"] = state

    result = await _search(service, "default/capacity-gate")

    assert result.status == "COMPLETED", result.outputs
    assert result.completion_disposition == "idle"
    assert result.outputs["reasonCode"] == "local_capacity_unavailable"
    assert "codex_openai_oauth" in result.outputs["summary"]
    assert "No issue was claimed" in result.outputs["summary"]
    assert manager["runtimes"] == ["codex_cli"]
    # Nothing was announced, labelled or reserved.
    assert github["posts"] == 0
    assert github["labels"] == []
    assert await tools.IssueClaimStore().get("default/capacity-gate") is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state",
    [
        _manager_state(_profile()),
        _manager_state(_profile(leases=1, capacity=2), pending=3),
        _manager_state(_profile(cooldown_until=_in(-1))),
        _manager_state(_profile(), scope={"cooldown_until": _in(-1)}),
        # Unknown capacity is not saturation: admission keeps its own backoff.
        None,
        {"profiles": {}},
        {"profiles": {"codex_openai_oauth": {"current_leases": None}}},
        RuntimeError("manager query timed out"),
    ],
    ids=[
        "free_slot",
        "free_slot_with_queue",
        "cooldown_elapsed",
        "scope_cooldown_elapsed",
        "manager_unavailable",
        "profile_unknown",
        "leases_unknown",
        "query_error",
    ],
)
async def test_search_claims_normally_unless_saturation_is_observed(
    journey, manager, state
):
    github, service, _sessions = journey
    manager["state"] = state

    result = await _search(service, "default/capacity-open")

    assert result.status == "COMPLETED", result.outputs
    assert result.completion_disposition is None
    assert result.outputs["issue"]["number"] == 3970
    assert github["posts"] == 1


@pytest.mark.asyncio
async def test_unpinned_run_follows_the_exclusive_configured_default(journey, manager):
    """The manager grants only the configured default when no profile is named."""
    github, service, _sessions = journey
    manager["state"] = _manager_state(_profile(leases=1), _profile("spare"))

    deferred = await _search(
        service, "default/default-profile", selection={"targetRuntime": "codex_cli"}
    )

    assert deferred.completion_disposition == "idle", deferred.outputs
    assert "default provider profile" in deferred.outputs["summary"]
    assert github["posts"] == 0

    # Without a configured default, any admissible profile can take the run.
    manager["state"] = _manager_state(
        _profile(leases=1, is_default=False), _profile("spare")
    )
    selected = await _search(
        service, "default/no-default", selection={"targetRuntime": "codex_cli"}
    )
    assert selected.completion_disposition is None, selected.outputs
    assert github["posts"] == 1


@pytest.mark.asyncio
async def test_runtime_aliases_query_the_canonical_manager(journey, manager):
    github, service, _sessions = journey
    manager["state"] = _manager_state(_profile(leases=1))

    result = await _search(
        service,
        "default/alias",
        selection={"targetRuntime": "codex", "profileId": "codex_openai_oauth"},
    )

    assert result.completion_disposition == "idle", result.outputs
    assert manager["runtimes"] == ["codex_cli"]
    assert github["posts"] == 0


@pytest.mark.asyncio
async def test_search_without_a_runtime_selection_is_not_gated(journey, manager):
    github, service, _sessions = journey
    manager["state"] = _manager_state(_profile(leases=1), pending=6)

    result = await _search(service, "default/no-selection", selection=None)

    assert result.completion_disposition is None, result.outputs
    assert manager["runtimes"] == []
    assert github["posts"] == 1
