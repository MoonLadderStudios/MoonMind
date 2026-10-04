"""Capacity deferral retries the same search without claiming unavailable work."""

import copy
import json
from datetime import UTC, datetime, timedelta

import pytest
from temporalio.exceptions import ApplicationError

from moonmind.workflows.temporal import story_output_tools as tools
from moonmind.workflows.temporal.workflows import run as run_module
from tests.unit.workflows.temporal import test_github_issue_search_capacity_gate as gate
from tests.unit.workflows.temporal.workflows import test_run_integration as integration

journey = gate.journey
manager = gate.manager
mock_run_workflow = integration.mock_run_workflow

TOOL_NAME = "github.load_issue_preset_brief"
NODE_ID = "search"
OWNER = "default/wf-1"


def _node(node_id=NODE_ID, *, tool_name=TOOL_NAME):
    return {
        "id": node_id,
        "tool": {"type": "skill", "name": tool_name},
        "inputs": dict(gate.SEARCH),
    }


def _registry():
    return json.dumps(
        {
            "skills": [
                {
                    "name": name,
                    "description": "Native issue search fixture",
                    "inputs": {"schema": {"type": "object"}},
                    "outputs": {"schema": {"type": "object"}},
                    "executor": {
                        "activity_type": "mm.skill.execute",
                        "selector": {"mode": "by_capability"},
                    },
                    "requirements": {"capabilities": ["sandbox"]},
                    "policies": {
                        "timeouts": {
                            "start_to_close_seconds": 60,
                            "schedule_to_close_seconds": 120,
                        },
                        "retries": {"max_attempts": 1},
                    },
                }
                for name in (TOOL_NAME, "repo.noop")
            ]
        }
    ).encode()


async def _run_native_search_stage(
    monkeypatch,
    workflow,
    service,
    *,
    wait,
    retry_enabled=True,
    failure_mode="FAIL_FAST",
    independent_after_search=False,
):
    """Exercise the production plan loop and tool, replacing only its boundaries."""
    nodes = [_node(), _node("after-search", tool_name="repo.noop")]
    if independent_after_search:
        nodes.append(_node("z-independent-after-search", tool_name="repo.noop"))
    plan = json.loads(integration._mock_plan_payload(nodes))
    plan["policy"]["failure_mode"] = failure_mode
    plan["edges"] = [{"from": NODE_ID, "to": "after-search"}]
    calls = []
    workflow._integration = None
    workflow._runtime_inheritance_parameters = copy.deepcopy(gate.SELECTION)
    workflow._issue_search_capacity_retry_enabled = retry_enabled

    def patched(patch_id):
        return patch_id in {
            run_module.RUN_TOOL_RUNTIME_SELECTION_CONTEXT_PATCH,
            run_module.RUN_PAUSE_SAFE_BOUNDARIES_PATCH,
            "tool-idle-objective-outcome-v1",
        }

    async def typed_activity(activity_type, payload, **_kwargs):
        assert activity_type == "artifact.read"
        return (
            _registry()
            if payload.artifact_ref == "art:sha256:456"
            else json.dumps(plan).encode()
        )

    async def activity(activity_type, payload, **_kwargs):
        if activity_type == "artifact.create":
            return {"artifact_id": "art:sha256:fixture-report"}
        assert activity_type == "mm.skill.execute"
        calls.append(copy.deepcopy(payload))
        invocation = payload["invocation_payload"]
        if invocation["tool"]["name"] == "repo.noop":
            return {"status": "COMPLETED", "outputs": {"summary": "Continued work"}}
        context = {**payload["context"], "execution_owner": OWNER}
        return await tools.load_github_issue_preset_brief(
            invocation["inputs"], context, github_service_factory=lambda: service
        )

    monkeypatch.setattr(run_module.workflow, "patched", patched)
    monkeypatch.setattr(run_module.workflow, "wait_condition", wait)
    monkeypatch.setattr(run_module, "execute_typed_activity", typed_activity)
    monkeypatch.setattr(run_module.workflow, "execute_activity", activity)
    await workflow._run_execution_stage(
        parameters={"publishMode": "none"}, plan_ref="plan"
    )
    return calls


