"""A deployment that cannot start a runtime must not spend the issue's retries.

The 2026-09-15..17 outage burned the whole MoonMind backlog: every scheduled
attempt died at ``OMNIGENT_HOST_LAUNCH_FAILED`` before any agent started, yet
each one consumed one of the issue's three portable retry slots. After the
third, ``choose_disposition`` saw ``budgetExhausted`` and escalated the *issue*
to needs-attention for a fault that belonged entirely to the deployment --
contradicting design section 2 ("Ordinary worker disappearance is not routed to
``status: needs-attention``; attention is reserved for a concrete unresolved
decision or safety problem").

An attempt where the deployment never started a runtime is evidence about the
deployment, not about the issue. It stays in lineage, it does not consume the
allowance, and it backs the issue off with the portable cooldown that the
handoff already carries so a broken deployment cannot hammer one issue.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from moonmind.workflows.temporal.github_issue_attempt import derive_retry_state
from moonmind.workflows.temporal.github_issue_attempts import (
    ATTEMPT_OUTCOMES,
    OUTCOME_RUNTIME_UNAVAILABLE,
    RUNTIME_UNAVAILABLE_COOLDOWN_SECONDS,
    AttemptHandoff,
    compute_effective_retry,
)
from moonmind.workflows.temporal.github_issue_finalization import (
    DISPOSITION_AVAILABLE,
    DISPOSITION_NEEDS_ATTENTION,
    choose_disposition,
)

REPO = "MoonLadderStudios/MoonMind"


def _handoff(attempt_id: str, outcome: str, *, predecessor: str = "", cooldown: str = "") -> AttemptHandoff:
    return AttemptHandoff(
        attempt_id=attempt_id,
        deployment_id="inst-test",
        repository=REPO,
        issue_number=973,
        predecessor_attempt_id=predecessor,
        activity="releasing",
        outcome=outcome,
        retry_allowance=3,
        cooldown_until=cooldown,
    )


def _chain(*outcomes: str, cooldown: str = "") -> list[AttemptHandoff]:
    chain: list[AttemptHandoff] = []
    previous = ""
    for index, outcome in enumerate(outcomes):
        attempt_id = f"att-00000000000{index}-aaaa"
        last = index == len(outcomes) - 1
        chain.append(
            _handoff(
                attempt_id,
                outcome,
                predecessor=previous,
                cooldown=cooldown if last else "",
            )
        )
        previous = attempt_id
    return chain


def _iso(offset_seconds: int) -> str:
    return (datetime.now(UTC) + timedelta(seconds=offset_seconds)).isoformat()


# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------


def test_runtime_unavailable_is_a_canonical_attempt_outcome() -> None:
    assert OUTCOME_RUNTIME_UNAVAILABLE == "runtime_unavailable"
    assert OUTCOME_RUNTIME_UNAVAILABLE in ATTEMPT_OUTCOMES


# ---------------------------------------------------------------------------
# Portable allowance (compute_effective_retry)
# ---------------------------------------------------------------------------


def test_runtime_unavailable_attempts_do_not_consume_the_allowance() -> None:
    """The exact shape of the outage: three launches, no agent ever started."""
    decision = compute_effective_retry(
        _chain(*[OUTCOME_RUNTIME_UNAVAILABLE] * 3), max_attempts=3
    )

    assert decision.allowed is True
    assert decision.reason_code == "allowed"
    assert decision.remaining == 3


def test_runtime_unavailable_attempts_are_still_retained_in_lineage() -> None:
    """Not counted is not erased: the history stays observable."""
    state = derive_retry_state(
        [handoff.to_dict() for handoff in _chain(*[OUTCOME_RUNTIME_UNAVAILABLE] * 3)],
        policy={"maxAttempts": 3},
    )

    assert state["blocked"] is False
    assert state["attemptsObserved"] == 3
    assert state["failuresRetained"] == 0
    assert state["remainingAllowance"] == 3


def test_real_attempts_alongside_runtime_failures_still_count() -> None:
    decision = compute_effective_retry(
        _chain(
            "failed",
            OUTCOME_RUNTIME_UNAVAILABLE,
            OUTCOME_RUNTIME_UNAVAILABLE,
            "no_work",
        ),
        max_attempts=3,
    )

    assert decision.allowed is True
    assert decision.remaining == 1


def test_genuine_failures_still_exhaust_the_allowance() -> None:
    """Regression guard: the budget must still bound real work attempts."""
    decision = compute_effective_retry(
        _chain("failed", "failed", "no_work"), max_attempts=3
    )

    assert decision.allowed is False
    assert decision.reason_code == "budget_exhausted"
    assert decision.remaining == 0


def test_derive_retry_state_does_not_charge_runtime_failures() -> None:
    state = derive_retry_state(
        [
            handoff.to_dict()
            for handoff in _chain(
                "failed", OUTCOME_RUNTIME_UNAVAILABLE, OUTCOME_RUNTIME_UNAVAILABLE
            )
        ],
        policy={"maxAttempts": 3},
    )

    assert state["blocked"] is False
    assert state["failuresRetained"] == 1
    assert state["remainingAllowance"] == 2


# ---------------------------------------------------------------------------
# Cooldown: a broken deployment backs off instead of hammering one issue
# ---------------------------------------------------------------------------


def test_a_live_cooldown_defers_the_candidate_without_spending_the_budget() -> None:
    decision = compute_effective_retry(
        _chain(OUTCOME_RUNTIME_UNAVAILABLE, cooldown=_iso(600)),
        max_attempts=3,
        now_epoch=datetime.now(UTC).timestamp(),
    )

    assert decision.allowed is False
    assert decision.reason_code == "cooling_down"
    # Deferred, never exhausted: the allowance is intact for the next window.
    assert decision.remaining == 3


def test_an_elapsed_cooldown_restores_the_candidate_with_no_operator_act() -> None:
    decision = compute_effective_retry(
        _chain(OUTCOME_RUNTIME_UNAVAILABLE, cooldown=_iso(-600)),
        max_attempts=3,
        now_epoch=datetime.now(UTC).timestamp(),
    )

    assert decision.allowed is True
    assert decision.reason_code == "allowed"


def test_cooldown_is_ignored_when_no_clock_is_supplied() -> None:
    """Callers that pass no clock keep their existing behavior."""
    decision = compute_effective_retry(
        _chain(OUTCOME_RUNTIME_UNAVAILABLE, cooldown=_iso(600)), max_attempts=3
    )

    assert decision.allowed is True


def test_an_unparseable_cooldown_never_blocks() -> None:
    decision = compute_effective_retry(
        _chain(OUTCOME_RUNTIME_UNAVAILABLE, cooldown="whenever"),
        max_attempts=3,
        now_epoch=datetime.now(UTC).timestamp(),
    )

    assert decision.allowed is True


def test_exhausted_budget_outranks_a_live_cooldown() -> None:
    """A real exhaustion is the more significant fact and keeps its reason."""
    decision = compute_effective_retry(
        _chain("failed", "failed", "failed", cooldown=_iso(600)),
        max_attempts=3,
        now_epoch=datetime.now(UTC).timestamp(),
    )

    assert decision.allowed is False
    assert decision.reason_code == "budget_exhausted"


def test_operator_hold_outranks_a_live_cooldown() -> None:
    chain = _chain(OUTCOME_RUNTIME_UNAVAILABLE, cooldown=_iso(600))
    from dataclasses import replace

    chain[-1] = replace(chain[-1], operator_hold=True, operator_hold_reason="paused")
    decision = compute_effective_retry(
        chain, max_attempts=3, now_epoch=datetime.now(UTC).timestamp()
    )

    assert decision.allowed is False
    assert decision.reason_code == "operator_hold"


def test_the_backoff_window_is_bounded_and_positive() -> None:
    assert 0 < RUNTIME_UNAVAILABLE_COOLDOWN_SECONDS <= 6 * 3600


# ---------------------------------------------------------------------------
# Disposition: a deployment fault does not escalate the issue
# ---------------------------------------------------------------------------


def test_runtime_unavailable_releases_to_available_instead_of_attention() -> None:
    decision = choose_disposition(
        {
            "trustworthyNoWork": True,
            "runtimeUnavailable": True,
            "freshRetryAllowed": True,
        }
    )

    assert decision["disposition"] == DISPOSITION_AVAILABLE
    assert decision["reasonCode"] == "runtime_unavailable"
    assert decision["schedulesReplacement"] is False
    assert "deployment" in decision["summary"].lower()


def test_runtime_unavailable_is_not_overridden_by_a_stale_budget_claim() -> None:
    """No agent started, so an exhausted-looking budget is not the issue's fault."""
    decision = choose_disposition(
        {"runtimeUnavailable": True, "budgetExhausted": True}
    )

    assert decision["disposition"] == DISPOSITION_AVAILABLE
    assert decision["reasonCode"] == "runtime_unavailable"


