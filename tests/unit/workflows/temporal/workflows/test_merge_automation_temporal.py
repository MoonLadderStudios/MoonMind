from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest
from temporalio.exceptions import CancelledError
from temporalio.workflow import ChildWorkflowCancellationType

from moonmind.workflows.temporal.workflows import merge_automation as merge_automation_module
from moonmind.workflows.temporal.workflows.merge_automation import (
    MoonMindMergeAutomationWorkflow,
)
from moonmind.workflows.temporal.workflows.merge_gate import (
    deterministic_resolver_idempotency_key,
    legacy_resolver_idempotency_key,
)


def _payload() -> dict[str, Any]:
    return {
        "workflowType": "MoonMind.MergeAutomation",
        "parentWorkflowId": "wf-parent",
        "parentRunId": "run-parent",
        "publishContextRef": "artifact://publish-context",
        "pullRequest": {
            "repo": "MoonLadderStudios/MoonMind",
            "number": 350,
            "url": "https://github.com/MoonLadderStudios/MoonMind/pull/350",
            "headSha": "abc123",
            "headBranch": "feature",
            "baseBranch": "main",
        },
        "jiraIssueKey": "MM-350",
        "mergeAutomationConfig": {
            "gate": {
                "github": {
                    "checks": "required",
                    "automatedReview": "required",
                },
                "jira": {"status": "optional"},
            },
            "resolver": {"mergeMethod": "squash"},
            "timeouts": {"fallbackPollSeconds": 300},
        },
        "idempotencyKey": "merge-automation:wf-parent:MoonLadderStudios/MoonMind:350:abc123",
    }


def _payload_with_post_merge_jira(**post_merge_overrides: Any) -> dict[str, Any]:
    payload = _payload()
    payload["mergeAutomationConfig"]["postMergeJira"] = {
        "enabled": True,
        "required": True,
        "strategy": "done_category",
        **post_merge_overrides,
    }
    return payload


@pytest.fixture(autouse=True)
def _default_temporal_patch_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        merge_automation_module.workflow,
        "patched",
        lambda _patch_id: True,
    )


def test_merge_automation_extracts_artifact_id_from_ref_shapes() -> None:
    assert (
        MoonMindMergeAutomationWorkflow._artifact_id_from_ref(
            {"artifact_id": "art-snake"}
        )
        == "art-snake"
    )
    assert (
        MoonMindMergeAutomationWorkflow._artifact_id_from_ref(
            {"artifactId": "art-camel"}
        )
        == "art-camel"
    )
    assert (
        MoonMindMergeAutomationWorkflow._artifact_id_from_ref(
            SimpleNamespace(artifact_id="art-attr-snake")
        )
        == "art-attr-snake"
    )
    assert (
        MoonMindMergeAutomationWorkflow._artifact_id_from_ref(
            SimpleNamespace(artifactId="art-attr-camel")
        )
        == "art-attr-camel"
    )


def test_merge_automation_summary_payload_bounds_published_artifact_refs() -> None:
    workflow = MoonMindMergeAutomationWorkflow()
    max_refs = merge_automation_module.MAX_PUBLISHED_ARTIFACT_REFS
    workflow._gate_snapshot_artifact_refs = [
        f"gate-{index}" for index in range(max_refs + 5)
    ]
    workflow._resolver_attempt_artifact_refs = [
        f"attempt-{index}" for index in range(max_refs + 3)
    ]

    payload = workflow.summary()

    assert payload["artifactRefs"]["gateSnapshots"] == [
        f"gate-{index}" for index in range(5, max_refs + 5)
    ]
    assert payload["artifactRefs"]["resolverAttempts"] == [
        f"attempt-{index}" for index in range(3, max_refs + 3)
    ]
    assert len(workflow._gate_snapshot_artifact_refs) == max_refs + 5
    assert len(workflow._resolver_attempt_artifact_refs) == max_refs + 3


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_at", ["create", "write_complete"])
async def test_write_json_artifact_preserves_cancellation(
    monkeypatch: pytest.MonkeyPatch,
    cancel_at: str,
) -> None:
    workflow = MoonMindMergeAutomationWorkflow()

    async def fake_execute_activity(
        activity_type: str,
        _payload: dict[str, Any],
        **_kwargs: Any,
    ) -> tuple[dict[str, str], dict[str, str]]:
        assert activity_type == "artifact.create"
        if cancel_at == "create":
            raise CancelledError("artifact create canceled")
        return ({"artifact_id": "art-cancel"}, {"upload_url": "memory://upload"})

    async def fake_execute_typed_activity(
        activity_type: str,
        _payload: Any,
        **_kwargs: Any,
    ) -> dict[str, str]:
        assert activity_type == "artifact.write_complete"
        raise CancelledError("artifact write canceled")

    monkeypatch.setattr(
        merge_automation_module.workflow,
        "execute_activity",
        fake_execute_activity,
    )
    monkeypatch.setattr(
        merge_automation_module,
        "execute_typed_activity",
        fake_execute_typed_activity,
    )

    with pytest.raises(CancelledError):
        await workflow._write_json_artifact(name="artifact.json", payload={})


