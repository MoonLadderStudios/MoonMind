"""Automated review provider identities shared by every resolver host.

This table is the single canonical mapping from a provider-neutral review
provider name to the exact request command, the reviewer identities whose
results count as that provider's answer, and how that answer's text is read.
The portable Skill uses it to decide whether a fresh review exists for the
current head; MoonMind uses it so a child run can only ever ask for a
*configured* provider and never for arbitrary comment text. Both hosts read a
provider's reply to a request through :func:`latest_review_reply`.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from types import MappingProxyType
from typing import Any
from urllib.parse import urlparse

# ``failure_class`` values share the canonical provider-failure class names so a
# host can report them through its existing provider-failure envelope.
REVIEW_FAILURE_RATE_LIMIT = "rate_limit"


@dataclass(frozen=True, slots=True)
class AutomatedReviewProvider:
    """One automated reviewer and the exact way it is requested."""

    provider: str
    command: str
    reviewer_logins: tuple[str, ...]
    clean_review_reactions: tuple[str, ...] = ("+1",)
    # The sentence that opens the clean result; the remaining opening-line text
    # is provider flair and is still checked for high-severity findings.
    clean_review_result: str = ""
    # ``(failure_class, openings)`` pairs recognizing the provider's notice that
    # it refused or could not perform a requested review.
    failure_reply_prefixes: tuple[tuple[str, tuple[str, ...]], ...] = ()


AUTOMATED_REVIEW_PROVIDERS = MappingProxyType(
    {
        "codex": AutomatedReviewProvider(
            provider="codex",
            command="@codex review",
            reviewer_logins=("chatgpt-codex-connector",),
            clean_review_result="Codex Review: Didn't find any major issues.",
            # "You have reached your Codex usage limits for code reviews." and
            # "Codex usage limits have been reached for code reviews."
            failure_reply_prefixes=(
                (
                    REVIEW_FAILURE_RATE_LIMIT,
                    (
                        "you have reached your codex usage limits for code reviews.",
                        "codex usage limits have been reached for code reviews.",
                        "you have reached your codex usage limits.",
                    ),
                ),
            ),
        ),
    }
)

DEFAULT_AUTOMATED_REVIEW_PROVIDER = "codex"


def normalize_provider_name(value: object) -> str:
    return str(value or "").strip().lower()


def resolve_automated_review_provider(value: object) -> AutomatedReviewProvider | None:
    """Return the provider record, or ``None`` when it is unknown/disabled."""

    name = normalize_provider_name(value)
    if not name or name == "none":
        return None
    return AUTOMATED_REVIEW_PROVIDERS.get(name)


def automated_review_provider_or_raise(value: object) -> AutomatedReviewProvider:
    """Return the provider record or fail fast on an unsupported provider."""

    provider = resolve_automated_review_provider(value)
    if provider is None:
        raise ValueError(
            "unsupported automated review provider: "
            f"{normalize_provider_name(value) or '<empty>'}"
        )
    return provider


def normalize_reviewer_login(login: object) -> str:
    normalized = str(login or "").strip().lower()
    if normalized.endswith("[bot]"):
        normalized = normalized[: -len("[bot]")]
    return normalized


# Severity identifies findings in provider-authored summary/reply bodies; it
# never disposes of them. Every priority uses the same applicability rules.
# Severity also decides whether a provider's clean result completes its review:
# P2/medium-or-below text after the clean result still completes it, while those
# findings stay actionable through the comment inventory.
_SEVERITY_P_RE = re.compile(
    r"\bP\s*[0-9]\b|\bsev\s*[0-9]\b",
    re.IGNORECASE,
)
_SEVERITY_TEXT_RE = re.compile(
    r"\bseverity\s*[:=\-]\s*(critical|high|major|severe|urgent|blocker|medium|low|minor|nit|info|p[0-9])\b"
    r"|\bpriority\s*[:=\-]\s*(critical|high|highest|urgent|blocker|medium|low|minor|p[0-9])\b"
    r"|\[(critical|high|major|severe|urgent|blocker|medium|low|minor|nit|info|p[0-9]|sev[0-9])\]"
    r"|^(critical|high|medium|low|minor|nit)\s*[:\-]"
    r"|\b(critical|high|medium|low|minor)\s+(severity|priority)\b"
    r"|\b(severity|priority)\s+(critical|high|medium|low|minor)\b",
    re.IGNORECASE | re.MULTILINE,
)


def has_explicit_finding_severity(body: object) -> bool:
    """Identify marked findings without deciding whether they still apply."""

    text = str(body or "")
    return bool(_SEVERITY_P_RE.search(text) or _SEVERITY_TEXT_RE.search(text))


# High-severity detection is restricted to structured priority/severity labels
# (for example `[P1]`, `priority: high`, or `severity: critical`) so prose
# adjectives in explicitly low-priority findings (for example
# `[P2] Avoid high memory usage`) cannot promote them back to high.
_HIGH_SEVERITY_P_RE = re.compile(
    r"\bP\s*[01]\b|\bsev\s*[01]\b",
    re.IGNORECASE,
)
_HIGH_SEVERITY_TEXT_RE = re.compile(
    r"\bseverity\s*[:=\-]\s*(critical|high|major|severe|urgent|blocker|p[01])\b"
    r"|\bpriority\s*[:=\-]\s*(critical|high|highest|urgent|blocker|p[01])\b"
    r"|\[(critical|high|major|severe|urgent|blocker|p[01]|sev[01])\]"
    r"|^(critical|high)\s*[:\-]"
    r"|\b(critical|high)\s+(severity|priority)\b"
    r"|\b(severity|priority)\s+(critical|high)\b",
    re.IGNORECASE | re.MULTILINE,
)
_NIT_RE = re.compile(r"\bnits?\b", re.IGNORECASE)


def has_high_severity_finding(body: object) -> bool:
    """Return True when *body* carries a structured P0/critical or P1/high marker."""

    text = str(body or "")
    return bool(
        _HIGH_SEVERITY_P_RE.search(text) or _HIGH_SEVERITY_TEXT_RE.search(text)
    )


def is_low_severity_only_finding(body: object) -> bool:
    """Return True when *body* is explicitly low severity and nothing higher.

    Text without any severity marker is conservatively treated as requiring
    review (return False) so unmarked feedback never completes a review.
    """

    text = str(body or "")
    marked = has_explicit_finding_severity(text) or bool(_NIT_RE.search(text))
    return marked and not has_high_severity_finding(text)


def is_automated_review_provider_login(provider: object, login: object) -> bool:
    record = resolve_automated_review_provider(provider)
    if record is None:
        return False
    return normalize_reviewer_login(login) in record.reviewer_logins


@dataclass(frozen=True, slots=True)
class ReviewReply:
    """The provider's authoritative answer to a review request.

    ``failure_class`` is empty for a clean result and names the provider
    failure (for example ``rate_limit``) when the provider refused the request.
    """

    comment: Mapping[str, Any]
    created_at: datetime
    failure_class: str = ""


def _comment_time(comment: Mapping[str, Any]) -> datetime | None:
    try:
        value = datetime.fromisoformat(
            str(comment.get("created_at") or "").replace("Z", "+00:00")
        )
    except ValueError:
        return None
    return value if value.tzinfo is not None else None


def _comment_id(value: object) -> int | None:
    # REST comment IDs share one monotonically increasing namespace. Review
    # and reaction IDs do not, so this tie-breaker is only for issue comments.
    text = str(value)
    return int(text) if text.isascii() and text.isdecimal() and int(text) > 0 else None


def review_comment_is_after(
    created_at: datetime,
    comment_id: object,
    earlier_at: datetime,
    earlier_id: object,
) -> bool:
    """Compare comment times, using numeric IDs for equal-second causality."""
    if created_at != earlier_at:
        return created_at > earlier_at
    later = _comment_id(comment_id)
    earlier = _comment_id(earlier_id)
    return later is not None and earlier is not None and later > earlier


@dataclass(frozen=True, slots=True)
class ReviewRequest:
    """The latest explicit request for a provider on the unchanged head."""

    comment: Mapping[str, Any]
    created_at: datetime


def is_review_request_comment(provider: AutomatedReviewProvider, comment: Any) -> bool:
    """Recognize only the provider's explicit issue-comment command."""
    if (
        not isinstance(comment, Mapping)
        or comment.get("type", "issue_comment") != "issue_comment"
    ):
        return False
    raw_body = str(comment.get("body") or "")
    opening = next((line for line in raw_body.splitlines() if line.strip()), "")
    if opening.startswith(("    ", "\t")):
        return False
    return (
        " ".join(raw_body.split()).rstrip(".").strip().lower()
        == provider.command.lower()
    )


