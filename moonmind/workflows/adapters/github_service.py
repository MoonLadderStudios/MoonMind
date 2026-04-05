"""General-purpose GitHub REST API service.

Provides provider-agnostic pull-request operations used by the ``repo.*``
Temporal activity family.  Extracted from `JulesClient` to decouple core
workflow orchestration from Jules-specific adapters (Constitution §I, §III, §VIII).
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Optional

import httpx
from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result models (provider-agnostic replacements for Jules-prefixed models)
# ---------------------------------------------------------------------------


class CreatePRResult(BaseModel):
    """Result from ``repo.create_pr``."""

    model_config = ConfigDict(populate_by_name=True)

    url: Optional[str] = Field(None, alias="url")
    created: bool = Field(..., alias="created")
    summary: str = Field(..., alias="summary")


class MergePRResult(BaseModel):
    """Result from ``repo.merge_pr``."""

    model_config = ConfigDict(populate_by_name=True)

    pr_url: str = Field(..., alias="prUrl")
    merged: bool = Field(..., alias="merged")
    merge_sha: Optional[str] = Field(None, alias="mergeSha")
    summary: str = Field(..., alias="summary")


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

_GITHUB_PR_URL_RE = re.compile(
    r"https://github\.com/([^/]+)/([^/]+)/pull/(\d+)"
)


class GitHubService:
    """Thin wrapper around the GitHub REST API for pull-request operations.

    This service is stateless and safe to share across activity invocations.
    Authentication is resolved from an explicit token, ``GITHUB_TOKEN`` env var,
    or the configured secret reference.
    """

    def __init__(self, *, timeout: float = 30.0) -> None:
        self._timeout = timeout

    # -- helpers ----------------------------------------------------------

    @staticmethod
    def _missing_auth_summary(action: str) -> str:
        return (
            "GitHub auth is not configured; set GITHUB_TOKEN "
            f"or configure GITHUB_TOKEN_SECRET_REF / WORKFLOW_GITHUB_TOKEN_SECRET_REF to {action}."
        )

    @staticmethod
    def _secret_ref_resolution_summary() -> str:
        return (
            "GitHub token secret reference could not be resolved. "
            "Ensure GITHUB_TOKEN is set, or configure "
            "GITHUB_TOKEN_SECRET_REF / WORKFLOW_GITHUB_TOKEN_SECRET_REF; see logs for details."
        )

    @staticmethod
    def parse_github_pr_url(pr_url: str) -> tuple[str, str, str] | None:
        """Extract ``(owner, repo, pr_number)`` from a GitHub PR URL.

        Returns ``None`` when the URL does not match the expected format.
        """
        match = _GITHUB_PR_URL_RE.match(pr_url)
        if not match:
            return None
        return match.group(1), match.group(2), match.group(3)

    @staticmethod
    async def resolve_github_token(
        explicit_token: str | None = None,
    ) -> tuple[str, str | None]:
        """Resolve a GitHub token from explicit input, env, or secret ref."""
        token = (explicit_token or os.environ.get("GITHUB_TOKEN", "")).strip()
        if token:
            return token, None

        from moonmind.config.settings import settings
        from moonmind.workflows.temporal.runtime.managed_api_key_resolve import (
            resolve_managed_api_key_reference,
        )

        secret_ref = str(
            getattr(settings.github, "github_token_secret_ref", "") or ""
        ).strip()
        if not secret_ref:
            return "", None

        try:
            return await resolve_managed_api_key_reference(secret_ref), None
        except Exception:
            logger.warning(
                "Failed to resolve GitHub token secret ref",
                exc_info=True,
            )
            return "", GitHubService._secret_ref_resolution_summary()

    @staticmethod
    def _github_headers(token: str) -> dict[str, str]:
        return {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    # -- PR operations ----------------------------------------------------

    async def create_pull_request(
        self,
        *,
        repo: str,
        head: str,
        base: str,
        title: str,
        body: str,
        github_token: str | None = None,
    ) -> CreatePRResult:
        """Create a GitHub pull request via REST API."""

        token, resolution_error = await self.resolve_github_token(github_token)
        if not token:
            return CreatePRResult(
                created=False,
                summary=resolution_error or self._missing_auth_summary("create a PR"),
            )

        api_url = f"https://api.github.com/repos/{repo}/pulls"
        headers = self._github_headers(token)
        payload = {"title": title, "head": head, "base": base, "body": body}

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            try:
                response = await client.post(
                    api_url, headers=headers, json=payload
                )
                response.raise_for_status()
                data = response.json()
                return CreatePRResult(
                    url=data.get("html_url"),
                    created=True,
                    summary=f"PR created successfully: {data.get('html_url')}",
                )
            except httpx.HTTPStatusError as exc:
                status_code = exc.response.status_code
                resp_body = (
                    exc.response.text[:500] if exc.response else "(no body)"
                )
                logger.error(
                    "GitHub create PR API returned HTTP %s for %s: %s",
                    status_code,
                    repo,
                    resp_body,
                )
                return CreatePRResult(
                    created=False,
                    summary=(
                        f"GitHub create PR failed with HTTP {status_code}"
                        f" for {repo}."
                    ),
                )
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                logger.error(
                    "GitHub create PR request failed for %s: %s",
                    repo,
                    exc.__class__.__name__,
                )
                return CreatePRResult(
                    created=False,
                    summary=(
                        f"GitHub create PR request failed:"
                        f" {exc.__class__.__name__}"
                    ),
                )

    async def merge_pull_request(
        self,
        *,
        pr_url: str,
        merge_method: str = "merge",
        github_token: str | None = None,
    ) -> MergePRResult:
        """Merge a GitHub pull request by URL."""

        parsed = self.parse_github_pr_url(pr_url)
        if not parsed:
            return MergePRResult(
                pr_url=pr_url,
                merged=False,
                summary=f"Could not parse PR URL: {pr_url}",
            )

        owner, repo, pr_number = parsed
        token, resolution_error = await self.resolve_github_token(github_token)
        if not token:
            return MergePRResult(
                pr_url=pr_url,
                merged=False,
                summary=resolution_error or self._missing_auth_summary("merge a PR"),
            )

        api_url = (
            f"https://api.github.com/repos/{owner}/{repo}"
            f"/pulls/{pr_number}/merge"
        )
        headers = self._github_headers(token)
        payload = {"merge_method": merge_method}

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            try:
                response = await client.put(
                    api_url, headers=headers, json=payload
                )
                response.raise_for_status()
                data = response.json()
                return MergePRResult(
                    pr_url=pr_url,
                    merged=data.get("merged", True),
                    merge_sha=data.get("sha"),
                    summary=(
                        f"PR {owner}/{repo}#{pr_number} merged successfully."
                    ),
                )
            except httpx.HTTPStatusError as exc:
                status_code = exc.response.status_code
                resp_body = (
                    exc.response.text[:500] if exc.response else "(no body)"
                )
                logger.error(
                    "GitHub merge API returned HTTP %s for %s: %s",
                    status_code,
                    pr_url,
                    resp_body,
                )
                return MergePRResult(
                    pr_url=pr_url,
                    merged=False,
                    summary=(
                        f"GitHub merge failed with HTTP {status_code}"
                        f" for PR {owner}/{repo}#{pr_number}."
                    ),
                )
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                logger.error(
                    "GitHub merge request failed for %s: %s",
                    pr_url,
                    exc.__class__.__name__,
                )
                return MergePRResult(
                    pr_url=pr_url,
                    merged=False,
                    summary=(
                        f"GitHub merge request failed:"
                        f" {exc.__class__.__name__}"
                    ),
                )

    async def update_pull_request_base(
        self,
        *,
        pr_url: str,
        new_base: str,
        github_token: str | None = None,
    ) -> tuple[bool, str]:
        """Update a GitHub PR's base (target) branch.

        Returns ``(success, summary)``.
        """

        parsed = self.parse_github_pr_url(pr_url)
        if not parsed:
            return False, f"Could not parse PR URL: {pr_url}"

        owner, repo, pr_number = parsed
        token, resolution_error = await self.resolve_github_token(github_token)
        if not token:
            return (
                False,
                resolution_error
                or self._missing_auth_summary("update a PR base branch"),
            )

        api_url = (
            f"https://api.github.com/repos/{owner}/{repo}"
            f"/pulls/{pr_number}"
        )
        headers = self._github_headers(token)
        payload: dict[str, Any] = {"base": new_base}

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            try:
                response = await client.patch(
                    api_url, headers=headers, json=payload
                )
                response.raise_for_status()
                return True, (
                    f"PR {owner}/{repo}#{pr_number} base updated"
                    f" to '{new_base}'."
                )
            except httpx.HTTPStatusError as exc:
                status_code = exc.response.status_code
                resp_body = (
                    exc.response.text[:500] if exc.response else "(no body)"
                )
                logger.error(
                    "GitHub update-base API returned HTTP %s for %s: %s",
                    status_code,
                    pr_url,
                    resp_body,
                )
                return False, (
                    f"Failed to update PR base to '{new_base}'"
                    f" (HTTP {status_code})."
                )
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                logger.error(
                    "GitHub update-base request failed for %s: %s",
                    pr_url,
                    exc.__class__.__name__,
                )
                return False, (
                    f"GitHub update-base request failed:"
                    f" {exc.__class__.__name__}"
                )


__all__ = [
    "CreatePRResult",
    "GitHubService",
    "MergePRResult",
]
