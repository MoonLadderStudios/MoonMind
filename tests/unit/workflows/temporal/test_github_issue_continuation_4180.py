"""Unit coverage for MoonLadderStudios/MoonMind#4180.

Portable prior-work discovery, verification, and phase routing: device-B
continuation at the validated head, complete-but-unreported routing, lost
PR-creation reconciliation, saved/diverged/human/merged/closed/competing and
wrong-base cases, private-only and unavailable evidence, publication-scope
None, merge-transfer guard, and revision-bound verification.
"""

from __future__ import annotations

from moonmind.workflows.temporal.github_issue_continuation import (
    NEXT_CONTINUE_SAME_PR,
    NEXT_FINALIZE_ONLY,
    NEXT_FRESH_IMPLEMENT,
    NEXT_NEEDS_ATTENTION,
    NEXT_VERIFY_ONLY,
    bind_verification_to_revision,
    check_merge_transfer,
    check_publication_scope,
    classify_recovery_eligibility,
    compare_saved_vs_pr,
    discover_existing_work,
    dispose_merged_closed,
    resolve_lost_pr_creation,
    route_continuation,
)


def _open_pr(number: int = 42, head: str = "feat-4180", base: str = "main") -> dict:
    return {
        "number": number,
        "state": "open",
        "merged": False,
        "head": {
            "ref": head,
            "sha": "abc123",
            "repo": {"full_name": "MoonLadderStudios/MoonMind"},
        },
        "base": {"ref": base},
    }


def _discover(**overrides):
    base = {
        "repository": "MoonLadderStudios/MoonMind",
        "issue_number": 4180,
        "lineage_validated": True,
        "lineage_pr_url": "https://github.com/MoonLadderStudios/MoonMind/pull/42",
        "lineage_pr_head": "feat-4180",
        "lineage_pr_base": "main",
        "lineage_saved_branch": "ckpt-4180",
        "lineage_saved_sha": "def456",
        "github_pr": _open_pr(),
    }
    base.update(overrides)
    return discover_existing_work(**base)


# -- req 1: typed trusted result; insufficient evidence rejected -------------


def test_insufficient_bases_are_rejected() -> None:
    for basis in ("title_match", "issue_mention", "newest_timestamp", "private_url"):
        result = _discover(candidate_basis=basis)
        assert result.trusted is False
        assert result.reason_code == "insufficient_evidence"
        assert result.existing_work is None


def test_unvalidated_lineage_is_rejected() -> None:
    result = _discover(lineage_validated=False)
    assert result.trusted is False
    assert result.reason_code == "lineage_unvalidated"


def test_identity_mismatch_is_rejected() -> None:
    bad = _open_pr(head="other-branch")
    result = _discover(github_pr=bad)
    assert result.trusted is False
    assert result.reason_code == "identity_mismatch"


def test_unreadable_github_object_requires_attention() -> None:
    result = _discover(github_pr=None)
    assert result.trusted is False
    assert result.reason_code == "github_unavailable"


def test_trusted_result_pins_identity() -> None:
    result = _discover()
    assert result.trusted is True
    work = result.existing_work
    assert work is not None
    assert work.repository == "MoonLadderStudios/MoonMind"
    assert work.issue_number == 4180
    assert work.pr_number == 42
    assert work.head_repo == "MoonLadderStudios/MoonMind"
    assert work.head_branch == "feat-4180"
    assert work.head_sha == "abc123"
    assert work.base == "main"
    assert work.saved_branch == "ckpt-4180"
    assert work.next_action == NEXT_CONTINUE_SAME_PR


def test_no_preserved_work_routes_fresh() -> None:
    result = _discover(
        lineage_pr_url="",
        lineage_saved_branch="",
        github_pr=_open_pr(),
    )
    assert result.trusted is True
    assert result.reason_code == "no_preserved_work"
    assert result.existing_work.next_action == NEXT_FRESH_IMPLEMENT


# -- req 2: adopt open PR at validated head; complete routes to gate --------


def test_device_b_continues_same_pr_at_validated_head() -> None:
    work = _discover().existing_work
    routing = route_continuation(work)
    assert routing.next_action == NEXT_CONTINUE_SAME_PR
    assert routing.workspace_revision == "abc123"
    assert routing.must_not_duplicate_pr is True
    assert routing.reassess_requirements is True
    assert routing.reuse_accepted_work is True


def test_complete_but_unreported_routes_to_verification() -> None:
    work = _discover().existing_work
    routing = route_continuation(
        work, implementation_complete=True, verification_current=False
    )
    assert routing.next_action == NEXT_VERIFY_ONLY
    assert routing.workspace_revision == "abc123"


