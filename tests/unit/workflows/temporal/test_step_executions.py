from __future__ import annotations

from datetime import UTC, datetime

from moonmind.schemas.step_execution_models import (
    STEP_EXECUTION_CONTENT_TYPE,
    StepExecutionManifestModel,
)
from moonmind.workflows.temporal.step_executions import (
    already_occurred_non_idempotent_effects,
    compensation_subject_key,
    external_handoff_gate_decision,
    git_effect_metadata,
    logical_step_success_allowed,
    plan_reattempt_compensation,
    side_effect_record,
    step_execution_id,
    step_execution_operation_idempotency_key,
    terminal_side_effect_manifest,
    validate_workspace_policy_launch,
    workspace_policy_metadata,
)


def test_step_execution_id_uses_run_scoped_identity() -> None:
    assert (
        step_execution_id(
            workflow_id="wf-1",
            run_id="run-1",
            logical_step_id="implement",
            execution_ordinal=2,
        )
        == "wf-1:run-1:implement:execution:2"
    )


def test_operation_idempotency_key_includes_execution_ordinal_and_operation() -> None:
    key = step_execution_operation_idempotency_key(
        workflow_id="wf-1",
        run_id="run-1",
        logical_step_id="implement",
        execution_ordinal=2,
        operation="execute",
    )

    assert key == "wf-1:run-1:implement:execution:2:execute"
    assert key != step_execution_operation_idempotency_key(
        workflow_id="wf-1",
        run_id="run-1",
        logical_step_id="implement",
        execution_ordinal=3,
        operation="execute",
    )
    assert key != step_execution_operation_idempotency_key(
        workflow_id="wf-1",
        run_id="run-1",
        logical_step_id="implement",
        execution_ordinal=2,
        operation="manifest_write",
    )


def test_manifest_payload_is_compact_boundary_contract() -> None:
    now = datetime(2026, 5, 17, 12, 0, tzinfo=UTC)
    manifest = StepExecutionManifestModel(
        workflowId="wf-1",
        runId="run-1",
        logicalStepId="implement",
        executionOrdinal=1,
        reason="initial_execution",
        status="running",
        startedAt=now,
        updatedAt=now,
        execution={
            "kind": "skill",
            "idempotencyKey": "wf-1:run-1:implement:execution:1:execute",
        },
        input={"preparedInputRefs": ["artifact://input"]},
        outputs={"summary": "Executing plan step"},
    )
    payload = manifest.model_dump(by_alias=True, mode="json")

    assert payload["contentType"] == STEP_EXECUTION_CONTENT_TYPE
    assert payload["stepExecutionId"] == "wf-1:run-1:implement:execution:1"
    assert payload["input"] == {"preparedInputRefs": ["artifact://input"]}
    assert payload["execution"]["kind"] == "skill"
    assert payload["outputs"] == {"summary": "Executing plan step"}