def _review_requests(
    provider: AutomatedReviewProvider,
    comments: Iterable[Any],
    *,
    head_sha: str,
    not_before: datetime | None = None,
) -> Iterable[ReviewRequest]:
    """Recognize explicit requests once for every resolver host.

    Hosts supply a head timestamp or the active request as the lower bound.
    REST issue-comment collections omit ``type``; portable inventories must
    explicitly identify any non-issue records so they cannot become requests.
    """

    if not head_sha:
        return
    for comment in comments:
        if not is_review_request_comment(provider, comment):
            continue
        created_at = _comment_time(comment)
        if created_at is None or (not_before is not None and created_at < not_before):
            continue
        commit = str(comment.get("commit_id") or "").strip()
        if commit and commit != head_sha:
            continue
        yield ReviewRequest(comment=comment, created_at=created_at)


def latest_review_request(
    provider: AutomatedReviewProvider,
    comments: Iterable[Any],
    *,
    head_sha: str,
    not_before: datetime | None = None,
) -> ReviewRequest | None:
    """Select the causally latest explicit request for the unchanged head."""
    latest = None
    for request in _review_requests(
        provider, comments, head_sha=head_sha, not_before=not_before
    ):
        if latest is None or review_comment_is_after(
            request.created_at,
            request.comment.get("id"),
            latest.created_at,
            latest.comment.get("id"),
        ):
            latest = request
    return latest


