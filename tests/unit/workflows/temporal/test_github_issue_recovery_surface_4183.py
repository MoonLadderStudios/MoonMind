"""Recovery-status projection and safe operator actions (#4183).

Covers MoonLadderStudios/MoonMind#4183 through the real
``github_issue_recovery_surface`` policy boundary: bounded
server-derived lifecycle context, six truthful attention categories,
supported operator actions with progressive disclosure, server-side
submission revalidation (stale/unauthorized/duplicate/wrong-issue/
conflicting-PR/unknown-writer), auditable portable-handoff decisions,
Continue variants without silent downgrade, and comment/UI consistency
with outage honesty and preserved failure history.
"""

from __future__ import annotations

from typing import Any

import pytest

from moonmind.workflows.temporal import github_issue_recovery_surface as surface

REPO = "o/r"
ISSUE_NUMBER = 4183


def _issue(*labels: str) -> dict[str, Any]:
    return {
        "number": ISSUE_NUMBER,
        "repository": REPO,
        "state": "open",
        "html_url": f"https://github.com/{REPO}/issues/{ISSUE_NUMBER}",
        "labels": [{"name": label} for label in labels],
    }


def _attempt(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "attempt_id": "att_" + "a" * 24,
        "deployment_id": "dep-origin",
        "seq": 3,
        "remaining_requirements": ["finish repair", "verify"],
        "recovery_phase": "continuation_ready",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Projection (required work item 1, acceptance criterion 1)
# ---------------------------------------------------------------------------


def test_projection_exposes_bounded_lifecycle_context() -> None:
    context = surface.build_issue_lifecycle_context(
        repository=REPO,
        issue_number=ISSUE_NUMBER,
        issue=_issue("status: recovery-needed"),
        current_attempt=_attempt(),
        predecessor_attempts=[_attempt(attempt_id="att_" + "0" * 24, result="failed", verification_summary="boom")],
        deployment_id="dep-origin",
        preserved_pr={"pr_url": f"https://github.com/{REPO}/pull/7", "head_sha": "b" * 40, "base": "main", "save_method": "pr_head_verified"},
        retry_state={"remaining": 2, "blocked": False},
        operator_hold={"active": False},
        sync_state={},
        local_facts={"observed_locally": True},
    )
    assert context["issue_url"] == f"https://github.com/{REPO}/issues/{ISSUE_NUMBER}"
    assert context["issue_state"] == "open"
    assert context["settled_lifecycle_state"] == "recovery_needed"
    assert context["originating_deployment_id"] == "dep-origin"
    assert context["preserved_revision"] == "b" * 40
    assert context["remaining_requirements"] == ["finish repair", "verify"]
    assert context["recovery_phase"] == "continuation_ready"
    assert context["retry_allowance_remaining"] == 2
    assert context["evidence_freshness"] == "fresh"
    assert context["sync_status"] == "fresh"
    assert context["prior_failure_count"] == 1
    assert context["recovery_availability"]["available"] is True


def test_projection_reports_unknown_rather_than_inferring() -> None:
    context = surface.build_issue_lifecycle_context(repository=REPO, issue_number=ISSUE_NUMBER)
    assert context["sync_status"] == "fresh"
    assert context["preserved_pr"] is None
    assert context["originating_deployment_id"] == ""


def test_outage_reports_pending_unknown_not_success() -> None:
    context = surface.build_issue_lifecycle_context(
        repository=REPO,
        issue_number=ISSUE_NUMBER,
        issue=_issue("status: in-progress"),
        current_attempt=_attempt(publication_unknown=True),
        sync_state={"github_unavailable": True},
    )
    assert context["sync_status"] == "unknown"
    assert context["attention_category"] == surface.ATTENTION_UNKNOWN_PUBLICATION
    availability = context["recovery_availability"]
    assert availability["available"] is False
    assert availability["reason"] == "github_unavailable"


def test_terminal_failures_returning_to_available_retain_history() -> None:
    predecessors = [
        _attempt(attempt_id="att_" + "1" * 24, result="failed", verification_summary="first boom"),
        _attempt(attempt_id="att_" + "2" * 24, result="failed", verification_summary="second boom"),
    ]
    context = surface.build_issue_lifecycle_context(
        repository=REPO,
        issue_number=ISSUE_NUMBER,
        issue=_issue(),
        current_attempt=_attempt(),
        predecessor_attempts=predecessors,
    )
    assert context["prior_failure_count"] == 2
    assert len(context["failure_history"]) == 2
    comment = surface.render_next_action_comment(context=context)
    assert "Prior failed attempts preserved: 2" in comment


# ---------------------------------------------------------------------------
# Attention categories (required work item 2, acceptance criterion 2)
# ---------------------------------------------------------------------------


def test_attention_confirmed_local_failure_requires_local_observation() -> None:
    # A remote workflow missing locally must NOT become a confirmed failure.
    context = surface.build_issue_lifecycle_context(
        repository=REPO,
        issue_number=ISSUE_NUMBER,
        issue=_issue("status: in-progress"),
        current_attempt=_attempt(),
        local_facts={"has_local_failure": True, "observed_locally": False},
    )
    assert context["attention_category"] is None

    context = surface.build_issue_lifecycle_context(
        repository=REPO,
        issue_number=ISSUE_NUMBER,
        issue=_issue("status: in-progress"),
        current_attempt=_attempt(local_failure_confirmed=True),
        local_facts={"observed_locally": True},
    )
    assert context["attention_category"] == surface.ATTENTION_CONFIRMED_LOCAL_FAILURE


def test_attention_categories_are_truthful() -> None:
    cases = [
        ({"owner_unresponsive": True}, {}, surface.ATTENTION_UNRESPONSIVE_REMOTE_OWNER),
        ({"cancelled": True}, {}, surface.ATTENTION_CANCELLATION_OR_HOLD),
        ({}, {"active": True, "reason": "operator pause"}, surface.ATTENTION_CANCELLATION_OR_HOLD),
    ]
    for attempt_overrides, hold, expected in cases:
        context = surface.build_issue_lifecycle_context(
            repository=REPO,
            issue_number=ISSUE_NUMBER,
            issue=_issue("status: needs-attention"),
            current_attempt=_attempt(**attempt_overrides),
            operator_hold=hold,
        )
        assert context["attention_category"] == expected, attempt_overrides

    context = surface.build_issue_lifecycle_context(
        repository=REPO,
        issue_number=ISSUE_NUMBER,
        issue=_issue("status: needs-attention"),
        current_attempt=_attempt(),
        retry_state={"blocked": True, "block_reason": "retry budget exhausted"},
    )
    assert context["attention_category"] == surface.ATTENTION_EXHAUSTED_RETRIES

    context = surface.build_issue_lifecycle_context(
        repository=REPO,
        issue_number=ISSUE_NUMBER,
        issue=_issue("status: recovery-needed"),
        current_attempt=_attempt(),
        preserved_pr={"save_method": "local_only"},
    )
    assert context["attention_category"] == surface.ATTENTION_PRIVATE_ONLY_WORK
    assert context["recovery_availability"]["reason"] == "private_only_work"


def test_acknowledgment_leaves_admission_blocked() -> None:
    context = surface.build_issue_lifecycle_context(
        repository=REPO,
        issue_number=ISSUE_NUMBER,
        issue=_issue("status: needs-attention"),
        current_attempt=_attempt(),
        operator_hold={"active": True, "acknowledged": True, "reason": "seen"},
    )
    assert context["attention_category"] == surface.ATTENTION_CANCELLATION_OR_HOLD
    assert context["recovery_availability"]["available"] is False
    assert context["recovery_availability"]["reason"] == "operator_hold"


# ---------------------------------------------------------------------------
# Actions (required work items 3 + 6)
# ---------------------------------------------------------------------------


def test_private_only_work_offers_no_resume() -> None:
    context = surface.build_issue_lifecycle_context(
        repository=REPO,
        issue_number=ISSUE_NUMBER,
        issue=_issue("status: recovery-needed"),
        current_attempt=_attempt(),
        preserved_pr={"save_method": "local_only"},
    )
    actions = {entry["action"]: entry for entry in surface.available_actions(context, permissions={"can_continue": True})}
    assert actions["continue_work"]["enabled"] is False
    assert actions["continue_work"]["disabled_reason"] == "private_only_work"


def test_continue_variant_derived_from_evidence() -> None:
    verify = surface.build_issue_lifecycle_context(
        repository=REPO, issue_number=ISSUE_NUMBER, current_attempt=_attempt(next_action="verify gates", remaining_requirements=[]),
    )
    assert surface.infer_continue_variant(verify) == surface.CONTINUE_VERIFY_ONLY

    seeded = surface.build_issue_lifecycle_context(
        repository=REPO,
        issue_number=ISSUE_NUMBER,
        current_attempt=_attempt(next_action="continue implementation", remaining_requirements=["x"]),
        preserved_pr={"pr_url": f"https://github.com/{REPO}/pull/7", "save_method": "pr_head_verified"},
    )
    assert surface.infer_continue_variant(seeded) == surface.CONTINUE_CODE_SEEDED

    publish = surface.build_issue_lifecycle_context(
        repository=REPO, issue_number=ISSUE_NUMBER, current_attempt=_attempt(next_action="finalize status"),
    )
    assert surface.infer_continue_variant(publish) == surface.CONTINUE_PUBLISH_FINALIZE


def test_unknown_continue_variant_refuses_silent_downgrade() -> None:
    with pytest.raises(ValueError, match="Unknown continue variant"):
        surface.record_operator_decision(
            action=surface.ACTION_CONTINUE,
            operator={"id": "op"},
            previous_state="recovery_needed",
            reason="go",
            continue_variant="fresh_retry",
        )


# ---------------------------------------------------------------------------
# Server-side revalidation (required work item 4, acceptance criterion 3)
# ---------------------------------------------------------------------------


def _perms(**overrides: Any) -> dict[str, Any]:
    base = {"can_continue": True, "can_hold": True, "can_retry_reset": True, "can_abandon": True, "can_resolve": True}
    base.update(overrides)
    return base


def test_unauthorized_action_rejected_by_server() -> None:
    verdict = surface.validate_operator_action(
        action=surface.ACTION_ABANDON,
        request={"issue_number": ISSUE_NUMBER, "idempotency_key": "k1"},
        live_issue=_issue(),
        permissions=_perms(can_abandon=False),
    )
    assert verdict["allowed"] is False
    assert verdict["code"] == "unauthorized"


def test_stale_attempt_rejected() -> None:
    verdict = surface.validate_operator_action(
        action=surface.ACTION_CONTINUE,
        request={"issue_number": ISSUE_NUMBER, "idempotency_key": "k2", "attempt_seq": 2},
        live_issue=_issue("status: recovery-needed"),
        live_attempt=_attempt(seq=3),
        permissions=_perms(),
        stop_proof={"writers_stopped": True},
    )
    assert verdict["allowed"] is False
    assert verdict["code"] == "stale_attempt"


def test_duplicate_idempotent_replay_does_not_repeat_effects() -> None:
    verdict = surface.validate_operator_action(
        action=surface.ACTION_HOLD,
        request={"issue_number": ISSUE_NUMBER, "idempotency_key": "seen-key"},
        live_issue=_issue(),
        permissions=_perms(),
        seen_idempotency_keys=["seen-key"],
    )
    assert verdict["allowed"] is True
    assert verdict["duplicate"] is True


def test_wrong_issue_rejected() -> None:
    verdict = surface.validate_operator_action(
        action=surface.ACTION_HOLD,
        request={"issue_number": 9999, "idempotency_key": "k3"},
        live_issue=_issue(),
        permissions=_perms(),
    )
    assert verdict["allowed"] is False
    assert verdict["code"] == "wrong_issue"


def test_conflicting_pr_rejected() -> None:
    verdict = surface.validate_operator_action(
        action=surface.ACTION_CONTINUE,
        request={"issue_number": ISSUE_NUMBER, "idempotency_key": "k4", "pr_url": f"https://github.com/{REPO}/pull/8"},
        live_issue=_issue("status: recovery-needed"),
        live_attempt=_attempt(),
        live_pr={"pr_url": f"https://github.com/{REPO}/pull/7"},
        permissions=_perms(),
        stop_proof={"writers_stopped": True},
    )
    assert verdict["allowed"] is False
    assert verdict["code"] == "conflicting_pr"


def test_unknown_writer_rejected() -> None:
    verdict = surface.validate_operator_action(
        action=surface.ACTION_AUTHORIZE_RETRY,
        request={"issue_number": ISSUE_NUMBER, "idempotency_key": "k5", "writer_attempt_id": "att_" + "f" * 24},
        live_issue=_issue(),
        live_attempt=_attempt(attempt_id="att_" + "a" * 24),
        permissions=_perms(),
        stop_proof={"writers_stopped": True},
    )
    assert verdict["allowed"] is False
    assert verdict["code"] == "unknown_writer"


def test_cross_device_stop_cannot_be_fabricated() -> None:
    verdict = surface.validate_operator_action(
        action=surface.ACTION_AUTHORIZE_RETRY,
        request={"issue_number": ISSUE_NUMBER, "idempotency_key": "k6"},
        live_issue=_issue(),
        live_attempt=_attempt(),
        permissions=_perms(),
        stop_proof={"writers_stopped": False, "remote_owner": True, "can_stop_remote": False},
    )
    assert verdict["allowed"] is False
    assert verdict["code"] == "cross_device_stop_required"


def test_continue_requires_conclusive_stop_proof() -> None:
    verdict = surface.validate_operator_action(
        action=surface.ACTION_CONTINUE,
        request={"issue_number": ISSUE_NUMBER, "idempotency_key": "k7"},
        live_issue=_issue("status: recovery-needed"),
        live_attempt=_attempt(),
        permissions=_perms(),
        stop_proof={"writers_stopped": False},
    )
    assert verdict["allowed"] is False
    assert verdict["code"] == "writer_not_stopped"


def test_unknown_publication_blocks_takeover() -> None:
    verdict = surface.validate_operator_action(
        action=surface.ACTION_CONTINUE,
        request={"issue_number": ISSUE_NUMBER, "idempotency_key": "k8"},
        live_issue=_issue("status: recovery-needed"),
        live_attempt=_attempt(),
        live_pr={"outcome_unknown": True},
        permissions=_perms(),
        stop_proof={"writers_stopped": True},
    )
    assert verdict["allowed"] is False
    assert verdict["code"] == "publication_unknown"


# ---------------------------------------------------------------------------
# Auditable decisions (required work item 5, acceptance criterion 4)
# ---------------------------------------------------------------------------


def test_hold_is_not_abandonment() -> None:
    with pytest.raises(ValueError, match="Hold is not abandonment"):
        surface.record_operator_decision(
            action=surface.ACTION_HOLD, operator={"id": "op"}, previous_state="x", reason="pause", work_disposition="abandoned",
        )


def test_abandonment_requires_explicit_disposition() -> None:
    with pytest.raises(ValueError, match="explicit 'abandoned'"):
        surface.record_operator_decision(
            action=surface.ACTION_ABANDON, operator={"id": "op"}, previous_state="x", reason="done", work_disposition="held",
        )


def test_decision_records_identity_reason_state_and_budget() -> None:
    decision = surface.record_operator_decision(
        action=surface.ACTION_AUTHORIZE_RETRY,
        operator={"id": "operator-7"},
        previous_state="needs_attention",
        reason="writer stopped on owner device",
        work_disposition="preserved",
        retry_budget_reset=True,
        competing_refs=[f"https://github.com/{REPO}/pull/7"],
    )
    assert decision["operator"] == "operator-7"
    assert decision["reason"] == "writer stopped on owner device"
    assert decision["previous_state"] == "needs_attention"
    assert decision["retry_budget_reset"] is True
    assert decision["competing_refs_preserved"] == [f"https://github.com/{REPO}/pull/7"]
    # Competing refs survive until this explicit disposition is recorded.


def test_acknowledge_never_releases_hold() -> None:
    decision = surface.record_operator_decision(
        action=surface.ACTION_ACKNOWLEDGE, operator={"id": "op"}, previous_state="needs_attention", reason="seen",
    )
    assert decision["hold_released"] is False
    assert decision["work_disposition"] == "acknowledged"


def test_comment_and_ui_stay_consistent() -> None:
    context = surface.build_issue_lifecycle_context(
        repository=REPO,
        issue_number=ISSUE_NUMBER,
        issue=_issue("status: needs-attention"),
        current_attempt=_attempt(),
        operator_hold={"active": True, "reason": "operator pause"},
    )
    comment = surface.render_next_action_comment(context=context)
    availability_detail = context["recovery_availability"]["detail"]
    assert availability_detail in comment
    assert "Needs attention" in comment
