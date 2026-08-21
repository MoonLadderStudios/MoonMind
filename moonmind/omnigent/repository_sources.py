"""Pure repository-source classification shared by intent and host adapters."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlsplit


_GITHUB_SLUG = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class RepositorySourceError(ValueError):
    """The authored source cannot be classified without runtime authority."""


def normalize_repository_source(repository_source: str) -> tuple[str, str]:
    """Return the canonical clone source and its provider-neutral kind."""

    value = str(repository_source or "").strip()
    if not value:
        raise RepositorySourceError(
            "repository source is required to materialize the workspace"
        )
    if value.startswith("file://"):
        return value, "local"
    if value.startswith(("http://", "https://", "git@", "ssh://")):
        kind = "remote"
        if value.startswith(("http://", "https://")):
            # Exact hostname matching prevents credentials from being offered
            # to lookalike origins such as github.com.evil.example.
            host = (urlsplit(value).hostname or "").lower()
            if host == "github.com":
                kind = "github_https"
        return value, kind
    if value.startswith(("/", "./", "../")) or Path(value).is_absolute():
        return value, "local"
    if _GITHUB_SLUG.fullmatch(value):
        suffix = "" if value.endswith(".git") else ".git"
        return f"https://github.com/{value}{suffix}", "github_https"
    raise RepositorySourceError(
        "unsupported repository source; expected owner/repo, URL, or path"
    )


__all__ = ["RepositorySourceError", "normalize_repository_source"]