def review_requests_after(
    provider: AutomatedReviewProvider,
    comments: Iterable[Any],
    *,
    head_sha: str,
    request_comment_id: object,
    requested_at: datetime,
) -> list[ReviewRequest]:
    """Retain every identifiable request from the active receipt onward."""
    anchor_id = _comment_id(request_comment_id)
    if anchor_id is None:
        raise ValueError("The active review request has no valid comment identity.")
    requests: dict[int, ReviewRequest] = {}
    for request in _review_requests(provider, comments, head_sha=head_sha):
        identifier = _comment_id(request.comment.get("id"))
        if identifier is None:
            raise ValueError("A review request has no valid comment identity.")
        if identifier == anchor_id:
            if request.created_at != requested_at:
                raise ValueError(
                    "The active review request timestamp is contradictory."
                )
        elif not review_comment_is_after(
            request.created_at, identifier, requested_at, anchor_id
        ):
            continue
        prior = requests.get(identifier)
        if prior is not None and prior.created_at != request.created_at:
            raise ValueError("A review request has contradictory timestamps.")
        requests[identifier] = request
    return sorted(
        requests.values(),
        key=lambda request: (
            request.created_at,
            _comment_id(request.comment.get("id")),
        ),
    )


def _request_reply_time(
    provider: AutomatedReviewProvider,
    comment: Mapping[str, Any],
    *,
    requested_at: datetime | None,
    head_sha: str,
    request_comment_id: object = None,
) -> datetime | None:
    """Return when *comment* answered the request, or ``None`` if it cannot."""

    user = comment.get("user")
    login = user.get("login") if isinstance(user, Mapping) else user
    if normalize_reviewer_login(login) not in provider.reviewer_logins:
        return None
    if requested_at is None or not head_sha:
        return None
    created_at = _comment_time(comment)
    if created_at is None or not review_comment_is_after(
        created_at, comment.get("id"), requested_at, request_comment_id
    ):
        return None
    commit = str(comment.get("commit_id") or "").strip()
    if commit and commit != head_sha:
        return None
    return created_at


# Provider boilerplate is outside the result. Only the boilerplate block is
# dropped so text around it still counts against a clean result.
_PROVIDER_FOOTER_RE = re.compile(
    r"<details>\s*<summary>\s*ℹ️ About Codex in GitHub\s*</summary>"
    r".*?(?:</details>|\Z)",
    re.IGNORECASE | re.DOTALL,
)
_REVIEWED_COMMIT_RE = re.compile(r"Reviewed commit:\s*`?([^`]*)`?", re.IGNORECASE)
_MIN_ABBREVIATED_SHA_LENGTH = 7


