"""The deliberate attention escalation must stay a recoverable Needs-attention state.

Reproduces the scheduled ``MoonMind GitHub Issue Search and Implement`` lockout
observed on 2026-09-18: every candidate was rejected, 52 of them as
``lifecycle_ineligible/blocked_mixed``. Each carried ``status: in-progress`` plus
``status: needs-attention`` -- the pair the escalation in
:func:`plan_label_mutation` deliberately produces and that design section 2.1
declares legitimate ("``status: needs-attention`` always blocks admission. It may
deliberately coexist with ``status: in-progress``"). Interpreting that pair as an
unreconciled contradiction removed the documented "Needs attention ->
authorized resolution" exit and made the state permanent.

Also covers the two provenance defects that stopped the reconciler from acting
on its own evidence: an empty default trusted-poster allow-list and a
``no-work`` outcome spelling that the canonical handoff vocabulary never emits.
"""

from __future__ import annotations

from typing import Any, Mapping

import pytest

from moonmind.workflows.temporal.activities import (
    github_issue_reconciliation_activities as recon_activities,
)
from moonmind.workflows.temporal.github_issue_attempts import ATTEMPT_OUTCOMES
from moonmind.workflows.temporal.github_issue_lifecycle import (
    SETTLED_BLOCKED_MIXED,
    SETTLED_NEEDS_ATTENTION,
    STATUS_IN_PROGRESS,
    STATUS_NEEDS_ATTENTION,
    interpret_issue,
    is_selectable_candidate,
    plan_label_mutation,
    plan_transition,
)

ESCALATED_LABELS = [STATUS_IN_PROGRESS, STATUS_NEEDS_ATTENTION]


def _issue(*labels: str) -> dict[str, Any]:
    return {"state": "open", "labels": list(labels)}


# ---------------------------------------------------------------------------
# Interpretation: the deliberate escalation is Needs attention, not a conflict
# ---------------------------------------------------------------------------


def test_deliberate_attention_escalation_settles_as_needs_attention() -> None:
    interpretation = interpret_issue(_issue(*ESCALATED_LABELS))

    assert interpretation.settled == SETTLED_NEEDS_ATTENTION
    # The coexisting writer status stays visible evidence, never silently dropped.
    assert interpretation.canonical_present == frozenset(ESCALATED_LABELS)
    assert interpretation.eligible_for_implement is False
    assert interpretation.eligible_for_continuation is False
    assert "attention" in interpretation.blocked_reason.lower()


def test_escalated_issue_is_not_selectable() -> None:
    selectable, interpretation = is_selectable_candidate(_issue(*ESCALATED_LABELS))

    assert selectable is False
    assert interpretation.settled == SETTLED_NEEDS_ATTENTION


@pytest.mark.parametrize(
    "labels",
    [
        ["status: in-progress", "status: code-review"],
        ["status: code-review", "status: needs-attention"],
        ["status: in-progress", "status: recovery-needed"],
        ["status: in-progress", "status: code-review", "status: needs-attention"],
    ],
)
def test_other_canonical_combinations_still_require_reconciliation(
    labels: list[str],
) -> None:
    """Only the declared escalation pair is legitimate; the rest stay blocked."""
    assert interpret_issue(_issue(*labels)).settled == SETTLED_BLOCKED_MIXED


# ---------------------------------------------------------------------------
# Transition contract: the authorized-resolution exit must exist
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("to_target", "evidence"),
    [
        (
            "to_available",
            {
                "authorized_resolution": "operator: infrastructure repaired",
                "preserved_work_disposition": "none recorded",
                "terminal_proof": "writers stopped; no work preserved",
            },
        ),
        (
            "to_recovery_needed",
            {
                "authorized_resolution": "operator: resume preserved work",
                "preserved_work_disposition": "branch retained",
            },
        ),
        (
            "to_in_progress",
            {"authorized_resolution": "operator: re-admit under the same policy"},
        ),
    ],
)
def test_escalated_issue_has_an_authorized_resolution_exit(
    to_target: str, evidence: Mapping[str, Any]
) -> None:
    decision = plan_transition(
        from_settled=interpret_issue(_issue(*ESCALATED_LABELS)).settled,
        to_target=to_target,
        evidence=dict(evidence),
        reason="Operator resolved the escalation after repairing the deployment.",
    )

    assert decision.allowed is True, decision.summary
    assert decision.reason_code == "allowed"


def test_escalated_issue_without_authorization_still_blocks() -> None:
    decision = plan_transition(
        from_settled=interpret_issue(_issue(*ESCALATED_LABELS)).settled,
        to_target="to_available",
        evidence={},
        reason="unattended release",
    )

    assert decision.allowed is False
    assert decision.reason_code == "missing_guard"


# ---------------------------------------------------------------------------
# Mutation plan: resolving the escalation must clear the coexisting status
# ---------------------------------------------------------------------------


def test_authorized_resolution_clears_the_coexisting_in_progress_label() -> None:
    plan = plan_label_mutation(
        from_settled=SETTLED_NEEDS_ATTENTION,
        to_target="to_available",
        current_labels=[*ESCALATED_LABELS, "bug", "high priority"],
    )

    assert plan.labels_to_add == ()
    # Both canonical statuses must go, or the resolved issue re-blocks at once.
    assert set(plan.labels_to_remove) == set(ESCALATED_LABELS)
    assert plan.close_issue is False


