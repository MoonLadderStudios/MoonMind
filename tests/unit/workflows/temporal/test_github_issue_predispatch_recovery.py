"""Recover active claims whose retained AgentRun history never dispatched work."""

# ruff: noqa: F811 -- imported pytest fixture

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from temporalio.api.common.v1 import ActivityType, WorkflowExecution
from temporalio.api.history.v1 import (
    ActivityTaskCompletedEventAttributes,
    ActivityTaskScheduledEventAttributes,
    ChildWorkflowExecutionStartedEventAttributes,
    HistoryEvent,
    StartChildWorkflowExecutionInitiatedEventAttributes,
    WorkflowExecutionStartedEventAttributes,
)
from temporalio.client import WorkflowExecutionStatus

from api_service.db.models import OmnigentRuntimeBindingRecord, ProviderProfileSlotLease
from moonmind.workflows.temporal import github_issue_claim_recovery as recovery
from moonmind.workflows.temporal import story_output_tools as tools
from moonmind.workflows.temporal.github_issue_attempts import (
    compute_effective_retry,
    parse_attempt_comment,
    render_attempt_comment,
)
from moonmind.workflows.temporal.github_issue_claim_lease import renew_owned_claim
from moonmind.workflows.temporal.issue_claim_store import (
    IssueClaimStore,
    publish_claim_comment,
)
from tests.unit.workflows.temporal.test_github_issue_claim_recovery import (
    _announce,
    _closed,
    history_events,
)
from tests.unit.workflows.temporal.test_issue_claim_journey import journey  # noqa: F401