@pytest.mark.asyncio
async def test_merge_automation_reenters_gate_after_resolver_remediation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = MoonMindMergeAutomationWorkflow()
    readiness_calls = 0
    child_results = [
        {
            "status": "success",
            "mergeAutomationDisposition": "reenter_gate",
            "headSha": "def456",
        },
        {
            "status": "success",
            "mergeAutomationDisposition": "merged",
        },
    ]
    child_workflow_ids: list[str] = []

    async def fake_execute_activity(
        activity_type: str,
        _payload: dict[str, Any],
        **_kwargs: Any,
    ) -> dict[str, Any]:
        assert activity_type != "merge_automation.create_resolver_run"
        nonlocal readiness_calls
        assert activity_type == "merge_automation.evaluate_readiness"
        readiness_calls += 1
        head_sha = "abc123" if readiness_calls == 1 else "def456"
        return {
            "headSha": head_sha,
            "ready": True,
            "pullRequestOpen": True,
            "policyAllowed": True,
            "checksComplete": True,
            "checksPassing": True,
            "automatedReviewComplete": True,
            "jiraStatusAllowed": True,
        }

    child_payloads: list[dict[str, Any]] = []

    async def fake_execute_child_workflow(
        workflow_type: str,
        payload: dict[str, Any],
        **kwargs: Any,
    ) -> dict[str, Any]:
        assert workflow_type == "MoonMind.Run"
        child_payloads.append(payload)
        child_workflow_ids.append(str(kwargs["id"]))
        return child_results.pop(0)

    monkeypatch.setattr(
        merge_automation_module.workflow,
        "execute_activity",
        fake_execute_activity,
    )
    monkeypatch.setattr(
        merge_automation_module.workflow,
        "execute_child_workflow",
        fake_execute_child_workflow,
    )
    monkeypatch.setattr(
        merge_automation_module.workflow,
        "now",
        lambda: datetime.now(timezone.utc),
    )
    monkeypatch.setattr(
        merge_automation_module.workflow,
        "upsert_memo",
        lambda _memo: None,
    )
    monkeypatch.setattr(
        merge_automation_module.workflow,
        "upsert_search_attributes",
        lambda _attrs: None,
    )

    result = await workflow.run(_payload())

    assert readiness_calls == 2
    assert result["status"] == "merged"
    assert result["cycles"] == 2
    first_resolver_id = deterministic_resolver_idempotency_key(
        parent_workflow_id="wf-parent",
        repo="MoonLadderStudios/MoonMind",
        pr_number=350,
        head_sha="abc123",
    )
    second_resolver_id = deterministic_resolver_idempotency_key(
        parent_workflow_id="wf-parent",
        repo="MoonLadderStudios/MoonMind",
        pr_number=350,
        head_sha="def456",
    )
    assert child_workflow_ids == [
        f"{first_resolver_id}:1",
        f"{second_resolver_id}:2",
    ]
    assert child_payloads[0]["workflow_type"] == "MoonMind.Run"
    assert child_payloads[0]["initial_parameters"]["publishMode"] == "none"
    assert child_payloads[0]["initial_parameters"]["task"]["publish"]["mode"] == "none"
    assert child_payloads[0]["initial_parameters"]["task"]["skill"]["id"] == "pr-resolver"
    assert child_payloads[0]["initial_parameters"]["task"]["tool"] == {
        "type": "skill",
        "name": "pr-resolver",
        "version": "1.0",
    }


