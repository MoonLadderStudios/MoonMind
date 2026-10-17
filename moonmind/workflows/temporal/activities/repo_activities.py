"""Provider-agnostic GitHub repository activities.

Registered on the ``mm.activity.integrations`` task queue.
"""

from __future__ import annotations

from datetime import timedelta

from temporalio import activity
from temporalio.exceptions import ApplicationError

from moonmind.config.settings import settings
from moonmind.security import scan_outbound_text


@activity.defn(name="repo.create_pr")
async def repo_create_pr_activity(payload: dict) -> dict:
    """Create a PR via GitHub REST API (provider-agnostic)."""
    import re
    from pydantic import BaseModel, Field, ValidationError

    _SECRET_PATTERNS = [
        re.compile(r"ghp_[A-Za-z0-9_]{36,}"),
        re.compile(r"github_pat_[A-Za-z0-9_]{22,}"),
        re.compile(r"AIza[A-Za-z0-9_\\-]{35}"),
        re.compile(r"ATATT[A-Za-z0-9_\\-]{10,}"),
        re.compile(r"AKIA[A-Z0-9]{16}"),
        re.compile(r"sk-[A-Za-z0-9]{20,}"),
        re.compile(r"-----BEGIN\s+(RSA|EC|DSA|OPENSSH)\s+PRIVATE\s+KEY-----"),
    ]

    class _CreatePRPayload(BaseModel):
        repo: str = Field(min_length=1)
        head: str = Field(min_length=1)
        base: str = Field(min_length=1)
        title: str = Field(min_length=1)
        body: str = Field(default="")
        draft: bool = Field(default=False)

    try:
        validated = _CreatePRPayload.model_validate(payload)
    except ValidationError as exc:
        raise ValueError(
            f"Invalid payload for repo_create_pr_activity: {exc}"
        ) from exc

    for pattern in _SECRET_PATTERNS:
        if pattern.search(validated.title) or pattern.search(validated.body):
            raise ValueError("PR title or body contains potential secret material. Request blocked.")

    for field in ("title", "body"):
        scan = scan_outbound_text(
            getattr(validated, field),
            location=f"repo.create_pr.{field}",
            settings=settings,
        )
        if not scan.allowed:
            raise ValueError(
                "PR title or body contains blocked outbound content. Request blocked."
            )

    from moonmind.workflows.adapters.github_service import GitHubService

    svc = GitHubService()
    result = await svc.create_pull_request(
        repo=validated.repo,
        head=validated.head,
        base=validated.base,
        title=validated.title,
        body=validated.body,
        draft=validated.draft,
    )
    if result.retryable:
        # Temporal owns bounded retries. Each attempt re-reads the frozen
        # head/base before creating, so a lost POST acknowledgment is adopted.
        raise ApplicationError(
            result.summary,
            type="GitHubTransientError",
            next_retry_delay=(
                timedelta(seconds=result.retry_after_seconds)
                if result.retry_after_seconds is not None else None
            ),
        )
    return result.model_dump(by_alias=True)


@activity.defn(name="repo.sync_pr_unverified_criteria")
async def repo_sync_pr_unverified_criteria_activity(payload: dict) -> dict:
    """Refresh verifier disclosure without replacing an operator's PR prose."""
    from pydantic import BaseModel, Field
    from moonmind.workflows.adapters.github_service import GitHubService

    class _Payload(BaseModel):
        repo: str = Field(min_length=1)
        prUrl: str = Field(min_length=1)
        section: str
        expectedHeadSha: str | None = Field(default=None, min_length=1)

    validated = _Payload.model_validate(payload)
    scan = scan_outbound_text(
        validated.section,
        location="repo.sync_pr_unverified_criteria",
        settings=settings,
    )
    if not scan.allowed:
        raise ValueError("Unverified criteria contain blocked outbound content")
    updated = await GitHubService().sync_pull_request_unverified_criteria(
        repo=validated.repo, pr_url=validated.prUrl, section=validated.section,
        expected_head_sha=validated.expectedHeadSha,
        admitted_workflow_id=(activity.info().workflow_id if activity.in_activity() else None),
    )
    return {"updated": updated}


__all__ = [
    "repo_create_pr_activity",
    "repo_sync_pr_unverified_criteria_activity",
]
