"""Typed execution helpers for Temporal activities."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import timedelta
from typing import TYPE_CHECKING, Any, Literal, overload

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.workflow import ActivityCancellationType

from moonmind.schemas.temporal_activity_models import PlanGenerateInput
from moonmind.workflows.temporal.artifacts import ArtifactRef

if TYPE_CHECKING:
    from moonmind.workflows.temporal.activity_runtime import PlanGenerateActivityResult


@overload
async def execute_typed_activity(
    activity: Literal["artifact.read"],
    arg: Mapping[str, Any],
    *,
    task_queue: str | None = None,
    start_to_close_timeout: timedelta | None = None,
    schedule_to_close_timeout: timedelta | None = None,
    heartbeat_timeout: timedelta | None = None,
    retry_policy: RetryPolicy | None = None,
    cancellation_type: ActivityCancellationType | None = None,
) -> bytes:
    pass


@overload
async def execute_typed_activity(
    activity: Literal["artifact.write_complete"],
    arg: Mapping[str, Any],
    *,
    task_queue: str | None = None,
    start_to_close_timeout: timedelta | None = None,
    schedule_to_close_timeout: timedelta | None = None,
    heartbeat_timeout: timedelta | None = None,
    retry_policy: RetryPolicy | None = None,
    cancellation_type: ActivityCancellationType | None = None,
) -> ArtifactRef:
    pass


@overload
async def execute_typed_activity(
    activity: Literal["plan.generate"],
    arg: Mapping[str, Any] | PlanGenerateInput,
    *,
    task_queue: str | None = None,
    start_to_close_timeout: timedelta | None = None,
    schedule_to_close_timeout: timedelta | None = None,
    heartbeat_timeout: timedelta | None = None,
    retry_policy: RetryPolicy | None = None,
    cancellation_type: ActivityCancellationType | None = None,
) -> PlanGenerateActivityResult:
    pass


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
) -> Any:
    pass


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
    kwargs = {
        "task_queue": task_queue,
        "start_to_close_timeout": start_to_close_timeout,
        "schedule_to_close_timeout": schedule_to_close_timeout,
        "heartbeat_timeout": heartbeat_timeout,
        "retry_policy": retry_policy,
        "cancellation_type": cancellation_type,
    }
    filtered_kwargs = {k: v for k, v in kwargs.items() if v is not None}

    return await workflow.execute_activity(activity, arg, **filtered_kwargs)