def test_manifest_payload_accepts_terminal_disposition_and_effect_matrices() -> None:
    now = datetime(2026, 5, 17, 12, 0, tzinfo=UTC)
    manifest = StepExecutionManifestModel(
        workflowId="wf-1",
        runId="run-1",
        logicalStepId="implement",
        executionOrdinal=1,
        reason="initial_execution",
        status="succeeded",
        terminalDisposition="accepted",
        startedAt=now,
        updatedAt=now,
        input={"preparedInputRefs": ["artifact://input"]},
        context={"bundleRef": "artifact://context"},
        workspace={"policy": "fresh_branch_from_source"},
        execution={"kind": "agent_runtime"},
        outputs={"summaryRef": "artifact://summary"},
        checks=[{"kind": "approval_policy", "status": "passed"}],
        sideEffects={
            "summary": {
                "categories": {
                    "git": 1,
                    "external": 1,
                    "artifact": 1,
                    "publication": 1,
                    "compensation": 1,
                    "memory": 1,
                    "retrieval": 1,
                    "record": 7,
                }
            },
            "records": [
                {"class": "external_idempotent", "kind": "normal", "operation": "jira"},
                {"class": "artifact_write", "kind": "normal", "operation": "artifact"},
                {"class": "publication", "kind": "normal", "operation": "publish"},
                {
                    "class": "external_non_idempotent",
                    "kind": "compensation",
                    "operation": "compensate",
                },
                {"class": "memory_update", "kind": "normal", "operation": "memory"},
                {
                    "class": "retrieval_index_update",
                    "kind": "normal",
                    "operation": "retrieval",
                },
                {"class": "workspace_mutation", "kind": "normal", "operation": "record"},
            ],
        },
        dependencyEffects={"invalidatedLogicalStepIds": ["verify"]},
        budget={"maxReviewAttempts": 2},
    )
    manifest.workspace["gitEffect"] = {
        "disposition": "accepted",
        "acceptedOutputPresent": True,
    }
    payload = manifest.model_dump(by_alias=True, mode="json")

    assert payload["terminalDisposition"] == "accepted"
    assert payload["workspace"]["gitEffect"]["disposition"] == "accepted"
    assert payload["sideEffects"]["summary"]["categories"]["retrieval"] == 1
    assert len(payload["sideEffects"]["records"]) == 7
    assert payload["dependencyEffects"]["invalidatedLogicalStepIds"] == ["verify"]


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


def _occurred_non_idempotent_effect(
    *,
    effect_class: str = "external_non_idempotent",
    operation: str = "jira.transition_issue",
    target: str = "MM-705",
    idempotency_key: str = "wf-1:run-1:implement:execution:1:jira-transition",
) -> dict[str, object]:
    return side_effect_record(
        effect_class=effect_class,  # type: ignore[arg-type]
        operation=operation,
        target=target,
        idempotency_key=idempotency_key,
        workflow_state_accepted=True,
    )


def test_already_occurred_filters_to_accepted_non_idempotent_effects() -> None:
    occurred = _occurred_non_idempotent_effect()
    blocked = side_effect_record(
        effect_class="external_non_idempotent",
        operation="jira.transition_issue",
        target="MM-705",
        idempotency_key="wf-1:run-1:implement:execution:1:jira-transition",
        workflow_state_accepted=False,
    )
    idempotent = side_effect_record(
        effect_class="external_idempotent",
        operation="jira.add_comment",
        target="MM-705",
        idempotency_key="wf-1:run-1:implement:execution:1:comment",
        workflow_state_accepted=True,
    )
    workspace = side_effect_record(
        effect_class="workspace_mutation",
        operation="workspace.write",
        target="/work/repo/file.py",
    )

    accounted = already_occurred_non_idempotent_effects(
        [occurred, blocked, idempotent, workspace]
    )

    assert len(accounted) == 1
    assert accounted[0]["operation"] == "jira.transition_issue"


def test_plan_reattempt_compensation_is_explicit_idempotent_and_observable() -> None:
    occurred = _occurred_non_idempotent_effect()

    plan = plan_reattempt_compensation(
        workflow_id="wf-1",
        run_id="run-1",
        logical_step_id="implement",
        execution_ordinal=2,
        prior_side_effect_records=[occurred],
    )

    assert plan["requiresCompensation"] is True
    assert plan["compensationComplete"] is True
    assert plan["reattemptAllowed"] is True
    assert len(plan["compensations"]) == 1
    compensation = plan["compensations"][0]
    # Explicit: classified as a compensation that reconciles the prior effect.
    assert compensation["kind"] == "compensation"
    assert compensation["operation"] == "compensate:jira.transition_issue"
    assert compensation["disposition"] == "accepted"
    assert compensation["compensates"]["operation"] == "jira.transition_issue"
    # Idempotent: deterministic key derived from the new attempt identity.
    assert (
        compensation["idempotencyKey"]
        == "wf-1:run-1:implement:execution:2:compensate:jira.transition_issue"
    )

    # Deterministic: replanning the same reattempt yields identical records.
    replan = plan_reattempt_compensation(
        workflow_id="wf-1",
        run_id="run-1",
        logical_step_id="implement",
        execution_ordinal=2,
        prior_side_effect_records=[occurred],
    )
    assert replan == plan