@pytest.mark.parametrize(
    "evidence",
    [
        {"runtimeUnavailable": True, "operatorHold": True},
        {"runtimeUnavailable": True, "intentionalCancellation": True},
    ],
)
def test_an_explicit_hold_still_outranks_a_runtime_failure(evidence: dict) -> None:
    decision = choose_disposition(evidence)

    assert decision["disposition"] == DISPOSITION_NEEDS_ATTENTION
    assert decision["reasonCode"] == "cancellation_hold"


def test_genuine_budget_exhaustion_still_escalates() -> None:
    decision = choose_disposition({"budgetExhausted": True})

    assert decision["disposition"] == DISPOSITION_NEEDS_ATTENTION
    assert decision["reasonCode"] == "unsafe_or_exhausted"


# ---------------------------------------------------------------------------
# Producers: the terminal handoff records the outcome and its back-off
# ---------------------------------------------------------------------------


def test_terminal_handoff_records_runtime_unavailable_with_a_cooldown() -> None:
    from moonmind.workflows.temporal.activities.github_issue_finalization_activities import (
        terminal_attempt_outcome,
    )

    outcome, next_action, cooldown = terminal_attempt_outcome(
        disposition=DISPOSITION_AVAILABLE,
        disposition_evidence={"runtimeUnavailable": True},
        now_epoch=0.0,
    )

    assert outcome == OUTCOME_RUNTIME_UNAVAILABLE
    assert next_action == "fresh_retry"
    assert cooldown, "a runtime-unavailable attempt must carry its back-off"


