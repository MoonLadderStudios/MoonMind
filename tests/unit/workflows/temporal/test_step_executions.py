from __future__ import annotations

from datetime import UTC, datetime

from moonmind.schemas.step_execution_models import STEP_EXECUTION_CONTENT_TYPE
from moonmind.workflows.temporal.step_executions import (
    build_step_execution_manifest_payload,
    git_effect_metadata,
    logical_step_success_allowed,
    side_effect_record,
    step_execution_id,
    step_execution_operation_idempotency_key,
    validate_workspace_policy_launch,
    workspace_policy_metadata,
)


def test_step_execution_id_uses_run_scoped_identity() -> None:
    assert (
        step_execution_id(
            workflow_id="wf-1",
            run_id="run-1",
            logical_step_id="implement",
            attempt=2,
        )
        == "wf-1:run-1:implement:execution:2"
    )


def test_operation_idempotency_key_includes_execution_ordinal_and_operation() -> None:
    key = step_execution_operation_idempotency_key(
        workflow_id="wf-1",
        run_id="run-1",
        logical_step_id="implement",
        attempt=2,
        operation="execute",
    )

    assert key == "wf-1:run-1:implement:execution:2:execute"
    assert key != step_execution_operation_idempotency_key(
        workflow_id="wf-1",
        run_id="run-1",
        logical_step_id="implement",
        attempt=3,
        operation="execute",
    )
    assert key != step_execution_operation_idempotency_key(
        workflow_id="wf-1",
        run_id="run-1",
        logical_step_id="implement",
        attempt=2,
        operation="manifest_write",
    )


def test_manifest_payload_is_compact_boundary_contract() -> None:
    now = datetime(2026, 5, 17, 12, 0, tzinfo=UTC)
    payload = build_step_execution_manifest_payload(
        workflow_id="wf-1",
        run_id="run-1",
        logical_step_id="implement",
        attempt=1,
        reason="initial_execution",
        status="running",
        updated_at=now,
        summary="Executing plan step",
        execution={
            "kind": "skill",
            "idempotencyKey": "wf-1:run-1:implement:execution:1:execute",
        },
        input_refs=["artifact://input"],
    )

    assert payload["contentType"] == STEP_EXECUTION_CONTENT_TYPE
    assert payload["stepExecutionId"] == "wf-1:run-1:implement:execution:1"
    assert payload["input"] == {"preparedInputRefs": ["artifact://input"]}
    assert payload["execution"]["kind"] == "skill"
    assert payload["outputs"] == {"summary": "Executing plan step"}


def test_workspace_policy_metadata_rejects_missing_required_checkpoint() -> None:
    decision = validate_workspace_policy_launch(
        policy="apply_previous_execution_diff_to_clean_baseline",
    )

    assert decision["allowed"] is False
    assert decision["reason"] == "missing_required_checkpoint_evidence"
    assert decision["workspace"]["policy"] == "apply_previous_execution_diff_to_clean_baseline"
    assert decision["workspace"]["requiredCheckpointKinds"] == ["git_patch"]
    assert decision["workspace"]["evidenceAccepted"] is False

    fresh = workspace_policy_metadata(policy="fresh_branch_from_source")
    assert fresh["evidenceRequired"] is False
    assert fresh["evidenceAccepted"] is True


def test_git_effect_metadata_enforces_accepted_output_rule() -> None:
    candidate = git_effect_metadata(
        disposition="candidate",
        working_tree_diff_ref="artifact://diff",
        patch_ref="artifact://patch",
    )

    assert candidate["disposition"] == "candidate"
    assert candidate["acceptedOutputPresent"] is False

    accepted = git_effect_metadata(
        disposition="accepted",
        head_commit="abc123",
        workspace_checkpoint_ref="artifact://checkpoint",
    )
    assert accepted["acceptedOutputPresent"] is True
    assert logical_step_success_allowed(git_effect=accepted) is True

    no_change = git_effect_metadata(
        disposition="accepted",
        no_change_accepted=True,
    )
    assert no_change["acceptedOutputPresent"] is True

    try:
        git_effect_metadata(disposition="accepted", patch_ref="artifact://patch")
    except ValueError as exc:
        assert "accepted git effect requires" in str(exc)
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("accepted dirty workspace without accepted output was allowed")


