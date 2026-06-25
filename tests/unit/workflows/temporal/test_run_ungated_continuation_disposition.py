"""Regression coverage for ungated merge-automation continuation dispositions.

Incident background: a top-level ``MoonMind.UserWorkflow`` running ``pr-resolver``
finished with ``mergeAutomationDisposition = "reenter_gate"`` and was reported as
``status: success`` even though the pull request was never merged. ``reenter_gate``
is a *continuation* disposition that only has meaning inside a
``MoonMind.MergeAutomation`` gate that re-enters and finalizes the merge. When the
resolver runs standalone (no owning gate), that continuation can never be re-entered,
so the run must not be treated as a successful PR resolution.
"""

from __future__ import annotations

from typing import Any

import pytest
from temporalio import client, exceptions
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from moonmind.workflows.temporal.workflows.merge_gate import (
    build_resolver_run_request,
)
from moonmind.workflows.temporal.workflows import run as run_workflow_module
from moonmind.workflows.temporal.workflows.run import (
    MoonMindRunWorkflow,
    MoonMindUserWorkflow,
)


def _ungated_resolver_parameters() -> dict[str, object]:
    """Parameters matching the standalone pr-resolver run from the incident.

    These mirror the real ``initial_parameters`` shape: a ``workflow`` block selecting
    the ``pr-resolver`` skill with ``publishMode=none`` and *no* ``mergeGate`` block.
    """

    return {
        "requestType": "task",
        "repository": "MoonLadderStudios/Tactics",
        "publishMode": "none",
        "targetRuntime": "claude_code",
        "workflow": {
            "instructions": "Resolve PR #1863 on branch story-006.",
            "tool": {"type": "skill", "name": "pr-resolver"},
            "skill": {"name": "pr-resolver"},
            "inputs": {"repo": "MoonLadderStudios/Tactics", "pr": "1863"},
            "publish": {"mode": "none"},
        },
    }


def _gated_resolver_parameters() -> dict[str, object]:
    """Parameters produced by MoonMind.MergeAutomation when it launches the resolver."""

    request = build_resolver_run_request(
        parent_workflow_id="mm:parent-merge-automation",
        pull_request={
            "repo": "MoonLadderStudios/Tactics",
            "number": 1863,
            "url": "https://github.com/MoonLadderStudios/Tactics/pull/1863",
            "headBranch": "story-006",
            "baseBranch": "main",
            "headSha": "e7a62914",
        },
        jira_issue_key=None,
        merge_method="squash",
    )
    return request["initial_parameters"]


def test_gated_parameters_are_recognized_as_merge_automation_owned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent_info = type(
        "ParentInfo",
        (),
        {"workflow_id": "mm:parent-merge-automation"},
    )
    workflow_info = type("WorkflowInfo", (), {"parent": parent_info})
    monkeypatch.setattr(run_workflow_module.workflow, "info", workflow_info)

    workflow = MoonMindRunWorkflow()
    assert workflow._is_merge_automation_gated(_gated_resolver_parameters()) is True


def test_standalone_resolver_parameters_are_not_gated() -> None:
    workflow = MoonMindRunWorkflow()
    assert workflow._is_merge_automation_gated(_ungated_resolver_parameters()) is False


def test_empty_merge_gate_without_parent_is_not_gated() -> None:
    workflow = MoonMindRunWorkflow()
    params = _ungated_resolver_parameters()
    params["mergeGate"] = {"pullRequestUrl": "https://example/pr/1"}
    assert workflow._is_merge_automation_gated(params) is False


def test_stale_merge_gate_payload_without_temporal_parent_is_not_gated() -> None:
    workflow = MoonMindRunWorkflow()
    params = _ungated_resolver_parameters()
    params["mergeGate"] = {
        "parentWorkflowId": "mm:parent-merge-automation",
        "pullRequestUrl": "https://example/pr/1",
    }

    assert workflow._is_merge_automation_gated(params) is False


def test_mismatched_temporal_parent_is_not_gated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent_info = type("ParentInfo", (), {"workflow_id": "mm:other-parent"})
    workflow_info = type("WorkflowInfo", (), {"parent": parent_info})
    monkeypatch.setattr(run_workflow_module.workflow, "info", workflow_info)

    workflow = MoonMindRunWorkflow()

    assert workflow._is_merge_automation_gated(_gated_resolver_parameters()) is False


