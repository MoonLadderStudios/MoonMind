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

from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
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


def _agent_request(**updates: Any) -> AgentExecutionRequest:
    payload: dict[str, Any] = {
        "agentKind": "external",
        "agentId": "omnigent",
        "correlationId": "mm:standalone-resolver",
        "idempotencyKey": "mm:standalone-resolver:node-1:execution:1",
    }
    payload.update(updates)
    return AgentExecutionRequest.model_validate(payload)


def test_standalone_runtime_instruction_denies_parent_owned_continuation() -> None:
    instruction = MoonMindRunWorkflow._terminal_continuation_authority_instruction(
        _agent_request()
    )

    assert "continuation authority: none" in instruction
    assert "Treat this execution as standalone" in instruction
    assert "keep supported bounded waits in the foreground" in instruction


def test_gated_runtime_instruction_names_validated_authority() -> None:
    instruction = MoonMindRunWorkflow._terminal_continuation_authority_instruction(
        _agent_request(
            terminalContinuationAuthority={
                "schemaVersion": "terminal-continuation-authority/v1",
                "gateType": "merge_automation",
                "ownerWorkflowId": "merge-automation:1",
                "ownerRunId": "merge-run-1",
                "ownerWorkflowType": "MoonMind.MergeAutomation",
                "allowedActions": ["reenter_gate"],
                "source": "validated_temporal_parent",
            }
        )
    )

    assert "continuation authority: validated" in instruction
    assert "MoonMind.MergeAutomation" in instruction
    assert "reenter_gate" in instruction


def test_continuation_authority_is_exposed_to_runtime_adapter_metadata() -> None:
    request = _agent_request(
        parameters={
            "metadata": {"moonmind": {"latestContextPackRef": "artifact://context"}},
            "title": "Resolve PR",
        }
    )
    authority_instruction = (
        MoonMindRunWorkflow._terminal_continuation_authority_instruction(request)
    )

    parameters = (
        MoonMindRunWorkflow._parameters_with_terminal_continuation_authority_instruction(
            request,
            authority_instruction=authority_instruction,
        )
    )

    assert parameters["title"] == "Resolve PR"
    assert (
        parameters["metadata"]["moonmind"]["latestContextPackRef"]
        == "artifact://context"
    )
    assert (
        parameters["metadata"]["moonmind"][
            "terminalContinuationAuthorityInstruction"
        ]
        == authority_instruction
    )


@pytest.mark.parametrize(
    ("metadata", "provider_error_code"),
    [
        (
            {
                "terminalContractOutcome": "terminal_failure",
                "terminalContractRecoveryOutcome": "unsupported_or_exhausted",
                "mergeAutomationDisposition": "manual_review",
            },
            None,
        ),
        (
            {
                "terminalContractOutcome": "terminal_failure",
                "terminalContractRecoveryOutcome": "unsupported_or_exhausted",
                "mergeAutomationDisposition": "failed",
            },
            None,
        ),
        (
            {
                "terminalContractOutcome": "continuation_requested",
                "terminalContractRecoveryOutcome": "continuation_rejected_unowned",
                "mergeAutomationDisposition": "reenter_gate",
            },
            "PR_RESOLVER_REENTER_GATE",
        ),
    ],
)
def test_terminal_contract_decisions_do_not_trigger_generic_runtime_retry(
    monkeypatch: pytest.MonkeyPatch,
    metadata: dict[str, str],
    provider_error_code: str | None,
) -> None:
    monkeypatch.setattr(run_workflow_module.workflow, "patched", lambda _patch: True)
    workflow = MoonMindRunWorkflow()
    result = {
        "status": "FAILED",
        "outputs": {
            "error": "execution_error",
            "failureClass": "execution_error",
            "providerErrorCode": provider_error_code,
            "metadata": metadata,
        },
    }

    assert (
        workflow._activity_result_retryable(
            result,
            failure_message="execution_error",
            tool_type="agent_runtime",
        )
        is False
    )


def test_missing_terminal_evidence_remains_retryable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(run_workflow_module.workflow, "patched", lambda _patch: True)
    workflow = MoonMindRunWorkflow()
    result = {
        "status": "FAILED",
        "outputs": {
            "error": "execution_error",
            "failureClass": "execution_error",
            "metadata": {
                "terminalContractOutcome": "terminal_failure",
                "terminalContractRecoveryOutcome": "continuation_boundary_unavailable",
                "terminalContractMissingEvidence": ["var/pr_resolver/result.json"],
            },
        },
    }

    assert workflow._activity_result_retryable(
        result,
        failure_message="execution_error",
        tool_type="agent_runtime",
    )


def test_real_provider_failure_with_rejected_continuation_remains_retryable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(run_workflow_module.workflow, "patched", lambda _patch: True)
    workflow = MoonMindRunWorkflow()
    result = {
        "status": "FAILED",
        "outputs": {
            "error": "execution_error",
            "failureClass": "execution_error",
            "providerErrorCode": "RATE_LIMITED",
            "retryRecommendation": "retry",
            "metadata": {
                "terminalContractOutcome": "continuation_requested",
                "terminalContractRecoveryOutcome": (
                    "continuation_rejected_failure_provenance"
                ),
                "mergeAutomationDisposition": "reenter_gate",
            },
        },
    }

    assert workflow._activity_result_retryable(
        result,
        failure_message="execution_error",
        tool_type="agent_runtime",
    )


