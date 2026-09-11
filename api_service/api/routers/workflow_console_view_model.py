"""Shared view-model helpers for the workflow console UI."""

from __future__ import annotations

import os
import re
import time
import logging
from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Mapping
from urllib.parse import quote, urlparse
from uuid import UUID

import httpx
from sqlalchemy.ext.asyncio import AsyncSession
from moonmind.config.settings import WorkflowSettings, settings
from moonmind.omnigent.execution_profiles import public_execution_catalog
from moonmind.utils.build_info import resolve_moonmind_build_id
from moonmind.workflows.executions.runtime_defaults import (
    DEFAULT_REPOSITORY,
    normalize_runtime_id,
    resolve_runtime_defaults,
)
from moonmind.workflows.executions.runtime_target_selection import (
    AuthoringSurface,
    resolve_runtime_target_selection,
    runtime_target_catalog_payload,
)
from moonmind.workflows.executions.execution_contract import build_runtime_command_preview_config

logger = logging.getLogger(__name__)

_POLL_INTERVALS_MS = {
    "list": 5000,
    "detail": 2000,
    "events": 1000,
}

_SUPPORTED_WORKER_RUNTIMES = ("codex_cli", "claude_code", "jules", "universal")
_OWNER_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_SSH_GIT_RE = re.compile(
    r"^(?:ssh://)?git@[A-Za-z0-9.-]+[:/]"
    r"([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?$"
)
_GITHUB_REPOSITORY_DISCOVERY_URL = "https://api.github.com/user/repos"
_GITHUB_REPOSITORY_METADATA_URL_TEMPLATE = "https://api.github.com/repos/{repository}"
_GITHUB_BRANCH_DISCOVERY_URL_TEMPLATE = "https://api.github.com/repos/{repository}/branches"
_GITHUB_ISSUE_DISCOVERY_URL_TEMPLATE = "https://api.github.com/repos/{repository}/issues"
_GITHUB_REPOSITORY_DISCOVERY_TIMEOUT_SECONDS = 5.0
_GITHUB_REPOSITORY_DISCOVERY_CACHE_TTL_SECONDS = 60.0
_GITHUB_REPOSITORY_OPTIONS_CACHE: dict[
    str, tuple[float, tuple["RepositoryOption", ...], str | None]
] = {}
# Scoped caches for the text-first branch picker (MoonLadderStudios/MoonMind#4054).
# Search pages are keyed by token + repository + query + limit; exact-name
# observations are keyed by token + repository + case-sensitive branch name;
# metadata is keyed by token + repository. All three use bounded eviction so a
# cold lookup can never grow memory without bound.
_GITHUB_BRANCH_SEARCH_CACHE: dict[
    str, tuple[float, tuple["BranchOption", ...], str | None, str | None, bool]
] = {}
_GITHUB_BRANCH_METADATA_CACHE: dict[str, tuple[float, str | None]] = {}
_GITHUB_BRANCH_RESOLVE_CACHE: dict[
    str, tuple[float, bool, str | None, str | None]
] = {}
_BRANCH_SUGGESTION_DEFAULT_LIMIT = 20
_BRANCH_SUGGESTION_MAX_LIMIT = 50
_BRANCH_QUERY_MAX_LENGTH = 100
_BRANCH_NAME_MAX_LENGTH = 255
_BRANCH_CACHE_MAX_ENTRIES = 200
_BRANCH_GITHUB_SINGLE_PAGE = 1

_JIRA_CREATE_PAGE_SOURCES = {
    "connections": "/api/jira/connections/verify",
    "projects": "/api/jira/projects",
    "boards": "/api/jira/projects/{projectKey}/boards",
    "columns": "/api/jira/boards/{boardId}/columns",
    "issues": "/api/jira/boards/{boardId}/issues",
    "issue": "/api/jira/issues/{issueKey}",
}

def _validate_jira_source_templates(sources: Mapping[str, str]) -> None:
    invalid = [
        name
        for name, value in sources.items()
        for normalized in (value.strip(),)
        if (
            value != normalized
            or not normalized
            or not normalized.startswith("/api/")
            or "://" in normalized
        )
    ]
    if invalid:
        invalid_names = ", ".join(sorted(invalid))
        raise ValueError(
            "Jira Create-page sources must be MoonMind API path templates: "
            f"{invalid_names}"
        )

_validate_jira_source_templates(_JIRA_CREATE_PAGE_SOURCES)

@dataclass(frozen=True, slots=True)
class RepositoryOption:
    """Browser-safe repository suggestion for the Create page."""

    value: str
    label: str
    source: str

    def to_payload(self) -> dict[str, str]:
        return {
            "value": self.value,
            "label": self.label,
            "source": self.source,
        }

@dataclass(frozen=True, slots=True)
class BranchOption:
    """Browser-safe branch suggestion for the Create page."""

    value: str
    label: str
    source: str

    def to_payload(self) -> dict[str, str]:
        return {
            "value": self.value,
            "label": self.label,
            "source": self.source,
        }

def _build_jira_sources() -> dict[str, str]:
    """Return MoonMind-owned Jira browser endpoint templates."""

    return dict(_JIRA_CREATE_PAGE_SOURCES)

def _normalize_repository_value(value: object) -> str | None:
    """Return a browser-safe owner/repo value, or ``None`` when invalid."""

    raw = str(value or "").strip()
    if not raw:
        return None
    if _OWNER_REPO_RE.fullmatch(raw):
        return raw

    ssh_match = _SSH_GIT_RE.fullmatch(raw)
    if ssh_match:
        owner, repo = ssh_match.groups()
        normalized = f"{owner}/{repo}"
        return normalized if _OWNER_REPO_RE.fullmatch(normalized) else None

    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        return None

    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) != 2:
        return None
    owner, repo = parts
    if repo.endswith(".git"):
        repo = repo[:-4]
    normalized = f"{owner}/{repo}"
    return normalized if _OWNER_REPO_RE.fullmatch(normalized) else None

def _append_repository_option(
    options: list[RepositoryOption],
    seen: set[str],
    value: object,
    *,
    source: str,
) -> None:
    normalized = _normalize_repository_value(value)
    if not normalized:
        return
    key = normalized.lower()
    if key in seen:
        return
    seen.add(key)
    options.append(
        RepositoryOption(value=normalized, label=normalized, source=source)
    )

def _fetch_github_repository_options(
    token: str,
) -> tuple[list[RepositoryOption], str | None]:
    """Fetch credential-visible GitHub repositories without exposing secrets."""

    if not token:
        return [], None
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    try:
        with httpx.Client(
            timeout=_GITHUB_REPOSITORY_DISCOVERY_TIMEOUT_SECONDS
        ) as client:
            response = client.get(
                _GITHUB_REPOSITORY_DISCOVERY_URL,
                headers=headers,
                params={
                    "per_page": 100,
                    "sort": "updated",
                    "affiliation": "owner,collaborator,organization_member",
                },
            )
            response.raise_for_status()
            data = response.json()
    except (httpx.HTTPError, ValueError):
        return [], "GitHub repository discovery is unavailable."

    options: list[RepositoryOption] = []
    seen: set[str] = set()
    if isinstance(data, list):
        for item in data:
            if isinstance(item, Mapping):
                _append_repository_option(
                    options,
                    seen,
                    item.get("full_name"),
                    source="github",
                )
    return options, None