def test_ungated_reenter_gate_disposition_blocks_success() -> None:
    workflow = MoonMindRunWorkflow()
    workflow._merge_automation_disposition = "reenter_gate"

    message = workflow._continuation_disposition_failure_message(
        _ungated_resolver_parameters()
    )

    assert message is not None
    assert "reenter_gate" in message
    assert "MergeAutomation" in message


def test_gated_reenter_gate_disposition_is_allowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent_info = type(
        "ParentInfo",
        (),
        {"workflow_id": "mm:parent-merge-automation"},
    )
    workflow_info = type("WorkflowInfo", (), {"parent": parent_info})
    monkeypatch.setattr(run_workflow_module.workflow, "info", workflow_info)

    workflow = MoonMindRunWorkflow()
    workflow._merge_automation_disposition = "reenter_gate"

    assert (
        workflow._continuation_disposition_failure_message(
            _gated_resolver_parameters()
        )
        is None
    )


def test_legacy_reenter_gate_maps_to_typed_gated_continuation() -> None:
    workflow = MoonMindRunWorkflow()

    workflow._record_execution_context(
        node_id="resolve-pr",
        execution_result={
            "outputs": {
                "mergeAutomationDisposition": "reenter_gate",
                "headSha": "abc123",
            }
        },
    )

    assert workflow._gated_continuation_request == {
        "schemaVersion": "gated-continuation/v1",
        "source": "legacy_merge_automation_disposition",
        "logicalStepId": "resolve-pr",
        "gateType": "merge_automation",
        "action": "reenter_gate",
        "targetLogicalStepId": "resolve-pr",
        "reason": (
            "Legacy pr-resolver merge automation disposition requires the "
            "workflow-owned merge gate to continue."
        ),
        "sideEffects": {"externalPullRequest": True},
    }
    assert (
        workflow._publish_context["gatedContinuation"]
        == workflow._gated_continuation_request
    )


def test_typed_gated_continuation_records_bounded_evidence() -> None:
    workflow = MoonMindRunWorkflow()

    workflow._record_execution_context(
        node_id="deploy",
        execution_result={
            "outputs": {
                "gatedContinuation": {
                    "gateType": "merge-automation",
                    "action": "reenter_gate",
                    "targetLogicalStepId": "deploy",
                    "reason": "CI still pending.",
                    "evidenceRefs": {
                        "gateSnapshot": "artifact://gate/snapshot",
                        "ignoredList": ["artifact://not-compact"],
                    },
                    "sideEffects": {"externalPullRequest": True},
                    "budget": {"maxAttempts": 3, "remaining": 2},
                }
            }
        },
    )

    assert workflow._gated_continuation_request == {
        "schemaVersion": "gated-continuation/v1",
        "source": "typed",
        "logicalStepId": "deploy",
        "gateType": "merge_automation",
        "action": "reenter_gate",
        "targetLogicalStepId": "deploy",
        "reason": "CI still pending.",
        "evidenceRefs": {"gateSnapshot": "artifact://gate/snapshot"},
        "sideEffects": {"externalPullRequest": True},
        "budget": {"maxAttempts": 3, "remaining": 2},
    }


def test_unsupported_typed_gated_continuation_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(run_workflow_module.workflow, "patched", lambda _patch: True)
    workflow = MoonMindRunWorkflow()
    workflow._record_execution_context(
        node_id="migration",
        execution_result={
            "outputs": {
                "gatedContinuation": {
                    "gateType": "database_migration",
                    "action": "wait_for_replica",
                    "reason": "Replica lag has not cleared.",
                }
            }
        },
    )

    message = workflow._continuation_disposition_failure_message(
        _ungated_resolver_parameters()
    )

    assert message is not None
    assert "unsupported_gate_type" in message
    assert "database_migration" in message
    assert "wait_for_replica" in message


def test_typed_merge_automation_continuation_requires_owning_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(run_workflow_module.workflow, "patched", lambda _patch: True)
    workflow = MoonMindRunWorkflow()
    workflow._record_execution_context(
        node_id="resolve-pr",
        execution_result={
            "outputs": {
                "gatedContinuation": {
                    "gateType": "merge_automation",
                    "action": "reenter_gate",
                    "reason": "Required checks are still running.",
                }
            }
        },
    )

    message = workflow._continuation_disposition_failure_message(
        _ungated_resolver_parameters()
    )

    assert message is not None
    assert "gateType='merge_automation'" in message
    assert "action='reenter_gate'" in message
    assert "not owned by that gate" in message


