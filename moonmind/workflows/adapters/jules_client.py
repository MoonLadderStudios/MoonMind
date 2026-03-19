"""Async HTTP adapter for the Jules API."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from typing import Any

import httpx

from moonmind.schemas.jules_models import (
    JulesCreateTaskRequest,
    JulesGetTaskRequest,
    JulesIntegrationCancelResult,
    JulesIntegrationFetchResult,
    JulesIntegrationMergePRResult,
    JulesIntegrationStartRequest,
    JulesIntegrationStartResult,
    JulesIntegrationStatusResult,
    JulesResolveTaskRequest,
    JulesTaskResponse,
    normalize_jules_status,
)

logger = logging.getLogger(__name__)


class JulesClientError(RuntimeError):
    """Raised when Jules API requests fail.

    Carries structured metadata for error mapping.  The string
    representation is intentionally scrubbed to avoid leaking
    secrets (API keys, bearer tokens, etc.).
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        request_path: str | None = None,
        ambiguous: bool = False,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.request_path = request_path
        self.ambiguous = ambiguous

    def __str__(self) -> str:
        parts = ["Jules API request failed"]
        if self.request_path:
            parts.append(f"path={self.request_path}")
        if self.status_code is not None:
            parts.append(f"status={self.status_code}")
        if self.ambiguous:
            parts.append("ambiguous")
        return ": ".join(parts)


