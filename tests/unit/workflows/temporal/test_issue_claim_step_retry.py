"""Failed-step retry reconciles the real durable claim before resetting history."""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from temporalio.client import WorkflowExecutionStatus

from api_service.db.models import TemporalWorkflowType
from moonmind.workflows.temporal import github_issue_claim_lease as leases
from moonmind.workflows.temporal import retry_from_step, story_output_tools
from moonmind.workflows.temporal.github_issue_attempts import parse_attempt_comment
from moonmind.workflows.temporal.issue_claim_store import IssueClaimStore
from moonmind.workflows.temporal.service import (
    TemporalExecutionRetryUnavailableError,
    TemporalExecutionService,
    TemporalExecutionTargetRunChangedError,
)
from tests.unit.workflows.temporal import test_issue_claim_journey as claim_journey
from tests.unit.workflows.temporal.test_retry_from_step import (
    _rows,
    _three_step_failure,
)

# Register the shared fixture without shadowing an unused imported name.
journey = claim_journey.journey

pytestmark = pytest.mark.usefixtures("deployment_without_recorded_connections")


@pytest.mark.asyncio
@pytest.mark.parametrize("replayable", [True, False])
@pytest.mark.parametrize("status_fault", [None, "rejected", "readback"])
async def test_retry_reacquires_only_after_replay_and_keeps_workflow_and_step(
    journey, monkeypatch, replayable, status_fault
):
    state, github, sessions = journey
    owner = "default/failed-workflow"
    await story_output_tools.load_github_issue_preset_brief(
        {"repository": "example/repo", "issueNumber": 3970},
        {"execution_owner": owner},
        github_service_factory=lambda: github,
    )
    store = IssueClaimStore(sessions)
    original = await store.get(owner)
    clock = leases.utc_now() + timedelta(minutes=31)
    monkeypatch.setattr(leases, "utc_now", lambda: clock)
    await leases.reconcile_expired_issue(
        service=github, repository="example/repo", issue_number=3970
    )
    assert state["labels"] == []
    monkeypatch.setattr(
        "moonmind.workflows.adapters.github_service.GitHubService", lambda: github
    )
    replay = AsyncMock(return_value=None if replayable else "history changed")
    monkeypatch.setattr(retry_from_step, "replay_failure_before_reset", replay)
    history, boundaries = _three_step_failure()
    record = SimpleNamespace(
        workflow_type=TemporalWorkflowType.USER_WORKFLOW,
        namespace="default",
        workflow_id="failed-workflow",
        run_id="failed-run",
        memo={},
        last_update_idempotency_key=None,
        last_update_response=None,
    )

    async def reset(*args, **kwargs):
        current = await store.get(owner)
        assert not leases.expired(parse_attempt_comment(current.comment_body).handoff)
        assert state["labels"] == ["status: in-progress"]
        assert args == ("failed-workflow",)
        assert kwargs["run_id"] == "failed-run"
        assert kwargs["workflow_task_finish_event_id"] == boundaries[3]
        return "retried-run"

    adapter = SimpleNamespace(
        describe_workflow=AsyncMock(
            return_value=SimpleNamespace(
                run_id="failed-run",
                status=WorkflowExecutionStatus.FAILED,
                parent_id=None,
            )
        ),
        fetch_workflow_history=AsyncMock(return_value=history),
        reset_workflow_execution=AsyncMock(side_effect=reset),
    )
    async with sessions() as session:
        service = TemporalExecutionService(session, client_adapter=adapter)
        sync = AsyncMock()
        monkeypatch.setattr(service, "_sync_retried_run_projection", sync)
        kwargs = dict(
            ledger_rows=_rows("completed", "completed", "failed", "pending"),
            idempotency_key="retry-failed-step",
            expected_run_id="failed-run",
        )
        if not replayable:
            with pytest.raises(TemporalExecutionRetryUnavailableError) as error:
                await service.retry_execution_from_step(record, **kwargs)
            assert error.value.reason == "history_not_replayable"
            adapter.reset_workflow_execution.assert_not_awaited()
            assert (await store.get(owner)).comment_body == original.comment_body
            return
        if status_fault:
            add_labels = github.add_issue_labels

            async def unavailable_status(**kwargs):
                if status_fault == "rejected":
                    return {"ok": False, "reasonCode": "fixture_rejected"}
                result = await add_labels(**kwargs)
                state["failed_read_path"] = "/repos/example/repo/issues/3970"
                return result

            monkeypatch.setattr(github, "add_issue_labels", unavailable_status)
            with pytest.raises(TemporalExecutionRetryUnavailableError) as error:
                await service.retry_execution_from_step(record, **kwargs)
            assert error.value.reason == "issue_claim_unavailable"
            adapter.reset_workflow_execution.assert_not_awaited()
            sync.assert_not_awaited()
            monkeypatch.setattr(github, "add_issue_labels", add_labels)
            state.pop("failed_read_path", None)
        response = await service.retry_execution_from_step(record, **kwargs)
        repeated = await service.retry_execution_from_step(record, **kwargs)

    assert response == repeated
    assert response["workflowId"] == "failed-workflow"
    assert response["stepId"] == "step-3"
    assert response["sourceRunId"] == "failed-run"
    assert response["runId"] == "retried-run"
    adapter.reset_workflow_execution.assert_awaited_once()
    sync.assert_awaited_once_with("failed-workflow", "retried-run")
    assert len(state["comments"]) == 1
    assert (await store.get(owner)).attempt_id == original.attempt_id


