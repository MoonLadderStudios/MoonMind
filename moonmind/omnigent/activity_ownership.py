"""Distinguish revoked Activity delivery from cancellation of the user's work.

Temporal owns delivery retry. A worker that loses delivery authority must not
stop a provider session or release its credentials; the durable binding and
bridge receipt remain the owners until a retry or fenced janitor takes over.
"""

from temporalio import activity


def current_delivery_owner() -> dict[str, str] | None:
    """Supply server-owned ancestry at the runtime composition boundary."""
    if not activity.in_activity():
        return None
    info = activity.info()
    return {
        "namespace": info.namespace,
        "workflowId": info.workflow_id,
        "runId": info.workflow_run_id,
    }


def delivery_was_revoked() -> bool:
    """Read the SDK's cancellation reason, never infer it from an error string."""
    if not activity.in_activity():
        return False
    details = activity.cancellation_details()
    return bool(
        details
        and not details.cancel_requested
        and (
            details.worker_shutdown
            or details.timed_out
            or details.paused
            or details.reset
            or details.not_found
        )
    )
