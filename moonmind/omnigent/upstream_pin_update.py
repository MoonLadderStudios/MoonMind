"""Reviewed upstream omnigent pin updates with exact-artifact qualification.

Source issue: MoonLadderStudios/MoonMind#3957.

This module is the hermetic planning contract behind the scheduled/manual
``omnigent-upstream-pin-updater`` workflow.  It contains no network access,
no subprocess calls, and no provider semantics: it turns an already-fetched
upstream release inventory (plus the already-resolved immutable tag commits)
into one explicit outcome, one evidence record, and one freshness status.

Non-negotiables enforced here:

* The ``omnigent`` gitlink is an immutable commit pin.  A release tag is
  provenance, never a substitute for the resolved commit.
* Prereleases and drafts are excluded unless the caller explicitly allows
  prereleases.  Drafts are never eligible.
* A release without a resolved immutable commit (missing, moved, or invalid
  tag) blocks the update instead of silently tracking upstream main.
* An empty eligible set is reported as ``no_suitable_release``, never as
  an instruction to track upstream main.
* Selection is idempotent: re-running with the same inputs yields the same
  outcome, and an already-current pin yields ``up_to_date``.
* Merge-time hermetic success (``tested``) never implies protected live
  evidence (``qualified``) or product-default promotion (``promoted``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal, Mapping, Sequence

from moonmind.omnigent.conformance import assert_secret_free

ISSUE_REF = "MoonLadderStudios/MoonMind#3957"
UPSTREAM_REPO_DEFAULT = "omnigent-ai/omnigent"
CHECK_CADENCE = "weekly"

CANDIDATE_SCHEMA_VERSION = "moonmind.omnigent-upstream-pin-candidate/v1"
EVIDENCE_SCHEMA_VERSION = "moonmind.omnigent-upstream-pin-evidence/v1"
STATUS_SCHEMA_VERSION = "moonmind.omnigent-upstream-pin-status/v1"

OutcomeStatus = Literal[
    "up_to_date",
    "candidate_available",
    "no_suitable_release",
    "blocked_invalid_tag",
    "blocked_transient_upstream",
]

VersionStage = Literal["available", "tested", "qualified", "promoted"]

#: Exact affected-asset inventory the evidence record must name.  Kept as a
#: tuple so the workflow and the tests share one canonical list.
AFFECTED_ASSETS = (
    "omnigent gitlink",
    "generated adapter assets",
    "lockfiles",
    "runtime-pack compatibility",
    "server/host images",
    "native network-contract fixtures",
)

#: Hermetic qualification shards that own the suites the update must run
#: through (adapter, host-registration, credential-materializer,
#: session/control, native UI/facade, evidence, cleanup).
QUALIFICATION_SHARDS = (
    "tests/unit/omnigent/test_adapter_contracts.py",
    "tests/unit/omnigent/test_host_protocol_adapter.py",
    "tests/unit/omnigent/test_host_registration_inventory.py",
    "tests/unit/omnigent/test_oauth_home_materializers.py",
    "tests/unit/omnigent/test_omnigent_session_timeline_api.py",
    "tests/unit/omnigent/test_session_supervisor_admission.py",
    "tests/unit/omnigent/test_control_plane_readiness.py",
    "tests/unit/omnigent/test_native_ui.py",
    "tests/unit/omnigent/test_native_ui_compat.py",
    "tests/unit/omnigent/test_omnigent_facade_unknown_route_fails_closed.py",
    "tests/unit/omnigent/test_authority_chain_evidence.py",
    "tests/unit/omnigent/test_execution_support_evidence.py",
    "tests/unit/omnigent/test_deployment_evidence_publishing.py",
    "tests/unit/omnigent/test_host_cleanup_service.py",
    "tests/unit/omnigent/test_oauth_host_janitor.py",
)

_FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
_SHORT_SHA = re.compile(r"^[0-9a-f]{7,40}$")


class UpstreamPinUpdateError(ValueError):
    """Raised when updater inputs cannot be interpreted safely."""


def normalize_commit(value: Any) -> str:
    """Validate a gitlink commit pin (full 40-hex immutable SHA)."""
    if not isinstance(value, str) or not _FULL_SHA.match(value.strip().lower()):
        raise UpstreamPinUpdateError(
            "current commit must be a full 40-hex immutable SHA "
            f"(got {value!r}); read it from `git ls-tree HEAD omnigent`"
        )
    return value.strip().lower()


def _normalize_resolved_commit(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip().lower()
    return candidate if _FULL_SHA.match(candidate) else None


def _is_draft_release(release: Mapping[str, Any]) -> bool:
    return bool(release.get("draft", False))


def _is_prerelease(release: Mapping[str, Any]) -> bool:
    return bool(release.get("prerelease", False))


def _release_tag(release: Mapping[str, Any]) -> str:
    tag = release.get("tag_name", release.get("tag"))
    return tag.strip() if isinstance(tag, str) else ""


def is_eligible_release(
    release: Mapping[str, Any], *, allow_prereleases: bool = False
) -> bool:
    """Return True when a release may become an update candidate.

    Eligibility is deliberately narrow: named tag, not a draft, not a
    prerelease unless explicitly allowed, and carrying a resolved immutable
    commit.  Anything else is either skipped (draft/prerelease when not
    allowed) or a blocking input problem (missing/invalid commit) handled
    by :func:`select_candidate`.
    """
    if not isinstance(release, Mapping):
        return False
    if _is_draft_release(release):
        return False
    if _is_prerelease(release) and not allow_prereleases:
        return False
    if not _release_tag(release):
        return False
    return _normalize_resolved_commit(release.get("resolved_commit")) is not None


def _published_at_sort_key(release: Mapping[str, Any]) -> str:
    published = release.get("published_at", release.get("publishedAt", ""))
    return published if isinstance(published, str) else ""


@dataclass(frozen=True, slots=True)
class CandidateResult:
    """Explicit planner outcome for one updater evaluation."""

    status: OutcomeStatus
    candidate: dict[str, Any] | None
    reason: str


def select_candidate(
    current_commit: str,
    releases: Sequence[Mapping[str, Any]],
    *,
    allow_prereleases: bool = False,
    upstream_repo: str = UPSTREAM_REPO_DEFAULT,
) -> CandidateResult:
    """Select the newest eligible upstream release, or report an explicit state.

    Never raises for domain states: missing releases, moved/invalid tags,
    and repeated runs each map to a closed-vocabulary outcome.  Only
    malformed caller inputs (bad current pin, non-list inventory) raise.
    """
    current = normalize_commit(current_commit)
    if not isinstance(releases, Sequence) or isinstance(releases, (str, bytes)):
        raise UpstreamPinUpdateError("releases must be a sequence of release objects")
    if not upstream_repo or not isinstance(upstream_repo, str):
        raise UpstreamPinUpdateError("upstream_repo must be a non-empty string")

    inventory: list[Mapping[str, Any]] = [
        rel for rel in releases if isinstance(rel, Mapping)
    ]
    eligible = [
        rel
        for rel in inventory
        if is_eligible_release(rel, allow_prereleases=allow_prereleases)
    ]
    if not eligible:
        if not inventory:
            return CandidateResult(
                status="no_suitable_release",
                candidate=None,
                reason=(
                    "upstream published no releases; leaving the known-good pin "
                    "intact instead of tracking upstream main"
                ),
            )
        tagged = [rel for rel in inventory if _release_tag(rel) and not _is_draft_release(rel)]
        if tagged and all(
            _normalize_resolved_commit(rel.get("resolved_commit")) is None
            for rel in tagged
        ):
            return CandidateResult(
                status="blocked_invalid_tag",
                candidate=None,
                reason=(
                    "eligible tags carry no resolvable immutable commit "
                    "(moved, deleted, or invalid tag); leaving the known-good "
                    "pin intact"
                ),
            )
        excluded = "prereleases/drafts" if not allow_prereleases else "drafts"
        return CandidateResult(
            status="no_suitable_release",
            candidate=None,
            reason=(
                f"no eligible release after excluding {excluded}; leaving the "
                "known-good pin intact instead of tracking upstream main"
            ),
        )

    newest = sorted(
        eligible,
        key=lambda rel: (_published_at_sort_key(rel), _release_tag(rel)),
        reverse=True,
    )[0]
    resolved = _normalize_resolved_commit(newest.get("resolved_commit"))
    assert resolved is not None  # guaranteed by eligibility filter
    tag = _release_tag(newest)
    if resolved == current:
        return CandidateResult(
            status="up_to_date",
            candidate=None,
            reason=f"pin already resolves {tag} ({resolved}); no update required",
        )
    return CandidateResult(
        status="candidate_available",
        candidate={
            "tag": tag,
            "commit": resolved,
            "name": newest.get("name", ""),
            "url": newest.get("html_url", newest.get("url", "")),
            "publishedAt": newest.get("published_at", newest.get("publishedAt", "")),
            "prerelease": _is_prerelease(newest),
            "upstreamRepo": upstream_repo,
        },
        reason=f"eligible release {tag} resolves to {resolved}",
    )


def blocked_transient_upstream(detail: str) -> CandidateResult:
    """Explicit outcome for transient upstream fetch failures (retryable)."""
    bounded = (detail or "upstream request failed").strip()[:300]
    return CandidateResult(
        status="blocked_transient_upstream",
        candidate=None,
        reason=f"transient upstream error ({bounded}); pin left intact, retry on next check",
    )


def _utc_now_iso(checked_at: str | None) -> str:
    if checked_at:
        parsed = datetime.fromisoformat(checked_at.replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def build_update_evidence(
    *,
    result: CandidateResult,
    current_commit: str,
    upstream_repo: str = UPSTREAM_REPO_DEFAULT,
    qualification_shards: Sequence[str] = QUALIFICATION_SHARDS,
    api_host_ui_changes: str = "",
    checked_at: str | None = None,
) -> dict[str, Any]:
    """Build the exact-artifact evidence record for a planner outcome.

    The record always names old/new commits, release provenance, the exact
    affected assets, qualification inputs (all pinned), and the owning
    shards.  Prior support evidence for the old commit is referenced as
    rollback identity, never carried over as qualification of the new
    combination.
    """
    current = normalize_commit(current_commit)
    candidate = result.candidate or {}
    new_commit = str(candidate.get("commit", current))
    evidence: dict[str, Any] = {
        "schemaVersion": EVIDENCE_SCHEMA_VERSION,
        "issueRef": ISSUE_REF,
        "status": result.status,
        "reason": result.reason,
        "upstreamRepo": upstream_repo,
        "oldCommit": current,
        "newCommit": new_commit,
        "releaseProvenance": {
            "tag": candidate.get("tag", ""),
            "name": candidate.get("name", ""),
            "url": candidate.get("url", ""),
            "publishedAt": candidate.get("publishedAt", ""),
            "prerelease": bool(candidate.get("prerelease", False)),
        },
        "apiHostUiChanges": api_host_ui_changes[:2000],
        "affectedAssets": list(AFFECTED_ASSETS),
        "qualificationInputs": {
            "currentCommit": current,
            "candidateCommit": new_commit,
            "releaseTag": candidate.get("tag", ""),
            "upstreamRepo": upstream_repo,
            "qualificationShards": list(qualification_shards),
        },
        "rollbackIdentity": {
            "previousQualifiedCommit": current,
            "note": (
                "retain the previous qualified artifact identities for the "
                "documented rollback path; historical run bindings are never "
                "rewritten"
            ),
        },
        "generatedAt": _utc_now_iso(checked_at),
    }
    assert_secret_free(evidence)
    return evidence


def build_freshness_status(
    *,
    result: CandidateResult,
    current_commit: str,
    last_qualified_commit: str | None = None,
    last_qualified_tag: str = "",
    promoted_commit: str | None = None,
    blocked_reason: str = "",
    checked_at: str | None = None,
) -> dict[str, Any]:
    """Publish update freshness with available/tested/qualified/promoted stages.

    * ``available`` — the candidate the updater discovered (may be empty).
    * ``tested`` — set only by a merge-time hermetic qualification run.
    * ``qualified`` — set only by protected live evidence for the exact
      combination (never copied from a previous commit or image digest).
    * ``promoted`` — set only when the rollout policy makes the qualified
      combination a product default.
    """
    current = normalize_commit(current_commit)
    qualified = (
        normalize_commit(last_qualified_commit)
        if last_qualified_commit is not None
        else current
    )
    promoted = (
        normalize_commit(promoted_commit) if promoted_commit is not None else qualified
    )
    candidate = result.candidate or {}
    if result.status in ("blocked_invalid_tag", "blocked_transient_upstream"):
        blocked = result.reason
    else:
        blocked = blocked_reason
    status: dict[str, Any] = {
        "schemaVersion": STATUS_SCHEMA_VERSION,
        "issueRef": ISSUE_REF,
        "checkCadence": CHECK_CADENCE,
        "checkedAt": _utc_now_iso(checked_at),
        "outcome": result.status,
        "available": {
            "tag": candidate.get("tag", ""),
            "commit": candidate.get("commit", ""),
        },
        "tested": {"commit": "", "hermeticRunRef": ""},
        "qualified": {"commit": qualified, "tag": last_qualified_tag},
        "promoted": {"commit": promoted},
        "lastQualifiedVersion": {"commit": qualified, "tag": last_qualified_tag},
        "blockedReason": blocked,
    }
    assert_secret_free(status)
    return status


def mark_tested(
    status: Mapping[str, Any], *, commit: str, hermetic_run_ref: str
) -> dict[str, Any]:
    """Record merge-time hermetic success without implying qualification."""
    updated = dict(status)
    updated["tested"] = {
        "commit": normalize_commit(commit),
        "hermeticRunRef": hermetic_run_ref,
    }
    assert_secret_free(updated)
    return updated