def _clamp_branch_suggestion_limit(limit: object) -> int:
    """Clamp a caller-supplied suggestion limit to the bounded range."""

    try:
        parsed = int(str(limit or "").strip() or _BRANCH_SUGGESTION_DEFAULT_LIMIT)
    except (TypeError, ValueError):
        return _BRANCH_SUGGESTION_DEFAULT_LIMIT
    return max(1, min(_BRANCH_SUGGESTION_MAX_LIMIT, parsed))


def _normalize_branch_query(query: object) -> str:
    """Return a bounded search query for local filtering of one branch page."""

    return str(query or "").strip()[:_BRANCH_QUERY_MAX_LENGTH]


def _is_valid_branch_name(value: str) -> bool:
    """Return whether a branch name is worth an exact upstream lookup."""

    candidate = value.strip()
    if not candidate or len(candidate) > _BRANCH_NAME_MAX_LENGTH:
        return False
    if candidate.startswith("/") or candidate.endswith("/"):
        return False
    if "//" in candidate or ".." in candidate or "@{" in candidate:
        return False
    if candidate.endswith(".lock") or candidate.endswith("."):
        return False
    for disallowed in ("\\", "^", ":", "?", "*", "[", "~"):
        if disallowed in candidate:
            return False
    return not any(ord(char) < 32 or ord(char) == 127 for char in candidate)


def _bounded_cache_put(cache: dict, key: str, value: object) -> None:
    """Store a cache entry with oldest-first eviction once the bound is hit."""

    if key in cache:
        cache[key] = value  # type: ignore[assignment]
        return
    while len(cache) >= _BRANCH_CACHE_MAX_ENTRIES:
        cache.pop(next(iter(cache)))
    cache[key] = value  # type: ignore[assignment]


def _github_branch_headers(token: str) -> dict[str, str]:
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _append_branch_option(
    options: list[BranchOption],
    seen: set[str],
    value: object,
) -> None:
    branch_name = str(value or "").strip()
    if not branch_name or branch_name in seen:
        return
    seen.add(branch_name)
    options.append(
        BranchOption(value=branch_name, label=branch_name, source="github")
    )


def _fetch_repository_default_branch(
    client: Any,
    headers: dict[str, str],
    normalized_repository: str,
) -> str | None:
    """Fetch only the small repository metadata projection (default branch)."""

    try:
        metadata_response = client.get(
            _GITHUB_REPOSITORY_METADATA_URL_TEMPLATE.format(
                repository=normalized_repository
            ),
            headers=headers,
        )
        metadata_response.raise_for_status()
        metadata = metadata_response.json()
    except (httpx.HTTPError, ValueError):
        return None
    if isinstance(metadata, dict):
        return str(metadata.get("default_branch") or "").strip() or None
    return None


def _filter_branch_options(
    options: list[BranchOption], query: str
) -> list[BranchOption]:
    """Filter one bounded page locally; never paginates to find more matches."""

    normalized_query = query.strip().lower()
    if not normalized_query:
        return list(options)
    return [
        option
        for option in options
        if normalized_query in option.value.lower()
    ]


def _fetch_github_branch_options(
    token: str,
    repository: str,
    query: str = "",
    limit: int = _BRANCH_SUGGESTION_DEFAULT_LIMIT,
) -> tuple[list[BranchOption], str | None, str | None, bool]:
    """Fetch at most one bounded branch page plus independent default metadata.

    Form initialization must resolve default-branch metadata even when the
    suggestion page is slow or fails, so metadata is fetched first and is
    preserved when the later branch-page request raises. Only a single GitHub
    branch page is ever read per call; ``has_more`` reports whether GitHub
    advertises further pages instead of draining them.
    """

    normalized_repository = _normalize_repository_value(repository)
    if not token or not normalized_repository:
        return [], None, None, False
    normalized_query = _normalize_branch_query(query)
    clamped_limit = _clamp_branch_suggestion_limit(limit)
    headers = _github_branch_headers(token)
    next_url: str | None = _GITHUB_BRANCH_DISCOVERY_URL_TEMPLATE.format(
        repository=normalized_repository
    )
    params: dict[str, int] | None = {"per_page": clamped_limit}
    try:
        with httpx.Client(
            timeout=_GITHUB_REPOSITORY_DISCOVERY_TIMEOUT_SECONDS
        ) as client:
            default_branch = _fetch_repository_default_branch(
                client, headers, normalized_repository
            )
            try:
                page_options: list[BranchOption] = []
                page_seen: set[str] = set()
                pages_read = 0
                has_more = False
                while next_url and pages_read < _BRANCH_GITHUB_SINGLE_PAGE:
                    response = client.get(
                        next_url,
                        headers=headers,
                        params=params,
                    )
                    params = None
                    response.raise_for_status()
                    data = response.json()
                    if isinstance(data, list):
                        for item in data:
                            if not isinstance(item, Mapping):
                                continue
                            _append_branch_option(
                                page_options,
                                page_seen,
                                item.get("name"),
                            )
                    has_more = bool(response.links.get("next", {}).get("url"))
                    next_url = None
                    pages_read += 1
            except (httpx.HTTPError, ValueError):
                # A slow/failed suggestion request must not withhold or discard
                # independently successful default-branch metadata.
                return [], "GitHub branch lookup is unavailable.", default_branch, False
    except (httpx.HTTPError, ValueError):
        return [], "GitHub branch lookup is unavailable.", None, False

    return (
        _filter_branch_options(page_options, normalized_query),
        None,
        default_branch,
        has_more,
    )


async def _fetch_github_branch_options_async(
    token: str,
    repository: str,
    query: str = "",
    limit: int = _BRANCH_SUGGESTION_DEFAULT_LIMIT,
) -> tuple[list[BranchOption], str | None, str | None, bool]:
    """Awaited async variant so async routes never block the event loop."""

    normalized_repository = _normalize_repository_value(repository)
    if not token or not normalized_repository:
        return [], None, None, False
    normalized_query = _normalize_branch_query(query)
    clamped_limit = _clamp_branch_suggestion_limit(limit)
    headers = _github_branch_headers(token)
    try:
        async with httpx.AsyncClient(
            timeout=_GITHUB_REPOSITORY_DISCOVERY_TIMEOUT_SECONDS
        ) as client:
            try:
                metadata_response = await client.get(
                    _GITHUB_REPOSITORY_METADATA_URL_TEMPLATE.format(
                        repository=normalized_repository
                    ),
                    headers=headers,
                )
                metadata_response.raise_for_status()
                metadata = metadata_response.json()
                default_branch = (
                    str(metadata.get("default_branch") or "").strip() or None
                    if isinstance(metadata, dict)
                    else None
                )
            except (httpx.HTTPError, ValueError):
                default_branch = None
            try:
                response = await client.get(
                    _GITHUB_BRANCH_DISCOVERY_URL_TEMPLATE.format(
                        repository=normalized_repository
                    ),
                    headers=headers,
                    params={"per_page": clamped_limit},
                )
                response.raise_for_status()
                data = response.json()
                page_options: list[BranchOption] = []
                page_seen: set[str] = set()
                if isinstance(data, list):
                    for item in data:
                        if not isinstance(item, Mapping):
                            continue
                        _append_branch_option(
                            page_options, page_seen, item.get("name")
                        )
                has_more = bool(response.links.get("next", {}).get("url"))
            except (httpx.HTTPError, ValueError):
                return [], "GitHub branch lookup is unavailable.", default_branch, False
    except (httpx.HTTPError, ValueError):
        return [], "GitHub branch lookup is unavailable.", None, False

    return (
        _filter_branch_options(page_options, normalized_query),
        None,
        default_branch,
        has_more,
    )