@pytest.mark.asyncio
async def test_native_search_waits_for_released_capacity_then_claims_once_and_continues(
    journey, manager, mock_run_workflow, monkeypatch
):
    github, service, _sessions = journey
    manager["state"] = gate._manager_state(gate._profile(leases=1))
    waits = []

    async def release_capacity(predicate, *, timeout=None, **_kwargs):
        # No backlog effect is allowed until the owning provider can start work.
        assert not predicate()
        assert github["posts"] == 0
        assert await tools.IssueClaimStore().get(OWNER) is None
        assert mock_run_workflow._state == "awaiting_slot"
        assert mock_run_workflow._step_ledger_rows[0]["status"] == "awaiting_external"
        waits.append(timeout)
        manager["state"] = gate._manager_state(gate._profile())
        raise TimeoutError

    calls = await _run_native_search_stage(
        monkeypatch, mock_run_workflow, service, wait=release_capacity
    )

    assert len(waits) == 1, "The occupied search must remain open and retry in this run"
    assert waits[0] == timedelta(seconds=30)
    assert github["posts"] == 1
    receipt = await tools.IssueClaimStore().get(OWNER)
    assert receipt is not None and receipt.issue_number == 3970 and receipt.confirmed
    assert len(calls) == 3
    assert calls[1]["context"] == calls[0]["context"]
    assert calls[1]["invocation_payload"] == calls[0]["invocation_payload"]
    assert calls[1]["idempotency_key"].endswith("_capacity_recheck_1")
    assert mock_run_workflow._publish_context.get("objectiveOutcome") != "idle"
    assert [row["status"] for row in mock_run_workflow._step_ledger_rows] == [
        "completed",
        "completed",
    ]


def _deferred_result():
    return {
        "status": "COMPLETED",
        "completion_disposition": "idle",
        "outputs": {
            "reasonCode": "local_capacity_unavailable",
            "summary": "Search deferred: provider profile is occupied. No issue was claimed.",
            "capacityEvidence": {
                "runtimeId": "codex_cli",
                "profileId": "codex_openai_oauth",
            },
        },
    }


def _execute_payload():
    return {
        "invocation_payload": {
            "id": NODE_ID,
            "tool": {"type": "skill", "name": TOOL_NAME},
            "inputs": dict(gate.SEARCH),
        },
        "context": {
            "execution_owner": OWNER,
            "runtime_selection": copy.deepcopy(gate.SELECTION),
        },
        "idempotency_key": "stable-step-execute",
    }


def _prepare_wait(workflow):
    workflow._issue_search_capacity_retry_enabled = True
    workflow._initialize_step_ledger(
        ordered_nodes=[_node()],
        dependency_map={NODE_ID: []},
        updated_at=run_module.workflow.now(),
    )
    workflow._mark_step_running(
        NODE_ID, updated_at=run_module.workflow.now(), summary="Search"
    )
    workflow._set_state("executing", summary="Search")


async def _wait_for_capacity(
    workflow, *, result=None, payload=None, tool_name=TOOL_NAME
):
    return await workflow._wait_for_issue_search_capacity(
        execution_result=_deferred_result() if result is None else result,
        node_id=NODE_ID,
        tool_name=tool_name,
        route=run_module.DEFAULT_ACTIVITY_CATALOG.resolve_activity("mm.skill.execute"),
        execute_payload=_execute_payload() if payload is None else payload,
        max_attempts_override=1,
    )