@pytest.mark.asyncio
@pytest.mark.parametrize("claimed", [True, False, "expired"])
async def test_continuation_discovers_claim_from_activity_ancestry(
    journey, claimed, monkeypatch
):
    _, github, sessions = journey
    owner = "default/continuation"
    store = IssueClaimStore(sessions)
    if claimed:
        await story_output_tools.load_github_issue_preset_brief(
            {"repository": "example/repo", "issueNumber": 3970},
            {"execution_owner": owner},
            github_service_factory=lambda: github,
        )

    if claimed == "expired":
        clock = leases.utc_now() + timedelta(minutes=31)
        monkeypatch.setattr(leases, "utc_now", lambda: clock)
    result = await leases.renew_execution_claim(
        {}, store=store, service=github, owner=owner
    )

    if claimed == "expired":
        assert result["status"] == "lost"
        assert leases.expired(
            parse_attempt_comment((await store.get(owner)).comment_body).handoff
        )
    elif claimed:
        receipt = await store.get(owner)
        assert result["status"] == "renewed"
        assert result["issueClaimLease"] == receipt.handoff()["issueClaimLease"]
    else:
        assert result == {"status": "unclaimed"}


@pytest.mark.asyncio
@pytest.mark.parametrize("blocked", ["released", "finalization_json", "run_changed"])
async def test_step_retry_checks_current_owner_before_touching_github(journey, blocked):
    state, github, sessions = journey
    owner = "default/changed-workflow"
    await story_output_tools.load_github_issue_preset_brief(
        {"repository": "example/repo", "issueNumber": 3970},
        {"execution_owner": owner},
        github_service_factory=lambda: github,
    )
    store = IssueClaimStore(sessions)
    if blocked != "run_changed":
        async with store.locked(owner) as row:
            setattr(
                row,
                blocked,
                {"intent": "failed"} if blocked == "finalization_json" else True,
            )
    original = state["comments"][0]["body"]
    adapter = SimpleNamespace(
        describe_workflow=AsyncMock(
            return_value=SimpleNamespace(
                run_id="successor-run" if blocked == "run_changed" else "failed-run",
            )
        )
    )
    async with sessions() as session:
        service = TemporalExecutionService(session, client_adapter=adapter)
        record = SimpleNamespace(namespace="default", workflow_id="changed-workflow")
        error = (
            TemporalExecutionTargetRunChangedError
            if blocked == "run_changed"
            else TemporalExecutionRetryUnavailableError
        )
        with pytest.raises(error):
            async with service._issue_claim_for_step_retry(
                record, source_run_id="failed-run"
            ):
                pytest.fail("Changed ownership must not authorize a reset")
    assert state["comments"][0]["body"] == original