def test_unwritable_pr_requires_attention() -> None:
    work = _discover().existing_work
    routing = route_continuation(work, writable=False)
    assert routing.next_action == NEXT_NEEDS_ATTENTION


# -- req 3: saved vs PR ancestry -------------------------------------------


def test_saved_ahead_without_lineage_needs_attention() -> None:
    out = compare_saved_vs_pr(
        saved_sha="s1", pr_head_sha="p1", ancestry="saved_ahead_of_pr",
        explicit_lineage=False,
    )
    assert out.disposition == "needs_attention"
    assert out.allow_force_push is False
    assert out.preserve_contributor_commits is True


def test_saved_ahead_with_lineage_incorporates_without_force_push() -> None:
    out = compare_saved_vs_pr(
        saved_sha="s1", pr_head_sha="p1", ancestry="saved_ahead_of_pr",
        explicit_lineage=True,
    )
    assert out.disposition == "incorporate_saved"
    assert out.allow_force_push is False


def test_diverged_never_force_pushes() -> None:
    out = compare_saved_vs_pr(
        saved_sha="s1", pr_head_sha="p1", ancestry="diverged",
        explicit_lineage=True,
    )
    assert out.allow_force_push is False
    assert out.preserve_contributor_commits is True
    assert out.require_reassessment is True


def test_unexpected_remote_change_requires_reassessment() -> None:
    out = compare_saved_vs_pr(
        saved_sha="s1", pr_head_sha="p1", ancestry="identical",
        unexpected_remote_change=True,
    )
    assert out.disposition == "reassess"
    assert out.require_reassessment is True


# -- req 4: lost creation response ------------------------------------------


def test_lost_creation_adopts_exact_identity() -> None:
    out = resolve_lost_pr_creation(
        intended_head="feat-4180",
        intended_base="main",
        observed_prs=[{"number": 42, "head": {"ref": "feat-4180"}, "base": {"ref": "main"}}],
        publication_record_saved=True,
    )
    assert out.outcome == "adopt_existing"
    assert out.pr_number == 42


def test_lost_creation_without_record_stays_blocked() -> None:
    out = resolve_lost_pr_creation(
        intended_head="feat-4180",
        intended_base="main",
        observed_prs=[{"number": 42, "head": {"ref": "feat-4180"}, "base": {"ref": "main"}}],
        publication_record_saved=False,
    )
    assert out.outcome == "blocked_ambiguous"


def test_lost_creation_ambiguous_stays_blocked() -> None:
    out = resolve_lost_pr_creation(
        intended_head="feat-4180",
        intended_base="main",
        observed_prs=[
            {"number": 42, "head": {"ref": "feat-4180"}, "base": {"ref": "main"}},
            {"number": 43, "head": {"ref": "feat-4180"}, "base": {"ref": "main"}},
        ],
        publication_record_saved=True,
    )
    assert out.outcome == "blocked_ambiguous"


# -- req 5: merged / closed inspection ---------------------------------------


def test_merged_pr_with_remaining_scope_implements_remaining_only() -> None:
    out = dispose_merged_closed(pr_state="merged", objective_satisfied=False)
    assert out.disposition == "implement_remaining_in_new_work"
    assert out.implement_remaining_only is True


def test_merged_pr_satisfying_objective_completes() -> None:
    out = dispose_merged_closed(pr_state="merged", objective_satisfied=True)
    assert out.disposition == "complete"


def test_closed_unmerged_and_competing_require_attention() -> None:
    assert dispose_merged_closed(pr_state="closed_unmerged").disposition == "needs_attention"
    assert dispose_merged_closed(
        pr_state="open", competing_prs=2
    ).disposition == "needs_attention"
    assert dispose_merged_closed(
        pr_state="open", wrong_base=True
    ).disposition == "needs_attention"
    assert dispose_merged_closed(
        pr_state="open", uncertain_ownership=True
    ).disposition == "needs_attention"


# -- req 6: exact recovery vs continuation -----------------------------------


def test_private_only_checkpoint_is_owner_only() -> None:
    out = classify_recovery_eligibility(
        private_only_checkpoint=True,
        github_code_available=True,
        sanitized_handoff_available=True,
    )
    assert out.eligibility == "owner_only"
    assert out.may_claim_exact_resume is False
    assert out.may_start_continuation is False


def test_exact_recovery_requires_all_compatible_inputs() -> None:
    out = classify_recovery_eligibility(
        checkpoint_compatible=True, runtime_compatible=True,
        immutable_inputs_compatible=True,
    )
    assert out.eligibility == "exact_recovery"
    assert out.may_claim_exact_resume is True