def _names_head_commit(commit: str, head_sha: str) -> bool:
    """Return True when *commit*, possibly abbreviated, names *head_sha*."""

    commit = commit.strip().lower()
    return len(commit) >= _MIN_ABBREVIATED_SHA_LENGTH and (
        head_sha.strip().lower().startswith(commit)
    )


def _is_clean_review_body(
    provider: AutomatedReviewProvider, body: str, *, head_sha: str
) -> bool:
    """Read a clean result while preserving quoted Markdown and named commits."""
    reviewed_commits = []
    lines = []
    body = _PROVIDER_FOOTER_RE.sub("", body)
    for line in body.splitlines():
        # Markdown code indentation quotes the result or its reviewed commit;
        # stripping it would turn embedded text into completion evidence.
        if line.strip() and line.expandtabs(4).startswith("    "):
            return False
        line = re.sub(r"^#{1,6}\s+", "", line.strip()).replace("**", "")
        line = " ".join(line.split())
        reviewed_commit = _REVIEWED_COMMIT_RE.fullmatch(line)
        if reviewed_commit:
            reviewed_commits.append(reviewed_commit.group(1))
        elif line:
            lines.append(line)
    if not all(_names_head_commit(commit, head_sha) for commit in reviewed_commits):
        return False
    if not lines or not lines[0].startswith(provider.clean_review_result):
        return False
    flair = lines[0][len(provider.clean_review_result) :]
    findings = "\n".join(lines[1:])
    # A clean response carrying only P2/medium-or-below findings is still a
    # clean review: there is nothing major left, so the Fix and Review Loop ends
    # instead of requesting another review. P0/critical or P1/high findings,
    # including any hidden in the flair, keep the response non-clean.
    if has_high_severity_finding(flair):
        return False
    return not findings or is_low_severity_only_finding(findings)


def _failure_class(provider: AutomatedReviewProvider, body: str) -> str:
    # Provider failure notices lead with the failure. Reading only the opening
    # line keeps a status table or task summary that merely mentions limits
    # from being mistaken for a refused request.
    opening = next((line for line in body.splitlines() if line.strip()), "")
    if opening.startswith(("    ", "\t")):
        return ""
    opening = opening.strip().lower()
    for failure_class, openings in provider.failure_reply_prefixes:
        if opening.startswith(openings):
            return failure_class
    return ""


def classify_review_reply(
    provider: AutomatedReviewProvider,
    comment: Mapping[str, Any],
    *,
    requested_at: datetime | None,
    head_sha: str,
    request_comment_id: object = None,
) -> ReviewReply | None:
    """Classify one provider comment as a clean result or a refused request.

    Only the provider identity's comments after the request on its unchanged
    head can answer it. Other provider comments (status tables, task replies,
    findings) return ``None``. Callers own verifying the current head.
    """

    created_at = _request_reply_time(
        provider,
        comment,
        requested_at=requested_at,
        head_sha=head_sha,
        request_comment_id=request_comment_id,
    )
    if created_at is None:
        return None
    raw_body = str(comment.get("body") or "")
    opening = next((line for line in raw_body.splitlines() if line.strip()), "")
    if opening.startswith(("    ", "\t")):
        return None
    if _is_clean_review_body(provider, raw_body, head_sha=head_sha):
        return ReviewReply(comment=comment, created_at=created_at)
    failure_class = _failure_class(provider, str(comment.get("body") or ""))
    if failure_class:
        return ReviewReply(
            comment=comment, created_at=created_at, failure_class=failure_class
        )
    return None


def latest_review_reply(
    provider: AutomatedReviewProvider,
    comments: Iterable[Any],
    *,
    requested_at: datetime | None,
    head_sha: str,
    request_comment_id: object = None,
) -> ReviewReply | None:
    """Return the latest authoritative reply to a request on its unchanged head.

    A later clean result supersedes an earlier refusal and vice versa, so the
    request is judged by the provider's most recent answer.
    """

    latest: ReviewReply | None = None
    for comment in comments:
        if not isinstance(comment, Mapping):
            continue
        reply = classify_review_reply(
            provider,
            comment,
            requested_at=requested_at,
            head_sha=head_sha,
            request_comment_id=request_comment_id,
        )
        if reply is not None and (
            latest is None
            or review_comment_is_after(
                reply.created_at,
                reply.comment.get("id"),
                latest.created_at,
                latest.comment.get("id"),
            )
        ):
            latest = reply
    return latest