def test_side_effect_records_gate_external_effects() -> None:
    blocked = side_effect_record(
        effect_class="publication",
        operation="repo.merge_pr",
        target="PR-1",
        workflow_state_accepted=False,
    )

    assert blocked["disposition"] == "blocked"
    assert blocked["reason"] == "workflow_state_not_gate_approved"

    accepted = side_effect_record(
        effect_class="publication",
        operation="repo.merge_pr",
        target="PR-1",
        workflow_state_accepted=True,
    )
    assert accepted["disposition"] == "accepted"

    idempotent = side_effect_record(
        effect_class="external_idempotent",
        operation="jira.add_comment",
        target="MM-717",
        idempotency_key="wf:run:step:execution:comment",
    )
    assert idempotent["idempotencyKey"] == "wf:run:step:execution:comment"

    jira_transition = side_effect_record(
        effect_class="external_idempotent",
        operation="jira.transition_issue",
        target="MM-717",
        idempotency_key="wf:run:step:execution:jira-transition",
        workflow_state_accepted=False,
    )
    assert jira_transition["disposition"] == "blocked"

    cleanup = side_effect_record(
        effect_class="external_idempotent",
        effect_kind="cleanup",
        operation="deployment.cleanup",
        target="deploy-1",
        idempotency_key="wf:run:step:execution:cleanup",
        workflow_state_accepted=True,
    )
    assert cleanup["kind"] == "cleanup"
    assert cleanup["disposition"] == "accepted"


def test_side_effect_records_sanitize_diagnostics_and_gate_workspace_writes() -> None:
    outside_workspace = side_effect_record(
        effect_class="workspace_mutation",
        operation="workspace.write",
        target="/tmp/outside-workspace/token=ghp_leakedsecret",
        approved_workspace_roots=["/work/agent_jobs/run/repo"],
        reason="password=hunter2",
    )

    assert outside_workspace["disposition"] == "blocked"
    assert outside_workspace["reason"] == "workspace_target_outside_approved_roots"
    assert "ghp_leakedsecret" not in str(outside_workspace)
    assert "hunter2" not in str(outside_workspace)

    approved_workspace = side_effect_record(
        effect_class="workspace_mutation",
        operation="workspace.write",
        target="/work/agent_jobs/run/repo/moonmind/workflows/temporal/run.py",
        approved_workspace_roots=["/work/agent_jobs/run/repo"],
    )

    assert approved_workspace["disposition"] == "accepted"
    assert approved_workspace["target"].endswith(
        "/moonmind/workflows/temporal/run.py"
    )


def test_manifest_payload_embeds_policy_git_effect_and_side_effect_records() -> None:
    now = datetime(2026, 5, 17, 12, 0, tzinfo=UTC)
    payload = build_step_execution_manifest_payload(
        workflow_id="wf-1",
        run_id="run-1",
        logical_step_id="implement",
        attempt=2,
        reason="tests_failed",
        status="failed",
        updated_at=now,
        workspace=workspace_policy_metadata(
            policy="continue_from_previous_execution",
            checkpoint_ref="artifact://checkpoint",
            checkpoint_valid=True,
        ),
        git_effect=git_effect_metadata(
            disposition="candidate",
            working_tree_diff_ref="artifact://diff",
            patch_ref="artifact://patch",
            workspace_checkpoint_ref="artifact://checkpoint",
        ),
        side_effect_records=[
            side_effect_record(
                effect_class="publication",
                operation="repo.merge_pr",
                workflow_state_accepted=False,
            )
        ],
    )

    assert payload["workspace"]["policy"] == "continue_from_previous_execution"
    assert payload["workspace"]["gitEffect"]["disposition"] == "candidate"
    assert payload["sideEffects"]["records"][0]["disposition"] == "blocked"
