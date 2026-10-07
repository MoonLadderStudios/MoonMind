"""Workflow-boundary tests for the merge-automation automated review loop."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from temporalio.exceptions import CancelledError

from moonmind.workflows.merge_automation_review import build_review_request_key
from moonmind.workflows.temporal.workflows import (
    merge_automation as merge_automation_module,
)
from moonmind.workflows.temporal.workflows.merge_automation import (
    MoonMindMergeAutomationWorkflow,
)

MERGE_AUTOMATION_WORKFLOW_ID = "merge-automation:wf-parent"
OWNER_RUN_ID = "owner-run"
HEAD_1 = "abc1234"
HEAD_2 = "def4567"


def _payload(**review_loop_overrides: Any) -> dict[str, Any]:
    review_loop = {
        "enabled": True,
        "provider": "codex",
        "requestMode": "pr_comment",
        "requireFreshReviewForEveryHead": True,
        "requestAfterRemediation": True,
        "maxCycles": 5,
        "maxConsecutiveNoProgressCycles": 2,
    }
    review_loop.update(review_loop_overrides)
    return {
        "workflowType": "MoonMind.MergeAutomation",
        "parentWorkflowId": "wf-parent",
        "parentRunId": "run-parent",
        "publishContextRef": "artifact://publish-context",
        "pullRequest": {
            "repo": "MoonLadderStudios/MoonMind",
            "number": 350,
            "url": "https://github.com/MoonLadderStudios/MoonMind/pull/350",
            "headSha": HEAD_1,
            "headBranch": "feature",
            "baseBranch": "main",
        },
        "mergeAutomationConfig": {
            "gate": {
                "github": {"checks": "required", "automatedReview": "required"},
                "jira": {"status": "optional"},
            },
            "resolver": {"mergeMethod": "squash"},
            "timeouts": {"fallbackPollSeconds": 60},
            "reviewLoop": review_loop,
        },
        "idempotencyKey": "merge-automation:wf-parent:350",
    }


def _ready(head_sha: str, **overrides: Any) -> dict[str, Any]:
    payload = {
        "headSha": head_sha,
        "ready": True,
        "pullRequestOpen": True,
        "policyAllowed": True,
        "checksComplete": True,
        "checksPassing": True,
        "automatedReviewComplete": True,
        "jiraStatusAllowed": True,
    }
    payload.update(overrides)
    return payload


def _awaiting_review(head_sha: str, **overrides: Any) -> dict[str, Any]:
    payload = {
        "headSha": head_sha,
        "ready": False,
        "pullRequestOpen": True,
        "policyAllowed": True,
        "checksComplete": True,
        "checksPassing": True,
        "automatedReviewComplete": False,
        "jiraStatusAllowed": True,
        "blockers": [
            {
                "kind": "automated_review_pending",
                "summary": "Requested automated review has not completed.",
                "retryable": True,
                "source": "github",
            }
        ],
    }
    payload.update(overrides)
    return payload


def _request_review_result(
    *,
    child_workflow_id: str,
    head_sha: str,
    progress_signature: str = "sig-1",
    provider: str = "codex",
) -> dict[str, Any]:
    return {
        "status": "success",
        "completionDisposition": "gated_continuation",
        "mergeAutomationDisposition": "request_review",
        "headSha": head_sha,
        "executionRef": "step:1",
        "childRunId": "child-run",
        "gatedContinuation": {
            "schemaVersion": "gated-continuation/v2",
            "gateType": "merge_automation",
            "action": "request_review",
            "provider": provider,
            "reason": "fresh_review_required_after_remediation",
            "executionRef": "step:1",
            "headSha": head_sha,
            "progressSignature": progress_signature,
            "ownerWorkflowId": MERGE_AUTOMATION_WORKFLOW_ID,
            "ownerRunId": OWNER_RUN_ID,
            "ownerWorkflowType": "MoonMind.MergeAutomation",
            "childWorkflowId": child_workflow_id,
            "childRunId": "child-run",
        },
    }


class _Harness:
    """Drive the workflow with scripted readiness/child/activity behavior."""

    def __init__(
        self,
        monkeypatch: pytest.MonkeyPatch,
        *,
        readiness: list[dict[str, Any]],
        child_results,
        request_results: list[Any] | None = None,
        now: datetime | None = None,
    ) -> None:
        self.readiness = list(readiness)
        self.child_results = child_results
        self.request_results = list(request_results or [])
        self.readiness_payloads: list[dict[str, Any]] = []
        self.request_payloads: list[dict[str, Any]] = []
        self.child_payloads: list[dict[str, Any]] = []
        self.child_workflow_ids: list[str] = []
        self.wait_calls = 0
        self.artifact_names: list[str] = []
        self._now = now or datetime(2026, 8, 24, 22, 0, tzinfo=timezone.utc)

        async def fake_execute_activity(
            activity_type: str,
            payload: dict[str, Any],
            **_kwargs: Any,
        ) -> Any:
            if activity_type == "merge_automation.evaluate_readiness":
                self.readiness_payloads.append(payload)
                return self.readiness.pop(0) if self.readiness else _ready(HEAD_1)
            if activity_type == "merge_automation.request_automated_review":
                self.request_payloads.append(payload)
                if not self.request_results:
                    raise AssertionError("unexpected review request")
                outcome = self.request_results.pop(0)
                if isinstance(outcome, Exception):
                    raise outcome
                return outcome
            if activity_type == "artifact.create":
                name = str(payload.get("name") or "")
                self.artifact_names.append(name)
                return ({"artifact_id": f"art-{len(self.artifact_names)}"}, {})
            raise AssertionError(f"unexpected activity {activity_type}")

        async def fake_execute_typed_activity(
            _activity_type: str, _payload: Any, **_kwargs: Any
        ) -> Any:
            return None

        async def fake_execute_child_workflow(
            workflow_type: str,
            payload: dict[str, Any],
            **kwargs: Any,
        ) -> dict[str, Any]:
            assert workflow_type == "MoonMind.UserWorkflow"
            child_workflow_id = str(kwargs["id"])
            self.child_payloads.append(payload)
            self.child_workflow_ids.append(child_workflow_id)
            if callable(self.child_results):
                return self.child_results(child_workflow_id, len(self.child_workflow_ids))
            return self.child_results.pop(0)

        async def fake_wait_condition(*_args: Any, **_kwargs: Any) -> None:
            self.wait_calls += 1
            self._now = self._now + timedelta(seconds=60)

        async def fake_sleep(*_args: Any, **_kwargs: Any) -> None:
            return None

        monkeypatch.setattr(
            merge_automation_module.workflow, "execute_activity", fake_execute_activity
        )
        monkeypatch.setattr(
            merge_automation_module,
            "execute_typed_activity",
            fake_execute_typed_activity,
        )
        monkeypatch.setattr(
            merge_automation_module.workflow,
            "execute_child_workflow",
            fake_execute_child_workflow,
        )
        monkeypatch.setattr(
            merge_automation_module.workflow, "wait_condition", fake_wait_condition
        )
        monkeypatch.setattr(merge_automation_module.workflow, "sleep", fake_sleep)
        monkeypatch.setattr(
            merge_automation_module.workflow, "now", lambda: self._now
        )
        monkeypatch.setattr(
            merge_automation_module.workflow, "upsert_memo", lambda _memo: None
        )
        monkeypatch.setattr(
            merge_automation_module.workflow,
            "upsert_search_attributes",
            lambda _attrs: None,
        )
        monkeypatch.setattr(
            merge_automation_module.workflow,
            "info",
            lambda: SimpleNamespace(
                workflow_id=MERGE_AUTOMATION_WORKFLOW_ID, run_id=OWNER_RUN_ID
            ),
        )
        monkeypatch.setattr(
            merge_automation_module.workflow,
            "patched",  # Preserve these historical Activity sequences; the modern confirmation
            # path is covered by test_resolver_merge_confirmation and its real journey.
            lambda _patch_id: _patch_id
            != merge_automation_module.MERGE_AUTOMATION_RESOLVER_MERGE_CONFIRMATION_PATCH,
        )


def _posted(head_sha: str, comment_id: int = 98765) -> dict[str, Any]:
    return {
        "status": "requested",
        "provider": "codex",
        "command": "@codex review",
        "headSha": head_sha,
        "requestCommentId": comment_id,
        "requestedAt": "2026-08-24T22:15:00Z",
        "actor": "moonmind-bot",
        "reconciled": False,
        "retryable": False,
        "summary": "Requested an automated codex review.",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("observation", [0, 1, "failed_queued"])
@pytest.mark.parametrize("barrier_enabled", [True, False])
async def test_recorded_pending_review_cannot_release_a_repair(
    monkeypatch, observation, barrier_enabled
):
    fixture = (
        Path(__file__).resolve().parents[4]
        / "fixtures/temporal/pr_review_completion/premature_readiness.json"
    )
    if observation == "failed_queued":
        recorded = {
            "headSha": HEAD_1,
            "ready": False,
            "pullRequestOpen": True,
            "checksComplete": False,
            "checksPassing": False,
            # Older readiness results can omit review evidence while reporting
            # both a completed build failure and a queued downstream check.
            "blockers": [
                {"kind": "checks_failed", "summary": "Build tests failed."},
                {"kind": "checks_running", "summary": "Downstream CI Gate is queued."},
            ],
        }
    else:
        recorded = json.loads(fixture.read_text())["observations"][observation]["result"]
    head = recorded["headSha"]
    payload = _payload()
    payload["pullRequest"]["headSha"] = head
    payload["mergeAutomationConfig"]["finishMode"] = "fix_only"
    payload["activeReviewRequest"] = {
        "provider": "codex",
        "headSha": head,
        "requestKey": "recorded-request",
        "requestCommentId": 98765,
        "requestedAt": "2026-08-24T22:15:00Z",
    }
    harness = _Harness(
        monkeypatch,
        readiness=[recorded, _ready(head, automatedReviewComplete=True)],
        child_results=[
            {"status": "success", "mergeAutomationDisposition": "review_clean"}
        ],
    )
    monkeypatch.setattr(
        merge_automation_module.workflow,
        "patched",
        lambda name: (
            barrier_enabled
            if name == "merge-automation-active-review-barrier-v1"
            else True
        ),
    )
    result = await MoonMindMergeAutomationWorkflow().run(payload)
    assert result["status"] == "review_clean"
    assert len(harness.child_payloads) == 1
    assert harness.wait_calls == int(barrier_enabled or observation == "failed_queued")
    assert not harness.request_payloads


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["comment", "reaction"])
async def test_clean_response_crosses_activity_skill_and_workflow_without_another_cycle(
    monkeypatch, tmp_path, kind
):
    import httpx
    import runpy
    from moonmind.workflows.temporal import activity_runtime
    from moonmind.workflows.temporal.activity_runtime import (
        TemporalIntegrationActivities,
    )

    monkeypatch.setattr(
        activity_runtime.temporal_activity, "info",
        lambda: SimpleNamespace(activity_id="readiness-clean-review"),
    )

    root = Path(__file__).resolve().parents[5]
    snapshot_module = runpy.run_path(
        str(root / ".agents/skills/pr-resolver/bin/pr_resolve_snapshot.py")
    )
    finalize_module = runpy.run_path(
        str(root / ".agents/skills/pr-resolver/bin/pr_resolve_finalize.py")
    )
    head = "a" * 40
    request = {
        "id": 98765,
        "type": "issue_comment",
        "user": "owner",
        "body": "@codex review",
        "created_at": "2026-08-24T22:15:00Z",
    }
    clean = {
        "id": 56,
        "type": "issue_comment",
        "body": "**Codex Review:** Didn't find any major issues. 🚀",
        "created_at": "2026-08-24T22:20:00Z",
        "user": {"login": "chatgpt-codex-connector[bot]"},
    }
    reaction = {
        "id": 57,
        "content": "+1",
        "created_at": clean["created_at"],
        "user": clean["user"],
    }
    polls = []

    def github_response(req):
        path = req.url.path
        if path.endswith("/pulls/350"):
            data = {"state": "open", "head": {"sha": head}, "mergeable": True}
        elif path.endswith("/status"):
            data = {"state": "success", "statuses": []}
        elif path.endswith("/check-runs"):
            data = {"check_runs": [{"status": "completed", "conclusion": "success"}]}
        elif path.endswith("/reviews"):
            data = []
        elif path.endswith("/issues/350/reactions"):
            data = [reaction] if kind == "reaction" and len(polls) > 1 else []
        elif path.endswith("/reactions"):
            data = []
        elif path.endswith("/comments"):
            data = [clean] if kind == "comment" and len(polls) > 1 else []
        else:
            raise AssertionError(path)
        return httpx.Response(200, json=data)

    transport = httpx.MockTransport(github_response)
    client_type = httpx.AsyncClient
    monkeypatch.setattr(
        "moonmind.workflows.adapters.github_service.httpx.AsyncClient",
        lambda **kwargs: client_type(transport=transport, **kwargs),
    )
    monkeypatch.setenv("GITHUB_TOKEN", "github-token-fixture")

    def finish(child_id, attempt):
        assert len(polls) == 2  # No runtime is started during the pending poll.
        comments = [request]
        if kind == "comment":
            comments.append({**clean, "user": clean["user"]["login"]})
        evidence = snapshot_module["build_automated_review_evidence"](
            provider="codex",
            require_fresh_review=True,
            pr_repo="owner/repo",
            pr_number=350,
            head_sha=head,
            comments=comments,
            reviews=[],
            head_committed_at=datetime(2026, 8, 24, 22, 10, tzinfo=timezone.utc),
            reactions_for_request=[],
            reactions_for_pr=[reaction] if kind == "reaction" else [],
        )
        snapshot = {
            "pr": {
                "number": 350,
                "state": "OPEN",
                "headRefOid": head,
                "mergeable": True,
            },
            "ci": {"isRunning": False, "hasFailures": False, "signalQuality": "ok"},
            "commentsFetch": {"succeeded": True},
            "commentsSummary": snapshot_module["summarize_comments"](comments),
            "automatedReview": evidence,
        }
        snapshot_path = tmp_path / "snapshot.json"
        result_path = tmp_path / "result.json"
        snapshot_path.write_text(json.dumps(snapshot))
        monkeypatch.setattr(
            "sys.argv",
            [
                "pr_resolve_finalize.py",
                "--skip-refresh",
                "--finish-mode",
                "fix_only",
                "--snapshot-path",
                str(snapshot_path),
                "--result-path",
                str(result_path),
            ],
        )
        with pytest.raises(SystemExit) as exit_info:
            finalize_module["main"]()
        assert exit_info.value.code == finalize_module["EXIT_CODE_REVIEW_CLEAN"]
        terminal = json.loads(result_path.read_text())
        assert terminal["status"] == "review_clean"
        # UserWorkflow wraps the Skill's terminal status in its execution status.
        return {
            "status": "success",
            "mergeAutomationDisposition": terminal["mergeAutomationDisposition"],
        }

    harness = _Harness(monkeypatch, readiness=[], child_results=finish)
    fake_activity = merge_automation_module.workflow.execute_activity
    activities = TemporalIntegrationActivities()

    async def activity(name, payload, **kwargs):
        if name == "merge_automation.evaluate_readiness":
            polls.append(payload)
            return await activities.merge_automation_evaluate_readiness(payload)
        return await fake_activity(name, payload, **kwargs)

    monkeypatch.setattr(merge_automation_module.workflow, "execute_activity", activity)
    payload = _payload()
    payload["pullRequest"]["headSha"] = head
    payload["mergeAutomationConfig"]["finishMode"] = "fix_only"
    payload["activeReviewRequest"] = {
        "provider": "codex",
        "headSha": head,
        "requestKey": "key",
        "requestCommentId": request["id"],
        "requestedAt": request["created_at"],
    }
    result = await MoonMindMergeAutomationWorkflow().run(payload)
    assert result["status"] == "review_clean"
    assert len(harness.child_payloads) == 1
    assert harness.wait_calls == 1
    assert harness.request_payloads == []


@pytest.mark.asyncio
async def test_review_loop_requests_once_per_head_then_merges(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resolver asks, workflow requests, review lands, resolver merges."""

    def child_results(child_workflow_id: str, attempt: int) -> dict[str, Any]:
        if attempt == 1:
            return _request_review_result(
                child_workflow_id=child_workflow_id, head_sha=HEAD_2
            )
        return {"status": "success", "mergeAutomationDisposition": "merged"}

    harness = _Harness(
        monkeypatch,
        readiness=[
            _ready(HEAD_1),
            _awaiting_review(HEAD_2),
            _ready(HEAD_2, automatedReviewComplete=True,
                   automatedReviewCompletionKind="review",
                   automatedReviewCompletionId=45678,
                   automatedReviewCompletedAt="2026-08-24T22:19:00Z"),
        ],
        child_results=child_results,
        request_results=[_posted(HEAD_2)],
    )

    result = await MoonMindMergeAutomationWorkflow().run(_payload())

    assert result["status"] == "merged"
    assert len(harness.request_payloads) == 1
    request = harness.request_payloads[0]
    assert request["expectedHeadSha"] == HEAD_2
    assert request["provider"] == "codex"
    assert request["requestKey"] == build_review_request_key(
        parent_workflow_id=MERGE_AUTOMATION_WORKFLOW_ID,
        repository="MoonLadderStudios/MoonMind",
        pr_number=350,
        head_sha=HEAD_2,
        provider="codex",
    )
    # The child never supplies request text.
    assert "command" not in request
    assert "body" not in request

    review_loop = result["reviewLoop"]
    assert review_loop["cycles"] == 1
    assert review_loop["activeRequest"] is None
    cycle = review_loop["cycleRecords"][0]
    assert cycle["headSha"] == HEAD_2
    assert cycle["requestCommentId"] == 98765
    assert cycle["completionKind"] == "review"
    assert cycle["completionId"] == 45678
    assert cycle["status"] == "completed"

    # While waiting, readiness carries the active request so only that
    # request's own result can open the gate.
    waiting_payload = harness.readiness_payloads[1]
    assert waiting_payload["activeReviewRequest"]["headSha"] == HEAD_2
    assert waiting_payload["activeReviewRequest"]["requestCommentId"] == 98765