@pytest.mark.asyncio
async def test_capacity_exhaustion_is_failed_with_bounded_backoff_and_preserved_evidence(
    mock_run_workflow, monkeypatch
):
    workflow = mock_run_workflow
    clock = [datetime(2026, 10, 4, tzinfo=UTC)]
    monkeypatch.setattr(run_module.workflow, "now", lambda: clock[0])
    _prepare_wait(workflow)
    waits = []
    calls = []
    initial = _deferred_result()

    async def remain_occupied(predicate, *, timeout=None, **_kwargs):
        if timeout is None:
            assert predicate()
            return
        assert not predicate()
        assert workflow._state == "awaiting_slot"
        assert workflow._waiting_reason
        assert workflow._step_ledger_rows[0]["status"] == "awaiting_external"
        waits.append(timeout)
        clock[0] += timeout
        raise TimeoutError

    async def activity(activity_type, payload, **_kwargs):
        if activity_type == "artifact.create":
            return {"artifact_id": "art:sha256:fixture-report"}
        assert activity_type == "mm.skill.execute"
        calls.append(copy.deepcopy(payload))
        return copy.deepcopy(initial)

    monkeypatch.setattr(run_module.workflow, "wait_condition", remain_occupied)
    monkeypatch.setattr(run_module.workflow, "execute_activity", activity)
    result = await _wait_for_capacity(workflow, result=initial)

    assert result["status"] == "FAILED"
    assert result["outputs"]["error"] == "RESOURCE_EXHAUSTED"
    assert "capacity" in result["outputs"]["reasonCode"]
    assert (
        result["outputs"]["capacityEvidence"] == initial["outputs"]["capacityEvidence"]
    )
    assert result.get("completion_disposition") != "idle"
    assert workflow._publish_context["objectiveOutcome"] == "failed"
    assert workflow._plan_blocked_message
    assert workflow._waiting_reason is None
    assert (
        workflow._determine_publish_completion(parameters={"publishMode": "none"})[0]
        == "failed"
    )
    assert waits[:4] == [timedelta(seconds=value) for value in (30, 60, 120, 240)]
    assert max(waits) <= timedelta(minutes=5)
    assert sum(waits, timedelta()) == timedelta(minutes=30)
    assert len(calls) <= 10
    assert [payload["idempotency_key"] for payload in calls] == [
        f"stable-step-execute_capacity_recheck_{index}"
        for index in range(1, len(calls) + 1)
    ]
    assert all(payload["context"] == _execute_payload()["context"] for payload in calls)


@pytest.mark.asyncio
async def test_capacity_wait_cancellation_does_not_dispatch_or_become_objective_failure(
    mock_run_workflow, monkeypatch
):
    workflow = mock_run_workflow
    _prepare_wait(workflow)

    async def cancel(predicate, *, timeout=None, **_kwargs):
        if timeout is None:
            assert predicate()
            return
        workflow._cancel_requested = True
        assert predicate()

    async def unexpected_activity(*_args, **_kwargs):
        pytest.fail("Cancellation must not dispatch another search")

    monkeypatch.setattr(run_module.workflow, "wait_condition", cancel)
    monkeypatch.setattr(run_module.workflow, "execute_activity", unexpected_activity)
    await _wait_for_capacity(workflow)
    assert workflow._cancel_requested
    assert workflow._publish_context.get("objectiveOutcome") != "failed"
    assert not workflow._plan_blocked_message


@pytest.mark.asyncio
async def test_capacity_wait_pauses_before_rechecking_and_keeps_the_same_execution(
    mock_run_workflow, monkeypatch
):
    workflow = mock_run_workflow
    _prepare_wait(workflow)
    events = []

    async def pause(predicate, **_kwargs):
        workflow._paused = True
        assert predicate()
        events.append("paused")

    async def safe_boundary():
        if not workflow._paused:
            return
        events.append("resumed")
        workflow._paused = False

    async def activity(*_args, **_kwargs):
        assert not workflow._paused
        events.append("rechecked")
        return {"status": "COMPLETED", "outputs": {"issue": {"number": 3970}}}

    monkeypatch.setattr(run_module.workflow, "wait_condition", pause)
    monkeypatch.setattr(workflow, "_wait_if_paused_at_safe_boundary", safe_boundary)
    monkeypatch.setattr(run_module.workflow, "execute_activity", activity)
    result = await _wait_for_capacity(workflow)
    assert result["outputs"]["issue"]["number"] == 3970
    assert events == ["paused", "resumed", "rechecked"]
    assert workflow._step_execution_for(NODE_ID) == 1
    assert workflow._state == "executing"
    assert workflow._waiting_reason is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    [
        "eligible-empty",
        "explicit-issue",
        "other-tool",
        "selected-issue",
        "claim",
        "failed-result",
        "not-idle",
        "retained-history",
    ],
)
async def test_capacity_retry_only_applies_to_unselected_native_search_deferral(
    mock_run_workflow, monkeypatch, case
):
    workflow = mock_run_workflow
    _prepare_wait(workflow)
    result = _deferred_result()
    payload = _execute_payload()
    tool_name = TOOL_NAME
    if case == "eligible-empty":
        result["outputs"]["reasonCode"] = "no_eligible_issues"
    elif case == "explicit-issue":
        payload["invocation_payload"]["inputs"] = {
            "repository": "example/repo",
            "issueNumber": 3970,
        }
    elif case == "other-tool":
        tool_name = "repo.noop"
    elif case == "selected-issue":
        result["outputs"]["issue"] = {"number": 3970}
    elif case == "claim":
        result["outputs"]["attemptId"] = "existing-claim"
    elif case == "failed-result":
        result["status"] = "FAILED"
    elif case == "not-idle":
        result.pop("completion_disposition")
    elif case == "retained-history":
        workflow._issue_search_capacity_retry_enabled = False

    async def unexpected(*_args, **_kwargs):
        pytest.fail("This outcome must not add a timer or another tool invocation")

    monkeypatch.setattr(run_module.workflow, "wait_condition", unexpected)
    monkeypatch.setattr(run_module.workflow, "execute_activity", unexpected)
    actual = await _wait_for_capacity(
        workflow, result=result, payload=payload, tool_name=tool_name
    )
    assert actual == result
    assert workflow._state == "executing"
    assert not workflow._plan_blocked_message