@pytest.mark.asyncio
async def test_merge_automation_tracks_current_head_when_checks_are_still_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = MoonMindMergeAutomationWorkflow()
    readiness_calls = 0
    child_workflow_ids: list[str] = []
    child_payloads: list[dict[str, Any]] = []
    wait_calls = 0

    async def fake_execute_activity(
        activity_type: str,
        _payload: dict[str, Any],
        **_kwargs: Any,
    ) -> dict[str, Any]:
        nonlocal readiness_calls
        if activity_type != "merge_automation.evaluate_readiness":
            raise RuntimeError(activity_type)
        readiness_calls += 1
        if readiness_calls == 1:
            return {
                "headSha": "def456",
                "ready": False,
                "pullRequestOpen": True,
                "policyAllowed": True,
                "checksComplete": False,
                "checksPassing": False,
                "automatedReviewComplete": True,
                "jiraStatusAllowed": True,
                "blockers": [
                    {
                        "kind": "checks_running",
                        "summary": "Required checks are still running.",
                        "retryable": True,
                        "source": "github",
                    }
                ],
            }
        return {
            "headSha": "def456",
            "ready": True,
            "pullRequestOpen": True,
            "policyAllowed": True,
            "checksComplete": True,
            "checksPassing": True,
            "automatedReviewComplete": True,
            "jiraStatusAllowed": True,
        }

    async def fake_execute_child_workflow(
        workflow_type: str,
        payload: dict[str, Any],
        **kwargs: Any,
    ) -> dict[str, Any]:
        assert workflow_type == "MoonMind.Run"
        child_payloads.append(payload)
        child_workflow_ids.append(str(kwargs["id"]))
        return {"status": "success", "mergeAutomationDisposition": "merged"}

    async def fake_wait_condition(*_args: Any, **_kwargs: Any) -> None:
        nonlocal wait_calls
        wait_calls += 1

    monkeypatch.setattr(
        merge_automation_module.workflow,
        "execute_activity",
        fake_execute_activity,
    )
    monkeypatch.setattr(
        merge_automation_module.workflow,
        "execute_child_workflow",
        fake_execute_child_workflow,
    )
    monkeypatch.setattr(
        merge_automation_module.workflow,
        "wait_condition",
        fake_wait_condition,
    )
    monkeypatch.setattr(
        merge_automation_module.workflow,
        "now",
        lambda: datetime.now(timezone.utc),
    )
    monkeypatch.setattr(
        merge_automation_module.workflow,
        "upsert_memo",
        lambda _memo: None,
    )
    monkeypatch.setattr(
        merge_automation_module.workflow,
        "upsert_search_attributes",
        lambda _attrs: None,
    )

    result = await workflow.run(_payload())

    expected_resolver_id = deterministic_resolver_idempotency_key(
        parent_workflow_id="wf-parent",
        repo="MoonLadderStudios/MoonMind",
        pr_number=350,
        head_sha="def456",
    )
    assert readiness_calls == 2
    assert wait_calls == 1
    assert result["status"] == "merged"
    assert result["latestHeadSha"] == "def456"
    assert result["blockers"] == []
    assert child_workflow_ids == [f"{expected_resolver_id}:1"]
    assert child_payloads[0]["initial_parameters"]["mergeGate"]["headSha"] == "def456"


@pytest.mark.asyncio
async def test_merge_automation_resolver_child_uses_try_cancel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = MoonMindMergeAutomationWorkflow()
    child_kwargs: dict[str, Any] = {}

    async def fake_execute_activity(
        activity_type: str,
        _payload: dict[str, Any],
        **_kwargs: Any,
    ) -> dict[str, Any]:
        assert activity_type == "merge_automation.evaluate_readiness"
        return {
            "headSha": "abc123",
            "ready": True,
            "pullRequestOpen": True,
            "policyAllowed": True,
            "checksComplete": True,
            "checksPassing": True,
            "automatedReviewComplete": True,
            "jiraStatusAllowed": True,
        }

    async def fake_execute_child_workflow(
        workflow_type: str,
        _payload: dict[str, Any],
        **kwargs: Any,
    ) -> dict[str, Any]:
        assert workflow_type == "MoonMind.Run"
        child_kwargs.update(kwargs)
        return {"status": "success", "mergeAutomationDisposition": "merged"}

    monkeypatch.setattr(
        merge_automation_module.workflow,
        "execute_activity",
        fake_execute_activity,
    )
    monkeypatch.setattr(
        merge_automation_module.workflow,
        "execute_child_workflow",
        fake_execute_child_workflow,
    )
    monkeypatch.setattr(merge_automation_module.workflow, "now", lambda: datetime.now(timezone.utc))
    monkeypatch.setattr(merge_automation_module.workflow, "upsert_memo", lambda _memo: None)
    monkeypatch.setattr(
        merge_automation_module.workflow,
        "upsert_search_attributes",
        lambda _attrs: None,
    )

    result = await workflow.run(_payload())

    assert result["status"] == "merged"
    assert child_kwargs["cancellation_type"] == ChildWorkflowCancellationType.TRY_CANCEL
    search_attributes = child_kwargs["search_attributes"]
    assert search_attributes["mm_owner_type"] == ["user"]
    assert search_attributes["mm_owner_id"] == ["wf-parent"]
    assert search_attributes["mm_repo"] == ["MoonLadderStudios/MoonMind"]