def test_authorized_resolution_to_another_status_adds_before_removing() -> None:
    plan = plan_label_mutation(
        from_settled=SETTLED_NEEDS_ATTENTION,
        to_target="to_recovery_needed",
        current_labels=[*ESCALATED_LABELS, "reliability"],
    )

    operations = plan.ordered_operations()
    assert operations[0] == ("add", "status: recovery-needed")
    assert set(operations[1:]) == {
        ("remove", STATUS_NEEDS_ATTENTION),
        ("remove", STATUS_IN_PROGRESS),
    }


def test_attention_escalation_still_retains_the_writer_status() -> None:
    """Regression guard: escalating must not remove the old writer's status."""
    plan = plan_label_mutation(
        from_settled="in_progress",
        to_target="to_needs_attention",
        current_labels=[STATUS_IN_PROGRESS, "bug"],
    )

    assert plan.labels_to_add == (STATUS_NEEDS_ATTENTION,)
    assert plan.labels_to_remove == ()


def test_unrelated_labels_are_never_touched() -> None:
    plan = plan_label_mutation(
        from_settled=SETTLED_NEEDS_ATTENTION,
        to_target="to_available",
        current_labels=[*ESCALATED_LABELS, "bug", "statuspage"],
    )

    touched = {label for _op, label in plan.ordered_operations()}
    assert "bug" not in touched
    assert "statuspage" not in touched


# ---------------------------------------------------------------------------
# Reconciler provenance: the deployment must trust its own GitHub account
# ---------------------------------------------------------------------------

_DEPLOYMENT_LOGIN = "moonmind-bot"

_HANDOFF_COMMENT = {
    "id": "5677286184",
    "user": {"login": _DEPLOYMENT_LOGIN},
    "body": (
        "MoonMind started implementation for MoonLadderStudios/MoonMind#4376.\n\n"
        "<!-- moonmind-attempt-handoff v2 attempt=att-ad947d69f233-978f -->\n"
        "```attempt-handoff-json\n"
        '{"activity":"preparing","attemptId":"att-ad947d69f233-978f","deploymentId":'
        '"inst-f75843b465a74aa2","formatVersion":2,"internalRetryCount":0,'
        '"issueNumber":4376,"leaseRenewedAt":"2026-09-15T19:15:00+00:00",'
        '"leaseExpiresAt":"2026-09-15T19:45:00+00:00",'
        '"nextAction":"continue_implementation","operatorHold":false,'
        '"outcome":"in_progress","repository":"MoonLadderStudios/MoonMind",'
        '"retryAllowance":3,"retryPolicyVersion":1,"retryRemaining":3,'
        '"writersStopped":false}\n'
        "```\n"
    ),
}


def test_reconciler_trusts_handoffs_from_its_own_authenticated_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No operator declaration is needed: the posting account is observable."""
    monkeypatch.delenv("MOONMIND_TRUSTED_POSTERS", raising=False)

    handoffs, malformed = recon_activities._validated_handoffs(
        [_HANDOFF_COMMENT],
        repository="MoonLadderStudios/MoonMind",
        issue_number=4376,
        authenticated_login=_DEPLOYMENT_LOGIN,
    )

    assert malformed is False
    assert [item["attemptId"] for item in handoffs] == ["att-ad947d69f233-978f"]


def test_reconciler_still_rejects_a_copied_marker_from_another_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MOONMIND_TRUSTED_POSTERS", raising=False)
    forged = {**_HANDOFF_COMMENT, "user": {"login": "someone-else"}}

    handoffs, malformed = recon_activities._validated_handoffs(
        [forged],
        repository="MoonLadderStudios/MoonMind",
        issue_number=4376,
        authenticated_login=_DEPLOYMENT_LOGIN,
    )

    assert handoffs == []
    assert malformed is True


def test_authenticated_login_joins_the_declared_allow_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MOONMIND_TRUSTED_POSTERS", "declared-operator")

    posters = recon_activities._trusted_posters(authenticated_login=_DEPLOYMENT_LOGIN)

    assert [poster.lower() for poster in posters] == [
        "declared-operator",
        _DEPLOYMENT_LOGIN,
    ]


def test_authenticated_login_is_not_duplicated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MOONMIND_TRUSTED_POSTERS", _DEPLOYMENT_LOGIN.upper())

    posters = recon_activities._trusted_posters(authenticated_login=_DEPLOYMENT_LOGIN)

    assert len(posters) == 1


# ---------------------------------------------------------------------------
# Reconciler evidence: the canonical no-work outcome must be recognized
# ---------------------------------------------------------------------------


def test_no_work_is_the_canonical_outcome_spelling() -> None:
    assert "no_work" in ATTEMPT_OUTCOMES
    assert "no-work" not in ATTEMPT_OUTCOMES


def test_no_work_handoff_yields_explicit_no_work_preservation_evidence() -> None:
    evidence = recon_activities._preservation_evidence_from_handoff(
        newest={"outcome": "no_work"},
        preserved={},
        pr_url="",
        pr_state={},
    )

    assert evidence == {
        "save_method": "explicit_no_work",
        "trustworthy_no_work": True,
    }
