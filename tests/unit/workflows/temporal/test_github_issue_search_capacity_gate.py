"""Issue search defers without claiming when the run's provider has no free slot.

A scheduled search that claims an issue its deployment cannot start only
announces, waits, backs off with ``ISSUE_CLAIM_CAPACITY_BLOCKED`` and fails.
Checking the run's provider profile first leaves the backlog untouched and
ends the run idle. Only the GitHub HTTP boundary and the ProviderProfileManager
query are replaced.
"""

# ruff: noqa: F811 -- imported pytest fixture

from datetime import UTC, datetime, timedelta

import pytest

from moonmind.workflows.temporal import story_output_tools as tools
from tests.unit.workflows.temporal.test_issue_claim_journey import journey  # noqa: F401

SEARCH = {"repository": "example/repo", "issueSearch": "", "includeAllAuthors": False}
SELECTION = {
    "targetRuntime": "codex_cli",
    "profileId": "codex_openai_oauth",
    "workflow": {
        "runtime": {"mode": "codex_cli", "executionProfileRef": "codex_openai_oauth"}
    },
}


def _manager_state(*, leases, capacity=1, pending=0, cooldown_until=None):
    return {
        "profiles": {
            "codex_openai_oauth": {
                "profile_id": "codex_openai_oauth",
                "enabled": True,
                "current_leases": [f"mm:busy-{index}:agent:node-1" for index in range(leases)],
                "effective_capacity": capacity,
                "max_parallel_runs": capacity,
                "cooldown_until": cooldown_until,
            }
        },
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
        _manager_state(leases=1, pending=6),
        _manager_state(leases=1),
        _manager_state(
            leases=0,
            cooldown_until=(datetime.now(UTC) + timedelta(minutes=10)).isoformat(),
        ),
    ],
    ids=["saturated_with_queue", "slot_in_use", "cooling_down"],
)
async def test_search_defers_without_claiming_when_profile_has_no_free_slot(
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
        _manager_state(leases=0),
        _manager_state(leases=1, capacity=2, pending=3),
        _manager_state(
            leases=0,
            cooldown_until=(datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
        ),
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
async def test_default_profile_selection_checks_the_runtimes_enabled_profiles(
    journey, manager
):
    github, service, _sessions = journey
    state = _manager_state(leases=1)
    state["profiles"]["spare"] = {
        "profile_id": "spare",
        "enabled": False,
        "current_leases": [],
        "effective_capacity": 1,
    }
    manager["state"] = state

    deferred = await _search(
        service, "default/default-profile", selection={"targetRuntime": "codex_cli"}
    )

    assert deferred.completion_disposition == "idle", deferred.outputs
    assert github["posts"] == 0

    state["profiles"]["spare"]["enabled"] = True
    selected = await _search(
        service, "default/default-profile-free", selection={"targetRuntime": "codex_cli"}
    )
    assert selected.completion_disposition is None, selected.outputs
    assert github["posts"] == 1


@pytest.mark.asyncio
async def test_search_without_a_runtime_selection_is_not_gated(journey, manager):
    github, service, _sessions = journey
    manager["state"] = _manager_state(leases=1, pending=6)

    result = await _search(service, "default/no-selection", selection=None)

    assert result.completion_disposition is None, result.outputs
    assert manager["runtimes"] == []
    assert github["posts"] == 1