@pytest.mark.asyncio
async def test_retained_workflow_keeps_its_original_idle_completion_without_new_commands(
    journey, manager, mock_run_workflow, monkeypatch
):
    github, service, _sessions = journey
    manager["state"] = gate._manager_state(gate._profile(leases=1))

    async def immediate(predicate, **_kwargs):
        assert predicate()

    calls = await _run_native_search_stage(
        monkeypatch, mock_run_workflow, service, wait=immediate, retry_enabled=False
    )
    assert len(calls) == 1
    assert github["posts"] == 0
    assert mock_run_workflow._publish_context["objectiveOutcome"] == "idle"
    assert [row["status"] for row in mock_run_workflow._step_ledger_rows] == [
        "completed",
        "skipped",
    ]


@pytest.mark.asyncio
async def test_native_empty_search_is_legitimate_idle_without_capacity_retry(
    journey, manager, mock_run_workflow, monkeypatch
):
    github, service, _sessions = journey
    manager["state"] = gate._manager_state(gate._profile())
    github["state"] = "closed"

    async def unexpected_wait(*_args, **_kwargs):
        pytest.fail("An empty eligible backlog must not retry capacity")

    calls = await _run_native_search_stage(
        monkeypatch, mock_run_workflow, service, wait=unexpected_wait
    )
    assert len(calls) == 1
    assert github["posts"] == 0
    assert mock_run_workflow._publish_context["objectiveOutcome"] == "idle"
    assert [row["status"] for row in mock_run_workflow._step_ledger_rows] == [
        "completed",
        "skipped",
    ]


@pytest.mark.asyncio
async def test_native_capacity_exhaustion_blocks_objective_success_even_with_continue_policy(
    journey, manager, mock_run_workflow, monkeypatch
):
    github, service, _sessions = journey
    manager["state"] = gate._manager_state(gate._profile(leases=1))
    clock = [datetime(2026, 10, 4, tzinfo=UTC)]
    monkeypatch.setattr(run_module.workflow, "now", lambda: clock[0])
    waits = []

    async def still_occupied(predicate, *, timeout=None, **_kwargs):
        assert not predicate()
        assert github["posts"] == 0
        waits.append(timeout)
        clock[0] += timeout
        raise TimeoutError

    calls = await _run_native_search_stage(
        monkeypatch,
        mock_run_workflow,
        service,
        wait=still_occupied,
        failure_mode="CONTINUE",
        independent_after_search=True,
    )
    assert waits, "Capacity must be retried before the run is failed"
    assert sum(waits, timedelta()) == timedelta(minutes=30)
    assert github["posts"] == 0
    assert await tools.IssueClaimStore().get(OWNER) is None
    assert [row["status"] for row in mock_run_workflow._step_ledger_rows] == [
        "failed",
        "skipped",
        "skipped",
    ]
    assert all(
        call["invocation_payload"]["tool"]["name"] == TOOL_NAME for call in calls
    )
    assert mock_run_workflow._publish_context["objectiveOutcome"] == "failed"
    outcome, summary, failed = mock_run_workflow._determine_publish_completion(
        parameters={"publishMode": "none"}
    )
    assert outcome == "failed" and failed
    assert "capacity" in summary.lower()