@pytest.mark.asyncio
async def test_comments_arriving_with_a_review_start_a_new_cycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A review that leaves comments must be re-reviewed after remediation."""

    def child_results(child_workflow_id: str, attempt: int) -> dict[str, Any]:
        if attempt == 1:
            # No comments yet on the published head: ask for the first review.
            return _request_review_result(
                child_workflow_id=child_workflow_id,
                head_sha=HEAD_1,
                progress_signature="sig-head-1",
            )
        if attempt == 2:
            # The review left actionable comments; fix-comments pushed HEAD_2.
            return _request_review_result(
                child_workflow_id=child_workflow_id,
                head_sha=HEAD_2,
                progress_signature="sig-head-2",
            )
        return {"status": "success", "mergeAutomationDisposition": "merged"}

    harness = _Harness(
        monkeypatch,
        readiness=[
            _ready(HEAD_1),
            _awaiting_review(HEAD_1),
            _ready(HEAD_1, automatedReviewComplete=True),
            _awaiting_review(HEAD_2),
            _ready(HEAD_2, automatedReviewComplete=True),
        ],
        child_results=child_results,
        request_results=[_posted(HEAD_1, 1), _posted(HEAD_2, 2)],
    )

    result = await MoonMindMergeAutomationWorkflow().run(_payload())

    assert result["status"] == "merged"
    assert [call["expectedHeadSha"] for call in harness.request_payloads] == [
        HEAD_1,
        HEAD_2,
    ]
    review_loop = result["reviewLoop"]
    assert review_loop["cycles"] == 2
    assert [cycle["headSha"] for cycle in review_loop["cycleRecords"]] == [
        HEAD_1,
        HEAD_2,
    ]
    assert all(
        cycle["status"] == "completed" for cycle in review_loop["cycleRecords"]
    )
    # Each head SHA gets its own request identity.
    assert (
        harness.request_payloads[0]["requestKey"]
        != harness.request_payloads[1]["requestKey"]
    )


@pytest.mark.asyncio
async def test_resolver_child_receives_review_loop_inputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _Harness(
        monkeypatch,
        readiness=[_ready(HEAD_1)],
        child_results=[{"status": "success", "mergeAutomationDisposition": "merged"}],
    )

    await MoonMindMergeAutomationWorkflow().run(_payload())

    args = harness.child_payloads[0]["initial_parameters"]["task"]["skill"]["args"]
    assert args["reviewProvider"] == "codex"
    assert args["requireFreshReview"] is True


@pytest.mark.asyncio
async def test_head_change_while_waiting_invalidates_the_pending_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def child_results(child_workflow_id: str, attempt: int) -> dict[str, Any]:
        if attempt == 1:
            return _request_review_result(
                child_workflow_id=child_workflow_id, head_sha=HEAD_1
            )
        return {"status": "success", "mergeAutomationDisposition": "merged"}

    harness = _Harness(
        monkeypatch,
        readiness=[
            _ready(HEAD_1),
            # Someone else pushed: the pending request cannot answer for HEAD_2.
            _awaiting_review(HEAD_2, automatedReviewRequestStale=True),
            _ready(HEAD_2),
        ],
        child_results=child_results,
        request_results=[_posted(HEAD_1)],
    )

    result = await MoonMindMergeAutomationWorkflow().run(_payload())

    assert result["status"] == "merged"
    assert result["latestHeadSha"] == HEAD_2
    review_loop = result["reviewLoop"]
    assert review_loop["activeRequest"] is None
    assert review_loop["cycleRecords"][0]["status"] == "stale"
    # No second request was posted for the abandoned head.
    assert len(harness.request_payloads) == 1


@pytest.mark.asyncio
async def test_webhook_loss_recovers_through_fallback_polling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No external signal arrives; the fallback poll still finds the result."""

    def child_results(child_workflow_id: str, attempt: int) -> dict[str, Any]:
        if attempt == 1:
            return _request_review_result(
                child_workflow_id=child_workflow_id, head_sha=HEAD_1
            )
        return {"status": "success", "mergeAutomationDisposition": "merged"}

    harness = _Harness(
        monkeypatch,
        readiness=[
            _ready(HEAD_1),
            _awaiting_review(HEAD_1),
            _awaiting_review(HEAD_1),
            _ready(HEAD_1, automatedReviewComplete=True),
        ],
        child_results=child_results,
        request_results=[_posted(HEAD_1)],
    )

    result = await MoonMindMergeAutomationWorkflow().run(_payload())

    assert result["status"] == "merged"
    assert harness.wait_calls == 2
    assert len(harness.request_payloads) == 1


