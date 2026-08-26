from __future__ import annotations

from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from moonmind.workflows.temporal.activity_catalog import (
        build_default_activity_catalog,
    )


@workflow.defn(name="MoonMind.OmnigentOAuthHostJanitor")
class MoonMindOmnigentOAuthHostJanitorWorkflow:
    @workflow.run
    async def run(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = dict(payload or {})
        route = build_default_activity_catalog().resolve_activity(
            "integration.omnigent.oauth_host_janitor"
        )
        return await workflow.execute_activity(
            "integration.omnigent.oauth_host_janitor",
            payload,
            task_queue=route.task_queue,
            start_to_close_timeout=timedelta(
                seconds=route.timeouts.start_to_close_seconds
            ),
            schedule_to_close_timeout=timedelta(
                seconds=route.timeouts.schedule_to_close_seconds
            ),
            retry_policy=RetryPolicy(
                initial_interval=timedelta(seconds=5),
                maximum_interval=timedelta(seconds=30),
                maximum_attempts=route.retries.max_attempts,
            ),
        )