def _fetch_github_branch_exact(
    token: str,
    repository: str,
    branch: str,
) -> tuple[bool, str | None, str | None, bool]:
    """Resolve one exact branch name without scanning suggestion pages.

    Returns ``(found, resolved_name, error, inconclusive)``. A 404 for the
    branch is only reported as definitively absent when repository metadata is
    independently reachable; otherwise the outcome is inconclusive so callers
    never turn an unknown repository/access state into a false diagnosis.
    """

    normalized_repository = _normalize_repository_value(repository)
    candidate = str(branch or "").strip()
    if not token or not normalized_repository or not _is_valid_branch_name(candidate):
        return False, None, None, True
    headers = _github_branch_headers(token)
    encoded_branch = quote(candidate, safe="/")
    branch_url = (
        f"https://api.github.com/repos/{normalized_repository}"
        f"/branches/{encoded_branch}"
    )
    try:
        with httpx.Client(
            timeout=_GITHUB_REPOSITORY_DISCOVERY_TIMEOUT_SECONDS
        ) as client:
            try:
                response = client.get(branch_url, headers=headers)
                if response.status_code == 404:
                    metadata = _fetch_repository_default_branch(
                        client, headers, normalized_repository
                    )
                    if metadata is None:
                        # Confirm the repository itself is reachable before
                        # calling a branch definitively absent.
                        try:
                            probe = client.get(
                                _GITHUB_REPOSITORY_METADATA_URL_TEMPLATE.format(
                                    repository=normalized_repository
                                ),
                                headers=headers,
                            )
                            probe.raise_for_status()
                        except (httpx.HTTPError, ValueError):
                            return False, None, (
                                "GitHub branch lookup is unavailable."
                            ), True
                    return False, None, None, False
                response.raise_for_status()
                payload = response.json()
                returned = (
                    str(payload.get("name") or "").strip()
                    if isinstance(payload, Mapping)
                    else ""
                )
                if returned and returned == candidate:
                    return True, returned, None, False
                return False, None, None, True
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code if exc.response is not None else None
                if status in (403, 429):
                    return False, None, (
                        "GitHub branch lookup is unavailable."
                    ), True
                raise
    except (httpx.HTTPError, ValueError):
        return False, None, "GitHub branch lookup is unavailable.", True


async def _fetch_github_branch_exact_async(
    token: str,
    repository: str,
    branch: str,
) -> tuple[bool, str | None, str | None, bool]:
    """Awaited async variant of the exact-name branch lookup."""

    normalized_repository = _normalize_repository_value(repository)
    candidate = str(branch or "").strip()
    if not token or not normalized_repository or not _is_valid_branch_name(candidate):
        return False, None, None, True
    headers = _github_branch_headers(token)
    encoded_branch = quote(candidate, safe="/")
    branch_url = (
        f"https://api.github.com/repos/{normalized_repository}"
        f"/branches/{encoded_branch}"
    )
    try:
        async with httpx.AsyncClient(
            timeout=_GITHUB_REPOSITORY_DISCOVERY_TIMEOUT_SECONDS
        ) as client:
            try:
                response = await client.get(branch_url, headers=headers)
                if response.status_code == 404:
                    try:
                        probe = await client.get(
                            _GITHUB_REPOSITORY_METADATA_URL_TEMPLATE.format(
                                repository=normalized_repository
                            ),
                            headers=headers,
                        )
                        probe.raise_for_status()
                    except (httpx.HTTPError, ValueError):
                        return False, None, (
                            "GitHub branch lookup is unavailable."
                        ), True
                    return False, None, None, False
                response.raise_for_status()
                payload = response.json()
                returned = (
                    str(payload.get("name") or "").strip()
                    if isinstance(payload, Mapping)
                    else ""
                )
                if returned and returned == candidate:
                    return True, returned, None, False
                return False, None, None, True
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code if exc.response is not None else None
                if status in (403, 429):
                    return False, None, (
                        "GitHub branch lookup is unavailable."
                    ), True
                raise
    except (httpx.HTTPError, ValueError):
        return False, None, "GitHub branch lookup is unavailable.", True

def _github_repository_options_cache_key(token: str) -> str:
    return sha256(token.encode("utf-8")).hexdigest()


def _github_branch_search_cache_key(
    token: str, repository: str, query: str, limit: int
) -> str:
    normalized_repository = _normalize_repository_value(repository) or ""
    raw_key = (
        f"{token}:{normalized_repository.lower()}:{query}:{int(limit)}"
    )
    return sha256(raw_key.encode("utf-8")).hexdigest()


def _github_branch_metadata_cache_key(token: str, repository: str) -> str:
    normalized_repository = _normalize_repository_value(repository) or ""
    raw_key = f"metadata:{token}:{normalized_repository.lower()}"
    return sha256(raw_key.encode("utf-8")).hexdigest()


def _github_branch_resolve_cache_key(
    token: str, repository: str, branch: str
) -> str:
    normalized_repository = _normalize_repository_value(repository) or ""
    # Branch names preserve case: the cache key must not lowercase them.
    raw_key = f"resolve:{token}:{normalized_repository.lower()}:{branch}"
    return sha256(raw_key.encode("utf-8")).hexdigest()

def _get_cached_github_repository_options(
    token: str,
) -> tuple[list[RepositoryOption], str | None]:
    now = time.monotonic()
    cache_key = _github_repository_options_cache_key(token)
    cached = _GITHUB_REPOSITORY_OPTIONS_CACHE.get(cache_key)
    if cached:
        cached_at, cached_options, cached_error = cached
        if now - cached_at < _GITHUB_REPOSITORY_DISCOVERY_CACHE_TTL_SECONDS:
            return list(cached_options), cached_error

    options, error = _fetch_github_repository_options(token)
    _GITHUB_REPOSITORY_OPTIONS_CACHE[cache_key] = (now, tuple(options), error)
    return options, error

