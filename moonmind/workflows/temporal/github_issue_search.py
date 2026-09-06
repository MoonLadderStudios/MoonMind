"""Bounded GitHub issue selection at the trusted issue-loading Activity boundary."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

import httpx

from moonmind.workflows.adapters.github_service import GitHubService


def declared_prerequisites(body: str, repository: str) -> list[tuple[str, int]]:
    """Read explicit prerequisite sentences, never parent/related issue links."""
    declarations = re.finditer(
        r"(?:\bCompletion depends on|\bIntegration prerequisites:|"
        r"(?:^|(?<=[.!?]))[ \t]*(?:[-*] )?Depends on:?)\s*"
        r"(.+?)(?=\.(?:\s|$)|\n|$)",
        body,
        re.IGNORECASE | re.MULTILINE,
    )
    refs: list[tuple[str, int]] = []
    for declaration in declarations:
        text = re.sub(
            r"https://github\.com/([\w.-]+/[\w.-]+)/issues/(\d+)",
            r"\1#\2",
            declaration.group(1),
        )
        for match in re.finditer(
            r"(?<![\w/])(?:([\w.-]+/[\w.-]+))?#([1-9]\d*)"
            r"(?:\s*[-–—]\s*#?([1-9]\d*))?",
            text,
        ):
            start = int(match.group(2))
            end = int(match.group(3) or start)
            if end < start or end - start >= 100:
                raise ValueError(
                    "GitHub prerequisite range is invalid or exceeds 100 issues."
                )
            refs.extend(
                (match.group(1) or repository, number)
                for number in range(start, end + 1)
            )
    refs = list(dict.fromkeys(refs))
    if len(refs) > 100:
        raise ValueError("GitHub prerequisite declaration exceeds 100 issues.")
    return refs


async def check_prerequisites(
    *,
    issue: Mapping[str, Any],
    repository: str,
    github_service: GitHubService,
) -> list[dict[str, Any]]:
    """Resolve prerequisite state through authenticated GitHub reads only."""
    refs = declared_prerequisites(str(issue.get("body") or ""), repository)
    if not refs:
        return []
    async with httpx.AsyncClient(timeout=30.0) as client:
        for dependency_repo, number in refs:
            token, _error = await github_service.resolve_github_token(
                repo=dependency_repo
            )
            if not token:
                raise ValueError("GitHub prerequisite lookup is unavailable.")
            try:
                response = await client.get(
                    f"https://api.github.com/repos/{dependency_repo}/issues/{number}",
                    headers=github_service._github_headers(token),
                )
                response.raise_for_status()
                payload = response.json()
            except (httpx.HTTPError, ValueError) as exc:
                raise ValueError("GitHub prerequisite lookup failed.") from exc
            if (
                not isinstance(payload, Mapping)
                or payload.get("state") not in {"open", "closed"}
                or payload.get("number") != number
                or not is_complete_open_issue(
                    {**payload, "state": "open"}, dependency_repo
                )
            ):
                raise ValueError("GitHub prerequisite identity or state is invalid.")
            if payload["state"] == "open":
                return [
                    {
                        "source": "prerequisite",
                        "repository": dependency_repo,
                        "number": number,
                        "statusKnown": True,
                        "done": False,
                    }
                ]
    return []


def is_complete_open_issue(payload: Any, repository: str) -> bool:
    """Validate raw GitHub evidence before normalization can hide missing fields."""

    if not isinstance(payload, Mapping) or "pull_request" in payload:
        return False
    number = payload.get("number")
    labels = payload.get("labels")
    return (
        type(number) is int
        and number > 0
        and payload.get("state") == "open"
        and str(payload.get("html_url")).casefold()
        == f"https://github.com/{repository}/issues/{number}".casefold()
        and isinstance(payload.get("title"), str)
        and bool(payload["title"].strip())
        and "body" in payload
        and isinstance(payload["body"], (str, type(None)))
        and isinstance(labels, list)
        and all(
            isinstance(label, Mapping)
            and isinstance(label.get("name"), str)
            and bool(label["name"].strip())
            for label in labels
        )
    )


async def resolve_issue(
    *,
    repository: str,
    query: str,
    github_service: GitHubService,
    blockers_from_issue: Callable[[Mapping[str, Any]], Awaitable[list[dict[str, Any]]]],
) -> tuple[int | None, dict[str, Any]]:
    """Select the best search match, or first unblocked open issue, within 500 rows."""

    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError(
            "GitHub issue search requires an explicit owner/repository scope."
        )
    evidence: dict[str, Any] = {
        "searchEvidence": {
            "fallbackScanning": not query,
            "pagesExamined": 0,
            "candidatesExamined": 0,
        }
    }
    counts = evidence["searchEvidence"]
    token, error = await github_service.resolve_github_token(repo=repository)
    if not token:
        return None, {
            **evidence,
            "error": error or "GitHub issue search is unavailable.",
        }

    async with httpx.AsyncClient(timeout=30.0) as client:
        for page in range(1, 6):
            if query:
                url = "https://api.github.com/search/issues"
                params = {
                    "q": f"{query} repo:{repository} is:issue is:open",
                    "per_page": 100,
                    "page": page,
                }
            else:
                url = f"https://api.github.com/repos/{repository}/issues"
                params = {
                    "state": "open",
                    "sort": "created",
                    "direction": "desc",
                    "per_page": 100,
                    "page": page,
                }
            try:
                response = await client.get(
                    url, params=params, headers=github_service._github_headers(token)
                )
                response.raise_for_status()
                payload = response.json()
            except (httpx.HTTPError, ValueError) as exc:
                return None, {
                    **evidence,
                    "error": f"GitHub issue search failed: {type(exc).__name__}.",
                }
            counts["pagesExamined"] += 1
            if query and (
                not isinstance(payload, Mapping)
                or payload.get("incomplete_results") is not False
            ):
                return None, {
                    **evidence,
                    "error": "GitHub returned incomplete or malformed search evidence.",
                }
            candidates = payload.get("items") if query else payload
            if not isinstance(candidates, list) or len(candidates) > 100:
                return None, {
                    **evidence,
                    "error": "GitHub returned malformed issue candidates.",
                }
            for candidate in candidates:
                counts["candidatesExamined"] += 1
                if not isinstance(candidate, Mapping):
                    return None, {
                        **evidence,
                        "error": "GitHub returned a malformed issue candidate.",
                    }
                if "pull_request" in candidate:
                    continue
                if not is_complete_open_issue(candidate, repository):
                    return None, {
                        **evidence,
                        "error": "GitHub candidate identity, state, or blocker evidence is invalid.",
                    }
                normalized = dict(candidate)
                labels = candidate["labels"]
                normalized["labels"] = [label["name"] for label in labels]
                if not query and await blockers_from_issue(normalized):
                    continue
                return candidate["number"], evidence
            if len(candidates) < 100:
                return None, {
                    **evidence,
                    "error": "No eligible open GitHub issue found; candidate pages exhausted.",
                }
    return None, {
        **evidence,
        "error": "No eligible GitHub issue found within the 500-candidate scan limit.",
    }
