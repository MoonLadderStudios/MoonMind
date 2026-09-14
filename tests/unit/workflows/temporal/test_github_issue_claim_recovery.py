"""Failed recurring selection -> automatic owner -> confirmed release -> selection."""

# ruff: noqa: F811 -- imported pytest fixture

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from temporalio.client import WorkflowExecutionStatus
from temporalio.testing import ActivityEnvironment

from moonmind.workflows.temporal import github_issue_claim_recovery as recovery
from moonmind.workflows.temporal import story_output_tools as tools
from moonmind.workflows.temporal.activity_runtime import TemporalIntegrationActivities
from moonmind.workflows.temporal.github_issue_attempts import parse_attempt_comment
from moonmind.workflows.temporal.issue_claim_store import IssueClaimStore
from tests.unit.workflows.temporal.test_issue_claim_journey import journey  # noqa: F401


async def history_events(events):
    for event in events:
        yield event


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault", ["none", "missing_label", "reject_release", "lose_release_ack"]
)
async def test_scheduled_recovery_releases_failed_claim_and_next_search_selects_it(
    journey, monkeypatch, tmp_path, fault
):
    state, service, sessions = journey
    owner = "default/failed-recurring-run"
    brief = await tools.load_github_issue_preset_brief(
        {"repository": "example/repo", "issueSearch": ""},
        {"execution_owner": owner},
        github_service_factory=lambda: service,
    )
    assert brief.status == "COMPLETED"
    store = IssueClaimStore(sessions)
    if fault == "missing_label":
        state["labels"] = []
    elif fault != "none":
        state[fault] = True
    handle = SimpleNamespace(
        describe=AsyncMock(
            return_value=SimpleNamespace(
                status=WorkflowExecutionStatus.FAILED,
                close_time=datetime.now(UTC) - timedelta(minutes=6),
                workflow_type="MoonMind.Run",
                run_id="failed-run-id",
            )
        ),
        fetch_history_events=lambda **kwargs: history_events([]),
    )
    client = SimpleNamespace(get_workflow_handle=lambda *args, **kwargs: handle)
    monkeypatch.setattr(recovery, "IssueClaimStore", lambda: store)
    monkeypatch.setattr(
        "moonmind.workflows.temporal.client.get_temporal_client",
        AsyncMock(return_value=client),
    )
    monkeypatch.setattr(
        "moonmind.workflows.adapters.github_service.GitHubService", lambda: service
    )
    boundary = TemporalIntegrationActivities().github_issue_reconcile_handoffs
    first = await ActivityEnvironment().run(boundary, {"stateDir": str(tmp_path)})
    if fault == "reject_release":
        assert not (await store.get(owner)).released
        state[fault] = False
    await ActivityEnvironment().run(boundary, {"stateDir": str(tmp_path)})
    receipt = await store.get(owner)
    assert receipt.released, first
    handoff = parse_attempt_comment(state["comments"][0]["body"]).handoff
    assert handoff.activity == "released" and handoff.writers_stopped
    assert handoff.next_action == "fresh_retry" and handoff.outcome == "no_work"
    assert state["labels"] == [] and state["posts"] == 1
    next_run = await tools.load_github_issue_preset_brief(
        {"repository": "example/repo", "issueSearch": ""},
        {"execution_owner": "default/next-recurring-run"},
        github_service_factory=lambda: service,
    )
    assert next_run.status == "COMPLETED", next_run.outputs
    assert next_run.completion_disposition != "idle", next_run.outputs
    assert (await store.get("default/next-recurring-run")).issue_number == 3970


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,minutes,reason",
    [
        (WorkflowExecutionStatus.RUNNING, 10000, "owner_or_child_running"),
        (WorkflowExecutionStatus.CONTINUED_AS_NEW, 10000, "owner_or_child_running"),
        (WorkflowExecutionStatus.FAILED, 4, "cleanup_grace"),
        (WorkflowExecutionStatus.CANCELED, 10000, "cancellation_hold"),
        (
            WorkflowExecutionStatus.COMPLETED,
            10000,
            "successful_owner_requires_finalization",
        ),
    ],
)
async def test_age_never_substitutes_for_terminal_authority(status, minutes, reason):
    now = datetime.now(UTC)
    handle = SimpleNamespace(
        describe=AsyncMock(
            return_value=SimpleNamespace(
                status=status,
                close_time=now - timedelta(minutes=minutes),
            )
        )
    )
    client = SimpleNamespace(get_workflow_handle=lambda *args, **kwargs: handle)
    with pytest.raises(ValueError, match=reason):
        await recovery._closed_execution_tree(
            client, SimpleNamespace(owner="default/old-run"), now
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault", ["unacknowledged_child", "unsettled_mutation", "history_limit"]
)
async def test_incomplete_controlling_history_cannot_authorize_release(
    fault, monkeypatch
):
    from temporalio.api.common.v1 import ActivityType
    from temporalio.api.history.v1 import (
        ActivityTaskScheduledEventAttributes,
        HistoryEvent,
        StartChildWorkflowExecutionInitiatedEventAttributes,
    )

    events = [HistoryEvent(event_id=1)]
    if fault == "unacknowledged_child":
        events[0].start_child_workflow_execution_initiated_event_attributes.CopyFrom(
            StartChildWorkflowExecutionInitiatedEventAttributes(
                namespace="default", workflow_id="child"
            )
        )
        reason = "child_start_unsettled"
    elif fault == "unsettled_mutation":
        events[0].activity_task_scheduled_event_attributes.CopyFrom(
            ActivityTaskScheduledEventAttributes(
                activity_type=ActivityType(name="mm.tool.execute")
            )
        )
        reason = "shared_mutation_outcome_unknown"
    else:
        monkeypatch.setattr(recovery, "MAX_HISTORY_EVENTS", 0)
        reason = "history_scan_incomplete"
    handle = SimpleNamespace(
        describe=AsyncMock(
            return_value=SimpleNamespace(
                status=WorkflowExecutionStatus.FAILED,
                close_time=datetime.now(UTC) - timedelta(minutes=6),
                workflow_type="MoonMind.Run",
            )
        ),
        fetch_history_events=lambda **kwargs: history_events(events),
    )
    client = SimpleNamespace(get_workflow_handle=lambda *args, **kwargs: handle)
    with pytest.raises(ValueError, match=reason):
        await recovery._closed_execution_tree(
            client, SimpleNamespace(owner="default/failed"), datetime.now(UTC)
        )


