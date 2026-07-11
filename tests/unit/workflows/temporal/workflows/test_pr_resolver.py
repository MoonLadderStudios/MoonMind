from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest
from temporalio.exceptions import CancelledError

from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.workflows.temporal.workflows import pr_resolver as resolver_module
from moonmind.workflows.temporal.workflows.pr_resolver import (
    MoonMindPRResolverWorkflow,
    blocker_progress_signature,
    build_pr_resolver_start_input,
    classify_pr_resolver_snapshot,
)


@pytest.mark.parametrize(
    ("snapshot", "classification"),
    [
        ({"pullRequestMerged": True}, "already_merged"),
        ({"pullRequestOpen": False}, "manual_review"),
        ({"blockers": [{"kind": "merge_conflict"}]}, "merge_conflicts"),
        ({"checksComplete": True, "checksPassing": False}, "ci_failures"),
        ({"checksComplete": False}, "ci_running"),
        (
            {
                "blockers": [
                    {
                        "kind": "automated_review_pending",
                        "summary": "Automated review has requested changes.",
                    }
                ]
            },
            "actionable_comments",
        ),
        (
            {"blockers": [{"kind": "automated_review_pending", "retryable": True}]},
            "review_grace",
        ),
        ({"ready": True, "blockers": []}, "ready_to_merge"),
        (
            {"blockers": [{"kind": "external_state_unavailable", "retryable": True}]},
            "mergeability_transient",
        ),
        (
            {"blockers": [{"kind": "policy_denied", "retryable": False}]},
            "manual_review",
        ),
    ],
)
def test_classify_pr_resolver_snapshot_covers_gate_transitions(
    snapshot: dict[str, Any], classification: str
) -> None:
    assert classify_pr_resolver_snapshot(snapshot)["classification"] == classification


def test_blocker_progress_signature_changes_only_with_meaningful_state() -> None:
    snapshot = {
        "headSha": "abc",
        "baseSha": "base",
        "blockers": [{"kind": "checks_failed", "summary": "Tests failed"}],
    }
    first = blocker_progress_signature(snapshot, "ci_failures")
    assert first == blocker_progress_signature(dict(snapshot), "ci_failures")
    assert first != blocker_progress_signature(
        {**snapshot, "headSha": "def"}, "ci_failures"
    )


def _agent_request() -> AgentExecutionRequest:
    return AgentExecutionRequest(
        agentKind="managed",
        agentId="codex",
        correlationId="corr-1",
        idempotencyKey="agent-1",
        resolvedSkillsetRef="artifact://skills",
        workspaceSpec={"repository": "MoonLadderStudios/MoonMind"},
        skill={
            "name": "pr-resolver",
            "inputs": {"pr": "3150", "mergeMethod": "squash"},
        },
        parameters={"metadata": {"moonmind": {"selectedSkill": "pr-resolver"}}},
    )


def test_build_pr_resolver_start_input_preserves_prepared_agent_contract() -> None:
    request = _agent_request()
    result = build_pr_resolver_start_input(
        request=request,
        node_inputs={"skill": request.skill},
        workflow_parameters={
            "mergeGate": {
                "pullRequestUrl": "https://github.com/MoonLadderStudios/MoonMind/pull/3150",
                "headSha": "abc",
            }
        },
        parent_workflow_id="parent",
        parent_run_id="run",
        principal="user-1",
        step_id="resolve",
    )

    assert result.workflow_type == "MoonMind.PRResolver"
    assert result.pr_number == 3150
    assert result.head_sha == "abc"
    assert result.base_agent_request["resolvedSkillsetRef"] == "artifact://skills"


def test_build_pr_resolver_start_input_preserves_direct_inputs_and_gate_policy() -> None:
    request = _agent_request()
    result = build_pr_resolver_start_input(
        request=request,
        node_inputs={
            "pr": "feature/resolver",
            "mergeMethod": "rebase",
            "skill": {"name": "pr-resolver", "inputs": {}},
        },
        workflow_parameters={
            "mergeGate": {
                "pullRequestUrl": "https://github.com/MoonLadderStudios/MoonMind/pull/3150",
                "checks": "optional",
                "automatedReview": "disabled",
            }
        },
        parent_workflow_id="parent",
        parent_run_id="run",
        principal="user-1",
        step_id="resolve",
    )

    assert result.pr_number == 3150
    assert result.merge_method == "rebase"
    assert result.policy.checks == "optional"
    assert result.policy.automated_review == "disabled"
    assert result.owned_by_merge_automation_gate is True


