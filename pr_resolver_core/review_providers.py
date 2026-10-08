"""Automated review provider identities shared by every resolver host.

This table is the single canonical mapping from a provider-neutral review
provider name to the exact request command and the reviewer identities whose
results count as that provider's answer.  The portable Skill uses it to decide
whether a fresh review exists for the current head; MoonMind uses it so a child
run can only ever ask for a *configured* provider and never for arbitrary
comment text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType


@dataclass(frozen=True, slots=True)
class AutomatedReviewProvider:
    """One automated reviewer and the exact way it is requested."""

    provider: str
    command: str
    reviewer_logins: tuple[str, ...]
    clean_review_reactions: tuple[str, ...] = ("+1",)
    clean_review_comments: tuple[str, ...] = ()


AUTOMATED_REVIEW_PROVIDERS = MappingProxyType(
    {
        "codex": AutomatedReviewProvider(
            provider="codex",
            command="@codex review",
            reviewer_logins=("chatgpt-codex-connector",),
            clean_review_comments=("Codex Review: Didn't find any major issues. 🚀",),
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


def is_clean_review_comment(
    provider: AutomatedReviewProvider,
    comment: dict,
    *,
    requested_at: datetime | None,
    head_sha: str,
) -> bool:
    """Recognize a provider's clean response within an unchanged-head request.

    A bare phrase inside arbitrary prose, a quoted response, or an edited old
    comment is not completion evidence. Callers own verifying the current head.
    """
    user = comment.get("user")
    login = user.get("login") if isinstance(user, dict) else user
    if normalize_reviewer_login(login) not in provider.reviewer_logins:
        return False
    if requested_at is None or not head_sha:
        return False
    try:
        created_at = datetime.fromisoformat(
            str(comment.get("created_at") or "").replace("Z", "+00:00")
        )
    except ValueError:
        return False
    if created_at.tzinfo is None or created_at <= requested_at:
        return False
    commit = str(comment.get("commit_id") or "").strip()
    if commit and commit != head_sha:
        return False
    body = str(comment.get("body") or "").strip()
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