def test_existing_history_preserves_provider_retry_decision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(run_workflow_module.workflow, "patched", lambda _patch: False)
    workflow = MoonMindRunWorkflow()
    result = {
        "status": "FAILED",
        "outputs": {
            "error": "execution_error",
            "failureClass": "execution_error",
            "metadata": {
                "terminalContractOutcome": "terminal_failure",
                "mergeAutomationDisposition": "manual_review",
            },
        },
    }

    assert workflow._activity_result_retryable(
        result,
        failure_message="execution_error",
        tool_type="agent_runtime",
    )


def test_explicit_step_retry_accepts_typed_integration_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Omnigent dropped-turn contract authorizes a fresh Step Execution."""

    monkeypatch.setattr(run_workflow_module.workflow, "patched", lambda _patch: True)
    workflow = MoonMindRunWorkflow()
    result = {
        "status": "FAILED",
        "outputs": {
            "error": "integration_error",
            "failureClass": "integration_error",
            "providerErrorCode": "OMNIGENT_CURRENT_TURN_NOT_STARTED",
            "retryRecommendation": "retry_step_execution",
        },
    }

    assert workflow._activity_result_retryable(
        result,
        failure_message="integration_error",
        tool_type="agent_runtime",
    )


def test_explicit_step_retry_accepts_transport_failure_before_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A status-less transport failure before any provider work is retryable.

    Regression for mm:2ebfad84-1c13-4572-af6f-bc1140f72842: an Omnigent
    ``ReadTimeout`` during first-message POST surfaced as
    ``integration_error``/``omnigent_http_error`` with no retry recommendation
    and failed the workflow permanently after one 150s attempt. The executor
    now recommends a fresh step execution when no work exists to preserve,
    and the parent honors it through the existing explicit-retry contract.
    """

    monkeypatch.setattr(run_workflow_module.workflow, "patched", lambda _patch: True)
    workflow = MoonMindRunWorkflow()
    result = {
        "status": "FAILED",
        "outputs": {
            "error": "integration_error",
            "failureClass": "integration_error",
            "providerErrorCode": "omnigent_http_error",
            "retryRecommendation": "retry_step_execution",
        },
    }

    assert workflow._activity_result_retryable(
        result,
        failure_message="integration_error",
        tool_type="agent_runtime",
    )


def test_explicit_step_retry_does_not_override_permanent_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Contradictory retry metadata cannot override permanent failure authority."""

    monkeypatch.setattr(run_workflow_module.workflow, "patched", lambda _patch: True)
    workflow = MoonMindRunWorkflow()
    result = {
        "status": "FAILED",
        "outputs": {
            "error": "permanent",
            "retryRecommendation": "retry_step_execution",
        },
    }

    assert not workflow._activity_result_retryable(
        result,
        failure_message="permanent",
        tool_type="agent_runtime",
    )


def test_existing_history_does_not_adopt_explicit_integration_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The new retry decision is gated away from in-flight workflow histories."""

    monkeypatch.setattr(run_workflow_module.workflow, "patched", lambda _patch: False)
    workflow = MoonMindRunWorkflow()
    result = {
        "status": "FAILED",
        "outputs": {
            "error": "integration_error",
            "failureClass": "integration_error",
            "providerErrorCode": "OMNIGENT_CURRENT_TURN_NOT_STARTED",
            "retryRecommendation": "retry_step_execution",
        },
    }

    assert not workflow._activity_result_retryable(
        result,
        failure_message="integration_error",
        tool_type="agent_runtime",
    )


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
        "headSha": "abc123",
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


def test_typed_gated_continuation_accepts_snake_case_retry_seconds() -> None:
    workflow = MoonMindRunWorkflow()

    workflow._record_execution_context(
        node_id="resolve-pr",
        execution_result={
            "outputs": {
                "gated_continuation": {
                    "gate_type": "merge_automation",
                    "action": "reenter_gate",
                    "retry_after_seconds": 90,
                }
            }
        },
    )

    assert workflow._gated_continuation_request["retryAfterSeconds"] == 90


def test_typed_merge_automation_continuation_exposes_parent_disposition() -> None:
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

    assert workflow._merge_automation_disposition == "reenter_gate"
    assert workflow._publish_context["mergeAutomationDisposition"] == "reenter_gate"


def test_gated_continuation_state_is_cleared_when_next_step_has_none() -> None:
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
    workflow._record_execution_context(
        node_id="summarize",
        execution_result={"outputs": {"message": "done"}},
    )

    assert workflow._gated_continuation_request is None
    assert workflow._merge_automation_disposition is None
    assert "gatedContinuation" not in workflow._publish_context
    assert "mergeAutomationDisposition" not in workflow._publish_context


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
