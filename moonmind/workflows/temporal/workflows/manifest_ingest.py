import logging
from datetime import timedelta
from typing import Any, Optional, TypedDict

from temporalio import exceptions, workflow
from temporalio.common import RetryPolicy

from moonmind.workflows.temporal.activity_catalog import (
    TemporalActivityRoute,
    build_default_activity_catalog,
)

WORKFLOW_NAME = "MoonMind.ManifestIngest"
DEFAULT_ACTIVITY_CATALOG = build_default_activity_catalog()

class ManifestIngestWorkflowInput(TypedDict, total=False):
    workflow_type: str
    manifest_ref: str
    action: str
    options: Optional[dict[str, Any]]

class ManifestIngestWorkflowOutput(TypedDict):
    status: str
    manifest_digest: Optional[str]
    plan_ref: Optional[str]
    summary_ref: Optional[str]

@workflow.defn(name=WORKFLOW_NAME)
class MoonMindManifestIngestWorkflow:
    def _retry_policy_for_route(self, route: TemporalActivityRoute) -> RetryPolicy:
        return RetryPolicy(
            initial_interval=timedelta(seconds=5),
            backoff_coefficient=2.0,
            maximum_interval=timedelta(seconds=route.retries.max_interval_seconds),
            maximum_attempts=route.retries.max_attempts,
            non_retryable_error_types=list(route.retries.non_retryable_error_codes),
        )

    def _execute_kwargs_for_route(self, route: TemporalActivityRoute) -> dict[str, Any]:
        return {
            "task_queue": route.task_queue,
            "start_to_close_timeout": timedelta(
                seconds=route.timeouts.start_to_close_seconds
            ),
            "schedule_to_close_timeout": timedelta(
                seconds=route.timeouts.schedule_to_close_seconds
            ),
            "retry_policy": self._retry_policy_for_route(route),
        }

    def _get_logger(self) -> logging.LoggerAdapter | logging.Logger:
        try:
            info = workflow.info()
        except Exception:
            logging.getLogger(__name__).exception("Error getting workflow info in _get_logger")
            return logging.getLogger(__name__)

        extra = {
            "workflow_id": getattr(info, "workflow_id", "unknown"),
            "run_id": getattr(info, "run_id", "unknown"),
            "task_queue": getattr(info, "task_queue", "unknown"),
        }

        logger_to_use = workflow.logger
        if not hasattr(logger_to_use, "isEnabledFor"):
            logger_to_use = logging.getLogger(__name__)

        try:
            logger_to_use.isEnabledFor(logging.INFO)
            return logging.LoggerAdapter(logger_to_use, extra=extra)
        except Exception:
            logging.getLogger(__name__).exception("Error checking logger capabilities in _get_logger")
            return logging.LoggerAdapter(logging.getLogger(__name__), extra=extra)

    def __init__(self) -> None:
        self._manifest_ref: Optional[str] = None
        self._plan_ref: Optional[str] = None
        self._summary_ref: Optional[str] = None

    @workflow.run
    async def run(self, input_payload: dict[str, Any]) -> ManifestIngestWorkflowOutput:
        self._get_logger().info("Starting MoonMind.ManifestIngest workflow")
        self._manifest_ref = input_payload.get("manifest_ref")

        if not self._manifest_ref:
            raise exceptions.ApplicationError(
                "manifest_ref is required", non_retryable=True
            )

        # 1. Compile Manifest
        compile_route = DEFAULT_ACTIVITY_CATALOG.resolve_activity("manifest.compile")
        compile_result = await workflow.execute_activity(
            "manifest.compile",
            {
                "principal": "system",
                "manifest_ref": self._manifest_ref,
                "action": input_payload.get("action", "apply"),
                "options": input_payload.get("options", {}),
                "requested_by": {"type": "system", "id": "temporal"},
                "execution_policy": {},
            },
            **self._execute_kwargs_for_route(compile_route),
        )

        plan_ref = (
            compile_result.get("plan_ref")
            if isinstance(compile_result, dict)
            else getattr(compile_result, "plan_ref", None)
        )
        manifest_digest = (
            compile_result.get("manifest_digest")
            if isinstance(compile_result, dict)
            else getattr(compile_result, "manifest_digest", None)
        )
        if plan_ref:
            self._plan_ref = plan_ref

        # 2. Write Summary
        summary_route = DEFAULT_ACTIVITY_CATALOG.resolve_activity(
            "manifest.write_summary"
        )
        summary_result = await workflow.execute_activity(
            "manifest.write_summary",
            {
                "principal": "system",
                "workflow_id": workflow.info().workflow_id,
                "state": "executing",
                "phase": "compiled",
                "manifest_ref": self._manifest_ref,
                "plan_ref": self._plan_ref,
            },
            **self._execute_kwargs_for_route(summary_route),
        )

        # summary_result is a tuple of (summary_ref, run_index_ref)
        if (
            summary_result
            and isinstance(summary_result, (list, tuple))
            and len(summary_result) > 0
        ):
            self._summary_ref = summary_result[0]
        elif isinstance(summary_result, dict) and "summary_ref" in summary_result:
            self._summary_ref = summary_result["summary_ref"]

        return {
            "status": "success",
            "manifest_digest": manifest_digest,
            "plan_ref": self._plan_ref,
            "summary_ref": self._summary_ref,
        }
