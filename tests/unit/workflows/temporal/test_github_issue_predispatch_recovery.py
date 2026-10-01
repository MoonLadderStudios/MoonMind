"""Recover active claims whose retained AgentRun history never dispatched work."""

# ruff: noqa: F811 -- imported pytest fixture

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from temporalio.api.common.v1 import ActivityType, WorkflowExecution
from temporalio.api.failure.v1 import ApplicationFailureInfo, Failure
from temporalio.api.history.v1 import (
    ActivityTaskCompletedEventAttributes,
    ActivityTaskFailedEventAttributes,
    ActivityTaskScheduledEventAttributes,
    ChildWorkflowExecutionStartedEventAttributes,
    ExternalWorkflowExecutionSignaledEventAttributes,
    HistoryEvent,
    SignalExternalWorkflowExecutionInitiatedEventAttributes,
    StartChildWorkflowExecutionInitiatedEventAttributes,
    WorkflowExecutionFailedEventAttributes,
    WorkflowExecutionSignaledEventAttributes,
    WorkflowExecutionStartedEventAttributes,
)
from temporalio.client import WorkflowExecutionStatus
from temporalio.converter import DefaultFailureConverter, DefaultPayloadConverter
from temporalio.exceptions import ApplicationError

from api_service.db.models import OmnigentRuntimeBindingRecord, ProviderProfileSlotLease
from moonmind.workflows.temporal import github_issue_claim_recovery as recovery
from moonmind.workflows.temporal import story_output_tools as tools
from moonmind.workflows.temporal.github_issue_attempts import (
    RUNTIME_UNAVAILABLE_COOLDOWN_SECONDS,
    compute_effective_retry,
    parse_attempt_comment,
    render_attempt_comment,
)
from moonmind.workflows.temporal.github_issue_claim_lease import (
    parse_time,
    renew_owned_claim,
)
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


