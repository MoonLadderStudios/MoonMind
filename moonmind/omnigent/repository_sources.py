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


def anonymous_https_repository_source(value: str) -> tuple[str, str] | None:
    """A credential-free, unambiguous HTTPS remote and its exact origin.

    This classifies a transport only; callers must separately prove an admitted
    anonymous read grant. Local paths, userinfo and URL options never qualify.
    Keep path case and suffix unchanged for non-GitHub hosting services.
    """
    try:
        if not isinstance(value, str) or any(
            ord(char) <= 32 or ord(char) == 127 for char in value
        ):
            return None
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or not re.fullmatch(r"[A-Za-z0-9.-]+(?::[0-9]+)?", parsed.netloc)
            or parsed.netloc != parsed.netloc.lower()
            or not re.fullmatch(r"/[A-Za-z0-9_.~/-]+", parsed.path)
            or any(part in {"", ".", ".."} for part in parsed.path[1:].split("/"))
        ):
            return None
        # Access also rejects out-of-range ports; zero is not a remote service.
        if parsed.port == 0:
            return None
        endpoint = "https://" + parsed.netloc
        return value, endpoint
    except (ValueError, TypeError):
        return None


__all__ = [
    "RepositorySourceError",
    "anonymous_https_repository_source",
    "normalize_repository_source",
]
