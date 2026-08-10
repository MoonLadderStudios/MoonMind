from __future__ import annotations

from collections.abc import Mapping
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError

with workflow.unsafe.imports_passed_through():
    from moonmind.workflows.temporal.activity_catalog import (
        build_default_activity_catalog,
    )

DEFAULT_ACTIVITY_CATALOG = build_default_activity_catalog()


@workflow.defn(name="MoonMind.ManagedRuntimeWorkspaceCleanup")
class MoonMindManagedRuntimeWorkspaceCleanupWorkflow:
    async def _execute_activity(
        self,
        activity_type: str,
        payload: dict[str, Any],
        *,
        summary: str,
    ) -> Any:
        route = DEFAULT_ACTIVITY_CATALOG.resolve_activity(activity_type)
        kwargs: dict[str, Any] = {
            "task_queue": route.task_queue,
            "start_to_close_timeout": timedelta(
                seconds=route.timeouts.start_to_close_seconds
            ),
            "schedule_to_close_timeout": timedelta(
                seconds=route.timeouts.schedule_to_close_seconds
            ),
            "retry_policy": RetryPolicy(
                initial_interval=timedelta(seconds=5),
                backoff_coefficient=2.0,
                maximum_interval=timedelta(seconds=route.retries.max_interval_seconds),
                maximum_attempts=route.retries.max_attempts,
                non_retryable_error_types=list(route.retries.non_retryable_error_codes),
            ),
            "summary": summary,
        }
        if route.timeouts.heartbeat_timeout_seconds is not None:
            kwargs["heartbeat_timeout"] = timedelta(
                seconds=route.timeouts.heartbeat_timeout_seconds
            )
        return await workflow.execute_activity(activity_type, payload, **kwargs)

    @workflow.run
    async def run(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = dict(payload or {})
        scheduled_maintenance = not payload
        workflow.set_current_details("Maintaining deployment storage")
        workflow.upsert_search_attributes(
            {
                "SessionStatus": ["cleanup"],
                "IsDegraded": [False],
            }
        )
        maintenance_errors: list[str] = []
        docker_result: dict[str, Any] = {}
        artifact_result: dict[str, Any] = {}

        try:
            result = await self._execute_activity(
                "agent_runtime.cleanup_managed_runtime_files",
                payload,
                summary="Clean retained managed runtime files",
            )
        except Exception:
            workflow.set_current_details("Managed runtime file cleanup failed")
            workflow.upsert_search_attributes(
                {
                    "SessionStatus": ["failed"],
                    "IsDegraded": [True],
                }
            )
            raise

        if scheduled_maintenance:
            # Keep the pre-existing workspace Activity first so an in-flight
            # execution can replay across this workflow change before the new
            # maintenance commands are appended to its history.
            try:
                raw_docker_result = await self._execute_activity(
                    "agent_runtime.reclaim_docker_storage",
                    {},
                    summary="Reclaim Docker storage under pressure",
                )
                if isinstance(raw_docker_result, Mapping):
                    docker_result = dict(raw_docker_result)
            except ActivityError:
                maintenance_errors.append("Docker storage maintenance failed")

            try:
                raw_artifact_result = await self._execute_activity(
                    "artifact.lifecycle_sweep",
                    {
                        "principal": "service:storage-maintenance",
                    },
                    summary="Expire and delete retained artifacts",
                )
                if isinstance(raw_artifact_result, Mapping):
                    artifact_result = dict(raw_artifact_result)
            except ActivityError:
                maintenance_errors.append("Artifact lifecycle sweep failed")

        normalized = dict(result or {})
        if scheduled_maintenance:
            normalized["dockerStorage"] = docker_result
            normalized["artifactLifecycle"] = artifact_result
            normalized["maintenanceErrors"] = maintenance_errors
        degraded = bool(normalized.get("errors")) or bool(maintenance_errors)
        degraded = degraded or bool(docker_result.get("errors"))
        workflow.set_current_details("Deployment storage maintenance complete")
        workflow.upsert_search_attributes(
            {
                "SessionStatus": ["completed"],
                "IsDegraded": [degraded],
            }
        )
        return normalized