def test_code_seeded_continuation_never_claims_exact_resume() -> None:
    out = classify_recovery_eligibility(
        checkpoint_compatible=False, runtime_compatible=False,
        immutable_inputs_compatible=False,
        github_code_available=True, sanitized_handoff_available=True,
    )
    assert out.eligibility == "code_seeded_continuation"
    assert out.may_claim_exact_resume is False
    assert out.may_start_continuation is True


def test_unavailable_objects_never_offer_resume() -> None:
    out = classify_recovery_eligibility()
    assert out.eligibility == "blocked"
    assert out.may_claim_exact_resume is False


# -- req 7/8: publication scope None + merge-transfer guard ------------------


def test_explicit_none_scope_authorizes_no_remote_effect() -> None:
    out = check_publication_scope(scope=None, requested=["push", "pr", "merge"])
    assert out.allowed == []
    assert out.qualified_local_save is True
    assert out.owner_recovery_report is True


def test_incomplete_code_must_not_merge_to_transfer() -> None:
    out = check_merge_transfer(
        implementation_complete=False, explicitly_reviewed_partial=False
    )
    assert out.allow_merge is False
    assert out.keep_issue_open is True
    assert out.forbid_closing_semantics is True


def test_reviewed_partial_keeps_scope_open_without_closing() -> None:
    out = check_merge_transfer(
        implementation_complete=False, explicitly_reviewed_partial=True
    )
    assert out.allow_merge is True
    assert out.keep_issue_open is True
    assert out.forbid_closing_semantics is True


# -- verification tied to exact revision -------------------------------------


def test_stale_head_bound_verification_is_not_current() -> None:
    out = bind_verification_to_revision(verified_sha="old", current_sha="new")
    assert out.current is False
    assert out.require_reassessment is True


def test_matching_revision_and_requirements_stays_current() -> None:
    out = bind_verification_to_revision(verified_sha="abc", current_sha="abc")
    assert out.current is True


def test_requirement_change_invalidates_verification() -> None:
    out = bind_verification_to_revision(
        verified_sha="abc", current_sha="abc", requirements_unchanged=False
    )
    assert out.current is False


def test_finalize_only_for_merged_discovery() -> None:
    result = _discover(
        github_pr={"number": 42, "state": "closed", "merged": True,
                   "head": {"ref": "feat-4180", "sha": "abc123",
                            "repo": {"full_name": "MoonLadderStudios/MoonMind"}},
                   "base": {"ref": "main"}}
    )
    assert result.trusted is True
    assert result.existing_work.next_action == NEXT_FINALIZE_ONLY


# -- Codex review follow-ups (PR #4240) --------------------------------------


def test_unknown_basis_is_rejected_fail_closed() -> None:
    for basis in ("issue_reference", "branch_name_match", "", "github_read"):
        result = _discover(candidate_basis=basis)
        assert result.trusted is False
        assert result.reason_code == "insufficient_evidence"
        assert result.existing_work is None


def test_next_actions_use_canonical_handoff_vocabulary() -> None:
    from moonmind.workflows.temporal.github_issue_attempt import NEXT_ACTIONS
    from moonmind.workflows.temporal import github_issue_continuation as cont

    for name in (
        "NEXT_CONTINUE_SAME_PR",
        "NEXT_VERIFY_ONLY",
        "NEXT_FINALIZE_ONLY",
        "NEXT_CREATE_PR_FROM_SAVED",
        "NEXT_FRESH_IMPLEMENT",
        "NEXT_NEEDS_ATTENTION",
        "NEXT_OWNER_RECOVERY",
        "NEXT_BLOCKED",
    ):
        assert getattr(cont, name) in NEXT_ACTIONS, name


def test_saved_branch_only_routes_to_saved_continuation() -> None:
    from moonmind.workflows.temporal.github_issue_continuation import (
        NEXT_CREATE_PR_FROM_SAVED,
    )

    result = _discover(
        lineage_pr_url="",
        lineage_saved_branch="ckpt-4180",
        lineage_saved_sha="def456",
        github_pr=None,
    )
    assert result.trusted is True
    assert result.reason_code == "saved_branch_only"
    assert result.existing_work is not None
    assert result.existing_work.next_action == NEXT_CREATE_PR_FROM_SAVED
    assert result.existing_work.head_branch == "ckpt-4180"
    assert result.existing_work.head_sha == "def456"


def test_complete_and_verified_routes_to_finalize_only() -> None:
    work = _discover().existing_work
    routing = route_continuation(
        work, implementation_complete=True, verification_current=True
    )
    assert routing.next_action == NEXT_FINALIZE_ONLY
    assert routing.workspace_revision == "abc123"
    assert routing.must_not_duplicate_pr is True