def test_build_pr_resolver_start_input_accepts_resolved_standalone_branch() -> None:
    request = _agent_request().model_copy(
        update={"skill": {"name": "pr-resolver", "inputs": {"pr": "feature/mm-1200"}}}
    )

    result = build_pr_resolver_start_input(
        request=request,
        node_inputs={"skill": request.skill},
        workflow_parameters={"repository": "MoonLadderStudios/MoonMind"},
        parent_workflow_id="parent",
        parent_run_id="run",
        principal="user-1",
        step_id="resolve",
        resolved_pull_request={
            "resolved": True,
            "prNumber": 3192,
            "prUrl": "https://github.com/MoonLadderStudios/MoonMind/pull/3192",
        },
    )

    assert result.pr_number == 3192
    assert result.pr_url == "https://github.com/MoonLadderStudios/MoonMind/pull/3192"
    assert result.owned_by_merge_automation_gate is False


def _workflow_payload(**policy: Any) -> dict[str, Any]:
    return {
        "workflowType": "MoonMind.PRResolver",
        "parentWorkflowId": "parent",
        "parentRunId": "run",
        "principal": "user-1",
        "repository": "MoonLadderStudios/MoonMind",
        "prNumber": 3150,
        "prUrl": "https://github.com/MoonLadderStudios/MoonMind/pull/3150",
        "mergeMethod": "squash",
        "headSha": "abc",
        "stepId": "resolve",
        "correlationId": "corr-1",
        "baseAgentRequest": _agent_request().model_dump(by_alias=True, mode="json"),
        "policy": {"pollIntervalSeconds": 1, **policy},
    }


@pytest.mark.asyncio
async def test_workflow_verifies_already_merged_before_terminal_publication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def execute_activity(name: str, payload: dict[str, Any], **kwargs: Any) -> Any:
        calls.append(name)
        if name == "pr_resolver.read_snapshot":
            return {
                "headSha": "abc",
                "pullRequestOpen": False,
                "pullRequestMerged": True,
                "blockers": [],
            }
        if name == "pr_resolver.classify_gate":
            return classify_pr_resolver_snapshot(payload["snapshot"])
        if name == "pr_resolver.verify_merged":
            return {"merged": True, "headSha": "abc", "mergeSha": "merge-1"}
        if name == "pr_resolver.write_terminal_result":
            assert payload["terminalResult"]["status"] == "already_merged"
            return {"resultRef": "result-ref", "publishEvidenceRef": "publish-ref"}
        raise AssertionError(name)

    monkeypatch.setattr(resolver_module.workflow, "execute_activity", execute_activity)
    monkeypatch.setattr(
        resolver_module.workflow,
        "info",
        lambda: SimpleNamespace(
            workflow_id="resolver-1", run_id="run-1", namespace="default"
        ),
    )
    monkeypatch.setattr(
        resolver_module.workflow, "now", lambda: datetime.now(timezone.utc)
    )

    result = await MoonMindPRResolverWorkflow().run(_workflow_payload())

    assert calls == [
        "pr_resolver.read_snapshot",
        "pr_resolver.classify_gate",
        "pr_resolver.verify_merged",
        "pr_resolver.write_terminal_result",
    ]
    assert result["metadata"]["mergeAutomationDisposition"] == "already_merged"
    assert result["publishEvidence"] == "publish-ref"
    assert result["outputRefs"]["publishEvidence"] == "publish-ref"


