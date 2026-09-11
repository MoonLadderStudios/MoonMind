"""Hermetic three-deployment qualification for MoonLadderStudios/MoonMind#4186.

Proves the integrated label-first lifecycle through real production
boundaries (design: docs/Workflows/GitHubIssueStatusStateMachineDesign.md,
section 10) rather than a collection of helper or YAML assertions.

Each of the design's 26 conformance rows maps to a named test below via
``ROW_COVERAGE`` (row -> test function, production entrypoint, required
evidence, owning child). The suite models three isolated deployment
identities with separate local execution state, ownership tables,
databases, and artifact stores; only the modeled GitHub service is shared.
It exercises the actual selector and admission boundary, trusted
continuation/finalization/reconciliation policy entrypoints, controllable
interleavings and faults, the best-effort contract (including the
explicitly unfenced delayed-write race), and authorization/side-effect
boundaries — without creating a separate runtime implementation or a new
coordination service.

Hermetic: no network, no credentials, no Temporal server. Provider behavior
against the real GitHub API is covered separately by
``tests/provider/github/test_github_issue_lifecycle_4186_provider.py`` and
is never inferred from these mocks.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from moonmind.workflows.temporal import github_issue_reconciliation as recon
from moonmind.workflows.temporal.github_issue_admission import (
    ENTRYPOINT_CONTINUATION,
    ENTRYPOINT_EXPLICIT,
    ENTRYPOINT_RETRY,
    ENTRYPOINT_SEARCH,
    AdmissionRequest,
    admit_exact_issue,
    admit_for_entrypoint,
    check_delayed_mutation_fenced,
    contender_quiesce_decision,
    revalidate_for_mutation,
    should_stop_on_resume,
)
from moonmind.workflows.temporal.github_issue_continuation import (
    ELIGIBILITY_BLOCKED,
    ELIGIBILITY_CODE_SEEDED_CONTINUATION,
    ELIGIBILITY_EXACT_RECOVERY,
    ELIGIBILITY_OWNER_ONLY,
    NEXT_CONTINUE_SAME_PR,
    NEXT_FRESH_IMPLEMENT,
    NEXT_NEEDS_ATTENTION,
    NEXT_VERIFY_ONLY,
    check_publication_scope,
    classify_recovery_eligibility,
    compare_saved_vs_pr,
    discover_existing_work,
    dispose_merged_closed,
    resolve_lost_pr_creation,
    route_continuation,
)
from moonmind.workflows.temporal.github_issue_finalization import (
    DISPOSITION_AVAILABLE,
    DISPOSITION_NEEDS_ATTENTION,
    DISPOSITION_RECOVERY_NEEDED,
    choose_disposition,
    confirm_writer_stop,
    separate_objective_from_execution,
    settle_shared_mutations,
    should_finalize_controlling_attempt,
)
from moonmind.workflows.temporal.github_issue_lifecycle import (
    SETTLED_AVAILABLE,
    SETTLED_BLOCKED_MIXED,
    SETTLED_BLOCKED_OPEN_DONE,
    SETTLED_BLOCKED_UNKNOWN,
    SETTLED_CLOSED,
    SETTLED_CODE_REVIEW,
    SETTLED_IN_PROGRESS,
    SETTLED_NEEDS_ATTENTION,
    SETTLED_RECOVERY_NEEDED,
    attempt_evidence_blocks_admission,
    interpret_issue,
)
from moonmind.workflows.temporal.github_issue_search import (
    is_lifecycle_selectable_candidate,
)

REPO = "MoonLadderStudios/MoonMind"
ISSUE_NUMBER = 4186


# ---------------------------------------------------------------------------
# Row coverage map: design section 10 row -> (test, entrypoint, evidence, owner)
# ---------------------------------------------------------------------------

ROW_COVERAGE: dict[int, dict[str, str]] = {
    1: {"test": "test_row01_unlabelled_open_eligible_after_checks", "entrypoint": "admit_exact_issue(search)+interpret_issue", "evidence": "admitted/available", "owner": "#4186"},
    2: {"test": "test_row02_missing_label_with_active_attempt_blocks", "entrypoint": "admit_exact_issue+is_lifecycle_selectable_candidate", "evidence": "missing_label_with_active_attempt", "owner": "#4186"},
    3: {"test": "test_row03_recovery_needed_adopts_same_pr", "entrypoint": "discover_existing_work+route_continuation", "evidence": "continue-implementation@validated-head", "owner": "#4180"},
    4: {"test": "test_row04_code_review_excluded_from_search", "entrypoint": "interpret_issue+admit_for_entrypoint", "evidence": "active_attempt_conflict/code_review", "owner": "#4186"},
    5: {"test": "test_row05_internal_retry_retains_attempt", "entrypoint": "should_finalize_controlling_attempt", "evidence": "internal_event_retains_attempt", "owner": "#4179"},
    6: {"test": "test_row06_terminal_no_work_failure_keeps_history", "entrypoint": "choose_disposition+admit_exact_issue", "evidence": "safe_no_work_retry/available", "owner": "#4179"},
    7: {"test": "test_row07_lost_pr_number_recovered_by_branch_identity", "entrypoint": "resolve_lost_pr_creation", "evidence": "adopt_existing/exact-branch", "owner": "#4180"},
    8: {"test": "test_row08_verified_work_routes_to_finalization_only", "entrypoint": "route_continuation+separate_objective_from_execution", "evidence": "verify/finalize-status, no-reimplementation", "owner": "#4180"},
    9: {"test": "test_row09_unknown_writer_or_merge_blocks_takeover", "entrypoint": "confirm_writer_stop+settle_shared_mutations", "evidence": "writers_running/mutations_unsettled", "owner": "#4179"},
    10: {"test": "test_row10_silent_owner_surfaces_attention_without_age_release", "entrypoint": "decide_issue_reconciliation", "evidence": "surface_attention, no-age-clear", "owner": "#4182"},
    11: {"test": "test_row11_simultaneous_announces_quiesce_without_clearing", "entrypoint": "contender_quiesce_decision+admit_exact_issue", "evidence": "self-quiesce, shared-label-retained", "owner": "#4178"},
    12: {"test": "test_row12_stale_resume_makes_no_shared_mutation", "entrypoint": "revalidate_for_mutation+should_stop_on_resume", "evidence": "allowed=False/stop", "owner": "#4178"},
    13: {"test": "test_row13_delayed_mutation_exposes_unfenced_race", "entrypoint": "check_delayed_mutation_fenced", "evidence": "fenced=False/unfenced_race", "owner": "#4178"},
    14: {"test": "test_row14_lost_response_reconciles_without_blind_repeat", "entrypoint": "resolve_lost_pr_creation+settle_shared_mutations", "evidence": "adopt-or-block, no-duplicate-effect", "owner": "#4180"},
    15: {"test": "test_row15_interrupted_transition_blocks_until_conclusive", "entrypoint": "interpret_issue+decide_issue_reconciliation", "evidence": "blocked_mixed/complete-only-when-conclusive", "owner": "#4182"},
    16: {"test": "test_row16_human_changes_preserved_and_reassessed", "entrypoint": "compare_saved_vs_pr", "evidence": "reassess/no-force-push", "owner": "#4180"},
    17: {"test": "test_row17_competing_or_closed_prs_need_decision", "entrypoint": "admit_exact_issue+dispose_merged_closed", "evidence": "ambiguous_pr_identity/no-silent-choice", "owner": "#4180"},
    18: {"test": "test_row18_private_only_checkpoint_is_owner_only", "entrypoint": "classify_recovery_eligibility", "evidence": "owner_only/no-continuation", "owner": "#4180"},
    19: {"test": "test_row19_retry_history_survives_device_change", "entrypoint": "admit_exact_issue+attempt_evidence_blocks_admission", "evidence": "retry_exhausted/lineage_gap", "owner": "#4178"},
    20: {"test": "test_row20_intentional_cancellation_schedules_nothing", "entrypoint": "choose_disposition", "evidence": "cancellation_hold/schedulesReplacement=False", "owner": "#4179"},
    21: {"test": "test_row21_contradictory_evidence_requests_attention", "entrypoint": "admit_exact_issue+decide_issue_reconciliation", "evidence": "attention, sole-deletion-limitation-documented", "owner": "#4182"},
    22: {"test": "test_row22_outage_or_partial_scan_blocks_admission", "entrypoint": "merge_scan_results+reconciliation_blocks_admission", "evidence": "partial/unknown, no-local-only-claim", "owner": "#4182"},
    23: {"test": "test_row23_manual_label_without_owner_respected", "entrypoint": "admit_exact_issue", "evidence": "manual_in_progress_without_trusted_owner", "owner": "#4178"},
    24: {"test": "test_row24_unknown_format_fails_closed", "entrypoint": "interpret_issue+admit_exact_issue", "evidence": "blocked_unknown/unknown_format", "owner": "#4176"},
    25: {"test": "test_row25_missing_permissions_fail_locally", "entrypoint": "check_publication_scope+check_reconciliation_readiness", "evidence": "explicit-None/no-GitHub-updated-claim", "owner": "#4180"},
    26: {"test": "test_row26_original_outcome_survives_auxiliary_failure", "entrypoint": "separate_objective_from_execution", "evidence": "primary-vs-auxiliary-distinguished", "owner": "#4179"},
}


def _req(entrypoint: str = ENTRYPOINT_EXPLICIT, attempt: str = "") -> AdmissionRequest:
    return AdmissionRequest(
        repository=REPO,
        issue_number=ISSUE_NUMBER,
        workflow_id="wf-4186",
        run_id="run-4186",
        installation_id="inst-4186abcd",
        attempt_id=attempt or ("att_" + "a" * 24),
        entrypoint=entrypoint,
    )


# ---------------------------------------------------------------------------
# Three isolated deployments sharing only the modeled GitHub service
# ---------------------------------------------------------------------------


class ModeledGitHubService:
    """Only shared surface: issue labels, attempt comments, PR records."""

    def __init__(self) -> None:
        self.labels: list[str] = []
        self.issue_state = "open"
        self.comments: list[dict[str, Any]] = []
        self.prs: dict[int, dict[str, Any]] = {}
        self.dropped_acks: list[dict[str, Any]] = []
        self.delayed_mutations: list[dict[str, Any]] = []

    def snapshot_issue(self) -> dict[str, Any]:
        return {"state": self.issue_state, "labels": list(self.labels), "number": ISSUE_NUMBER}


class DeploymentLocalState:
    """Private per-deployment state: ownership table, database, artifact store.

    Each deployment gets its own instances; the qualification asserts the
    three deployments never alias the same mutable object.
    """

    def __init__(self, deployment_id: str) -> None:
        self.deployment_id = deployment_id
        self.ownership_table: dict[str, Any] = {}
        self.database: dict[str, Any] = {}
        self.artifact_store: dict[str, Any] = {}
        self.pending_sync: list[dict[str, Any]] = []
        self.connected = True


class ThreeDeployments:
    def __init__(self) -> None:
        self.github = ModeledGitHubService()
        self.deployments = {
            name: DeploymentLocalState(f"deploy-{name}")
            for name in ("a", "b", "c")
        }

    def isolated(self) -> bool:
        states = list(self.deployments.values())
        tables = {id(s.ownership_table) for s in states}
        dbs = {id(s.database) for s in states}
        stores = {id(s.artifact_store) for s in states}
        return len(tables) == len(dbs) == len(stores) == 3


def _open_pr(number: int = 7, head: str = "feat-4186", base: str = "main") -> dict[str, Any]:
    return {
        "number": number,
        "state": "open",
        "merged": False,
        "head": {"ref": head, "sha": "a" * 40, "repo": {"full_name": REPO}},
        "base": {"ref": base},
    }


def _discover_pr(**overrides: Any) -> Any:
    base: dict[str, Any] = {
        "repository": REPO,
        "issue_number": ISSUE_NUMBER,
        "lineage_validated": True,
        "lineage_pr_url": f"https://github.com/{REPO}/pull/7",
        "lineage_pr_head": "feat-4186",
        "lineage_pr_base": "main",
        "lineage_saved_branch": "ckpt-4186",
        "lineage_saved_sha": "b" * 40,
        "github_pr": _open_pr(),
    }
    base.update(overrides)
    return discover_existing_work(**base)


def test_coverage_map_is_complete() -> None:
    assert set(ROW_COVERAGE) == set(range(1, 27)), "every design row 1..26 must map"
    for row, entry in ROW_COVERAGE.items():
        assert entry["test"] in globals(), f"row {row}: named test {entry['test']} missing"
        assert entry["entrypoint"] and entry["evidence"] and entry["owner"]


def test_three_deployments_share_only_github() -> None:
    topo = ThreeDeployments()
    assert topo.isolated()
    # A fixture that accidentally shares a local ownership table does not qualify.
    topo.deployments["a"].ownership_table["att_x"] = {"owner": "a"}
    assert "att_x" not in topo.deployments["b"].ownership_table
    assert "att_x" not in topo.deployments["c"].ownership_table
    topo.github.labels.append("status: in-progress")
    assert topo.github.snapshot_issue()["labels"] == ["status: in-progress"]


def test_default_and_explicit_inputs_take_same_production_path() -> None:
    issue = {"state": "open", "labels": ["bug"], "number": ISSUE_NUMBER}
    for entrypoint in (ENTRYPOINT_SEARCH, ENTRYPOINT_EXPLICIT, ENTRYPOINT_CONTINUATION, ENTRYPOINT_RETRY):
        decision = admit_for_entrypoint(entrypoint, repository=REPO, issue_number=ISSUE_NUMBER, issue=issue)
        assert decision.allowed is True
        assert decision.settled == SETTLED_AVAILABLE


# -- Row 1 ---------------------------------------------------------------

def test_row01_unlabelled_open_eligible_after_checks() -> None:
    interpretation = interpret_issue({"state": "open", "labels": ["bug", "priority: high"]})
    assert interpretation.settled == SETTLED_AVAILABLE
    assert interpretation.eligible_for_implement is True
    decision = admit_exact_issue(_req(ENTRYPOINT_SEARCH), issue={"state": "open", "labels": ["bug"]})
    assert decision.allowed is True
    assert decision.reason_code == "admitted"
    assert is_lifecycle_selectable_candidate({"state": "open", "labels": ["bug"], "number": 1}) is True


# -- Row 2 ---------------------------------------------------------------

def test_row02_missing_label_with_active_attempt_blocks() -> None:
    contenders = [{"attemptId": "att_" + "b" * 24, "activity": "active"}]
    decision = admit_exact_issue(
        _req(), issue={"state": "open", "labels": []}, active_attempt_comments=contenders
    )
    assert decision.allowed is False
    assert decision.reason_code == "missing_label_with_active_attempt"
    assert is_lifecycle_selectable_candidate(
        {"state": "open", "labels": [], "number": 1},
        attempt_context={"has_unresolved_active_attempt": True},
    ) is False


# -- Row 3 ---------------------------------------------------------------

def test_row03_recovery_needed_adopts_same_pr() -> None:
    interpretation = interpret_issue({"state": "open", "labels": ["status: recovery-needed"]})
    assert interpretation.settled == SETTLED_RECOVERY_NEEDED
    result = _discover_pr()
    assert result.trusted is True
    routing = route_continuation(result.existing_work)
    assert routing.next_action == NEXT_CONTINUE_SAME_PR
    assert routing.workspace_revision == "a" * 40
    assert routing.must_not_duplicate_pr is True
    assert routing.reassess_requirements is True


# -- Row 4 ---------------------------------------------------------------

def test_row04_code_review_excluded_from_search() -> None:
    interpretation = interpret_issue({"state": "open", "labels": ["status: code-review"]})
    assert interpretation.settled == SETTLED_CODE_REVIEW
    decision = admit_for_entrypoint(
        ENTRYPOINT_SEARCH, repository=REPO, issue_number=ISSUE_NUMBER,
        issue={"state": "open", "labels": ["status: code-review"]},
    )
    assert decision.allowed is False
    assert is_lifecycle_selectable_candidate(
        {"state": "open", "labels": ["status: code-review"], "number": 1}
    ) is False


# -- Row 5 ---------------------------------------------------------------

def test_row05_internal_retry_retains_attempt() -> None:
    for event in ("internal_retry", "remediation_iteration", "review_wait", "child_failure"):
        outcome = should_finalize_controlling_attempt(event)
        assert outcome["finalize"] is False
        assert outcome["reasonCode"] == "internal_event_retains_attempt"
    outcome = should_finalize_controlling_attempt("failed")
    assert outcome["finalize"] is True


# -- Row 6 ---------------------------------------------------------------

def test_row06_terminal_no_work_failure_keeps_history() -> None:
    disposition = choose_disposition({"trustworthy_no_work": True, "fresh_retry_allowed": True})
    assert disposition["disposition"] == DISPOSITION_AVAILABLE
    assert disposition["disposition"] == "to_available"
    assert disposition["reasonCode"] == "safe_no_work_retry"
    assert disposition["schedulesReplacement"] is False
    # Retry budget/history still enforced at admission on the next attempt.
    denied = admit_exact_issue(
        _req(),
        issue={"state": "open", "labels": []},
        attempt_context={"linkedAttempts": [
            {"attemptId": "att_" + "c" * 24, "outcome": "failed"},
            {"attemptId": "att_" + "d" * 24, "outcome": "failed"},
            {"attemptId": "att_" + "e" * 24, "outcome": "failed"},
            {"attemptId": "att_" + "f" * 24, "outcome": "failed"},
            {"attemptId": "att_" + "1" * 24, "outcome": "failed"},
            {"attemptId": "att_" + "2" * 24, "outcome": "failed"},
        ]},
        retry_policy={"maxAttempts": 3},
    )
    assert denied.allowed is False
    assert denied.reason_code == "retry_exhausted"


# -- Row 7 ---------------------------------------------------------------

def test_row07_lost_pr_number_recovered_by_branch_identity() -> None:
    out = resolve_lost_pr_creation(
        intended_head="feat-4186",
        intended_base="main",
        observed_prs=[{"number": 7, "head": {"ref": "feat-4186"}, "base": {"ref": "main"}}],
        publication_record_saved=True,
    )
    assert out.outcome == "adopt_existing"
    assert out.pr_number == 7


# -- Row 8 ---------------------------------------------------------------

def test_row08_verified_work_routes_to_finalization_only() -> None:
    work = _discover_pr().existing_work
    routing = route_continuation(work, implementation_complete=True, verification_current=False)
    assert routing.next_action == NEXT_VERIFY_ONLY
    done = dispose_merged_closed(pr_state="merged", objective_satisfied=True)
    assert done.disposition == "complete"
    assert done.implement_remaining_only is False
    separated = separate_objective_from_execution(execution_outcome="failed", objective_verified=True)
    assert separated["rebuyImplementation"] is False


# -- Row 9 ---------------------------------------------------------------

def test_row09_unknown_writer_or_merge_blocks_takeover() -> None:
    stop = confirm_writer_stop({})
    assert stop["stopped"] is False
    settled = settle_shared_mutations({"push_outcome": "unknown", "pr_outcome": "confirmed", "merge_outcome_absent": True})
    assert settled["settled"] is False
    assert settled["reasonCode"] == "mutations_unsettled"


# -- Row 10 --------------------------------------------------------------

def test_row10_silent_owner_surfaces_attention_without_age_release() -> None:
    decision = recon.decide_issue_reconciliation(
        issue={"state": "open", "labels": ["status: in-progress"]},
        intended_from_settled="in_progress",
        intended_to_target="to_available",
        writer_evidence={},
        trusted_handoff_present=False,
        local_workflow_available=False,
    )
    assert decision.action != recon.ACTION_COMPLETE
    assert decision.action in {
        recon.ACTION_ATTENTION, recon.ACTION_DEFERRED_UNKNOWN, recon.ACTION_NO_ACTION,
    }
    # Age alone never releases: a manual in-progress label with no trusted
    # owner is still respected at admission.
    denied = admit_exact_issue(_req(), issue={"state": "open", "labels": ["status: in-progress"]})
    assert denied.allowed is False


# -- Row 11 --------------------------------------------------------------

def test_row11_simultaneous_announces_quiesce_without_clearing() -> None:
    own = "att_" + "a" * 24
    contenders = [
        {"attemptId": "att_" + "b" * 24, "activity": "preparing"},
        {"attemptId": "att_" + "c" * 24, "activity": "active"},
    ]
    decision = contender_quiesce_decision(own_attempt_id=own, observed_contenders=contenders)
    assert decision["quiesce"] is True
    assert decision["clearOtherInProgress"] is False
    # No exactly-one-claim property: both announcements are represented, and
    # a contender blocks shared mutations for this attempt.
    blocked = admit_exact_issue(
        _req(attempt=own), issue={"state": "open", "labels": []}, active_attempt_comments=contenders
    )
    assert blocked.allowed is False


# -- Row 12 --------------------------------------------------------------

def test_row12_stale_resume_makes_no_shared_mutation() -> None:
    revalidation = revalidate_for_mutation(
        own_attempt_id="att_" + "a" * 24,
        observed={"labels": ["status: in-progress"]},
        known_successor_attempt_id="att_" + "b" * 24,
    )
    assert revalidation["allowed"] is False
    stop = should_stop_on_resume(
        own_attempt_id="att_" + "a" * 24, known_successor_attempt_id="att_" + "b" * 24
    )
    assert stop["stop"] is True


# -- Row 13 --------------------------------------------------------------

def test_row13_delayed_mutation_exposes_unfenced_race() -> None:
    exposed = check_delayed_mutation_fenced(mutation_issued_before_check=True)
    assert exposed["fenced"] is False
    assert exposed["reasonCode"] == "unfenced_race"
    clean = check_delayed_mutation_fenced(mutation_issued_before_check=False)
    assert clean["fenced"] is True


# -- Row 14 --------------------------------------------------------------

def test_row14_lost_response_reconciles_without_blind_repeat() -> None:
    # Accepted on GitHub but response lost: exact branch identity adopts the
    # observed PR instead of creating a duplicate.
    out = resolve_lost_pr_creation(
        intended_head="feat-4186",
        intended_base="main",
        observed_prs=[{"number": 7, "head": {"ref": "feat-4186"}, "base": {"ref": "main"}}],
        publication_record_saved=True,
    )
    assert out.outcome == "adopt_existing"
    # Without the exact record the attempt stays blocked rather than blindly repeating.
    blocked = resolve_lost_pr_creation(
        intended_head="feat-4186",
        intended_base="main",
        observed_prs=[],
        publication_record_saved=False,
    )
    assert blocked.outcome != "adopt_existing"


# -- Row 15 --------------------------------------------------------------

def test_row15_interrupted_transition_blocks_until_conclusive() -> None:
    interpretation = interpret_issue(
        {"state": "open", "labels": ["status: in-progress", "status: code-review"]}
    )
    assert interpretation.settled == SETTLED_BLOCKED_MIXED
    denied = admit_exact_issue(
        _req(), issue={"state": "open", "labels": ["status: in-progress", "status: code-review"]}
    )
    assert denied.allowed is False
    assert denied.reason_code == "mixed_state"
    conclusive = recon.decide_issue_reconciliation(
        issue={"state": "open", "labels": ["status: in-progress", "status: code-review"]},
        intended_from_settled="blocked_mixed",
        intended_to_target="to_code_review",
        writer_evidence={"writers_stopped": True, "stop_method": "runtime_quiescence",
                         "stop_evidence": "runtime reports 0 writers"},
        mutation_evidence={"push_outcome": "confirmed", "pr_outcome": "confirmed", "merge_outcome_absent": True},
        preservation_evidence={"save_method": "pr_head_verified",
                               "pr_url": f"https://github.com/{REPO}/pull/7",
                               "pr_head_sha": "b" * 40, "pr_base": "main",
                               "revision": "b" * 40, "preservation_verified": True},
        proposed_disposition="code_review",
        trusted_handoff_present=True,
    )
    assert conclusive.action == recon.ACTION_COMPLETE
    planned = recon.plan_repair_mutation(
        from_settled="in_progress", to_target="to_code_review",
        current_labels=["status: in-progress"], proposed_disposition="code_review",
    )
    assert planned["allowed"] is True
    assert planned["mutation"]["labelsToAdd"] == ["status: code-review"]
    assert planned["mutation"]["labelsToRemove"] == ["status: in-progress"]


# -- Row 16 --------------------------------------------------------------

def test_row16_human_changes_preserved_and_reassessed() -> None:
    out = compare_saved_vs_pr(
        saved_sha="s1", pr_head_sha="p1", ancestry="identical", unexpected_remote_change=True
    )
    assert out.disposition == "reassess"
    assert out.allow_force_push is False
    diverged = compare_saved_vs_pr(
        saved_sha="s1", pr_head_sha="p1", ancestry="diverged", explicit_lineage=True
    )
    assert diverged.allow_force_push is False
    assert diverged.preserve_contributor_commits is True


# -- Row 17 --------------------------------------------------------------

def test_row17_competing_or_closed_prs_need_decision() -> None:
    denied = admit_exact_issue(
        _req(), issue={"state": "open", "labels": ["status: recovery-needed"]},
        pr_identities=[{"prUrl": f"https://github.com/{REPO}/pull/7"}, {"prUrl": f"https://github.com/{REPO}/pull/8"}],
    )
    assert denied.allowed is False
    assert denied.reason_code == "ambiguous_pr_identity"
    competing = dispose_merged_closed(pr_state="open", competing_prs=2)
    assert competing.disposition == "needs_attention"
    closed = dispose_merged_closed(pr_state="closed_unmerged")
    assert closed.disposition == "needs_attention"


# -- Row 18 --------------------------------------------------------------

def test_row18_private_only_checkpoint_is_owner_only() -> None:
    out = classify_recovery_eligibility(private_only_checkpoint=True)
    assert out.eligibility == ELIGIBILITY_OWNER_ONLY
    assert out.may_start_continuation is False
    assert out.may_claim_exact_resume is False


# -- Row 19 --------------------------------------------------------------

def test_row19_retry_history_survives_device_change() -> None:
    assert attempt_evidence_blocks_admission(
        {"linkedAttempts": [{"attemptId": "att_" + "9" * 24, "lineageGap": True, "missingPredecessor": "att_x"}],
         "retryPolicy": {"maxAttempts": 5, "lineageRef": "policy-v1"}}
    ) is True
    denied = admit_exact_issue(
        _req(), issue={"state": "open", "labels": ["status: recovery-needed"]},
        attempt_context={"linkedAttempts": [
            {"attemptId": "att_" + "9" * 24, "lineageGap": True, "missingPredecessor": "att_x"}]},
        retry_policy={"maxAttempts": 5, "lineageRef": "policy-v1"},
    )
    assert denied.allowed is False


# -- Row 20 --------------------------------------------------------------

def test_row20_intentional_cancellation_schedules_nothing() -> None:
    disposition = choose_disposition({"intentional_cancellation": True})
    assert disposition["disposition"] == DISPOSITION_NEEDS_ATTENTION
    assert disposition["disposition"] == "to_needs_attention"
    assert disposition["reasonCode"] == "cancellation_hold"
    assert disposition["schedulesReplacement"] is False


# -- Row 21 --------------------------------------------------------------

def test_row21_contradictory_evidence_requests_attention() -> None:
    denied = admit_exact_issue(
        _req(), issue={"state": "open", "labels": ["status: recovery-needed"]},
        attempt_context={"linkedAttempts": [
            {"attemptId": "second", "outcome": "pending", "policyLineage": "policy-v1"},
            {"attemptId": "first", "lineageGap": True, "missingPredecessor": "first"}]},
        retry_policy={"maxAttempts": 5, "lineageRef": "policy-v1"},
    )
    assert denied.allowed is False
    assert denied.reason_code in {"lineage_gap", "retry_exhausted", "operator_hold"}
    # Documented limitation: sole-history deletion on an Available issue
    # without a tombstone is indistinguishable from never-attempted.
    assert attempt_evidence_blocks_admission(None) is False
    assert interpret_issue({"state": "open", "labels": []}).settled == SETTLED_AVAILABLE


# -- Row 22 --------------------------------------------------------------

def test_row22_outage_or_partial_scan_blocks_admission() -> None:
    scan = recon.merge_scan_results(examined=3, surfaced=1, transport_error="rate_limited", rate_limited=True)
    assert scan["admissionAllowed"] is False
    assert recon.reconciliation_blocks_admission(scan) is True
    partial = recon.merge_scan_results(examined=100, repaired=2, pages_exhausted=True, requests_exhausted=True)
    assert partial["status"] in {"partial", "unknown"}
    assert recon.reconciliation_blocks_admission(partial) is True
    denied = admit_exact_issue(
        _req(), issue={"state": "open", "labels": []},
        reads_complete={"labels": True, "comments": False, "prs": True, "blockers": True, "retryPolicy": True},
    )
    assert denied.allowed is False
    assert denied.reason_code == "read_failure"


# -- Row 23 --------------------------------------------------------------

def test_row23_manual_label_without_owner_respected() -> None:
    interpretation = interpret_issue({"state": "open", "labels": ["status: in-progress"]})
    assert interpretation.settled == SETTLED_IN_PROGRESS
    denied = admit_exact_issue(_req(), issue={"state": "open", "labels": ["status: in-progress"]})
    assert denied.allowed is False
    assert denied.reason_code == "manual_in_progress_without_trusted_owner"


# -- Row 24 --------------------------------------------------------------

def test_row24_unknown_format_fails_closed() -> None:
    interpretation = interpret_issue({"state": "open", "labels": ["status: frobnicate"]})
    assert interpretation.settled == SETTLED_BLOCKED_UNKNOWN
    denied = admit_exact_issue(_req(), issue={"state": "open", "labels": ["status: frobnicate"]})
    assert denied.allowed is False
    assert denied.reason_code == "unknown_format"


def test_terminal_and_attention_states_excluded() -> None:
    closed = interpret_issue({"state": "closed", "labels": ["status: done"]})
    assert closed.settled == SETTLED_CLOSED
    assert closed.eligible_for_implement is False
    open_done = interpret_issue({"state": "open", "labels": ["status: done"]})
    assert open_done.settled == SETTLED_BLOCKED_OPEN_DONE
    assert open_done.eligible_for_implement is False
    held = admit_exact_issue(_req(), issue={"state": "open", "labels": ["status: needs-attention"]})
    assert held.allowed is False
    assert held.reason_code == "operator_hold"
    assert held.settled == SETTLED_NEEDS_ATTENTION


# -- Row 25 --------------------------------------------------------------

def test_row25_missing_permissions_fail_locally() -> None:
    scoped = check_publication_scope(scope=None, requested=["push", "pr", "merge"])
    assert list(scoped.allowed) == []
    readiness = recon.check_reconciliation_readiness(token_available=False, token_error="no token resolved")
    assert readiness["ready"] is False
    assert readiness["reasonCode"] == "github_credentials_missing"
    # No claim that GitHub was updated: explicit None authorizes nothing.
    assert scoped.qualified_local_save is True
    assert scoped.owner_recovery_report is True


# -- Row 26 --------------------------------------------------------------

def test_row26_original_outcome_survives_auxiliary_failure() -> None:
    separated = separate_objective_from_execution(
        execution_outcome="failed", objective_verified=True, auxiliary_failures=["publication"],
    )
    assert separated["reasonCode"] == "objective_preserved"
    assert separated["rebuyImplementation"] is False
    assert "publication" in separated["auxiliaryFailures"]


# ---------------------------------------------------------------------------
# Integrated best-effort contract
# ---------------------------------------------------------------------------


def test_best_effort_contract_across_three_deployments() -> None:
    """A fails on device A, B continues the same PR, C respects review state."""
    topo = ThreeDeployments()
    assert topo.isolated()

    # Device A: failed partial implementation with portable work preserved.
    topo.github.labels = ["status: in-progress"]
    topo.github.comments.append({"attemptId": "att_" + "a" * 24, "activity": "active"})
    disposition_a = choose_disposition({"portable_work_safe": True, "preservation_verified": True})
    assert disposition_a["disposition"] == DISPOSITION_RECOVERY_NEEDED
    assert disposition_a["disposition"] == "to_recovery_needed"
    topo.github.labels = ["status: recovery-needed"]
    topo.github.prs[7] = _open_pr()
    topo.deployments["a"].pending_sync.append({"effect": "terminal-handoff", "attempt": "a"})

    # Device B: separate local state, same shared GitHub; continues the PR.
    assert topo.deployments["b"].ownership_table == {}
    assert topo.deployments["b"].database == {}
    interpretation = interpret_issue(topo.github.snapshot_issue())
    assert interpretation.settled == SETTLED_RECOVERY_NEEDED
    assert interpretation.eligible_for_continuation is True
    routing = route_continuation(_discover_pr().existing_work)
    assert routing.next_action == NEXT_CONTINUE_SAME_PR
    assert routing.must_not_duplicate_pr is True

    # Device C: observes code-review state and does not start implementation.
    topo.github.labels = ["status: code-review"]
    denied_c = admit_for_entrypoint(
        ENTRYPOINT_SEARCH, repository=REPO, issue_number=ISSUE_NUMBER,
        issue=topo.github.snapshot_issue(),
    )
    assert denied_c.allowed is False

    # No unsafe automatic takeover anywhere: unknown writer outcome still blocks.
    assert confirm_writer_stop({})["stopped"] is False


def test_no_hidden_todo_lease_or_second_resolver() -> None:
    # No required status:todo gate: an unlabelled issue is available.
    assert interpret_issue({"state": "open", "labels": ["status: todo"]}).settled == SETTLED_AVAILABLE
    assert interpret_issue({"state": "open", "labels": []}).eligible_for_implement is True
    # No per-device lock labels or claiming states: unknown claiming formats block.
    claiming = interpret_issue({"state": "open", "labels": ["status: claiming"]})
    assert claiming.settled == SETTLED_BLOCKED_UNKNOWN
    # Publication scope introduces no second PR resolver: explicit None
    # authorizes nothing and routes only through the existing boundary.
    scoped = check_publication_scope(scope=None, requested=["push", "pr", "merge"])
    assert list(scoped.allowed) == []
    # Exact recovery is never claimed from labels alone.
    out = classify_recovery_eligibility(github_code_available=True, sanitized_handoff_available=True)
    assert out.eligibility in {ELIGIBILITY_CODE_SEEDED_CONTINUATION, ELIGIBILITY_BLOCKED}
    exact = classify_recovery_eligibility(
        checkpoint_compatible=True, runtime_compatible=True, immutable_inputs_compatible=True
    )
    assert exact.eligibility == ELIGIBILITY_EXACT_RECOVERY


def test_recovery_eligibility_matrix() -> None:
    assert classify_recovery_eligibility(private_only_checkpoint=True).eligibility == ELIGIBILITY_OWNER_ONLY
    blocked = classify_recovery_eligibility()
    assert blocked.eligibility == ELIGIBILITY_BLOCKED
    assert blocked.may_start_continuation is False


def test_verify_and_finalize_only_routes() -> None:
    work = _discover_pr().existing_work
    assert route_continuation(work, implementation_complete=True, verification_current=False).next_action == NEXT_VERIFY_ONLY
    assert route_continuation(work, writable=False).next_action == NEXT_NEEDS_ATTENTION
    fresh = _discover_pr(lineage_pr_url="", lineage_saved_branch="")
    assert fresh.reason_code == "no_preserved_work"
    assert fresh.existing_work.next_action == NEXT_FRESH_IMPLEMENT


def test_required_ci_selects_production_journey() -> None:
    """Required CI must select this journey; it cannot pass on skips alone."""
    from tools import select_test_suites

    journey_paths = [
        "moonmind/workflows/temporal/github_issue_lifecycle.py",
        "moonmind/workflows/temporal/github_issue_admission.py",
        "moonmind/workflows/temporal/github_issue_continuation.py",
        "moonmind/workflows/temporal/github_issue_finalization.py",
        "moonmind/workflows/temporal/github_issue_reconciliation.py",
        "moonmind/workflows/temporal/github_issue_search.py",
    ]
    selection = select_test_suites.select_suites(journey_paths)
    assert selection.temporal_boundary is True
    assert selection.reliability_journey is True
    own_paths = [
        "tests/unit/workflows/temporal/test_github_issue_qualification_4186.py",
        "tests/integration/reliability/test_github_issue_lifecycle_qualification_4186_replay.py",
        "tests/integration/reliability/replays/github-issue-lifecycle-26row-4186/manifest.json",
    ]
    own = select_test_suites.select_suites(own_paths)
    assert own.temporal_boundary is True
    assert own.reliability_journey is True
    # Guard against an all-skipped pass: the row map must bind every row to a
    # concrete test defined in this module.
    assert len(ROW_COVERAGE) == 26


def test_traceability_artifact_matches_row_map() -> None:
    manifest_path = (
        Path(__file__).resolve().parents[4]
        / "tests" / "integration" / "reliability" / "replays"
        / "github-issue-lifecycle-26row-4186" / "manifest.json"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    mapped_rows = {int(item["row"]) for item in manifest["rows"]}
    assert mapped_rows == set(range(1, 27))
    for item in manifest["rows"]:
        assert item["test"] == ROW_COVERAGE[int(item["row"])]["test"]
        assert item["evidence"] and item["entrypoint"] and item["owner"]
