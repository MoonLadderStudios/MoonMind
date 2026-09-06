"""Bounded GitHub issue selection at the trusted issue-loading Activity boundary."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from typing import Any

import httpx

from moonmind.workflows.adapters.github_service import GitHubService


async def resolve_issue(
    *,
    repository: str,
    query: str,
    github_service: GitHubService,
    blockers_from_issue: Callable[[Mapping[str, Any]], list[dict[str, Any]]],
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
                number = candidate.get("number")
                if (
                    type(number) is not int
                    or number <= 0
                    or candidate.get("state") != "open"
                    or str(candidate.get("html_url")).casefold()
                    != f"https://github.com/{repository}/issues/{number}".casefold()
                    or not isinstance(candidate.get("body"), (str, type(None)))
                    or "body" not in candidate
                    or not isinstance(candidate.get("labels"), list)
                ):
                    return None, {
                        **evidence,
                        "error": "GitHub candidate identity, state, or blocker evidence is invalid.",
                    }
                normalized = dict(candidate)
                labels = candidate["labels"]
                if any(
                    not isinstance(label, Mapping)
                    or not isinstance(label.get("name"), str)
                    for label in labels
                ):
                    return None, {
                        **evidence,
                        "error": "GitHub returned malformed blocker labels.",
                    }
                normalized["labels"] = [label["name"] for label in labels]
                if not query and blockers_from_issue(normalized):
                    continue
                return number, evidence
            if len(candidates) < 100:
                return None, {
                    **evidence,
                    "error": "No eligible open GitHub issue found; candidate pages exhausted.",
                }
    return None, {
        **evidence,
        "error": "No eligible GitHub issue found within the 500-candidate scan limit.",
    }
