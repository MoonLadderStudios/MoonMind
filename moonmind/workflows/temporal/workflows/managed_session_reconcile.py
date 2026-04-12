from __future__ import annotations

from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from moonmind.workflows.temporal.activity_catalog import (
        build_default_activity_catalog,
    )


DEFAULT_ACTIVITY_CATALOG = build_default_activity_catalog()


@workflow.defn(name="MoonMind.ManagedSessionReconcile")
class MoonMindManagedSessionReconcileWorkflow:
    @workflow.run
    async def run(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        del payload
        workflow.set_current_details("Reconciling managed Codex sessions")
        workflow.upsert_search_attributes(
            {
                "SessionStatus": ["reconciling"],
                "IsDegraded": [False],
            }
        )
        route = DEFAULT_ACTIVITY_CATALOG.resolve_activity(
            "agent_runtime.reconcile_managed_sessions"
        )
        try:
            result = await workflow.execute_activity(
                "agent_runtime.reconcile_managed_sessions",
                {},
                task_queue=route.task_queue,
                start_to_close_timeout=timedelta(
                    seconds=route.timeouts.start_to_close_seconds
                ),
                schedule_to_close_timeout=timedelta(
                    seconds=route.timeouts.schedule_to_close_seconds
                ),
                heartbeat_timeout=timedelta(
                    seconds=route.timeouts.heartbeat_timeout_seconds or 30
                ),
                retry_policy=RetryPolicy(
                    initial_interval=timedelta(seconds=5),
                    backoff_coefficient=2.0,
                    maximum_interval=timedelta(seconds=route.retries.max_interval_seconds),
                    maximum_attempts=route.retries.max_attempts,
                    non_retryable_error_types=list(route.retries.non_retryable_error_codes),
                ),
                summary="Reconcile managed Codex sessions",
            )
        except Exception:
            workflow.set_current_details("Managed Codex session reconcile failed")
            workflow.upsert_search_attributes(
                {
                    "SessionStatus": ["failed"],
                    "IsDegraded": [True],
                }
            )
            raise
        normalized = result or {}
        is_degraded = bool(normalized.get("degradedSessionRecords"))
        workflow.set_current_details("Managed Codex session reconcile complete")
        workflow.upsert_search_attributes(
            {
                "SessionStatus": ["completed"],
                "IsDegraded": [is_degraded],
            }
        )
        return normalized
