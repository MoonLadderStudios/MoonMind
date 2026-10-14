"""Delayed evidence resumes the existing publication owner, never a fresh issue."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from moonmind.workflows.temporal.workflows import run as run_module


@pytest.fixture
def owner(monkeypatch):
    monkeypatch.setattr(run_module.workflow, "patched", lambda _: True)
    monkeypatch.setattr(
        run_module.workflow, "now", lambda: datetime(2026, 10, 14, tzinfo=timezone.utc)
    )
    monkeypatch.setattr(
        run_module.workflow,
        "info",
        lambda: SimpleNamespace(workflow_id="issue-owner", run_id="run-1"),
    )
    parent = run_module.MoonMindRunWorkflow()
    parent._repo = "example/repo"
    parent._assessment_context = {
        "assessmentArtifactRef": "artifact://assessment",
        "briefArtifactRef": "artifact://brief",
    }
    parent._moonspec_draft_publication_reason = "Exact-head CI proof was pending"
    parent._moonspec_gate_verdict = "BLOCKED"
    parent._publish_context = {
        "headSha": "a" * 40,
        "branch": "issue-2740",
        "baseRef": "main",
        "pullRequestUrl": "https://github.com/example/repo/pull/2",
        "moonSpecGate": {
            "verdict": "BLOCKED",
            "recommendedNextAction": "blocked",
            "gateResultRef": "artifact://old-gate",
        },
    }
    parent._write_json_artifact = AsyncMock(
        return_value="artifact://continuation-decision"
    )
    parent._update_memo = lambda: None
    parent._update_search_attributes = lambda: None
    return parent


def test_verifier_scope_refs_come_from_trusted_owner(owner):
    params = {
        "assessmentArtifactRef": "artifact://forged",
        "briefArtifactRef": "artifact://forged",
    }
    owner._ensure_moonspec_verify_parameters(
        parameters=params, node_inputs={}, node_id="verify"
    )
    assert params["assessmentArtifactRef"] == "artifact://assessment"
    assert params["briefArtifactRef"] == "artifact://brief"
    assert params["acceptanceScopeContract"] == "source-bound/v1"


@pytest.mark.asyncio
async def test_explicit_human_stop_does_not_collect_or_resume(owner, monkeypatch):
    owner._publish_context["moonSpecGate"]["recommendedNextAction"] = "needs_human"
    activity = AsyncMock()
    monkeypatch.setattr(run_module.workflow, "execute_activity", activity)
    await owner._resume_published_issue_evidence(
        parameters={"publishMode": "pr"}, ordered_nodes=[]
    )
    activity.assert_not_awaited()
    assert owner._moonspec_draft_publication_reason


@pytest.mark.asyncio
async def test_retained_history_does_not_gain_delayed_artifact_commands(
    owner, monkeypatch
):
    monkeypatch.setattr(run_module.workflow, "patched", lambda _: False)
    activity = AsyncMock()
    monkeypatch.setattr(run_module.workflow, "execute_activity", activity)
    await owner._resume_published_issue_evidence(
        parameters={"publishMode": "pr"}, ordered_nodes=[]
    )
    activity.assert_not_awaited()


@pytest.fixture
def admitted(owner):
    from tests.unit.workflows.temporal.test_remediation_loop import _spec, _state

    owner._remediation_loop_spec = _spec()
    owner._remediation_loop_state = _state()
    owner._publish_context["moonSpecDraftPublication"] = {
        "policy": "draft_pr_on_environment_blocked"
    }
    owner._wait_if_paused_at_safe_boundary = AsyncMock()
    owner._sync_remediation_loop_projection = lambda: None
    return owner, [
        {
            "id": "verify",
            "annotations": {"issueImplementRole": "moonspec-verification-gate"},
            "tool": {"type": "agent_runtime", "name": "codex_cli"},
            "inputs": {
                "selectedSkill": "moonspec-verify",
                "instructions": "Verify original source",
            },
        }
    ]


@pytest.mark.asyncio
async def test_new_exact_head_bytes_resume_verifier_then_persist_acceptance(
    admitted, monkeypatch
):
    from moonmind.workflows.skills.approval_policy import StepGateResult
    from moonmind.workflows.temporal.remediation_loop import RemediationLoopPhase
    from moonmind.workflows.temporal.workflows import (
        issue_evidence_continuation as continuation,
    )

    owner, nodes = admitted
    subject = {
        "repository": "example/repo",
        "revision": "a" * 40,
        "contentDigest": "tree",
    }
    observed = {
        "status": "ready",
        "headSha": "a" * 40,
        "subject": subject,
        "semanticDigest": "sha256:bytes",
        "evidenceSetDigest": "sha256:inventory",
        "manifestRef": "artifact://manifest",
        "evidenceRefs": ["artifact://proof"],
        "runIds": [12],
    }
    reader = AsyncMock(
        side_effect=[
            observed,
            {"status": "ready", "headSha": "a" * 40},
            {"status": "ready", "headSha": "a" * 40},
        ]
    )
    monkeypatch.setattr(continuation, "_activity", reader)
    verifier = AsyncMock(return_value={"gateResultRef": "artifact://new-gate"})
    monkeypatch.setattr(continuation, "_verify", verifier)
    gate = StepGateResult(
        verdict="FULLY_IMPLEMENTED",
        recommended_next_action="advance",
        validated_refs={
            "acceptance": {"subject": subject, "evidenceSetDigest": "sha256:inventory"}
        },
    )
    monkeypatch.setattr(owner, "_moonspec_verify_gate_result", lambda _: gate)
    monkeypatch.setattr(
        owner, "_moonspec_verify_gate_result_ref", lambda _: "artifact://new-gate"
    )
    monkeypatch.setattr(owner, "_record_moonspec_verify_gate", lambda **_: None)
    await owner._resume_published_issue_evidence(
        parameters={"publishMode": "pr"}, ordered_nodes=nodes
    )
    verifier.assert_awaited_once()
    assert reader.await_args_list[-1].args[1]["action"] == "mark_ready"
    assert owner._remediation_loop_state.phase == RemediationLoopPhase.ACCEPTED
    assert owner._remediation_loop_state.consumed_budgets.attempts == 0
    assert owner._remediation_loop_state.consumed_budgets.evidence_retries == 1
    assert owner._moonspec_draft_publication_reason is None
    assert owner._publish_context["issueEvidenceContinuation"]["status"] == "accepted"


@pytest.mark.asyncio
async def test_moved_head_never_launches_verifier(admitted, monkeypatch):
    from moonmind.workflows.temporal.workflows import (
        issue_evidence_continuation as continuation,
    )

    owner, nodes = admitted
    monkeypatch.setattr(
        continuation,
        "_activity",
        AsyncMock(return_value={"status": "moved", "headSha": "b" * 40}),
    )
    verify = AsyncMock()
    monkeypatch.setattr(continuation, "_verify", verify)
    await owner._resume_published_issue_evidence(
        parameters={"publishMode": "pr"}, ordered_nodes=nodes
    )
    verify.assert_not_awaited()
    assert (
        owner._publish_context["issueEvidenceContinuation"]["nextAction"]
        == "reconcile-candidate"
    )
    assert owner._remediation_loop_state.consumed_budgets.attempts == 0


@pytest.mark.asyncio
async def test_pending_ci_wait_does_not_spend_implementation_or_evidence_attempt(
    admitted, monkeypatch
):
    from moonmind.workflows.temporal.workflows import (
        issue_evidence_continuation as continuation,
    )

    owner, nodes = admitted
    monkeypatch.setattr(
        continuation,
        "_activity",
        AsyncMock(
            return_value={"status": "pending", "headSha": "a" * 40, "runIds": [12]}
        ),
    )

    async def cancel_while_waiting(*_, **__):
        owner._cancel_requested = True

    monkeypatch.setattr(run_module.workflow, "wait_condition", cancel_while_waiting)
    verify = AsyncMock()
    monkeypatch.setattr(continuation, "_verify", verify)
    await owner._resume_published_issue_evidence(
        parameters={"publishMode": "pr"}, ordered_nodes=nodes
    )
    verify.assert_not_awaited()
    assert owner._remediation_loop_state.consumed_budgets.attempts == 0
    assert owner._remediation_loop_state.consumed_budgets.evidence_retries == 0


@pytest.mark.asyncio
async def test_actual_verifier_request_is_read_only_exact_head_and_materializes_bytes(
    admitted, monkeypatch
):
    from moonmind.workflows.temporal.workflows import (
        issue_evidence_continuation as continuation,
    )

    owner, nodes = admitted
    monkeypatch.setattr(
        run_module.workflow,
        "info",
        lambda: SimpleNamespace(
            workflow_id="issue-owner",
            run_id="run-1",
            namespace="default",
            task_queue="workflow",
            start_time=datetime(2026, 10, 14, tzinfo=timezone.utc),
        ),
    )
    monkeypatch.setattr(
        owner,
        "_resolve_agent_node_skillset_ref",
        AsyncMock(return_value="artifact://skillset"),
    )
    monkeypatch.setattr(owner, "_workflow_child_task_queue", lambda: "workflow")
    child = AsyncMock(return_value={"metadata": {"proof": "observed"}})
    monkeypatch.setattr(run_module.workflow, "execute_child_workflow", child)
    nodes[0]["inputs"]["runtime"] = {"mode": "codex_cli"}
    observation = {
        "headSha": "a" * 40,
        "subject": {
            "repository": "example/repo",
            "revision": "a" * 40,
            "contentDigest": "tree",
        },
        "artifactRef": "artifact://manifest",
        "evidenceRefs": ["artifact://proof"],
        "evidenceSet": {"artifact://proof": "sha256:" + "1" * 64},
        "evidenceSetDigest": "sha256:" + "2" * 64,
    }
    result = await continuation._verify(
        owner,
        node=nodes[0],
        parameters={"publishMode": "pr"},
        observation=observation,
        ordinal=1,
    )
    assert result["proof"] == "observed"
    request = child.await_args.args[1]
    assert request.parameters["publishMode"] == "none"
    assert request.parameters["repositoryOperation"] == "read"
    assert request.parameters["evidenceSet"] == observation["evidenceSet"]
    assert request.parameters["assessmentArtifactRef"] == "artifact://assessment"
    assert (
        request.workspace_spec["repositoryTarget"]["revision"]["commitSha"] == "a" * 40
    )
    assert {"artifact://manifest", "artifact://proof"}.issubset(request.input_refs)


@pytest.mark.asyncio
async def test_duplicate_byte_inventory_never_dispatches_another_verifier(
    admitted, monkeypatch
):
    from moonmind.workflows.temporal.workflows import (
        issue_evidence_continuation as continuation,
    )

    owner, nodes = admitted
    owner._publish_context["issueEvidenceContinuation"] = {
        "consumedEvidenceDigest": "sha256:same"
    }
    monkeypatch.setattr(
        continuation,
        "_activity",
        AsyncMock(
            return_value={
                "status": "ready",
                "headSha": "a" * 40,
                "semanticDigest": "sha256:same",
                "evidenceSetDigest": "sha256:new-url-only",
            }
        ),
    )

    async def cancel(*_, **__):
        owner._cancel_requested = True

    monkeypatch.setattr(run_module.workflow, "wait_condition", cancel)
    verify = AsyncMock()
    monkeypatch.setattr(continuation, "_verify", verify)
    await owner._resume_published_issue_evidence(
        parameters={"publishMode": "pr"}, ordered_nodes=nodes
    )
    verify.assert_not_awaited()
    assert owner._remediation_loop_state.consumed_budgets.evidence_retries == 0


@pytest.mark.asyncio
async def test_concrete_defect_enters_existing_same_pr_repair_without_budget_reset(
    admitted, monkeypatch
):
    from moonmind.workflows.temporal.workflows import (
        issue_evidence_continuation as continuation,
    )
    from moonmind.workflows.skills.approval_policy import StepGateResult

    owner, nodes = admitted
    owner._remediation_loop_runtime = {"mode": "codex_cli"}
    subject_a = {
        "repository": "example/repo",
        "revision": "a" * 40,
        "contentDigest": "tree-a",
    }
    subject_b = {
        "repository": "example/repo",
        "revision": "b" * 40,
        "contentDigest": "tree-b",
    }

    def evidence(head, subject):
        return {
            "status": "ready",
            "headSha": head,
            "subject": subject,
            "semanticDigest": "sha256:" + head,
            "evidenceSetDigest": "sha256:" + head,
            "evidenceRefs": ["artifact://proof"],
            "runIds": [12],
            "collectionComplete": False,
            "pendingRunIds": [13],
        }

    reader = AsyncMock(
        side_effect=[
            evidence("a" * 40, subject_a),
            {"status": "ready", "headSha": "a" * 40},
            evidence("b" * 40, subject_b),
            {"status": "ready", "headSha": "b" * 40},
            {"status": "ready", "headSha": "b" * 40},
        ]
    )
    monkeypatch.setattr(continuation, "_activity", reader)
    verify = AsyncMock(
        side_effect=[
            {"gateResultRef": "artifact://gap"},
            {"push_status": "pushed"},
            {"gateResultRef": "artifact://pass"},
        ]
    )
    monkeypatch.setattr(continuation, "_verify", verify)
    defect = StepGateResult(
        verdict="ADDITIONAL_WORK_NEEDED",
        recommended_next_action="reattempt_current_step",
        remaining_work_ref="artifact://gap",
        issues=({"requirement": "AC1", "gapType": "implementation"},),
    )
    accepted = StepGateResult(
        verdict="FULLY_IMPLEMENTED",
        recommended_next_action="advance",
        validated_refs={
            "acceptance": {
                "subject": subject_b,
                "evidenceSetDigest": "sha256:" + "b" * 40,
            }
        },
    )
    monkeypatch.setattr(
        owner,
        "_moonspec_verify_gate_result",
        lambda outputs: (
            defect if outputs["gateResultRef"].endswith("gap") else accepted
        ),
    )
    monkeypatch.setattr(
        owner,
        "_moonspec_verify_gate_result_ref",
        lambda outputs: outputs["gateResultRef"],
    )
    monkeypatch.setattr(owner, "_record_moonspec_verify_gate", lambda **_: None)
    monkeypatch.setattr(owner, "_record_publish_result_from_execution", AsyncMock())
    monkeypatch.setattr(
        owner, "_workflow_verified_published_head", lambda: ("issue-2740", "b" * 40)
    )
    await owner._resume_published_issue_evidence(
        parameters={"publishMode": "pr"}, ordered_nodes=nodes
    )
    assert verify.await_count == 3
    assert verify.await_args_list[1].kwargs["read_only"] is False
    assert owner._remediation_loop_state.consumed_budgets.attempts == 1
    assert owner._remediation_loop_state.consumed_budgets.evidence_retries == 1
    assert owner._publish_context["issueEvidenceContinuation"]["headSha"] == "b" * 40
    assert all(call.args[1]["prNumber"] == 2 for call in reader.await_args_list)


@pytest.mark.asyncio
async def test_partial_evidence_waits_for_final_bytes_with_default_one_judgment_budget(
    admitted, monkeypatch
):
    from moonmind.workflows.temporal.workflows import (
        issue_evidence_continuation as continuation,
    )
    from moonmind.workflows.skills.approval_policy import StepGateResult

    owner, nodes = admitted
    subject = {
        "repository": "example/repo",
        "revision": "a" * 40,
        "contentDigest": "tree",
    }

    def observed(digest, complete):
        return {
            "status": "ready",
            "headSha": "a" * 40,
            "subject": subject,
            "semanticDigest": digest,
            "evidenceSetDigest": digest,
            "collectionComplete": complete,
            "pendingRunIds": [] if complete else [13],
            "evidenceRefs": ["artifact://proof"],
        }

    collections = iter(
        [
            observed("sha256:first", False),
            observed("sha256:second", False),
            {
                **observed("sha256:second", False),
                "evidenceSetDigest": "sha256:cosmetic-new-reference",
            },
            observed("sha256:final", True),
        ]
    )

    async def read(_owner, payload):
        if payload.get("action"):
            return {"status": "ready", "headSha": "a" * 40}
        return next(collections)

    monkeypatch.setattr(continuation, "_activity", read)
    wait = AsyncMock(side_effect=TimeoutError)
    monkeypatch.setattr(run_module.workflow, "wait_condition", wait)
    verify = AsyncMock(
        side_effect=[
            {"gateResultRef": "artifact://partial-1"},
            {"gateResultRef": "artifact://partial-2"},
            {"gateResultRef": "artifact://complete"},
        ]
    )
    monkeypatch.setattr(continuation, "_verify", verify)
    partial = StepGateResult(
        verdict="NO_DETERMINATION",
        recommended_next_action="blocked",
        remaining_work_ref="artifact://missing-required-ci",
    )
    accepted = StepGateResult(
        verdict="FULLY_IMPLEMENTED",
        recommended_next_action="advance",
        validated_refs={
            "acceptance": {"subject": subject, "evidenceSetDigest": "sha256:final"}
        },
    )
    monkeypatch.setattr(
        owner,
        "_moonspec_verify_gate_result",
        lambda outputs: (
            accepted if outputs["gateResultRef"].endswith("complete") else partial
        ),
    )
    monkeypatch.setattr(
        owner,
        "_moonspec_verify_gate_result_ref",
        lambda outputs: outputs["gateResultRef"],
    )
    monkeypatch.setattr(owner, "_record_moonspec_verify_gate", lambda **_: None)
    await owner._resume_published_issue_evidence(
        parameters={"publishMode": "pr"}, ordered_nodes=nodes
    )
    assert owner._remediation_loop_spec.budgets.max_evidence_retries == 1
    assert owner._remediation_loop_state.consumed_budgets.evidence_retries == 1
    assert owner._remediation_loop_state.consumed_budgets.attempts == 0
    assert verify.await_count == 3
    assert (
        owner._remediation_loop_state.latest_verification_ref == "artifact://complete"
    )
    wait.assert_awaited_once()
    assert owner._publish_context["issueEvidenceContinuation"]["status"] == "accepted"


@pytest.mark.asyncio
async def test_pending_inventory_stops_at_owner_wall_clock_without_spending_attempts(
    admitted, monkeypatch
):
    from datetime import timedelta
    from moonmind.workflows.temporal.workflows import (
        issue_evidence_continuation as continuation,
    )

    owner, nodes = admitted
    now = datetime(2026, 10, 14, tzinfo=timezone.utc)
    clock = [now]
    owner._remediation_loop_spec = owner._remediation_loop_spec.model_copy(
        update={
            "budgets": owner._remediation_loop_spec.budgets.model_copy(
                update={"max_wall_clock_seconds": 60}
            )
        }
    )
    monkeypatch.setattr(run_module.workflow, "now", lambda: clock[0])
    monkeypatch.setattr(
        run_module.workflow,
        "info",
        lambda: SimpleNamespace(
            workflow_id="issue-owner", run_id="run-1", start_time=now
        ),
    )
    reader = AsyncMock(
        return_value={"status": "pending", "headSha": "a" * 40, "pendingRunIds": [13]}
    )
    monkeypatch.setattr(continuation, "_activity", reader)

    async def wait(*_, **__):
        clock[0] += timedelta(seconds=61)
        raise TimeoutError

    monkeypatch.setattr(run_module.workflow, "wait_condition", wait)
    verify = AsyncMock()
    monkeypatch.setattr(continuation, "_verify", verify)
    await owner._resume_published_issue_evidence(
        parameters={"publishMode": "pr"}, ordered_nodes=nodes
    )
    verify.assert_not_awaited()
    reader.assert_awaited_once()
    assert owner._remediation_loop_state.consumed_budgets.evidence_retries == 0
    assert owner._remediation_loop_state.consumed_budgets.attempts == 0
    assert (
        "budget expired"
        in owner._publish_context["issueEvidenceContinuation"]["summary"]
    )


@pytest.mark.asyncio
async def test_lost_issue_claim_stops_before_collecting_or_dispatching(
    admitted, monkeypatch
):
    from temporalio.exceptions import ApplicationError
    from moonmind.workflows.temporal.workflows import (
        issue_evidence_continuation as continuation,
    )

    owner, nodes = admitted
    owner._trusted_issue_context = {
        "issueClaimLease": {"owner": "issue-owner", "attemptId": "attempt-1"}
    }
    owner._execute_kwargs_for_route = lambda _: {}
    renew = AsyncMock(return_value={"status": "lost"})
    monkeypatch.setattr(run_module.workflow, "execute_activity", renew)
    reader = AsyncMock()
    monkeypatch.setattr(continuation, "_activity", reader)
    verify = AsyncMock()
    monkeypatch.setattr(continuation, "_verify", verify)
    with pytest.raises(ApplicationError, match="claim lease lost"):
        await owner._resume_published_issue_evidence(
            parameters={"publishMode": "pr"}, ordered_nodes=nodes
        )
    reader.assert_not_awaited()
    verify.assert_not_awaited()
    assert owner._remediation_loop_state.consumed_budgets.evidence_retries == 0


def test_real_remaining_work_shrinks_semantic_gap_without_optional_issues(owner):
    from moonmind.workflows.skills.approval_policy import parse_step_gate_result
    from moonmind.workflows.temporal.remediation_loop import record_semantic_progress
    from moonmind.workflows.temporal.workflows.issue_evidence_continuation import (
        _semantic_gap_signature,
    )
    from tests.unit.workflows.temporal.test_remediation_loop import _state

    reports = [
        {
            "verdict": "ADDITIONAL_WORK_NEEDED",
            "recommendedNextAction": "reattempt_current_step",
            "remainingWork": [
                {
                    "requirement": key,
                    "gapType": "implementation",
                    "remainingWork": "Implement required behavior",
                }
                for key in ids
            ],
        }
        for ids in (["AC1", "AC2"], ["AC2"], ["AC2"])
    ]
    state = _state()
    for index, report in enumerate(reports):
        parsed = parse_step_gate_result(report)
        assert not parsed.invalid
        assert parsed.issues == ()
        state = record_semantic_progress(
            state,
            progress_ref=f"artifact://report-{index}",
            progress_signature=_semantic_gap_signature(
                owner, {"moonSpecVerify": report}, parsed
            ),
        )
        assert state.consumed_budgets.consecutive_semantic_no_progress == (
            1 if index == 2 else 0
        )