def test_plan_reattempt_compensation_dedupes_already_compensated_subjects() -> None:
    occurred = _occurred_non_idempotent_effect()
    subject = compensation_subject_key(occurred)

    plan = plan_reattempt_compensation(
        workflow_id="wf-1",
        run_id="run-1",
        logical_step_id="implement",
        execution_ordinal=3,
        prior_side_effect_records=[occurred],
        already_compensated_subjects=[subject],
    )

    assert plan["requiresCompensation"] is False
    assert plan["compensations"] == []
    assert plan["alreadyCompensated"][0]["subject"] == subject
    assert plan["reattemptAllowed"] is True


def _patch_blocked_compensation(monkeypatch) -> None:
    """Force planned compensations to be recorded with a blocked disposition."""

    from moonmind.workflows.temporal import step_executions as step_executions_module

    real_side_effect_record = step_executions_module.side_effect_record

    def _blocked_compensation(*args: object, **kwargs: object) -> dict[str, object]:
        record = real_side_effect_record(*args, **kwargs)  # type: ignore[arg-type]
        record["disposition"] = "blocked"
        return record

    monkeypatch.setattr(
        step_executions_module, "side_effect_record", _blocked_compensation
    )


def test_plan_reattempt_compensation_blocks_when_compensation_not_accepted(
    monkeypatch,
) -> None:
    occurred = _occurred_non_idempotent_effect()
    _patch_blocked_compensation(monkeypatch)

    plan = plan_reattempt_compensation(
        workflow_id="wf-1",
        run_id="run-1",
        logical_step_id="implement",
        execution_ordinal=2,
        prior_side_effect_records=[occurred],
    )

    assert plan["requiresCompensation"] is True
    assert plan["compensations"][0]["disposition"] == "blocked"
    # A blocked compensation leaves the prior effect uncompensated: completion
    # must be false and the reattempt must not be allowed to advance.
    assert plan["compensationComplete"] is False
    assert plan["reattemptAllowed"] is False
    assert plan["reason"] == "uncompensated_non_idempotent_effects"


def test_plan_reattempt_compensation_policy_override_allows_blocked_compensation(
    monkeypatch,
) -> None:
    occurred = _occurred_non_idempotent_effect()
    _patch_blocked_compensation(monkeypatch)

    plan = plan_reattempt_compensation(
        workflow_id="wf-1",
        run_id="run-1",
        logical_step_id="implement",
        execution_ordinal=2,
        prior_side_effect_records=[occurred],
        policy_permits_non_idempotent_reattempt=True,
    )

    # Completion still reflects the blocked compensation, but explicit policy
    # permission lets the reattempt advance.
    assert plan["compensationComplete"] is False
    assert plan["reattemptAllowed"] is True


def test_plan_reattempt_compensation_noop_without_prior_effects() -> None:
    plan = plan_reattempt_compensation(
        workflow_id="wf-1",
        run_id="run-1",
        logical_step_id="implement",
        execution_ordinal=2,
        prior_side_effect_records=[],
    )

    assert plan["requiresCompensation"] is False
    assert plan["compensations"] == []
    assert plan["reattemptAllowed"] is True
    assert "reason" not in plan


def test_manifest_payload_embeds_policy_git_effect_and_side_effect_records() -> None:
    now = datetime(2026, 5, 17, 12, 0, tzinfo=UTC)
    workspace = workspace_policy_metadata(
        policy="continue_from_previous_execution",
        checkpoint_ref="artifact://checkpoint",
        checkpoint_valid=True,
    )
    workspace["gitEffect"] = git_effect_metadata(
        disposition="candidate",
        working_tree_diff_ref="artifact://diff",
        patch_ref="artifact://patch",
        workspace_checkpoint_ref="artifact://checkpoint",
    )
    manifest = StepExecutionManifestModel(
        workflowId="wf-1",
        runId="run-1",
        logicalStepId="implement",
        executionOrdinal=2,
        reason="tests_failed",
        status="failed",
        startedAt=now,
        updatedAt=now,
        workspace=workspace,
        sideEffects={
            "records": [
                side_effect_record(
                    effect_class="publication",
                    operation="repo.merge_pr",
                    workflow_state_accepted=False,
                )
            ],
        },
    )
    payload = manifest.model_dump(by_alias=True, mode="json")

    assert payload["workspace"]["policy"] == "continue_from_previous_execution"
    assert payload["workspace"]["gitEffect"]["disposition"] == "candidate"
    assert payload["sideEffects"]["records"][0]["disposition"] == "blocked"