@pytest.mark.asyncio
async def test_slow_owner_cannot_starve_rotation_and_next_sweep(journey, monkeypatch):
    _, service, sessions = journey
    store = IssueClaimStore(sessions)
    for number, name in enumerate(("a", "b", "c"), 1):
        await store.prepare(
            owner="default/" + name,
            repository="example/repo",
            issue_number=number,
            attempt_id=name,
            actor_id="123",
            comment_body="",
        )

    async def describe(name):
        if name == "a":
            await asyncio.sleep(1)
        return SimpleNamespace(
            status=WorkflowExecutionStatus.FAILED,
            close_time=datetime.now(UTC) - timedelta(minutes=6),
            workflow_type="MoonMind.Run",
        )

    client = SimpleNamespace(
        get_workflow_handle=lambda name, **kwargs: SimpleNamespace(
            describe=lambda: describe(name),
            fetch_history_events=lambda **kwargs: history_events([]),
        )
    )
    monkeypatch.setattr(recovery, "MAX_CLAIM_SECONDS", 0.05)
    monkeypatch.setattr(recovery, "MAX_SWEEP_SECONDS", 0.01)
    state = {}
    args = {
        "state": state,
        "store": store,
        "service": service,
        "client_factory": AsyncMock(return_value=client),
    }
    first = await recovery.reconcile_local_claims(**args)
    assert (
        first["examined"] == 1 and first["results"][0]["reasonCode"] == "TimeoutError"
    )
    assert state["localClaimCursor"]["*"] == "default/a"
    monkeypatch.setattr(recovery, "MAX_SWEEP_SECONDS", 60)
    monkeypatch.setattr(recovery, "MAX_CLAIM_SECONDS", 5)
    second = await recovery.reconcile_local_claims(**args)
    assert second["released"] == 2
    assert not (await store.get("default/a")).released
    assert state["localClaimCursor"]["*"] == ""


