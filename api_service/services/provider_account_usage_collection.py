"""Production account-usage dispatcher: one collection workflow per attempt.

The coordinating service stays in this process; dispatch only starts
``MoonMind.ProviderAccountUsageCollection`` on the workflow fleet and returns.
Credential access, owner requests and idle probes run in worker activities
under the credential authority that workflow acquires, so the API never runs a
CLI or mounts an OAuth home. A runtime/provider with no registered collector
reports ``not_supported``.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Awaitable, Callable

from api_service.services.provider_account_usage import (
    AccountUsageProfileRef,
    CollectionRequest,
    DispatchResult,
)
from moonmind.provider_profiles.usage_collection import (
    UsageCollectorRegistry,
    default_usage_collectors,
)

#: Bounds one attempt's workflow end to end, including cleanup and release.
_EXECUTION_TIMEOUT = timedelta(minutes=15)


class TemporalAccountUsageDispatcher:
    def __init__(
        self,
        *,
        collectors: UsageCollectorRegistry | None = None,
        client_factory: Callable[[], Awaitable[Any]] | None = None,
    ) -> None:
        self._collectors = collectors or default_usage_collectors()
        self._client_factory = client_factory
        self._adapter: Any = None

    def supports(self, profile: AccountUsageProfileRef | CollectionRequest) -> bool:
        return self._collectors.get(profile.runtime_id, profile.provider_id) is not None

    async def _client(self) -> Any:
        if self._client_factory is not None:
            return await self._client_factory()
        if self._adapter is None:
            from moonmind.workflows.temporal.client import TemporalClientAdapter

            self._adapter = TemporalClientAdapter()
        return await self._adapter.get_client()

    async def dispatch(self, request: CollectionRequest) -> DispatchResult:
        from temporalio.exceptions import WorkflowAlreadyStartedError

        from moonmind.workflows.temporal.activity_catalog import get_workflow_task_queue
        from moonmind.workflows.temporal.workflows.provider_account_usage import (
            WORKFLOW_NAME,
            workflow_id_for_attempt,
        )

        if not self.supports(request):
            return DispatchResult.NOT_SUPPORTED
        client = await self._client()
        try:
            await client.start_workflow(
                WORKFLOW_NAME,
                {
                    "profile_id": request.profile_id,
                    "credential_generation": request.credential_generation,
                    "provider_id": request.provider_id,
                    "runtime_id": request.runtime_id,
                    "attempt_id": request.attempt_id,
                    "trigger": request.trigger,
                    "deadline_at": request.deadline_at.isoformat(),
                },
                id=workflow_id_for_attempt(request.profile_id, request.attempt_id),
                task_queue=get_workflow_task_queue(),
                execution_timeout=_EXECUTION_TIMEOUT,
            )
        except WorkflowAlreadyStartedError:
            # A retried dispatch of the same attempt; it is already collecting.
            pass
        return DispatchResult.DISPATCHED


__all__ = ["TemporalAccountUsageDispatcher"]