def _closed_capacity_wait(*, extra_activity="", previous_run=""):
    """The September outage: renew, inspect admission, wait, expire; no launch."""
    started = HistoryEvent(
        event_id=1,
        workflow_execution_started_event_attributes=WorkflowExecutionStartedEventAttributes(
            continued_execution_run_id=previous_run
        ),
    )
    activities = [
        "github_issue.renew_claim",
        "integration.resolve_adapter_metadata",
        "omnigent.evaluate_session_admission",
        "provider_profile.list",
        "provider_profile.manager_state",
    ]
    if extra_activity:
        activities.append(extra_activity)
    events = [started]
    for name in activities:
        event_id = len(events) + 1
        events.extend(
            [
                HistoryEvent(
                    event_id=event_id,
                    activity_task_scheduled_event_attributes=ActivityTaskScheduledEventAttributes(
                        activity_type=ActivityType(name=name)
                    ),
                ),
                HistoryEvent(
                    event_id=event_id + 1,
                    activity_task_completed_event_attributes=ActivityTaskCompletedEventAttributes(
                        scheduled_event_id=event_id
                    ),
                ),
            ]
        )
    parent_events = [
        HistoryEvent(
            event_id=1,
            workflow_execution_started_event_attributes=WorkflowExecutionStartedEventAttributes(),
        ),
        HistoryEvent(
            event_id=2,
            start_child_workflow_execution_initiated_event_attributes=StartChildWorkflowExecutionInitiatedEventAttributes(
                namespace="default", workflow_id="waiting-agent"
            ),
        ),
        HistoryEvent(
            event_id=3,
            child_workflow_execution_started_event_attributes=ChildWorkflowExecutionStartedEventAttributes(
                initiated_event_id=2,
                workflow_execution=WorkflowExecution(
                    workflow_id="waiting-agent", run_id="agent-run"
                ),
            ),
        ),
    ]
    handles = {
        "waiting-agent": SimpleNamespace(
            describe=AsyncMock(
                return_value=_closed(
                    WorkflowExecutionStatus.FAILED, "MoonMind.AgentRun", "agent-run"
                )
            ),
            fetch_history_events=lambda **kwargs: history_events(events),
        ),
        "capacity-wait": SimpleNamespace(
            describe=AsyncMock(
                return_value=_closed(
                    WorkflowExecutionStatus.FAILED,
                    "MoonMind.UserWorkflow",
                    "parent-run",
                )
            ),
            fetch_history_events=lambda **kwargs: history_events(parent_events),
            # Retained executions predate the typed capacity-backoff error.
            result=AsyncMock(
                side_effect=RuntimeError("GitHub issue claim lease expired")
            ),
        ),
    }
    return SimpleNamespace(get_workflow_handle=lambda name, **kwargs: handles[name])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault",
    [
        "none",
        "exhausted",
        "lost_ack",
        "held_slot",
        "dispatched",
        "unknown_activity",
        "prior_run",
    ],
)
async def test_expired_capacity_wait_is_recovered_from_history(journey, fault):
    state, service, sessions = journey
    store = IssueClaimStore(sessions)
    async with sessions.kw["bind"].begin() as connection:
        for table in (
            OmnigentRuntimeBindingRecord.__table__,
            ProviderProfileSlotLease.__table__,
        ):
            await connection.run_sync(lambda conn, table=table: table.create(conn))
    owner = "default/capacity-wait"
    await _announce(service, owner)
    await renew_owned_claim(store=store, service=service, owner=owner)
    receipt = await store.get(owner)
    expired = datetime.now(UTC) - timedelta(days=2)
    handoff = replace(
        parse_attempt_comment(receipt.comment_body).handoff,
        lease_renewed_at=(expired - timedelta(minutes=30)).isoformat(),
        lease_expires_at=expired.isoformat(),
        retry_allowance=1 if fault == "exhausted" else 3,
        retry_remaining=1 if fault == "exhausted" else 3,
    )
    if fault == "exhausted":
        assert (
            compute_effective_retry(
                [handoff],
                max_attempts=handoff.retry_allowance,
                now_epoch=datetime.now(UTC).timestamp(),
            ).reason_code
            == "budget_exhausted"
        )
    await publish_claim_comment(
        store, receipt, service, render_attempt_comment(handoff)
    )
    await store.end_ownership(owner, receipt.attempt_id, reason="reservation_expired")
    # Ordinary selection already removed the advisory label.
    state["labels"] = []
    state["lose_update_ack"] = fault == "lost_ack"
    if fault == "held_slot":
        async with sessions() as session, session.begin():
            session.add(
                ProviderProfileSlotLease(
                    runtime_id="codex",
                    workflow_id="waiting-agent",
                    profile_id="profile",
                    lease_state="held",
                )
            )
    client = _closed_capacity_wait(
        extra_activity={
            "dispatched": "omnigent.execute",
            "unknown_activity": "future.dispatch",
        }.get(fault, ""),
        previous_run="earlier-agent-run" if fault == "prior_run" else "",
    )
    result = await recovery.reconcile_local_claims(
        state={},
        store=store,
        service=service,
        client_factory=AsyncMock(return_value=client),
    )
    if fault not in {"none", "exhausted", "lost_ack"}:
        assert result["released"] == 0
        assert result["results"][0]["reasonCode"] == (
            "runtime_cleanup_pending"
            if fault == "held_slot"
            else "saved_work_requires_recovery"
        )
        assert parse_attempt_comment(state["comments"][0]["body"]).handoff == handoff
        return
    assert result["released"] == 1, result
    recovered = parse_attempt_comment(state["comments"][0]["body"]).handoff
    assert recovered.outcome == "runtime_unavailable"
    assert recovered.activity == "released" and recovered.writers_stopped
    assert recovered.attempt_id == handoff.attempt_id
    assert recovered.cooldown_until and state["labels"] == []
    retry = compute_effective_retry(
        [recovered],
        max_attempts=handoff.retry_allowance,
        now_epoch=datetime.now(UTC).timestamp(),
    )
    assert retry.remaining == handoff.retry_allowance
    assert retry.reason_code == "cooling_down"
    # After portable back-off, the actual search path admits a successor.
    elapsed = replace(recovered, cooldown_until=expired.isoformat())
    await publish_claim_comment(
        store, await store.get(owner), service, render_attempt_comment(elapsed)
    )
    next_run = await tools.load_github_issue_preset_brief(
        {"repository": "example/repo", "issueSearch": ""},
        {"execution_owner": "default/after-capacity-recovery"},
        github_service_factory=lambda: service,
    )
    assert (
        next_run.status == "COMPLETED" and next_run.completion_disposition != "idle"
    ), next_run.outputs
    assert (await store.get("default/after-capacity-recovery")).issue_number == 3970
