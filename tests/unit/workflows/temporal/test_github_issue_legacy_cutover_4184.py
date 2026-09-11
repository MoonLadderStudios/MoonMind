"""Conservative legacy reconciliation and coordinated deployment guidance (#4184).

Covers MoonLadderStudios/MoonMind#4184 acceptance through the real
policy call shapes: the caller/persisted-contract inventory naming the
surviving policy owner with a todo-free new path, bounded read-only
legacy assessment over the required matrix (manual status, generic
historical start comment, missing/contradictory history, open Done,
reopened issue, partial PR, unsupported attempt version), repair gating
through the shared evidence/authorization rules, retained-history
replay/drainage with no obsolete-shape writes, mixed old/new
deployment qualification, and upgrade/rollback fixtures that preserve
labels, comments, code, artifacts, and unrelated settings.
"""

from __future__ import annotations

from typing import Any

from moonmind.workflows.temporal import github_issue_attempt as attempt
from moonmind.workflows.temporal import github_issue_lifecycle as lifecycle
from moonmind.workflows.temporal import github_issue_legacy_cutover as cutover
from moonmind.workflows.temporal.github_issue_attempt import AttemptHandoff, render_attempt_comment

REPO = "o/r"
ISSUE = 11
ATTEMPT_ID = "att_" + "a" * 24
TRUSTED = ["moonmind-bot"]


def _handoff_body(**overrides: Any) -> str:
    base: dict[str, Any] = {
        "attempt_id": ATTEMPT_ID,
        "deployment_id": "deploy-a",
        "repository": REPO,
        "issue_number": ISSUE,
        "workflow_id": "wf-1",
        "run_id": "run-1",
        "activity": "active",
        "writers_stopped": False,
        "outcome": "pending",
        "next_action": "continue-implementation",
    }
    base.update(overrides)
    body, error = render_attempt_comment(AttemptHandoff(**base))
    assert not error, error
    return body


def _assess(**overrides: Any) -> cutover.LegacyAssessment:
    kwargs: dict[str, Any] = {
        "repository": REPO,
        "issue_number": ISSUE,
        "issue": {"state": "open", "labels": []},
        "comments": [],
        "prs": [],
        "checkpoints": [],
        "trusted_posters": TRUSTED,
        "issue_flags": {},
    }
    kwargs.update(overrides)
    return cutover.assess_legacy_issue(**kwargs)


def _finding_codes(assessment: cutover.LegacyAssessment) -> set[str]:
    return {finding.finding for finding in assessment.findings}


def test_inventory_names_surviving_owner_and_new_path_todo_free() -> None:
    assert cutover.SURVIVING_POLICY_OWNER == "moonmind.workflows.temporal.github_issue_lifecycle"
    assert cutover.CALLER_INVENTORY, "inventory must name readers/writers/contracts"
    for entry in cutover.CALLER_INVENTORY:
        assert entry["survivingPolicy"] == cutover.SURVIVING_POLICY_OWNER
    assert {entry["role"] for entry in cutover.CALLER_INVENTORY} >= {"reader", "writer", "contract"}
    verdict = cutover.verify_new_path_todo_free()
    assert verdict["todoFree"] is True
    bad = cutover.verify_new_path_todo_free(["status: in-progress", "status: todo"])
    assert bad["todoFree"] is False


def test_manual_status_respected_without_age_clear() -> None:
    assessment = _assess(issue={"state": "open", "labels": ["status: in-progress"]})
    assert cutover.FINDING_MANUAL_STATUS in _finding_codes(assessment)
    assert assessment.settled == lifecycle.SETTLED_IN_PROGRESS
    owner = next(f for f in assessment.findings if f.finding == cutover.FINDING_MANUAL_STATUS).owner
    assert owner == "unknown"
    repair = cutover.plan_legacy_repair(
        assessment=assessment,
        from_settled=lifecycle.SETTLED_IN_PROGRESS,
        to_target=lifecycle.TO_AVAILABLE,
        evidence={"writers_stopped": True, "terminal_proof": "x"},
        reason="cleanup",
    )
    assert repair["allowed"] is False
    assert repair["reasonCode"] == "operator_decision_required"


def test_generic_historical_start_comment() -> None:
    assessment = _assess(comments=[{"body": "Started working on this last quarter", "author_login": "someone"}])
    assert cutover.FINDING_GENERIC_START in _finding_codes(assessment)