@pytest.mark.asyncio
async def test_merge_automation_resolver_child_uses_legacy_id_before_patch_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = MoonMindMergeAutomationWorkflow()
    child_workflow_ids: list[str] = []

    async def fake_execute_activity(
        activity_type: str,
        _payload: dict[str, Any],
        **_kwargs: Any,
    ) -> dict[str, Any]:
        assert activity_type == "merge_automation.evaluate_readiness"
        return {
            "headSha": "abc123",
            "ready": True,
            "pullRequestOpen": True,
            "policyAllowed": True,
            "checksComplete": True,
            "checksPassing": True,
            "automatedReviewComplete": True,
            "jiraStatusAllowed": True,
        }

    async def fake_execute_child_workflow(
        workflow_type: str,
        _payload: dict[str, Any],
        **kwargs: Any,
    ) -> dict[str, Any]:
        assert workflow_type == "MoonMind.Run"
        child_workflow_ids.append(str(kwargs["id"]))
        return {"status": "success", "mergeAutomationDisposition": "merged"}

    monkeypatch.setattr(
        merge_automation_module.workflow,
        "patched",
        lambda _patch_id: False,
    )
    monkeypatch.setattr(
        merge_automation_module.workflow,
        "execute_activity",
        fake_execute_activity,
    )
    monkeypatch.setattr(
        merge_automation_module.workflow,
        "execute_child_workflow",
        fake_execute_child_workflow,
    )
    monkeypatch.setattr(
        merge_automation_module.workflow,
        "now",
        lambda: datetime.now(timezone.utc),
    )
    monkeypatch.setattr(
        merge_automation_module.workflow,
        "upsert_memo",
        lambda _memo: None,
    )
    monkeypatch.setattr(
        merge_automation_module.workflow,
        "upsert_search_attributes",
        lambda _attrs: None,
    )

    result = await workflow.run(_payload())

    assert result["status"] == "merged"
    legacy_resolver_id = legacy_resolver_idempotency_key(
        parent_workflow_id="wf-parent",
        repo="MoonLadderStudios/MoonMind",
        pr_number=350,
        head_sha="abc123",
    )
    assert child_workflow_ids == [f"{legacy_resolver_id}:1"]


@pytest.mark.asyncio
async def test_merge_automation_launches_resolver_when_checks_are_failing_but_complete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = MoonMindMergeAutomationWorkflow()
    child_calls = 0

    async def fake_execute_activity(
        activity_type: str,
        _payload: dict[str, Any],
        **_kwargs: Any,
    ) -> dict[str, Any]:
        assert activity_type == "merge_automation.evaluate_readiness"
        return {
            "headSha": "abc123",
            "ready": False,
            "pullRequestOpen": True,
            "policyAllowed": True,
            "checksComplete": True,
            "checksPassing": False,
            "automatedReviewComplete": True,
            "jiraStatusAllowed": True,
            "blockers": [
                {
                    "kind": "checks_failed",
                    "summary": "Required checks are failing.",
                    "retryable": True,
                    "source": "github",
                }
            ],
        }

    async def fake_execute_child_workflow(
        workflow_type: str,
        _payload: dict[str, Any],
        **_kwargs: Any,
    ) -> dict[str, Any]:
        nonlocal child_calls
        assert workflow_type == "MoonMind.Run"
        child_calls += 1
        return {"status": "success", "mergeAutomationDisposition": "merged"}

    monkeypatch.setattr(
        merge_automation_module.workflow,
        "execute_activity",
        fake_execute_activity,
    )
    monkeypatch.setattr(
        merge_automation_module.workflow,
        "execute_child_workflow",
        fake_execute_child_workflow,
    )
    monkeypatch.setattr(
        merge_automation_module.workflow,
        "now",
        lambda: datetime.now(timezone.utc),
    )
    monkeypatch.setattr(merge_automation_module.workflow, "upsert_memo", lambda _memo: None)
    monkeypatch.setattr(
        merge_automation_module.workflow,
        "upsert_search_attributes",
        lambda _attrs: None,
    )

    result = await workflow.run(_payload())

    assert child_calls == 1
    assert result["status"] == "merged"
    assert result["blockers"] == []


