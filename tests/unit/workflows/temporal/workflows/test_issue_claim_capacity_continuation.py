"""Capacity waits after an issue claim's work began keep the reservation.

The typed ``ISSUE_CLAIM_CAPACITY_BLOCKED`` backoff releases an issue only
before the claim's first slot grant. A retry or later step of the same claim
re-queues for the Provider Profile after earlier work already ran; when another
workflow takes the freed slot in between, that wait must not fail the run as
"no work started" and throw away the claim's progress.
"""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from temporalio import workflow

from moonmind.schemas.agent_run_progress import (
    AGENT_RUN_PROGRESS_PATCH_ID,
    AGENT_RUN_PROGRESS_RESUME_EDGES_PATCH_ID,
    build_progress_projection,
)
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.workflows.temporal import github_issue_lease_workflow
from moonmind.workflows.temporal.workflows.agent_run import MoonMindAgentRun
from moonmind.workflows.temporal.workflows.run import MoonMindUserWorkflow

CHILD_WF = "parent-wf:agent:step-2"
LEASE = {
    "owner": "default/parent-wf",
    "attemptId": "att-1",
    "repository": "example/repo",
    "issueNumber": 2703,
    "commentId": "123",
}


def _parent(monkeypatch) -> MoonMindUserWorkflow:
    parent = MoonMindUserWorkflow()
    monkeypatch.setattr(parent, "_update_search_attributes", lambda: None)
    monkeypatch.setattr(parent, "_update_memo", lambda: None)
    patches = {AGENT_RUN_PROGRESS_PATCH_ID, AGENT_RUN_PROGRESS_RESUME_EDGES_PATCH_ID}
    monkeypatch.setattr(workflow, "patched", lambda patch_id: patch_id in patches)
    monkeypatch.setattr(
        workflow, "now", lambda: datetime(2026, 10, 4, tzinfo=timezone.utc)
    )
    monkeypatch.setattr(workflow, "deprecate_patch", lambda _patch_id: None)
    monkeypatch.setattr(
        workflow,
        "info",
        lambda: SimpleNamespace(
            namespace="default", workflow_id="parent-wf", run_id="run-1", parent=None
        ),
    )
    parent._record_trusted_issue_context(
        {"trustedSource": "moonmind.github.get_issue", "issueClaimLease": LEASE}
    )
    parent._active_agent_child_workflow_id = CHILD_WF
    return parent


def _progress(revision: int, state: str, reason: str, wait: str = "none") -> dict:
    return build_progress_projection(
        agent_run_workflow_id=CHILD_WF,
        agent_run_run_id="child-run",
        source_workflow_id="parent-wf",
        source_run_id="run-1",
        step_execution_id="parent-wf:run-1:step-2:execution:1",
        source_generation=CHILD_WF,
        projection_revision=revision,
        state=state,
        reason_code=reason,
        wait_code=wait,
    ).canonical_dict()


def _build_request(parent: MoonMindUserWorkflow):
    return parent._build_agent_execution_request(
        node_inputs={"runtime": {"mode": "omnigent"}},
        node_id="step-2",
        tool_name="auto",
        workflow_parameters={},
    )


def _next_request_lease(parent: MoonMindUserWorkflow) -> dict:
    """The lease an AgentRun dispatched now would receive."""
    request = parent._with_issue_claim_work_started(_build_request(parent))
    return request.parameters["issueClaimLease"]


def test_later_agent_run_learns_the_claim_already_held_capacity(monkeypatch):
    parent = _parent(monkeypatch)
    parent.agent_run_progress(
        _progress(1, "awaiting_slot", "awaiting_provider_capacity", "provider_capacity")
    )
    parent.agent_run_progress(_progress(2, "launching", "launching"))

    lease = _next_request_lease(parent)

    assert lease["workStarted"] is True
    assert {key: lease[key] for key in LEASE} == LEASE


def test_request_cached_before_the_claim_started_is_stamped_at_dispatch(monkeypatch):
    # Publish repair and blocker rechecks re-dispatch requests built earlier.
    parent = _parent(monkeypatch)
    cached = _build_request(parent)
    parent.agent_run_progress(_progress(1, "running", "running"))

    dispatched = parent._with_issue_claim_work_started(cached)

    assert dispatched.parameters["issueClaimLease"]["workStarted"] is True


def test_claim_still_waiting_for_its_first_slot_keeps_the_fast_backoff(monkeypatch):
    parent = _parent(monkeypatch)
    parent.agent_run_progress(
        _progress(1, "awaiting_slot", "awaiting_provider_capacity", "provider_capacity")
    )

    assert _next_request_lease(parent) == LEASE


def test_started_work_does_not_carry_over_to_a_different_claim(monkeypatch):
    parent = _parent(monkeypatch)
    parent.agent_run_progress(_progress(1, "running", "running"))
    other = {**LEASE, "attemptId": "att-2", "commentId": "456"}
    parent._record_trusted_issue_context(
        {"trustedSource": "moonmind.github.get_issue", "issueClaimLease": other}
    )

    assert _next_request_lease(parent) == other


async def _should_renew_for(monkeypatch, lease: dict):
    captured = {}

    async def capture(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(workflow, "patched", lambda _patch_id: False)
    monkeypatch.setattr(
        github_issue_lease_workflow, "execute_with_issue_lease", capture
    )
    run = MoonMindAgentRun()
    await run.run(
        AgentExecutionRequest(
            agentKind="external",
            agentId="omnigent",
            correlationId="corr",
            idempotencyKey="idem",
            parameters={"issueClaimLease": lease},
        )
    )
    # Queued behind a slot another workflow holds; never granted in this run.
    run._capacity_requested = True
    return captured["should_renew"]


@pytest.mark.asyncio
async def test_retry_of_a_started_claim_keeps_renewing_while_queued(monkeypatch):
    should_renew = await _should_renew_for(monkeypatch, {**LEASE, "workStarted": True})

    assert should_renew() is True


@pytest.mark.asyncio
async def test_first_launch_of_a_claim_still_backs_off_while_queued(monkeypatch):
    should_renew = await _should_renew_for(monkeypatch, dict(LEASE))

    assert should_renew() is False