@pytest.mark.asyncio
async def test_repeated_blocker_without_remote_progress_stops_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    child_calls = 0

    async def execute_activity(name: str, payload: dict[str, Any], **kwargs: Any) -> Any:
        if name in {"pr_resolver.read_snapshot", "pr_resolver.verify_remote_head"}:
            return {
                "headSha": "abc",
                "checksComplete": True,
                "checksPassing": False,
                "blockers": [{"kind": "checks_failed", "summary": "Tests failed"}],
            }
        if name == "pr_resolver.classify_gate":
            return classify_pr_resolver_snapshot(payload["snapshot"])
        if name == "pr_resolver.write_terminal_result":
            assert payload["terminalResult"]["reasonCode"] == (
                "repeated_blocker_without_progress"
            )
            return {"resultRef": "result-ref", "publishEvidenceRef": "publish-ref"}
        raise AssertionError(name)

    async def execute_child(*args: Any, **kwargs: Any) -> dict[str, Any]:
        nonlocal child_calls
        child_calls += 1
        return {"outputRefs": ["child-evidence"], "metadata": {}}

    monkeypatch.setattr(resolver_module.workflow, "execute_activity", execute_activity)
    monkeypatch.setattr(resolver_module.workflow, "execute_child_workflow", execute_child)
    monkeypatch.setattr(
        resolver_module.workflow,
        "info",
        lambda: SimpleNamespace(
            workflow_id="resolver-1", run_id="run-1", namespace="default"
        ),
    )
    monkeypatch.setattr(
        resolver_module.workflow, "now", lambda: datetime.now(timezone.utc)
    )

    result = await MoonMindPRResolverWorkflow().run(
        _workflow_payload(
            maxRemediationsPerType=5,
            maxIdenticalBlockersWithoutProgress=1,
        )
    )

    assert child_calls == 1
    assert result["metadata"]["mergeAutomationDisposition"] == "manual_review"
    assert result["failureClass"] == "execution_error"


@pytest.mark.asyncio
async def test_cancellation_prevents_new_mutations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def execute_activity(name: str, payload: dict[str, Any], **kwargs: Any) -> Any:
        calls.append(name)
        raise CancelledError("resolver canceled")

    async def execute_child(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise AssertionError("cancellation must not dispatch remediation")

    monkeypatch.setattr(resolver_module.workflow, "execute_activity", execute_activity)
    monkeypatch.setattr(resolver_module.workflow, "execute_child_workflow", execute_child)
    monkeypatch.setattr(
        resolver_module.workflow,
        "info",
        lambda: SimpleNamespace(
            workflow_id="resolver-1", run_id="run-1", namespace="default"
        ),
    )
    monkeypatch.setattr(
        resolver_module.workflow, "now", lambda: datetime.now(timezone.utc)
    )

    resolver = MoonMindPRResolverWorkflow()
    with pytest.raises(CancelledError):
        await resolver.run(_workflow_payload())

    assert calls == ["pr_resolver.read_snapshot"]
    assert resolver.state()["state"] == "canceled"


@pytest.mark.asyncio
async def test_hard_activity_failure_publishes_failed_terminal_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def execute_activity(name: str, payload: dict[str, Any], **kwargs: Any) -> Any:
        calls.append(name)
        if name == "pr_resolver.read_snapshot":
            raise RuntimeError("provider failed")
        if name == "pr_resolver.write_terminal_result":
            assert payload["terminalResult"]["reasonCode"] == "hard_execution_failure"
            return {"resultRef": "failed-result", "publishEvidenceRef": "failed-publish"}
        raise AssertionError(name)

    monkeypatch.setattr(resolver_module.workflow, "execute_activity", execute_activity)
    monkeypatch.setattr(
        resolver_module.workflow,
        "info",
        lambda: SimpleNamespace(
            workflow_id="resolver-1", run_id="run-1", namespace="default"
        ),
    )
    monkeypatch.setattr(
        resolver_module.workflow, "now", lambda: datetime.now(timezone.utc)
    )

    result = await MoonMindPRResolverWorkflow().run(_workflow_payload())

    assert calls == [
        "pr_resolver.read_snapshot",
        "pr_resolver.write_terminal_result",
    ]
    assert result["failureClass"] == "execution_error"
    assert result["metadata"]["mergeAutomationDisposition"] == "failed"