def _get_cached_github_branch_options(
    token: str,
    repository: str,
    query: str = "",
    limit: int = _BRANCH_SUGGESTION_DEFAULT_LIMIT,
) -> tuple[list[BranchOption], str | None, str | None, bool]:
    """Return one bounded cached search page with oldest-first eviction."""

    clamped_limit = _clamp_branch_suggestion_limit(limit)
    normalized_query = _normalize_branch_query(query)
    now = time.monotonic()
    cache_key = _github_branch_search_cache_key(
        token, repository, normalized_query, clamped_limit
    )
    cached = _GITHUB_BRANCH_SEARCH_CACHE.get(cache_key)
    if cached:
        cached_at, cached_options, cached_error, cached_default_branch, cached_has_more = cached
        if now - cached_at < _GITHUB_REPOSITORY_DISCOVERY_CACHE_TTL_SECONDS:
            return (
                list(cached_options),
                cached_error,
                cached_default_branch,
                cached_has_more,
            )

    options, error, default_branch, has_more = _fetch_github_branch_options(
        token, repository, normalized_query, clamped_limit
    )
    _bounded_cache_put(
        _GITHUB_BRANCH_SEARCH_CACHE,
        cache_key,
        (now, tuple(options), error, default_branch, has_more),
    )
    # Legacy full-list cache is intentionally not populated: cold discovery is
    # always bounded to a single page per operation.
    return options, error, default_branch, has_more


async def _get_cached_github_branch_options_async(
    token: str,
    repository: str,
    query: str = "",
    limit: int = _BRANCH_SUGGESTION_DEFAULT_LIMIT,
) -> tuple[list[BranchOption], str | None, str | None, bool]:
    """Async cached search page for use inside async API routes."""

    clamped_limit = _clamp_branch_suggestion_limit(limit)
    normalized_query = _normalize_branch_query(query)
    now = time.monotonic()
    cache_key = _github_branch_search_cache_key(
        token, repository, normalized_query, clamped_limit
    )
    cached = _GITHUB_BRANCH_SEARCH_CACHE.get(cache_key)
    if cached:
        cached_at, cached_options, cached_error, cached_default_branch, cached_has_more = cached
        if now - cached_at < _GITHUB_REPOSITORY_DISCOVERY_CACHE_TTL_SECONDS:
            return (
                list(cached_options),
                cached_error,
                cached_default_branch,
                cached_has_more,
            )

    options, error, default_branch, has_more = (
        await _fetch_github_branch_options_async(
            token, repository, normalized_query, clamped_limit
        )
    )
    _bounded_cache_put(
        _GITHUB_BRANCH_SEARCH_CACHE,
        cache_key,
        (now, tuple(options), error, default_branch, has_more),
    )
    return options, error, default_branch, has_more


def _get_cached_repository_default_branch(
    token: str,
    repository: str,
) -> tuple[str | None, str | None]:
    """Return cached default-branch metadata without enumerating branches."""

    normalized_repository = _normalize_repository_value(repository)
    if not token or not normalized_repository:
        return None, None
    now = time.monotonic()
    cache_key = _github_branch_metadata_cache_key(token, repository)
    cached = _GITHUB_BRANCH_METADATA_CACHE.get(cache_key)
    if cached:
        cached_at, cached_default = cached
        if now - cached_at < _GITHUB_REPOSITORY_DISCOVERY_CACHE_TTL_SECONDS:
            return cached_default, None
    headers = _github_branch_headers(token)
    try:
        with httpx.Client(
            timeout=_GITHUB_REPOSITORY_DISCOVERY_TIMEOUT_SECONDS
        ) as client:
            default_branch = _fetch_repository_default_branch(
                client, headers, normalized_repository
            )
    except (httpx.HTTPError, ValueError):
        return None, "GitHub branch lookup is unavailable."
    _bounded_cache_put(
        _GITHUB_BRANCH_METADATA_CACHE, cache_key, (now, default_branch)
    )
    return default_branch, None


async def _get_cached_repository_default_branch_async(
    token: str,
    repository: str,
) -> tuple[str | None, str | None]:
    """Async cached default-branch metadata for use inside async API routes."""

    normalized_repository = _normalize_repository_value(repository)
    if not token or not normalized_repository:
        return None, None
    now = time.monotonic()
    cache_key = _github_branch_metadata_cache_key(token, repository)
    cached = _GITHUB_BRANCH_METADATA_CACHE.get(cache_key)
    if cached:
        cached_at, cached_default = cached
        if now - cached_at < _GITHUB_REPOSITORY_DISCOVERY_CACHE_TTL_SECONDS:
            return cached_default, None
    headers = _github_branch_headers(token)
    try:
        async with httpx.AsyncClient(
            timeout=_GITHUB_REPOSITORY_DISCOVERY_TIMEOUT_SECONDS
        ) as client:
            try:
                response = await client.get(
                    _GITHUB_REPOSITORY_METADATA_URL_TEMPLATE.format(
                        repository=normalized_repository
                    ),
                    headers=headers,
                )
                response.raise_for_status()
                metadata = response.json()
                default_branch = (
                    str(metadata.get("default_branch") or "").strip() or None
                    if isinstance(metadata, dict)
                    else None
                )
            except (httpx.HTTPError, ValueError):
                return None, "GitHub branch lookup is unavailable."
    except (httpx.HTTPError, ValueError):
        return None, "GitHub branch lookup is unavailable."
    _bounded_cache_put(
        _GITHUB_BRANCH_METADATA_CACHE, cache_key, (now, default_branch)
    )
    return default_branch, None


def _get_cached_branch_exact(
    token: str,
    repository: str,
    branch: str,
) -> tuple[bool, str | None, str | None, bool]:
    """Return a cached exact-name observation without scanning pages."""

    candidate = str(branch or "").strip()
    if not _is_valid_branch_name(candidate):
        return False, None, None, True
    now = time.monotonic()
    cache_key = _github_branch_resolve_cache_key(token, repository, candidate)
    cached = _GITHUB_BRANCH_RESOLVE_CACHE.get(cache_key)
    if cached:
        cached_at, found, resolved, error = cached
        if now - cached_at < _GITHUB_REPOSITORY_DISCOVERY_CACHE_TTL_SECONDS:
            if found or error is None:
                # Definitive observations (found, or definitively absent with
                # no error) stay definitive.
                return found, resolved, error, False
            return found, resolved, error, True
    found, resolved, error, inconclusive = _fetch_github_branch_exact(
        token, repository, candidate
    )
    _bounded_cache_put(
        _GITHUB_BRANCH_RESOLVE_CACHE, cache_key, (now, found, resolved, error)
    )
    return found, resolved, error, inconclusive


async def _get_cached_branch_exact_async(
    token: str,
    repository: str,
    branch: str,
) -> tuple[bool, str | None, str | None, bool]:
    """Async cached exact-name observation for use inside async API routes."""

    candidate = str(branch or "").strip()
    if not _is_valid_branch_name(candidate):
        return False, None, None, True
    now = time.monotonic()
    cache_key = _github_branch_resolve_cache_key(token, repository, candidate)
    cached = _GITHUB_BRANCH_RESOLVE_CACHE.get(cache_key)
    if cached:
        cached_at, found, resolved, error = cached
        if now - cached_at < _GITHUB_REPOSITORY_DISCOVERY_CACHE_TTL_SECONDS:
            if found:
                return found, resolved, error, False
            if error is None:
                return found, resolved, error, False
            return found, resolved, error, True
    found, resolved, error, inconclusive = await _fetch_github_branch_exact_async(
        token, repository, candidate
    )
    _bounded_cache_put(
        _GITHUB_BRANCH_RESOLVE_CACHE, cache_key, (now, found, resolved, error)
    )
    return found, resolved, error, inconclusive