def test_direct_read_missing_head_sha_requires_attention() -> None:
    pr = _open_pr()
    del pr["head"]["sha"]
    result = _discover(github_pr=pr)
    assert result.trusted is False
    assert result.reason_code == "github_unavailable"


def test_direct_read_missing_head_ref_requires_attention() -> None:
    pr = _open_pr()
    del pr["head"]["ref"]
    result = _discover(github_pr=pr)
    assert result.trusted is False
    assert result.reason_code == "github_unavailable"


def test_unknown_scalar_scope_authorizes_nothing() -> None:
    out = check_publication_scope(scope="typo", requested=["push", "pr", "merge"])
    assert out.allowed == []
    assert out.qualified_local_save is True


def test_lost_creation_rejects_wrong_fork_repo() -> None:
    out = resolve_lost_pr_creation(
        intended_head="feat-4180",
        intended_base="main",
        intended_head_repo="MoonLadderStudios/MoonMind",
        observed_prs=[
            {"number": 42, "head": {"ref": "feat-4180",
                                    "repo": {"full_name": "someone-else/MoonMind"}},
             "base": {"ref": "main"}}
        ],
        publication_record_saved=True,
    )
    assert out.outcome == "no_match"
    assert out.pr_number is None


def test_lost_creation_adopts_matching_repo_and_sha() -> None:
    out = resolve_lost_pr_creation(
        intended_head="feat-4180",
        intended_base="main",
        intended_head_repo="MoonLadderStudios/MoonMind",
        intended_head_sha="abc123",
        observed_prs=[
            {"number": 42,
             "head": {"ref": "feat-4180", "sha": "abc123",
                      "repo": {"full_name": "MoonLadderStudios/MoonMind"}},
             "base": {"ref": "main"}}
        ],
        publication_record_saved=True,
    )
    assert out.outcome == "adopt_existing"
    assert out.pr_number == 42


def test_lost_creation_rejects_sha_mismatch() -> None:
    out = resolve_lost_pr_creation(
        intended_head="feat-4180",
        intended_base="main",
        intended_head_sha="abc123",
        observed_prs=[
            {"number": 42, "head": {"ref": "feat-4180", "sha": "different"},
             "base": {"ref": "main"}}
        ],
        publication_record_saved=True,
    )
    assert out.outcome == "no_match"
    assert out.pr_number is None


def test_plan_issue_continuation_routes_open_pr() -> None:
    from moonmind.workflows.temporal.github_issue_continuation import (
        plan_issue_continuation,
    )

    planned = plan_issue_continuation(
        repository="MoonLadderStudios/MoonMind",
        issue_number=4180,
        lineage_validated=True,
        lineage_pr_url="https://github.com/MoonLadderStudios/MoonMind/pull/42",
        lineage_pr_head="feat-4180",
        lineage_pr_base="main",
        lineage_saved_branch="ckpt-4180",
        lineage_saved_sha="def456",
        github_pr=_open_pr(),
    )
    assert planned["discovery"]["trusted"] is True
    assert planned["routing"] is not None
    assert planned["routing"]["nextAction"] == "continue-implementation"
    assert planned["routing"]["workspaceRevision"] == "abc123"


def test_plan_issue_continuation_saved_only_needs_no_same_pr_routing() -> None:
    from moonmind.workflows.temporal.github_issue_continuation import (
        plan_issue_continuation,
    )

    planned = plan_issue_continuation(
        repository="MoonLadderStudios/MoonMind",
        issue_number=4180,
        lineage_validated=True,
        lineage_pr_url="",
        lineage_saved_branch="ckpt-4180",
        lineage_saved_sha="def456",
        github_pr=None,
    )
    assert planned["discovery"]["trusted"] is True
    assert planned["discovery"]["reasonCode"] == "saved_branch_only"
    assert planned["routing"] is None


def test_lifecycle_preserved_work_continuation_uses_boundary() -> None:
    from moonmind.workflows.temporal.github_issue_lifecycle import (
        preserved_work_continuation,
    )

    planned = preserved_work_continuation(
        repository="MoonLadderStudios/MoonMind",
        issue_number=4180,
        lineage_validated=True,
        lineage_pr_url="https://github.com/MoonLadderStudios/MoonMind/pull/42",
        lineage_pr_head="feat-4180",
        lineage_pr_base="main",
        lineage_saved_branch="ckpt-4180",
        lineage_saved_sha="def456",
        github_pr=_open_pr(),
    )
    assert planned["discovery"]["trusted"] is True
    assert planned["routing"]["nextAction"] == "continue-implementation"