def test_missing_history_explains_incomplete_evidence() -> None:
    assessment = _assess()
    assert cutover.FINDING_MISSING_HISTORY in _finding_codes(assessment)
    finding = next(f for f in assessment.findings if f.finding == cutover.FINDING_MISSING_HISTORY)
    assert "does not prove no work exists" in finding.evidence


def test_contradictory_history_from_untrusted_poster() -> None:
    body = _handoff_body()
    assessment = _assess(comments=[{"body": body, "author_login": "impostor"}])
    assert cutover.FINDING_CONTRADICTORY_HISTORY in _finding_codes(assessment)


def test_open_done_is_inconsistent() -> None:
    assessment = _assess(issue={"state": "open", "labels": ["status: done"]})
    assert cutover.FINDING_OPEN_DONE in _finding_codes(assessment)
    assert assessment.settled == lifecycle.SETTLED_BLOCKED_OPEN_DONE


def test_reopened_issue_is_reassessed_not_freshened() -> None:
    body = _handoff_body(writers_stopped=True)
    assessment = _assess(
        comments=[{"body": body, "author_login": "moonmind-bot"}],
        issue_flags={"was_reopened": True},
    )
    assert cutover.FINDING_REOPENED in _finding_codes(assessment)


def test_partial_pr_defaults_to_finish_existing_pr() -> None:
    body = _handoff_body()
    assessment = _assess(
        comments=[{"body": body, "author_login": "moonmind-bot"}],
        prs=[{"url": "https://github.com/o/r/pull/7", "state": "open", "head_sha": "b" * 40}],
    )
    codes = _finding_codes(assessment)
    assert cutover.FINDING_PARTIAL_PR in codes
    finding = next(f for f in assessment.findings if f.finding == cutover.FINDING_PARTIAL_PR)
    assert finding.suggested_action == cutover.ACTION_CONTINUE_PR


def test_multiple_prs_require_operator_decision() -> None:
    assessment = _assess(
        prs=[
            {"url": "https://github.com/o/r/pull/7", "state": "open", "head_sha": "b" * 40},
            {"url": "https://github.com/o/r/pull/8", "state": "open", "head_sha": "c" * 40},
        ]
    )
    assert cutover.FINDING_LINKED_PRS in _finding_codes(assessment)


def test_unsupported_attempt_version_fails_closed() -> None:
    body = _handoff_body()
    old_version_json = f'"formatVersion": "{attempt.ATTEMPT_COMMENT_FORMAT_VERSION}"'
    assert old_version_json in body
    # Replace only the JSON value, not the machine-block sentinel, so the
    # block still extracts and fails closed at version validation.
    tampered = body.replace(old_version_json, '"formatVersion": "moonmind.github_issue_attempt.v0"', 1)
    assert tampered != body
    assessment = _assess(comments=[{"body": tampered, "author_login": "moonmind-bot"}])
    assert cutover.FINDING_UNSUPPORTED_VERSION in _finding_codes(assessment)


def test_private_only_checkpoint_requires_owner_recovery() -> None:
    assessment = _assess(checkpoints=[{"id": "ckpt-1", "scope": "local-only", "owner": "deploy-a"}])
    assert cutover.FINDING_PRIVATE_ONLY in _finding_codes(assessment)


def test_assessment_is_bounded_and_repeatable() -> None:
    comments = [{"body": f"note {i}", "author_login": "someone"} for i in range(cutover.MAX_COMMENTS_ASSESSED + 10)]
    first = _assess(comments=comments)
    second = _assess(comments=comments)
    assert first.complete is False
    assert "never a clean repository" in first.completeness_note
    assert [f.to_dict() for f in first.findings] == [f.to_dict() for f in second.findings]


def test_conclusively_stopped_repair_uses_shared_guards_and_preserves() -> None:
    body = _handoff_body(writers_stopped=True)
    assessment = _assess(
        issue={"state": "open", "labels": ["status: in-progress"]},
        comments=[{"body": body, "author_login": "moonmind-bot"}],
    )
    assert assessment.settled == lifecycle.SETTLED_IN_PROGRESS
    repair = cutover.plan_legacy_repair(
        assessment=assessment,
        from_settled=lifecycle.SETTLED_IN_PROGRESS,
        to_target=lifecycle.TO_RECOVERY_NEEDED,
        evidence={"writers_stopped": True, "handoff_published": "handoff-1"},
        reason="preserve partial work",
        conclusively_stopped=True,
    )
    assert repair["allowed"] is True
    assert "portableHandoff" in repair
    assert "human labels" in repair["preserved"]
    assert "old comments" in repair["preserved"]


