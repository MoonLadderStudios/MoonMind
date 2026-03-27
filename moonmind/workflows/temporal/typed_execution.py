"""Typed execution helpers for Temporal activities."""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Literal, overload

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.workflow import ActivityCancellationType

from moonmind.schemas.temporal_activity_models import (
    ArtifactReadInput,
    ArtifactReadOutput,
    ArtifactWriteCompleteInput,
)


@overload
async def execute_typed_activity(
    activity: Literal["artifact.read"],
    arg: ArtifactReadInput,
    *,
    task_queue: str | None = None,
    start_to_close_timeout: timedelta | None = None,
    schedule_to_close_timeout: timedelta | None = None,
    heartbeat_timeout: timedelta | None = None,
    retry_policy: RetryPolicy | None = None,
    cancellation_type: ActivityCancellationType | None = None,
) -> ArtifactReadOutput: ...


@overload
async def execute_typed_activity(
    activity: Literal["artifact.write_complete"],
    arg: ArtifactWriteCompleteInput,
    *,
    task_queue: str | None = None,
    start_to_close_timeout: timedelta | None = None,
    schedule_to_close_timeout: timedelta | None = None,
    heartbeat_timeout: timedelta | None = None,
    retry_policy: RetryPolicy | None = None,
    cancellation_type: ActivityCancellationType | None = None,
) -> Any: ...


@overload
async def execute_typed_activity(
    activity: str,
    arg: Any,
    *,
    task_queue: str | None = None,
    start_to_close_timeout: timedelta | None = None,
    schedule_to_close_timeout: timedelta | None = None,
    heartbeat_timeout: timedelta | None = None,
    retry_policy: RetryPolicy | None = None,
    cancellation_type: ActivityCancellationType | None = None,
) -> Any: ...


async def execute_typed_activity(
    activity: str,
    arg: Any,
    *,
    task_queue: str | None = None,
    start_to_close_timeout: timedelta | None = None,
    schedule_to_close_timeout: timedelta | None = None,
    heartbeat_timeout: timedelta | None = None,
    retry_policy: RetryPolicy | None = None,
    cancellation_type: ActivityCancellationType | None = None,
) -> Any:
    """A statically typed facade for workflow.execute_activity.

    This wraps temporalio's untyped execute_activity but provides
    specific overloads for strictly typed activity inputs and outputs.
    """
    kwargs: dict[str, Any] = {}
    if task_queue is not None:
        kwargs["task_queue"] = task_queue
    if start_to_close_timeout is not None:
        kwargs["start_to_close_timeout"] = start_to_close_timeout
    if schedule_to_close_timeout is not None:
        kwargs["schedule_to_close_timeout"] = schedule_to_close_timeout
    if heartbeat_timeout is not None:
        kwargs["heartbeat_timeout"] = heartbeat_timeout
    if retry_policy is not None:
        kwargs["retry_policy"] = retry_policy
    if cancellation_type is not None:
        kwargs["cancellation_type"] = cancellation_type

    return await workflow.execute_activity(activity, arg, **kwargs)
