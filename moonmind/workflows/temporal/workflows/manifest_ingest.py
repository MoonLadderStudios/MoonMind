import logging
from datetime import timedelta
from typing import Any, Optional, TypedDict

from temporalio import exceptions, workflow
from temporalio.common import RetryPolicy

DEFAULT_ACTIVITY_RETRY_POLICY = RetryPolicy(
    initial_interval=timedelta(seconds=5),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(minutes=1),
    maximum_attempts=5,
)

WORKFLOW_TASK_QUEUE = "mm.workflow"

WORKFLOW_NAME = "MoonMind.ManifestIngest"


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
    def _get_logger(self) -> logging.LoggerAdapter | logging.Logger:
        try:
            info = workflow.info()
        except Exception:
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
            start_to_close_timeout=timedelta(minutes=5),
            task_queue=WORKFLOW_TASK_QUEUE,
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
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
            start_to_close_timeout=timedelta(minutes=5),
            task_queue=WORKFLOW_TASK_QUEUE,
            retry_policy=DEFAULT_ACTIVITY_RETRY_POLICY,
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