def test_retained_history_replay_and_drainage() -> None:
    replayable = cutover.classify_retained_payload(
        {"family": "activity_inputs", "shape": "attempt_handoff", "version": attempt.ATTEMPT_COMMENT_FORMAT_VERSION}
    )
    assert replayable["disposition"] == "replay"
    obsolete = cutover.classify_retained_payload({"family": "tool_results", "shape": "status: todo", "version": "v0"})
    assert obsolete["disposition"] == "drain"
    assert cutover.is_obsolete_shape("status: todo") is True
    assert cutover.is_obsolete_shape("attempt_handoff") is False
    unknown = cutover.classify_retained_payload({"family": "mystery", "shape": "x", "version": "v1"})
    assert unknown["disposition"] == "reject"
    plan = cutover.drainage_plan_for_pending(
        [
            {"family": "pending_finalization", "shape": "terminal_handoff", "version": "v1"},
            {"family": "preset_expansion", "shape": "expanded_preset", "version": "v2"},
        ]
    )
    assert plan["writesObsoleteShapes"] is False
    assert len(plan["steps"]) == 2


def test_mixed_deployment_unqualified_until_all_conform() -> None:
    mixed = cutover.evaluate_mixed_deployment(
        [
            {"installationId": "deploy-a", "codeVersion": "v2", "oldSelectorActive": False, "defaultsReconciled": True},
            {"installationId": "deploy-b", "codeVersion": "v1", "oldSelectorActive": True, "defaultsReconciled": True},
        ]
    )
    assert mixed["qualified"] is False
    assert any("old claimer" in reason for reason in mixed["reasons"])
    conforming = cutover.evaluate_mixed_deployment(
        [
            {"installationId": "deploy-a", "codeVersion": "v2", "oldSelectorActive": False, "defaultsReconciled": True},
            {"installationId": "deploy-b", "codeVersion": "v2", "oldSelectorActive": False, "defaultsReconciled": True},
        ]
    )
    assert conforming["qualified"] is True
    duplicate = cutover.evaluate_mixed_deployment(
        [
            {"installationId": "deploy-a", "codeVersion": "v2"},
            {"installationId": "deploy-a", "codeVersion": "v2"},
        ]
    )
    assert duplicate["qualified"] is False


def test_upgrade_rollback_fixtures_preserve_everything() -> None:
    state = {
        "labels": ["bug", "status: in-progress"],
        "comments": ["attempt handoff"],
        "code_refs": ["o/r/pull/7"],
        "artifacts": ["bundle.tgz"],
        "settings": {"unrelated": True},
        "attempt_format": attempt.ATTEMPT_COMMENT_FORMAT_VERSION,
    }
    upgraded = cutover.simulate_upgrade(state)
    assert upgraded["preservedLabels"] == ["bug", "status: in-progress"]
    assert upgraded["settingsUnchanged"] is True
    refused = cutover.simulate_rollback(state, hold_drain=False)
    assert refused["rolledBack"] is False
    assert refused["reasonCode"] == "hold_drain_required"
    held = cutover.simulate_rollback(state, hold_drain=True)
    assert held["rolledBack"] is True
    assert held["preservedLabels"] == ["bug", "status: in-progress"]
    assert held["settingsUnchanged"] is True


def test_canonical_handoff_format_is_parsed_first() -> None:
    from moonmind.workflows.temporal import github_issue_attempts as canonical

    handoff = canonical.build_attempt_handoff(
        attempt_id="canon-1",
        deployment_id="deploy-canon",
        repository=REPO,
        issue_number=ISSUE,
        activity="active",
    )
    body = canonical.render_attempt_comment(handoff)
    assessment = _assess(
        issue={"state": "open", "labels": ["status: in-progress"]},
        comments=[{"body": body, "author_login": "moonmind-bot"}],
    )
    assert assessment.complete is True
    assert cutover.FINDING_MISSING_HISTORY not in _finding_codes(assessment)
    assert cutover.FINDING_MANUAL_STATUS not in _finding_codes(assessment)


def test_ordinary_discussion_without_handoff_reports_missing_history() -> None:
    assessment = _assess(
        comments=[{"body": "Looks good, thanks for the update!", "author_login": "someone"}]
    )
    assert cutover.FINDING_MISSING_HISTORY in _finding_codes(assessment)


def test_incomplete_assessment_refuses_repair() -> None:
    comments = [{"body": f"note {i}", "author_login": "someone"} for i in range(cutover.MAX_COMMENTS_ASSESSED + 2)]
    assessment = _assess(comments=comments)
    assert assessment.complete is False
    repair = cutover.plan_legacy_repair(
        assessment=assessment,
        from_settled=assessment.settled,
        to_target=lifecycle.TO_IN_PROGRESS,
        evidence={"admission_passed": True, "prior_work_inspected": True},
        reason="should stay blocked",
    )
    assert repair["allowed"] is False
    assert repair["reasonCode"] == "incomplete_assessment"