# ---------------------------------------------------------------------------
# MM-826: terminal_side_effect_manifest aggregation
# ---------------------------------------------------------------------------


def _accepted_git_effect() -> dict:
    return git_effect_metadata(
        disposition="accepted",
        baseline_commit="abc123",
        head_commit="def456",
    )


def test_terminal_manifest_accepted_opens_handoff_gates() -> None:
    # SCN-1 / FR-001 / FR-002 / DESIGN-REQ-005,013
    records = [
        side_effect_record(
            effect_class="publication",
            operation="repo.create_pr",
            workflow_state_accepted=True,
        ),
        side_effect_record(
            effect_class="artifact_write",
            operation="artifact.write",
        ),
    ]
    manifest = terminal_side_effect_manifest(
        terminal_disposition="accepted",
        git_effect=_accepted_git_effect(),
        side_effect_records=records,
    )
    assert manifest["terminalDisposition"] == "accepted"
    assert manifest["acceptedOutputPresent"] is True
    assert manifest["handoffGatesOpen"] is True
    assert manifest["invalidReason"] is None
    assert manifest["git"]["disposition"] == "accepted"
    # All classes aggregated under their class key.
    assert "publication" in manifest["sideEffects"]
    assert "artifact_write" in manifest["sideEffects"]


def test_terminal_manifest_accepted_without_git_evidence_is_invalid() -> None:
    # Edge case / FR-002: accepted disposition must carry accepted git output.
    manifest = terminal_side_effect_manifest(
        terminal_disposition="accepted",
        git_effect=git_effect_metadata(disposition="candidate"),
        side_effect_records=[],
    )
    assert manifest["invalidReason"] == "accepted_without_accepted_git_output"
    assert manifest["handoffGatesOpen"] is False


def test_terminal_manifest_accepted_without_git_at_all_is_invalid() -> None:
    manifest = terminal_side_effect_manifest(
        terminal_disposition="accepted",
        git_effect=None,
        side_effect_records=[],
    )
    assert manifest["invalidReason"] == "accepted_without_accepted_git_output"
    assert manifest["handoffGatesOpen"] is False


def test_terminal_manifest_non_accepted_dispositions_close_gates() -> None:
    # SCN-2/3/4 / FR-003 / DESIGN-REQ-022
    for disposition in (
        "candidate",
        "discarded",
        "superseded",
        "blocked",
        "failed_with_remaining_work",
        "failed_unrecoverable",
    ):
        records = [
            side_effect_record(
                effect_class="publication",
                operation="repo.create_pr",
                workflow_state_accepted=False,
            ),
        ]
        manifest = terminal_side_effect_manifest(
            terminal_disposition=disposition,
            git_effect=git_effect_metadata(disposition="candidate"),
            side_effect_records=records,
        )
        assert manifest["handoffGatesOpen"] is False, disposition
        # The blocked publication record is preserved for audit.
        assert manifest["sideEffects"]["publication"][0]["disposition"] == "blocked"


def test_terminal_manifest_non_accepted_with_accepted_external_is_invalid() -> None:
    # FR-003 / DESIGN-REQ-022: a failed attempt must not retain an accepted
    # publication/non-idempotent/provider-account record.
    records = [
        side_effect_record(
            effect_class="publication",
            operation="repo.publish",
            workflow_state_accepted=True,  # would be "accepted" — illegal under failure
        ),
    ]
    manifest = terminal_side_effect_manifest(
        terminal_disposition="failed_with_remaining_work",
        git_effect=git_effect_metadata(disposition="candidate"),
        side_effect_records=records,
    )
    assert manifest["handoffGatesOpen"] is False
    assert manifest["invalidReason"] == "non_accepted_disposition_has_accepted_external_handoff"