@pytest.mark.asyncio
async def test_native_capacity_exhaustion_is_typed_execution_failure_with_fail_fast_policy(
    journey, manager, mock_run_workflow, monkeypatch
):
    github, service, _sessions = journey
    manager["state"] = gate._manager_state(gate._profile(leases=1))
    clock = [datetime(2026, 10, 4, tzinfo=UTC)]
    monkeypatch.setattr(run_module.workflow, "now", lambda: clock[0])

    async def still_occupied(predicate, *, timeout=None, **_kwargs):
        assert not predicate()
        clock[0] += timeout
        raise TimeoutError

    with pytest.raises(ApplicationError) as caught:
        await _run_native_search_stage(
            monkeypatch, mock_run_workflow, service, wait=still_occupied
        )
    assert caught.value.type == "RESOURCE_EXHAUSTED"
    assert (
        mock_run_workflow._classify_failure_category(caught.value) == "execution_error"
    )
    assert github["posts"] == 0
    assert await tools.IssueClaimStore().get(OWNER) is None


@pytest.mark.asyncio
async def test_same_owner_retries_recover_claim_even_when_capacity_becomes_occupied(
    journey, manager
):
    github, service, _sessions = journey
    manager["state"] = gate._manager_state(gate._profile())
    context = {"execution_owner": OWNER, "runtime_selection": gate.SELECTION}
    selected = await tools.load_github_issue_preset_brief(
        gate.SEARCH, context, github_service_factory=lambda: service
    )
    assert selected.outputs["issue"]["number"] == 3970
    manager["state"] = gate._manager_state(gate._profile(leases=1))
    recovered = await tools.load_github_issue_preset_brief(
        gate.SEARCH, context, github_service_factory=lambda: service
    )
    assert recovered.status == "COMPLETED"
    assert recovered.completion_disposition is None
    assert recovered.outputs["issue"]["number"] == 3970
    assert recovered.outputs["attemptId"] == selected.outputs["attemptId"]
    assert github["posts"] == 1
    # The resumed claim follows the stored receipt and does not query capacity again.
    assert manager["runtimes"] == ["codex_cli"]


@pytest.mark.asyncio
async def test_competing_scheduled_search_cannot_duplicate_an_active_claim(
    journey, manager
):
    github, service, _sessions = journey
    manager["state"] = gate._manager_state(gate._profile())
    first = await gate._search(service, OWNER)
    assert first.outputs["issue"]["number"] == 3970
    competing = await gate._search(service, "default/later-scheduled-occurrence")
    assert competing.completion_disposition == "idle"
    assert competing.outputs["reasonCode"] != "local_capacity_unavailable"
    assert (
        await tools.IssueClaimStore().get("default/later-scheduled-occurrence") is None
    )
    assert github["posts"] == 1


@pytest.mark.parametrize("enabled", [False, True])
def test_idle_summary_does_not_claim_success_in_new_histories(
    mock_run_workflow, enabled
):
    workflow = mock_run_workflow
    workflow._issue_search_capacity_retry_enabled = enabled
    workflow._publish_context["objectiveOutcome"] = "idle"
    summary = workflow._compose_success_completion_message(
        publish_detail="No eligible work"
    )
    assert "No eligible work" in summary
    assert ("Workflow completed successfully" in summary) is not enabled


@pytest.mark.parametrize("enabled,expected", [(False, "active"), (True, "succeeded")])
def test_verified_no_commit_projects_objective_success_with_replay_safety(
    mock_run_workflow, monkeypatch, enabled, expected
):
    workflow = mock_run_workflow
    workflow._issue_search_capacity_retry_enabled = enabled
    workflow._state = "no_commit"
    memos = []
    monkeypatch.setattr(
        run_module.workflow,
        "patched",
        lambda patch_id: patch_id == "run-objective-outcome-projection-v1",
    )
    monkeypatch.setattr(run_module.workflow, "upsert_memo", memos.append)
    workflow._update_memo()
    assert memos[-1]["objectiveOutcome"] == expected
