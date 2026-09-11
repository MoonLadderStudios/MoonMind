"""Workflow-boundary tests for pr-resolver verdict routing (#4223)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from moonmind.workflows.temporal.workflows import (
    merge_automation as merge_automation_module,
)
from moonmind.workflows.temporal.workflows import run as run_workflow_module
from moonmind.workflows.temporal.workflows.merge_automation import (
    MoonMindMergeAutomationWorkflow,
)

MERGE_AUTOMATION_WORKFLOW_ID = "merge-automation:wf-parent"
OWNER_RUN_ID = "owner-run"
HEAD_1 = "abc1234"
HEAD_2 = "def4567"


def _payload() -> dict[str, Any]:
    return {
        "workflowType": "MoonMind.MergeAutomation",
        "parentWorkflowId": "wf-parent",
        "parentRunId": "run-parent",
        "publishContextRef": "artifact://publish-context",
        "pullRequest": {
            "repo": "MoonLadderStudios/MoonMind",
            "number": 4207,
            "url": "https://github.com/MoonLadderStudios/MoonMind/pull/4207",
            "headSha": HEAD_1,
            "headBranch": "feature",
            "baseBranch": "main",
        },
        "jiraIssueKey": "MM-4207",
        "mergeAutomationConfig": {
            "gate": {
                "github": {"checks": "required", "automatedReview": "required"},
                "jira": {"status": "optional"},
            },
            "resolver": {"mergeMethod": "squash"},
            "timeouts": {"fallbackPollSeconds": 60},
        },
        "idempotencyKey": "merge-automation:wf-parent:MoonLadderStudios/MoonMind:4207:abc1234",
    }


def _ready(head_sha: str) -> dict[str, Any]:
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


def _blocked_ci_failures_result(head_sha: str) -> dict[str, Any]:
    """Mirror var/pr_resolver/result.json blocked shape (art_01M264P832...)."""
    return {
        "status": "success",
        "mergeAutomationDisposition": "manual_review",
        "headSha": head_sha,
        "prResolverStatus": "blocked",
        "prResolverReason": "ci_failures",
        "final_reason": "ci_failures",
        "prResolverNextStep": "run_full_remediation",
        "next_step": "run_full_remediation",
        "terminalContractEvidenceRef": "art_01M264P832WTY8ZFRVH9AX88E3",
        "summary": (
            "pr-resolver reported status 'blocked'; ci_failures; "
            "next_step=run_full_remediation"
        ),
    }


def _reenter_gate_result(child_workflow_id: str, head_sha: str) -> dict[str, Any]:
    """Mirror var/pr_resolver/result.json reenter_gate shape (art_01M25S1A...)."""
    return {
        "status": "success",
        "completionDisposition": "gated_continuation",
        "mergeAutomationDisposition": "reenter_gate",
        "headSha": head_sha,
        "executionRef": "step:1",
        "childRunId": "child-run",
        "gatedContinuation": {
            "schemaVersion": "gated-continuation/v1",
            "gateType": "merge_automation",
            "action": "reenter_gate",
            "reason": "codex_review_grace_wait",
            "retryAfterSeconds": 1,
            "executionRef": "step:1",
            "headSha": head_sha,
            "ownerWorkflowId": MERGE_AUTOMATION_WORKFLOW_ID,
            "ownerRunId": OWNER_RUN_ID,
            "ownerWorkflowType": "MoonMind.MergeAutomation",
            "childWorkflowId": child_workflow_id,
            "childRunId": "child-run",
        },
    }


class _Harness:
    def __init__(
        self,
        monkeypatch: pytest.MonkeyPatch,
        *,
        readiness: list[dict[str, Any]],
        child_results,
    ) -> None:
        self.readiness = list(readiness)
        self.child_results = child_results
        self.child_workflow_ids: list[str] = []
        self.child_payloads: list[dict[str, Any]] = []
        self.resolver_ids: list[str] = []
        self.remediation_ids: list[str] = []
        self.sleep_seconds: list[float] = []
        self._now = datetime(2026, 9, 10, 11, 20, tzinfo=timezone.utc)

        async def fake_execute_activity(
            activity_type: str,
            payload: dict[str, Any],
            **_kwargs: Any,
        ) -> Any:
            if activity_type == "merge_automation.evaluate_readiness":
                return self.readiness.pop(0) if self.readiness else _ready(HEAD_1)
            if activity_type == "artifact.create":
                return ({"artifact_id": "art-test"}, {})
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
            child_id = str(kwargs["id"])
            self.child_workflow_ids.append(child_id)
            self.child_payloads.append(payload)
            skill_id = ""
            try:
                skill_id = str(
                    payload.get("initial_parameters", {})
                    .get("task", {})
                    .get("skill", {})
                    .get("id")
                    or ""
                )
            except AttributeError:
                skill_id = ""
            if skill_id == "remediate-issue" or child_id.endswith(":remediation"):
                self.remediation_ids.append(child_id)
                result = self.child_results
                if callable(result):
                    return result(child_id, len(self.child_workflow_ids), "remediation")
                return {"status": "success", "headSha": HEAD_1}
            self.resolver_ids.append(child_id)
            if callable(self.child_results):
                return self.child_results(
                    child_id, len(self.resolver_ids), "resolver"
                )
            return self.child_results.pop(0)

        async def fake_wait_condition(*_args: Any, **_kwargs: Any) -> None:
            self._now = self._now + timedelta(seconds=60)

        async def fake_sleep(delay: timedelta, **_kwargs: Any) -> None:
            self.sleep_seconds.append(delay.total_seconds())

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
            merge_automation_module.workflow, "patched", lambda _patch_id: True
        )


@pytest.fixture(autouse=True)
def _default_patch_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        merge_automation_module.workflow, "patched", lambda _patch_id: True
    )
    monkeypatch.setattr(
        merge_automation_module.workflow,
        "info",
        lambda: SimpleNamespace(
            workflow_id=MERGE_AUTOMATION_WORKFLOW_ID, run_id=OWNER_RUN_ID
        ),
    )


def test_pr_resolver_verdict_projects_skill_facts_without_reinterpretation() -> None:
    verdict = MoonMindMergeAutomationWorkflow._pr_resolver_verdict(
        _blocked_ci_failures_result(HEAD_1)
    )
    assert verdict["prResolverStatus"] == "blocked"
    assert verdict["finalReason"] == "ci_failures"
    assert verdict["nextStep"] == "run_full_remediation"
    assert verdict["headSha"] == HEAD_1
    assert verdict["terminalContractEvidenceRef"] == "art_01M264P832WTY8ZFRVH9AX88E3"


def test_resolver_verdict_signature_bounds_identical_head_reason() -> None:
    first = MoonMindMergeAutomationWorkflow._pr_resolver_verdict(
        _blocked_ci_failures_result(HEAD_1)
    )
    second = MoonMindMergeAutomationWorkflow._pr_resolver_verdict(
        _blocked_ci_failures_result(HEAD_1)
    )
    assert (
        MoonMindMergeAutomationWorkflow._resolver_verdict_signature(first)
        == MoonMindMergeAutomationWorkflow._resolver_verdict_signature(second)
    )
    moved = MoonMindMergeAutomationWorkflow._pr_resolver_verdict(
        _blocked_ci_failures_result(HEAD_2)
    )
    assert (
        MoonMindMergeAutomationWorkflow._resolver_verdict_signature(first)
        != MoonMindMergeAutomationWorkflow._resolver_verdict_signature(moved)
    )


@pytest.mark.asyncio
async def test_blocked_ci_failures_runs_one_remediation_then_stops_on_repeat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def child_results(child_id: str, resolver_n: int, kind: str) -> dict[str, Any]:
        if kind == "remediation":
            return {"status": "success", "headSha": HEAD_1}
        return _blocked_ci_failures_result(HEAD_1)

    harness = _Harness(
        monkeypatch,
        readiness=[_ready(HEAD_1), _ready(HEAD_1), _ready(HEAD_1)],
        child_results=child_results,
    )
    result = await MoonMindMergeAutomationWorkflow().run(_payload())

    assert result["status"] == "failed"
    assert "ci_failures" in result["summary"]
    assert "run_full_remediation" in result["summary"]
    # One remediation step and one re-entry: two resolver children total.
    assert len(harness.resolver_ids) == 2
    assert len(harness.remediation_ids) == 1
    # The remediation child carries the Skill evidence as remaining work.
    remediation_payload = next(
        payload
        for payload, child_id in zip(
            harness.child_payloads, harness.child_workflow_ids
        )
        if child_id in harness.remediation_ids
    )
    skill_args = remediation_payload["initial_parameters"]["task"]["skill"]["args"]
    assert skill_args["remainingWorkPath"] == "art_01M264P832WTY8ZFRVH9AX88E3"
    assert skill_args["finalReason"] == "ci_failures"
    # Materializable verifier refs use the artifact:// scheme for the runtime.
    assert (
        skill_args["gateResultRef"] == "artifact://art_01M264P832WTY8ZFRVH9AX88E3"
    )
    assert (
        skill_args["remainingWorkRef"] == "artifact://art_01M264P832WTY8ZFRVH9AX88E3"
    )
    assert (
        remediation_payload["initial_parameters"]["gateResultRef"]
        == "artifact://art_01M264P832WTY8ZFRVH9AX88E3"
    )
    assert (
        remediation_payload["initial_parameters"]["remainingWorkRef"]
        == "artifact://art_01M264P832WTY8ZFRVH9AX88E3"
    )
    # The remediation child runs in the authoritative PR-head workspace with
    # a workflow-owned publication handoff.
    workspace_spec = remediation_payload["initial_parameters"]["workspaceSpec"]
    assert workspace_spec["repository"] == "MoonLadderStudios/MoonMind"
    assert workspace_spec["branch"] == "feature"
    assert workspace_spec["targetBranch"] == "feature"
    assert remediation_payload["initial_parameters"]["publishMode"] == "auto"
    # Per-cycle record lists head/reason/nextStep and the stop reason.
    cycles = result["resolverVerdictCycles"]
    assert len(cycles) == 2
    for cycle in cycles:
        assert cycle["headSha"] == HEAD_1
        assert cycle["finalReason"] == "ci_failures"
        assert cycle["nextStep"] == "run_full_remediation"
    assert result["resolverVerdictStopReason"] == "identical_verdict_head_no_progress"
    assert cycles[-1]["stopReason"] == "identical_verdict_head_no_progress"


@pytest.mark.asyncio
async def test_retry_finalize_after_backoff_waits_without_launching_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"resolvers": 0}

    def child_results(child_id: str, resolver_n: int, kind: str) -> dict[str, Any]:
        assert kind == "resolver"
        calls["resolvers"] += 1
        if calls["resolvers"] == 1:
            return {
                "status": "success",
                "mergeAutomationDisposition": "manual_review",
                "headSha": HEAD_1,
                "prResolverStatus": "blocked",
                "final_reason": "snapshot_refresh_failed",
                "next_step": "retry_finalize_after_backoff",
                "retryAfterSeconds": 90,
                "summary": "pr-resolver reported status 'blocked'; "
                "snapshot_refresh_failed; "
                "next_step=retry_finalize_after_backoff",
            }
        return {"status": "success", "mergeAutomationDisposition": "merged"}

    harness = _Harness(
        monkeypatch,
        readiness=[_ready(HEAD_1), _ready(HEAD_1)],
        child_results=child_results,
    )
    result = await MoonMindMergeAutomationWorkflow().run(_payload())

    assert result["status"] == "merged"
    assert harness.sleep_seconds == [90]
    assert harness.remediation_ids == []
    assert len(harness.resolver_ids) == 2


@pytest.mark.asyncio
async def test_reenter_gate_handoff_unchanged_for_cycle_1_history_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def child_results(child_id: str, resolver_n: int, kind: str) -> dict[str, Any]:
        assert kind == "resolver"
        if resolver_n == 1:
            return _reenter_gate_result(child_id, HEAD_1)
        return {"status": "success", "mergeAutomationDisposition": "merged"}

    harness = _Harness(
        monkeypatch,
        readiness=[_ready(HEAD_1), _ready(HEAD_2)],
        child_results=child_results,
    )
    result = await MoonMindMergeAutomationWorkflow().run(_payload())

    assert result["status"] == "merged"
    assert result["cycles"] == 2
    assert result.get("resolverVerdictCycles") == []
    assert harness.remediation_ids == []
    assert harness.sleep_seconds == [1]


@pytest.mark.asyncio
async def test_manual_review_without_verdict_facts_keeps_legacy_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _Harness(
        monkeypatch,
        readiness=[_ready(HEAD_1)],
        child_results=[
            {"status": "success", "mergeAutomationDisposition": "manual_review"}
        ],
    )
    result = await MoonMindMergeAutomationWorkflow().run(_payload())

    assert result["status"] == "failed"
    assert result["summary"] == "pr-resolver requested manual review."
    assert harness.remediation_ids == []


def test_run_workflow_projects_pr_resolver_verdict_for_owning_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        run_workflow_module.workflow, "patched", lambda _patch_id: True
    )
    workflow = run_workflow_module.MoonMindRunWorkflow()
    outputs = {
        "summary": (
            "pr-resolver reported status 'blocked'; ci_failures; "
            "next_step=run_full_remediation"
        ),
        "mergeAutomationDisposition": "manual_review",
        "terminalContractId": "pr_resolver_terminal.v1",
        "prResolverStatus": "blocked",
        "prResolverReason": "ci_failures",
        "prResolverNextStep": "run_full_remediation",
        "terminalContractEvidenceRef": "art_01M264P832WTY8ZFRVH9AX88E3",
        "headSha": HEAD_1,
    }
    workflow._record_execution_context(
        node_id="node-1", execution_result={"outputs": outputs}
    )
    assert workflow._pr_resolver_status == "blocked"
    assert workflow._pr_resolver_reason == "ci_failures"
    assert workflow._pr_resolver_next_step == "run_full_remediation"
    assert (
        workflow._terminal_contract_evidence_ref
        == "art_01M264P832WTY8ZFRVH9AX88E3"
    )
    assert workflow._merge_automation_disposition == "manual_review"
    assert workflow._merge_automation_head_sha == HEAD_1


def test_remediation_artifact_ref_normalization() -> None:
    workflow_obj = MoonMindMergeAutomationWorkflow()
    assert (
        workflow_obj._normalize_remediation_artifact_ref(
            "art_01M264P832WTY8ZFRVH9AX88E3"
        )
        == "artifact://art_01M264P832WTY8ZFRVH9AX88E3"
    )
    assert (
        workflow_obj._normalize_remediation_artifact_ref(
            "artifact://art_01M264P832WTY8ZFRVH9AX88E3"
        )
        == "artifact://art_01M264P832WTY8ZFRVH9AX88E3"
    )
    assert workflow_obj._normalize_remediation_artifact_ref("") == ""
    assert workflow_obj._normalize_remediation_artifact_ref(None) == ""


def test_run_workflow_skips_verdict_mutation_without_patch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        run_workflow_module.workflow, "patched", lambda _patch_id: False
    )
    workflow = run_workflow_module.MoonMindRunWorkflow()
    outputs = {
        "mergeAutomationDisposition": "manual_review",
        "terminalContractId": "pr_resolver_terminal.v1",
        "prResolverStatus": "blocked",
        "prResolverReason": "ci_failures",
        "prResolverNextStep": "run_full_remediation",
        "terminalContractEvidenceRef": "art_01M264P832WTY8ZFRVH9AX88E3",
        "headSha": HEAD_1,
    }
    workflow._record_execution_context(
        node_id="node-1", execution_result={"outputs": outputs}
    )
    # Old histories replay without the new verdict-state mutation.
    assert workflow._pr_resolver_status is None
    assert workflow._pr_resolver_reason is None
    assert workflow._pr_resolver_next_step is None
    assert workflow._terminal_contract_evidence_ref is None
    assert "prResolverStatus" not in workflow._publish_context