def test_terminal_handoff_keeps_no_work_for_an_ordinary_release() -> None:
    from moonmind.workflows.temporal.activities.github_issue_finalization_activities import (
        terminal_attempt_outcome,
    )

    outcome, next_action, cooldown = terminal_attempt_outcome(
        disposition=DISPOSITION_AVAILABLE,
        disposition_evidence={"trustworthyNoWork": True},
        now_epoch=0.0,
    )

    assert outcome == "no_work"
    assert next_action == "fresh_retry"
    assert cooldown == ""


def test_terminal_handoff_keeps_failed_for_an_escalation() -> None:
    from moonmind.workflows.temporal.activities.github_issue_finalization_activities import (
        terminal_attempt_outcome,
    )

    outcome, _next_action, cooldown = terminal_attempt_outcome(
        disposition=DISPOSITION_NEEDS_ATTENTION,
        disposition_evidence={"budgetExhausted": True},
        now_epoch=0.0,
    )

    assert outcome == "failed"
    assert cooldown == ""


def test_recovery_without_a_started_agent_reports_a_runtime_fault() -> None:
    """The sweep already proves 'no agent started'; it must say so."""
    from moonmind.workflows.temporal.github_issue_claim_recovery import (
        recovery_disposition_evidence,
    )

    evidence = recovery_disposition_evidence(
        agent_started=False, remaining=0, runtime_unavailable=True
    )

    assert evidence["runtimeUnavailable"] is True
    assert evidence.get("budgetExhausted") is not True
    assert choose_disposition(evidence)["disposition"] == DISPOSITION_AVAILABLE


def test_a_failure_that_is_not_a_runtime_fault_still_costs_an_attempt() -> None:
    """No agent started is not enough: the failure must name a runtime fault."""
    from moonmind.workflows.temporal.github_issue_claim_recovery import (
        recovery_disposition_evidence,
    )

    evidence = recovery_disposition_evidence(
        agent_started=False, remaining=0, runtime_unavailable=False
    )

    assert evidence.get("runtimeUnavailable") is not True
    assert evidence["budgetExhausted"] is True
    assert choose_disposition(evidence)["disposition"] == DISPOSITION_NEEDS_ATTENTION