# ---------------------------------------------------------------------------
# MM-826: external_handoff_gate_decision (activity-boundary assertion)
# ---------------------------------------------------------------------------


def test_handoff_decision_allows_accepted_gate_approved() -> None:
    # SCN-1 / FR-004
    decision = external_handoff_gate_decision(
        operation="repo.create_pr",
        effect_class="publication",
        terminal_disposition="accepted",
        gate_approved=True,
    )
    assert decision["allowed"] is True
    assert decision["reason"] is None
    assert decision["sideEffect"] is None


def test_handoff_decision_denies_non_accepted_dispositions() -> None:
    # SCN-2/3/4 / FR-004 / FR-005 / SC-001 / SC-002
    for disposition in (
        "candidate",
        "discarded",
        "superseded",
        "blocked",
        "failed_with_remaining_work",
        "failed_unrecoverable",
        None,
    ):
        decision = external_handoff_gate_decision(
            operation="repo.create_pr",
            effect_class="publication",
            terminal_disposition=disposition,
            gate_approved=True,
        )
        assert decision["allowed"] is False, disposition
        assert decision["reason"] == "producing_step_not_accepted"
        assert decision["sideEffect"]["disposition"] == "blocked"
        assert decision["sideEffect"]["class"] == "publication"


def test_handoff_decision_denies_when_gate_not_approved() -> None:
    # SCN-6 / FR-007: accepted disposition is not enough; gate approval required.
    decision = external_handoff_gate_decision(
        operation="jira.transition_issue",
        effect_class="publication",
        terminal_disposition="accepted",
        gate_approved=False,
    )
    assert decision["allowed"] is False
    assert decision["reason"] == "gate_not_approved"
    assert decision["sideEffect"]["disposition"] == "blocked"


def test_handoff_decision_denies_non_idempotent_without_policy() -> None:
    # SCN-5 / FR-006: non-idempotent external action without explicit policy.
    decision = external_handoff_gate_decision(
        operation="provider_account.lease",
        effect_class="provider_account",
        terminal_disposition="accepted",
        gate_approved=True,
        policy_permits_non_idempotent=False,
    )
    assert decision["allowed"] is False
    assert decision["reason"] == "non_idempotent_external_action_without_policy"
    assert decision["sideEffect"]["disposition"] == "blocked"


def test_handoff_decision_allows_non_idempotent_with_policy() -> None:
    # SCN-5 / FR-006: explicit policy permits the non-idempotent action, but only
    # when also accepted + gate-approved.
    decision = external_handoff_gate_decision(
        operation="provider_account.lease",
        effect_class="provider_account",
        terminal_disposition="accepted",
        gate_approved=True,
        policy_permits_non_idempotent=True,
    )
    assert decision["allowed"] is True
    assert decision["reason"] is None


def test_handoff_decision_gated_operation_prefix_is_non_idempotent() -> None:
    # FR-006: gated operation prefix counts as non-idempotent even for a
    # generic external effect class.
    decision = external_handoff_gate_decision(
        operation="deployment.publish",
        effect_class="external_non_idempotent",
        terminal_disposition="accepted",
        gate_approved=True,
    )
    assert decision["allowed"] is False
    assert decision["reason"] == "non_idempotent_external_action_without_policy"


def test_handoff_decision_sanitizes_secret_like_target() -> None:
    # FR-008 / SC-005
    decision = external_handoff_gate_decision(
        operation="repo.merge",
        effect_class="publication",
        terminal_disposition="candidate",
        gate_approved=True,
        target="https://x?token=ghp_abcdefghijklmnopqrstuvwxyz0123456789",
    )
    assert decision["allowed"] is False
    assert "ghp_" not in (decision["sideEffect"]["target"] or "")
    assert "[REDACTED]" in (decision["sideEffect"]["target"] or "")
