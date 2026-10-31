"""Temporal client helpers for manifest-ingest execution bootstrap."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

from temporalio.api.common.v1 import Payload, WorkflowExecution
from temporalio.api.enums.v1 import ResetReapplyExcludeType
from temporalio.api.workflowservice.v1 import (
    DescribeNamespaceRequest,
    ResetWorkflowExecutionRequest,
)
from temporalio.client import (
    Client,
    WorkflowExecutionDescription,
    WorkflowHistory,
    WorkflowUpdateStage,
)
from temporalio.common import (
    SearchAttributeKey,
    SearchAttributePair,
    TypedSearchAttributes,
    WorkflowIDReusePolicy,
)
from temporalio.exceptions import WorkflowAlreadyStartedError

from api_service.db.models import TemporalExecutionRecord
from moonmind.config.settings import settings
from moonmind.observability import temporal_tracing_interceptors
from moonmind.schemas.container_job_models import (
    ContainerJobWorkflowInput,
    container_job_workflow_id,
)
from moonmind.gates.checkpoint_compat_drain import (
    CheckpointCompatDrainObservations,
    collect_checkpoint_compat_drain_observations,
)
from moonmind.workflows.temporal.activity_catalog import (
    ARTIFACTS_TASK_QUEUE,
    get_workflow_poll_task_queues,
    publication_task_queue,
)
from moonmind.omnigent.git_identity import admitted_publication_git_identity
from moonmind.workflows.temporal.data_converter import MOONMIND_TEMPORAL_DATA_CONVERTER
from moonmind.workflows.temporal.hard_switch_cutover import (
    RENAMED_USER_WORKFLOW_TYPE,
    admit_user_workflow_input,
    resolve_user_workflow_start_contract,
)
from moonmind.workflows.temporal.workers import (
    WORKFLOW_FLEET,
    TemporalWorkerTopology,
    describe_configured_worker,
)

logger = logging.getLogger(__name__)


def decode_schedule_action_value(value: Any) -> Any:
    """Read SDK-described action payloads without mutating their wire form."""
    if isinstance(value, Payload):
        return MOONMIND_TEMPORAL_DATA_CONVERTER.payload_converter.from_payloads([value])[0]
    return value


# All MoonMind-owned Temporal task queues.  Used to scope Visibility queries
# so that drain metrics and batch signals only target our own workflows.
_MOONMIND_TASK_QUEUES: tuple[str, ...] = (
    "mm.workflow",
    "mm.workflow.merge_automation",
    "mm.activity.artifacts",
    "mm.activity.llm",
    "mm.activity.sandbox",
    "mm.activity.integrations",
    "mm.activity.agent_runtime",
)


def _workflow_control_task_queues() -> tuple[str, ...]:
    return tuple(dict.fromkeys((*_MOONMIND_TASK_QUEUES, *get_workflow_poll_task_queues())))


MANAGED_SESSION_RECONCILE_SCHEDULE_ID = "mm-operational:managed-session-reconcile"
MANAGED_SESSION_RECONCILE_WORKFLOW_ID_BASE = (
    "mm-operational:managed-session-reconcile"
)
MANAGED_RUNTIME_WORKSPACE_CLEANUP_SCHEDULE_ID = (
    "mm-operational:managed-runtime-workspace-cleanup"
)
MANAGED_RUNTIME_WORKSPACE_CLEANUP_WORKFLOW_ID_BASE = (
    "mm-operational:managed-runtime-workspace-cleanup"
)
OMNIGENT_OAUTH_HOST_JANITOR_SCHEDULE_ID = "omnigent-oauth-host-janitor"
OMNIGENT_OAUTH_HOST_JANITOR_WORKFLOW_ID_BASE = "omnigent-oauth-host-janitor-run"
GITHUB_ISSUE_RECONCILE_SCHEDULE_ID = "mm-operational:github-issue-reconcile"
GITHUB_ISSUE_RECONCILE_WORKFLOW_ID_BASE = "mm-operational:github-issue-reconcile"
ALLOW_LIVE_TEMPORAL_IN_TESTS_ENV = "MOONMIND_ALLOW_LIVE_TEMPORAL_IN_TESTS"
_WORKFLOW_UPDATE_ACCEPTED_TIMEOUT = timedelta(seconds=10)
_WORKFLOW_CONTROL_CONCURRENCY = 10
# Enumeration budgets: dispatch workers bound concurrent tasks, so target
# discovery gets its own explicit bounds. Checkpoint cadence avoids one
# ever-growing audit payload rewrite per target; the limit caps memory,
# response size, total duration and write volume per request.
_WORKFLOW_CONTROL_ENUMERATION_CHECKPOINT_EVERY = 100
_WORKFLOW_CONTROL_ENUMERATION_LIMIT = 1000
_SINGLE_VALUE_KEYWORD_LIST_SEARCH_ATTRIBUTES = frozenset(
    {
        "mm_target_runtime",
        "mm_target_skill",
        "mm_title",
    }
)
# Text attributes hold space-separated opaque tokens matched as exact terms
# (MoonLadderStudios/MoonMind#4640: recorded Provider Profile membership).
_TEXT_SEARCH_ATTRIBUTES = frozenset({"mm_provider_profile"})

def _is_rpc_status(exc: BaseException, status_name: str) -> bool:
    """Check whether *exc* is a Temporal ``RPCError`` with the given gRPC status.

    Falls back to string matching if the gRPC/Temporal imports are unavailable
    or the exception type doesn't match.
    """
    try:
        from temporalio.service import RPCError, RPCStatusCode

        if isinstance(exc, RPCError):
            return exc.status == getattr(RPCStatusCode, status_name, None)
    except ImportError:
        pass  # gRPC/Temporal SDK not available; fall through to string match
    # Fallback: string match on the exception message.
    return status_name.lower().replace("_", " ") in str(exc).lower()

def _build_typed_search_attributes(
    search_attributes: Mapping[str, Any] | None,
) -> TypedSearchAttributes | None:
    """Convert legacy dict-based search attributes to the typed object model."""
    if not search_attributes:
        return None

    pairs: list[SearchAttributePair] = []
    for key, raw_value in search_attributes.items():
        # Callers currently wrap everything in a list for legacy compatibility.
        if isinstance(raw_value, Sequence) and not isinstance(
            raw_value, (str, bytes, bytearray)
        ):
            values = list(raw_value)
        else:
            values = [raw_value]

        if not values or values[0] is None:
            continue

        # Temporal only supports lists for KeywordList. All other types must be unwrapped.
        if all(isinstance(v, str) for v in values):
            if key in _TEXT_SEARCH_ATTRIBUTES:
                key_type = SearchAttributeKey.for_text(key)
                pairs.append(SearchAttributePair(key_type, " ".join(values)))
            elif key in _SINGLE_VALUE_KEYWORD_LIST_SEARCH_ATTRIBUTES:
                key_type = SearchAttributeKey.for_keyword_list(key)
                pairs.append(SearchAttributePair(key_type, values))
            elif len(values) > 1:
                key_type = SearchAttributeKey.for_keyword_list(key)
                pairs.append(SearchAttributePair(key_type, values))
            else:
                key_type = SearchAttributeKey.for_keyword(key)
                pairs.append(SearchAttributePair(key_type, values[0]))
        elif all(isinstance(v, bool) for v in values):
            key_type = SearchAttributeKey.for_bool(key)
            pairs.append(SearchAttributePair(key_type, values[0]))
        elif all(isinstance(v, int) for v in values):
            key_type = SearchAttributeKey.for_int(key)
            pairs.append(SearchAttributePair(key_type, values[0]))
        elif all(isinstance(v, float) for v in values):
            key_type = SearchAttributeKey.for_float(key)
            pairs.append(SearchAttributePair(key_type, values[0]))
        elif all(isinstance(v, datetime) for v in values):
            key_type = SearchAttributeKey.for_datetime(key)
            pairs.append(SearchAttributePair(key_type, values[0]))
        else:
            # Fallback to keyword for unknown types, ensuring we pass a string.
            key_type = SearchAttributeKey.for_keyword(key)
            pairs.append(SearchAttributePair(key_type, str(values[0])))

    return TypedSearchAttributes(pairs)

@dataclass(frozen=True, slots=True)
class WorkflowStartResult:
    """Result of starting a Temporal workflow."""

    workflow_id: str
    run_id: str

@dataclass(frozen=True, slots=True)
class ScheduleTriggerResult:
    """Best-effort metadata for a workflow started by a Temporal Schedule."""

    scheduled_at: datetime | None = None
    started_at: datetime | None = None
    workflow_id: str | None = None
    run_id: str | None = None
    disposition: str = "pending"
    message: str | None = None


@dataclass(frozen=True, slots=True)
class SemanticCompatDrainObservations:
    """One semantic adapter's retained consumers from a complete history scan.

    Closed histories remain reset consumers while Temporal retains them. Counts
    are conservative: an unrecorded patch cannot prove an old branch unused.
    Missing observations never permit removal, and this receipt deletes nothing.
    """

    open_histories: int | None = None
    queued_starts: int | None = None
    saved_inputs: int | None = None
    supported_resets: int | None = None
    errors: tuple[str, ...] = ()

    @property
    def may_remove(self) -> bool:
        return not self.errors and all(
            type(value) is int and value == 0
            for value in (
                self.open_histories, self.queued_starts,
                self.saved_inputs, self.supported_resets,
            )
        )


def _schedule_object_value(source: object, *keys: str) -> Any:
    if source is None:
        return None
    if isinstance(source, Mapping):
        for key in keys:
            if key in source:
                return source[key]
        return None
    for key in keys:
        value = getattr(source, key, None)
        if value is not None:
            return value
    return None

def _schedule_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
        return _schedule_datetime(parsed)
    return None


def _latest_schedule_trigger_result(
    description: object,
    *,
    not_before: datetime | None = None,
    scheduled_at: datetime | None = None,
) -> ScheduleTriggerResult:
    """Extract the latest start-workflow action from a schedule description."""

    info = _schedule_object_value(description, "info")
    recent_actions = (
        _schedule_object_value(info, "recent_actions", "recentActions") or []
    )
    try:
        action_results = list(recent_actions)
    except TypeError:
        return ScheduleTriggerResult()

    def _sort_key(action_result: object) -> datetime:
        return (
            _schedule_datetime(
                _schedule_object_value(action_result, "actual_time", "actualTime")
            )
            or _schedule_datetime(
                _schedule_object_value(action_result, "started_at", "startedAt")
            )
            or _schedule_datetime(
                _schedule_object_value(action_result, "schedule_time", "scheduleTime")
            )
            or _schedule_datetime(
                _schedule_object_value(action_result, "scheduled_at", "scheduledAt")
            )
            or datetime.min.replace(tzinfo=timezone.utc)
        )

    threshold = _schedule_datetime(not_before) if not_before is not None else None
    requested_time = _schedule_datetime(scheduled_at)
    for action_result in sorted(action_results, key=_sort_key, reverse=True):
        scheduled_at = (
            _schedule_datetime(
                _schedule_object_value(action_result, "schedule_time", "scheduleTime")
            )
            or _schedule_datetime(
                _schedule_object_value(action_result, "scheduled_at", "scheduledAt")
            )
        )
        started_at = (
            _schedule_datetime(
                _schedule_object_value(action_result, "actual_time", "actualTime")
            )
            or _schedule_datetime(
                _schedule_object_value(action_result, "started_at", "startedAt")
            )
        )
        if requested_time is not None and scheduled_at != requested_time:
            continue
        action_time = started_at or scheduled_at
        if (
            threshold is not None
            and action_time is not None
            and action_time < threshold
        ):
            continue
        action = _schedule_object_value(
            action_result,
            "start_workflow_result",
            "startWorkflowResult",
            "action",
        )
        workflow_id = _schedule_object_value(action, "workflow_id", "workflowId")
        run_id = _schedule_object_value(
            action,
            "run_id",
            "runId",
            "first_execution_run_id",
            "firstExecutionRunId",
        )
        if workflow_id or run_id:
            return ScheduleTriggerResult(
                scheduled_at=scheduled_at,
                started_at=started_at,
                workflow_id=str(workflow_id) if workflow_id else None,
                run_id=str(run_id) if run_id else None,
                disposition="started" if workflow_id and run_id else "pending",
            )
    return ScheduleTriggerResult()


async def get_temporal_client(address: str, namespace: str) -> Client:
    """Connect to and return a Temporal client."""

    return await Client.connect(
        address,
        namespace=namespace,
        data_converter=MOONMIND_TEMPORAL_DATA_CONVERTER,
        interceptors=temporal_tracing_interceptors(),
    )

async def fetch_workflow_execution(
    client: Client, workflow_id: str
) -> WorkflowExecutionDescription:
    """Fetch the latest execution description for a workflow."""

    handle = client.get_workflow_handle(workflow_id)
    return await handle.describe()

async def query_workflow(
    client: Client,
    workflow_id: str,
    query_name: str,
    arg: Any = None,
) -> Any:
    """Execute a query against the latest run for ``workflow_id``."""

    handle = client.get_workflow_handle(workflow_id)
    if arg is None:
        return await handle.query(query_name)
    return await handle.query(query_name, arg)

_CHECKPOINT_BRANCH_TURN_WORKFLOW_TYPE = "MoonMind.CheckpointBranchTurn"
_CHECKPOINT_PERSISTENCE_ACTIVITY_PREFIX = "checkpoint_branch.turn."
_ACTIVITY_CLOSE_EVENT_FIELDS = (
    "activity_task_completed_event_attributes",
    "activity_task_failed_event_attributes",
    "activity_task_timed_out_event_attributes",
    "activity_task_canceled_event_attributes",
)


def _old_queue_checkpoint_persistence(events: Sequence[Any]) -> tuple[int, int]:
    """Return (scheduled, unclosed) checkpoint persistence off the artifacts queue."""

    scheduled: set[int] = set()
    closed: set[int] = set()
    for event in events:
        if event.HasField("activity_task_scheduled_event_attributes"):
            attrs = event.activity_task_scheduled_event_attributes
            if (
                attrs.activity_type.name.startswith(
                    _CHECKPOINT_PERSISTENCE_ACTIVITY_PREFIX
                )
                and attrs.task_queue.name != ARTIFACTS_TASK_QUEUE
            ):
                scheduled.add(event.event_id)
            continue
        for field_name in _ACTIVITY_CLOSE_EVENT_FIELDS:
            if event.HasField(field_name):
                closed.add(getattr(event, field_name).scheduled_event_id)
                break
    return len(scheduled), len(scheduled - closed)


async def _retained_compat_histories(
    client: Any, query: str,
) -> AsyncIterator[WorkflowHistory]:
    """Shared drain observation route; every history read is pinned to its run."""
    async for execution in client.list_workflows(query=query):
        if not execution.run_id:
            raise ValueError("retained consumer has no observable run identity")
        yield await client.get_workflow_handle(
            execution.id, run_id=execution.run_id,
        ).fetch_history()


async def _semantic_compat_consumers(
    history: WorkflowHistory, converter: Any,
) -> tuple[dict[str, tuple[bool, bool]], bool]:
    """Read recorded decisions and saved payloads without executing a workflow."""
    from moonmind.workflows.skills.approval_policy import has_retained_step_gate_shape
    from moonmind.workflows.temporal.workflows.run import (
        NATIVE_PR_BRANCH_DEFAULTS_PATCH,
        RUN_ACCOUNT_INTENT_NORMALIZATION_PATCH,
        RUN_TERMINAL_GATE_PUBLISHED_HEAD_FEASIBILITY_PATCH,
    )

    markers: dict[str, int] = {}
    agent_commands: list[int] = []
    publication_commands: list[int] = []
    retained_gate = saved_gate = False
    first_task_completed = False
    payload_fields = (
        ("workflow_execution_started_event_attributes", "input"),
        ("activity_task_scheduled_event_attributes", "input"),
        ("activity_task_completed_event_attributes", "result"),
        ("start_child_workflow_execution_initiated_event_attributes", "input"),
        ("child_workflow_execution_completed_event_attributes", "result"),
        ("workflow_execution_signaled_event_attributes", "input"),
        ("workflow_execution_continued_as_new_event_attributes", "input"),
    )
    for event in history.events:
        if event.HasField("workflow_task_completed_event_attributes"):
            first_task_completed = True
        if event.HasField("marker_recorded_event_attributes"):
            marker = event.marker_recorded_event_attributes
            if marker.marker_name == "core_patch":
                patch = json.loads(marker.details["patch-data"].payloads[0].data)
                markers.setdefault(patch["id"], event.event_id)
        if event.HasField("start_child_workflow_execution_initiated_event_attributes"):
            attrs = event.start_child_workflow_execution_initiated_event_attributes
            if attrs.workflow_type.name == "MoonMind.AgentRun":
                agent_commands.append(event.event_id)
        if event.HasField("activity_task_scheduled_event_attributes"):
            if event.activity_task_scheduled_event_attributes.activity_type.name == "repo.create_pr":
                publication_commands.append(event.event_id)
        for field_name, payload_name in payload_fields:
            if not event.HasField(field_name):
                continue
            payloads = getattr(getattr(event, field_name), payload_name).payloads
            values = await converter.decode(payloads)
            legacy = any(has_retained_step_gate_shape(value) for value in values)
            retained_gate |= legacy
            if field_name in {
                "workflow_execution_started_event_attributes",
                "workflow_execution_continued_as_new_event_attributes",
            }:
                saved_gate |= legacy
        if event.HasField("workflow_execution_update_accepted_event_attributes"):
            request = event.workflow_execution_update_accepted_event_attributes.accepted_request
            values = await converter.decode(request.input.args.payloads)
            retained_gate |= any(
                has_retained_step_gate_shape(value) for value in values
            )

    def needs_patch(patch: str, commands: Sequence[int]) -> bool:
        marker = markers.get(patch)
        # A late marker cannot erase earlier commands that used the old branch.
        return marker is None or any(command < marker for command in commands)

    return {
        "gate_aliases": (retained_gate, saved_gate),
        "profile_selection": (
            needs_patch(RUN_ACCOUNT_INTENT_NORMALIZATION_PATCH, agent_commands), False,
        ),
        "native_pr_branches": (
            needs_patch(NATIVE_PR_BRANCH_DEFAULTS_PATCH, publication_commands)
            or needs_patch(RUN_TERMINAL_GATE_PUBLISHED_HEAD_FEASIBILITY_PATCH, publication_commands),
            False,
        ),
    }, not first_task_completed


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
        if (
            os.getenv("PYTEST_CURRENT_TEST")
            and os.getenv(ALLOW_LIVE_TEMPORAL_IN_TESTS_ENV) != "1"
        ):
            raise RuntimeError(
                "Refusing to open an implicit live Temporal connection while pytest "
                f"is running. Pass an injected Temporal client/adapter, or set "
                f"{ALLOW_LIVE_TEMPORAL_IN_TESTS_ENV}=1 for tests that intentionally "
                "use a live Temporal server."
            )
        async with self._lock:
            if self._client is None:
                self._client = await Client.connect(
                    settings.temporal.address,
                    namespace=settings.temporal.namespace,
                    data_converter=MOONMIND_TEMPORAL_DATA_CONVERTER,
                    interceptors=temporal_tracing_interceptors(),
                )
            return self._client

    def _get_task_queue(
        self,
        workflow_type: str | None = None,
        *,
        task_queue: str | None = None,
        memo: Mapping[str, Any] | None = None,
    ) -> str:
        """Resolve the task queue for the given workflow type."""
        if not task_queue:
            if workflow_type == RENAMED_USER_WORKFLOW_TYPE:
                task_queue = resolve_user_workflow_start_contract(settings.temporal).task_queue
            else:
                if self._workflow_topology is None:
                    self._workflow_topology = describe_configured_worker(
                        temporal_settings=settings.temporal.model_copy(
                            update={"worker_fleet": WORKFLOW_FLEET}
                        )
                    )
                task_queue = self._workflow_topology.task_queues[0]
        return (
            publication_task_queue(task_queue)
            if admitted_publication_git_identity(memo or {}) is not None
            else task_queue
        )

    def resolve_workflow_task_queue(self, workflow_type: str | None) -> str:
        """Resolve the task queue a schedule/start action should use."""

        return self._get_task_queue(workflow_type)

    async def start_workflow(
        self,
        *,
        workflow_type: str,
        workflow_id: str,
        input_args: Mapping[str, Any] | None = None,
        memo: Mapping[str, Any] | None = None,
        search_attributes: Mapping[str, Any] | None = None,
        start_delay: timedelta | None = None,
        task_queue: str | None = None,
        id_reuse_policy: WorkflowIDReusePolicy | None = None,
    ) -> WorkflowStartResult:
        """Start a new Temporal workflow.

        Args:
            start_delay: Optional delay before the workflow is dispatched
                to a worker. The workflow is created immediately (visible in
                Visibility) but task dispatch is deferred by the specified
                duration.
        """
        client = await self.get_client()

        task_queue = self._get_task_queue(workflow_type, task_queue=task_queue, memo=memo)

        input_args = admit_user_workflow_input(workflow_type, input_args)
        args = [input_args] if input_args is not None else []

        formatted_search_attributes = {
            k: v if isinstance(v, list) else [v]
            for k, v in (search_attributes or {}).items()
        }

        if start_delay is not None:
            scheduled_for = datetime.now(timezone.utc) + start_delay
            formatted_search_attributes["mm_scheduled_for"] = [scheduled_for]

        if not formatted_search_attributes:
            formatted_search_attributes = None

        search_attributes_typed = _build_typed_search_attributes(formatted_search_attributes)

        start_kwargs: dict[str, Any] = {
            "id": workflow_id,
            "task_queue": task_queue,
            "memo": memo,
            "search_attributes": search_attributes_typed,
        }
        if start_delay is not None:
            start_kwargs["start_delay"] = start_delay
        if id_reuse_policy is not None:
            start_kwargs["id_reuse_policy"] = id_reuse_policy
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

    async def start_container_job(
        self, request: ContainerJobWorkflowInput | Mapping[str, Any]
    ) -> WorkflowStartResult:
        """Start or attach to one asynchronous ``MoonMind.ContainerJob``."""

        model = (
            request
            if isinstance(request, ContainerJobWorkflowInput)
            else ContainerJobWorkflowInput.model_validate(request)
        )
        return await self.start_workflow(
            workflow_type="MoonMind.ContainerJob",
            workflow_id=container_job_workflow_id(model.job_id),
            input_args=model.model_dump(mode="json", by_alias=True),
            id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
        )

    async def signal_container_job_cancel(self, job_id: str) -> None:
        """Signal the canonical container-job workflow to stop."""
        handle = await self.get_workflow_handle(container_job_workflow_id(job_id))
        await handle.signal("cancel")

    async def get_workflow_handle(self, workflow_id: str, *, run_id: str | None = None) -> Any:
        """Get a handle to an existing workflow execution."""
        client = await self.get_client()
        if run_id is not None:
            return client.get_workflow_handle(workflow_id, run_id=run_id)
        return client.get_workflow_handle(workflow_id)

    async def cancel_workflow(
        self, workflow_id: str, *, run_id: str | None = None
    ) -> None:
        """Cancel an existing workflow execution."""
        handle = (
            await self.get_workflow_handle(workflow_id, run_id=run_id)
            if run_id is not None
            else await self.get_workflow_handle(workflow_id)
        )
        await handle.cancel()

    async def terminate_workflow(
        self, workflow_id: str, *, reason: str, run_id: str | None = None
    ) -> None:
        """Force terminate an existing workflow execution."""
        handle = await self.get_workflow_handle(workflow_id, run_id=run_id)
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

    async def send_reschedule_signal(self, workflow_id: str, scheduled_for: datetime) -> None:
        """Send a reschedule signal to a delayed workflow."""
        handle = await self.get_workflow_handle(workflow_id)
        await handle.signal("reschedule", scheduled_for.isoformat())

    async def update_workflow(
        self,
        workflow_id: str,
        update_name: str,
        arg: Any = None,
        *,
        run_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> Any:
        """Execute an update on an existing workflow execution."""
        handle = (
            await self.get_workflow_handle(workflow_id, run_id=run_id)
            if run_id is not None
            else await self.get_workflow_handle(workflow_id)
        )
        options = {"id": idempotency_key} if idempotency_key is not None else {}
        if arg is not None:
            return await handle.execute_update(update_name, arg, **options)
        return await handle.execute_update(update_name, **options)

    async def namespace_retention_seconds(self) -> float:
        """Read the server's current history retention before uncertain admission recovery."""
        client = await self.get_client()
        description = await client.workflow_service.describe_namespace(
            DescribeNamespaceRequest(namespace=client.namespace)
        )
        return (
            description.config.workflow_execution_retention_ttl.ToTimedelta().total_seconds()
        )

    async def describe_workflow(
        self, workflow_id: str, *, run_id: str | None = None
    ) -> WorkflowExecutionDescription:
        """Describe an existing workflow execution."""
        handle = await self.get_workflow_handle(workflow_id, run_id=run_id)
        return await handle.describe()

    async def fetch_workflow_history(
        self, workflow_id: str, *, run_id: str
    ) -> WorkflowHistory:
        """Fetch the complete recorded history of one exact run."""
        handle = await self.get_workflow_handle(workflow_id, run_id=run_id)
        return await handle.fetch_history()

    async def reset_workflow_execution(
        self,
        workflow_id: str,
        *,
        run_id: str,
        workflow_task_finish_event_id: int,
        reason: str,
        request_id: str,
    ) -> str:
        """Reset one run to a workflow task and return the new run's id.

        Signals and updates recorded after the reset point belong to the
        replaced attempt, so they are not reapplied to the new run. Temporal
        deduplicates repeated requests with the same ``request_id``.
        """
        client = await self.get_client()
        response = await client.workflow_service.reset_workflow_execution(
            ResetWorkflowExecutionRequest(
                namespace=client.namespace,
                workflow_execution=WorkflowExecution(
                    workflow_id=workflow_id, run_id=run_id
                ),
                reason=reason,
                workflow_task_finish_event_id=workflow_task_finish_event_id,
                request_id=request_id,
                reset_reapply_exclude_types=[
                    ResetReapplyExcludeType.RESET_REAPPLY_EXCLUDE_TYPE_SIGNAL,
                    ResetReapplyExcludeType.RESET_REAPPLY_EXCLUDE_TYPE_UPDATE,
                ],
            )
        )
        return str(response.run_id)

    async def read_workflow_start_input(
        self, workflow_id: str, *, run_id: str
    ) -> Mapping[str, Any]:
        """Read admitted inputs from the exact run, never from mutable Visibility."""
        handle = await self.get_workflow_handle(workflow_id, run_id=run_id)
        client = await self.get_client()
        async for event in handle.fetch_history_events(page_size=1):
            if event.event_id != 1 or not event.HasField(
                "workflow_execution_started_event_attributes"
            ):
                break
            values = await client.data_converter.decode(
                event.workflow_execution_started_event_attributes.input.payloads
            )
            if len(values) == 1 and isinstance(values[0], Mapping):
                return values[0]
            break
        raise ValueError("The source run has no readable workflow start input")

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
            task_queues = _workflow_control_task_queues()

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

    # --- Checkpoint compat drain observation (MoonLadderStudios/MoonMind#3949) ---

    async def observe_checkpoint_compat_drain(
        self,
    ) -> CheckpointCompatDrainObservations:
        """Collect the three checkpoint compat drain dimensions from Temporal.

        A ``MoonMind.CheckpointBranchTurn`` history is a compat consumer when
        it scheduled ``checkpoint_branch.turn.*`` persistence on any queue
        other than the artifacts queue: pre-cutover runs keep that route on
        replay, while new runs record the cutover patch before their first
        persistence call (so a missing marker alone is not a consumer).
        Running consumers count as open pre-cutover histories and their
        unclosed old-queue persistence activities as pending old-queue
        tasks; closed consumers Visibility still returns remain resettable
        and count as supported resets. A failed listing or unreadable
        history leaves its dimensions ``None`` (unknown), never zero.
        """

        client = await self.get_client()
        base_query = f'WorkflowType="{_CHECKPOINT_BRANCH_TURN_WORKFLOW_TYPE}"'
        running = await self._scan_checkpoint_compat_consumers(
            client, f'{base_query} AND ExecutionStatus="Running"'
        )
        closed = await self._scan_checkpoint_compat_consumers(
            client, f'{base_query} AND ExecutionStatus!="Running"'
        )
        return collect_checkpoint_compat_drain_observations(
            open_pre_cutover_histories=None if running is None else running[0],
            pending_old_queue_tasks=None if running is None else running[1],
            supported_resets_pending=None if closed is None else closed[0],
        )

    async def observe_semantic_compat_drain(
        self,
    ) -> dict[str, SemanticCompatDrainObservations]:
        """Observe removal eligibility for the moved gate/profile/PR adapters.

        Reuse checkpoint drainage's Visibility/history route. All open parents
        can still receive a saved gate result; queued and failed-first-task work
        retains every adapter. Closed aliases and pre-patch histories retain
        reset support until history retention drains them. No fixture result,
        missing marker, failed listing or unreadable payload proves drainage.
        """
        families = ("gate_aliases", "profile_selection", "native_pr_branches")
        counts = {family: [0, 0, 0, 0] for family in families}
        errors: list[str] = []
        try:
            client = await self.get_client()
        except Exception as exc:
            return {
                family: SemanticCompatDrainObservations(errors=(f"connection: {exc}",))
                for family in families
            }
        for running in (True, False):
            query = (
                f'WorkflowType="{RENAMED_USER_WORKFLOW_TYPE}" AND '
                f'ExecutionStatus{"=" if running else "!="}"Running"'
            )
            try:
                async for history in _retained_compat_histories(client, query):
                    consumers, queued = await _semantic_compat_consumers(
                        history, client.data_converter,
                    )
                    for family, (retained, saved) in consumers.items():
                        counts[family][0] += int(running and (retained or family == "gate_aliases"))
                        counts[family][1] += int(running and queued)
                        counts[family][2] += int(saved)
                        counts[family][3] += int(not running and (retained or queued))
            except Exception as exc:
                # Partial scans are not zero. Preserve the original diagnostic.
                errors.append(f"{query}: {type(exc).__name__}: {exc}")
                logger.warning(
                    "Semantic compat drain observation failed for %s", query,
                    exc_info=True,
                )
        if errors:
            return {
                family: SemanticCompatDrainObservations(errors=tuple(errors))
                for family in families
            }
        # A paused durable schedule can still reintroduce a saved alias after
        # history retention expires. Describe its stored action; listing
        # metadata alone does not expose the admitted arguments.
        try:
            from temporalio.client import ScheduleActionStartWorkflow
            from moonmind.workflows.skills.approval_policy import has_retained_step_gate_shape

            async for entry in await client.list_schedules():
                description = await client.get_schedule_handle(entry.id).describe()
                action = description.schedule.action
                if (
                    isinstance(action, ScheduleActionStartWorkflow)
                    and action.workflow == RENAMED_USER_WORKFLOW_TYPE
                ):
                    payloads = [arg for arg in action.args if isinstance(arg, Payload)]
                    values = [arg for arg in action.args if not isinstance(arg, Payload)]
                    values.extend(await client.data_converter.decode(payloads))
                    counts["gate_aliases"][2] += int(any(
                        has_retained_step_gate_shape(value) for value in values
                    ))
        except Exception as exc:
            errors.append(f"saved schedule inputs: {type(exc).__name__}: {exc}")
            logger.warning("Semantic compat saved-input observation failed", exc_info=True)
        return {
            family: SemanticCompatDrainObservations(
                open_histories=values[0], queued_starts=values[1],
                saved_inputs=None if errors and family == "gate_aliases" else values[2],
                supported_resets=values[3],
                errors=tuple(errors) if family == "gate_aliases" else (),
            )
            for family, values in counts.items()
        }

    @staticmethod
    async def _scan_checkpoint_compat_consumers(
        client: Any, query: str
    ) -> tuple[int, int] | None:
        consumers = 0
        outstanding = 0
        try:
            async for history in _retained_compat_histories(client, query):
                scheduled, pending = _old_queue_checkpoint_persistence(
                    history.events
                )
                if scheduled:
                    consumers += 1
                    outstanding += pending
        except Exception:
            logger.warning(
                "Checkpoint compat drain observation failed for %s; "
                "reporting the dimension as unknown",
                query,
                exc_info=True,
            )
            return None
        return consumers, outstanding

    # --- Worker Pause/Resume: Batch Updates for Quiesce mode (DOC-REQ-003) ---

    async def send_batch_pause_update(self, *, task_queues=None, request_id=None, batch=None, on_progress=None):
        return await self._send_update_to_running_workflows(
            update_name="Pause", task_queues=task_queues, request_id=request_id,
            batch=batch, on_progress=on_progress,
        )

    async def send_batch_resume_update(self, *, task_queues=None, request_id=None, batch=None, on_progress=None):
        return await self._send_update_to_running_workflows(
            update_name="Resume", task_queues=task_queues, request_id=request_id,
            batch=batch, on_progress=on_progress,
        )

    async def _send_update_to_running_workflows(
        self, *, update_name: str, task_queues=None, request_id=None,
        batch=None, on_progress=None,
    ):
        """Persist target intent before dispatch, then observe actual safe points.

        A durable caller supplies on_progress and retries the same batch after a
        restart. IDs are pinned to runs; ACCEPTED never means quiesced. Query
        failure retains unknown evidence and never destroys successful targets.
        Enumeration is paged and resumable: partial target lists checkpoint with
        an explicit cursor/policy marker, targets deduplicate against already
        persisted identities, and a truncated or failed scan never reports a
        fully confirmed result. Every observed outcome carries a truthful
        disposition: ``already_terminal`` (positive close evidence only),
        ``unsupported`` (no control protocol), or ``superseded`` (run or
        generation replaced) instead of collapsing to ``unknown``.
        """
        import hashlib
        from uuid import uuid4
        from moonmind.schemas.workflow_control_models import WorkflowControlBatch, WorkflowControlTarget

        client = await self.get_client()
        result = batch or WorkflowControlBatch(requestId=request_id or str(uuid4()), action=update_name)
        if result.action != update_name or (request_id and result.request_id != request_id):
            raise ValueError("control request identity mismatch")
        # The callback commonly owns one AsyncSession. Serialize commits while
        # independent targets make bounded parallel progress through Temporal.
        persistence_lock = asyncio.Lock()

        async def persist(target=None, state=None, reason=None):
            async with persistence_lock:
                if target is not None:
                    target.state, target.reason = state, reason
                if on_progress is not None:
                    await on_progress(result.model_copy(deep=True))
        if not result.enumerated:
            queues = _workflow_control_task_queues() if task_queues is None else task_queues
            # Queue membership alone does not imply the Pause/Resume/query
            # protocol. Operator and manifest workflows share these queues.
            query = (
                'ExecutionStatus="Running"'
                f' AND WorkflowType="{RENAMED_USER_WORKFLOW_TYPE}"'
            )
            if queues:
                query += ' AND TaskQueue IN (' + ', '.join(f'"{queue}"' for queue in queues) + ')'
            # Record the selection policy: a point-in-time Visibility scan,
            # never an atomic system snapshot. Retries resume from the
            # already-persisted targets below instead of starting over.
            result.enumeration_policy = (
                f"visibility point-in-time scan: {query} (non-atomic)"
            )
            seen = {(target.workflow_id, target.run_id) for target in result.targets}
            targets = list(result.targets)
            if len(targets) >= _WORKFLOW_CONTROL_ENUMERATION_LIMIT:
                # A resumed pass must not grow the capped audit payload one
                # run per read: the budget applies to the accumulated total,
                # not to newly appended targets in this pass.
                result.targets = targets[:_WORKFLOW_CONTROL_ENUMERATION_LIMIT]
                result.enumeration_error = "control_enumeration_truncated"
                await persist()
                return result
            try:
                async for execution in client.list_workflows(query=query):
                    identity = (str(execution.id), str(execution.run_id or ""))
                    if identity in seen:
                        continue
                    seen.add(identity)
                    workflow_id, run_id = identity
                    update_id = hashlib.sha256(f"{result.request_id}:{update_name}:{workflow_id}:{run_id}".encode()).hexdigest()
                    targets.append(WorkflowControlTarget(workflowId=workflow_id, runId=run_id, updateId=update_id))
                    result.enumeration_cursor = workflow_id
                    if len(targets) >= _WORKFLOW_CONTROL_ENUMERATION_LIMIT:
                        result.targets = targets[:_WORKFLOW_CONTROL_ENUMERATION_LIMIT]
                        result.enumeration_error = "control_enumeration_truncated"
                        await persist()
                        return result
                    if len(targets) % _WORKFLOW_CONTROL_ENUMERATION_CHECKPOINT_EVERY == 0:
                        result.targets = targets
                        await persist()
            except Exception:
                # Checkpoint the partial target list/cursor so a retry resumes
                # instead of repeating the full scan before any target progresses.
                result.targets = targets
                result.enumeration_error = "control_visibility_unavailable"
                await persist()
                return result
            result.enumeration_error = None
            result.enumeration_cursor = None
            result.targets = targets
            result.enumerated = True
            await persist()
        async def reconcile(target):
            if target.state in WorkflowControlTarget.TERMINAL_STATES:
                return
            if not target.run_id:
                await persist(target, "unknown", "run_identity_unavailable")
                return
            handle = client.get_workflow_handle(target.workflow_id, run_id=target.run_id)
            if target.state in {"requested", "unknown"}:
                try:
                    await handle.start_update(
                        update_name,
                        args=[{"controlGeneration": result.generation}] if result.generation else [],
                        id=target.update_id,
                        wait_for_stage=WorkflowUpdateStage.ACCEPTED,
                        rpc_timeout=_WORKFLOW_UPDATE_ACCEPTED_TIMEOUT,
                    )
                except Exception as exc:
                    await _record_acceptance_failure(handle, target, exc)
                    return
                await persist(target, "accepted", None)
            from temporalio.client import WorkflowUpdateFailedError
            try:
                await handle.get_update_handle(target.update_id).result(rpc_timeout=timedelta(seconds=1))
            except WorkflowUpdateFailedError:
                await persist(target, "failed", "update_failed")
                return
            except Exception:
                await persist(target, "pending", "update_completion_pending")
                return
            try:
                observed = await handle.query("control_state", rpc_timeout=_WORKFLOW_UPDATE_ACCEPTED_TIMEOUT)
                observed_run_id = observed.get("runId") if isinstance(observed, dict) else None
                if not observed_run_id:
                    # No positive evidence of a successor run: a transient or
                    # cross-version malformed response stays retryable instead
                    # of permanently parking the target as superseded.
                    state, reason = "unknown", "control_query_unavailable"
                elif observed_run_id != target.run_id:
                    # The pinned run identity is preserved: a Continue-As-New,
                    # reset, or later execution sharing the workflow id never
                    # inherits this control request.
                    state, reason = "superseded", "run_superseded"
                elif result.generation and observed.get("controlGeneration") != result.generation:
                    # A newer command owns the run; the old observation must
                    # not overwrite current requested state or report confirmed.
                    state, reason = "superseded", "control_generation_superseded"
                elif update_name == "Pause" and observed.get("safePoint") is True:
                    state, reason = "safe_point", None
                elif update_name == "Resume" and observed.get("resumed") is True:
                    state, reason = "resumed", None
                else:
                    state, reason = "pending", "safe_point_pending"
            except Exception as exc:
                message = str(exc).lower()
                if "query" in message and (
                    "unknown" in message
                    or "unregistered" in message
                    or "unsupported" in message
                ):
                    # Only an explicit unknown/unregistered-query error proves
                    # the workflow lacks the control protocol. A generic
                    # NOT_FOUND can mean the pinned execution or namespace is
                    # transiently unavailable, so it stays retryable.
                    state, reason = "unsupported", "control_protocol_unsupported"
                else:
                    state, reason = "unknown", "control_query_unavailable"
            await persist(target, state, reason)

        async def _record_acceptance_failure(handle, target, exc):
            """Map an Update acceptance failure to a truthful disposition.

            Only positive close evidence (a describe showing the pinned run is
            no longer running) becomes ``already_terminal``. A failed or absent
            lookup stays ``unknown``. A closed run is never equated with
            verified physical host cleanup or lease release.
            """
            try:
                description = await handle.describe()
            except Exception:
                await persist(target, "unknown", "update_acceptance_unavailable")
                return
            run_status = getattr(getattr(description, "status", None), "name", "")
            if (
                isinstance(run_status, str)
                and run_status
                and run_status != "RUNNING"
            ):
                await persist(target, "already_terminal", "workflow_already_terminal")
                return
            await persist(target, "unknown", "update_acceptance_unavailable")

        targets = iter(result.targets)

        async def worker():
            for target in targets:
                await reconcile(target)

        # Fixed workers bound task/RPC count as well as concurrency. TaskGroup
        # cancels and joins every worker if durable progress cannot be committed.
        async with asyncio.TaskGroup() as group:
            for _ in range(min(_WORKFLOW_CONTROL_CONCURRENCY, len(result.targets))):
                group.create_task(worker())
        return result

    # --- Temporal Schedule CRUD ---

    async def ensure_managed_session_reconcile_schedule(
        self,
        *,
        cron_expression: str = "*/10 * * * *",
        timezone: str = "UTC",
        enabled: bool = True,
    ) -> str:
        """Create or replace the recurring managed-session reconcile Schedule."""

        from temporalio.client import (
            Schedule,
            ScheduleActionStartWorkflow,
            ScheduleUpdate,
        )

        from moonmind.workflows.temporal.schedule_errors import ScheduleOperationError
        from moonmind.workflows.temporal.schedule_mapping import (
            build_schedule_policy,
            build_schedule_spec,
            build_schedule_state,
        )

        client = await self.get_client()
        schedule = Schedule(
            action=ScheduleActionStartWorkflow(
                "MoonMind.ManagedSessionReconcile",
                # Self-monitoring input for #4226 schedule health: the
                # reconcile activity describes these schedule IDs itself at
                # the activity boundary and durably tracks SkippedOverlap
                # counters/streaks across ticks. The reconcile schedule
                # itself uses OverlapPolicy=Skip, so its own growth proves
                # the operational sweeper is starving.
                {
                    "scheduleIdsToWatch": [
                        MANAGED_SESSION_RECONCILE_SCHEDULE_ID,
                    ]
                },
                id=MANAGED_SESSION_RECONCILE_WORKFLOW_ID_BASE,
                task_queue=self._get_task_queue(),
                typed_search_attributes=_build_typed_search_attributes(
                    {
                        "mm_entry": ["operational"],
                        "mm_state": ["scheduled"],
                    }
                ),
                static_summary="Managed session reconcile",
                static_details=(
                    "Recurring operational sweeper for managed runtime sessions"
                ),
            ),
            spec=build_schedule_spec(
                cron=cron_expression,
                timezone=timezone,
                jitter_seconds=0,
            ),
            policy=build_schedule_policy(
                overlap_mode="skip",
                catchup_mode="last",
            ),
            state=build_schedule_state(
                enabled=enabled,
                note="Managed Codex session reconcile and orphan sweep",
            ),
        )
        try:
            handle = client.get_schedule_handle(MANAGED_SESSION_RECONCILE_SCHEDULE_ID)
            async def _replace_schedule(_: Any) -> ScheduleUpdate:
                return ScheduleUpdate(schedule=schedule)

            await handle.update(_replace_schedule)
            return MANAGED_SESSION_RECONCILE_SCHEDULE_ID
        except Exception as update_exc:
            if not _is_rpc_status(update_exc, "NOT_FOUND") and "not found" not in str(
                update_exc
            ).lower():
                raise ScheduleOperationError(
                    "Failed to update managed session reconcile schedule: "
                    f"{update_exc}"
                ) from update_exc
        try:
            handle = await client.create_schedule(
                MANAGED_SESSION_RECONCILE_SCHEDULE_ID,
                schedule,
            )
            return handle.id
        except Exception as create_exc:
            raise ScheduleOperationError(
                "Failed to create managed session reconcile schedule: "
                f"{create_exc}"
            ) from create_exc

    async def ensure_managed_runtime_workspace_cleanup_schedule(
        self,
        *,
        cron_expression: str = "0 * * * *",
        timezone: str = "UTC",
        enabled: bool = True,
    ) -> str:
        """Create or replace the recurring deployment-storage Schedule."""

        from temporalio.client import (
            Schedule,
            ScheduleActionStartWorkflow,
            ScheduleUpdate,
        )

        from moonmind.workflows.temporal.schedule_errors import ScheduleOperationError
        from moonmind.workflows.temporal.schedule_mapping import (
            build_schedule_policy,
            build_schedule_spec,
            build_schedule_state,
        )

        client = await self.get_client()
        def _build_schedule(*, schedule_enabled: bool) -> Schedule:
            return Schedule(
                action=ScheduleActionStartWorkflow(
                    "MoonMind.ManagedRuntimeWorkspaceCleanup",
                    {},
                    id=MANAGED_RUNTIME_WORKSPACE_CLEANUP_WORKFLOW_ID_BASE,
                    task_queue=self._get_task_queue(),
                    typed_search_attributes=_build_typed_search_attributes(
                        {
                            "mm_entry": ["operational"],
                            "mm_state": ["scheduled"],
                        }
                    ),
                    static_summary="Deployment storage maintenance",
                    static_details=(
                        "Recurring bounded workspace, artifact, and Docker storage maintenance"
                    ),
                ),
                spec=build_schedule_spec(
                    cron=cron_expression,
                    timezone=timezone,
                    jitter_seconds=0,
                ),
                policy=build_schedule_policy(
                    overlap_mode="skip",
                    catchup_mode="last",
                ),
                state=build_schedule_state(
                    enabled=schedule_enabled,
                    note="Deployment storage maintenance",
                ),
            )

        schedule = _build_schedule(schedule_enabled=enabled)
        try:
            handle = client.get_schedule_handle(
                MANAGED_RUNTIME_WORKSPACE_CLEANUP_SCHEDULE_ID
            )

            async def _replace_schedule(input: Any) -> ScheduleUpdate:  # noqa: A002
                del input
                return ScheduleUpdate(
                    schedule=_build_schedule(schedule_enabled=enabled)
                )

            await handle.update(_replace_schedule)
            return MANAGED_RUNTIME_WORKSPACE_CLEANUP_SCHEDULE_ID
        except Exception as update_exc:
            if not _is_rpc_status(update_exc, "NOT_FOUND") and "not found" not in str(
                update_exc
            ).lower():
                raise ScheduleOperationError(
                    "Failed to update managed runtime workspace cleanup schedule: "
                    f"{update_exc}"
                ) from update_exc
        try:
            handle = await client.create_schedule(
                MANAGED_RUNTIME_WORKSPACE_CLEANUP_SCHEDULE_ID,
                schedule,
            )
            return handle.id
        except Exception as create_exc:
            raise ScheduleOperationError(
                "Failed to create managed runtime workspace cleanup schedule: "
                f"{create_exc}"
            ) from create_exc

    async def ensure_omnigent_oauth_host_janitor_schedule(
        self,
        *,
        cron_expression: str = "*/5 * * * *",
        timezone: str = "UTC",
        enabled: bool = True,
    ) -> str:
        """Create or replace the profile-bound OAuth host janitor schedule."""

        from temporalio.client import Schedule, ScheduleActionStartWorkflow, ScheduleUpdate
        from moonmind.workflows.temporal.schedule_errors import ScheduleOperationError
        from moonmind.workflows.temporal.schedule_mapping import (
            build_schedule_policy,
            build_schedule_spec,
            build_schedule_state,
        )

        client = await self.get_client()

        def _schedule() -> Schedule:
            return Schedule(
                action=ScheduleActionStartWorkflow(
                    "MoonMind.OmnigentOAuthHostJanitor",
                    {},
                    id=OMNIGENT_OAUTH_HOST_JANITOR_WORKFLOW_ID_BASE,
                    task_queue=self._get_task_queue(),
                    typed_search_attributes=_build_typed_search_attributes(
                        {"mm_entry": ["operational"], "mm_state": ["scheduled"]}
                    ),
                    static_summary="Omnigent OAuth host janitor",
                    static_details="Reconcile profile-bound OAuth host leases",
                ),
                spec=build_schedule_spec(
                    cron=cron_expression, timezone=timezone, jitter_seconds=0
                ),
                policy=build_schedule_policy(overlap_mode="skip", catchup_mode="last"),
                state=build_schedule_state(
                    enabled=enabled, note="Omnigent OAuth host cleanup"
                ),
            )

        try:
            handle = client.get_schedule_handle(OMNIGENT_OAUTH_HOST_JANITOR_SCHEDULE_ID)

            async def _replace(input: Any) -> ScheduleUpdate:  # noqa: A002
                del input
                return ScheduleUpdate(schedule=_schedule())

            await handle.update(_replace)
            return OMNIGENT_OAUTH_HOST_JANITOR_SCHEDULE_ID
        except Exception as update_exc:
            if not _is_rpc_status(update_exc, "NOT_FOUND") and "not found" not in str(
                update_exc
            ).lower():
                raise ScheduleOperationError(
                    f"Failed to update Omnigent OAuth host janitor schedule: {update_exc}"
                ) from update_exc
        try:
            handle = await client.create_schedule(
                OMNIGENT_OAUTH_HOST_JANITOR_SCHEDULE_ID, _schedule()
            )
            return handle.id
        except Exception as create_exc:
            raise ScheduleOperationError(
                f"Failed to create Omnigent OAuth host janitor schedule: {create_exc}"
            ) from create_exc

    async def ensure_github_issue_reconcile_schedule(
        self,
        *,
        cron_expression: str = "*/5 * * * *",
        timezone: str = "UTC",
        enabled: bool = True,
        repository: str = "",
    ) -> str:
        """Create or replace the recurring GitHub issue reconcile schedule.

        Registers ``MoonMind.GitHubIssueReconcile`` through the production
        Temporal scheduling mechanism so the default five-minute maintenance run
        starts without manual invocation. Skip-on-overlap keeps duplicate
        observations bounded without a global lock.
        """

        from temporalio.client import Schedule, ScheduleActionStartWorkflow, ScheduleUpdate
        from moonmind.workflows.temporal.schedule_errors import ScheduleOperationError
        from moonmind.workflows.temporal.schedule_mapping import (
            build_schedule_policy,
            build_schedule_spec,
            build_schedule_state,
        )

        client = await self.get_client()

        def _schedule() -> Schedule:
            return Schedule(
                action=ScheduleActionStartWorkflow(
                    "MoonMind.GitHubIssueReconcile",
                    {"repository": repository} if repository else {},
                    id=GITHUB_ISSUE_RECONCILE_WORKFLOW_ID_BASE,
                    task_queue=self._get_task_queue(),
                    typed_search_attributes=_build_typed_search_attributes(
                        {"mm_entry": ["operational"], "mm_state": ["scheduled"]}
                    ),
                    static_summary="GitHub issue reconcile",
                    static_details=(
                        "Recurring bounded reconciliation of interrupted issue handoffs"
                    ),
                ),
                spec=build_schedule_spec(
                    cron=cron_expression, timezone=timezone, jitter_seconds=0
                ),
                policy=build_schedule_policy(overlap_mode="skip", catchup_mode="last"),
                state=build_schedule_state(
                    enabled=enabled, note="GitHub issue handoff reconciliation"
                ),
            )

        try:
            handle = client.get_schedule_handle(GITHUB_ISSUE_RECONCILE_SCHEDULE_ID)

            async def _replace(input: Any) -> ScheduleUpdate:  # noqa: A002
                del input
                return ScheduleUpdate(schedule=_schedule())

            await handle.update(_replace)
            return GITHUB_ISSUE_RECONCILE_SCHEDULE_ID
        except Exception as update_exc:
            if not _is_rpc_status(update_exc, "NOT_FOUND") and "not found" not in str(
                update_exc
            ).lower():
                raise ScheduleOperationError(
                    f"Failed to update GitHub issue reconcile schedule: {update_exc}"
                ) from update_exc
        try:
            handle = await client.create_schedule(
                GITHUB_ISSUE_RECONCILE_SCHEDULE_ID, _schedule()
            )
            return handle.id
        except Exception as create_exc:
            raise ScheduleOperationError(
                f"Failed to create GitHub issue reconcile schedule: {create_exc}"
            ) from create_exc

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
            make_scheduled_workflow_id_base,
        )

        client = await self.get_client()
        definition_uuid = _UUID(str(definition_id))
        schedule_id = make_schedule_id(definition_uuid)
        task_queue = self._get_task_queue(workflow_type, memo=memo)

        admitted_input = admit_user_workflow_input(workflow_type, workflow_input)
        args = [admitted_input] if admitted_input is not None else []

        formatted_sa = {
            k: v if isinstance(v, list) else [v]
            for k, v in (search_attributes or {}).items()
        }
        typed_search_attributes = _build_typed_search_attributes(formatted_sa)

        try:
            handle = await client.create_schedule(
                schedule_id,
                Schedule(
                    action=ScheduleActionStartWorkflow(
                        workflow_type,
                        *args,
                        id=make_scheduled_workflow_id_base(definition_uuid),
                        task_queue=task_queue,
                        memo=memo,
                        **(
                            {"typed_search_attributes": typed_search_attributes}
                            if typed_search_attributes
                            else {}
                        ),
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
        workflow_type: str | None = None,
        workflow_input: Mapping[str, Any] | None = None,
        preserve_existing_admission: bool = False,
        memo: Mapping[str, Any] | None = None,
        search_attributes: Mapping[str, Any] | None = None,
    ) -> None:
        """Update the spec, policy, or state of an existing schedule.

        Only provided (non-``None``) fields are modified; everything else
        is preserved from the current schedule description. Automatic repairs
        preserve existing admission; explicit execution-target replacements
        retain the default new-admission behavior. Without a workflow replacement,
        supplied memo/search attributes are merged by key onto the current
        action; its execution inputs and other options remain untouched.

        Raises:
            ScheduleNotFoundError: if the schedule does not exist.
            ScheduleOperationError: on unexpected SDK failure.
        """
        from uuid import UUID as _UUID

        from temporalio.client import ScheduleActionStartWorkflow, ScheduleUpdate

        from moonmind.workflows.temporal.schedule_errors import ScheduleOperationError
        from moonmind.workflows.temporal.schedule_mapping import (
            build_schedule_policy,
            build_schedule_spec,
            build_schedule_state,
            make_scheduled_workflow_id_base,
        )

        definition_uuid = _UUID(str(definition_id))
        handle, _desc = await self._get_schedule_handle(definition_id)
        task_queue = self._get_task_queue(workflow_type, memo=memo)

        formatted_sa = {
            k: v if isinstance(v, list) else [v]
            for k, v in (search_attributes or {}).items()
        }
        typed_search_attributes = _build_typed_search_attributes(formatted_sa)

        async def _do_update(input: Any) -> Any:  # noqa: A002
            schedule = input.description.schedule

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

            if workflow_type is not None:
                admitted_input = admit_user_workflow_input(workflow_type, workflow_input)
                if (
                    preserve_existing_admission
                    and workflow_type == RENAMED_USER_WORKFLOW_TYPE
                    and isinstance(schedule.action, ScheduleActionStartWorkflow)
                    and schedule.action.workflow == workflow_type
                    and len(schedule.action.args) == 1
                ):
                    existing_input = decode_schedule_action_value(schedule.action.args[0])
                    if isinstance(existing_input, Mapping):
                        # Read the current durable action inside the SDK's
                        # optimistic update callback, never a stale description
                        # or a caller-supplied opt-out. Absence is old admission.
                        admitted_input = dict(admitted_input or {})
                        admitted_input.pop("linked_recovery_retired", None)
                        if "linked_recovery_retired" in existing_input:
                            admitted_input["linked_recovery_retired"] = existing_input[
                                "linked_recovery_retired"
                            ]
                args = [admitted_input] if admitted_input is not None else []
                schedule.action = ScheduleActionStartWorkflow(
                    workflow_type,
                    args=args,
                    id=make_scheduled_workflow_id_base(definition_uuid),
                    task_queue=task_queue,
                    memo=memo,
                    **(
                        {"typed_search_attributes": typed_search_attributes}
                        if typed_search_attributes
                        else {}
                    ),
                )

            elif isinstance(schedule.action, ScheduleActionStartWorkflow):
                # Refresh future-start projections without changing durable
                # args/admission, routing, identity, or already-started runs.
                if memo is not None:
                    schedule.action.memo = {**(schedule.action.memo or {}), **memo}
                if typed_search_attributes:
                    schedule.action.typed_search_attributes = (
                        schedule.action.typed_search_attributes.updated(
                            *typed_search_attributes
                        )
                    )

            return ScheduleUpdate(schedule=schedule)

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

    async def observe_schedule_trigger(
        self, *, definition_id: Any, scheduled_at: datetime
    ) -> ScheduleTriggerResult:
        """Read the action identified by the timestamp we explicitly authored.

        Never substitute the latest action or infer a skip from a lifetime
        counter. Reads can be repeated after response loss without triggering.
        """
        _, description = await self._get_schedule_handle(definition_id)
        return _latest_schedule_trigger_result(description, scheduled_at=scheduled_at)

    async def trigger_schedule(
        self,
        *,
        definition_id: Any,
        request_id: str | None = None,
        scheduled_at: datetime | None = None,
        review_ready_owner: tuple[str, str] | None = None,
    ) -> ScheduleTriggerResult:
        from uuid import uuid4
        from google.protobuf.timestamp_pb2 import Timestamp
        from temporalio.api.schedule.v1 import SchedulePatch, TriggerImmediatelyRequest
        from temporalio.api.workflowservice.v1 import PatchScheduleRequest
        from temporalio.client import ScheduleOverlapPolicy, WorkflowExecutionStatus

        handle, description = await self._get_schedule_handle(definition_id)
        client = await self.get_client()
        if review_ready_owner is not None:
            # Override Skip for this one journaled trigger only. Keep at most
            # two issue owners: a verified durable merge owner and one successor.
            # The schedule policy, issue claims and prerequisite checks stay intact.
            owner_id, owner_run = review_ready_owner
            actions = description.info.running_actions
            eligible = (
                not description.schedule.state.paused
                and description.schedule.policy.overlap == ScheduleOverlapPolicy.SKIP
                and owner_id.startswith(f"mm:{definition_id}-")
            )
            # ALLOW_ALL successors are deliberately absent from this list on
            # Temporal 1.29.1. The recurring service owns the durable bound;
            # this read only adds a conservative check for native owners.
            for action in actions:
                if action.workflow_id != owner_id:
                    other = await client.get_workflow_handle(action.workflow_id).describe()
                    if other.status == WorkflowExecutionStatus.RUNNING:
                        eligible = False
            if eligible:
                parent = await client.get_workflow_handle(
                    owner_id, run_id=owner_run
                ).describe()
                memo = await parent.memo()
                marker = memo.get("implementationAvailability") or {}
                eligible = (
                    parent.status == WorkflowExecutionStatus.RUNNING
                    and isinstance(marker, dict)
                    and marker.get("state") == "review_ready"
                    and marker.get("definitionId") == str(definition_id)
                    and isinstance(marker.get("mergeWorkflowId"), str)
                    and bool(marker["mergeWorkflowId"])
                )
                if eligible:
                    merge = await client.get_workflow_handle(
                        marker["mergeWorkflowId"]
                    ).describe()
                    eligible = (
                        merge.status == WorkflowExecutionStatus.RUNNING
                        and merge.workflow_type == "MoonMind.MergeAutomation"
                        and merge.parent_id == owner_id
                        and merge.parent_run_id == owner_run
                    )
            if not eligible:
                return ScheduleTriggerResult(
                    disposition="skipped",
                    message="No independent implementation slot is available.",
                )
        # A positive active-owner observation can answer Skip without sending
        # an unobservable trigger. Temporal remains the overlap authority.
        if (
            review_ready_owner is None
            and description.schedule.policy.overlap == ScheduleOverlapPolicy.SKIP
        ):
            for running in description.info.running_actions:
                owner = client.get_workflow_handle(
                    running.workflow_id, run_id=running.first_execution_run_id
                )
                observed = await owner.describe()
                if observed.status == WorkflowExecutionStatus.RUNNING:
                    return ScheduleTriggerResult(
                        workflow_id=running.workflow_id,
                        run_id=running.first_execution_run_id,
                        disposition="skipped",
                        message="Already running; no new execution started.",
                    )
        scheduled_at = scheduled_at or datetime.now(timezone.utc)
        timestamp = Timestamp()
        timestamp.FromDatetime(scheduled_at)
        # SDK trigger() invents both identities internally. Persisted callers
        # supply them here, including the scheduled_time supported by Temporal.
        try:
            await client.workflow_service.patch_schedule(
                PatchScheduleRequest(
                    namespace=client.namespace,
                    schedule_id=handle.id,
                    request_id=request_id or str(uuid4()),
                    identity=client.identity,
                    patch=SchedulePatch(
                        trigger_immediately=TriggerImmediatelyRequest(
                            scheduled_time=timestamp,
                            **(
                                {
                                    "overlap_policy": ScheduleOverlapPolicy.ALLOW_ALL.value
                                }
                                if review_ready_owner is not None
                                else {}
                            ),
                        )
                    ),
                )
            )
        except Exception as exc:
            from moonmind.workflows.temporal.schedule_errors import (
                ScheduleOperationError,
            )

            raise ScheduleOperationError(f"Failed to trigger schedule: {exc}") from exc
        try:
            return await self.observe_schedule_trigger(
                definition_id=definition_id, scheduled_at=scheduled_at
            )
        except Exception:
            return ScheduleTriggerResult()

    async def recurring_issue_owner(self, workflow_id: str) -> dict[str, Any]:
        """Observe a durable owner directly, including untracked ALLOW_ALL starts.

        Follow the current run of this unique occurrence ID across continuation.
        A transport error is unknown ownership, never evidence of a free slot.
        """
        from temporalio.client import WorkflowExecutionStatus

        client = await self.get_client()
        try:
            description = await client.get_workflow_handle(workflow_id).describe()
        except Exception as exc:
            if _is_rpc_status(exc, "NOT_FOUND"):
                return {"state": "terminal", "workflowId": workflow_id}
            raise
        if description.status != WorkflowExecutionStatus.RUNNING:
            return {"state": "terminal", "workflowId": workflow_id}
        result = {
            "state": "running",
            "workflowId": workflow_id,
            "runId": description.run_id,
            "startedAt": description.start_time,
            "root": description.parent_id is None
            and description.workflow_type == "MoonMind.UserWorkflow",
            "reviewReady": False,
        }
        memo = await description.memo()
        marker = memo.get("implementationAvailability") or {}
        if (
            isinstance(marker, dict)
            and marker.get("state") == "review_ready"
            and marker.get("mergeWorkflowId")
        ):
            try:
                merge = await client.get_workflow_handle(
                    marker["mergeWorkflowId"]
                ).describe()
            except Exception as exc:
                if _is_rpc_status(exc, "NOT_FOUND"):
                    return result
                raise
            result["reviewReady"] = (
                merge.status == WorkflowExecutionStatus.RUNNING
                and merge.workflow_type == "MoonMind.MergeAutomation"
                and merge.parent_id == workflow_id
                and merge.parent_run_id == description.run_id
            )
        return result

    async def recurring_native_owner_ids(self, definition_id: Any) -> list[str]:
        _, description = await self._get_schedule_handle(definition_id)
        return [action.workflow_id for action in description.info.running_actions]

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
    """Protocol for the execution service used to create executions."""

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