@pytest.mark.asyncio
async def test_fresh_workflow_ids_cannot_reset_the_portable_retry_allowance(journey):
    state, service, sessions = journey
    store = IssueClaimStore(sessions)
    handle = SimpleNamespace(
        describe=AsyncMock(
            return_value=SimpleNamespace(
                status=WorkflowExecutionStatus.FAILED,
                close_time=datetime.now(UTC) - timedelta(minutes=6),
                workflow_type="MoonMind.Run",
            )
        ),
        fetch_history_events=lambda **kwargs: history_events([]),
    )
    client = SimpleNamespace(get_workflow_handle=lambda *args, **kwargs: handle)
    for index in range(3):
        owner = f"default/retry-{index}"
        result = await tools.load_github_issue_preset_brief(
            {"repository": "example/repo", "issueSearch": ""},
            {"execution_owner": owner},
            github_service_factory=lambda: service,
        )
        assert result.status == "COMPLETED" and result.completion_disposition != "idle"
        recovered = await recovery.reconcile_local_claims(
            state={},
            store=store,
            service=service,
            client_factory=AsyncMock(return_value=client),
        )
        assert recovered["released"] == 1, recovered
        handoff = parse_attempt_comment((await store.get(owner)).comment_body).handoff
        assert handoff.retry_remaining == 2 - index
    assert handoff.next_action == "obtain_attention"
    assert "moonmind:in-progress" not in state["labels"]
    next_run = await tools.load_github_issue_preset_brief(
        {"repository": "example/repo", "issueSearch": ""},
        {"execution_owner": "default/must-not-reset"},
        github_service_factory=lambda: service,
    )
    assert next_run.completion_disposition == "idle"
    assert await store.get("default/must-not-reset") is None


async def persist_saved_binding(sessions, *, agent_id, run_id, saved, state="cleaned"):
    from api_service.db.models import (
        OmnigentExecutionPlanRecord,
        OmnigentRuntimeBindingRecord,
    )
    from moonmind.omnigent.harness_platform.stores import DbExecutionPlanStore
    from moonmind.omnigent.runtime_bindings import (
        DbRuntimeBindingStore,
        RuntimeBindingState,
    )
    from tests.unit.omnigent.test_generic_platform_production_services import (
        _exact_plan,
    )

    async with sessions.kw["bind"].begin() as connection:
        for table in (
            OmnigentExecutionPlanRecord.__table__,
            OmnigentRuntimeBindingRecord.__table__,
        ):
            await connection.run_sync(
                lambda conn, table=table: table.create(conn, checkfirst=True)
            )
    plan = _exact_plan("opencode-go/model")
    await DbExecutionPlanStore(sessions).persist(plan)
    store = DbRuntimeBindingStore(sessions)
    binding = await store.create_initial(
        execution_plan_ref=plan.planRef,
        idempotency_key=agent_id,
        provider_leases={},
        initial_phase_results={
            "owner": {"namespace": "default", "workflowId": agent_id, "runId": run_id},
            "workspace": {"workspaceSpec": {}},
            "saved": saved,
        },
    )
    binding = await store.update(
        binding.bindingId,
        expected_revision=binding.revision,
        expected_fencing_generation=binding.fencingGeneration,
        state=RuntimeBindingState.cleanup_pending,
        increment_fence=True,
    )
    if state == "cleanup_pending":
        return binding
    return await store.update(
        binding.bindingId,
        expected_revision=binding.revision,
        expected_fencing_generation=binding.fencingGeneration,
        state=RuntimeBindingState(state),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault",
    [
        "none",
        "active_host",
        "changed_digest",
        "foreign_repository",
        "missing_proof",
        "commit",
        "remote_deleted",
        "advanced_base",
        "untracked",
    ],
)
async def test_serialized_native_checkpoint_and_cleanup_control_release(
    journey, monkeypatch, tmp_path, fault
):
    import json

    from tests.unit.omnigent.test_claim_recovery_workspace import (
        capture_saved_workspace,
        comparison_from_git,
    )

    state, service, sessions = journey
    change = (
        fault
        if fault in {"commit", "remote_deleted", "advanced_base", "untracked"}
        else "none"
    )
    saved, _, workspace, _ = await capture_saved_workspace(
        tmp_path, monkeypatch, change
    )
    state["comparison_provider"] = comparison_from_git(workspace)
    saved = json.loads(json.dumps(saved))
    if fault == "changed_digest":
        saved["archiveDigest"] = "sha256:" + "b" * 64
    elif fault == "foreign_repository":
        saved["recoveryEvidence"]["repository"] = "other/repo"
    elif fault == "missing_proof":
        saved.pop("recoveryEvidence")
    await persist_saved_binding(
        sessions,
        agent_id="agent",
        run_id="run",
        saved=saved,
        state="cleanup_pending" if fault == "active_host" else "cleaned",
    )
    call = recovery._runtime_no_work(
        IssueClaimStore(sessions),
        {("agent", "run")},
        SimpleNamespace(owner="default/parent", repository="example/repo"),
        service,
    )
    if fault in {"none", "advanced_base"}:
        assert await call == [saved["checkpointRef"]]
    elif fault == "remote_deleted":
        import httpx

        with pytest.raises(httpx.HTTPStatusError):
            await call
    else:
        with pytest.raises(
            ValueError, match="runtime_cleanup_pending|saved_work_requires_recovery"
        ):
            await call