@pytest.mark.asyncio
async def test_merge_automation_finishes_already_merged_without_resolver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = MoonMindMergeAutomationWorkflow()
    readiness_calls = 0

    async def fake_execute_activity(
        activity_type: str,
        _payload: dict[str, Any],
        **_kwargs: Any,
    ) -> dict[str, Any]:
        nonlocal readiness_calls
        assert activity_type == "merge_automation.evaluate_readiness"
        readiness_calls += 1
        return {
            "headSha": "def456",
            "ready": False,
            "pullRequestOpen": False,
            "pullRequestMerged": True,
            "policyAllowed": True,
            "checksComplete": True,
            "checksPassing": True,
            "automatedReviewComplete": True,
            "jiraStatusAllowed": True,
        }

    async def fake_execute_child_workflow(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise AssertionError("already-merged PRs must not launch pr-resolver")

    monkeypatch.setattr(
        merge_automation_module.workflow,
        "execute_activity",
        fake_execute_activity,
    )
    monkeypatch.setattr(
        merge_automation_module.workflow,
        "execute_child_workflow",
        fake_execute_child_workflow,
    )
    monkeypatch.setattr(merge_automation_module.workflow, "now", lambda: datetime.now(timezone.utc))
    monkeypatch.setattr(merge_automation_module.workflow, "upsert_memo", lambda _memo: None)
    monkeypatch.setattr(
        merge_automation_module.workflow,
        "upsert_search_attributes",
        lambda _attrs: None,
    )

    result = await workflow.run(_payload())

    assert readiness_calls == 1
    assert result["status"] == "already_merged"
    assert result["latestHeadSha"] == "def456"
    assert result["blockers"] == []
    assert result["resolverChildWorkflowIds"] == []


@pytest.mark.asyncio
async def test_merge_automation_runs_post_merge_jira_before_merged_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = MoonMindMergeAutomationWorkflow()
    activity_calls: list[str] = []

    async def fake_execute_activity(
        activity_type: str,
        payload: dict[str, Any],
        **_kwargs: Any,
    ) -> dict[str, Any]:
        activity_calls.append(activity_type)
        if activity_type == "merge_automation.evaluate_readiness":
            return {
                "headSha": "abc123",
                "ready": True,
                "pullRequestOpen": True,
                "policyAllowed": True,
                "checksComplete": True,
                "checksPassing": True,
                "automatedReviewComplete": True,
                "jiraStatusAllowed": True,
            }
        if activity_type == "merge_automation.complete_post_merge_jira":
            assert payload["resolverDisposition"] == "merged"
            assert payload["jiraIssueKey"] == "MM-350"
            return {
                "status": "succeeded",
                "required": True,
                "issueKey": "MM-350",
                "issueKeySource": "merge_automation",
                "transitionId": "41",
                "transitionName": "Done",
                "alreadyDone": False,
                "transitioned": True,
            }
        raise AssertionError(activity_type)

    async def fake_execute_child_workflow(
        _workflow_type: str,
        _payload: dict[str, Any],
        **_kwargs: Any,
    ) -> dict[str, Any]:
        assert "merge_automation.complete_post_merge_jira" not in activity_calls
        return {"status": "success", "mergeAutomationDisposition": "merged"}

    monkeypatch.setattr(merge_automation_module.workflow, "execute_activity", fake_execute_activity)
    monkeypatch.setattr(merge_automation_module.workflow, "execute_child_workflow", fake_execute_child_workflow)
    monkeypatch.setattr(merge_automation_module.workflow, "now", lambda: datetime.now(timezone.utc))
    monkeypatch.setattr(merge_automation_module.workflow, "upsert_memo", lambda _memo: None)
    monkeypatch.setattr(merge_automation_module.workflow, "upsert_search_attributes", lambda _attrs: None)

    result = await workflow.run(_payload_with_post_merge_jira())

    assert result["status"] == "merged"
    assert activity_calls.index("merge_automation.evaluate_readiness") < activity_calls.index(
        "merge_automation.complete_post_merge_jira"
    )
    assert result["postMergeJira"]["status"] == "succeeded"


@pytest.mark.asyncio
async def test_merge_automation_blocks_when_required_post_merge_jira_blocks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = MoonMindMergeAutomationWorkflow()

    async def fake_execute_activity(
        activity_type: str,
        _payload: dict[str, Any],
        **_kwargs: Any,
    ) -> dict[str, Any]:
        if activity_type == "merge_automation.evaluate_readiness":
            return {
                "headSha": "abc123",
                "ready": True,
                "pullRequestOpen": True,
                "policyAllowed": True,
                "checksComplete": True,
                "checksPassing": True,
                "automatedReviewComplete": True,
                "jiraStatusAllowed": True,
            }
        if activity_type == "merge_automation.complete_post_merge_jira":
            return {
                "status": "blocked",
                "required": True,
                "reason": "Multiple done transitions are available.",
                "issueKey": "MM-350",
                "transitioned": False,
            }
        raise AssertionError(activity_type)

    async def fake_execute_child_workflow(
        _workflow_type: str,
        _payload: dict[str, Any],
        **_kwargs: Any,
    ) -> dict[str, Any]:
        return {"status": "success", "mergeAutomationDisposition": "already_merged"}

    monkeypatch.setattr(merge_automation_module.workflow, "execute_activity", fake_execute_activity)
    monkeypatch.setattr(merge_automation_module.workflow, "execute_child_workflow", fake_execute_child_workflow)
    monkeypatch.setattr(merge_automation_module.workflow, "now", lambda: datetime.now(timezone.utc))
    monkeypatch.setattr(merge_automation_module.workflow, "upsert_memo", lambda _memo: None)
    monkeypatch.setattr(merge_automation_module.workflow, "upsert_search_attributes", lambda _attrs: None)

    result = await workflow.run(_payload_with_post_merge_jira())

    assert result["status"] == "failed"
    assert result["postMergeJira"]["status"] == "blocked"
    assert result["blockers"][0]["source"] == "jira"


@pytest.mark.asyncio
@pytest.mark.parametrize("disposition", ["manual_review", "failed"])
async def test_merge_automation_does_not_run_post_merge_jira_for_non_success_dispositions(
    monkeypatch: pytest.MonkeyPatch,
    disposition: str,
) -> None:
    workflow = MoonMindMergeAutomationWorkflow()

    async def fake_execute_activity(
        activity_type: str,
        _payload: dict[str, Any],
        **_kwargs: Any,
    ) -> dict[str, Any]:
        assert activity_type == "merge_automation.evaluate_readiness"
        return {
            "headSha": "abc123",
            "ready": True,
            "pullRequestOpen": True,
            "policyAllowed": True,
            "checksComplete": True,
            "checksPassing": True,
            "automatedReviewComplete": True,
            "jiraStatusAllowed": True,
        }

    child_calls = 0

    async def fake_execute_child_workflow(
        _workflow_type: str,
        _payload: dict[str, Any],
        **_kwargs: Any,
    ) -> dict[str, Any]:
        nonlocal child_calls
        child_calls += 1
        return {"status": "success", "mergeAutomationDisposition": disposition}

    monkeypatch.setattr(merge_automation_module.workflow, "execute_activity", fake_execute_activity)
    monkeypatch.setattr(merge_automation_module.workflow, "execute_child_workflow", fake_execute_child_workflow)
    monkeypatch.setattr(merge_automation_module.workflow, "now", lambda: datetime.now(timezone.utc))
    monkeypatch.setattr(merge_automation_module.workflow, "upsert_memo", lambda _memo: None)
    monkeypatch.setattr(merge_automation_module.workflow, "upsert_search_attributes", lambda _attrs: None)

    result = await workflow.run(_payload_with_post_merge_jira())
    assert child_calls == 1
    assert result["status"] == "failed"
    assert "postMergeJira" not in result


@pytest.mark.asyncio
async def test_merge_automation_cancellation_while_resolver_active_sets_canceled_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = MoonMindMergeAutomationWorkflow()

    async def fake_execute_activity(
        activity_type: str,
        _payload: dict[str, Any],
        **_kwargs: Any,
    ) -> dict[str, Any]:
        assert activity_type == "merge_automation.evaluate_readiness"
        return {
            "headSha": "abc123",
            "ready": True,
            "pullRequestOpen": True,
            "policyAllowed": True,
            "checksComplete": True,
            "checksPassing": True,
            "automatedReviewComplete": True,
            "jiraStatusAllowed": True,
        }

    async def fake_execute_child_workflow(
        workflow_type: str,
        _payload: dict[str, Any],
        **kwargs: Any,
    ) -> dict[str, Any]:
        assert workflow_type == "MoonMind.Run"
        assert kwargs["cancellation_type"] == ChildWorkflowCancellationType.TRY_CANCEL
        raise CancelledError("resolver child canceled")

    monkeypatch.setattr(
        merge_automation_module.workflow,
        "execute_activity",
        fake_execute_activity,
    )
    monkeypatch.setattr(
        merge_automation_module.workflow,
        "execute_child_workflow",
        fake_execute_child_workflow,
    )
    monkeypatch.setattr(merge_automation_module.workflow, "now", lambda: datetime.now(timezone.utc))
    monkeypatch.setattr(merge_automation_module.workflow, "upsert_memo", lambda _memo: None)
    monkeypatch.setattr(
        merge_automation_module.workflow,
        "upsert_search_attributes",
        lambda _attrs: None,
    )

    result = await workflow.run(_payload())

    assert workflow.summary()["status"] == "canceled"
    assert (
        workflow.summary()["summary"]
        == "Merge automation canceled while resolver child was active."
    )
    assert result["status"] == "canceled"
    assert (
        result["summary"]
        == "Merge automation canceled while resolver child was active."
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("disposition", "expected_status"),
    [
        ("merged", "merged"),
        ("already_merged", "already_merged"),
    ],
)
async def test_merge_automation_success_dispositions_complete_successfully(
    monkeypatch: pytest.MonkeyPatch,
    disposition: str,
    expected_status: str,
) -> None:
    workflow = MoonMindMergeAutomationWorkflow()

    async def fake_execute_activity(
        activity_type: str,
        _payload: dict[str, Any],
        **_kwargs: Any,
    ) -> dict[str, Any]:
        assert activity_type == "merge_automation.evaluate_readiness"
        return {
            "headSha": "abc123",
            "ready": True,
            "pullRequestOpen": True,
            "policyAllowed": True,
            "checksComplete": True,
            "checksPassing": True,
            "automatedReviewComplete": True,
            "jiraStatusAllowed": True,
        }

    async def fake_execute_child_workflow(
        workflow_type: str,
        _payload: dict[str, Any],
        **_kwargs: Any,
    ) -> dict[str, Any]:
        assert workflow_type == "MoonMind.Run"
        return {"status": "success", "mergeAutomationDisposition": disposition}

    monkeypatch.setattr(
        merge_automation_module.workflow,
        "execute_activity",
        fake_execute_activity,
    )
    monkeypatch.setattr(
        merge_automation_module.workflow,
        "execute_child_workflow",
        fake_execute_child_workflow,
    )
    monkeypatch.setattr(merge_automation_module.workflow, "now", lambda: datetime.now(timezone.utc))
    monkeypatch.setattr(merge_automation_module.workflow, "upsert_memo", lambda _memo: None)
    monkeypatch.setattr(
        merge_automation_module.workflow,
        "upsert_search_attributes",
        lambda _attrs: None,
    )

    result = await workflow.run(_payload())

    assert result["status"] == expected_status
    assert result["blockers"] == []


@pytest.mark.asyncio
async def test_merge_automation_writes_visibility_artifact_refs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = MoonMindMergeAutomationWorkflow()
    created_names: list[str] = []
    written_artifact_ids: list[str] = []

    async def fake_execute_activity(
        activity_type: str,
        payload: dict[str, Any],
        **_kwargs: Any,
    ) -> Any:
        if activity_type == "artifact.create":
            created_names.append(str(payload["name"]))
            artifact_id = f"artifact-{len(created_names)}"
            return ({"artifact_id": artifact_id}, {"upload_url": "memory://upload"})
        assert activity_type == "merge_automation.evaluate_readiness"
        return {
            "headSha": "abc123",
            "ready": True,
            "pullRequestOpen": True,
            "policyAllowed": True,
            "checksComplete": True,
            "checksPassing": True,
            "automatedReviewComplete": True,
            "jiraStatusAllowed": True,
        }

    async def fake_execute_child_workflow(
        workflow_type: str,
        _payload: dict[str, Any],
        **_kwargs: Any,
    ) -> dict[str, Any]:
        assert workflow_type == "MoonMind.Run"
        return {"status": "success", "mergeAutomationDisposition": "merged"}

    async def fake_execute_typed_activity(
        activity_type: str,
        payload: Any,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        assert activity_type == "artifact.write_complete"
        written_artifact_ids.append(payload.artifact_id)
        return {"artifact_id": payload.artifact_id}

    monkeypatch.setattr(
        merge_automation_module.workflow,
        "execute_activity",
        fake_execute_activity,
    )
    monkeypatch.setattr(
        merge_automation_module.workflow,
        "execute_child_workflow",
        fake_execute_child_workflow,
    )
    monkeypatch.setattr(
        merge_automation_module,
        "execute_typed_activity",
        fake_execute_typed_activity,
    )
    monkeypatch.setattr(merge_automation_module.workflow, "now", lambda: datetime.now(timezone.utc))
    monkeypatch.setattr(merge_automation_module.workflow, "upsert_memo", lambda _memo: None)
    monkeypatch.setattr(
        merge_automation_module.workflow,
        "upsert_search_attributes",
        lambda _attrs: None,
    )

    result = await workflow.run(_payload())

    assert result["status"] == "merged"
    assert "reports/merge_automation_summary.json" in created_names
    assert "artifacts/merge_automation/gate_snapshots/0.json" in created_names
    assert "artifacts/merge_automation/resolver_attempts/1.json" in created_names
    assert result["artifactRefs"]["summary"] in written_artifact_ids
    assert result["artifactRefs"]["gateSnapshots"]
    assert result["artifactRefs"]["resolverAttempts"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("disposition", "expected_summary"),
    [
        ("manual_review", "pr-resolver requested manual review."),
        ("failed", "pr-resolver reported failure."),
    ],
)
async def test_merge_automation_non_success_dispositions_fail(
    monkeypatch: pytest.MonkeyPatch,
    disposition: str,
    expected_summary: str,
) -> None:
    workflow = MoonMindMergeAutomationWorkflow()

    async def fake_execute_activity(
        activity_type: str,
        _payload: dict[str, Any],
        **_kwargs: Any,
    ) -> dict[str, Any]:
        assert activity_type == "merge_automation.evaluate_readiness"
        return {
            "headSha": "abc123",
            "ready": True,
            "pullRequestOpen": True,
            "policyAllowed": True,
            "checksComplete": True,
            "checksPassing": True,
            "automatedReviewComplete": True,
            "jiraStatusAllowed": True,
        }

    async def fake_execute_child_workflow(
        workflow_type: str,
        _payload: dict[str, Any],
        **_kwargs: Any,
    ) -> dict[str, Any]:
        assert workflow_type == "MoonMind.Run"
        return {
            "status": "success",
            "mergeAutomationDisposition": disposition,
            "summary": "resolver details",
        }

    monkeypatch.setattr(
        merge_automation_module.workflow,
        "execute_activity",
        fake_execute_activity,
    )
    monkeypatch.setattr(
        merge_automation_module.workflow,
        "execute_child_workflow",
        fake_execute_child_workflow,
    )
    monkeypatch.setattr(merge_automation_module.workflow, "now", lambda: datetime.now(timezone.utc))
    monkeypatch.setattr(merge_automation_module.workflow, "upsert_memo", lambda _memo: None)
    monkeypatch.setattr(
        merge_automation_module.workflow,
        "upsert_search_attributes",
        lambda _attrs: None,
    )

    result = await workflow.run(_payload())

    assert result["status"] == "failed"
    assert result["summary"] == expected_summary
    assert result["blockers"]
    assert result["blockers"][0]["kind"] == disposition


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("resolver_result", "expected_summary"),
    [
        ({"status": "success"}, "pr-resolver child result missing mergeAutomationDisposition."),
        (
            {"status": "success", "mergeAutomationDisposition": "surprising"},
            "pr-resolver child result has unsupported mergeAutomationDisposition: surprising",
        ),
    ],
)
async def test_merge_automation_invalid_dispositions_fail_deterministically(
    monkeypatch: pytest.MonkeyPatch,
    resolver_result: dict[str, Any],
    expected_summary: str,
) -> None:
    workflow = MoonMindMergeAutomationWorkflow()

    async def fake_execute_activity(
        activity_type: str,
        _payload: dict[str, Any],
        **_kwargs: Any,
    ) -> dict[str, Any]:
        assert activity_type == "merge_automation.evaluate_readiness"
        return {
            "headSha": "abc123",
            "ready": True,
            "pullRequestOpen": True,
            "policyAllowed": True,
            "checksComplete": True,
            "checksPassing": True,
            "automatedReviewComplete": True,
            "jiraStatusAllowed": True,
        }

    async def fake_execute_child_workflow(
        workflow_type: str,
        _payload: dict[str, Any],
        **_kwargs: Any,
    ) -> dict[str, Any]:
        assert workflow_type == "MoonMind.Run"
        return resolver_result

    monkeypatch.setattr(
        merge_automation_module.workflow,
        "execute_activity",
        fake_execute_activity,
    )
    monkeypatch.setattr(
        merge_automation_module.workflow,
        "execute_child_workflow",
        fake_execute_child_workflow,
    )
    monkeypatch.setattr(merge_automation_module.workflow, "now", lambda: datetime.now(timezone.utc))
    monkeypatch.setattr(merge_automation_module.workflow, "upsert_memo", lambda _memo: None)
    monkeypatch.setattr(
        merge_automation_module.workflow,
        "upsert_search_attributes",
        lambda _attrs: None,
    )

    result = await workflow.run(_payload())

    assert result["status"] == "failed"
    assert result["summary"] == expected_summary
    assert result["blockers"]
    assert result["blockers"][0]["kind"] == "resolver_disposition_invalid"


@pytest.mark.asyncio
async def test_merge_automation_ignores_wait_condition_timeout_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = MoonMindMergeAutomationWorkflow()
    readiness_calls = 0

    async def fake_execute_activity(
        activity_type: str,
        _payload: dict[str, Any],
        **_kwargs: Any,
    ) -> dict[str, Any]:
        nonlocal readiness_calls
        assert activity_type == "merge_automation.evaluate_readiness"
        readiness_calls += 1
        if readiness_calls == 1:
            return {
                "headSha": "abc123",
                "ready": False,
                "pullRequestOpen": True,
                "policyAllowed": True,
                "checksComplete": False,
            }
        return {
            "headSha": "abc123",
            "ready": False,
            "pullRequestOpen": False,
            "policyAllowed": True,
        }

    async def fake_wait_condition(*_args: Any, **_kwargs: Any) -> None:
        raise TimeoutError

    monkeypatch.setattr(
        merge_automation_module.workflow,
        "execute_activity",
        fake_execute_activity,
    )
    monkeypatch.setattr(
        merge_automation_module.workflow,
        "wait_condition",
        fake_wait_condition,
    )
    monkeypatch.setattr(merge_automation_module.workflow, "now", lambda: datetime.now(timezone.utc))
    monkeypatch.setattr(merge_automation_module.workflow, "upsert_memo", lambda _memo: None)
    monkeypatch.setattr(
        merge_automation_module.workflow,
        "upsert_search_attributes",
        lambda _attrs: None,
    )

    result = await workflow.run(_payload())

    assert readiness_calls == 2
    assert result["status"] == "blocked"


@pytest.mark.asyncio
async def test_merge_automation_propagates_unexpected_wait_condition_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = MoonMindMergeAutomationWorkflow()

    async def fake_execute_activity(
        activity_type: str,
        _payload: dict[str, Any],
        **_kwargs: Any,
    ) -> dict[str, Any]:
        assert activity_type == "merge_automation.evaluate_readiness"
        return {
            "headSha": "abc123",
            "ready": False,
            "pullRequestOpen": True,
            "policyAllowed": True,
            "checksComplete": False,
        }

    async def fake_wait_condition(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("unexpected workflow wait failure")

    monkeypatch.setattr(
        merge_automation_module.workflow,
        "execute_activity",
        fake_execute_activity,
    )
    monkeypatch.setattr(
        merge_automation_module.workflow,
        "wait_condition",
        fake_wait_condition,
    )
    monkeypatch.setattr(merge_automation_module.workflow, "now", lambda: datetime.now(timezone.utc))
    monkeypatch.setattr(merge_automation_module.workflow, "upsert_memo", lambda _memo: None)
    monkeypatch.setattr(
        merge_automation_module.workflow,
        "upsert_search_attributes",
        lambda _attrs: None,
    )

    with pytest.raises(RuntimeError, match="unexpected workflow wait failure"):
        await workflow.run(_payload())
