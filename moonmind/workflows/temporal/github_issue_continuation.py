"""Portable prior-work discovery, verification, and phase routing.

Owns MoonLadderStudios/MoonMind#4180: resolve existing issue work before
choosing a workspace or implementing code, so a later deployment normally
continues the same PR (design:
docs/Workflows/GitHubIssueStatusStateMachineDesign.md, section 7 and
section 8.2).

Scope: typed trusted existing-work result plus continuation phase routing.
Actual admission uses #4178 and terminal handoff uses #4179. Existing
checkpoint, saved-work, publication, and portable PR resolver implementations
remain authoritative and are not rebuilt here; this module consumes their
evidence at the boundary and decides what the reads mean.

Deterministic and side-effect-free: no network I/O. Trusted Activities and
services perform GitHub reads, workspace preparation, and publication writes;
this module classifies already-read evidence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence


# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

#: Candidate bases that are never sufficient canonical-work evidence on their
#: own (issue required work item 1). The check below is a whitelist: only the
#: validated attempt-lineage plus direct-read basis is sufficient, and every
#: other (including unknown future) basis is rejected.
INSUFFICIENT_EVIDENCE_KINDS = frozenset(
    {
        "title_match",
        "issue_mention",
        "newest_timestamp",
        "private_url",
    }
)

#: The only sufficient discovery basis: validated attempt lineage plus a direct
#: GitHub read. Compared after the same normalization as the input
#: (lowercase, spaces/dashes to underscores).
SUFFICIENT_EVIDENCE_BASES = frozenset(
    {
        "attempt_lineage+github_read",
        "attempt_lineage_github_read",
    }
)

#: Next actions use the canonical portable handoff vocabulary
#: (``github_issue_attempt.NEXT_ACTIONS``: ``fresh-retry``,
#: ``continue-implementation``, ``verify``, ``continue-review``,
#: ``finalize-status``, ``obtain-operator-attention``) so a routing result can
#: be copied into an ``AttemptHandoff`` without rejection or silent conversion
#: to ``continue_implementation``. Phases that share one canonical action share
#: its value; the finer-grained distinction stays in the ``detail``/``summary``
#: fields and the preserved ``saved_branch``/``saved_sha`` evidence.
NEXT_CONTINUE_SAME_PR = "continue-implementation"
NEXT_VERIFY_ONLY = "verify"
NEXT_FINALIZE_ONLY = "finalize-status"
NEXT_CREATE_PR_FROM_SAVED = "continue-implementation"
NEXT_FRESH_IMPLEMENT = "fresh-retry"
NEXT_NEEDS_ATTENTION = "obtain-operator-attention"
NEXT_OWNER_RECOVERY = "obtain-operator-attention"
NEXT_BLOCKED = "obtain-operator-attention"

#: PR states observed through direct GitHub reads.
PR_STATE_OPEN = "open"
PR_STATE_MERGED = "merged"
PR_STATE_CLOSED_UNMERGED = "closed_unmerged"
PR_STATE_UNKNOWN = "unknown"

#: Ancestry relationships between a saved checkpoint branch and a live PR head.
ANCESTRY_IDENTICAL = "identical"
ANCESTRY_SAVED_BEHIND_PR = "saved_behind_pr"
ANCESTRY_SAVED_AHEAD_OF_PR = "saved_ahead_of_pr"
ANCESTRY_DIVERGED = "diverged"
ANCESTRY_UNKNOWN = "unknown"

#: Continuation eligibility classes (required work item 6).
ELIGIBILITY_EXACT_RECOVERY = "exact_recovery"
ELIGIBILITY_CODE_SEEDED_CONTINUATION = "code_seeded_continuation"
ELIGIBILITY_OWNER_ONLY = "owner_only"
ELIGIBILITY_BLOCKED = "blocked"


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if value is None:
        return ""
    return str(value).strip()


def _pr_number_from_url(url: Any) -> int | None:
    text = _text(url)
    if not text:
        return None
    # Ordinary GitHub PR references only; a private workflow URL never parses.
    marker = "/pull/"
    if marker not in text:
        return None
    tail = text.split(marker, 1)[1].strip().rstrip("/")
    digits = ""
    for char in tail:
        if char.isdigit():
            digits += char
        else:
            break
    try:
        number = int(digits)
    except ValueError:
        return None
    return number if number > 0 else None


# ---------------------------------------------------------------------------
# Typed existing-work result (required work item 1)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExistingWork:
    """One typed, trusted existing-work result for an issue."""

    repository: str
    issue_number: int
    pr_number: int | None = None
    pr_url: str = ""
    pr_state: str = PR_STATE_UNKNOWN
    head_repo: str = ""
    head_branch: str = ""
    head_sha: str = ""
    base: str = ""
    saved_branch: str = ""
    saved_sha: str = ""
    gate_evidence: Mapping[str, Any] = field(default_factory=dict)
    next_action: str = NEXT_FRESH_IMPLEMENT
    evidence_source: str = "attempt_lineage+github_read"
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "repository": self.repository,
            "issueNumber": self.issue_number,
            "prNumber": self.pr_number,
            "prUrl": self.pr_url,
            "prState": self.pr_state,
            "headRepo": self.head_repo,
            "headBranch": self.head_branch,
            "headSha": self.head_sha,
            "base": self.base,
            "savedBranch": self.saved_branch,
            "savedSha": self.saved_sha,
            "gateEvidence": dict(self.gate_evidence),
            "nextAction": self.next_action,
            "evidenceSource": self.evidence_source,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class DiscoveryResult:
    """Outcome of discovering one trusted existing-work result."""

    trusted: bool
    reason_code: str
    summary: str
    existing_work: ExistingWork | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "trusted": self.trusted,
            "reasonCode": self.reason_code,
            "summary": self.summary,
            "existingWork": self.existing_work.to_dict()
            if self.existing_work is not None
            else None,
        }


def discover_existing_work(
    *,
    repository: str,
    issue_number: int,
    lineage_validated: bool,
    lineage_pr_url: Any = "",
    lineage_pr_head: Any = "",
    lineage_pr_base: Any = "",
    lineage_saved_branch: Any = "",
    lineage_saved_sha: Any = "",
    candidate_basis: Any = "attempt_lineage+github_read",
    github_pr: Mapping[str, Any] | None = None,
    gate_evidence: Mapping[str, Any] | None = None,
) -> DiscoveryResult:
    """Produce one typed trusted existing-work result or an explicit rejection.

    The caller passes the validated attempt lineage (already checked by
    ``validate_attempt_handoff`` at the trusted boundary) and the direct
    GitHub PR read for the lineage-pinned PR. Both must agree on
    repository/issue/PR/head/base identity before the result is trusted.

    ``candidate_basis`` names how the candidate was found. Title matches,
    contextual issue mentions, newest timestamps, and private workflow URLs
    are insufficient canonical-work evidence and are rejected here.
    """
    repo = _text(repository)
    try:
        issue_no = int(issue_number)
    except (TypeError, ValueError):
        issue_no = 0
    basis = _text(candidate_basis).lower().replace(" ", "_").replace("-", "_")
    if basis not in SUFFICIENT_EVIDENCE_BASES:
        return DiscoveryResult(
            trusted=False,
            reason_code="insufficient_evidence",
            summary=(
                f"Candidate basis '{basis}' is insufficient canonical-work "
                "evidence; a validated attempt lineage plus a direct GitHub "
                "read is required."
            ),
        )
    if not repo or issue_no <= 0:
        return DiscoveryResult(
            trusted=False,
            reason_code="invalid_identity",
            summary="Repository/issue identity is missing or invalid.",
        )
    if not lineage_validated:
        return DiscoveryResult(
            trusted=False,
            reason_code="lineage_unvalidated",
            summary=(
                "Attempt lineage is not validated; title, mention, timestamp, "
                "or private-URL evidence alone cannot establish canonical work."
            ),
        )
    pr_url = _text(lineage_pr_url)
    pr_number = _pr_number_from_url(pr_url)
    head_branch = _text(lineage_pr_head)
    base = _text(lineage_pr_base)
    saved_branch = _text(lineage_saved_branch)
    saved_sha = _text(lineage_saved_sha)

    if not pr_number and not saved_branch:
        # No preserved work at all: fresh implementation is the honest route,
        # not a fabricated adoption.
        return DiscoveryResult(
            trusted=True,
            reason_code="no_preserved_work",
            summary="Validated lineage records no PR or saved branch; fresh work.",
            existing_work=ExistingWork(
                repository=repo,
                issue_number=issue_no,
                next_action=NEXT_FRESH_IMPLEMENT,
                gate_evidence=dict(gate_evidence or {}),
                detail="No preserved PR or saved branch in validated lineage.",
            ),
        )
    if not pr_number and saved_branch:
        # Saved-branch-only lineage (failure before PR creation): the usable
        # preserved work routes to saved-seeded continuation, never a silent
        # fresh start and never blocked for a nonexistent PR object. The
        # trusted boundary must still validate the GitHub-accessible branch
        # evidence before creating the PR under the admitted publication
        # policy.
        return DiscoveryResult(
            trusted=True,
            reason_code="saved_branch_only",
            summary=(
                f"Validated lineage records saved branch '{saved_branch}' "
                "with no PR; continue preserved work and create the PR only "
                "under the admitted publication policy."
            ),
            existing_work=ExistingWork(
                repository=repo,
                issue_number=issue_no,
                head_branch=saved_branch,
                head_sha=saved_sha,
                base=base,
                saved_branch=saved_branch,
                saved_sha=saved_sha,
                gate_evidence=dict(gate_evidence or {}),
                next_action=NEXT_CREATE_PR_FROM_SAVED,
                evidence_source="attempt_lineage+saved_branch",
                detail=(
                    "Saved-branch-only preserved work; create the normal PR "
                    "only under the admitted publication policy."
                ),
            ),
        )
    if github_pr is None:
        # GitHub objects that cannot be read are not treated as absent work;
        # the next action is attention, never a silent fresh start.
        return DiscoveryResult(
            trusted=False,
            reason_code="github_unavailable",
            summary=(
                "Preserved work is named by lineage but its GitHub object "
                "could not be read; requires attention rather than a fresh start."
            ),
        )
    if not isinstance(github_pr, Mapping):
        return DiscoveryResult(
            trusted=False,
            reason_code="github_unavailable",
            summary="GitHub PR read is malformed; requires attention.",
        )
    # Pin identity: the direct read must describe the same PR number, head
    # branch, and base recorded in lineage. Anything else is a mismatch, not
    # an adoption.
    read_number = github_pr.get("number")
    read_number_ok = read_number == pr_number if pr_number else True
    head_data = github_pr.get("head")
    base_data = github_pr.get("base")
    read_head = (
        _text(head_data.get("ref")) if isinstance(head_data, Mapping) else ""
    )
    read_base = (
        _text(base_data.get("ref")) if isinstance(base_data, Mapping) else ""
    )
    read_head_sha = (
        _text(head_data.get("sha")) if isinstance(head_data, Mapping) else ""
    )
    head_repo = ""
    if isinstance(head_data, Mapping) and isinstance(
        head_data.get("repo"), Mapping
    ):
        head_repo = _text(head_data["repo"].get("full_name"))
    if pr_number and (not read_head or not read_head_sha):
        # A direct read that names the expected PR but omits the current head
        # ref or revision is incomplete identity evidence: trusting it would
        # publish an empty workspace_revision and fall back to mutable branch
        # state instead of the promised exact-head workspace.
        return DiscoveryResult(
            trusted=False,
            reason_code="github_unavailable",
            summary=(
                "Direct GitHub read omits the current head ref or revision; "
                "requires attention rather than trusting an empty revision."
            ),
        )
    read_state = _text(github_pr.get("state")).lower()
    merged = bool(github_pr.get("merged"))
    if merged:
        pr_state = PR_STATE_MERGED
    elif read_state == "open":
        pr_state = PR_STATE_OPEN
    elif read_state == "closed":
        pr_state = PR_STATE_CLOSED_UNMERGED
    else:
        pr_state = PR_STATE_UNKNOWN
    if pr_number and not read_number_ok:
        return DiscoveryResult(
            trusted=False,
            reason_code="identity_mismatch",
            summary="Direct GitHub read describes a different PR number; rejected.",
        )
    if head_branch and read_head and head_branch != read_head:
        return DiscoveryResult(
            trusted=False,
            reason_code="identity_mismatch",
            summary="Direct GitHub read describes a different head branch; rejected.",
        )
    if base and read_base and base != read_base:
        return DiscoveryResult(
            trusted=False,
            reason_code="identity_mismatch",
            summary="Direct GitHub read describes a different base; rejected.",
        )
    if pr_state == PR_STATE_UNKNOWN:
        return DiscoveryResult(
            trusted=False,
            reason_code="github_unavailable",
            summary="GitHub PR state is unreadable; requires attention.",
        )
    gates = dict(gate_evidence or {})
    if pr_state == PR_STATE_OPEN:
        next_action = NEXT_CONTINUE_SAME_PR
    elif pr_state == PR_STATE_MERGED:
        next_action = NEXT_FINALIZE_ONLY
    else:
        next_action = NEXT_NEEDS_ATTENTION
    return DiscoveryResult(
        trusted=True,
        reason_code="trusted",
        summary=(
            f"Trusted existing work: {repo}#{issue_no} PR {pr_number} "
            f"({pr_state}) at {head_repo or repo}:{read_head or head_branch}."
        ),
        existing_work=ExistingWork(
            repository=repo,
            issue_number=issue_no,
            pr_number=pr_number,
            pr_url=pr_url,
            pr_state=pr_state,
            head_repo=head_repo or repo,
            head_branch=read_head or head_branch,
            head_sha=read_head_sha,
            base=read_base or base,
            saved_branch=saved_branch,
            saved_sha=saved_sha,
            gate_evidence=gates,
            next_action=next_action,
            detail=(
                "Pinned from validated attempt lineage plus direct GitHub read."
            ),
        ),
    )


# ---------------------------------------------------------------------------
# Continuation routing for one trusted open PR (required work item 2)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ContinuationRouting:
    """Phase routing for one trusted existing-work result."""

    next_action: str
    workspace_revision: str
    reassess_requirements: bool
    reuse_accepted_work: bool
    must_not_duplicate_pr: bool
    summary: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "nextAction": self.next_action,
            "workspaceRevision": self.workspace_revision,
            "reassessRequirements": self.reassess_requirements,
            "reuseAcceptedWork": self.reuse_accepted_work,
            "mustNotDuplicatePr": self.must_not_duplicate_pr,
            "summary": self.summary,
        }


def route_continuation(
    existing_work: ExistingWork,
    *,
    implementation_complete: bool = False,
    verification_current: bool = False,
    writable: bool = True,
) -> ContinuationRouting:
    """Route one trusted writable open PR to its remaining phase.

    The isolated workspace is always prepared from the verified current head,
    never the base branch. Complete implementation with missing verification
    or handoff routes to the missing gate, not back to implementation. Stale
    head-bound verification is never treated as current: when the head moved,
    requirements are reassessed.
    """
    if existing_work.pr_state != PR_STATE_OPEN:
        return ContinuationRouting(
            next_action=NEXT_NEEDS_ATTENTION,
            workspace_revision="",
            reassess_requirements=True,
            reuse_accepted_work=True,
            must_not_duplicate_pr=True,
            summary="Only an open PR routes to same-PR continuation here.",
        )
    if not writable:
        return ContinuationRouting(
            next_action=NEXT_NEEDS_ATTENTION,
            workspace_revision=existing_work.head_sha,
            reassess_requirements=True,
            reuse_accepted_work=True,
            must_not_duplicate_pr=True,
            summary="PR is not writable; requires explicit disposition.",
        )
    if implementation_complete and verification_current:
        return ContinuationRouting(
            next_action=NEXT_FINALIZE_ONLY,
            workspace_revision=existing_work.head_sha,
            reassess_requirements=True,
            reuse_accepted_work=True,
            must_not_duplicate_pr=True,
            summary=(
                "Implementation is complete and verification is current; "
                "perform only the missing handoff/status finalization on the "
                "same PR instead of reimplementing."
            ),
        )
    if implementation_complete and not verification_current:
        return ContinuationRouting(
            next_action=NEXT_VERIFY_ONLY,
            workspace_revision=existing_work.head_sha,
            reassess_requirements=True,
            reuse_accepted_work=True,
            must_not_duplicate_pr=True,
            summary=(
                "Implementation is complete; perform only the missing "
                "verification/review/finalization work on the same PR."
            ),
        )
    return ContinuationRouting(
        next_action=NEXT_CONTINUE_SAME_PR,
        workspace_revision=existing_work.head_sha,
        reassess_requirements=True,
        reuse_accepted_work=True,
        must_not_duplicate_pr=True,
        summary=(
            "Continue the same PR from its validated current head; "
            "reassess only remaining requirements without duplication."
        ),
    )


# ---------------------------------------------------------------------------
# Saved checkpoint branch vs live PR head (required work item 3)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SavedVsPrComparison:
    """Ancestry-aware disposition of saved work relative to the live PR head."""

    disposition: str
    allow_force_push: bool
    preserve_contributor_commits: bool
    require_reassessment: bool
    summary: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "disposition": self.disposition,
            "allowForcePush": self.allow_force_push,
            "preserveContributorCommits": self.preserve_contributor_commits,
            "requireReassessment": self.require_reassessment,
            "summary": self.summary,
        }


def compare_saved_vs_pr(
    *,
    saved_sha: Any = "",
    pr_head_sha: Any = "",
    ancestry: Any = ANCESTRY_UNKNOWN,
    explicit_lineage: bool = False,
    unexpected_remote_change: bool = False,
) -> SavedVsPrComparison:
    """Compare a saved checkpoint branch against the live PR head.

    Never authorizes force-push, divergent-work discard, or human-contribution
    overwrite. Unexpected remote-head changes always require reassessment.
    """
    saved = _text(saved_sha)
    head = _text(pr_head_sha)
    relation = _text(ancestry).lower().replace(" ", "_").replace("-", "_")
    if unexpected_remote_change:
        return SavedVsPrComparison(
            disposition="reassess",
            allow_force_push=False,
            preserve_contributor_commits=True,
            require_reassessment=True,
            summary=(
                "Unexpected remote-head change requires reassessment before "
                "incorporating additional work."
            ),
        )
    if not saved or not head:
        return SavedVsPrComparison(
            disposition="needs_attention",
            allow_force_push=False,
            preserve_contributor_commits=True,
            require_reassessment=True,
            summary="Saved or live revision is unknown; requires attention.",
        )
    if saved == head or relation == ANCESTRY_IDENTICAL:
        return SavedVsPrComparison(
            disposition="in_sync",
            allow_force_push=False,
            preserve_contributor_commits=True,
            require_reassessment=False,
            summary="Saved branch matches the live PR head; continue directly.",
        )
    if relation == ANCESTRY_SAVED_BEHIND_PR:
        return SavedVsPrComparison(
            disposition="reassess_then_rebase_saved",
            allow_force_push=False,
            preserve_contributor_commits=True,
            require_reassessment=True,
            summary=(
                "Live PR moved ahead of the saved branch; reassess the head, "
                "then incorporate saved work without overwriting contributors."
            ),
        )
    if relation == ANCESTRY_SAVED_AHEAD_OF_PR:
        if not explicit_lineage:
            return SavedVsPrComparison(
                disposition="needs_attention",
                allow_force_push=False,
                preserve_contributor_commits=True,
                require_reassessment=True,
                summary=(
                    "Saved work extends beyond the PR head without explicit "
                    "lineage; requires attention before incorporating."
                ),
            )
        return SavedVsPrComparison(
            disposition="incorporate_saved",
            allow_force_push=False,
            preserve_contributor_commits=True,
            require_reassessment=True,
            summary=(
                "Explicit lineage supports the saved-ahead branch; reassess, "
                "then incorporate without force-push."
            ),
        )
    # Diverged or unknown ancestry: never discard, never force-push.
    return SavedVsPrComparison(
        disposition="needs_attention",
        allow_force_push=False,
        preserve_contributor_commits=True,
        require_reassessment=True,
        summary=(
            "Diverged or unknown ancestry requires explicit disposition; "
            "contributor commits are preserved and force-push is denied."
        ),
    )


# ---------------------------------------------------------------------------
# Intended publication-branch identity / lost creation response (req item 4)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LostCreationResolution:
    """Reconciliation of a lost PR-creation response by exact head/base identity."""

    outcome: str
    pr_number: int | None
    summary: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome,
            "prNumber": self.pr_number,
            "summary": self.summary,
        }


def resolve_lost_pr_creation(
    *,
    intended_head: Any = "",
    intended_base: Any = "",
    intended_head_repo: Any = "",
    intended_head_sha: Any = "",
    observed_prs: Sequence[Mapping[str, Any]] | None = None,
    publication_record_saved: bool = False,
) -> LostCreationResolution:
    """Discover the existing PR for the exact intended head/base identity.

    The intended publication-branch identity (preserved through the existing
    publication record and portable handoff) is inspected on a lost creation
    response or crash before saving the PR number. Ambiguous identities stay
    blocked; a second PR is never created to reconcile the loss. Branch refs
    are not globally unique across forks, so adoption also compares the head
    repository (and the pinned revision when supplied) before adopting.
    """
    head = _text(intended_head)
    base = _text(intended_base)
    want_repo = _text(intended_head_repo)
    want_sha = _text(intended_head_sha)
    if not head or not base:
        return LostCreationResolution(
            outcome="blocked_ambiguous",
            pr_number=None,
            summary="Intended head/base identity is missing; remains blocked.",
        )
    if not publication_record_saved:
        return LostCreationResolution(
            outcome="blocked_ambiguous",
            pr_number=None,
            summary=(
                "Intended publication-branch identity was not preserved; "
                "remains blocked rather than creating another PR."
            ),
        )
    matches: list[int] = []
    for item in observed_prs or []:
        if not isinstance(item, Mapping):
            continue
        item_head = item.get("head")
        item_base = item.get("base")
        head_ref = (
            _text(item_head.get("ref")) if isinstance(item_head, Mapping) else ""
        )
        base_ref = (
            _text(item_base.get("ref")) if isinstance(item_base, Mapping) else ""
        )
        if head_ref != head or base_ref != base:
            continue
        if isinstance(item_head, Mapping):
            observed_repo = (
                _text(item_head["repo"].get("full_name"))
                if isinstance(item_head.get("repo"), Mapping)
                else ""
            )
            observed_sha = _text(item_head.get("sha"))
        else:
            observed_repo = ""
            observed_sha = ""
        if want_repo:
            # Fork PRs can share branch/base names; without a verified head
            # repository the identity is ambiguous and must not be adopted.
            if not observed_repo or observed_repo != want_repo:
                continue
        if want_sha:
            if not observed_sha or observed_sha != want_sha:
                continue
        try:
            number = int(item.get("number"))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
        if number > 0:
            matches.append(number)
    if len(matches) == 1:
        return LostCreationResolution(
            outcome="adopt_existing",
            pr_number=matches[0],
            summary=(
                f"Adopted existing PR #{matches[0]} for exact identity "
                f"{head} -> {base}; no duplicate created."
            ),
        )
    if not matches:
        return LostCreationResolution(
            outcome="no_match",
            pr_number=None,
            summary=(
                "No PR matches the exact intended head/base identity; "
                "creation may be retried only under the admitted scope."
            ),
        )
    return LostCreationResolution(
        outcome="blocked_ambiguous",
        pr_number=None,
        summary="Multiple PRs share the intended identity; remains blocked.",
    )


# ---------------------------------------------------------------------------
# Merged and closed PR inspection (required work item 5)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MergedClosedDisposition:
    """Disposition of a merged or closed-unmerged PR target."""

    disposition: str
    implement_remaining_only: bool
    summary: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "disposition": self.disposition,
            "implementRemainingOnly": self.implement_remaining_only,
            "summary": self.summary,
        }


def dispose_merged_closed(
    *,
    pr_state: Any = PR_STATE_UNKNOWN,
    objective_satisfied: bool = False,
    competing_prs: int = 0,
    wrong_base: bool = False,
    uncertain_ownership: bool = False,
    incompatible_constraints: bool = False,
) -> MergedClosedDisposition:
    """Inspect merged/closed PRs instead of generic resolver errors.

    A merged PR triggers intended-destination reassessment. Closed-unmerged,
    competing, wrong-base, incompatible, or uncertain-ownership targets
    require attention and explicit disposition: never automatic reopening or
    winner selection.
    """
    state = _text(pr_state).lower()
    if state == PR_STATE_MERGED:
        if objective_satisfied:
            return MergedClosedDisposition(
                disposition="complete",
                implement_remaining_only=False,
                summary="Merged PR satisfies the objective on its destination.",
            )
        return MergedClosedDisposition(
            disposition="implement_remaining_in_new_work",
            implement_remaining_only=True,
            summary=(
                "Merged PR leaves requirements unmet; implement only the "
                "remaining requirements in newly admitted work."
            ),
        )
    if state == PR_STATE_CLOSED_UNMERGED:
        return MergedClosedDisposition(
            disposition="needs_attention",
            implement_remaining_only=False,
            summary="Closed-unmerged PR requires explicit disposition; not reopened.",
        )
    if state == PR_STATE_OPEN:
        if competing_prs > 1 or wrong_base or uncertain_ownership or incompatible_constraints:
            return MergedClosedDisposition(
                disposition="needs_attention",
                implement_remaining_only=False,
                summary=(
                    "Competing PRs, wrong base, incompatible constraints, or "
                    "uncertain ownership require attention; no silent winner."
                ),
            )
        return MergedClosedDisposition(
            disposition="continue_same_pr",
            implement_remaining_only=False,
            summary="Single open PR continues normally.",
        )
    return MergedClosedDisposition(
        disposition="needs_attention",
        implement_remaining_only=False,
        summary="Unreadable PR target requires attention.",
    )


# ---------------------------------------------------------------------------
# Exact recovery vs code-seeded continuation (required work item 6)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RecoveryEligibility:
    """Cross-device continuation eligibility class."""

    eligibility: str
    may_claim_exact_resume: bool
    may_start_continuation: bool
    summary: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "eligibility": self.eligibility,
            "mayClaimExactResume": self.may_claim_exact_resume,
            "mayStartContinuation": self.may_start_continuation,
            "summary": self.summary,
        }


def classify_recovery_eligibility(
    *,
    checkpoint_compatible: bool = False,
    runtime_compatible: bool = False,
    immutable_inputs_compatible: bool = False,
    github_code_available: bool = False,
    sanitized_handoff_available: bool = False,
    private_only_checkpoint: bool = False,
    exact_restore_supported: bool = True,
) -> RecoveryEligibility:
    """Distinguish exact failed-step recovery from code-seeded continuation.

    Exact recovery additionally requires all compatible checkpoint inputs.
    GitHub-accessible code plus a sanitized handoff may support a new
    continuation without those guarantees. A private-only checkpoint is not
    cross-device recoverability and can never justify starting over while
    hidden work might exist.
    """
    if private_only_checkpoint:
        return RecoveryEligibility(
            eligibility=ELIGIBILITY_OWNER_ONLY,
            may_claim_exact_resume=False,
            may_start_continuation=False,
            summary=(
                "Work exists only in another device's private storage; "
                "requires owner recovery, not a fresh start that discards it."
            ),
        )
    if not exact_restore_supported:
        if github_code_available and sanitized_handoff_available:
            return RecoveryEligibility(
                eligibility=ELIGIBILITY_CODE_SEEDED_CONTINUATION,
                may_claim_exact_resume=False,
                may_start_continuation=True,
                summary=(
                    "Exact restore is unsupported; a code-seeded continuation "
                    "from GitHub code and a sanitized handoff is allowed, "
                    "reported as continuation rather than exact resume."
                ),
            )
        return RecoveryEligibility(
            eligibility=ELIGIBILITY_BLOCKED,
            may_claim_exact_resume=False,
            may_start_continuation=False,
            summary="Exact restore unsupported and no portable handoff exists.",
        )
    if checkpoint_compatible and runtime_compatible and immutable_inputs_compatible:
        return RecoveryEligibility(
            eligibility=ELIGIBILITY_EXACT_RECOVERY,
            may_claim_exact_resume=True,
            may_start_continuation=True,
            summary="Compatible checkpoint, runtime, and immutable inputs allow exact recovery.",
        )
    if github_code_available and sanitized_handoff_available:
        return RecoveryEligibility(
            eligibility=ELIGIBILITY_CODE_SEEDED_CONTINUATION,
            may_claim_exact_resume=False,
            may_start_continuation=True,
            summary=(
                "Checkpoint inputs are incompatible; continuation from "
                "GitHub-accessible code and a sanitized handoff is allowed "
                "without exact-resume claims."
            ),
        )
    return RecoveryEligibility(
        eligibility=ELIGIBILITY_BLOCKED,
        may_claim_exact_resume=False,
        may_start_continuation=False,
        summary="No compatible checkpoint and no portable handoff; blocked.",
    )


# ---------------------------------------------------------------------------
# Publication scope and merge-transfer guards (required work items 7-8)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PublicationScopeDecision:
    """Enforcement of the authored publication scope at the routing layer."""

    allowed: Sequence[str]
    qualified_local_save: bool
    owner_recovery_report: bool
    summary: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": list(self.allowed),
            "qualifiedLocalSave": self.qualified_local_save,
            "ownerRecoveryReport": self.owner_recovery_report,
            "summary": self.summary,
        }


def check_publication_scope(
    *,
    scope: Any = None,
    requested: Sequence[Any] | None = None,
) -> PublicationScopeDecision:
    """Preserve the authored publication scope.

    Explicit ``None`` authorizes no checkpoint push, PR, or merge. The
    routing layer reuses authorized existing-PR repair/merge routes through
    the existing boundary and never introduces a native second resolver;
    where only local saving is admitted it reports owner-only recovery.
    """
    wanted = [_text(item).lower() for item in (requested or []) if _text(item)]
    if scope is None:
        return PublicationScopeDecision(
            allowed=[],
            qualified_local_save=True,
            owner_recovery_report=True,
            summary=(
                "Authored publication scope is None: no push, PR, or merge is "
                "authorized; use qualified existing local saving and report "
                "owner-only recovery."
            ),
        )
    scope_text = _text(scope).lower() if not isinstance(scope, (list, tuple)) else ""
    if isinstance(scope, (list, tuple)):
        allowed = [_text(item) for item in scope if _text(item)]
        denied = [item for item in wanted if item not in {a.lower() for a in allowed}]
        if denied:
            return PublicationScopeDecision(
                allowed=[a for a in allowed if a.lower() in {w for w in wanted}],
                qualified_local_save=True,
                owner_recovery_report=False,
                summary=(
                    f"Requested {', '.join(denied)} exceeds the authored scope; "
                    "only in-scope operations are allowed."
                ),
            )
        return PublicationScopeDecision(
            allowed=wanted,
            qualified_local_save=False,
            owner_recovery_report=False,
            summary="Requested operations are within the authored scope.",
        )
    if scope_text in {"none", "local_only", "local-only", ""}:
        if wanted:
            return PublicationScopeDecision(
                allowed=[],
                qualified_local_save=True,
                owner_recovery_report=True,
                summary=(
                    "Scope admits local saving only; requested remote effects "
                    "are denied and owner-only recovery is reported."
                ),
            )
        return PublicationScopeDecision(
            allowed=[],
            qualified_local_save=True,
            owner_recovery_report=False,
            summary="Local-only scope; no remote effects requested.",
        )
    # Fail closed: an unrecognized scalar scope (for example a malformed or
    # unsupported persisted value such as "typo") authorizes no remote effect
    # rather than every requested operation.
    return PublicationScopeDecision(
        allowed=[],
        qualified_local_save=True,
        owner_recovery_report=True,
        summary=(
            f"Unrecognized publication scope '{scope_text}'; no push, PR, or "
            "merge is authorized."
        ),
    )


@dataclass(frozen=True)
class MergeTransferGuard:
    """Guard against merging incomplete code merely to transfer work."""

    allow_merge: bool
    keep_issue_open: bool
    forbid_closing_semantics: bool
    summary: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowMerge": self.allow_merge,
            "keepIssueOpen": self.keep_issue_open,
            "forbidClosingSemantics": self.forbid_closing_semantics,
            "summary": self.summary,
        }


def check_merge_transfer(
    *,
    implementation_complete: bool = False,
    explicitly_reviewed_partial: bool = False,
) -> MergeTransferGuard:
    """Decide whether a merge may carry work between devices.

    Incomplete code is never merged merely to transfer it. An explicitly
    reviewed partial increment may merge only while leaving unmet scope open
    and without misleading closing semantics. Normal completion still
    requires the existing verification/review/merge policy.
    """
    if implementation_complete:
        return MergeTransferGuard(
            allow_merge=True,
            keep_issue_open=False,
            forbid_closing_semantics=False,
            summary="Complete implementation merges under the normal policy.",
        )
    if explicitly_reviewed_partial:
        return MergeTransferGuard(
            allow_merge=True,
            keep_issue_open=True,
            forbid_closing_semantics=True,
            summary=(
                "Reviewed partial increment may merge only with unmet scope "
                "left open and no closing semantics."
            ),
        )
    return MergeTransferGuard(
        allow_merge=False,
        keep_issue_open=True,
        forbid_closing_semantics=True,
        summary="Incomplete unreviewed code must not merge to transfer work.",
    )


# ---------------------------------------------------------------------------
# Verification binding (acceptance: tied to current requirements + revision)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VerificationBinding:
    """Whether prior verification evidence still applies to the current head."""

    current: bool
    require_reassessment: bool
    summary: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "current": self.current,
            "requireReassessment": self.require_reassessment,
            "summary": self.summary,
        }


def bind_verification_to_revision(
    *,
    verified_sha: Any = "",
    current_sha: Any = "",
    requirements_unchanged: bool = True,
) -> VerificationBinding:
    """Tie verification to the exact code revision and current requirements.

    Unexpected changes are never hidden by prior success: any head movement
    or requirement change invalidates stale head-bound verification.
    """
    verified = _text(verified_sha)
    current = _text(current_sha)
    if not verified or not current:
        return VerificationBinding(
            current=False,
            require_reassessment=True,
            summary="Verification revision is unknown; reassessment required.",
        )
    if verified != current or not requirements_unchanged:
        return VerificationBinding(
            current=False,
            require_reassessment=True,
            summary=(
                "Head or requirements changed since verification; prior "
                "success does not carry over."
            ),
        )
    return VerificationBinding(
        current=True,
        require_reassessment=False,
        summary="Verification matches the current head and requirements.",
    )


# ---------------------------------------------------------------------------
# Admission-boundary adapter (production consumption entrypoint)
# ---------------------------------------------------------------------------


def plan_issue_continuation(
    *,
    repository: str,
    issue_number: int,
    lineage_validated: bool,
    lineage_pr_url: Any = "",
    lineage_pr_head: Any = "",
    lineage_pr_base: Any = "",
    lineage_saved_branch: Any = "",
    lineage_saved_sha: Any = "",
    candidate_basis: Any = "attempt_lineage+github_read",
    github_pr: Mapping[str, Any] | None = None,
    gate_evidence: Mapping[str, Any] | None = None,
    implementation_complete: bool = False,
    verification_current: bool = False,
    writable: bool = True,
) -> dict[str, Any]:
    """Discover trusted existing work and route it to its remaining phase.

    Admission-boundary entrypoint consumed by the issue lifecycle
    (``github_issue_lifecycle.preserved_work_continuation``): the caller passes
    the validated attempt lineage and the direct GitHub PR read, and receives
    the typed discovery result plus same-PR phase routing. Routing applies to
    trusted open PRs; every other trusted outcome (saved-branch-only, merged,
    fresh) is carried by the discovery ``nextAction`` and needs no same-PR
    routing. Deterministic and side-effect-free.
    """
    discovery = discover_existing_work(
        repository=repository,
        issue_number=issue_number,
        lineage_validated=lineage_validated,
        lineage_pr_url=lineage_pr_url,
        lineage_pr_head=lineage_pr_head,
        lineage_pr_base=lineage_pr_base,
        lineage_saved_branch=lineage_saved_branch,
        lineage_saved_sha=lineage_saved_sha,
        candidate_basis=candidate_basis,
        github_pr=github_pr,
        gate_evidence=gate_evidence,
    )
    routing: ContinuationRouting | None = None
    if (
        discovery.trusted
        and discovery.existing_work is not None
        and discovery.existing_work.pr_state == PR_STATE_OPEN
    ):
        routing = route_continuation(
            discovery.existing_work,
            implementation_complete=implementation_complete,
            verification_current=verification_current,
            writable=writable,
        )
    return {
        "discovery": discovery.to_dict(),
        "routing": routing.to_dict() if routing is not None else None,
    }


__all__ = [
    "INSUFFICIENT_EVIDENCE_KINDS",
    "SUFFICIENT_EVIDENCE_BASES",
    "NEXT_CONTINUE_SAME_PR",
    "NEXT_VERIFY_ONLY",
    "NEXT_FINALIZE_ONLY",
    "NEXT_CREATE_PR_FROM_SAVED",
    "NEXT_FRESH_IMPLEMENT",
    "NEXT_NEEDS_ATTENTION",
    "NEXT_OWNER_RECOVERY",
    "NEXT_BLOCKED",
    "PR_STATE_OPEN",
    "PR_STATE_MERGED",
    "PR_STATE_CLOSED_UNMERGED",
    "PR_STATE_UNKNOWN",
    "ANCESTRY_IDENTICAL",
    "ANCESTRY_SAVED_BEHIND_PR",
    "ANCESTRY_SAVED_AHEAD_OF_PR",
    "ANCESTRY_DIVERGED",
    "ANCESTRY_UNKNOWN",
    "ELIGIBILITY_EXACT_RECOVERY",
    "ELIGIBILITY_CODE_SEEDED_CONTINUATION",
    "ELIGIBILITY_OWNER_ONLY",
    "ELIGIBILITY_BLOCKED",
    "ExistingWork",
    "DiscoveryResult",
    "discover_existing_work",
    "ContinuationRouting",
    "route_continuation",
    "SavedVsPrComparison",
    "compare_saved_vs_pr",
    "LostCreationResolution",
    "resolve_lost_pr_creation",
    "MergedClosedDisposition",
    "dispose_merged_closed",
    "RecoveryEligibility",
    "classify_recovery_eligibility",
    "PublicationScopeDecision",
    "check_publication_scope",
    "MergeTransferGuard",
    "check_merge_transfer",
    "VerificationBinding",
    "bind_verification_to_revision",
    "plan_issue_continuation",
]
