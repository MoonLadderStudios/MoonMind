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
from datetime import datetime
from types import MappingProxyType
from typing import Any

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
    clean_review_comments: tuple[str, ...] = ()
    # ``(failure_class, openings)`` pairs recognizing the provider's notice that
    # it refused or could not perform a requested review.
    failure_reply_prefixes: tuple[tuple[str, tuple[str, ...]], ...] = ()


AUTOMATED_REVIEW_PROVIDERS = MappingProxyType(
    {
        "codex": AutomatedReviewProvider(
            provider="codex",
            command="@codex review",
            reviewer_logins=("chatgpt-codex-connector",),
            clean_review_comments=("Codex Review: Didn't find any major issues. 🚀",),
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


def latest_review_request(
    provider: AutomatedReviewProvider,
    comments: Iterable[Any],
    *,
    head_sha: str,
    not_before: datetime | None = None,
) -> ReviewRequest | None:
    """Select requests once for both the portable snapshot and GitHub gate.

    Hosts supply a head timestamp or the active request as the lower bound.
    REST issue-comment collections omit ``type``; portable inventories must
    explicitly identify any non-issue records so they cannot become requests.
    """

    latest = None
    if not head_sha:
        return None
    for comment in comments:
        if not is_review_request_comment(provider, comment):
            continue
        created_at = _comment_time(comment)
        if created_at is None or (not_before is not None and created_at < not_before):
            continue
        commit = str(comment.get("commit_id") or "").strip()
        if commit and commit != head_sha:
            continue
        if latest is None or review_comment_is_after(
            created_at, comment.get("id"), latest.created_at, latest.comment.get("id")
        ):
            latest = ReviewRequest(comment=comment, created_at=created_at)
    return latest


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


def _is_clean_review_body(provider: AutomatedReviewProvider, body: str) -> bool:
    """Recognize the provider's exact clean result.

    A bare phrase inside arbitrary prose or a quoted response is not
    completion evidence, and a marked finding of any priority keeps the
    response non-clean.
    """
    if has_explicit_finding_severity(body):
        return False
    # Provider boilerplate is outside the result. Keep any other text so a
    # mixed clean/findings response cannot be mistaken for a clean result.
    body = re.sub(
        r"<details>\s*<summary>\s*ℹ️ About Codex in GitHub\s*</summary>.*?</details>\s*$",
        "",
        body,
        count=1,
        flags=re.IGNORECASE | re.DOTALL,
    ).strip()
    body = re.sub(r"^#{1,6}\s+", "", body).replace("**", "")
    body = " ".join(body.split())
    return body in provider.clean_review_comments


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
    body = raw_body.strip()
    if _is_clean_review_body(provider, body):
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
