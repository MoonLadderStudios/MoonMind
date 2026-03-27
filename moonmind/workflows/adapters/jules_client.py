"""Async HTTP adapter for the Jules API."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
import json
import logging
import os
import re
from typing import Any

import httpx

from moonmind.schemas.jules_models import (
    JulesActivity,
    JulesCreateTaskRequest,
    JulesGetTaskRequest,
    JulesIntegrationCancelResult,
    JulesIntegrationFetchResult,
    JulesIntegrationStartRequest,
    JulesIntegrationStartResult,
    JulesIntegrationStatusResult,
    JulesListActivitiesResult,
    JulesResolveTaskRequest,
    JulesSendMessageRequest,
    JulesTaskResponse,
    normalize_jules_status,
)

logger = logging.getLogger(__name__)

_QUESTION_TEXT_KEYS = (
    "agentMessage",
    "agent_message",
    "message",
    "text",
    "content",
    "description",
    "prompt",
    "question",
    "body",
)


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

    async def send_message(self, request: JulesSendMessageRequest) -> None:
        """Send a follow-up prompt to an existing Jules session.

        Used for multi-step workflows: after a session reaches COMPLETED,
        this resumes it with new instructions.  The Jules API returns an
        empty body on success; callers must resume polling ``get_task()``
        to track the session through its next execution cycle.

        See: https://developers.google.com/jules/api/reference/rest/v1alpha/sessions/sendMessage
        """
        path = f"/sessions/{request.session_id}:sendMessage"
        await self._post_json_empty(path, json={"prompt": request.prompt})

    async def list_activities(
        self,
        session_id: str,
    ) -> JulesListActivitiesResult:
        """Fetch session activities and extract the latest agent question.

        Calls ``GET /v1alpha/sessions/{session}/activities`` to retrieve all
        activities.  Scans for the most recent ``AgentMessaged`` activity from
        ``originator == "agent"`` and returns its ``agentMessage`` text and
        ``id`` for deduplication.

        See: https://developers.google.com/jules/api/reference/rest/v1alpha/sessions.activities/list
        """
        data = await self._get_json(f"/sessions/{session_id}/activities")
        raw_activities = data.get("activities", [])

        # Parse and sort by createTime descending for deterministic ordering.
        activities: list[JulesActivity] = [
            JulesActivity.model_validate(a) for a in raw_activities
        ]
        activities.sort(
            key=lambda a: a.create_time or "",
            reverse=True,
        )

        latest_question: str | None = None
        activity_id: str | None = None
        for activity in activities:
            if not _is_agent_originated_activity(activity):
                continue
            extracted_question = _extract_activity_question(activity)
            if not extracted_question:
                continue
            latest_question = extracted_question
            activity_id = activity.id or activity.name
            break

        return JulesListActivitiesResult(
            sessionId=session_id,
            latestAgentQuestion=latest_question,
            activityId=activity_id,
        )

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
        if normalized == "running" and task.pull_request_url:
            normalized = "completed"

        return JulesIntegrationStatusResult(
            taskQueue=task_queue,
            externalOperationId=external_operation_id,
            normalizedStatus=normalized,
            providerStatus=provider_status,
            terminal=normalized in {"completed", "failed", "canceled"},
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
        if normalized == "running" and task.pull_request_url:
            normalized = "completed"

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
    # GitHub PR helpers – delegated to GitHubService
    # ------------------------------------------------------------------

    @staticmethod
    def _github_service() -> "GitHubService":
        from moonmind.workflows.adapters.github_service import GitHubService

        return GitHubService()

    async def merge_pull_request(
        self,
        *,
        pr_url: str,
        merge_method: str = "merge",
        github_token: str | None = None,
    ) -> "MergePRResult":
        """Merge a GitHub pull request by URL.

        Delegates to :class:`GitHubService`.
        """
        svc = self._github_service()
        return await svc.merge_pull_request(
            pr_url=pr_url,
            merge_method=merge_method,
            github_token=github_token,
        )

    async def update_pull_request_base(
        self,
        *,
        pr_url: str,
        new_base: str,
        github_token: str | None = None,
    ) -> tuple[bool, str]:
        """Update a GitHub PR's base (target) branch.

        Delegates to :class:`GitHubService`.
        """
        svc = self._github_service()
        return await svc.update_pull_request_base(
            pr_url=pr_url,
            new_base=new_base,
            github_token=github_token,
        )

    async def create_pull_request(
        self,
        *,
        repo: str,
        head: str,
        base: str,
        title: str,
        body: str,
        github_token: str | None = None,
    ) -> "CreatePRResult":
        """Create a GitHub pull request.

        Delegates to :class:`GitHubService`.
        """
        svc = self._github_service()
        return await svc.create_pull_request(
            repo=repo, head=head, base=base, title=title, body=body,
            github_token=github_token,
        )

    async def _post_json(self, path: str, *, json: dict[str, Any]) -> dict[str, Any]:
        return await self._request_with_retry("POST", path, json=json)

    async def _post_json_empty(self, path: str, *, json: dict[str, Any]) -> None:
        """POST expecting an empty (or ignored) response body."""
        await self._request_with_retry(
            "POST", path, json=json, allow_empty_response=True
        )

    async def _get_json(self, path: str) -> dict[str, Any]:
        return await self._request_with_retry("GET", path)

    async def _request_with_retry(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        allow_empty_response: bool = False,
    ) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(1, self._retry_attempts + 1):
            try:
                response = await self._client.request(method, path, json=json)
                response.raise_for_status()
                if allow_empty_response and not response.content.strip():
                    return {}
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


def _is_agent_originated_activity(activity: JulesActivity) -> bool:
    originator = str(activity.originator or "").strip().lower()
    if originator in {"agent", "assistant", "jules"}:
        return True
    activity_name = str(activity.name or "").strip().lower()
    return "agent" in activity_name or "assistant" in activity_name


def _extract_activity_question(activity: JulesActivity) -> str | None:
    direct_message = (
        str(activity.agent_messaged.agent_message).strip()
        if activity.agent_messaged and activity.agent_messaged.agent_message
        else ""
    )
    if direct_message:
        return direct_message
    if activity.description:
        normalized_description = str(activity.description).strip()
        if normalized_description:
            return normalized_description
    for candidate in _iter_activity_text_candidates(activity):
        normalized = str(candidate or "").strip()
        if normalized:
            return normalized
    return None


def _iter_activity_text_candidates(activity: JulesActivity) -> list[str]:
    candidates: list[str] = []
    for key in _QUESTION_TEXT_KEYS:
        candidates.extend(
            _extract_nested_values(getattr(activity, "model_extra", None), key)
        )
        if activity.agent_messaged is not None:
            candidates.extend(
                _extract_nested_values(
                    getattr(activity.agent_messaged, "model_extra", None), key
                )
            )
    return candidates


def _extract_nested_values(payload: Any, target_key: str) -> list[str]:
    values: list[str] = []
    if isinstance(payload, Mapping):
        for key, value in payload.items():
            if str(key).strip() == target_key:
                flattened = _stringify_candidate(value)
                if flattened:
                    values.append(flattened)
            values.extend(_extract_nested_values(value, target_key))
        return values
    if isinstance(payload, Sequence) and not isinstance(
        payload, (str, bytes, bytearray)
    ):
        for item in payload:
            values.extend(_extract_nested_values(item, target_key))
    return values


def _stringify_candidate(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        normalized = value.strip()
        return normalized or None
    if isinstance(value, Mapping):
        for key in _QUESTION_TEXT_KEYS:
            nested = _stringify_candidate(value.get(key))
            if nested:
                return nested
        return None
    if isinstance(value, Sequence) and not isinstance(
        value, (str, bytes, bytearray)
    ):
        for item in value:
            nested = _stringify_candidate(item)
            if nested:
                return nested
        return None
    text = str(value).strip()
    return text or None


__all__ = [
    "JulesClient",
    "JulesClientError",
]