def _closed_capacity_wait(
    *,
    extra_activity="",
    previous_run="",
    failure_type="",
    failure_message="GitHub issue claim lease expired",
    capacity_request=True,
    failed_admission=False,
    nested_failure=False,
    capacity_acknowledged=True,
    capacity_granted=False,
    terminal_recorded=True,
):
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
        if failed_admission and name == "integration.resolve_adapter_metadata":
            events[-1] = HistoryEvent(
                event_id=event_id + 1,
                activity_task_failed_event_attributes=ActivityTaskFailedEventAttributes(
                    scheduled_event_id=event_id,
                    failure=Failure(
                        message="Adapter is not registered",
                        application_failure_info=ApplicationFailureInfo(
                            type="ValueError", non_retryable=True
                        ),
                    ),
                ),
            )
    if capacity_request:
        event_id = len(events) + 1
        events.extend(
            [
                HistoryEvent(
                    event_id=event_id,
                    signal_external_workflow_execution_initiated_event_attributes=SignalExternalWorkflowExecutionInitiatedEventAttributes(
                        namespace="default",
                        workflow_execution=WorkflowExecution(
                            workflow_id="provider-profile-manager:codex"
                        ),
                        signal_name="request_slot",
                    ),
                ),
                HistoryEvent(
                    event_id=event_id + 1,
                    external_workflow_execution_signaled_event_attributes=ExternalWorkflowExecutionSignaledEventAttributes(
                        initiated_event_id=event_id
                    ),
                ),
            ]
        )
        if not capacity_acknowledged:
            events.pop()
    if capacity_granted:
        events.append(
            HistoryEvent(
                event_id=len(events) + 1,
                workflow_execution_signaled_event_attributes=WorkflowExecutionSignaledEventAttributes(
                    signal_name="slot_assigned"
                ),
            )
        )
    failure = Failure(
        message=failure_message,
        application_failure_info=ApplicationFailureInfo(
            type=failure_type, non_retryable=True
        ),
    )
    if failure_type == "SlotAcquisitionTimeout":
        # AgentRun raises this from wait_condition's TimeoutError; use the SDK
        # converter so its real exception context is retained in the fixture.
        try:
            try:
                raise TimeoutError()
            except TimeoutError:
                raise ApplicationError(
                    "Provider capacity wait timed out",
                    type="SlotAcquisitionTimeout",
                    non_retryable=True,
                )
        except ApplicationError as error:
            DefaultFailureConverter().to_failure(
                error, DefaultPayloadConverter(), failure
            )
    if nested_failure:
        failure.cause.CopyFrom(
            Failure(
                message="Invalid profile",
                application_failure_info=ApplicationFailureInfo(
                    type="ProfileResolutionError", non_retryable=True
                ),
            )
        )
    if terminal_recorded:
        events.append(
            HistoryEvent(
                event_id=len(events) + 1,
                workflow_execution_failed_event_attributes=WorkflowExecutionFailedEventAttributes(
                    failure=failure
                ),
            )
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
        "manager_started",
        "permanent_profile",
        "permanent_adapter",
        "failed_admission",
        "missing_capacity_request",
        "nested_permanent",
        "request_not_acknowledged",
        "capacity_granted",
        "missing_terminal",
        "capacity_timeout",
        "capacity_timeout_permanent",
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
        # Even before the sweep records its outcome, a lapsed attempt no
        # longer spends the single-attempt allowance.
        before = compute_effective_retry(
            [handoff],
            max_attempts=handoff.retry_allowance,
            now_epoch=datetime.now(UTC).timestamp(),
        )
        assert before.reason_code == "allowed"
        assert before.remaining == handoff.retry_allowance
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
            "manager_started": "provider_profile.ensure_manager",
        }.get(fault, ""),
        previous_run="earlier-agent-run" if fault == "prior_run" else "",
        failure_type={
            "permanent_profile": "ProfileResolutionError",
            "permanent_adapter": "ValueError",
            "capacity_timeout": "SlotAcquisitionTimeout",
            "capacity_timeout_permanent": "SlotAcquisitionTimeout",
        }.get(fault, ""),
        failure_message=(
            "Invalid profile or adapter"
            if fault in {"permanent_profile", "permanent_adapter"}
            else "GitHub issue claim lease expired"
        ),
        capacity_request=fault != "missing_capacity_request",
        failed_admission=fault == "failed_admission",
        nested_failure=fault in {"nested_permanent", "capacity_timeout_permanent"},
        capacity_acknowledged=fault != "request_not_acknowledged",
        capacity_granted=fault == "capacity_granted",
        terminal_recorded=fault != "missing_terminal",
    )
    result = await recovery.reconcile_local_claims(
        state={},
        store=store,
        service=service,
        client_factory=AsyncMock(return_value=client),
    )
    if fault not in {
        "none",
        "exhausted",
        "lost_ack",
        "manager_started",
        "capacity_timeout",
    }:
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
    # The back-off runs from when the attempt ended two days ago, so the
    # sweep's late recording does not defer the issue all over again.
    assert parse_time(recovered.cooldown_until) == expired + timedelta(
        seconds=RUNTIME_UNAVAILABLE_COOLDOWN_SECONDS
    )
    assert retry.reason_code == "allowed"
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


@pytest.mark.asyncio
async def test_fast_capacity_backoff_records_an_unspent_allowance(journey):
    """The live backoff releases before its lease lapses; the record says so.

    ``ISSUE_CLAIM_CAPACITY_BLOCKED`` fires on the first renewal cadence, so
    the sweep finalizes an attempt whose own handoff is still ``in_progress``
    under a valid lease. That attempt never ran and is never charged, so the
    released handoff must not report it as a spent retry.
    """
    from moonmind.workflows.temporal.github_issue_lease_workflow import (
        CAPACITY_BLOCKED_CODE,
    )

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
    announced = parse_attempt_comment(state["comments"][0]["body"]).handoff
    assert parse_time(announced.lease_expires_at) > datetime.now(UTC)
    assert announced.retry_remaining == announced.retry_allowance == 3
    message = (
        f"{CAPACITY_BLOCKED_CODE}: queued behind unavailable local capacity; "
        "releasing the issue reservation and backing off"
    )
    client = _closed_capacity_wait(
        failure_type="CapacityBlocked", failure_message=message
    )
    client.get_workflow_handle("capacity-wait").result = AsyncMock(
        side_effect=RuntimeError(f"Child Workflow execution failed: {message}")
    )

    result = await recovery.reconcile_local_claims(
        state={},
        store=store,
        service=service,
        client_factory=AsyncMock(return_value=client),
    )

    assert result["released"] == 1, result
    recovered = parse_attempt_comment(state["comments"][0]["body"]).handoff
    assert recovered.outcome == "runtime_unavailable"
    assert recovered.next_action == "fresh_retry"
    assert recovered.retry_remaining == announced.retry_allowance
    assert "Retry: 3/3 remaining." in state["comments"][0]["body"]