def _is_create_page_path(initial_path: str) -> bool:
    normalized_path = urlparse(initial_path or "").path.rstrip("/")
    return normalized_path == "/workflows/new"

def _build_repository_options(
    *,
    include_credential_discovery: bool = True,
) -> dict[str, Any]:
    """Build Create-page repository suggestions from safe runtime sources."""

    options: list[RepositoryOption] = []
    seen: set[str] = set()
    _append_repository_option(
        options,
        seen,
        settings.workflow.github_repository,
        source="default",
    )

    configured_repos = str(getattr(settings.github, "github_repos", "") or "")
    for raw_repo in configured_repos.split(","):
        _append_repository_option(options, seen, raw_repo, source="configured")

    discovery_error: str | None = None
    github_config = getattr(settings, "github", None)
    github_enabled = (
        bool(getattr(github_config, "github_enabled", True))
        if github_config
        else False
    )
    github_token = (
        str(getattr(github_config, "github_token", "") or "").strip()
        if github_config
        else ""
    )
    if include_credential_discovery and github_enabled and github_token:
        discovered, discovery_error = _get_cached_github_repository_options(
            github_token
        )
        if discovery_error:
            discovery_error = "GitHub repository discovery is unavailable."
        for option in discovered:
            _append_repository_option(
                options,
                seen,
                option.value,
                source="github",
            )

    return {
        "items": [option.to_payload() for option in options],
        "error": discovery_error,
    }

def _github_branch_token() -> tuple[bool, str]:
    """Return (enabled, token) for MoonMind-owned GitHub branch lookup."""

    github_config = getattr(settings, "github", None)
    github_enabled = (
        bool(getattr(github_config, "github_enabled", True))
        if github_config
        else False
    )
    github_token = (
        str(getattr(github_config, "github_token", "") or "").strip()
        if github_config
        else ""
    )
    return github_enabled, github_token


def build_repository_branch_options(
    repository: str,
    query: str = "",
    limit: int = _BRANCH_SUGGESTION_DEFAULT_LIMIT,
) -> dict[str, Any]:
    """Build bounded Create-page branch suggestions plus default metadata."""

    normalized_repository = _normalize_repository_value(repository)
    if not normalized_repository:
        return {
            "items": [],
            "error": "Repository must be owner/repo before branches can be loaded.",
            "defaultBranch": None,
            "hasMore": False,
        }

    github_enabled, github_token = _github_branch_token()
    if not github_enabled or not github_token:
        return {
            "items": [],
            "error": "GitHub branch lookup is unavailable.",
            "defaultBranch": None,
            "hasMore": False,
        }

    options, error, default_branch, has_more = _get_cached_github_branch_options(
        github_token,
        normalized_repository,
        query,
        limit,
    )
    if error:
        error = "GitHub branch lookup is unavailable."
    return {
        "items": [option.to_payload() for option in options],
        "error": error,
        "defaultBranch": default_branch,
        "hasMore": has_more,
    }


async def build_repository_branch_options_async(
    repository: str,
    query: str = "",
    limit: int = _BRANCH_SUGGESTION_DEFAULT_LIMIT,
) -> dict[str, Any]:
    """Awaited variant so async routes never block the event loop on HTTP."""

    normalized_repository = _normalize_repository_value(repository)
    if not normalized_repository:
        return {
            "items": [],
            "error": "Repository must be owner/repo before branches can be loaded.",
            "defaultBranch": None,
            "hasMore": False,
        }

    github_enabled, github_token = _github_branch_token()
    if not github_enabled or not github_token:
        return {
            "items": [],
            "error": "GitHub branch lookup is unavailable.",
            "defaultBranch": None,
            "hasMore": False,
        }

    options, error, default_branch, has_more = (
        await _get_cached_github_branch_options_async(
            github_token,
            normalized_repository,
            query,
            limit,
        )
    )
    if error:
        error = "GitHub branch lookup is unavailable."
    return {
        "items": [option.to_payload() for option in options],
        "error": error,
        "defaultBranch": default_branch,
        "hasMore": has_more,
    }


def build_repository_branch_metadata(repository: str) -> dict[str, Any]:
    """Resolve only default-branch metadata without enumerating branches."""

    normalized_repository = _normalize_repository_value(repository)
    if not normalized_repository:
        return {
            "defaultBranch": None,
            "error": "Repository must be owner/repo before branches can be loaded.",
        }

    github_enabled, github_token = _github_branch_token()
    if not github_enabled or not github_token:
        return {
            "defaultBranch": None,
            "error": "GitHub branch lookup is unavailable.",
        }

    default_branch, error = _get_cached_repository_default_branch(
        github_token, normalized_repository
    )
    if error:
        error = "GitHub branch lookup is unavailable."
    return {"defaultBranch": default_branch, "error": error}


async def build_repository_branch_metadata_async(
    repository: str,
) -> dict[str, Any]:
    """Awaited metadata-only variant for async routes."""

    normalized_repository = _normalize_repository_value(repository)
    if not normalized_repository:
        return {
            "defaultBranch": None,
            "error": "Repository must be owner/repo before branches can be loaded.",
        }

    github_enabled, github_token = _github_branch_token()
    if not github_enabled or not github_token:
        return {
            "defaultBranch": None,
            "error": "GitHub branch lookup is unavailable.",
        }

    default_branch, error = await _get_cached_repository_default_branch_async(
        github_token, normalized_repository
    )
    if error:
        error = "GitHub branch lookup is unavailable."
    return {"defaultBranch": default_branch, "error": error}


def resolve_repository_branch(repository: str, branch: str) -> dict[str, Any]:
    """Resolve one exact branch name independent of suggestion pages."""

    normalized_repository = _normalize_repository_value(repository)
    candidate = str(branch or "").strip()
    if not normalized_repository:
        return {
            "found": False,
            "branch": None,
            "defaultBranch": None,
            "error": "Repository must be owner/repo before branches can be loaded.",
            "inconclusive": True,
        }
    if not _is_valid_branch_name(candidate):
        return {
            "found": False,
            "branch": None,
            "defaultBranch": None,
            "error": None,
            "inconclusive": True,
        }

    github_enabled, github_token = _github_branch_token()
    if not github_enabled or not github_token:
        return {
            "found": False,
            "branch": None,
            "defaultBranch": None,
            "error": "GitHub branch lookup is unavailable.",
            "inconclusive": True,
        }

    found, resolved, error, inconclusive = _get_cached_branch_exact(
        github_token, normalized_repository, candidate
    )
    default_branch, metadata_error = _get_cached_repository_default_branch(
        github_token, normalized_repository
    )
    if error:
        error = "GitHub branch lookup is unavailable."
    if metadata_error:
        metadata_error = "GitHub branch lookup is unavailable."
    return {
        "found": found,
        "branch": resolved,
        "defaultBranch": default_branch,
        "error": error or metadata_error,
        "inconclusive": inconclusive or bool(error or metadata_error),
    }


