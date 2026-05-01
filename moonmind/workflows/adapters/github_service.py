"""General-purpose GitHub REST API service.

Provides provider-agnostic pull-request operations used by the ``repo.*``
Temporal activity family.  Extracted from `JulesClient` to decouple core
workflow orchestration from Jules-specific adapters (Constitution §I, §III, §VIII).
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Mapping, Optional

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
    head_sha: Optional[str] = Field(None, alias="headSha")

class MergePRResult(BaseModel):
    """Result from ``repo.merge_pr``."""

    model_config = ConfigDict(populate_by_name=True)

    pr_url: str = Field(..., alias="prUrl")
    merged: bool = Field(..., alias="merged")
    merge_sha: Optional[str] = Field(None, alias="mergeSha")
    summary: str = Field(..., alias="summary")

class PullRequestReadinessResult(BaseModel):
    """Compact readiness evidence for one pull request revision."""

    model_config = ConfigDict(populate_by_name=True)

    head_sha: str = Field(..., alias="headSha")
    ready: bool = Field(False, alias="ready")
    pull_request_open: bool | None = Field(None, alias="pullRequestOpen")
    pull_request_merged: bool | None = Field(None, alias="pullRequestMerged")
    checks_complete: bool | None = Field(None, alias="checksComplete")
    checks_passing: bool | None = Field(None, alias="checksPassing")
    automated_review_complete: bool | None = Field(
        None, alias="automatedReviewComplete"
    )
    policy_allowed: bool | None = Field(True, alias="policyAllowed")
    blockers: list[dict[str, Any]] = Field(default_factory=list, alias="blockers")

# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

_GITHUB_PR_URL_RE = re.compile(
    r"https://github\.com/([^/]+)/([^/]+)/pull/(\d+)"
)
_CONFLICTING_MERGEABLE_STATES = {"dirty"}
_CONFLICTING_MERGE_STATE_STATUSES = {"CONFLICTING", "DIRTY"}
_CONFLICTING_MERGEABLE_VALUES = {"CONFLICTING", "DIRTY"}


def _pull_request_has_merge_conflicts(pr_data: Mapping[str, Any]) -> bool:
    mergeable_state = str(pr_data.get("mergeable_state") or "").strip().lower()
    merge_state_status = str(
        pr_data.get("mergeStateStatus") or pr_data.get("merge_state_status") or ""
    ).strip().upper()
    mergeable = pr_data.get("mergeable")

    if mergeable_state in _CONFLICTING_MERGEABLE_STATES:
        return True
    if merge_state_status in _CONFLICTING_MERGE_STATE_STATUSES:
        return True
    if (
        isinstance(mergeable, str)
        and mergeable.strip().upper() in _CONFLICTING_MERGEABLE_VALUES
    ):
        return True
    return False


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
                    head_sha=(data.get("head") or {}).get("sha"),
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

    async def evaluate_pull_request_readiness(
        self,
        *,
        repo: str,
        pr_number: int,
        head_sha: str,
        policy: dict[str, Any] | None = None,
        github_token: str | None = None,
    ) -> PullRequestReadinessResult:
        """Evaluate GitHub readiness for a tracked pull request revision."""

        token, resolution_error = await self.resolve_github_token(github_token)
        if not token:
            return PullRequestReadinessResult(
                headSha=head_sha,
                ready=False,
                pullRequestOpen=None,
                checksComplete=None,
                checksPassing=None,
                automatedReviewComplete=None,
                policyAllowed=True,
                blockers=[
                    {
                        "kind": "external_state_unavailable",
                        "summary": resolution_error
                        or self._missing_auth_summary("evaluate PR readiness"),
                        "retryable": True,
                        "source": "github",
                    }
                ],
            )

        policy = dict(policy or {})
        checks_required = policy.get("checks", "required") == "required"
        review_required = policy.get("automatedReview", "required") == "required"
        headers = self._github_headers(token)
        blockers: list[dict[str, Any]] = []
        observed_head_sha = head_sha
        pr_open: bool | None = None
        pr_merged: bool | None = None
        checks_complete: bool | None = None
        checks_passing: bool | None = None
        automated_review_complete: bool | None = None
        merge_conflicted = False

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            try:
                pr_response = await client.get(
                    f"https://api.github.com/repos/{repo}/pulls/{pr_number}",
                    headers=headers,
                )
                pr_response.raise_for_status()
                pr_data = pr_response.json()
                pr_open = pr_data.get("state") == "open"
                pr_merged = bool(pr_data.get("merged"))
                head = pr_data.get("head") if isinstance(pr_data, dict) else {}
                if isinstance(head, dict):
                    observed_head_sha = str(head.get("sha") or head_sha)
                merge_conflicted = _pull_request_has_merge_conflicts(pr_data)
            except httpx.HTTPStatusError as exc:
                blockers.append(
                    {
                        "kind": "external_state_unavailable",
                        "summary": (
                            "GitHub pull request state could not be fetched "
                            f"(HTTP {exc.response.status_code})."
                        ),
                        "retryable": exc.response.status_code >= 500,
                        "source": "github",
                    }
                )
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                blockers.append(
                    {
                        "kind": "external_state_unavailable",
                        "summary": (
                            "GitHub pull request state request failed: "
                            f"{exc.__class__.__name__}."
                        ),
                        "retryable": True,
                        "source": "github",
                    }
                )

            if pr_open is False and pr_merged is not True:
                blockers.append(
                    {
                        "kind": "pull_request_closed",
                        "summary": "Pull request is closed.",
                        "retryable": False,
                        "source": "github",
                    }
                )

            if pr_open is True and pr_merged is not True and merge_conflicted:
                return PullRequestReadinessResult(
                    headSha=observed_head_sha,
                    ready=True,
                    pullRequestOpen=pr_open,
                    pullRequestMerged=pr_merged,
                    checksComplete=checks_complete,
                    checksPassing=checks_passing,
                    automatedReviewComplete=automated_review_complete,
                    policyAllowed=True,
                    blockers=[],
                )

            if checks_required and pr_merged is not True and not blockers:
                check_evidence = await self._evaluate_github_checks(
                    client=client,
                    repo=repo,
                    head_sha=observed_head_sha,
                    headers=headers,
                )
                checks_complete = check_evidence["complete"]
                checks_passing = check_evidence["passing"]
                blockers.extend(check_evidence["blockers"])

            if review_required and pr_merged is not True and not blockers:
                review_evidence = await self._evaluate_automated_review(
                    client=client,
                    repo=repo,
                    pr_number=pr_number,
                    headers=headers,
                )
                automated_review_complete = review_evidence["complete"]
                blockers.extend(review_evidence["blockers"])

        return PullRequestReadinessResult(
            headSha=observed_head_sha,
            ready=not blockers and pr_merged is not True,
            pullRequestOpen=pr_open,
            pullRequestMerged=pr_merged,
            checksComplete=checks_complete,
            checksPassing=checks_passing,
            automatedReviewComplete=automated_review_complete,
            policyAllowed=True,
            blockers=blockers,
        )

    async def _evaluate_github_checks(
        self,
        *,
        client: httpx.AsyncClient,
        repo: str,
        head_sha: str,
        headers: dict[str, str],
    ) -> dict[str, Any]:
        blockers: list[dict[str, Any]] = []
        try:
            status_response = await client.get(
                f"https://api.github.com/repos/{repo}/commits/{head_sha}/status",
                headers=headers,
            )
            status_response.raise_for_status()
            status_data = status_response.json()
            status_state = str(status_data.get("state") or "").lower()
            commit_statuses = status_data.get("statuses") or []

            checks_response = await client.get(
                f"https://api.github.com/repos/{repo}/commits/{head_sha}/check-runs",
                headers=headers,
            )
            checks_response.raise_for_status()
            checks_data = checks_response.json()
            check_runs = checks_data.get("check_runs") or []
        except httpx.HTTPStatusError as exc:
            return {
                "complete": None,
                "passing": None,
                "blockers": [
                    {
                        "kind": "external_state_unavailable",
                        "summary": (
                            "GitHub check state could not be fetched "
                            f"(HTTP {exc.response.status_code})."
                        ),
                        "retryable": exc.response.status_code >= 500,
                        "source": "github",
                    }
                ],
            }
        except (httpx.TransportError, httpx.TimeoutException) as exc:
            return {
                "complete": None,
                "passing": None,
                "blockers": [
                    {
                        "kind": "external_state_unavailable",
                        "summary": (
                            "GitHub check state request failed: "
                            f"{exc.__class__.__name__}."
                        ),
                        "retryable": True,
                        "source": "github",
                    }
                ],
            }

        pending_runs = [
            run
            for run in check_runs
            if str(run.get("status") or "").lower() != "completed"
        ]
        failed_runs = [
            run
            for run in check_runs
            if str(run.get("conclusion") or "").lower()
            not in {"", "success", "neutral", "skipped"}
        ]
        has_commit_statuses = bool(commit_statuses)
        has_check_runs = bool(check_runs)
        status_pending = status_state in {"pending", "expected"} and (
            has_commit_statuses or not has_check_runs
        )
        status_failed = status_state in {"failure", "error"} and (
            has_commit_statuses or not has_check_runs
        )
        has_running_checks = status_pending or bool(pending_runs)
        has_failed_checks = status_failed or bool(failed_runs)

        if has_running_checks:
            blockers.append(
                {
                    "kind": "checks_running",
                    "summary": "Required checks are still running.",
                    "retryable": True,
                    "source": "github",
                }
            )

        return {
            "complete": not has_running_checks,
            "passing": not has_running_checks and not has_failed_checks,
            "blockers": blockers,
        }

    async def _evaluate_automated_review(
        self,
        *,
        client: httpx.AsyncClient,
        repo: str,
        pr_number: int,
        headers: dict[str, str],
    ) -> dict[str, Any]:
        try:
            response = await client.get(
                f"https://api.github.com/repos/{repo}/pulls/{pr_number}/reviews",
                headers=headers,
            )
            response.raise_for_status()
            reviews = response.json()
        except httpx.HTTPStatusError as exc:
            return {
                "complete": None,
                "blockers": [
                    {
                        "kind": "external_state_unavailable",
                        "summary": (
                            "GitHub review state could not be fetched "
                            f"(HTTP {exc.response.status_code})."
                        ),
                        "retryable": exc.response.status_code >= 500,
                        "source": "github",
                    }
                ],
            }
        except (httpx.TransportError, httpx.TimeoutException) as exc:
            return {
                "complete": None,
                "blockers": [
                    {
                        "kind": "external_state_unavailable",
                        "summary": (
                            "GitHub review state request failed: "
                            f"{exc.__class__.__name__}."
                        ),
                        "retryable": True,
                        "source": "github",
                    }
                ],
            }

        latest_review_states: dict[str, tuple[str, bool]] = {}
        review_items = [
            (str(review.get("submitted_at") or ""), index, review)
            for index, review in enumerate(reviews)
            if isinstance(review, dict)
        ]
        for _submitted_at, index, review in sorted(review_items):
            user = review.get("user") if isinstance(review.get("user"), dict) else {}
            reviewer = str(user.get("login") or review.get("user") or index)
            latest_review_states[reviewer] = (
                str(review.get("state") or "").upper(),
                self._is_trusted_automation_reviewer(reviewer, user),
            )

        review_completed = any(
            state == "APPROVED" or (state == "COMMENTED" and trusted_automation)
            for state, trusted_automation in latest_review_states.values()
        )
        changes_requested = any(
            state == "CHANGES_REQUESTED"
            for state, _trusted_automation in latest_review_states.values()
        )
        if review_completed and not changes_requested:
            return {"complete": True, "blockers": []}
        if changes_requested:
            return {
                "complete": False,
                "blockers": [
                    {
                        "kind": "automated_review_pending",
                        "summary": "Automated review has requested changes.",
                        "retryable": True,
                        "source": "github",
                    }
                ],
            }
        reaction_evidence = await self._evaluate_codex_review_reaction(
            client=client,
            repo=repo,
            pr_number=pr_number,
            headers=headers,
        )
        if reaction_evidence["complete"]:
            return reaction_evidence
        if reaction_evidence["blockers"]:
            return reaction_evidence
        return {
            "complete": False,
            "blockers": [
                {
                    "kind": "automated_review_pending",
                    "summary": "Automated review has not completed.",
                    "retryable": True,
                    "source": "github",
                }
            ],
        }

    async def _evaluate_codex_review_reaction(
        self,
        *,
        client: httpx.AsyncClient,
        repo: str,
        pr_number: int,
        headers: dict[str, str],
    ) -> dict[str, Any]:
        reaction_url = (
            f"https://api.github.com/repos/{repo}/issues/{pr_number}/reactions"
            "?per_page=100"
        )
        try:
            while reaction_url:
                response = await client.get(reaction_url, headers=headers)
                response.raise_for_status()
                reactions = response.json()
                for reaction in reactions:
                    if not isinstance(reaction, dict):
                        continue
                    user = (
                        reaction.get("user")
                        if isinstance(reaction.get("user"), dict)
                        else {}
                    )
                    reviewer = str(user.get("login") or "")
                    if (
                        str(reaction.get("content") or "") == "+1"
                        and self._is_codex_connector_reviewer(reviewer)
                    ):
                        return {"complete": True, "blockers": []}
                reaction_url = response.links.get("next", {}).get("url")
        except httpx.HTTPStatusError as exc:
            return {
                "complete": None,
                "blockers": [
                    {
                        "kind": "external_state_unavailable",
                        "summary": (
                            "GitHub review reaction state could not be fetched "
                            f"(HTTP {exc.response.status_code})."
                        ),
                        "retryable": exc.response.status_code >= 500,
                        "source": "github",
                    }
                ],
            }
        except (httpx.TransportError, httpx.TimeoutException) as exc:
            return {
                "complete": None,
                "blockers": [
                    {
                        "kind": "external_state_unavailable",
                        "summary": (
                            "GitHub review reaction state request failed: "
                            f"{exc.__class__.__name__}."
                        ),
                        "retryable": True,
                        "source": "github",
                    }
                ],
            }
        return {"complete": False, "blockers": []}

    @staticmethod
    def _is_codex_connector_reviewer(reviewer: str) -> bool:
        login = reviewer.strip().lower()
        if login.endswith("[bot]"):
            login = login[: -len("[bot]")]
        return login == "chatgpt-codex-connector"

    @staticmethod
    def _is_trusted_automation_reviewer(
        reviewer: str,
        user: Mapping[str, Any],
    ) -> bool:
        login = reviewer.strip().lower()
        user_type = str(user.get("type") or "").strip().lower()
        return (
            user_type == "bot"
            or login.endswith("[bot]")
            or login
            in {
                "chatgpt-codex-connector",
                "gemini-code-assist",
                "github-actions",
            }
        )

__all__ = [
    "CreatePRResult",
    "GitHubService",
    "MergePRResult",
    "PullRequestReadinessResult",
]