@pytest.mark.asyncio
async def test_repeated_signature_stops_for_manual_review(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def child_results(child_workflow_id: str, _attempt: int) -> dict[str, Any]:
        return _request_review_result(
            child_workflow_id=child_workflow_id,
            head_sha=HEAD_1,
            progress_signature="same-signature",
        )

    harness = _Harness(
        monkeypatch,
        readiness=[
            _ready(HEAD_1),
            _ready(HEAD_1, automatedReviewComplete=True),
            _ready(HEAD_1, automatedReviewComplete=True),
            _ready(HEAD_1, automatedReviewComplete=True),
        ],
        child_results=child_results,
        request_results=[_posted(HEAD_1, 1), _posted(HEAD_1, 2), _posted(HEAD_1, 3)],
    )

    result = await MoonMindMergeAutomationWorkflow().run(_payload())

    assert result["status"] == "blocked"
    assert [b["kind"] for b in result["blockers"]] == ["review_loop_no_progress"]
    # The third cycle is refused before another request is posted.
    assert len(harness.request_payloads) == 2


@pytest.mark.asyncio
async def test_cycle_budget_stops_the_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    signatures = iter(["s1", "s2", "s3", "s4", "s5"])

    def child_results(child_workflow_id: str, _attempt: int) -> dict[str, Any]:
        return _request_review_result(
            child_workflow_id=child_workflow_id,
            head_sha=HEAD_1,
            progress_signature=next(signatures),
        )

    harness = _Harness(
        monkeypatch,
        readiness=[_ready(HEAD_1, automatedReviewComplete=True) for _ in range(6)],
        child_results=child_results,
        request_results=[_posted(HEAD_1, index) for index in range(1, 4)],
    )

    result = await MoonMindMergeAutomationWorkflow().run(
        _payload(maxCycles=2)
    )

    assert result["status"] == "blocked"
    assert [b["kind"] for b in result["blockers"]] == [
        "review_cycle_budget_exhausted"
    ]
    assert len(harness.request_payloads) == 2


@pytest.mark.asyncio
async def test_unprovable_request_blocks_instead_of_merging(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def child_results(child_workflow_id: str, _attempt: int) -> dict[str, Any]:
        return _request_review_result(
            child_workflow_id=child_workflow_id, head_sha=HEAD_1
        )

    _Harness(
        monkeypatch,
        readiness=[_ready(HEAD_1)],
        child_results=child_results,
        request_results=[RuntimeError("github unavailable")],
    )

    result = await MoonMindMergeAutomationWorkflow().run(_payload())

    assert result["status"] == "blocked"
    assert [b["kind"] for b in result["blockers"]] == [
        "automated_review_request_failed"
    ]


@pytest.mark.asyncio
async def test_stale_head_at_request_time_adopts_the_new_head(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def child_results(child_workflow_id: str, attempt: int) -> dict[str, Any]:
        if attempt == 1:
            return _request_review_result(
                child_workflow_id=child_workflow_id, head_sha=HEAD_1
            )
        return {"status": "success", "mergeAutomationDisposition": "merged"}

    harness = _Harness(
        monkeypatch,
        readiness=[_ready(HEAD_1), _ready(HEAD_2)],
        child_results=child_results,
        request_results=[
            {
                "status": "stale_head",
                "provider": "codex",
                "headSha": HEAD_1,
                "observedHeadSha": HEAD_2,
                "retryable": False,
                "summary": "Pull request head advanced before the request.",
            }
        ],
    )

    result = await MoonMindMergeAutomationWorkflow().run(_payload())

    assert result["status"] == "merged"
    assert result["latestHeadSha"] == HEAD_2
    # A skipped request does not consume the cycle budget.
    assert result["reviewLoop"]["cycles"] == 0
    assert len(harness.request_payloads) == 1


@pytest.mark.asyncio
async def test_request_review_requires_the_configured_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def child_results(child_workflow_id: str, _attempt: int) -> dict[str, Any]:
        return _request_review_result(
            child_workflow_id=child_workflow_id,
            head_sha=HEAD_1,
            provider="some-other-reviewer",
        )

    _Harness(
        monkeypatch,
        readiness=[_ready(HEAD_1)],
        child_results=child_results,
    )

    result = await MoonMindMergeAutomationWorkflow().run(_payload())

    assert result["status"] == "failed"
    assert [b["kind"] for b in result["blockers"]] == [
        "resolver_continuation_invalid"
    ]


@pytest.mark.asyncio
async def test_request_review_requires_owner_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def child_results(child_workflow_id: str, _attempt: int) -> dict[str, Any]:
        result = _request_review_result(
            child_workflow_id=child_workflow_id, head_sha=HEAD_1
        )
        result["gatedContinuation"]["ownerWorkflowId"] = "someone-else"
        return result

    _Harness(
        monkeypatch,
        readiness=[_ready(HEAD_1)],
        child_results=child_results,
    )

    result = await MoonMindMergeAutomationWorkflow().run(_payload())

    assert result["status"] == "failed"
    assert [b["kind"] for b in result["blockers"]] == [
        "resolver_continuation_invalid"
    ]


@pytest.mark.asyncio
async def test_request_review_without_review_loop_fails_fast(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def child_results(child_workflow_id: str, _attempt: int) -> dict[str, Any]:
        return _request_review_result(
            child_workflow_id=child_workflow_id, head_sha=HEAD_1
        )

    _Harness(
        monkeypatch,
        readiness=[_ready(HEAD_1)],
        child_results=child_results,
    )

    result = await MoonMindMergeAutomationWorkflow().run(_payload(enabled=False))

    assert result["status"] == "failed"
    assert [b["kind"] for b in result["blockers"]] == [
        "resolver_continuation_invalid"
    ]


@pytest.mark.asyncio
async def test_cancellation_during_review_wait_is_reported_as_canceled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def child_results(child_workflow_id: str, _attempt: int) -> dict[str, Any]:
        return _request_review_result(
            child_workflow_id=child_workflow_id, head_sha=HEAD_1
        )

    harness = _Harness(
        monkeypatch,
        readiness=[_ready(HEAD_1), _awaiting_review(HEAD_1)],
        child_results=child_results,
        request_results=[_posted(HEAD_1)],
    )

    async def cancel_wait(*_args: Any, **_kwargs: Any) -> None:
        harness.wait_calls += 1
        raise CancelledError("canceled")

    monkeypatch.setattr(
        merge_automation_module.workflow, "wait_condition", cancel_wait
    )

    with pytest.raises(CancelledError):
        await MoonMindMergeAutomationWorkflow().run(_payload())

    assert harness.wait_calls == 1
    assert len(harness.request_payloads) == 1


@pytest.mark.asyncio
async def test_expiry_stops_the_review_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    def child_results(child_workflow_id: str, _attempt: int) -> dict[str, Any]:
        return _request_review_result(
            child_workflow_id=child_workflow_id, head_sha=HEAD_1
        )

    harness = _Harness(
        monkeypatch,
        readiness=[_ready(HEAD_1), _awaiting_review(HEAD_1)],
        child_results=child_results,
        request_results=[_posted(HEAD_1)],
    )
    payload = _payload()
    payload["mergeAutomationConfig"]["timeouts"]["expireAfterSeconds"] = 30

    result = await MoonMindMergeAutomationWorkflow().run(payload)

    assert result["status"] == "expired"
    assert len(harness.request_payloads) == 1


@pytest.mark.asyncio
async def test_review_cycles_survive_continue_as_new_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A resumed run keeps its ledger, so budgets are not silently reset."""

    payload = _payload()
    payload["reviewCycles"] = [
        {
            "cycle": 1,
            "provider": "codex",
            "headSha": HEAD_1,
            "requestKey": "key-1",
            "requestCommentId": 5,
            "requestedAt": "2026-08-24T22:00:00Z",
            "status": "completed",
        }
    ]
    payload["activeReviewRequest"] = {
        "provider": "codex",
        "headSha": HEAD_1,
        "requestKey": "key-1",
        "requestCommentId": 5,
        "requestedAt": "2026-08-24T22:00:00Z",
    }

    harness = _Harness(
        monkeypatch,
        readiness=[_ready(HEAD_1, automatedReviewComplete=True)],
        child_results=[{"status": "success", "mergeAutomationDisposition": "merged"}],
    )

    result = await MoonMindMergeAutomationWorkflow().run(payload)

    assert result["status"] == "merged"
    assert result["reviewLoop"]["cycles"] == 1
    # The restored request is settled by the first readiness evaluation.
    assert harness.readiness_payloads[0]["activeReviewRequest"]["requestKey"] == "key-1"
    assert result["reviewLoop"]["activeRequest"] is None


@pytest.mark.asyncio
async def test_pre_review_loop_start_input_still_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An in-flight payload recorded before the review loop keeps working.

    The new fields are additive with defaults, so a history that never carried
    ``reviewLoop``, ``reviewCycles``, or ``activeReviewRequest`` still validates
    and takes the original gate path.
    """

    legacy_payload = {
        "workflowType": "MoonMind.MergeAutomation",
        "parentWorkflowId": "wf-parent",
        "parentRunId": "run-parent",
        "publishContextRef": "artifact://publish-context",
        "pullRequest": {
            "repo": "MoonLadderStudios/MoonMind",
            "number": 350,
            "url": "https://github.com/MoonLadderStudios/MoonMind/pull/350",
            "headSha": HEAD_1,
        },
        "mergeAutomationConfig": {
            "gate": {
                "github": {"checks": "required", "automatedReview": "required"},
                "jira": {"status": "optional"},
            },
            "resolver": {"mergeMethod": "squash"},
            "timeouts": {"fallbackPollSeconds": 300},
        },
    }

    harness = _Harness(
        monkeypatch,
        readiness=[_ready(HEAD_1, automatedReviewComplete=True)],
        child_results=[{"status": "success", "mergeAutomationDisposition": "merged"}],
    )

    result = await MoonMindMergeAutomationWorkflow().run(legacy_payload)

    assert result["status"] == "merged"
    assert "reviewLoop" not in result
    # The legacy resolver child is launched without review-loop Skill args.
    args = harness.child_payloads[0]["initial_parameters"]["task"]["skill"]["args"]
    assert "reviewProvider" not in args
    assert "requireFreshReview" not in args
    # The readiness payload still carries the additive key with a null value.
    assert harness.readiness_payloads[0]["activeReviewRequest"] is None
    assert (
        harness.readiness_payloads[0]["mergeAutomationConfig"]["reviewLoop"]["enabled"]
        is False
    )


@pytest.mark.asyncio
async def test_fix_only_finish_mode_ends_the_loop_without_merging(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The loop terminates successfully once nothing is left to address."""

    payload = _payload()
    payload["mergeAutomationConfig"]["finishMode"] = "fix_only"

    harness = _Harness(
        monkeypatch,
        readiness=[
            _ready(HEAD_1, automatedReviewComplete=True),
        ],
        child_results=[
            {"status": "success", "mergeAutomationDisposition": "review_clean"}
        ],
    )

    result = await MoonMindMergeAutomationWorkflow().run(payload)

    assert result["status"] == "review_clean"
    assert result["blockers"] == []
    assert "no actionable comments remain" in result["summary"]
    # The resolver child was launched without merge authority.
    args = harness.child_payloads[0]["initial_parameters"]["task"]["skill"]["args"]
    assert args["finishMode"] == "fix_only"


@pytest.mark.asyncio
async def test_fix_only_finish_mode_still_runs_the_review_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Withholding merge authority must not weaken the request/fix cycle."""

    payload = _payload()
    payload["mergeAutomationConfig"]["finishMode"] = "fix_only"

    def child_results(child_workflow_id: str, attempt: int) -> dict[str, Any]:
        if attempt == 1:
            return _request_review_result(
                child_workflow_id=child_workflow_id, head_sha=HEAD_2
            )
        return {"status": "success", "mergeAutomationDisposition": "review_clean"}

    harness = _Harness(
        monkeypatch,
        readiness=[
            _ready(HEAD_1),
            _awaiting_review(HEAD_2),
            _ready(
                HEAD_2,
                automatedReviewComplete=True,
                automatedReviewCompletionKind="review",
                automatedReviewCompletionId=45678,
                automatedReviewCompletedAt="2026-08-24T22:19:00Z",
            ),
        ],
        child_results=child_results,
        request_results=[_posted(HEAD_2)],
    )

    result = await MoonMindMergeAutomationWorkflow().run(payload)

    assert result["status"] == "review_clean"
    assert len(harness.request_payloads) == 1
    assert result["reviewLoop"]["cycles"] == 1
    for child_payload in harness.child_payloads:
        args = child_payload["initial_parameters"]["task"]["skill"]["args"]
        assert args["finishMode"] == "fix_only"
        assert args["reviewProvider"] == "codex"


@pytest.mark.asyncio
async def test_merge_finish_mode_is_the_default_for_existing_payloads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An in-flight payload without finishMode keeps its merge authority."""

    payload = _payload()
    assert "finishMode" not in payload["mergeAutomationConfig"]

    harness = _Harness(
        monkeypatch,
        readiness=[_ready(HEAD_1, automatedReviewComplete=True)],
        child_results=[{"status": "success", "mergeAutomationDisposition": "merged"}],
    )

    result = await MoonMindMergeAutomationWorkflow().run(payload)

    assert result["status"] == "merged"
    args = harness.child_payloads[0]["initial_parameters"]["task"]["skill"]["args"]
    assert args["finishMode"] == "merge"


@pytest.mark.asyncio
async def test_review_clean_is_rejected_when_merge_authority_was_granted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A run asked to merge must not close the gate as an unmerged success."""

    payload = _payload()
    payload["mergeAutomationConfig"]["finishMode"] = "merge"

    _Harness(
        monkeypatch,
        readiness=[_ready(HEAD_1, automatedReviewComplete=True)],
        child_results=[
            {"status": "success", "mergeAutomationDisposition": "review_clean"}
        ],
    )

    result = await MoonMindMergeAutomationWorkflow().run(payload)

    assert result["status"] == "failed"
    assert result["blockers"][0]["kind"] == "resolver_disposition_invalid"
    assert "granted merge authority" in result["summary"]


@pytest.mark.asyncio
@pytest.mark.parametrize("signature", ["fe7be0bb2682|3984847667|", None])
@pytest.mark.parametrize("budget", [2, 5])
async def test_repeated_reenter_handoff_exhausts_shared_progress_budget(
    monkeypatch, signature, budget
):
    def result(child_id, attempt):
        value = _request_review_result(child_workflow_id=child_id, head_sha=HEAD_1)
        value["mergeAutomationDisposition"] = "reenter_gate"
        value["gatedContinuation"].update(
            schemaVersion="gated-continuation/v1",
            action="reenter_gate",
            reason="automated_review_wait",
            retryAfterSeconds=60,
            progressSignature=signature,
        )
        return value

    harness = _Harness(monkeypatch, readiness=[_ready(HEAD_1)], child_results=result)
    outcome = await MoonMindMergeAutomationWorkflow().run(
        _payload(maxConsecutiveNoProgressCycles=budget)
    )
    assert outcome["status"] == "blocked"
    assert outcome["blockers"][0]["kind"] == "review_loop_no_progress"
    assert len(harness.child_workflow_ids) == budget + 1
    assert outcome["reviewLoop"]["noProgressCycles"] == budget
    assert not harness.request_payloads


@pytest.mark.asyncio
async def test_reenter_progress_budget_preserves_pre_patch_history(monkeypatch):
    def result(child_id, attempt):
        if attempt == 5:
            return {"status": "success", "mergeAutomationDisposition": "merged"}
        value = _request_review_result(child_workflow_id=child_id, head_sha=HEAD_1)
        value["mergeAutomationDisposition"] = "reenter_gate"
        value["gatedContinuation"].update(
            schemaVersion="gated-continuation/v1",
            action="reenter_gate",
            reason="ci_running",
            retryAfterSeconds=60,
        )
        return value

    harness = _Harness(monkeypatch, readiness=[_ready(HEAD_1)], child_results=result)
    monkeypatch.setattr(
        merge_automation_module.workflow,
        "patched",
        lambda name: name
        not in {
            "merge-automation-bound-reenter-progress-v1",
            merge_automation_module.MERGE_AUTOMATION_RESOLVER_MERGE_CONFIRMATION_PATCH,
        },
    )
    assert (await MoonMindMergeAutomationWorkflow().run(_payload()))[
        "status"
    ] == "merged"
    assert len(harness.child_workflow_ids) == 5


def _review_only_payload() -> dict[str, Any]:
    payload = _payload()
    payload["mergeAutomationConfig"]["finishMode"] = "review_only"
    # Exercise the actual child-plan preparation boundary if a regression tries
    # to start a resolver, rather than silently taking a legacy runtime path.
    payload["resolverTemplate"] = {"targetRuntime": "omnigent"}
    return payload


def _review_only_harness(
    monkeypatch: pytest.MonkeyPatch,
    *,
    readiness: list[dict[str, Any]],
    request_results: list[Any] | None = None,
) -> _Harness:
    harness = _Harness(
        monkeypatch,
        readiness=readiness,
        child_results=[],
        request_results=request_results,
    )
    harness.typed_activity_names = []
    original_activity = merge_automation_module.workflow.execute_activity

    async def bounded_activity(name, payload, **kwargs):
        if name == "merge_automation.evaluate_readiness":
            assert harness.readiness, "review-only exhausted its scripted observations"
        return await original_activity(name, payload, **kwargs)

    async def artifact_only_typed_activity(name, _payload, **_kwargs):
        harness.typed_activity_names.append(name)
        assert name == "artifact.write_complete", f"unauthorized typed activity: {name}"
        return None

    monkeypatch.setattr(
        merge_automation_module.workflow, "execute_activity", bounded_activity
    )
    monkeypatch.setattr(
        merge_automation_module, "execute_typed_activity", artifact_only_typed_activity
    )
    return harness


def _assert_review_only_has_no_resolver(harness: _Harness, result) -> None:
    assert harness.child_payloads == []
    assert result["resolverChildWorkflowIds"] == []
    assert result.get("remediationChildWorkflowIds", []) == []
    assert all(name == "artifact.write_complete" for name in harness.typed_activity_names)
    assert not any("resolver" in name for name in harness.artifact_names)
    # Unexpected plain activities (including child-plan preparation and
    # post-merge GitHub/Jira effects) already fail in the existing harness.


def _review_only_completed(head_sha: str) -> dict[str, Any]:
    return _awaiting_review(
        head_sha,
        checksComplete=False,
        checksPassing=False,
        automatedReviewComplete=True,
        automatedReviewCompletionKind="review",
        automatedReviewCompletionId=45678,
        automatedReviewCompletedAt="2026-08-24T22:19:00Z",
        blockers=[
            {"kind": "checks_running", "summary": "Required CI remains pending."}
        ],
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("ci_state", ["queued", "failure"])
async def test_review_only_requests_and_waits_for_findings_without_resolver(
    monkeypatch, ci_state
):
    """A request-bound CHANGES_REQUESTED review completes only this objective."""
    import httpx

    from moonmind.workflows.temporal import activity_runtime
    from moonmind.workflows.temporal.activity_runtime import TemporalIntegrationActivities

    harness = _review_only_harness(
        monkeypatch, readiness=[], request_results=[_posted(HEAD_1)]
    )
    active_polls = []
    polls = []
    monkeypatch.setattr(
        activity_runtime.temporal_activity,
        "info",
        lambda: SimpleNamespace(activity_id="review-only-readiness"),
    )
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def selected_token(authority, *, repository, operation):
        assert authority["executionOwner"] == MERGE_AUTOMATION_WORKFLOW_ID
        assert repository == "MoonLadderStudios/MoonMind"
        assert operation == "read"
        yield "github-token-fixture"

    monkeypatch.setattr(
        activity_runtime, "merge_automation_repository_token", selected_token
    )

    def github_response(request):
        path = request.url.path
        if path.endswith("/pulls/350"):
            data = {"state": "open", "head": {"sha": HEAD_1}, "mergeable": True}
        elif path.endswith("/status"):
            data = {"state": "pending", "statuses": []}
        elif path.endswith("/check-runs"):
            data = {
                "check_runs": [
                    {
                        "name": "Required CI",
                        "status": "queued" if ci_state == "queued" else "completed",
                        "conclusion": None if ci_state == "queued" else "failure",
                    }
                ]
            }
        elif path.endswith("/reviews"):
            data = (
                [
                    {
                        "id": 45678,
                        "state": "CHANGES_REQUESTED",
                        "body": "The review identified a bug requiring a separate fix.",
                        "commit_id": HEAD_1,
                        "submitted_at": "2026-08-24T22:19:00Z",
                        "user": {"login": "chatgpt-codex-connector"},
                    }
                ]
                if len(active_polls) > 1
                else []
            )
        elif path.endswith("/comments") or path.endswith("/reactions"):
            data = []
        else:
            raise AssertionError(f"unexpected GitHub read: {path}")
        return httpx.Response(200, json=data)

    transport = httpx.MockTransport(github_response)
    client_type = httpx.AsyncClient
    monkeypatch.setattr(
        "moonmind.workflows.adapters.github_service.httpx.AsyncClient",
        lambda **kwargs: client_type(transport=transport, **kwargs),
    )
    original_activity = merge_automation_module.workflow.execute_activity
    activities = TemporalIntegrationActivities()

    async def activity(name, payload, **kwargs):
        if name == "merge_automation.evaluate_readiness":
            polls.append(payload)
            harness.readiness_payloads.append(payload)
            assert len(polls) <= 4, "review-only never settled matching completion"
            if payload.get("activeReviewRequest"):
                active_polls.append(payload)
            return await activities.merge_automation_evaluate_readiness(payload)
        return await original_activity(name, payload, **kwargs)

    monkeypatch.setattr(merge_automation_module.workflow, "execute_activity", activity)
    result = await MoonMindMergeAutomationWorkflow().run(_review_only_payload())

    assert result["status"] == "review_complete"
    assert result["latestHeadSha"] == HEAD_1
    assert harness.wait_calls >= 1
    assert len(harness.request_payloads) == 1
    request = harness.request_payloads[0]
    assert request["parentWorkflowId"] == MERGE_AUTOMATION_WORKFLOW_ID
    assert request["expectedHeadSha"] == HEAD_1
    assert request["provider"] == "codex"
    assert request["requestKey"] == build_review_request_key(
        parent_workflow_id=MERGE_AUTOMATION_WORKFLOW_ID,
        repository="MoonLadderStudios/MoonMind",
        pr_number=350,
        head_sha=HEAD_1,
        provider="codex",
    )
    assert "command" not in request and "body" not in request
    assert active_polls[0]["activeReviewRequest"]["requestCommentId"] == 98765
    cycle = result["reviewLoop"]["cycleRecords"][0]
    assert cycle["headSha"] == HEAD_1
    assert cycle["completionKind"] == "review"
    assert cycle["completionId"] == 45678
    assert cycle["completedAt"] == "2026-08-24T22:19:00Z"
    assert cycle["status"] == "completed"
    assert result["reviewLoop"]["activeRequest"] is None
    assert "no actionable comments" not in str(result.get("summary") or "").lower()
    _assert_review_only_has_no_resolver(harness, result)


@pytest.mark.asyncio
async def test_review_only_restored_request_waits_without_reposting(monkeypatch):
    payload = _review_only_payload()
    request_key = build_review_request_key(
        parent_workflow_id=MERGE_AUTOMATION_WORKFLOW_ID,
        repository="MoonLadderStudios/MoonMind",
        pr_number=350,
        head_sha=HEAD_1,
        provider="codex",
    )
    active = {
        "provider": "codex",
        "headSha": HEAD_1,
        "requestKey": request_key,
        "requestCommentId": 98765,
        "requestedAt": "2026-08-24T22:15:00Z",
    }
    payload["activeReviewRequest"] = active
    payload["reviewCycles"] = [{"cycle": 1, **active, "status": "requested"}]
    harness = _review_only_harness(
        monkeypatch,
        readiness=[_awaiting_review(HEAD_1), _review_only_completed(HEAD_1)],
    )

    result = await MoonMindMergeAutomationWorkflow().run(payload)

    assert result["status"] == "review_complete"
    assert harness.request_payloads == []
    assert harness.wait_calls == 1
    restored = harness.readiness_payloads[0]["activeReviewRequest"]
    assert {key: restored[key] for key in active} == active
    assert result["reviewLoop"]["cycles"] == 1
    assert result["reviewLoop"]["cycleRecords"][0]["requestKey"] == request_key
    _assert_review_only_has_no_resolver(harness, result)


@pytest.mark.asyncio
@pytest.mark.parametrize("request_outcome", ["stale_head", "wrong_posted_head"])
async def test_review_only_rejects_request_for_a_different_head(
    monkeypatch, request_outcome
):
    outcome = (
        {"status": "stale_head", "observedHeadSha": HEAD_2}
        if request_outcome == "stale_head"
        else _posted(HEAD_2)
    )
    harness = _review_only_harness(
        monkeypatch,
        readiness=[_awaiting_review(HEAD_1)],
        request_results=[outcome],
    )

    result = await MoonMindMergeAutomationWorkflow().run(_review_only_payload())

    assert result["status"] in {"blocked", "failed"}
    assert result["latestHeadSha"] == HEAD_1
    assert len(harness.request_payloads) == 1
    assert harness.request_payloads[0]["expectedHeadSha"] == HEAD_1
    assert harness.wait_calls == 0
    _assert_review_only_has_no_resolver(harness, result)


@pytest.mark.asyncio
async def test_review_only_head_change_during_wait_blocks_without_retargeting(monkeypatch):
    harness = _review_only_harness(
        monkeypatch,
        readiness=[
            _awaiting_review(HEAD_1),
            _awaiting_review(HEAD_2, automatedReviewRequestStale=True),
        ],
        request_results=[_posted(HEAD_1)],
    )

    result = await MoonMindMergeAutomationWorkflow().run(_review_only_payload())

    assert result["status"] == "blocked"
    assert result["latestHeadSha"] == HEAD_1
    assert [request["expectedHeadSha"] for request in harness.request_payloads] == [HEAD_1]
    assert any(blocker["kind"] == "stale_revision" for blocker in result["blockers"])
    _assert_review_only_has_no_resolver(harness, result)


@pytest.mark.asyncio
async def test_review_only_wrong_head_completion_cannot_finish_request(monkeypatch):
    harness = _review_only_harness(
        monkeypatch,
        readiness=[_awaiting_review(HEAD_1), _review_only_completed(HEAD_2)],
        request_results=[_posted(HEAD_1)],
    )

    result = await MoonMindMergeAutomationWorkflow().run(_review_only_payload())

    assert result["status"] == "blocked"
    assert result["latestHeadSha"] == HEAD_1
    assert len(harness.request_payloads) == 1
    assert not any(
        cycle["status"] == "completed" for cycle in result["reviewLoop"]["cycleRecords"]
    )
    _assert_review_only_has_no_resolver(harness, result)


@pytest.mark.asyncio
async def test_review_only_historical_evidence_does_not_replace_fresh_request(monkeypatch):
    harness = _review_only_harness(
        monkeypatch,
        readiness=[_awaiting_review(HEAD_1), _review_only_completed(HEAD_1)],
        request_results=[_posted(HEAD_1)],
    )
    original_activity = merge_automation_module.workflow.execute_activity

    async def activity(name, payload, **kwargs):
        if name == "merge_automation.evaluate_readiness" and not payload.get(
            "activeReviewRequest"
        ):
            harness.readiness_payloads.append(payload)
            assert len(harness.readiness_payloads) <= 3, "historical review caused a loop"
            return _ready(
                HEAD_1,
                automatedReviewCompletionKind="review",
                automatedReviewCompletionId=123,
                automatedReviewCompletedAt="2026-08-24T22:10:00Z",
            )
        return await original_activity(name, payload, **kwargs)

    monkeypatch.setattr(merge_automation_module.workflow, "execute_activity", activity)
    result = await MoonMindMergeAutomationWorkflow().run(_review_only_payload())

    assert result["status"] == "review_complete"
    assert len(harness.request_payloads) == 1
    assert result["reviewLoop"]["cycleRecords"][0]["completionId"] == 45678
    assert harness.wait_calls >= 1
    _assert_review_only_has_no_resolver(harness, result)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "missing_field", ["automatedReviewCompletionId", "automatedReviewCompletedAt"]
)
async def test_review_only_incomplete_completion_evidence_expires(monkeypatch, missing_field):
    """A true completion flag cannot replace identifiable, dated evidence."""
    completion = _review_only_completed(HEAD_1)
    completion.pop(missing_field)
    harness = _review_only_harness(
        monkeypatch,
        readiness=[_awaiting_review(HEAD_1), completion, dict(completion)],
        request_results=[_posted(HEAD_1)],
    )
    payload = _review_only_payload()
    payload["mergeAutomationConfig"]["timeouts"]["expireAfterSeconds"] = 120

    result = await MoonMindMergeAutomationWorkflow().run(payload)

    assert result["status"] == "expired"
    assert len(harness.request_payloads) == 1
    assert not any(
        cycle["status"] == "completed" for cycle in result["reviewLoop"]["cycleRecords"]
    )
    _assert_review_only_has_no_resolver(harness, result)


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_binding", ["provider", "requestKey"])
async def test_review_only_restored_request_binding_mismatch_blocks(
    monkeypatch, invalid_binding
):
    payload = _review_only_payload()
    active = {
        "provider": "codex",
        "headSha": HEAD_1,
        "requestKey": build_review_request_key(
            parent_workflow_id=MERGE_AUTOMATION_WORKFLOW_ID,
            repository="MoonLadderStudios/MoonMind",
            pr_number=350,
            head_sha=HEAD_1,
            provider="codex",
        ),
        "requestCommentId": 98765,
        "requestedAt": "2026-08-24T22:15:00Z",
    }
    active[invalid_binding] = (
        "untrusted-provider" if invalid_binding == "provider" else "different-owner-request"
    )
    payload["activeReviewRequest"] = active
    payload["reviewCycles"] = [{"cycle": 1, **active, "status": "requested"}]
    harness = _review_only_harness(
        monkeypatch, readiness=[_review_only_completed(HEAD_1)]
    )

    result = await MoonMindMergeAutomationWorkflow().run(payload)

    assert result["status"] in {"blocked", "failed"}
    assert harness.request_payloads == []
    assert not any(
        cycle["status"] == "completed" for cycle in result["reviewLoop"]["cycleRecords"]
    )
    _assert_review_only_has_no_resolver(harness, result)


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_completion", ["missing_kind", "before_request"])
async def test_review_only_restored_completed_cycle_requires_fresh_completion(
    monkeypatch, invalid_completion
):
    """Restoration must enforce the same completion evidence as a live request."""
    payload = _review_only_payload()
    cycle = {
        "cycle": 1,
        "provider": "codex",
        "headSha": HEAD_1,
        "requestKey": build_review_request_key(
            parent_workflow_id=MERGE_AUTOMATION_WORKFLOW_ID,
            repository="MoonLadderStudios/MoonMind",
            pr_number=350,
            head_sha=HEAD_1,
            provider="codex",
        ),
        "requestCommentId": 98765,
        "requestedAt": "2026-08-24T22:15:00Z",
        "completionKind": "review",
        "completionId": 45678,
        "completedAt": "2026-08-24T22:19:00Z",
        "status": "completed",
    }
    if invalid_completion == "missing_kind":
        cycle.pop("completionKind")
    else:
        cycle["completedAt"] = "2026-08-24T22:14:00Z"
    payload["reviewCycles"] = [cycle]
    payload["mergeAutomationConfig"]["timeouts"]["expireAfterSeconds"] = 120
    harness = _review_only_harness(
        monkeypatch,
        readiness=[_awaiting_review(HEAD_1) for _ in range(3)],
    )

    result = await MoonMindMergeAutomationWorkflow().run(payload)

    assert result["status"] in {"blocked", "expired"}
    assert harness.request_payloads == []
    assert result["latestHeadSha"] == HEAD_1
    _assert_review_only_has_no_resolver(harness, result)


@pytest.mark.asyncio
async def test_review_only_restored_completion_waits_for_live_pr_observation(
    monkeypatch,
):
    """Saved completion cannot prove the target head is still open during an outage."""
    payload = _review_only_payload()
    payload["reviewCycles"] = [
        {
            "cycle": 1,
            "provider": "codex",
            "headSha": HEAD_1,
            "requestKey": build_review_request_key(
                parent_workflow_id=MERGE_AUTOMATION_WORKFLOW_ID,
                repository="MoonLadderStudios/MoonMind",
                pr_number=350,
                head_sha=HEAD_1,
                provider="codex",
            ),
            "requestCommentId": 98765,
            "requestedAt": "2026-08-24T22:15:00Z",
            "completionKind": "review",
            "completionId": 45678,
            "completedAt": "2026-08-24T22:19:00Z",
            "status": "completed",
        }
    ]
    harness = _review_only_harness(
        monkeypatch,
        readiness=[
            _awaiting_review(
                HEAD_1,
                pullRequestOpen=None,
                automatedReviewComplete=None,
                blockers=[
                    {
                        "kind": "external_state_unavailable",
                        "summary": "GitHub pull request state could not be fetched (HTTP 503).",
                        "retryable": True,
                        "source": "github",
                    }
                ],
            ),
            _ready(HEAD_1),
        ],
    )

    result = await MoonMindMergeAutomationWorkflow().run(payload)

    assert result["status"] == "review_complete"
    assert len(harness.readiness_payloads) == 2
    assert harness.wait_calls == 1
    assert harness.request_payloads == []
    _assert_review_only_has_no_resolver(harness, result)