async def resolve_repository_branch_async(
    repository: str, branch: str
) -> dict[str, Any]:
    """Awaited exact-name variant for async routes."""

    normalized_repository = _normalize_repository_value(repository)
    candidate = str(branch or "").strip()
    if not normalized_repository:
        return {
            "found": False,
            "branch": None,
            "defaultBranch": None,
            "error": "Repository must be owner/repo before branches can be loaded.",
            "inconclusive": True,
        }
    if not _is_valid_branch_name(candidate):
        return {
            "found": False,
            "branch": None,
            "defaultBranch": None,
            "error": None,
            "inconclusive": True,
        }

    github_enabled, github_token = _github_branch_token()
    if not github_enabled or not github_token:
        return {
            "found": False,
            "branch": None,
            "defaultBranch": None,
            "error": "GitHub branch lookup is unavailable.",
            "inconclusive": True,
        }

    found, resolved, error, inconclusive = await _get_cached_branch_exact_async(
        github_token, normalized_repository, candidate
    )
    default_branch, metadata_error = (
        await _get_cached_repository_default_branch_async(
            github_token, normalized_repository
        )
    )
    if error:
        error = "GitHub branch lookup is unavailable."
    if metadata_error:
        metadata_error = "GitHub branch lookup is unavailable."
    return {
        "found": found,
        "branch": resolved,
        "defaultBranch": default_branch,
        "error": error or metadata_error,
        "inconclusive": inconclusive or bool(error or metadata_error),
    }


