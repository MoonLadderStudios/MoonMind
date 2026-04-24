"""Typed execution helpers for Temporal activities."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import timedelta
from typing import Any, Literal, overload

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.workflow import ActivityCancellationType

from moonmind.schemas.temporal_activity_models import (
    AgentRuntimeCancelInput,
    AgentRuntimeFetchResultInput,
    AgentRuntimeStatusInput,
    ArtifactReadInput,
    ArtifactWriteCompleteInput,
    ExternalAgentRunInput,
    PlanGenerateInput,
)
from moonmind.schemas.agent_runtime_models import (
    AgentExecutionRequest,
    AgentRunHandle,
    AgentRunResult,
    AgentRunStatus,
)
from moonmind.workflows.temporal.activity_runtime import PlanGenerateActivityResult
from moonmind.workflows.temporal.artifacts import ArtifactRef

@overload
async def execute_typed_activity(
    activity: Literal["artifact.read"],
    arg: Mapping[str, Any] | ArtifactReadInput,
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
    arg: Mapping[str, Any] | ArtifactWriteCompleteInput,
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
    activity: Literal["integration.jules.start", "integration.codex_cloud.start"],
    arg: AgentExecutionRequest,
    *,
    task_queue: str | None = None,
    start_to_close_timeout: timedelta | None = None,
    schedule_to_close_timeout: timedelta | None = None,
    heartbeat_timeout: timedelta | None = None,
    retry_policy: RetryPolicy | None = None,
    cancellation_type: ActivityCancellationType | None = None,
    summary: str | Mapping[str, Any] | None = None,
) -> AgentRunHandle:
    pass

@overload
async def execute_typed_activity(
    activity: Literal["integration.jules.status", "integration.codex_cloud.status"],
    arg: ExternalAgentRunInput,
    *,
    task_queue: str | None = None,
    start_to_close_timeout: timedelta | None = None,
    schedule_to_close_timeout: timedelta | None = None,
    heartbeat_timeout: timedelta | None = None,
    retry_policy: RetryPolicy | None = None,
    cancellation_type: ActivityCancellationType | None = None,
    summary: str | Mapping[str, Any] | None = None,
) -> AgentRunStatus:
    pass

@overload
async def execute_typed_activity(
    activity: Literal[
        "integration.jules.fetch_result",
        "integration.codex_cloud.fetch_result",
        "agent_runtime.publish_artifacts",
    ],
    arg: ExternalAgentRunInput | AgentRunResult,
    *,
    task_queue: str | None = None,
    start_to_close_timeout: timedelta | None = None,
    schedule_to_close_timeout: timedelta | None = None,
    heartbeat_timeout: timedelta | None = None,
    retry_policy: RetryPolicy | None = None,
    cancellation_type: ActivityCancellationType | None = None,
    summary: str | Mapping[str, Any] | None = None,
) -> AgentRunResult:
    pass

@overload
async def execute_typed_activity(
    activity: Literal["integration.jules.cancel", "integration.codex_cloud.cancel"],
    arg: ExternalAgentRunInput,
    *,
    task_queue: str | None = None,
    start_to_close_timeout: timedelta | None = None,
    schedule_to_close_timeout: timedelta | None = None,
    heartbeat_timeout: timedelta | None = None,
    retry_policy: RetryPolicy | None = None,
    cancellation_type: ActivityCancellationType | None = None,
    summary: str | Mapping[str, Any] | None = None,
) -> AgentRunStatus:
    pass

@overload
async def execute_typed_activity(
    activity: Literal["agent_runtime.status"],
    arg: AgentRuntimeStatusInput,
    *,
    task_queue: str | None = None,
    start_to_close_timeout: timedelta | None = None,
    schedule_to_close_timeout: timedelta | None = None,
    heartbeat_timeout: timedelta | None = None,
    retry_policy: RetryPolicy | None = None,
    cancellation_type: ActivityCancellationType | None = None,
    summary: str | Mapping[str, Any] | None = None,
) -> AgentRunStatus:
    pass

@overload
async def execute_typed_activity(
    activity: Literal["agent_runtime.fetch_result"],
    arg: AgentRuntimeFetchResultInput,
    *,
    task_queue: str | None = None,
    start_to_close_timeout: timedelta | None = None,
    schedule_to_close_timeout: timedelta | None = None,
    heartbeat_timeout: timedelta | None = None,
    retry_policy: RetryPolicy | None = None,
    cancellation_type: ActivityCancellationType | None = None,
    summary: str | Mapping[str, Any] | None = None,
) -> AgentRunResult:
    pass

@overload
async def execute_typed_activity(
    activity: Literal["agent_runtime.cancel"],
    arg: AgentRuntimeCancelInput,
    *,
    task_queue: str | None = None,
    start_to_close_timeout: timedelta | None = None,
    schedule_to_close_timeout: timedelta | None = None,
    heartbeat_timeout: timedelta | None = None,
    retry_policy: RetryPolicy | None = None,
    cancellation_type: ActivityCancellationType | None = None,
    summary: str | Mapping[str, Any] | None = None,
) -> AgentRunStatus:
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
    summary: str | Mapping[str, Any] | None = None,
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
    summary: str | Mapping[str, Any] | None = None,
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
        "summary": summary,
    }
    filtered_kwargs = {k: v for k, v in kwargs.items() if v is not None}

    return await workflow.execute_activity(activity, arg, **filtered_kwargs)
