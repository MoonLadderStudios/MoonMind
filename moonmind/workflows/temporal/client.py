"""Temporal client helpers for manifest-ingest execution bootstrap."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Protocol

from temporalio.client import Client, WorkflowExecutionDescription
from temporalio.exceptions import WorkflowAlreadyStartedError

from api_service.db.models import TemporalExecutionRecord
from moonmind.config.settings import settings
from moonmind.schemas.manifest_ingest_models import ManifestNodeModel, RequestedByModel
from moonmind.workflows.temporal.workers import (
    WORKFLOW_FLEET,
    TemporalWorkerTopology,
    describe_configured_worker,
)

if TYPE_CHECKING:
    from moonmind.workflows.temporal.service import TemporalExecutionService

MANIFEST_CHILD_PARENT_CLOSE_POLICY = "REQUEST_CANCEL"

# All MoonMind-owned Temporal task queues.  Used to scope Visibility queries
# so that drain metrics and batch signals only target our own workflows.
_MOONMIND_TASK_QUEUES: tuple[str, ...] = (
    "mm.workflow",
    "mm.activity.artifacts",
    "mm.activity.llm",
    "mm.activity.sandbox",
    "mm.activity.integrations",
    "mm.activity.agent_runtime",
)


def _is_rpc_status(exc: BaseException, status_name: str) -> bool:
    """Check whether *exc* is a Temporal ``RPCError`` with the given gRPC status.

    Falls back to string matching if the gRPC/Temporal imports are unavailable
    or the exception type doesn't match.
    """
    try:
        from temporalio.service import RPCError
        from grpc import StatusCode

        if isinstance(exc, RPCError):
            return exc.status == getattr(StatusCode, status_name, None)
    except ImportError:
        pass  # gRPC/Temporal SDK not available; fall through to string match
    # Fallback: string match on the exception message.
    return status_name.lower().replace("_", " ") in str(exc).lower()


@dataclass(frozen=True, slots=True)
class WorkflowStartResult:
    """Result of starting a Temporal workflow."""

    workflow_id: str
    run_id: str


async def get_temporal_client(address: str, namespace: str) -> Client:
    """Connect to and return a Temporal client."""

    return await Client.connect(address, namespace=namespace)


async def fetch_workflow_execution(
    client: Client, workflow_id: str
) -> WorkflowExecutionDescription:
    """Fetch the latest execution description for a workflow."""

    handle = client.get_workflow_handle(workflow_id)
    return await handle.describe()


class TemporalClientAdapter:
    """Adapter for communicating with the Temporal server."""

    def __init__(self, client: Client | None = None) -> None:
        """Initialize the Temporal client adapter."""
        self._client = client
        self._lock = asyncio.Lock()
        self._workflow_topology: TemporalWorkerTopology | None = None

    async def get_client(self) -> Client:
        """Get or initialize the Temporal client connection."""
        if self._client is not None:
            return self._client
        async with self._lock:
            if self._client is None:
                self._client = await Client.connect(
                    settings.temporal.address,
                    namespace=settings.temporal.namespace,
                )
            return self._client

    def _get_task_queue(self) -> str:
        """Resolve the task queue for the given workflow type."""
        if self._workflow_topology is None:
            self._workflow_topology = describe_configured_worker(
                temporal_settings=settings.temporal.model_copy(
                    update={"worker_fleet": WORKFLOW_FLEET}
                )
            )
        return self._workflow_topology.task_queues[0]

    async def start_workflow(
        self,
        *,
        workflow_type: str,
        workflow_id: str,
        input_args: Mapping[str, Any] | None = None,
        memo: Mapping[str, Any] | None = None,
        search_attributes: Mapping[str, Any] | None = None,
        start_delay: timedelta | None = None,
    ) -> WorkflowStartResult:
        """Start a new Temporal workflow.

        Args:
            start_delay: Optional delay before the workflow is dispatched
                to a worker. The workflow is created immediately (visible in
                Visibility) but task dispatch is deferred by the specified
                duration.
        """
        client = await self.get_client()

        task_queue = self._get_task_queue()

        args = [input_args] if input_args is not None else []

        formatted_search_attributes = {}
        if search_attributes:
            formatted_search_attributes = {
                k: v if isinstance(v, list) else [v]
                for k, v in search_attributes.items()
            }

        if start_delay is not None:
            scheduled_for = datetime.now(timezone.utc) + start_delay
            formatted_search_attributes["mm_scheduled_for"] = [scheduled_for]

        if not formatted_search_attributes:
            formatted_search_attributes = None

        start_kwargs: dict[str, Any] = {
            "id": workflow_id,
            "task_queue": task_queue,
            "memo": memo,
            "search_attributes": formatted_search_attributes,
        }
        if start_delay is not None:
            start_kwargs["start_delay"] = start_delay

        try:
            handle = await client.start_workflow(
                workflow_type,
                *args,
                **start_kwargs,
            )
            return WorkflowStartResult(
                workflow_id=handle.id,
                run_id=handle.result_run_id,
            )
        except WorkflowAlreadyStartedError as err:
            return WorkflowStartResult(
                workflow_id=err.workflow_id,
                run_id=err.run_id,
            )

    async def get_workflow_handle(self, workflow_id: str) -> Any:
        """Get a handle to an existing workflow execution."""
        client = await self.get_client()
        return client.get_workflow_handle(workflow_id)

    async def cancel_workflow(self, workflow_id: str) -> None:
        """Cancel an existing workflow execution."""
        handle = await self.get_workflow_handle(workflow_id)
        await handle.cancel()

    async def terminate_workflow(self, workflow_id: str, *, reason: str) -> None:
        """Force terminate an existing workflow execution."""
        handle = await self.get_workflow_handle(workflow_id)
        await handle.terminate(reason=reason)

    async def signal_workflow(
        self, workflow_id: str, signal_name: str, arg: Any = None
    ) -> None:
        """Send a signal to an existing workflow execution."""
        handle = await self.get_workflow_handle(workflow_id)
        if arg is not None:
            await handle.signal(signal_name, arg)
        else:
            await handle.signal(signal_name)

    async def update_workflow(
        self, workflow_id: str, update_name: str, arg: Any = None
    ) -> Any:
        """Execute an update on an existing workflow execution."""
        handle = await self.get_workflow_handle(workflow_id)
        if arg is not None:
            return await handle.execute_update(update_name, arg)
        return await handle.execute_update(update_name)

    async def describe_workflow(self, workflow_id: str) -> WorkflowExecutionDescription:
        """Describe an existing workflow execution."""
        handle = await self.get_workflow_handle(workflow_id)
        return await handle.describe()

    # --- Worker Pause: Temporal Visibility drain metrics (DOC-REQ-002) ---

    async def get_drain_metrics(
        self,
        *,
        task_queues: Sequence[str] | None = None,
    ) -> dict[str, int]:
        """Query Temporal Visibility for running workflow counts.

        Returns a dict with ``running``, ``queued`` (pending), and ``stale_running``
        counts derived from ``ListWorkflowExecutions`` with
        ``ExecutionStatus="Running"``.

        ``task_queues``: optional list of task queues to filter.  When omitted the
        ``task_queues``: optional list of task queues to filter.  Defaults to
        ``_MOONMIND_TASK_QUEUES`` so queries are scoped to MoonMind workflows.
        """
        client = await self.get_client()

        if task_queues is None:
            task_queues = _MOONMIND_TASK_QUEUES

        visibility_filter = 'ExecutionStatus="Running"'
        if task_queues:
            quoted = ", ".join(f'"{tq}"' for tq in task_queues)
            visibility_filter += f" AND TaskQueue IN ({quoted})"

        count_result = await client.count_workflows(query=visibility_filter)
        running_count = count_result.count

        return {
            "running": running_count,
            "queued": 0,  # Temporal doesn't distinguish "queued" from "running"
            "stale_running": 0,
        }

    # --- Worker Pause: Batch Signals for Quiesce mode (DOC-REQ-003) ---

    async def send_batch_pause_signal(
        self,
        *,
        task_queues: Sequence[str] | None = None,
    ) -> int:
        """Send a ``pause`` signal to all running workflows (Quiesce mode).

        Returns the number of workflows signaled.
        """
        return await self._send_signal_to_running_workflows(
            signal_name="pause",
            task_queues=task_queues,
        )

    async def send_batch_resume_signal(
        self,
        *,
        task_queues: Sequence[str] | None = None,
    ) -> int:
        """Send a ``resume`` signal to all running workflows.

        Returns the number of workflows signaled.
        """
        return await self._send_signal_to_running_workflows(
            signal_name="resume",
            task_queues=task_queues,
        )

    async def _send_signal_to_running_workflows(
        self,
        *,
        signal_name: str,
        task_queues: Sequence[str] | None = None,
    ) -> int:
        """Iterate running workflows via Visibility and signal each one.

        This approach works with all Temporal server versions.  When the
        Temporal Batch Operations API becomes available in the Python SDK,
        this can be replaced with a single ``StartBatchOperation`` call.
        """
        client = await self.get_client()

        if task_queues is None:
            task_queues = _MOONMIND_TASK_QUEUES

        visibility_filter = 'ExecutionStatus="Running"'
        if task_queues:
            quoted = ", ".join(f'"{tq}"' for tq in task_queues)
            visibility_filter += f" AND TaskQueue IN ({quoted})"

        signaled = 0
        _log = logging.getLogger(__name__)
        _sem = asyncio.Semaphore(50)

        async def _signal_one(wf_id: str) -> bool:
            async with _sem:
                try:
                    handle = client.get_workflow_handle(wf_id)
                    await handle.signal(signal_name)
                    return True
                except Exception:
                    _log.warning(
                        "Failed to signal workflow %s with %s",
                        wf_id,
                        signal_name,
                        exc_info=True,
                    )
                    return False

        tasks: list[asyncio.Task[bool]] = []
        async for execution in client.list_workflows(query=visibility_filter):
            tasks.append(asyncio.create_task(_signal_one(execution.id)))

        results = await asyncio.gather(*tasks)
        signaled = sum(1 for ok in results if ok)
        return signaled

    # --- Temporal Schedule CRUD ---

    async def create_schedule(
        self,
        *,
        definition_id: Any,
        cron_expression: str,
        timezone: str = "UTC",
        overlap_mode: str = "skip",
        catchup_mode: str = "last",
        jitter_seconds: int = 0,
        enabled: bool = True,
        note: str = "",
        workflow_type: str,
        workflow_input: Mapping[str, Any] | None = None,
        memo: Mapping[str, Any] | None = None,
        search_attributes: Mapping[str, Any] | None = None,
    ) -> str:
        """Create a Temporal Schedule for recurring workflow execution.

        Returns the schedule ID (``mm-schedule:{definition_id}``).

        Raises:
            ScheduleAlreadyExistsError: if the schedule already exists.
            ScheduleOperationError: on unexpected SDK failure.
        """
        from uuid import UUID as _UUID

        from temporalio.client import (
            Schedule,
            ScheduleActionStartWorkflow,
        )

        from moonmind.workflows.temporal.schedule_errors import (
            ScheduleAlreadyExistsError,
            ScheduleOperationError,
        )
        from moonmind.workflows.temporal.schedule_mapping import (
            build_schedule_policy,
            build_schedule_spec,
            build_schedule_state,
            make_schedule_id,
            make_workflow_id_template,
        )

        client = await self.get_client()
        definition_uuid = _UUID(str(definition_id))
        schedule_id = make_schedule_id(definition_uuid)
        task_queue = self._get_task_queue()

        args = [workflow_input] if workflow_input is not None else []

        formatted_sa = {}
        if search_attributes:
            formatted_sa = {
                k: v if isinstance(v, list) else [v]
                for k, v in search_attributes.items()
            }

        formatted_sa["mm_scheduled_for"] = ["{{.ScheduleTime}}"]

        if not formatted_sa:
            formatted_sa = None

        try:
            handle = await client.create_schedule(
                schedule_id,
                Schedule(
                    action=ScheduleActionStartWorkflow(
                        workflow_type,
                        *args,
                        id=make_workflow_id_template(definition_uuid),
                        task_queue=task_queue,
                        memo=memo,
                        **({"untyped_search_attributes": formatted_sa} if formatted_sa else {}),
                    ),
                    spec=build_schedule_spec(
                        cron=cron_expression,
                        timezone=timezone,
                        jitter_seconds=jitter_seconds,
                    ),
                    policy=build_schedule_policy(
                        overlap_mode=overlap_mode,
                        catchup_mode=catchup_mode,
                    ),
                    state=build_schedule_state(
                        enabled=enabled,
                        note=note,
                    ),
                ),
            )
            return handle.id
        except Exception as exc:
            if _is_rpc_status(exc, "ALREADY_EXISTS"):
                raise ScheduleAlreadyExistsError(
                    f"Schedule {schedule_id} already exists"
                ) from exc
            raise ScheduleOperationError(
                f"Failed to create schedule {schedule_id}: {exc}"
            ) from exc

    async def _get_schedule_handle(self, definition_id: Any) -> Any:
        """Resolve a ``ScheduleHandle`` for the given definition, or raise.

        Probes with ``describe()`` to verify the schedule exists.
        Returns ``(handle, description)``.
        """
        from uuid import UUID as _UUID

        from moonmind.workflows.temporal.schedule_errors import (
            ScheduleNotFoundError,
            ScheduleOperationError,
        )
        from moonmind.workflows.temporal.schedule_mapping import make_schedule_id

        client = await self.get_client()
        schedule_id = make_schedule_id(_UUID(str(definition_id)))
        try:
            handle = client.get_schedule_handle(schedule_id)
            # Probe the handle to verify existence.
            description = await handle.describe()
            return handle, description
        except Exception as exc:
            if _is_rpc_status(exc, "NOT_FOUND"):
                raise ScheduleNotFoundError(
                    f"Schedule {schedule_id} not found"
                ) from exc
            raise ScheduleOperationError(
                f"Failed to get schedule handle {schedule_id}: {exc}"
            ) from exc

    async def describe_schedule(self, *, definition_id: Any) -> Any:
        """Describe a Temporal Schedule (next runs, recent actions, state).

        Returns:
            The ``ScheduleDescription`` from the Temporal SDK.

        Raises:
            ScheduleNotFoundError: if the schedule does not exist.
            ScheduleOperationError: on unexpected SDK failure.
        """
        _handle, description = await self._get_schedule_handle(definition_id)
        return description

    async def update_schedule(
        self,
        *,
        definition_id: Any,
        cron_expression: str | None = None,
        timezone: str | None = None,
        overlap_mode: str | None = None,
        catchup_mode: str | None = None,
        jitter_seconds: int | None = None,
        enabled: bool | None = None,
        note: str | None = None,
    ) -> None:
        """Update the spec, policy, or state of an existing schedule.

        Only provided (non-``None``) fields are modified; everything else
        is preserved from the current schedule description.

        Raises:
            ScheduleNotFoundError: if the schedule does not exist.
            ScheduleOperationError: on unexpected SDK failure.
        """
        from moonmind.workflows.temporal.schedule_errors import ScheduleOperationError
        from moonmind.workflows.temporal.schedule_mapping import (
            build_schedule_policy,
            build_schedule_spec,
            build_schedule_state,
        )

        handle, _desc = await self._get_schedule_handle(definition_id)

        async def _do_update(input: Any) -> Any:  # noqa: A002
            schedule = input.schedule

            if cron_expression is not None or timezone is not None or jitter_seconds is not None:
                current_cron = (
                    schedule.spec.cron_expressions[0]
                    if schedule.spec.cron_expressions
                    else "0 0 * * *"
                )
                current_tz = schedule.spec.time_zone_name or "UTC"
                current_jitter = int(schedule.spec.jitter.total_seconds()) if schedule.spec.jitter else 0

                schedule.spec = build_schedule_spec(
                    cron=cron_expression if cron_expression is not None else current_cron,
                    timezone=timezone if timezone is not None else current_tz,
                    jitter_seconds=jitter_seconds if jitter_seconds is not None else current_jitter,
                )

            if overlap_mode is not None or catchup_mode is not None:
                current_overlap = (
                    schedule.policy.overlap.name.lower()
                    if schedule.policy and schedule.policy.overlap
                    else "skip"
                )
                _CATCHUP_LAST_SECONDS = timedelta(minutes=15).total_seconds()
                current_catchup_seconds = (
                    schedule.policy.catchup_window.total_seconds()
                    if schedule.policy and schedule.policy.catchup_window
                    else _CATCHUP_LAST_SECONDS
                )
                # Reverse-map current catchup seconds to mode
                if current_catchup_seconds == 0:
                    current_catchup_mode = "none"
                elif current_catchup_seconds <= _CATCHUP_LAST_SECONDS:
                    current_catchup_mode = "last"
                else:
                    current_catchup_mode = "all"

                schedule.policy = build_schedule_policy(
                    overlap_mode=overlap_mode if overlap_mode is not None else current_overlap,
                    catchup_mode=catchup_mode if catchup_mode is not None else current_catchup_mode,
                )

            if enabled is not None or note is not None:
                current_paused = schedule.state.paused if schedule.state else False
                current_note = schedule.state.note if schedule.state else ""
                schedule.state = build_schedule_state(
                    enabled=not current_paused if enabled is None else enabled,
                    note=note if note is not None else current_note,
                )

            return schedule

        try:
            await handle.update(_do_update)
        except Exception as exc:
            raise ScheduleOperationError(
                f"Failed to update schedule: {exc}"
            ) from exc

    async def pause_schedule(self, *, definition_id: Any) -> None:
        """Pause a Temporal Schedule (no new runs until unpaused).

        Raises:
            ScheduleNotFoundError: if the schedule does not exist.
            ScheduleOperationError: on unexpected SDK failure.
        """
        from moonmind.workflows.temporal.schedule_errors import ScheduleOperationError

        handle, _desc = await self._get_schedule_handle(definition_id)
        try:
            await handle.pause()
        except Exception as exc:
            raise ScheduleOperationError(
                f"Failed to pause schedule: {exc}"
            ) from exc

    async def unpause_schedule(self, *, definition_id: Any) -> None:
        """Unpause a Temporal Schedule.

        Raises:
            ScheduleNotFoundError: if the schedule does not exist.
            ScheduleOperationError: on unexpected SDK failure.
        """
        from moonmind.workflows.temporal.schedule_errors import ScheduleOperationError

        handle, _desc = await self._get_schedule_handle(definition_id)
        try:
            await handle.unpause()
        except Exception as exc:
            raise ScheduleOperationError(
                f"Failed to unpause schedule: {exc}"
            ) from exc

    async def trigger_schedule(self, *, definition_id: Any) -> None:
        """Trigger an immediate run of the schedule.

        Raises:
            ScheduleNotFoundError: if the schedule does not exist.
            ScheduleOperationError: on unexpected SDK failure.
        """
        from moonmind.workflows.temporal.schedule_errors import ScheduleOperationError

        handle, _desc = await self._get_schedule_handle(definition_id)
        try:
            await handle.trigger()
        except Exception as exc:
            raise ScheduleOperationError(
                f"Failed to trigger schedule: {exc}"
            ) from exc

    async def delete_schedule(self, *, definition_id: Any) -> None:
        """Delete a Temporal Schedule.

        Raises:
            ScheduleNotFoundError: if the schedule does not exist.
            ScheduleOperationError: on unexpected SDK failure.
        """
        from moonmind.workflows.temporal.schedule_errors import ScheduleOperationError

        handle, _desc = await self._get_schedule_handle(definition_id)
        try:
            await handle.delete()
        except Exception as exc:
            raise ScheduleOperationError(
                f"Failed to delete schedule: {exc}"
            ) from exc


class TemporalExecutionCreatorProtocol(Protocol):
    """Protocol for the execution service used by manifest child scheduling."""

    async def create_execution(
        self,
        *,
        workflow_type: str,
        owner_id: Any,
        title: str | None,
        input_artifact_ref: str | None,
        plan_artifact_ref: str | None,
        manifest_artifact_ref: str | None,
        failure_policy: str | None,
        initial_parameters: dict[str, Any] | None,
        idempotency_key: str | None,
        repository: str | None = ...,
        integration: str | None = ...,
        summary: str | None = ...,
    ) -> TemporalExecutionRecord:
        pass


@dataclass(frozen=True, slots=True)
class ManifestChildWorkflowStart:
    """Child workflow metadata captured for one scheduled manifest node."""

    node_id: str
    workflow_id: str
    run_id: str
    workflow_type: str
    parent_close_policy: str = MANIFEST_CHILD_PARENT_CLOSE_POLICY


def build_manifest_child_parameters(
    *,
    parent_execution: TemporalExecutionRecord,
    node: ManifestNodeModel,
    requested_by: RequestedByModel | Mapping[str, object],
) -> dict[str, object]:
    """Return the child-run lineage payload for one manifest node."""

    requested_by_model = RequestedByModel.model_validate(requested_by)
    return {
        "manifestIngestWorkflowId": parent_execution.workflow_id,
        "manifestIngestRunId": parent_execution.run_id,
        "manifestArtifactRef": parent_execution.manifest_ref,
        "nodeId": node.node_id,
        "requestedBy": requested_by_model.model_dump(by_alias=True),
        "runtimeHints": {
            "manifestNodeState": node.state,
            "workflowType": "MoonMind.Run",
        },
        "parentClosePolicy": MANIFEST_CHILD_PARENT_CLOSE_POLICY,
    }


async def start_manifest_child_runs(
    *,
    execution_service: TemporalExecutionService,
    parent_execution: TemporalExecutionRecord,
    requested_by: RequestedByModel | Mapping[str, object],
    nodes: Sequence[ManifestNodeModel],
    limit: int,
) -> list[ManifestChildWorkflowStart]:
    """Start bounded child runs for ready manifest nodes."""

    starts: list[ManifestChildWorkflowStart] = []
    for node in list(nodes)[: max(0, limit)]:
        child = await execution_service.create_execution(
            workflow_type="MoonMind.Run",
            owner_id=parent_execution.owner_id,
            title=node.title or f"Manifest node {node.node_id}",
            input_artifact_ref=parent_execution.manifest_ref,
            plan_artifact_ref=parent_execution.plan_ref,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters=build_manifest_child_parameters(
                parent_execution=parent_execution,
                node=node,
                requested_by=requested_by,
            ),
            idempotency_key=(
                f"{parent_execution.workflow_id}:{parent_execution.run_id}:{node.node_id}"
            ),
            _skip_pause_guard=True,
        )
        starts.append(
            ManifestChildWorkflowStart(
                node_id=node.node_id,
                workflow_id=child.workflow_id,
                run_id=child.run_id,
                workflow_type=child.workflow_type.value,
            )
        )
    return starts
