"""Code-host ownership for the portable resolver bundle.

The ``pr-resolver`` Skill is GitHub-only (``required-capabilities: git+gh``,
``supportedHosts: cli``, ``nativeHostEligible: false``). Until GitLab resolver
support is implemented behind this same collection/transport boundary, GitLab
input must be rejected here — before any paid execution (``gh`` calls, agent
turns, or mutations) — while GitHub selectors pass through unchanged.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

_GITLAB_URL_MARKERS = ("/merge_requests/", "/-/merge_requests/")
_GITLAB_PREFIX_RE = re.compile(r"^gitlab\s*:", re.IGNORECASE)
_GITLAB_HOST_RE = re.compile(r"(^|\.)gitlab\.", re.IGNORECASE)


class UnsupportedCodeHostError(ValueError):
    """A non-GitHub selector reached the GitHub-only resolver."""

    def __init__(self, selector: str) -> None:
        self.selector = selector
        super().__init__(
            "gitlab_not_supported_by_resolver: the pr-resolver Skill is "
            "GitHub-only; GitLab merge-request input is rejected before "
            "execution until resolver support is implemented."
        )


def detect_code_host(selector: object) -> str:
    """Return ``gitlab`` for GitLab-shaped selectors, else ``github``."""

    raw = str(selector or "").strip()
    if not raw:
        return "github"
    if _GITLAB_PREFIX_RE.match(raw):
        return "gitlab"
    if "!" in raw and ("/merge_requests" in raw or "gitlab" in raw.lower()):
        return "gitlab"
    for marker in _GITLAB_URL_MARKERS:
        if marker in raw:
            return "gitlab"
    try:
        host = (urlparse(raw).hostname or "").lower()
    except ValueError:
        return "github"
    if host and _GITLAB_HOST_RE.search(host):
        return "gitlab"
    return "github"


def ensure_github_only_selector(selector: object) -> str | None:
    """Reject GitLab input for the GitHub-only resolver before paid execution."""

    if selector is None:
        return None
    raw = str(selector).strip()
    if not raw:
        return raw
    if detect_code_host(raw) != "github":
        try:
            from moonmind.integrations.gitlab.errors import GitLabToolError
        except Exception:
            raise UnsupportedCodeHostError(raw) from None
        raise GitLabToolError(
            "The pr-resolver Skill is GitHub-only; GitLab merge-request "
            "input is rejected before execution until resolver support is "
            "implemented.",
            code="gitlab_resolver_unsupported",
            status_code=501,
            action="resolve_pr",
        ) from None
    return raw