@dataclass(frozen=True, slots=True)
class AutomaticReviewSummary:
    """A provider-authored completion table bound to one GitHub PR."""

    comment: Mapping[str, Any]
    repository: str
    pr_number: int
    commit_ref: str
    completed_at: datetime
    updated_at: datetime


_CODEX_SUMMARY_MARKER = "<!-- codex-pull-request-review-summary -->"
_CODEX_COMPLETED_STATUS_RE = re.compile(
    r'✅\s*\*\*Completed\*\*\s*<relative-time datetime="([^"]+)">'
    r"([^<]+)</relative-time>"
)


def _provider_bot(provider: AutomatedReviewProvider, user: object) -> bool:
    login = user.get("login") if isinstance(user, Mapping) else user
    if isinstance(user, Mapping) and user.get("type") not in {None, "Bot"}:
        return False
    return (
        str(login or "").lower().endswith("[bot]")
        and normalize_reviewer_login(login) in provider.reviewer_logins
    )


def _pr_issue_comment(
    comment: Mapping[str, Any], repository: str, pr_number: int
) -> bool:
    identifier = _comment_id(comment.get("id"))
    target = urlparse(str(comment.get("url") or comment.get("html_url") or ""))
    return bool(
        identifier is not None
        and comment.get("type", "issue_comment") == "issue_comment"
        and target.scheme == "https"
        and target.netloc == "github.com"
        and not target.query
        and target.path.lower() == f"/{repository}/pull/{pr_number}".lower()
        and target.fragment == f"issuecomment-{identifier}"
    )


def _timestamp(value: object) -> datetime | None:
    return _comment_time({"created_at": value})


def latest_automatic_review_summary(
    provider: AutomatedReviewProvider,
    comments: Iterable[Any],
    *,
    repository: str,
    pr_number: int,
) -> AutomaticReviewSummary | None:
    """Read Codex's actual completion format, never quoted or human text.

    Select the latest summary *before* testing completion, so a newer start,
    failure, malformed table, or changed commit cannot revive an older result.
    A completion table alone is not a clean result; the caller must resolve its
    commit and qualify a later provider no-findings reaction on this PR.
    """
    if provider.provider != "codex" or not repository or not pr_number:
        return None
    candidates = []
    for comment in comments:
        if not isinstance(comment, Mapping) or not _provider_bot(
            provider, comment.get("user")
        ):
            continue
        if not _pr_issue_comment(comment, repository, pr_number):
            continue
        opening = next(
            (
                line
                for line in str(comment.get("body") or "").splitlines()
                if line.strip()
            ),
            "",
        )
        if opening != _CODEX_SUMMARY_MARKER:
            continue
        created = _comment_time(comment)
        updated = _timestamp(comment.get("updated_at"))
        if created is None or updated is None or updated < created:
            return None  # Unknown chronology cannot supersede a known result.
        candidates.append((updated, _comment_id(comment.get("id")), comment))
    if not candidates:
        return None
    updated, _, comment = max(candidates, key=lambda item: item[:2])
    body = str(comment.get("body") or "")
    if has_explicit_finding_severity(body):
        return None
    lines = [
        line.strip()
        for line in _PROVIDER_FOOTER_RE.sub("", body).splitlines()
        if line.strip()
    ]
    expected = [
        _CODEX_SUMMARY_MARKER,
        "## Codex Review Summary",
        "This comment shows the latest Codex review activity on this pull request.",
        "| Review | Status | Commit | Review trigger |",
    ]
    if len(lines) != 6 or lines[:4] != expected:
        return None
    cells = [cell.strip() for cell in lines[4].strip("|").split("|")]
    if len(cells) != 4 or not all(re.fullmatch(r":?-+:?", cell) for cell in cells):
        return None
    cells = [cell.strip() for cell in lines[5].strip("|").split("|")]
    if len(cells) != 4 or cells[0] != "📝 **Code Review**" or not cells[3]:
        return None
    status = _CODEX_COMPLETED_STATUS_RE.fullmatch(cells[1])
    commit = re.fullmatch(r"`([0-9a-fA-F]{7,40})`", cells[2])
    if status is None or commit is None:
        return None
    completed = _timestamp(status.group(1))
    if completed is None or _timestamp(status.group(2)) != completed:
        return None
    # REST updated_at has second precision; the rendered completion can have
    # microseconds within that same server timestamp bucket.
    second_precision = "." not in str(comment.get("updated_at"))
    after_update = (
        completed >= updated + timedelta(seconds=1)
        if second_precision
        else completed > updated
    )
    if completed < _comment_time(comment) or after_update:
        return None
    return AutomaticReviewSummary(
        comment, repository, pr_number, commit.group(1).lower(), completed, updated
    )


