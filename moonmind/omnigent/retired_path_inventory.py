"""Read-only inventory of work that can still reach a retired path.

``moonmind drain-inventory`` (MoonLadderStudios/MoonMind#5343) runs these
readers once against the deployment's PostgreSQL and Temporal and reports
every item through the drain report in
:mod:`moonmind.omnigent.codex_cutover_drain`. Nothing here deletes,
terminates, signals, or rewrites anything: PostgreSQL is read inside one
read-only transaction and Temporal is only listed and described.

An item is ``active`` while open work or an enabled schedule holds it,
``clean`` once nothing open can load it, and ``unknown`` when its source could
not be read or the read exceeded its deadline. Open
``MoonMind.AgentSession`` children are still a supported product path, so
they are listed for information and never decide the exit status. The
release registers no worker for ``MoonMind.ControlStopContinuation`` (#5340),
so an execution of that type still open needs an old-release worker.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Mapping

from moonmind.omnigent.codex_cutover_drain import run_codex_drain_procedure

#: Retired realizer and Host Class this inventory tracks. Their retirement
#: owners are ``RETIRED_REALIZERS`` (harness_platform/support.py) and
#: ``RETIRED_HOST_CLASS_REFS`` (harness_platform/host_classes.py).
PROFILE_BOUND_REALIZER_REF = "codex-profile-bound@1"
OPENCODE_V1_HOST_CLASS_REF = "omnigent-opencode@1"
AGENT_SESSION_WORKFLOW_TYPE = "MoonMind.AgentSession"
CONTROL_STOP_CONTINUATION_WORKFLOW_TYPE = "MoonMind.ControlStopContinuation"

RETIRED_PATH_CATEGORIES: tuple[str, ...] = (
    "profile_bound_plans",
    "profile_bound_queued_starts",
    "profile_bound_retries",
    "opencode_v1_plans",
    "opencode_v1_host_class_requests",
    "agent_session_children",
    "legacy_task_queue_workflows",
    "control_stop_continuation_workflows",
)
RETIRED_PATH_INFORMATIONAL: tuple[str, ...] = ("agent_session_children",)
#: Categories read from PostgreSQL; the rest come from Temporal visibility.
RECORDED_PATH_CATEGORIES: tuple[str, ...] = (
    "profile_bound_plans",
    "profile_bound_queued_starts",
    "profile_bound_retries",
    "opencode_v1_plans",
    "opencode_v1_host_class_requests",
)

_PLAN_REF_KEYS = frozenset({"planRef", "plan_ref", "executionPlanRef"})
_HOST_CLASS_KEYS = frozenset({"hostClassRef", "host_class_ref"})
_CONNECT_TIMEOUT_SECONDS = 30.0
#: PostgreSQL ends any read still waiting after this, including one queued
#: behind a migration's lock, so the report marks those categories unknown.
_READ_TIMEOUT_SECONDS = 30.0


@dataclass(frozen=True, slots=True)
class OpenExecution:
    """One open execution source record and the refs its inputs name."""

    workflow_id: str
    queued: bool
    plan_refs: frozenset[str]
    host_class_refs: frozenset[str]


@dataclass(frozen=True, slots=True)
class RecordedState:
    """One consistent PostgreSQL snapshot of retired plans and open work."""

    plans: Mapping[str, Mapping[str, str]]
    plan_workflow_ids: Mapping[str, frozenset[str]]
    open_executions: Mapping[str, OpenExecution]
    recorded_workflow_ids: frozenset[str]
    #: Enabled schedule definitions per retired plan. A schedule loads its
    #: recorded plan on every refresh and occurrence, so it holds the plan
    #: even while no occurrence is open.
    plan_schedule_ids: Mapping[str, frozenset[str]] = field(default_factory=dict)

    def unrecorded_workflow_ids(self) -> list[str]:
        """Plan holders with no execution source record, to ask Temporal."""

        holders = set().union(*self.plan_workflow_ids.values())
        return sorted(holders - self.recorded_workflow_ids)


def _collect_values(value: Any, keys: frozenset[str]) -> set[str]:
    found: set[str] = set()
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if key in keys and isinstance(nested, str) and nested.strip():
                found.add(nested.strip())
            else:
                found |= _collect_values(nested, keys)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            found |= _collect_values(nested, keys)
    return found


def open_execution(workflow_id: str, memo: Any, parameters: Any) -> OpenExecution:
    """Describe one open source record from its memo and recorded inputs."""

    start = memo.get("temporalStart") if isinstance(memo, Mapping) else None
    queued = isinstance(start, Mapping) and start.get("state") == "pending"
    return OpenExecution(
        workflow_id=workflow_id,
        queued=queued,
        plan_refs=frozenset(_collect_values(parameters, _PLAN_REF_KEYS)),
        host_class_refs=frozenset(_collect_values(parameters, _HOST_CLASS_KEYS)),
    )


async def read_recorded_state(engine: Any) -> RecordedState:
    """Read retired plans and open executions in one read-only transaction."""

    from sqlalchemy import or_, select, text

    from api_service.db.models import MoonMindWorkflowState
    from api_service.db.models import OmnigentExecutionPlanRecord as Plan
    from api_service.db.models import OmnigentExecutionPlanUsageRecord as Usage
    from api_service.db.models import RecurringWorkflowDefinition as Schedule
    from api_service.db.models import TemporalExecutionCanonicalRecord as Source

    retired = or_(
        Plan.execution_realizer_ref == PROFILE_BOUND_REALIZER_REF,
        Plan.host_class_ref == OPENCODE_V1_HOST_CLASS_REF,
    )
    retired_refs = select(Plan.plan_ref).where(retired)
    holder_ids = select(Usage.workflow_id).where(Usage.plan_ref.in_(retired_refs))
    terminal = (
        MoonMindWorkflowState.NO_COMMIT,
        MoonMindWorkflowState.COMPLETED,
        MoonMindWorkflowState.FAILED,
        MoonMindWorkflowState.CANCELED,
    )
    async with engine.connect() as connection:
        # The first statement fixes one snapshot and refuses every write.
        await connection.execute(
            text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        )
        await connection.execute(
            text(f"SET LOCAL statement_timeout = {int(_READ_TIMEOUT_SECONDS * 1000)}")
        )
        try:
            plan_rows = (
                await connection.execute(
                    select(
                        Plan.plan_ref,
                        Plan.execution_realizer_ref,
                        Plan.host_class_ref,
                    ).where(retired)
                )
            ).all()
            usage_rows = (
                await connection.execute(
                    select(Usage.plan_ref, Usage.workflow_id).where(
                        Usage.plan_ref.in_(retired_refs)
                    )
                )
            ).all()
            open_rows = (
                await connection.execute(
                    select(Source.workflow_id, Source.memo, Source.parameters).where(
                        Source.close_status.is_(None),
                        Source.state.notin_(terminal),
                    )
                )
            ).all()
            recorded_rows = (
                await connection.execute(
                    select(Source.workflow_id).where(Source.workflow_id.in_(holder_ids))
                )
            ).all()
            schedule_rows = (
                await connection.execute(
                    select(Schedule.id, Schedule.target).where(
                        Schedule.enabled.is_(True)
                    )
                )
            ).all()
        finally:
            await connection.rollback()

    plans = {
        row.plan_ref: {
            "realizer": row.execution_realizer_ref,
            "hostClass": row.host_class_ref,
        }
        for row in plan_rows
    }
    open_executions = {
        row.workflow_id: open_execution(row.workflow_id, row.memo, row.parameters)
        for row in open_rows
    }
    holders: dict[str, set[str]] = {plan_ref: set() for plan_ref in plans}
    for row in usage_rows:
        holders.setdefault(row.plan_ref, set()).add(row.workflow_id)
    for execution in open_executions.values():
        for plan_ref in execution.plan_refs & set(plans):
            holders[plan_ref].add(execution.workflow_id)
    schedules: dict[str, set[str]] = {}
    for row in schedule_rows:
        for plan_ref in _collect_values(row.target, _PLAN_REF_KEYS) & set(plans):
            schedules.setdefault(plan_ref, set()).add(str(row.id))
    return RecordedState(
        plans=plans,
        plan_workflow_ids={ref: frozenset(ids) for ref, ids in holders.items()},
        open_executions=open_executions,
        recorded_workflow_ids=frozenset(row.workflow_id for row in recorded_rows)
        | frozenset(open_executions),
        plan_schedule_ids={ref: frozenset(ids) for ref, ids in schedules.items()},
    )


def recorded_path_items(
    state: RecordedState,
    temporal_status: Mapping[str, str],
) -> dict[str, list[dict[str, Any]]]:
    """Classify retired plans, queued starts, retries and Host Class requests.

    ``temporal_status`` maps plan holders without a source record to
    ``open``, ``closed`` or ``unknown`` as Temporal reported them.
    """

    def is_open(workflow_id: str) -> bool:
        return (
            workflow_id in state.open_executions
            or temporal_status.get(workflow_id) == "open"
        )

    def plan_items(attribute: str, value: str) -> list[dict[str, Any]]:
        items = []
        for plan_ref in sorted(state.plans):
            if state.plans[plan_ref][attribute] != value:
                continue
            holders = sorted(state.plan_workflow_ids.get(plan_ref, ()))
            open_ids = [workflow_id for workflow_id in holders if is_open(workflow_id)]
            schedule_ids = sorted(state.plan_schedule_ids.get(plan_ref, ()))
            unresolved = [
                workflow_id
                for workflow_id in holders
                if temporal_status.get(workflow_id) == "unknown"
            ]
            active = bool(open_ids or schedule_ids)
            item: dict[str, Any] = {
                "id": plan_ref,
                "state": "active" if active else "unknown" if unresolved else "clean",
                "openWorkflowIds": open_ids,
            }
            if schedule_ids:
                item["scheduleIds"] = schedule_ids
            if unresolved and not active:
                item["unresolvedWorkflowIds"] = unresolved
            items.append(item)
        return items

    profile_bound = {
        plan_ref
        for plan_ref, plan in state.plans.items()
        if plan["realizer"] == PROFILE_BOUND_REALIZER_REF
    }
    queued: list[dict[str, Any]] = []
    retries: list[dict[str, Any]] = []
    holders_by_workflow: dict[str, set[str]] = {}
    for plan_ref in profile_bound:
        for workflow_id in state.plan_workflow_ids.get(plan_ref, ()):
            if is_open(workflow_id):
                holders_by_workflow.setdefault(workflow_id, set()).add(plan_ref)
    for workflow_id in sorted(holders_by_workflow):
        execution = state.open_executions.get(workflow_id)
        item = {
            "id": workflow_id,
            "state": "active",
            "planRefs": sorted(holders_by_workflow[workflow_id]),
        }
        (queued if execution is not None and execution.queued else retries).append(item)
    host_class_requests = [
        {
            "id": execution.workflow_id,
            "state": "active",
            "phase": "queued" if execution.queued else "started",
        }
        for execution in sorted(
            state.open_executions.values(), key=lambda item: item.workflow_id
        )
        if OPENCODE_V1_HOST_CLASS_REF in execution.host_class_refs
    ]
    return {
        "profile_bound_plans": plan_items("realizer", PROFILE_BOUND_REALIZER_REF),
        "profile_bound_queued_starts": queued,
        "profile_bound_retries": retries,
        "opencode_v1_plans": plan_items("hostClass", OPENCODE_V1_HOST_CLASS_REF),
        "opencode_v1_host_class_requests": host_class_requests,
    }


async def describe_workflow_status(client: Any, workflow_id: str) -> str:
    """Return ``open``, ``closed`` or ``unknown`` for one workflow id."""

    from temporalio.client import WorkflowExecutionStatus
    from temporalio.service import RPCError, RPCStatusCode

    try:
        description = await client.get_workflow_handle(workflow_id).describe()
    except RPCError as exc:
        # A workflow Temporal no longer has cannot schedule anything.
        return "closed" if exc.status == RPCStatusCode.NOT_FOUND else "unknown"
    except Exception:
        return "unknown"
    return "open" if description.status == WorkflowExecutionStatus.RUNNING else "closed"


async def list_open_workflows(client: Any, query: str) -> list[dict[str, Any]]:
    """List running workflows matching one visibility query as active items."""

    return [
        {
            "id": execution.id,
            "state": "active",
            "workflowType": execution.workflow_type,
            "taskQueue": execution.task_queue,
            "parentWorkflowId": execution.parent_id,
        }
        async for execution in client.list_workflows(
            f'ExecutionStatus="Running" AND {query}'
        )
    ]


def _quoted(value: str) -> str:
    if not value or '"' in value or "\\" in value:
        raise ValueError(f"unsupported visibility query value: {value!r}")
    return f'"{value}"'


async def _attempt(
    action: Callable[[], Awaitable[Any]],
) -> tuple[Any, Exception | None]:
    try:
        return await action(), None
    except Exception as exc:
        return None, exc


def _source(value: Any, error: Exception | None) -> Callable[[], Any]:
    """Defer a settled read so the drain collector owns failure semantics."""

    def settled() -> Any:
        if error is not None:
            raise error
        return value

    return settled


async def collect_retired_path_report(
    *,
    engine_factory: Callable[[], Any],
    temporal_connect: Callable[[], Awaitable[Any]],
    legacy_task_queue: str,
) -> dict[str, Any]:
    """Read every retired-path source once and return the drain report."""

    try:
        engine = engine_factory()
    except Exception as exc:
        recorded, recorded_error = None, exc
    else:
        try:
            recorded, recorded_error = await _attempt(
                lambda: read_recorded_state(engine)
            )
        finally:
            await engine.dispose()
    client, client_error = await _attempt(
        lambda: asyncio.wait_for(temporal_connect(), _CONNECT_TIMEOUT_SECONDS)
    )

    sources: dict[str, Callable[[], Any]] = {}
    if recorded_error is not None:
        for category in RECORDED_PATH_CATEGORIES:
            sources[category] = _source(None, recorded_error)
    else:
        status = {
            workflow_id: (
                "unknown"
                if client is None
                else await describe_workflow_status(client, workflow_id)
            )
            for workflow_id in recorded.unrecorded_workflow_ids()
        }
        for category, items in recorded_path_items(recorded, status).items():
            sources[category] = _source(items, None)

    for category, attribute, value in (
        ("agent_session_children", "WorkflowType", AGENT_SESSION_WORKFLOW_TYPE),
        ("legacy_task_queue_workflows", "TaskQueue", legacy_task_queue),
        (
            "control_stop_continuation_workflows",
            "WorkflowType",
            CONTROL_STOP_CONTINUATION_WORKFLOW_TYPE,
        ),
    ):
        if client_error is not None:
            items, error = None, client_error
        else:
            items, error = await _attempt(
                lambda: list_open_workflows(client, f"{attribute}={_quoted(value)}")
            )
        sources[category] = _source(items, error)

    return run_codex_drain_procedure(
        sources,
        categories=RETIRED_PATH_CATEGORIES,
        informational=RETIRED_PATH_INFORMATIONAL,
    )


async def run_retired_path_inventory(
    *,
    database_url: str,
    temporal_address: str,
    temporal_namespace: str,
    legacy_task_queue: str,
) -> dict[str, Any]:
    """Run the inventory against one deployment's PostgreSQL and Temporal."""

    from sqlalchemy.ext.asyncio import create_async_engine

    from moonmind.workflows.temporal.client import get_temporal_client

    return await collect_retired_path_report(
        engine_factory=lambda: create_async_engine(
            database_url,
            connect_args={"timeout": _CONNECT_TIMEOUT_SECONDS},
        ),
        temporal_connect=lambda: get_temporal_client(
            temporal_address, temporal_namespace
        ),
        legacy_task_queue=legacy_task_queue,
    )


__all__ = [
    "AGENT_SESSION_WORKFLOW_TYPE",
    "CONTROL_STOP_CONTINUATION_WORKFLOW_TYPE",
    "OPENCODE_V1_HOST_CLASS_REF",
    "PROFILE_BOUND_REALIZER_REF",
    "RECORDED_PATH_CATEGORIES",
    "RETIRED_PATH_CATEGORIES",
    "RETIRED_PATH_INFORMATIONAL",
    "OpenExecution",
    "RecordedState",
    "collect_retired_path_report",
    "describe_workflow_status",
    "list_open_workflows",
    "open_execution",
    "read_recorded_state",
    "recorded_path_items",
    "run_retired_path_inventory",
]