def test_repair_origin_must_match_assessment() -> None:
    body = _handoff_body(writers_stopped=True)
    assessment = _assess(
        issue={"state": "open", "labels": ["status: in-progress"]},
        comments=[{"body": body, "author_login": "moonmind-bot"}],
    )
    assert assessment.settled == lifecycle.SETTLED_IN_PROGRESS
    repair = cutover.plan_legacy_repair(
        assessment=assessment,
        from_settled=lifecycle.SETTLED_AVAILABLE,
        to_target=lifecycle.TO_IN_PROGRESS,
        evidence={"admission_passed": True, "prior_work_inspected": True},
        reason="mismatched origin",
    )
    assert repair["allowed"] is False
    assert repair["reasonCode"] == "settled_mismatch"


def test_multiple_validated_attempts_require_reconciliation() -> None:
    from moonmind.workflows.temporal import github_issue_attempts as canonical

    first = canonical.build_attempt_handoff(
        attempt_id="canon-a",
        deployment_id="deploy-a",
        repository=REPO,
        issue_number=ISSUE,
        activity="active",
    )
    second = canonical.build_attempt_handoff(
        attempt_id="canon-b",
        deployment_id="deploy-b",
        repository=REPO,
        issue_number=ISSUE,
        activity="active",
    )
    assessment = _assess(
        issue={"state": "open", "labels": ["status: in-progress"]},
        comments=[
            {"body": canonical.render_attempt_comment(first), "author_login": "moonmind-bot"},
            {"body": canonical.render_attempt_comment(second), "author_login": "moonmind-bot"},
        ],
    )
    assert cutover.FINDING_CONTRADICTORY_HISTORY in _finding_codes(assessment)
    repair = cutover.plan_legacy_repair(
        assessment=assessment,
        from_settled=assessment.settled,
        to_target=lifecycle.TO_RECOVERY_NEEDED,
        evidence={"writers_stopped": True, "handoff_published": "handoff-1"},
        reason="must stay blocked",
    )
    assert repair["allowed"] is False
    assert repair["reasonCode"] == "operator_decision_required"


def test_missing_defaults_reconciliation_is_unqualified() -> None:
    result = cutover.evaluate_mixed_deployment(
        [{"installationId": "a", "codeVersion": "v2"}]
    )
    assert result["qualified"] is False
    assert any("reconciliation" in reason for reason in result["reasons"])


def test_partial_pr_blocks_fresh_admission() -> None:
    assessment = _assess(
        prs=[{"url": "https://github.com/o/r/pull/7", "state": "open", "head_sha": "b" * 40}],
    )
    assert cutover.FINDING_PARTIAL_PR in _finding_codes(assessment)
    repair = cutover.plan_legacy_repair(
        assessment=assessment,
        from_settled=assessment.settled,
        to_target=lifecycle.TO_IN_PROGRESS,
        evidence={"admission_passed": True, "prior_work_inspected": True},
        reason="fresh admission contradicts continue_pr",
    )
    assert repair["allowed"] is False
    assert repair["reasonCode"] == "action_mismatch"


def test_bare_boolean_operator_decision_grants_no_authority() -> None:
    body = _handoff_body()
    assessment = _assess(
        issue={"state": "open", "labels": ["status: in-progress"]},
        comments=[{"body": body, "author_login": "impostor"}],
    )
    assert cutover.FINDING_CONTRADICTORY_HISTORY in _finding_codes(assessment)
    repair = cutover.plan_legacy_repair(
        assessment=assessment,
        from_settled=assessment.settled,
        to_target=lifecycle.TO_RECOVERY_NEEDED,
        evidence={"writers_stopped": True, "handoff_published": "handoff-1"},
        reason="bare boolean must not authorize",
        operator_decision={"authorized_resolution": True},
    )
    assert repair["allowed"] is False
    assert repair["reasonCode"] == "operator_verification_required"


def test_pr_overflow_is_partial_never_clean() -> None:
    prs = [{"url": f"https://github.com/o/r/pull/{i}", "state": "closed", "head_sha": "b" * 40} for i in range(cutover.MAX_PRS_ASSESSED + 5)]
    assessment = _assess(prs=prs)
    assert assessment.complete is False
    assert "PR" in assessment.completeness_note