def test_recovery_with_a_started_agent_keeps_the_existing_budget_rules() -> None:
    from moonmind.workflows.temporal.github_issue_claim_recovery import (
        recovery_disposition_evidence,
    )

    exhausted = recovery_disposition_evidence(agent_started=True, remaining=0)
    assert exhausted["budgetExhausted"] is True
    assert exhausted["freshRetryAllowed"] is False
    assert choose_disposition(exhausted)["disposition"] == DISPOSITION_NEEDS_ATTENTION

    allowed = recovery_disposition_evidence(agent_started=True, remaining=2)
    assert allowed["budgetExhausted"] is False
    assert allowed["freshRetryAllowed"] is True
    assert choose_disposition(allowed)["disposition"] == DISPOSITION_AVAILABLE


def test_reconstruction_defers_a_candidate_inside_its_backoff_window() -> None:
    """Selection rotates past a backed-off candidate instead of re-announcing."""
    from moonmind.workflows.temporal.github_issue_attempts import (
        reconstruct_from_comments,
        render_attempt_comment,
    )

    handoff = _chain(OUTCOME_RUNTIME_UNAVAILABLE, cooldown=_iso(1800))[0]
    comments = [
        {
            "id": "1",
            "user": {"login": "moonmind-bot"},
            "body": render_attempt_comment(handoff),
        }
    ]
    kwargs = dict(
        expected_repository=REPO,
        expected_issue_number=973,
        trusted_posters=["moonmind-bot"],
        max_attempts=3,
    )

    deferred = reconstruct_from_comments(
        comments, **kwargs, now_epoch=datetime.now(UTC).timestamp()
    )
    assert deferred.outcome == "needs_attention"
    assert deferred.reason_code == "cooling_down"
    # The allowance is untouched, so the next window admits it normally.
    assert deferred.retry_remaining == 3

    elapsed = reconstruct_from_comments(comments, **kwargs, now_epoch=0.0)
    assert elapsed.outcome == "reconstructed"


# ---------------------------------------------------------------------------
# The runtime fault is recognized from MoonMind's own typed failure code
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "detail",
    [
        "Provider request failed with provider error OMNIGENT_HOST_LAUNCH_FAILED: "
        "Admitted Omnigent execution-plan dispatch failed: restricted-egress "
        "backend attestation failed (retryRecommendation: repair_host_launcher)",
        "OMNIGENT_HOST_CAPACITY_UNAVAILABLE: no capacity for this allocation",
        "OMNIGENT_HOST_REGISTRATION_TIMEOUT: host never registered",
        "OMNIGENT_GENERIC_REALIZER_NOT_READY: realizer unavailable",
    ],
)
def test_typed_host_provisioning_codes_are_runtime_faults(detail: str) -> None:
    from moonmind.workflows.temporal.github_issue_claim_recovery import (
        runtime_provisioning_failure,
    )

    assert runtime_provisioning_failure(detail) is True


@pytest.mark.parametrize(
    "detail",
    [
        "",
        "ValueError: the acceptance report was malformed",
        "OMNIGENT_AGENT_PROFILE_INVALID: the requested profile is not valid",
        "OMNIGENT_MODEL_UNAVAILABLE: the selected model is not available",
        "Step verification failed: tests did not pass",
    ],
)
def test_other_failures_are_not_treated_as_runtime_faults(detail: str) -> None:
    """A failure that says something about the work must still cost an attempt."""
    from moonmind.workflows.temporal.github_issue_claim_recovery import (
        runtime_provisioning_failure,
    )

    assert runtime_provisioning_failure(detail) is False


def test_issue_claim_capacity_blocked_is_a_runtime_fault() -> None:
    """Capacity-blocked backoff with no agent started says nothing about the issue."""
    from moonmind.workflows.temporal.github_issue_claim_recovery import (
        runtime_provisioning_failure,
    )

    assert (
        runtime_provisioning_failure(
            "ISSUE_CLAIM_CAPACITY_BLOCKED: queued behind unavailable provider "
            "capacity; backing off"
        )
        is True
    )


def test_recovery_codes_stay_in_sync_with_the_failure_taxonomy() -> None:
    from moonmind.omnigent.harness_platform.failures import HarnessPlatformFailure
    from moonmind.workflows.temporal.github_issue_claim_recovery import (
        RUNTIME_PROVISIONING_FAILURES,
    )

    known = {str(member) for member in HarnessPlatformFailure}
    assert RUNTIME_PROVISIONING_FAILURES
    assert set(RUNTIME_PROVISIONING_FAILURES) <= known