def build_repository_issue_options(repository: str, query: str = "") -> dict[str, Any]:
    """Build GitHub issue suggestions through MoonMind-owned GitHub lookup."""

    normalized_repository = _normalize_repository_value(repository)
    if not normalized_repository:
        return {
            "items": [],
            "error": "Repository must be owner/repo before issues can be loaded.",
        }

    github_enabled = bool(getattr(settings.github, "github_enabled", True))
    github_token = str(getattr(settings.github, "github_token", "") or "").strip()
    if not github_enabled or not github_token:
        return {"items": [], "error": "GitHub issue lookup is unavailable."}

    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {github_token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    params: dict[str, Any] = {"state": "open", "per_page": 20}
    normalized_query = str(query or "").strip()
    if normalized_query.startswith("#") and normalized_query[1:].isdigit():
        issue_number = normalized_query[1:]
        url = f"https://api.github.com/repos/{normalized_repository}/issues/{issue_number}"
        urls = [(url, None)]
    else:
        url = _GITHUB_ISSUE_DISCOVERY_URL_TEMPLATE.format(repository=normalized_repository)
        urls = [(url, params)]

    items: list[dict[str, Any]] = []
    try:
        with httpx.Client(timeout=_GITHUB_REPOSITORY_DISCOVERY_TIMEOUT_SECONDS) as client:
            for request_url, request_params in urls:
                response = client.get(request_url, headers=headers, params=request_params)
                response.raise_for_status()
                payload = response.json()
                raw_items = payload if isinstance(payload, list) else [payload]
                for raw in raw_items:
                    if not isinstance(raw, Mapping) or raw.get("pull_request"):
                        continue
                    title = str(raw.get("title") or "").strip()
                    number = raw.get("number")
                    body = str(raw.get("body") or "")
                    if normalized_query and not normalized_query.startswith("#"):
                        haystack = f"{number} {title} {body}".lower()
                        if normalized_query.lower() not in haystack:
                            continue
                    labels = raw.get("labels") if isinstance(raw.get("labels"), list) else []
                    label_names: list[str] = []
                    for label in labels:
                        value = label.get("name") if isinstance(label, Mapping) else label
                        if value is None:
                            continue
                        value_text = str(value).strip()
                        if value_text:
                            label_names.append(value_text)
                    items.append({
                        "repository": normalized_repository,
                        "number": number,
                        "title": title,
                        "body": body,
                        "url": str(raw.get("html_url") or ""),
                        "state": str(raw.get("state") or ""),
                        "labels": label_names,
                    })
    except (httpx.HTTPStatusError, httpx.TransportError, httpx.TimeoutException):
        return {"items": [], "error": "GitHub issue lookup is unavailable."}

    return {"items": items, "error": None}

def _jira_create_page_enabled() -> bool:
    """Return whether the Create-page Jira browser rollout is enabled."""

    return bool(settings.feature_flags.jira_create_page_enabled)

_STATUS_MAPS: dict[str, dict[str, str]] = {
    "temporal": {
        "scheduled": "queued",
        "initializing": "queued",
        "planning": "running",
        "executing": "running",
        "awaiting_external": "awaiting_action",
        "awaiting_slot": "queued",
        "waiting_on_dependencies": "waiting",
        "finalizing": "running",
        "running": "running",
        "succeeded": "completed",
        "completed": "completed",
        "failed": "failed",
        "canceled": "canceled",
        # Accept British spelling from legacy data or external adapters.
        "cancelled": "canceled",
        "queued": "queued",
        "awaiting_action": "awaiting_action",
    },

}

def normalize_status(source: str, raw_status: str | None) -> str:
    """Normalize source-specific status values into dashboard display states."""

    status_key = (raw_status or "").strip().lower()

    mapping = _STATUS_MAPS.get("temporal")

    if mapping and status_key in mapping:
        return mapping[status_key]

    # Fallback for unexpected values so the dashboard remains renderable.
    if "running" in status_key:
        return "running"
    if status_key in {"success", "completed", "done"}:
        return "succeeded"
    if status_key in {"error", "failed", "failure"}:
        return "failed"

    return "queued"

def status_maps() -> dict[str, dict[str, str]]:
    """Return a copy of status maps so callers can safely mutate local copies."""

    return deepcopy(_STATUS_MAPS)

def _build_supported_runtimes() -> list[str]:
    supported: list[str] = [
        "omnigent",
        "codex_cli",
        "claude_code",
        "codex_cloud",
    ]
    if settings.jules_runtime_gate.enabled:
        supported.append("jules")
    return supported

def _build_default_attachment_policy(config: "dict[str, Any]") -> dict[str, Any]:
    """Normalize attachment policy values for dashboard consumption."""

    max_count = max(1, int(config.get("agent_job_attachment_max_count", 1) or 1))
    max_bytes = max(
        1, int(config.get("agent_job_attachment_max_bytes", 10 * 1024 * 1024) or 1)
    )
    total_bytes = max(
        1, int(config.get("agent_job_attachment_total_bytes", 25 * 1024 * 1024) or 1)
    )
    allowed_types = tuple(
        config.get("agent_job_attachment_allowed_content_types") or ()
    )
    return {
        "maxCount": max_count,
        "maxBytes": max_bytes,
        "totalBytes": max(total_bytes, max_bytes),
        "allowedContentTypes": (
            list(allowed_types)
            if allowed_types
            else ["image/png", "image/jpeg", "image/webp"]
        ),
    }

def build_live_logs_feature_config() -> dict[str, object]:
    """Build the grouped Live Logs feature flags for dashboard consumers."""

    return {
        "logStreamingEnabled": bool(WorkflowSettings(_env_file=None).log_streaming_enabled),
        "liveLogsSessionTimelineEnabled": bool(
            settings.feature_flags.live_logs_session_timeline_enabled
        ),
        "liveLogsSessionTimelineRollout": str(
            settings.feature_flags.live_logs_session_timeline_rollout
        ),
        "liveLogsStructuredHistoryEnabled": bool(
            settings.feature_flags.live_logs_structured_history_enabled
        ),
    }

def _build_dashboard_system_metadata() -> dict[str, str | None]:
    """Return operator-facing build metadata for the dashboard shell and runtime config."""

    build_id = resolve_moonmind_build_id()
    return {
        "buildId": build_id,
    }

def _build_jira_runtime_config() -> dict[str, Any] | None:
    """Build Create-page Jira browser config when the UI rollout is enabled."""

    if not _jira_create_page_enabled():
        return None

    return {
        "sources": _build_jira_sources(),
        "system": {
            "enabled": True,
            "defaultProjectKey": settings.feature_flags.jira_create_page_default_project_key,
            "defaultBoardId": settings.feature_flags.jira_create_page_default_board_id,
            "rememberLastBoardInSession": settings.feature_flags.jira_create_page_remember_last_board_in_session,
        },
    }

def build_runtime_config(
    initial_path: str,
    *,
    default_runtime_override: str | None = None,
    default_provider_profile_ref: str | None = None,
) -> dict[str, Any]:
    """Build runtime config consumed by dashboard JavaScript.

    ``default_runtime_override`` and ``default_provider_profile_ref`` come
    from the per-request effective settings (user/workspace overrides resolved
    by ``SettingsCatalogService``). When omitted, behavior matches the legacy
    env+settings-only resolution.
    """

    supported_runtimes = _build_supported_runtimes()
    temporal_dashboard = settings.temporal_dashboard
    runtime_override = (
        normalize_runtime_id(default_runtime_override)
        if isinstance(default_runtime_override, str)
        and default_runtime_override.strip()
        else ""
    )
    configured_runtime = normalize_runtime_id(
        str(os.environ.get("MOONMIND_WORKER_RUNTIME", "")).strip().lower() or None
    ) if os.environ.get("MOONMIND_WORKER_RUNTIME", "").strip() else ""
    # The versioned runtime-provider rollout policy owns the promoted default
    # (MoonLadderStudios/MoonMind#3833). Per-request user/workspace overrides and
    # the worker runtime pin remain explicit authored intentions above it.
    runtime_target_catalog = runtime_target_catalog_payload()
    rollout_selection = resolve_runtime_target_selection(
        surface=AuthoringSurface.dashboard_config,
        workflow_settings=settings.workflow,
    )
    if runtime_override in supported_runtimes:
        default_runtime = runtime_override
    elif configured_runtime in supported_runtimes:
        default_runtime = configured_runtime
    elif rollout_selection.runtime_id in supported_runtimes:
        default_runtime = rollout_selection.runtime_id
    else:
        default_runtime = supported_runtimes[0]
    default_model_by_runtime: dict[str, str] = {}
    default_effort_by_runtime: dict[str, str] = {}
    for runtime in supported_runtimes:
        default_model, default_effort = resolve_runtime_defaults(
            runtime,
            workflow_settings=settings.workflow,
        )
        if default_model:
            default_model_by_runtime[runtime] = default_model
        if default_effort:
            default_effort_by_runtime[runtime] = default_effort
    default_model = default_model_by_runtime.get(default_runtime, "")
    default_effort = default_effort_by_runtime.get(default_runtime, "")
    default_repository = (
        str(settings.workflow.github_repository or "").strip()
        or DEFAULT_REPOSITORY
    )
    default_publish_mode = (
        str(settings.workflow.default_publish_mode or "").strip().lower() or "pr"
    )
    repository_options = _build_repository_options(
        include_credential_discovery=_is_create_page_path(initial_path)
    )

    system_metadata = _build_dashboard_system_metadata()
    jira_runtime_config = _build_jira_runtime_config()
    jira_sources = (
        {"jira": jira_runtime_config["sources"]} if jira_runtime_config else {}
    )
    jira_system = (
        {"jiraIntegration": jira_runtime_config["system"]}
        if jira_runtime_config
        else {}
    )
    return {
        "initialPath": initial_path,
        "pollIntervalsMs": {
            "list": _POLL_INTERVALS_MS["list"],
            "detail": _POLL_INTERVALS_MS["detail"],
            "events": _POLL_INTERVALS_MS["events"],
        },
        "statusMaps": status_maps(),
        "sources": {
            "schedules": {
                "list": "/api/recurring-workflows?scope=personal",
                "create": "/api/recurring-workflows",
                "detail": "/api/recurring-workflows/{definitionId}",
                "update": "/api/recurring-workflows/{definitionId}",
                "runNow": "/api/recurring-workflows/{definitionId}/run",
                "runs": "/api/recurring-workflows/{definitionId}/runs?limit=200",
                "delete": "/api/recurring-workflows/{definitionId}",
            },
            "temporal": {
                "list": temporal_dashboard.list_endpoint,
                "create": temporal_dashboard.create_endpoint,
                "detail": temporal_dashboard.detail_endpoint,
                "steps": temporal_dashboard.steps_endpoint,
                "update": temporal_dashboard.update_endpoint,
                "manifestStatus": "/api/executions/{workflowId}/manifest-status",
                "manifestNodes": "/api/executions/{workflowId}/manifest-nodes",
                "signal": temporal_dashboard.signal_endpoint,
                "cancel": temporal_dashboard.cancel_endpoint,
                "artifacts": temporal_dashboard.artifacts_endpoint,
                "artifactCreate": temporal_dashboard.artifact_create_endpoint,
                "artifactMetadata": temporal_dashboard.artifact_metadata_endpoint,
                "artifactPresignDownload": temporal_dashboard.artifact_presign_download_endpoint,
                "artifactDownload": temporal_dashboard.artifact_download_endpoint,
            },
            "agentRuns": {
                "observabilitySummary": "/api/agent-runs/{agentRunId}/observability-summary",
                "observabilityEvents": "/api/agent-runs/{agentRunId}/observability/events",
                "logsStream": "/api/agent-runs/{agentRunId}/logs/stream",
                "logsStdout": "/api/agent-runs/{agentRunId}/logs/stdout",
                "logsStderr": "/api/agent-runs/{agentRunId}/logs/stderr",
                "logsMerged": "/api/agent-runs/{agentRunId}/logs/merged",
                "diagnostics": "/api/agent-runs/{agentRunId}/diagnostics",
                "artifactSession": "/api/agent-runs/{agentRunId}/artifact-sessions/{sessionId}",
                "artifactSessionControl": "/api/agent-runs/{agentRunId}/artifact-sessions/{sessionId}/control",
                "sessionResources": "/api/sessions/{sessionId}/resources",
            },
            **jira_sources,
            "github": {
                "branches": "/api/github/branches?repository={repository}",
                "branchResolve": "/api/github/branches/resolve?repository={repository}&branch={branch}",
                "branchMetadata": "/api/github/branches/metadata?repository={repository}",
                "issues": "/api/github/issues?repository={repository}&q={query}",
            },

        },
        "features": {
            "temporalDashboard": {
                "enabled": bool(temporal_dashboard.enabled),
                "listEnabled": bool(temporal_dashboard.list_enabled),
                "detailEnabled": bool(temporal_dashboard.detail_enabled),
                "actionsEnabled": bool(temporal_dashboard.actions_enabled),
                "submitEnabled": bool(temporal_dashboard.submit_enabled),
                "temporalWorkflowEditing": bool(
                    temporal_dashboard.temporal_workflow_editing_enabled
                ),
                "debugFieldsEnabled": bool(temporal_dashboard.debug_fields_enabled),
                # MM-997: desktop workflow detail routes use the workspace shell by
                # default. Setting this client flag to false restores standalone
                # desktop detail presentation without changing mobile behavior.
                "workspaceShellEnabled": True,
            },
            **build_live_logs_feature_config(),
        },
        "system": {
            **system_metadata,
            "omnigentExecutionCatalog": public_execution_catalog(),
            "runtimeTargetCatalog": runtime_target_catalog,
            "defaultRepository": default_repository,
            "repositoryOptions": repository_options,
            "defaultRuntime": default_runtime,
            "defaultModel": default_model,
            "defaultEffort": default_effort,
            "defaultModelByRuntime": default_model_by_runtime,
            "defaultEffortByRuntime": default_effort_by_runtime,
            "defaultPublishMode": default_publish_mode,
            "workerRuntimeEnv": "MOONMIND_WORKER_RUNTIME",
            "supportedRuntimes": supported_runtimes,
            "supportedWorkerRuntimes": list(_SUPPORTED_WORKER_RUNTIMES),
            "runtimeCommandPreview": build_runtime_command_preview_config(),
            "presetCatalog": {
                "enabled": bool(settings.feature_flags.preset_catalog_enabled),
                "templateSaveEnabled": bool(
                    settings.feature_flags.preset_catalog_enabled
                ),
                "list": "/api/presets",
                "detail": "/api/presets/{slug}",
                "expand": "/api/presets/{slug}:expand",
                "saveFromWorkflow": "/api/presets/save-from-workflow",
            },
            "providerProfiles": {
                "list": "/api/v1/provider-profiles",
                "create": "/api/v1/provider-profiles",
                "detail": "/api/v1/provider-profiles/{profileId}",
                "update": "/api/v1/provider-profiles/{profileId}",
                "delete": "/api/v1/provider-profiles/{profileId}",
                **(
                    {"defaultProfileRef": default_provider_profile_ref}
                    if isinstance(default_provider_profile_ref, str)
                    and default_provider_profile_ref.strip()
                    else {}
                ),
            },
            "attachmentPolicy": {
                "enabled": bool(settings.workflow.agent_job_attachment_enabled),
                **_build_default_attachment_policy(
                    {
                        "agent_job_attachment_max_count": settings.workflow.agent_job_attachment_max_count,
                        "agent_job_attachment_max_bytes": settings.workflow.agent_job_attachment_max_bytes,
                        "agent_job_attachment_total_bytes": settings.workflow.agent_job_attachment_total_bytes,
                        "agent_job_attachment_allowed_content_types": settings.workflow.agent_job_attachment_allowed_content_types,
                    }
                ),
            },
            **jira_system,
        },
    }

_PROVIDER_PROFILE_INVALID_DIAGNOSTIC_CODES = frozenset(
    {"provider_profile_not_found", "provider_profile_disabled"}
)


def _coerce_uuid(value: Any) -> UUID | None:
    if isinstance(value, UUID):
        return value
    if value is None:
        return None
    try:
        return UUID(str(value))
    except (TypeError, ValueError):
        return None


async def resolve_dashboard_runtime_config(
    initial_path: str,
    *,
    session: AsyncSession | None,
    user: Any = None,
) -> dict[str, Any]:
    """Resolve runtime config with user/workspace setting overrides applied.

    Falls back to the legacy ``build_runtime_config`` behavior when the session
    is unavailable or the settings catalog cannot be reached. Settings consumed:

    - ``workflow.default_runtime`` (workspace scope)
    - ``workflow.default_provider_profile_ref`` (user scope, falls back to
      workspace and env). Refs are dropped when their diagnostics indicate the
      profile is missing or disabled, so the frontend can fall back to its
      ``is_default`` lookup instead of selecting a stale ID.
    """

    if session is None:
        return build_runtime_config(initial_path)

    # Local imports keep the view-model importable without DB models loaded
    # for tests that exercise build_runtime_config directly.
    from api_service.services.settings_catalog import SettingsCatalogService

    workspace_id = _coerce_uuid(getattr(user, "workspace_id", None))
    user_id = _coerce_uuid(getattr(user, "id", None))

    runtime_override: str | None = None
    profile_ref: str | None = None

    try:
        service = SettingsCatalogService(
            session=session,
            workspace_id=workspace_id,
            user_id=user_id,
        )
        runtime_eff = await service.effective_value_async(
            "workflow.default_runtime", scope="workspace"
        )
        if isinstance(runtime_eff.value, str) and runtime_eff.value.strip():
            runtime_override = runtime_eff.value.strip()

        profile_scope = "user" if user_id is not None else "workspace"
        profile_eff = await service.effective_value_async(
            "workflow.default_provider_profile_ref", scope=profile_scope
        )
        if isinstance(profile_eff.value, str) and profile_eff.value.strip():
            invalid = any(
                diag.code in _PROVIDER_PROFILE_INVALID_DIAGNOSTIC_CODES
                for diag in profile_eff.diagnostics
            )
            if not invalid:
                profile_ref = profile_eff.value.strip()
    except Exception:
        # Settings overrides are best-effort. If the DB is unreachable or the
        # catalog service raises, fall back to the env/settings-only defaults
        # so the dashboard still renders. The route still has the boot payload
        # to send back; we just don't get user/workspace personalization.
        logger.debug(
            "resolve_dashboard_runtime_config: falling back to defaults",
            exc_info=True,
        )
        runtime_override = None
        profile_ref = None

    return build_runtime_config(
        initial_path,
        default_runtime_override=runtime_override,
        default_provider_profile_ref=profile_ref,
    )


__all__ = [
    "build_live_logs_feature_config",
    "build_repository_branch_metadata",
    "build_repository_branch_metadata_async",
    "build_repository_branch_options",
    "build_repository_branch_options_async",
    "build_runtime_config",
    "resolve_dashboard_runtime_config",
    "resolve_repository_branch",
    "resolve_repository_branch_async",
    "normalize_status",
    "BranchOption",
    "RepositoryOption",
    "status_maps",
]