class JulesClient:
    """HTTP wrapper for Jules task management endpoints."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        timeout: float = 30.0,
        retry_attempts: int = 3,
        retry_delay_seconds: float = 1.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._retry_attempts = retry_attempts
        self._retry_delay_seconds = retry_delay_seconds
        if client is not None:
            self._client = client
            self._owns_client = False
        else:
            headers = {
                "Accept": "application/json",
                "X-Goog-Api-Key": api_key,
            }
            self._client = httpx.AsyncClient(
                base_url=base_url, timeout=timeout, headers=headers
            )
            self._owns_client = True

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def create_task(self, request: JulesCreateTaskRequest) -> JulesTaskResponse:
        data = await self._post_json(
            "/sessions",
            json=request.model_dump(by_alias=True, mode="json", exclude_none=True),
        )
        return JulesTaskResponse.model_validate(data)

    async def resolve_task(self, request: JulesResolveTaskRequest) -> JulesTaskResponse:
        data = await self._post_json(
            f"/sessions/{request.task_id}/finish",
            json=request.model_dump(by_alias=True, mode="json", exclude={"task_id"}),
        )
        return JulesTaskResponse.model_validate(data)

    async def get_task(self, request: JulesGetTaskRequest) -> JulesTaskResponse:
        data = await self._get_json(f"/sessions/{request.task_id}")
        return JulesTaskResponse.model_validate(data)

    async def start_integration(
        self,
        request: JulesIntegrationStartRequest,
        *,
        task_queue: str = "mm.activity.integrations",
        recommended_poll_seconds: int | None = None,
    ) -> JulesIntegrationStartResult:
        """Start Jules work using the provider-neutral monitoring contract."""

        metadata = dict(request.metadata)
        metadata.setdefault("moonmind", {})
        moonmind_meta = metadata["moonmind"]
        if isinstance(moonmind_meta, dict):
            moonmind_meta.setdefault("correlationId", request.correlation_id)
            moonmind_meta.setdefault("idempotencyKey", request.idempotency_key)
            if request.callback_correlation_key:
                moonmind_meta.setdefault(
                    "callbackCorrelationKey", request.callback_correlation_key
                )
            if request.callback_url:
                moonmind_meta.setdefault("callbackUrl", request.callback_url)

        description = request.description
        if request.input_refs or request.parameters:
            envelope = {
                "description": request.description,
                "inputRefs": request.input_refs,
                "parameters": request.parameters,
            }
            description = json.dumps(envelope, sort_keys=True)

        try:
            created = await self.create_task(
                JulesCreateTaskRequest(
                    title=request.title,
                    description=description,
                    metadata=metadata,
                )
            )
        except JulesClientError as exc:
            if exc.request_path == "/sessions" and exc.status_code is None:
                raise JulesClientError(
                    "ambiguous Jules start result",
                    request_path=exc.request_path,
                    ambiguous=True,
                ) from exc
            raise

        provider_status = str(created.status or "").strip() or "unknown"
        return JulesIntegrationStartResult(
            taskQueue=task_queue,
            externalOperationId=str(created.task_id),
            normalizedStatus=normalize_jules_status(provider_status),
            providerStatus=provider_status,
            callbackSupported=bool(request.callback_url),
            callbackCorrelationKey=request.callback_correlation_key,
            recommendedPollSeconds=recommended_poll_seconds,
            externalUrl=str(created.url or "").strip() or None,
            providerSummary={
                "provider": "jules",
                "idempotencyKey": request.idempotency_key,
            },
            idempotencyKey=request.idempotency_key,
        )

    async def get_integration_status(
        self,
        *,
        external_operation_id: str,
        task_queue: str = "mm.activity.integrations",
        recommended_poll_seconds: int | None = None,
    ) -> JulesIntegrationStatusResult:
        """Return provider-neutral monitoring status for one Jules task."""

        task = await self.get_task(JulesGetTaskRequest(task_id=external_operation_id))
        provider_status = str(task.status or "").strip() or "unknown"
        normalized = normalize_jules_status(provider_status)
        return JulesIntegrationStatusResult(
            taskQueue=task_queue,
            externalOperationId=external_operation_id,
            normalizedStatus=normalized,
            providerStatus=provider_status,
            terminal=normalized in {"succeeded", "failed", "canceled"},
            recommendedPollSeconds=recommended_poll_seconds,
            externalUrl=str(task.url or "").strip() or None,
            providerSummary={"provider": "jules"},
        )

    async def fetch_integration_result(
        self,
        *,
        external_operation_id: str,
        result_refs: list[str] | None = None,
        task_queue: str = "mm.activity.integrations",
    ) -> JulesIntegrationFetchResult:
        """Return a compact, idempotent Jules result envelope."""

        task = await self.get_task(JulesGetTaskRequest(task_id=external_operation_id))
        provider_status = str(task.status or "").strip() or "unknown"
        normalized = normalize_jules_status(provider_status)
        summary = (
            f"Jules task {external_operation_id} completed with status "
            f"'{provider_status}'."
        )
        return JulesIntegrationFetchResult(
            taskQueue=task_queue,
            externalOperationId=external_operation_id,
            outputRefs=list(result_refs or []),
            summary=summary,
            diagnosticsRef=None,
            providerStatus=provider_status,
            normalizedStatus=normalized,
        )

    async def cancel_integration(
        self,
        *,
        external_operation_id: str,
        task_queue: str = "mm.activity.integrations",
    ) -> JulesIntegrationCancelResult:
        """Attempt best-effort cancellation for one Jules task."""

        try:
            task = await self.resolve_task(
                JulesResolveTaskRequest(
                    task_id=external_operation_id,
                    resolution_notes="Canceled by MoonMind.",
                    status="canceled",
                )
            )
        except JulesClientError as exc:
            if exc.status_code in {404, 405, 501}:
                return JulesIntegrationCancelResult(
                    taskQueue=task_queue,
                    externalOperationId=external_operation_id,
                    accepted=False,
                    unsupported=True,
                    finalProviderStatus=None,
                    normalizedStatus="unknown",
                    summary=(
                        f"Jules cancellation is unsupported for task "
                        f"{external_operation_id}."
                    ),
                )
            if exc.status_code is None:
                return JulesIntegrationCancelResult(
                    taskQueue=task_queue,
                    externalOperationId=external_operation_id,
                    accepted=False,
                    ambiguous=True,
                    finalProviderStatus=None,
                    normalizedStatus="unknown",
                    summary=(
                        f"Jules cancellation for task {external_operation_id} "
                        "returned an ambiguous transport failure."
                    ),
                )
            raise

        provider_status = str(task.status or "").strip() or "canceled"
        normalized = normalize_jules_status(provider_status)
        return JulesIntegrationCancelResult(
            taskQueue=task_queue,
            externalOperationId=external_operation_id,
            accepted=True,
            finalProviderStatus=provider_status,
            normalizedStatus=normalized,
            summary=f"Jules task {external_operation_id} cancellation accepted.",
        )

    # ------------------------------------------------------------------
    # GitHub PR helpers (used by branch-publish auto-merge)
    # ------------------------------------------------------------------

    _GITHUB_PR_URL_RE = re.compile(
        r"https://github\.com/([^/]+)/([^/]+)/pull/(\d+)"
    )

    @staticmethod
    def _parse_github_pr_url(
        pr_url: str,
    ) -> tuple[str, str, str] | None:
        """Extract ``(owner, repo, pr_number)`` from a GitHub PR URL.

        Returns ``None`` when the URL does not match the expected format.
        """
        match = JulesClient._GITHUB_PR_URL_RE.match(pr_url)
        if not match:
            return None
        return match.group(1), match.group(2), match.group(3)

    @staticmethod
    def _resolve_github_token(explicit_token: str | None = None) -> str:
        """Return the GitHub token from the explicit arg or ``GITHUB_TOKEN`` env."""
        token = (explicit_token or os.environ.get("GITHUB_TOKEN", "")).strip()
        return token

    @staticmethod
    def _github_headers(token: str) -> dict[str, str]:
        """Build standard GitHub API v3 request headers."""
        return {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    async def merge_pull_request(
        self,
        *,
        pr_url: str,
        merge_method: str = "merge",
        github_token: str | None = None,
    ) -> JulesIntegrationMergePRResult:
        """Merge a GitHub pull request by URL.

        This is used after Jules completes a branch-publish session to
        auto-merge the PR that Jules created into the target branch.
        """

        parsed = self._parse_github_pr_url(pr_url)
        if not parsed:
            return JulesIntegrationMergePRResult(
                prUrl=pr_url,
                merged=False,
                summary=f"Could not parse PR URL: {pr_url}",
            )

        owner, repo, pr_number = parsed
        token = self._resolve_github_token(github_token)
        if not token:
            return JulesIntegrationMergePRResult(
                prUrl=pr_url,
                merged=False,
                summary="GITHUB_TOKEN is not configured; cannot merge PR.",
            )

        api_url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}/merge"
        headers = self._github_headers(token)
        payload = {"merge_method": merge_method}

        async with httpx.AsyncClient(timeout=30.0) as gh_client:
            try:
                response = await gh_client.put(api_url, headers=headers, json=payload)
                response.raise_for_status()
                data = response.json()
                return JulesIntegrationMergePRResult(
                    prUrl=pr_url,
                    merged=data.get("merged", True),
                    mergeSha=data.get("sha"),
                    summary=(
                        f"PR {owner}/{repo}#{pr_number} merged successfully."
                    ),
                )
            except httpx.HTTPStatusError as exc:
                status_code = exc.response.status_code
                body = exc.response.text[:500] if exc.response else "(no body)"
                logger.error(
                    "GitHub merge API returned HTTP %s for %s: %s",
                    status_code, pr_url, body,
                )
                return JulesIntegrationMergePRResult(
                    prUrl=pr_url,
                    merged=False,
                    summary=(
                        f"GitHub merge failed with HTTP {status_code} "
                        f"for PR {owner}/{repo}#{pr_number}."
                    ),
                )
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                logger.error(
                    "GitHub merge request failed for %s: %s",
                    pr_url, exc.__class__.__name__,
                )
                return JulesIntegrationMergePRResult(
                    prUrl=pr_url,
                    merged=False,
                    summary=(
                        f"GitHub merge request failed: {exc.__class__.__name__}"
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

        Returns ``(success, summary)`` where *success* is ``True`` when the
        base branch was changed successfully.

        Used before ``merge_pull_request()`` when the user's desired target
        branch differs from the branch Jules started from.
        """

        parsed = self._parse_github_pr_url(pr_url)
        if not parsed:
            return False, f"Could not parse PR URL: {pr_url}"

        owner, repo, pr_number = parsed
        token = self._resolve_github_token(github_token)
        if not token:
            return False, "GITHUB_TOKEN is not configured; cannot update PR base."

        api_url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}"
        headers = self._github_headers(token)
        payload = {"base": new_base}

        async with httpx.AsyncClient(timeout=30.0) as gh_client:
            try:
                response = await gh_client.patch(api_url, headers=headers, json=payload)
                response.raise_for_status()
                return True, (
                    f"PR {owner}/{repo}#{pr_number} base updated to '{new_base}'."
                )
            except httpx.HTTPStatusError as exc:
                status_code = exc.response.status_code
                body = exc.response.text[:500] if exc.response else "(no body)"
                logger.error(
                    "GitHub update-base API returned HTTP %s for %s: %s",
                    status_code, pr_url, body,
                )
                return False, (
                    f"Failed to update PR base to '{new_base}' "
                    f"(HTTP {status_code})."
                )
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                logger.error(
                    "GitHub update-base request failed for %s: %s",
                    pr_url, exc.__class__.__name__,
                )
                return False, (
                    f"GitHub update-base request failed: {exc.__class__.__name__}"
                )

    async def _post_json(self, path: str, *, json: dict[str, Any]) -> dict[str, Any]:
        return await self._request_with_retry("POST", path, json=json)

    async def _get_json(self, path: str) -> dict[str, Any]:
        return await self._request_with_retry("GET", path)

    async def _request_with_retry(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(1, self._retry_attempts + 1):
            try:
                response = await self._client.request(method, path, json=json)
                response.raise_for_status()
                payload = response.json()
                if isinstance(payload, dict):
                    return payload
                raise JulesClientError(
                    f"Jules API response for {path} was not a JSON object",
                    request_path=path,
                )
            except httpx.HTTPStatusError as exc:
                status_code = exc.response.status_code
                retryable = (500 <= status_code < 600) or status_code == 429
                last_error = exc
                if retryable and attempt < self._retry_attempts:
                    delay = self._retry_delay_seconds
                    if status_code == 429:
                        retry_after = exc.response.headers.get("Retry-After")
                        if retry_after is not None:
                            try:
                                delay = max(delay, float(retry_after))
                            except (ValueError, TypeError):
                                pass
                    logger.warning(
                        "Jules API request %s failed with HTTP %s (attempt %s/%s); retrying in %.1fs",
                        path,
                        status_code,
                        attempt,
                        self._retry_attempts,
                        delay,
                    )
                    await asyncio.sleep(delay)
                    continue
                logger.error(
                    "Jules API %s %s returned HTTP %s: %s",
                    method, path, status_code,
                    exc.response.text[:2000] if exc.response else "(no body)",
                )
                raise JulesClientError(
                    f"HTTP {status_code}",
                    status_code=status_code,
                    request_path=path,
                ) from exc
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                last_error = exc
                if attempt < self._retry_attempts:
                    logger.warning(
                        "Jules API request %s failed due to %s (attempt %s/%s); retrying in %.1fs",
                        path,
                        exc.__class__.__name__,
                        attempt,
                        self._retry_attempts,
                        self._retry_delay_seconds,
                    )
                    await asyncio.sleep(self._retry_delay_seconds)
                    continue
                break
            except httpx.HTTPError as exc:
                raise JulesClientError(
                    exc.__class__.__name__,
                    request_path=path,
                ) from exc
        raise JulesClientError(
            last_error.__class__.__name__ if last_error else "unknown error",
            request_path=path,
        ) from last_error


__all__ = [
    "JulesClient",
    "JulesClientError",
]