def automatic_review_reply(
    provider: AutomatedReviewProvider,
    comments: Iterable[Any],
    *,
    summary: AutomaticReviewSummary,
    head_sha: str,
    head_committed_at: datetime,
) -> tuple[ReviewReply | None, Mapping[str, Any] | None]:
    """Preserve authoritative replies and flag newer unclassified feedback.

    Marked findings remain owned by the independent comment inventory. A new
    provider message without a qualified outcome cannot reuse an older clean
    signal, including when an old comment was edited after completion.
    """
    latest = None
    unknown = None
    unknown_at = None
    undated = None
    lower_bound = max(_comment_time(summary.comment), head_committed_at)
    for comment in comments:
        if not isinstance(comment, Mapping) or not _provider_bot(
            provider, comment.get("user")
        ):
            continue
        if not _pr_issue_comment(comment, summary.repository, summary.pr_number):
            continue
        if comment.get("id") == summary.comment.get("id"):
            continue
        commit = str(comment.get("commit_id") or "").strip()
        if commit and commit != head_sha:
            continue
        created = _comment_time(comment)
        updated = _timestamp(comment.get("updated_at")) or created
        if created is None or updated < created:
            undated = comment
            continue
        effective = {**comment, "created_at": updated.isoformat()}
        reply = classify_review_reply(
            provider,
            effective,
            requested_at=lower_bound,
            head_sha=head_sha,
            request_comment_id=summary.comment.get("id"),
        )
        if reply is not None and (
            latest is None
            or review_comment_is_after(
                reply.created_at,
                comment.get("id"),
                latest.created_at,
                latest.comment.get("id"),
            )
        ):
            latest = ReviewReply(comment, reply.created_at, reply.failure_class)
        body = str(comment.get("body") or "")
        opening = next((line for line in body.splitlines() if line.strip()), "")
        if (
            reply is None
            and opening != _CODEX_SUMMARY_MARKER
            and not has_explicit_finding_severity(body)
            and updated > summary.completed_at
            and (
                unknown is None
                or review_comment_is_after(
                    updated, comment.get("id"), unknown_at, unknown.get("id")
                )
            )
        ):
            unknown, unknown_at = comment, updated
    if (
        unknown is not None
        and latest is not None
        and not latest.failure_class
        and latest.created_at > unknown_at
    ):
        unknown = None
    return latest, undated if undated is not None else unknown


def automatic_review_reaction(
    provider: AutomatedReviewProvider,
    reactions: Iterable[Any],
    *,
    summary: AutomaticReviewSummary,
    not_before: datetime,
) -> Mapping[str, Any] | None:
    """Require a later PR-scoped no-findings reaction, never stale thumbs-up."""
    candidates = []
    starts = []
    lower_bound = max(summary.completed_at, summary.updated_at, not_before)
    for reaction in reactions:
        if not isinstance(reaction, Mapping) or not _provider_bot(
            provider, reaction.get("user")
        ):
            continue
        content = str(reaction.get("content") or "")
        if content not in {*provider.clean_review_reactions, "eyes"}:
            continue
        created = _timestamp(reaction.get("created_at"))
        if created is None or _comment_id(reaction.get("id")) is None:
            return None
        if content == "eyes":
            starts.append(created)
        elif created > lower_bound:
            candidates.append((created, reaction))
    if not candidates:
        return None
    created, reaction = max(candidates, key=lambda item: item[0])
    if any(start >= created for start in starts):
        return None
    return reaction