def test_typed_merge_automation_continuation_is_allowed_for_owning_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(run_workflow_module.workflow, "patched", lambda _patch: True)
    parent_info = type(
        "ParentInfo",
        (),
        {"workflow_id": "mm:parent-merge-automation"},
    )
    workflow_info = type("WorkflowInfo", (), {"parent": parent_info})
    monkeypatch.setattr(run_workflow_module.workflow, "info", workflow_info)

    workflow = MoonMindRunWorkflow()
    workflow._record_execution_context(
        node_id="resolve-pr",
        execution_result={
            "outputs": {
                "gatedContinuation": {
                    "gateType": "merge_automation",
                    "action": "reenter_gate",
                    "reason": "Required checks are still running.",
                }
            }
        },
    )

    assert (
        workflow._continuation_disposition_failure_message(
            _gated_resolver_parameters()
        )
        is None
    )


@pytest.fixture
def ungated_continuation_workflow_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        MoonMindUserWorkflow, "_trusted_owner_metadata", lambda self: ("user", "user-1")
    )
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "upsert_search_attributes",
        lambda _attributes: None,
    )
    monkeypatch.setattr(run_workflow_module.workflow, "upsert_memo", lambda _memo: None)

    async def fake_planning_stage(self: MoonMindUserWorkflow, **_kwargs: Any) -> str:
        return "artifact://plan/ungated-continuation"

    async def fake_execution_stage(self: MoonMindUserWorkflow, **_kwargs: Any) -> None:
        self._merge_automation_disposition = "reenter_gate"

    async def fake_finalizing_stage(
        self: MoonMindUserWorkflow,
        *,
        parameters: dict[str, Any],
        status: str,
        error: str | None = None,
    ) -> None:
        self._finish_summary = {
            "finishOutcome": {
                "code": "FAILED" if status == "failed" else "PUBLISH_DISABLED",
                "reason": error or status,
            },
            "publish": {"mode": self._publish_mode(parameters), "status": status},
        }

    async def fake_record_terminal_state(
        self: MoonMindUserWorkflow, **_kwargs: Any
    ) -> None:
        return None

    monkeypatch.setattr(MoonMindUserWorkflow, "_run_planning_stage", fake_planning_stage)
    monkeypatch.setattr(
        MoonMindUserWorkflow, "_run_execution_stage", fake_execution_stage
    )
    monkeypatch.setattr(
        MoonMindUserWorkflow, "_run_finalizing_stage", fake_finalizing_stage
    )
    monkeypatch.setattr(
        MoonMindUserWorkflow, "_record_terminal_state", fake_record_terminal_state
    )


@pytest.mark.asyncio
async def test_user_workflow_ungated_reenter_gate_disposition_fails_at_boundary(
    ungated_continuation_workflow_environment: None,
) -> None:
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="test-ungated-continuation-disposition",
            workflows=[MoonMindUserWorkflow],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            with pytest.raises(client.WorkflowFailureError) as exc_info:
                await env.client.execute_workflow(
                    MoonMindUserWorkflow.run,
                    {
                        "workflowType": "MoonMind.UserWorkflow",
                        "initialParameters": _ungated_resolver_parameters(),
                    },
                    id="test-user-workflow-ungated-continuation",
                    task_queue="test-ungated-continuation-disposition",
                )

            assert isinstance(exc_info.value.cause, exceptions.ApplicationError)
            assert exc_info.value.cause.non_retryable is True
            assert (
                "mergeAutomationDisposition='reenter_gate'"
                in exc_info.value.cause.message
            )
            assert "not owned by merge automation" in exc_info.value.cause.message


@pytest.mark.parametrize("disposition", ["merged", "already_merged"])
def test_terminal_dispositions_are_not_continuation_failures(disposition: str) -> None:
    workflow = MoonMindRunWorkflow()
    workflow._merge_automation_disposition = disposition

    assert (
        workflow._continuation_disposition_failure_message(
            _ungated_resolver_parameters()
        )
        is None
    )


@pytest.mark.parametrize("disposition", ["", None, "   ", "manual_review", "totally_new_state"])
def test_blank_or_unknown_dispositions_do_not_block(disposition) -> None:
    """Degraded/unknown provider dispositions must not trip the continuation guard."""

    workflow = MoonMindRunWorkflow()
    workflow._merge_automation_disposition = disposition

    assert (
        workflow._continuation_disposition_failure_message(
            _ungated_resolver_parameters()
        )
        is None
    )
