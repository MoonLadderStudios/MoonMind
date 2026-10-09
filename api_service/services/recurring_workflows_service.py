"""Service layer for recurring workflow definitions and Temporal-driven dispatch."""

from __future__ import annotations

import asyncio
import copy
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Iterable, Mapping
from uuid import UUID, uuid4

from sqlalchemy import Select, and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from api_service.db.models import (
    ManagedAgentProviderProfile,
    OmnigentAgentProfile,
    OmnigentAgentProfileUsage,
    OmnigentAgentProfileVersion,
    OmnigentOAuthHostBindingRecord,
    OmnigentPolicyVersion,
    RecurringWorkflowDefinition,
    RecurringWorkflowRun,
    RecurringWorkflowRunOutcome,
    RecurringWorkflowRunTrigger,
    RecurringWorkflowScopeType,
    TemporalArtifact,
    TemporalExecutionRecord,
    User,
)
from api_service.services.omnigent_agent_profile_selection import (
    compile_agent_profile_snapshot_parameters,
    refresh_managed_bootstrap_snapshot,
    refresh_schedule_deployment_snapshot,
    resolve_agent_profile_snapshot,
    resolve_default_agent_profile_snapshot,
)
from api_service.services.provider_profile_projection import provider_profile_label_snapshot
from api_service.services.provider_profile_runtime import (
    require_launch_target_provider_profile_runtime,
    resolve_launch_target_profile_selection,
)
from moonmind.config.settings import settings
from moonmind.runtime_intent import (
    RuntimeIntentValidationError,
    model_selection_fields,
    validate_model_selection_submission,
)
from moonmind.workflows.executions.execution_contract import (
    WorkflowContractError,
    reject_retired_vector_fields,
    strip_absent_vector_fields,
)
from moonmind.workflows.executions.runtime_target_selection import (
    AuthoringSurface,
    resolve_runtime_target_selection,
)
from moonmind.workflows.executions.provider_profile_projection import (
    PROVIDER_PROFILE_MEMO_KEY,
    PROVIDER_PROFILE_SEARCH_ATTRIBUTE,
    build_provider_profile_projection,
)
from moonmind.workflows.recurring.cron import (
    compute_next_occurrence,
    parse_cron_expression,
    validate_timezone_name,
)
from moonmind.workflows.temporal.schedule_mapping import (
    make_scheduled_workflow_id_base,
)
from moonmind.workflows.temporal.client import (
    ScheduleTriggerResult,
    TemporalClientAdapter,
)
from moonmind.workflows.temporal.schedule_errors import (
    ScheduleAdapterError,
    ScheduleAlreadyExistsError,
    ScheduleNotFoundError,
    ScheduleOperationError,
)

logger = logging.getLogger(__name__)

_DEFAULT_SCHEDULER_MAX_BACKFILL = 3

# MoonLadderStudios/MoonMind#3833: a schedule either pins its runtime-provider
# target version or follows the qualified default through an explicit,
# separately versioned update policy. Changing a schedule's target advances the
# schedule revision (``definition.version``); it never happens silently.
# MoonLadderStudios/MoonMind#3931: future launches follow installed managed
# runtime selection through the one shared boundary (Workflow Create/schedule
# submission), without independent image/rollout pins. The stored target below
# is recorded authority for occurrences (identity, cadence, paused state,
# Profile, and intent preserved); new schedules persist ``pinned`` at creation
# and reconcile incompatible persisted inputs through an explicit schedule
# revision, never by recreating the schedule. Schedules that should track the
# qualified default opt into ``follow_qualified_default``; the refresh path
# raises for ``pinned`` on target change so the follow happens only via that
# explicit revision.
SCHEDULE_TARGET_PINNED = "pinned"
SCHEDULE_TARGET_FOLLOW_QUALIFIED_DEFAULT = "follow_qualified_default"
_SCHEDULE_TARGET_UPDATE_POLICIES = frozenset(
    {SCHEDULE_TARGET_PINNED, SCHEDULE_TARGET_FOLLOW_QUALIFIED_DEFAULT}
)
# Per schedule: the reusable plan this process last compiled for it and the
# digest of the inputs it was compiled from. What a compile reads from the
# image or process environment (built-in Skills, rollout policy, evidence
# settings) is fixed for the process lifetime, so the plan stays current until
# one of the fingerprinted inputs moves. One entry per schedule.
_SCHEDULE_PLAN_INPUTS: dict[str, tuple[str, str]] = {}
# MoonLadderStudios/MoonMind#4192: the native ManifestIngest product is
# retired. MoonMind.ManifestIngest is intentionally absent from the live
# recurring catalog: new recurring targets carrying it are rejected
# actionably in _normalize_target below. Old-release definitions stay
# readable as replay/drain evidence; they are not schedulable for new work.
_SUPPORTED_RECURRING_WORKFLOW_TYPES = (
    "MoonMind.UserWorkflow",
)

class RecurringWorkflowValidationError(ValueError):
    """Raised when recurring workflow inputs are invalid."""

class RecurringWorkflowConflictError(RuntimeError):
    """Raised when a recurring definition changed before an authored update."""

class RecurringWorkflowNotFoundError(RuntimeError):
    """Raised when a recurring definition does not exist."""

class RecurringWorkflowAuthorizationError(RuntimeError):
    """Raised when a caller does not have access to a recurring definition."""

@dataclass(frozen=True, slots=True)
class RecurringPolicy:
    overlap_mode: str = "skip"
    max_concurrent_runs: int = 1
    catchup_mode: str = "last"
    max_backfill: int = 3
    misfire_grace_seconds: int = 900
    jitter_seconds: int = 0

@dataclass(frozen=True, slots=True)
class RecurringScheduleRuntimeSummary:
    """Read-only Temporal projection for a recurring schedule definition."""

    next_run_at: datetime | None = None
    last_scheduled_for: datetime | None = None
    last_dispatch_status: str | None = None
    last_dispatch_error: str | None = None

@dataclass(frozen=True, slots=True)
class ManagedScheduleRefresh:
    """One managed-schedule refresh pass: how many moved, and why others did not."""

    refreshed: int = 0
    failures: tuple[str, ...] = ()

def _json_object(value: object, *, field_name: str) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return dict(value)
    raise RecurringWorkflowValidationError(f"{field_name} must be a JSON object")

def _clean_text(
    value: object, *, field_name: str, required: bool = False
) -> str | None:
    text = str(value or "").strip()
    if not text:
        if required:
            raise RecurringWorkflowValidationError(f"{field_name} is required")
        return None
    return text

def _normalize_scope_type(value: object) -> RecurringWorkflowScopeType:
    raw = str(value or "").strip().lower() or RecurringWorkflowScopeType.PERSONAL.value
    try:
        return RecurringWorkflowScopeType(raw)
    except ValueError as exc:
        raise RecurringWorkflowValidationError(
            "scopeType must be one of: personal, global"
        ) from exc

def _normalize_schedule_type(value: object) -> str:
    raw = str(value or "").strip().lower() or "cron"
    if raw != "cron":
        raise RecurringWorkflowValidationError("scheduleType must be 'cron'")
    return raw

def _normalize_policy(
    policy_payload: Mapping[str, Any] | None,
    *,
    global_max_backfill: int,
) -> RecurringPolicy:
    payload = dict(policy_payload or {})

    overlap_payload = payload.get("overlap")
    overlap = dict(overlap_payload) if isinstance(overlap_payload, Mapping) else {}
    overlap_mode = str(overlap.get("mode") or "skip").strip().lower()
    if overlap_mode not in {"skip", "allow", "buffer_one", "cancel_previous"}:
        raise RecurringWorkflowValidationError("policy.overlap.mode must be skip, allow, buffer_one, or cancel_previous")

    max_concurrent_raw = overlap.get("maxConcurrentRuns")
    try:
        max_concurrent = (
            int(max_concurrent_raw) if max_concurrent_raw is not None else 1
        )
    except (TypeError, ValueError) as exc:
        raise RecurringWorkflowValidationError(
            "policy.overlap.maxConcurrentRuns must be an integer"
        ) from exc
    max_concurrent = max(1, max_concurrent)

    catchup_payload = payload.get("catchup")
    catchup = dict(catchup_payload) if isinstance(catchup_payload, Mapping) else {}
    catchup_mode = str(catchup.get("mode") or "last").strip().lower()
    if catchup_mode not in {"none", "last", "all"}:
        raise RecurringWorkflowValidationError(
            "policy.catchup.mode must be none, last, or all"
        )

    max_backfill_raw = catchup.get("maxBackfill")
    try:
        max_backfill = int(max_backfill_raw) if max_backfill_raw is not None else 3
    except (TypeError, ValueError) as exc:
        raise RecurringWorkflowValidationError(
            "policy.catchup.maxBackfill must be an integer"
        ) from exc
    max_backfill = max(1, max_backfill)
    max_backfill = min(max_backfill, max(1, int(global_max_backfill)))

    misfire_raw = payload.get("misfireGraceSeconds", 900)
    try:
        misfire_grace = int(misfire_raw)
    except (TypeError, ValueError) as exc:
        raise RecurringWorkflowValidationError(
            "policy.misfireGraceSeconds must be an integer"
        ) from exc
    misfire_grace = max(0, misfire_grace)

    jitter_raw = payload.get("jitterSeconds", 0)
    try:
        jitter_seconds = int(jitter_raw)
    except (TypeError, ValueError) as exc:
        raise RecurringWorkflowValidationError(
            "policy.jitterSeconds must be an integer"
        ) from exc
    jitter_seconds = max(0, jitter_seconds)

    return RecurringPolicy(
        overlap_mode=overlap_mode,
        max_concurrent_runs=max_concurrent,
        catchup_mode=catchup_mode,
        max_backfill=max_backfill,
        misfire_grace_seconds=misfire_grace,
        jitter_seconds=jitter_seconds,
    )

def _overlap_mode_from_temporal(overlap: object | None) -> str:
    if overlap is None:
        return "skip"
    raw = str(getattr(overlap, "name", overlap) or "").strip().upper()
    return {
        "SKIP": "skip",
        "ALLOW_ALL": "allow",
        "BUFFER_ONE": "buffer_one",
        "CANCEL_OTHER": "cancel_previous",
    }.get(raw, "skip")

def _catchup_mode_from_temporal_window(catchup_window: object | None) -> str:
    if catchup_window is None:
        return "last"
    total_seconds = getattr(catchup_window, "total_seconds", None)
    if total_seconds is None:
        return "last"
    secs = float(total_seconds())
    if secs == 0:
        return "none"
    if secs <= timedelta(minutes=15).total_seconds():
        return "last"
    return "all"

def _first_mapping_value(
    source: Mapping[str, Any],
    keys: tuple[str, ...],
) -> Any:
    for key in keys:
        if key in source:
            return source[key]
    return None

def _normalize_target(target_payload: Mapping[str, Any]) -> dict[str, Any]:
    target = dict(target_payload)
    workflow_type = str(
        target.get("workflowType") or target.get("workflow_type") or ""
    ).strip()
    # MoonLadderStudios/MoonMind#4192: the native ManifestIngest product is
    # retired. Reject actionably so old authoring surfaces the removal
    # instead of a generic unsupported-type message. Old-release definitions
    # stay readable as replay/drain evidence; they are not normalizable for
    # new schedules.
    if workflow_type == "MoonMind.ManifestIngest":
        raise RecurringWorkflowValidationError(
            "MoonMind.ManifestIngest was retired "
            "(MoonLadderStudios/MoonMind#4192): the new release does not "
            "register or launch manifest ingest workflows."
        )
    if workflow_type not in _SUPPORTED_RECURRING_WORKFLOW_TYPES:
        raise RecurringWorkflowValidationError(
            "target.workflowType must be one of: "
            + ", ".join(_SUPPORTED_RECURRING_WORKFLOW_TYPES)
        )

    initial_parameters = target.get("initialParameters")
    if initial_parameters is None:
        initial_parameters = target.get("initial_parameters")
    if initial_parameters is None:
        initial_parameters = {}
    if not isinstance(initial_parameters, Mapping):
        raise RecurringWorkflowValidationError(
            "target.initialParameters must be an object when provided"
        )

    initial_parameters = copy.deepcopy(dict(initial_parameters))
    try:
        from moonmind.workflows.executions.execution_contract import (
            validate_workflow_runtime_targets,
        )

        validate_workflow_runtime_targets(initial_parameters)
        reject_retired_vector_fields(
            initial_parameters, field_path="target.initialParameters"
        )
        strip_absent_vector_fields(initial_parameters)
        for task_key in ("task", "workflow"):
            task = initial_parameters.get(task_key)
            if isinstance(task, Mapping):
                task = dict(task)
                reject_retired_vector_fields(
                    task, field_path=f"target.initialParameters.{task_key}"
                )
                initial_parameters[task_key] = strip_absent_vector_fields(task)
    except WorkflowContractError as exc:
        raise RecurringWorkflowValidationError(str(exc)) from exc

    target["workflowType"] = workflow_type
    target["initialParameters"] = initial_parameters
    target.pop("workflow_type", None)
    target.pop("initial_parameters", None)

    for camel_key, aliases in (
        ("inputArtifactRef", ("inputArtifactRef", "input_artifact_ref")),
        ("planArtifactRef", ("planArtifactRef", "plan_artifact_ref")),
        ("failurePolicy", ("failurePolicy", "failure_policy")),
    ):
        value = _first_mapping_value(target, aliases)
        if value is not None:
            target[camel_key] = value
        for alias in aliases:
            if alias != camel_key:
                target.pop(alias, None)

    return target

def _coerce_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)

def _object_value(source: object, *keys: str) -> Any:
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

def _coerce_optional_utc_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return _coerce_utc(value)
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        try:
            return _coerce_utc(datetime.fromisoformat(raw.replace("Z", "+00:00")))
        except ValueError:
            return None
    return None

def _first_temporal_datetime(source: object, *keys: str) -> datetime | None:
    values = _object_value(source, *keys)
    if not values:
        return None
    if isinstance(values, (datetime, str)):
        return _coerce_optional_utc_datetime(values)
    try:
        iterator = iter(values)
    except TypeError:
        return _coerce_optional_utc_datetime(values)
    for item in iterator:
        coerced = _coerce_optional_utc_datetime(item)
        if coerced is not None:
            return coerced
    return None

def _last_temporal_action(info: object) -> object | None:
    actions = _object_value(info, "recent_actions", "recentActions") or []
    try:
        action_list = list(actions)
    except TypeError:
        return None
    if not action_list:
        return None

    def _sort_key(action: object) -> datetime:
        return (
            _coerce_optional_utc_datetime(
                _object_value(action, "started_at", "startedAt")
            )
            or _coerce_optional_utc_datetime(
                _object_value(action, "actual_time", "actualTime")
            )
            or _coerce_optional_utc_datetime(
                _object_value(action, "scheduled_at", "scheduledAt")
            )
            or _coerce_optional_utc_datetime(
                _object_value(action, "schedule_time", "scheduleTime")
            )
            or datetime.min.replace(tzinfo=UTC)
        )

    return max(action_list, key=_sort_key)

def _dispatch_status_from_temporal_action(action: object | None) -> str | None:
    if action is None:
        return None
    if _object_value(action, "action") is not None:
        return RecurringWorkflowRunOutcome.ENQUEUED.value
    result = _object_value(action, "start_workflow_result", "startWorkflowResult")
    if result is not None:
        return RecurringWorkflowRunOutcome.ENQUEUED.value
    raw_status = _object_value(action, "start_workflow_status", "startWorkflowStatus")
    status_name = str(getattr(raw_status, "name", raw_status) or "").lower()
    if (
        "failed" in status_name
        or "terminated" in status_name
        or "canceled" in status_name
    ):
        return RecurringWorkflowRunOutcome.DISPATCH_ERROR.value
    if status_name:
        return RecurringWorkflowRunOutcome.ENQUEUED.value
    return None

def _dispatch_error_from_temporal_action(action: object | None) -> str | None:
    if action is None:
        return None
    for key in ("failure", "error", "message"):
        value = _object_value(action, key)
        if value:
            return str(value)
    return None

class RecurringWorkflowsService:
    """CRUD and dispatch helpers for recurring definitions."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        temporal_client_adapter: TemporalClientAdapter | None = None,
        artifact_service: Any | None = None,
    ) -> None:
        self._session = session
        self._adapter = temporal_client_adapter or TemporalClientAdapter()
        self._artifact_service = artifact_service

    async def _lock_definition_for_update(
        self,
        definition_id: UUID,
    ) -> RecurringWorkflowDefinition:
        definition = await self._session.scalar(
            select(RecurringWorkflowDefinition)
            .where(RecurringWorkflowDefinition.id == definition_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if definition is None:
            raise RecurringWorkflowNotFoundError(
                f"Recurring workflow {definition_id} not found"
            )
        return definition

    async def list_definitions(
        self,
        *,
        include_disabled: bool = True,
        limit: int = 200,
        offset: int = 0,
    ) -> list[RecurringWorkflowDefinition]:
        # Single-user (#4351): schedules are instance resources. There is no
        # scope/user visibility predicate; legacy scope/``owner_user_id``
        # values are stored provenance and stay readable. Every scope is
        # listed together. Obsolete ``scope``/``user_id`` inputs were removed
        # with their callers; residual HTTP ``scope`` query compat lives only
        # in the router as a deprecated ignored value (#4354 owns its removal).
        stmt: Select[tuple[RecurringWorkflowDefinition]] = select(RecurringWorkflowDefinition)
        if not include_disabled:
            stmt = stmt.where(RecurringWorkflowDefinition.enabled.is_(True))
        stmt = stmt.order_by(
            RecurringWorkflowDefinition.updated_at.desc(),
            RecurringWorkflowDefinition.id.desc(),
        ).offset(max(0, int(offset))).limit(max(1, min(int(limit), 500)))
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def count_definitions(
        self,
        *,
        include_disabled: bool = True,
    ) -> int:
        # Single-user (#4351): instance visibility. Every scope is counted
        # together; obsolete scope/user inputs were removed with their callers.
        stmt = select(func.count()).select_from(RecurringWorkflowDefinition)
        if not include_disabled:
            stmt = stmt.where(RecurringWorkflowDefinition.enabled.is_(True))
        result = await self._session.execute(stmt)
        return int(result.scalar_one() or 0)

    async def get_definition(self, definition_id: UUID) -> RecurringWorkflowDefinition:
        # Single-user (#4351): the admitted operator sees every schedule.
        # Admission owns access; legacy owner/scope values are provenance.
        # Execution-state, source-authority, and approval validation still
        # apply at their owning boundaries.
        stmt: Select[tuple[RecurringWorkflowDefinition]] = (
            select(RecurringWorkflowDefinition)
            .where(RecurringWorkflowDefinition.id == definition_id)
            .options(selectinload(RecurringWorkflowDefinition.runs))
        )
        result = await self._session.execute(stmt)
        definition = result.scalars().first()
        if definition is None:
            raise RecurringWorkflowNotFoundError(
                f"Recurring workflow definition '{definition_id}' was not found"
            )
        return definition

    def _workflow_bundle_for_target(
        self,
        *,
        definition_id: UUID,
        name: str,
        owner_user_id: UUID | None,
        target_payload: Mapping[str, Any],
    ) -> tuple[str, dict[str, Any]]:
        workflow_type = str(target_payload["workflowType"])
        # MoonLadderStudios/MoonMind#4192: retired product. Targets are
        # rejected in _normalize_target before dispatch; this guard keeps
        # direct callers from scheduling ManifestIngest work.
        if workflow_type == "MoonMind.ManifestIngest":
            raise RecurringWorkflowValidationError(
                "MoonMind.ManifestIngest was retired "
                "(MoonLadderStudios/MoonMind#4192): the new release does not "
                "register or launch manifest ingest workflows."
            )

        from moonmind.workflows.executions.execution_contract import (
            validate_workflow_runtime_targets,
        )

        initial_parameters = dict(target_payload.get("initialParameters") or {})
        try:
            validate_workflow_runtime_targets(initial_parameters)
        except WorkflowContractError as exc:
            raise RecurringWorkflowValidationError(str(exc)) from exc
        system_payload = initial_parameters.get("system")
        system = dict(system_payload) if isinstance(system_payload, Mapping) else {}
        recurrence = dict(system.get("recurrence") or {})
        recurrence["definitionId"] = str(definition_id)
        system["recurrence"] = recurrence
        initial_parameters["system"] = system
        return workflow_type, {
            "workflow_type": workflow_type,
            "title": str(target_payload.get("title") or name),
            # Single-user (#4351): legacy owner strings are preserved as
            # provenance for retained histories; new schedules emit None.
            # See decode_recurring_workflow_input for replay compatibility.
            "owner_user_id": str(owner_user_id) if owner_user_id else None,
            "initial_parameters": initial_parameters,
            "input_artifact_ref": target_payload.get("inputArtifactRef"),
            "plan_artifact_ref": target_payload.get("planArtifactRef"),
            "failure_policy": target_payload.get("failurePolicy"),
        }

    def decode_recurring_workflow_input(
        self, workflow_input: Mapping[str, Any] | None
    ) -> dict[str, Any]:
        """Decode a retained schedule action payload without a user table.

        Single-user (#4351): already-admitted Temporal schedule payloads may
        carry a legacy ``owner_user_id`` human-owner string. The decoder
        preserves that string as non-authoritative provenance, keeps schedule
        IDs/cadence/frozen inputs untouched, and never consults a present-day
        user table nor maps every actor to a synthetic constant. ``None``
        (new instance schedules) stays ``None``.
        """
        payload = dict(workflow_input or {})
        owner = payload.get("owner_user_id")
        if owner is None:
            payload["owner_user_id"] = None
        else:
            text = str(owner).strip()
            payload["owner_user_id"] = text or None
        return payload

    def _owner_search_attributes(self, owner_user_id: UUID | None) -> dict[str, str]:
        # Single-user (#4351): new instance schedules carry no human owner
        # but Temporal requires trusted owner metadata
        # (run.py::_trusted_owner_metadata). Emit the instance owner so
        # scheduled MoonMind.UserWorkflow starts carry mm_owner_type/
        # mm_owner_id and can execute; legacy rows keep their USER attrs.
        if owner_user_id is None:
            return {
                "mm_owner_type": "system",
                "mm_owner_id": "system",
            }
        return {
            "mm_owner_type": "user",
            "mm_owner_id": str(owner_user_id),
        }

    async def _workflow_start_metadata(
        self,
        *,
        definition_id: UUID,
        owner_user_id: UUID | None,
        workflow_input: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Project the admitted schedule input through the normal list owner.

        Temporal starts occurrences directly from this action, bypassing
        create_execution. Keep memo and membership together on every action
        writer so launch-resolved selections can extend the same projection.
        """

        parameters = workflow_input.get("initial_parameters") or {}
        summary, search_value = build_provider_profile_projection(
            parameters,
            labels=await provider_profile_label_snapshot(self._session, parameters),
        )
        return {
            "memo": {
                "definitionId": str(definition_id),
                PROVIDER_PROFILE_MEMO_KEY: summary,
            },
            "search_attributes": {
                **self._owner_search_attributes(owner_user_id),
                PROVIDER_PROFILE_SEARCH_ATTRIBUTE: search_value,
            },
        }

    def _expected_task_queue(self, workflow_type: str) -> str | None:
        resolver = getattr(self._adapter, "resolve_workflow_task_queue", None)
        if not callable(resolver):
            return None
        try:
            resolved = resolver(workflow_type)
        except Exception:
            logger.warning(
                "Could not resolve expected task queue for recurring workflow %s",
                workflow_type,
                exc_info=True,
            )
            return None
        task_queue = str(resolved or "").strip()
        return task_queue or None

    def _schedule_action_mismatch(
        self,
        *,
        action: object | None,
        definition_id: UUID,
        workflow_type: str,
        workflow_input: Mapping[str, Any],
    ) -> bool:
        temporal_workflow_type = _object_value(action, "workflow")
        temporal_workflow_id_base = _object_value(action, "id")
        temporal_args = _object_value(action, "args") or []
        try:
            temporal_args_list = list(temporal_args)
        except TypeError:
            temporal_args_list = []
        temporal_input = temporal_args_list[0] if temporal_args_list else None
        temporal_task_queue = _object_value(action, "task_queue", "taskQueue")
        expected_task_queue = self._expected_task_queue(workflow_type)
        expected_workflow_id_base = make_scheduled_workflow_id_base(definition_id)
        memo = _object_value(action, "memo") or {}
        typed_attributes = _object_value(action, "typed_search_attributes") or []
        has_profile_projection = (
            isinstance(memo, Mapping)
            and PROVIDER_PROFILE_MEMO_KEY in memo
            and any(
                getattr(getattr(pair, "key", None), "name", None)
                == PROVIDER_PROFILE_SEARCH_ATTRIBUTE
                for pair in typed_attributes
            )
        )
        return (
            not has_profile_projection
            or temporal_workflow_type != workflow_type
            or temporal_workflow_id_base != expected_workflow_id_base
            or temporal_input != workflow_input
            or (
                expected_task_queue is not None
                and temporal_task_queue != expected_task_queue
            )
        )

    async def _ensure_schedule_action_current(
        self,
        definition: RecurringWorkflowDefinition,
    ) -> None:
        policy_src = (
            definition.policy if isinstance(definition.policy, Mapping) else None
        )
        policy_obj = _normalize_policy(
            policy_src,
            global_max_backfill=_DEFAULT_SCHEDULER_MAX_BACKFILL,
        )
        workflow_type, workflow_input = self._workflow_bundle_for_definition(
            definition
        )
        try:
            description = await self._adapter.describe_schedule(
                definition_id=definition.id
            )
        except ScheduleNotFoundError:
            await self._recreate_temporal_schedule(definition, policy_obj)
            return

        schedule = _object_value(description, "schedule")
        action = _object_value(schedule, "action")
        if not self._schedule_action_mismatch(
            action=action,
            definition_id=definition.id,
            workflow_type=workflow_type,
            workflow_input=workflow_input,
        ):
            return

        await self._adapter.update_schedule(
            definition_id=definition.id,
            workflow_type=workflow_type,
            workflow_input=workflow_input,
            **await self._workflow_start_metadata(
                definition_id=definition.id,
                owner_user_id=definition.owner_user_id,
                workflow_input=workflow_input,
            ),
        )

    async def _refresh_managed_bootstrap_target(
        self,
        definition: RecurringWorkflowDefinition,
    ) -> bool:
        """Advance one schedule when deployment-managed launch authority moves."""

        from api_service.services.omnigent_agent_bootstrap_service import (
            BOOTSTRAP_PROFILE_ID,
        )

        target = dict(definition.target or {})
        initial_parameters = dict(target.get("initialParameters") or {})
        has_plan = isinstance(
            initial_parameters.get("omnigentExecutionPlan"), Mapping
        )
        previous = initial_parameters.get("agentProfileSnapshot")
        if (
            not isinstance(previous, Mapping)
            or previous.get("profileId") != BOOTSTRAP_PROFILE_ID
        ):
            if has_plan:
                return await self._refresh_omnigent_execution_plan_target(
                    definition,
                    target=target,
                    initial_parameters=initial_parameters,
                )
            if previous is None:
                return await self._admit_plan_less_omnigent_target(
                    definition, target=target
                )
            return False
        if not has_plan and target.get("agentProfileSnapshot") != previous:
            raise RecurringWorkflowValidationError(
                "managed schedule Agent Profile snapshot identities conflict"
            )

        profile = await self._session.get(
            OmnigentAgentProfile,
            BOOTSTRAP_PROFILE_ID,
        )
        if profile is None or profile.active_version is None:
            raise RecurringWorkflowValidationError(
                "managed bootstrap Agent Profile is unavailable"
            )
        active = await self._session.scalar(
            select(OmnigentAgentProfileVersion).where(
                OmnigentAgentProfileVersion.profile_id == BOOTSTRAP_PROFILE_ID,
                OmnigentAgentProfileVersion.version == profile.active_version,
            )
        )
        if active is None:
            raise RecurringWorkflowValidationError(
                "managed bootstrap Agent Profile active version is unavailable"
            )
        execution = (
            active.document.get("execution")
            if isinstance(active.document, Mapping)
            else None
        )
        allowed_policy_refs = (
            execution.get("allowedLaunchPolicyRefs")
            if isinstance(execution, Mapping)
            else None
        )
        selected_policy_ref = (
            str(allowed_policy_refs[0]).strip()
            if isinstance(allowed_policy_refs, list) and allowed_policy_refs
            else ""
        )
        current = (
            previous.get("version") == active.version
            and previous.get("digest") == active.digest
        )
        provider_profile_ref = str(previous.get("providerProfileRef") or "").strip()
        if not current and selected_policy_ref and provider_profile_ref:
            host_binding = await self._session.scalar(
                select(OmnigentOAuthHostBindingRecord).where(
                    OmnigentOAuthHostBindingRecord.provider_profile_id
                    == provider_profile_ref
                )
            )
            # The schedule follows its provider's host binding onto the new
            # policy; a cutover defers the binding while its host still serves.
            if (
                host_binding is not None
                and host_binding.launch_policy_ref != selected_policy_ref
            ):
                return False
        if has_plan:
            # Recompiling advances the managed snapshot with the plan.
            return await self._refresh_omnigent_execution_plan_target(
                definition,
                target=target,
                initial_parameters=initial_parameters,
            )
        if current:
            return False

        # Single-user (#4351): schedule refresh uses the definition's real
        # frozen inputs, not a present-day user row. Legacy owner is
        # provenance only.
        actor = None
        refreshed = await refresh_managed_bootstrap_snapshot(
            self._session,
            parameters=initial_parameters,
            consumer_type="schedule",
            consumer_id=str(definition.id),
            user=actor,
            replace_existing_usage=True,
        )
        target["initialParameters"] = refreshed
        target["agentProfile"] = dict(refreshed["agentProfile"])
        target["agentProfileSnapshot"] = dict(refreshed["agentProfileSnapshot"])
        definition.target = target
        definition.updated_at = datetime.now(UTC)
        definition.version = int(definition.version or 0) + 1
        await self._session.flush()
        return True

    async def _admit_plan_less_omnigent_target(
        self,
        definition: RecurringWorkflowDefinition,
        *,
        target: dict[str, Any],
    ) -> bool:
        """Admit a stored plan-less Omnigent schedule through the plan owner.

        Definitions saved before creation and edits compiled a plan
        (MoonLadderStudios/MoonMind#3935) would otherwise start new plan-less
        work in the retained session supervisor on every occurrence.
        """

        if target.get("workflowType") not in _SUPPORTED_RECURRING_WORKFLOW_TYPES:
            return False
        previous_version = definition.version
        previous_target = definition.target
        # Single-user (#4351): the deployment refresh admits with the
        # instance's system principal, as for the other refresh paths.
        admitted = await self._admit_omnigent_schedule_target(
            definition_id=definition.id,
            target=target,
            agent_profile_selection=None,
            actor=None,
            require_actor=False,
        )
        if admitted is None:
            return False
        # Artifact persistence may commit its session. Reacquire the schedule
        # fence before publishing the admitted target.
        await self._session.refresh(definition, with_for_update=True)
        if (
            definition.version != previous_version
            or definition.target != previous_target
        ):
            raise RecurringWorkflowConflictError(
                "schedule changed during deployment refresh; retry from its current revision"
            )
        definition.target = admitted
        definition.updated_at = datetime.now(UTC)
        definition.version = int(definition.version or 0) + 1
        await self._session.flush()
        return True

    async def _schedule_plan_inputs_digest(
        self,
        *,
        target: Mapping[str, Any],
        initial_parameters: Mapping[str, Any],
        provider_profile: Any,
        binding: Any,
    ) -> str | None:
        """Digest what a schedule's plan is compiled from, or None if unknown.

        Compilation writes new artifacts, so a recompiled binding never equals
        the stored one; this digest decides currency instead. It covers the
        authored target (task, model, Skills, refreshed Agent Profile
        snapshot), the original task input, the Provider Profile identity that
        selects the credential materializer, the selected launch policy's
        runtime snapshot, and the exact deployed Omnigent server build.
        """

        from api_service.services.omnigent_execution_plan_service import (
            json_artifact_digest,
        )
        from api_service.services.omnigent_policies import (
            OmnigentPolicyService,
            PolicyConflict,
            PolicyNotFound,
        )
        from moonmind.omnigent.deployment_identity import (
            resolve_deployed_server_build_digest,
        )
        from moonmind.omnigent.harness_platform.failures import HarnessPlatformError

        snapshot = initial_parameters.get("agentProfileSnapshot") or {}
        try:
            policy_snapshot = await OmnigentPolicyService(
                self._session
            ).resolve_runtime_snapshot(str(snapshot.get("launchPolicyRef") or ""))
            server_build = resolve_deployed_server_build_digest()
        except (PolicyConflict, PolicyNotFound, HarnessPlatformError):
            # Compilation reports why this authority is not ready.
            return None
        return json_artifact_digest(
            {
                "initialParameters": {
                    key: value
                    for key, value in initial_parameters.items()
                    if key not in {"omnigentExecutionPlan", "resolvedSkillsetRef"}
                },
                "runtimeProviderTarget": target.get("runtimeProviderTarget"),
                "runtimeProviderTargetUpdatePolicy": str(
                    target.get("runtimeProviderTargetUpdatePolicy")
                    or SCHEDULE_TARGET_PINNED
                ).strip(),
                "taskInputSnapshotDigest": binding.task_input_snapshot_digest,
                "providerProfile": {
                    "profileId": getattr(provider_profile, "profile_id", None),
                    "runtimeId": getattr(provider_profile, "runtime_id", None),
                    "providerId": getattr(provider_profile, "provider_id", None),
                },
                "policySnapshotDigest": json_artifact_digest(policy_snapshot),
                "serverBuildDigest": server_build,
            }
        )

    async def _refresh_omnigent_execution_plan_target(
        self,
        definition: RecurringWorkflowDefinition,
        *,
        target: dict[str, Any],
        initial_parameters: dict[str, Any],
    ) -> bool:
        """Atomically advance time-limited admission evidence for a schedule."""

        previous_version = definition.version
        previous_target = definition.target

        from api_service.services.omnigent_execution_plan_service import (
            compile_and_persist_execution_plan,
        )
        from moonmind.omnigent.harness_platform.stores import (
            SessionExecutionPlanStore,
        )
        from moonmind.schemas.agent_runtime_models import (
            OmnigentExecutionPlanBinding,
        )
        from moonmind.workflows.temporal.artifacts import (
            TemporalArtifactRepository,
            TemporalArtifactService,
        )

        try:
            current_binding = OmnigentExecutionPlanBinding.model_validate(
                initial_parameters["omnigentExecutionPlan"]
            )
        except Exception as exc:
            raise RecurringWorkflowValidationError(
                "scheduled Omnigent execution-plan authority is invalid"
            ) from exc
        snapshot = target.get("agentProfileSnapshot")
        if not isinstance(snapshot, Mapping):
            raise RecurringWorkflowValidationError(
                "scheduled Omnigent Agent Profile snapshot is unavailable"
            )
        provider_profile_ref = str(snapshot.get("providerProfileRef") or "").strip()
        provider_profile = await self._session.get(
            ManagedAgentProviderProfile,
            provider_profile_ref,
        )
        if provider_profile is None:
            raise RecurringWorkflowValidationError(
                "scheduled Omnigent Provider Profile is unavailable"
            )
        # Single-user (#4351): execution-plan refresh is bound to the
        # schedule's frozen snapshot, not a present-day user row.
        actor = None
        if initial_parameters.get("agentProfileSnapshot") != snapshot:
            raise RecurringWorkflowValidationError(
                "scheduled Agent Profile snapshot identities conflict"
            )
        # The original task input survives every plan refresh. Its creator
        # records the admitted principal even when the deployment refresh is
        # run by a service or a legacy schedule owner no longer exists.
        task_artifact = await self._session.get(
            TemporalArtifact,
            current_binding.task_input_snapshot_ref.removeprefix(
                "artifact://"
            ).removeprefix("artifact:"),
        )
        principal = str(
            getattr(task_artifact, "created_by_principal", None) or ""
        ).strip()
        if not principal or getattr(
            task_artifact, "sha256", None
        ) != current_binding.task_input_snapshot_digest.removeprefix("sha256:"):
            raise RecurringWorkflowValidationError(
                "scheduled original task-input principal authority is unavailable "
                "or does not match the frozen snapshot"
            )
        initial_parameters = await refresh_schedule_deployment_snapshot(
            self._session,
            parameters=initial_parameters,
            consumer_id=str(definition.id),
            user=actor,
        )
        snapshot = initial_parameters["agentProfileSnapshot"]
        inputs_digest = await self._schedule_plan_inputs_digest(
            target=target,
            initial_parameters=initial_parameters,
            provider_profile=provider_profile,
            binding=current_binding,
        )
        if inputs_digest is not None and _SCHEDULE_PLAN_INPUTS.get(
            str(definition.id)
        ) == (current_binding.plan_ref, inputs_digest):
            return False
        artifact_service = self._artifact_service or TemporalArtifactService(
            TemporalArtifactRepository(self._session)
        )
        compilation_parameters = dict(initial_parameters)
        compilation_parameters.pop("omnigentExecutionPlan", None)
        compilation_parameters.pop("resolvedSkillsetRef", None)
        try:
            persisted_plan = await compile_and_persist_execution_plan(
                session_factory=None,
                execution_plan_store=SessionExecutionPlanStore(self._session),
                artifact_service=artifact_service,
                principal=principal,
                workflow_id=f"mm-schedule:{definition.id}",
                agent_profile_snapshot=dict(snapshot),
                provider_profile=provider_profile,
                initial_parameters=compilation_parameters,
                authored_request_ref=current_binding.task_input_snapshot_ref,
                authored_request_digest=(
                    current_binding.task_input_snapshot_digest
                ),
                task_input_snapshot_ref=current_binding.task_input_snapshot_ref,
                task_input_snapshot_digest=(
                    current_binding.task_input_snapshot_digest
                ),
                db_session=self._session,
            )
        except Exception as exc:
            raise RecurringWorkflowValidationError(
                f"could not refresh scheduled Omnigent authority: {exc}"
            ) from exc
        # Strict support evidence is time-limited and admitted repository
        # authority comes from mutable connections, so those plans are
        # recompiled every pass instead of being reused.
        compiled_payload = persisted_plan.envelope.payload
        admission = getattr(compiled_payload, "admissionAuthority", None)
        if (
            inputs_digest is None
            or getattr(admission, "admissionMode", None) == "strict"
            or (getattr(compiled_payload, "resolvedTools", None) or {}).get(
                "repositoryAccess"
            )
        ):
            _SCHEDULE_PLAN_INPUTS.pop(str(definition.id), None)
        else:
            _SCHEDULE_PLAN_INPUTS[str(definition.id)] = (
                persisted_plan.binding.plan_ref,
                inputs_digest,
            )
        # MoonLadderStudios/MoonMind#3833: a schedule pins its runtime-provider
        # target. Advancing time-limited admission evidence must never silently
        # move the schedule onto a different harness, realizer, or rollout row.
        pinned_target = target.get("runtimeProviderTarget")
        current_target_payload = getattr(
            persisted_plan, "runtime_provider_rollout", None
        )
        current_target_payload = (
            dict(current_target_payload) if current_target_payload else None
        )
        update_policy = str(
            target.get("runtimeProviderTargetUpdatePolicy")
            or SCHEDULE_TARGET_PINNED
        ).strip()
        if update_policy not in _SCHEDULE_TARGET_UPDATE_POLICIES:
            raise RecurringWorkflowValidationError(
                "unsupported scheduled runtime-provider target update policy: "
                f"{update_policy!r}"
            )
        if (
            isinstance(pinned_target, Mapping)
            and current_target_payload is not None
            and str(pinned_target.get("targetId") or "")
            != str(current_target_payload.get("targetId") or "")
        ):
            if update_policy == SCHEDULE_TARGET_PINNED:
                raise RecurringWorkflowValidationError(
                    "scheduled Omnigent runtime-provider target changed from "
                    f"{pinned_target.get('targetId')!r} to "
                    f"{current_target_payload.get('targetId')!r}; the schedule "
                    "pins its target, so an explicit schedule revision is "
                    "required before the new target is used"
                )
            # follow_qualified_default: adopting a newly qualified target is a
            # schedule default change, so it advances the schedule revision
            # below alongside the new plan authority.

        if (
            persisted_plan.binding == current_binding
            and pinned_target == current_target_payload
        ):
            return False

        # Artifact persistence may commit its session. Reacquire the schedule
        # fence before publishing usage and definition authority together.
        await self._session.refresh(definition, with_for_update=True)
        if (
            definition.version != previous_version
            or definition.target != previous_target
        ):
            raise RecurringWorkflowConflictError(
                "schedule changed during deployment refresh; retry from its current revision"
            )
        # Compilation persists artifacts with commits, so its profile and
        # policy reads no longer fence a concurrent bootstrap cutover. Lock
        # fresh authority rows until the schedule action has been published.
        if snapshot.get("document", {}).get("schemaVersion") == "moonmind.omnigent-agent-profile.v2":
            from api_service.services.omnigent_policies import OmnigentPolicyService

            profile = await self._session.scalar(
                select(OmnigentAgentProfile)
                .where(OmnigentAgentProfile.profile_id == snapshot["profileId"])
                .execution_options(populate_existing=True)
                .with_for_update()
            )
            if (
                profile is None
                or profile.state != "active"
                or profile.active_version != snapshot["version"]
            ):
                raise RecurringWorkflowConflictError(
                    "Agent Profile changed during deployment refresh; retry from current authority"
                )
            policy_id, policy_version = snapshot["launchPolicyRef"].rsplit("@", 1)
            policy = await self._session.scalar(
                select(OmnigentPolicyVersion)
                .where(
                    OmnigentPolicyVersion.policy_id == policy_id,
                    OmnigentPolicyVersion.version == int(policy_version),
                )
                .execution_options(populate_existing=True)
                .with_for_update()
            )
            if policy is None:
                raise RecurringWorkflowConflictError(
                    "launch policy disappeared during deployment refresh"
                )
            await OmnigentPolicyService(self._session).resolve_runtime_snapshot(
                snapshot["launchPolicyRef"]
            )

        from moonmind.omnigent.deployment_identity import (
            assert_plan_matches_deployed_runtime,
        )

        await assert_plan_matches_deployed_runtime(persisted_plan.envelope.payload)
        if snapshot != previous_target.get("agentProfileSnapshot"):
            usage = await self._session.scalar(
                select(OmnigentAgentProfileUsage).where(
                    OmnigentAgentProfileUsage.consumer_type == "schedule",
                    OmnigentAgentProfileUsage.consumer_id == str(definition.id),
                )
            )
            if usage is None or usage.effective_snapshot != previous_target.get(
                "agentProfileSnapshot"
            ):
                raise RecurringWorkflowValidationError(
                    "scheduled Agent Profile usage changed during deployment refresh"
                )
            usage.version = snapshot["version"]
            usage.digest = snapshot["digest"]
            usage.effective_snapshot = dict(snapshot)

        if current_target_payload is not None:
            target["runtimeProviderTarget"] = current_target_payload
        target.setdefault("runtimeProviderTargetUpdatePolicy", update_policy)
        initial_parameters["omnigentExecutionPlan"] = (
            persisted_plan.binding.model_dump(
                mode="json", by_alias=True, exclude_none=True
            )
        )
        initial_parameters["resolvedSkillsetRef"] = (
            persisted_plan.resolved_skillset_ref
        )
        target["initialParameters"] = initial_parameters
        target["agentProfileSnapshot"] = dict(snapshot)
        if "agentProfile" in initial_parameters:
            target["agentProfile"] = dict(initial_parameters["agentProfile"])
        target["omnigentAuthorityArtifactRefs"] = [
            current_binding.task_input_snapshot_ref,
            *persisted_plan.artifact_refs,
        ]
        definition.target = target
        definition.updated_at = datetime.now(UTC)
        definition.version = int(definition.version or 0) + 1
        await self._session.flush()
        return True

    async def refresh_managed_bootstrap_schedules(
        self, limit: int = 500
    ) -> ManagedScheduleRefresh:
        """Refresh scheduled actions after a managed bootstrap policy cutover.

        Individual schedule failures are contained so one broken definition
        cannot block the others, startup reconciliation, or a deployment
        update. Each failure is returned with its reason; the API's bootstrap
        reconciliation retries it on its next pass.
        """

        batch_size = max(1, int(limit))
        definition_ids: list[UUID] = []
        last_definition_id: UUID | None = None
        while True:
            statement = select(RecurringWorkflowDefinition.id).where(
                RecurringWorkflowDefinition.temporal_schedule_id.is_not(None),
            )
            if last_definition_id is not None:
                statement = statement.where(
                    RecurringWorkflowDefinition.id > last_definition_id
                )
            batch = list(
                (
                    await self._session.execute(
                        statement.order_by(RecurringWorkflowDefinition.id).limit(
                            batch_size
                        )
                    )
                ).scalars()
            )
            if not batch:
                break
            definition_ids.extend(batch)
            last_definition_id = batch[-1]
            if len(batch) < batch_size:
                break

        refreshed = 0
        failed: list[str] = []
        for definition_id in definition_ids:
            try:
                definition = await self._lock_definition_for_update(definition_id)
                if not await self._refresh_managed_bootstrap_target(definition):
                    continue
                # Update Temporal before committing the corresponding DB
                # authority. A crash between the two is repaired idempotently:
                # the next pass observes the current action and commits the DB.
                await self._ensure_schedule_action_current(definition)
                await self._session.commit()
                refreshed += 1
            except Exception as exc:
                await self._session.rollback()
                # The caller may be a release updater whose container is gone
                # by the time an operator looks, so carry the reason with the
                # identifier instead of leaving it only in this log line.
                from moonmind.utils.logging import redact_sensitive_text

                failed.append(
                    f"{definition_id}: {redact_sensitive_text(str(exc))[:500]}"
                )
                logger.warning(
                    "Failed to refresh managed bootstrap schedule %s: %s",
                    definition_id,
                    exc,
                )
        return ManagedScheduleRefresh(refreshed=refreshed, failures=tuple(failed))

    async def _validate_model_selection_submission(
        self,
        target: Mapping[str, Any],
        *,
        principal: str,
        saved_target: Mapping[str, Any] | None = None,
    ) -> None:
        async def read_input_artifact(ref: str) -> Any:
            from moonmind.workflows.temporal.artifacts import (
                TemporalArtifactRepository,
                TemporalArtifactService,
            )

            service = self._artifact_service or TemporalArtifactService(
                TemporalArtifactRepository(self._session)
            )
            try:
                _artifact, body = await service.read(
                    artifact_id=ref.removeprefix("artifact://").removeprefix("input/"),
                    principal=principal,
                    allow_restricted_raw=True,
                )
                import json

                return json.loads(body.decode("utf-8"))
            except Exception as exc:
                raise RecurringWorkflowValidationError(
                    f"Cannot validate model selection in input artifact {ref}: {exc}"
                ) from exc

        try:
            await validate_model_selection_submission(
                target,
                saved_payload=saved_target,
                read_input_artifact=read_input_artifact,
                field_name="target",
            )
        except RuntimeIntentValidationError as exc:
            raise RecurringWorkflowValidationError(str(exc)) from exc

    async def _admit_omnigent_schedule_target(
        self,
        *,
        definition_id: UUID,
        target: Mapping[str, Any],
        agent_profile_selection: Mapping[str, Any] | None,
        actor: User | None,
        require_actor: bool = True,
    ) -> dict[str, Any] | None:
        """Compile the execution plan an Omnigent schedule target launches with.

        Creation, target edits and the deployment refresh of stored plan-less
        definitions share this one admission owner
        (MoonLadderStudios/MoonMind#3935). Returns the admitted target, or
        None when the target selects another runtime or already carries
        launch authority.
        """

        initial_parameters = dict(target.get("initialParameters") or {})
        authored_profile = resolve_launch_target_profile_selection(target)
        # An omitted runtime that defaults to Omnigent is admitted like an
        # explicit one. Otherwise the schedule would launch plan-less work
        # into the retained session supervisor.
        defaults_to_omnigent = (
            not authored_profile.runtime_ids
            and resolve_runtime_target_selection(
                surface=AuthoringSurface.schedule,
                workflow_settings=settings.workflow,
                record_metrics=False,
            ).runtime_id
            == "omnigent"
        )
        selects_omnigent = (
            "omnigent" in authored_profile.runtime_ids or defaults_to_omnigent
        )
        retained_snapshot = None
        if (
            selects_omnigent
            and agent_profile_selection is None
            and initial_parameters.get("agentProfileSnapshot")
            and not initial_parameters.get("omnigentExecutionPlan")
        ):
            supplied_snapshot = initial_parameters["agentProfileSnapshot"]
            usage = await self._session.scalar(
                select(OmnigentAgentProfileUsage).where(
                    OmnigentAgentProfileUsage.consumer_type == "schedule",
                    OmnigentAgentProfileUsage.consumer_id == str(definition_id),
                )
            )
            if (
                not isinstance(supplied_snapshot, Mapping)
                or usage is None
                or usage.profile_id != supplied_snapshot.get("profileId")
                or usage.version != supplied_snapshot.get("version")
                or usage.digest != supplied_snapshot.get("digest")
                or usage.effective_snapshot != supplied_snapshot
            ):
                raise RecurringWorkflowValidationError(
                    "an unverified Agent Profile snapshot without an execution "
                    "plan cannot authorize a schedule; submit an agentProfile "
                    "selection for new launch authority"
                )
            # A retained definition already owns this exact frozen authority.
            # Derive its missing plan without reselecting present-day defaults
            # or accepting authored changes to the server-owned snapshot.
            retained_snapshot = copy.deepcopy(dict(usage.effective_snapshot))
        needs_profile_snapshot = (
            selects_omnigent
            and not initial_parameters.get("agentProfileSnapshot")
            and not initial_parameters.get("omnigentExecutionPlan")
        )
        if (
            agent_profile_selection is None
            and not needs_profile_snapshot
            and retained_snapshot is None
        ):
            return None
        if require_actor and actor is None:
            raise RecurringWorkflowValidationError(
                "an authenticated actor is required for agent profile selection"
            )
        if agent_profile_selection is not None:
            snapshot = await resolve_agent_profile_snapshot(
                self._session, selection=agent_profile_selection,
                consumer_type="schedule", consumer_id=str(definition_id), user=actor,
            )
        elif retained_snapshot is not None:
            snapshot = retained_snapshot
        else:
            snapshot = await resolve_default_agent_profile_snapshot(
                self._session,
                provider_profile_ref=authored_profile.profile_id,
                launch_policy_ref=(initial_parameters.get("omnigent") or {}).get("launchPolicyRef"),
                consumer_type="schedule", consumer_id=str(definition_id), user=actor,
            )
        initial_parameters = dict(target.get("initialParameters") or {})
        if defaults_to_omnigent:
            initial_parameters["targetRuntime"] = "omnigent"
        initial_parameters = compile_agent_profile_snapshot_parameters(
            initial_parameters,
            snapshot=snapshot,
        )
        target_runtime = str(
            initial_parameters.get("targetRuntime") or ""
        ).strip().lower()
        if target_runtime != "omnigent":
            raise RecurringWorkflowValidationError(
                "agent profile schedules require targetRuntime='omnigent'"
            )
        from api_service.services.omnigent_execution_plan_service import (
            compile_and_persist_execution_plan,
            persist_json_artifact,
        )
        from moonmind.omnigent.harness_platform.stores import (
            SessionExecutionPlanStore,
        )
        from moonmind.workflows.temporal.artifacts import (
            TemporalArtifactRepository,
            TemporalArtifactService,
        )

        provider_profile = await self._session.get(
            ManagedAgentProviderProfile,
            str(snapshot["providerProfileRef"]),
        )
        if provider_profile is None:
            raise RecurringWorkflowValidationError(
                "selected Provider Profile disappeared before plan compilation"
            )
        if needs_profile_snapshot and agent_profile_selection is None:
            from moonmind.workflows.executions.model_resolver import (
                resolve_model_effort,
            )

            authored_parameters = target.get("initialParameters") or {}
            task_intent = (
                authored_parameters.get("workflow")
                or authored_parameters.get("task")
                or {}
            )
            runtime_intent = task_intent.get("runtime") or {}
            resolved = resolve_model_effort(
                runtime_id=provider_profile.runtime_id,
                profile=provider_profile,
                # Raw schedule fields may still author a legacy flat pair.
                # Only a nested selection supersedes that saved intent.
                authored_runtime=(
                    runtime_intent
                    if model_selection_fields(runtime_intent)
                    else None
                ),
                requested_model=runtime_intent.get(
                    "model", authored_parameters.get("model")
                ),
                requested_effort=runtime_intent.get(
                    "effort", authored_parameters.get("effort")
                ),
                requested_model_tier=runtime_intent.get(
                    "modelTier", authored_parameters.get("modelTier")
                ),
                tier_fallback=runtime_intent.get(
                    "tierFallback", authored_parameters.get("tierFallback", "clamp")
                ),
                require_launch_ready=False,
            )
            initial_parameters.update(
                model=resolved.model,
                effort=resolved.effort,
                modelSource=resolved.model_source,
            )
        principal = str(getattr(actor, "id", "") or "system")
        artifact_service = self._artifact_service or TemporalArtifactService(
            TemporalArtifactRepository(self._session)
        )
        task_snapshot = {
            "snapshotVersion": "original-task-input/v1",
            "source": {
                "kind": "schedule",
                "definitionId": str(definition_id),
            },
            "target": {
                **target,
                "initialParameters": initial_parameters,
            },
        }
        task_input_snapshot_ref, task_input_snapshot_digest = (
            await persist_json_artifact(
                artifact_service=artifact_service,
                principal=principal,
                artifact_class="original_task_input_snapshot",
                payload=task_snapshot,
            )
        )
        try:
            persisted_plan = await compile_and_persist_execution_plan(
                session_factory=None,
                execution_plan_store=SessionExecutionPlanStore(
                    self._session
                ),
                artifact_service=artifact_service,
                principal=principal,
                workflow_id=f"mm-schedule:{definition_id}",
                agent_profile_snapshot=snapshot,
                provider_profile=provider_profile,
                initial_parameters=initial_parameters,
                authored_request_ref=task_input_snapshot_ref,
                authored_request_digest=task_input_snapshot_digest,
                task_input_snapshot_ref=task_input_snapshot_ref,
                task_input_snapshot_digest=task_input_snapshot_digest,
                db_session=self._session,
            )
        except Exception as exc:
            raise RecurringWorkflowValidationError(
                f"invalid Omnigent schedule authority: {exc}"
            ) from exc
        initial_parameters["omnigentExecutionPlan"] = (
            persisted_plan.binding.model_dump(
                mode="json", by_alias=True, exclude_none=True
            )
        )
        initial_parameters["resolvedSkillsetRef"] = (
            persisted_plan.resolved_skillset_ref
        )
        # Seed the schedule pin from the creation result so the first
        # refresh compares against the initially compiled target instead of
        # None (MoonLadderStudios/MoonMind#3988).
        created_rollout = getattr(
            persisted_plan, "runtime_provider_rollout", None
        )
        created_target = (
            dict(created_rollout) if created_rollout is not None else None
        )
        admitted = {
            **target,
            "agentProfile": {
                "profileId": snapshot["profileId"],
                "version": snapshot["version"],
                "digest": snapshot["digest"],
            },
            "agentProfileSnapshot": snapshot,
            "initialParameters": initial_parameters,
            "omnigentAuthorityArtifactRefs": [
                task_input_snapshot_ref,
                *persisted_plan.artifact_refs,
            ],
        }
        if created_target is not None:
            admitted["runtimeProviderTarget"] = created_target
            admitted["runtimeProviderTargetUpdatePolicy"] = SCHEDULE_TARGET_PINNED
        return admitted

    async def create_definition(
        self,
        *,
        name: str,
        description: str | None,
        enabled: bool,
        schedule_type: str,
        cron: str,
        timezone: str,
        scope_type: str,
        scope_ref: str | None,
        owner_user_id: UUID | None,
        target: Mapping[str, Any],
        policy: Mapping[str, Any] | None,
        agent_profile_selection: Mapping[str, Any] | None = None,
        actor: User | None = None,
    ) -> RecurringWorkflowDefinition:
        schedule_kind = _normalize_schedule_type(schedule_type)
        cron_normalized = str(cron or "").strip()
        parse_cron_expression(cron_normalized)
        timezone_name = validate_timezone_name(timezone)
        name_text = _clean_text(name, field_name="name", required=True) or ""
        await self._validate_model_selection_submission(
            target,
            principal=str(getattr(actor, "id", None) or owner_user_id or "system"),
        )
        target_payload = _normalize_target(_json_object(target, field_name="target"))
        # The stored target's initialParameters are what a later schedule action
        # launches, so the runtime/Provider Profile pair is validated here,
        # before anything is persisted.
        await require_launch_target_provider_profile_runtime(
            session=self._session,
            target=target_payload,
        )
        policy_payload = _json_object(policy, field_name="policy")
        policy_obj = _normalize_policy(
            policy_payload,
            global_max_backfill=_DEFAULT_SCHEDULER_MAX_BACKFILL,
        )
        scope = _normalize_scope_type(scope_type)

        # Single-user (#4351): new schedules carry no human owner. Legacy
        # ``owner_user_id`` values remain stored as non-authoritative
        # provenance; ``None`` is the normal value for instance schedules.

        now = datetime.now(UTC)
        next_run_at = compute_next_occurrence(
            cron=cron_normalized,
            timezone_name=timezone_name,
            after=now,
        )

        definition_id = uuid4()
        # Admit before the definition joins the session: artifact persistence
        # commits the shared session, so a later plan or Temporal failure
        # could otherwise leave a committed definition without a schedule.
        admitted_target = await self._admit_omnigent_schedule_target(
            definition_id=definition_id,
            target=target_payload,
            agent_profile_selection=agent_profile_selection,
            actor=actor,
        )
        if admitted_target is not None:
            target_payload = admitted_target
        definition = RecurringWorkflowDefinition(
            id=definition_id,
            name=name_text,
            description=_clean_text(description, field_name="description"),
            enabled=bool(enabled),
            schedule_type=schedule_kind,
            cron=cron_normalized,
            timezone=timezone_name,
            next_run_at=next_run_at,
            owner_user_id=owner_user_id,
            scope_type=scope,
            scope_ref=_clean_text(scope_ref, field_name="scopeRef"),
            target=target_payload,
            policy=policy_payload,
            temporal_schedule_id=f"mm-schedule:{definition_id}",
            created_at=now,
            updated_at=now,
            version=1,
        )
        self._session.add(definition)
        await self._session.flush()

        workflow_type, workflow_input = self._workflow_bundle_for_target(
            definition_id=definition_id,
            name=name_text,
            owner_user_id=owner_user_id,
            # Profile resolution above replaces the authored selection with the
            # immutable launch snapshot. Build the durable schedule input from
            # that authoritative target, not the pre-resolution request copy.
            target_payload=definition.target,
        )

        try:
            await self._adapter.create_schedule(
                definition_id=definition_id,
                cron_expression=cron_normalized,
                timezone=timezone_name,
                overlap_mode=policy_obj.overlap_mode,
                catchup_mode=policy_obj.catchup_mode,
                jitter_seconds=policy_obj.jitter_seconds,
                enabled=bool(enabled),
                note=name_text,
                workflow_type=workflow_type,
                workflow_input=workflow_input,
                **await self._workflow_start_metadata(
                    definition_id=definition_id,
                    owner_user_id=owner_user_id,
                    workflow_input=workflow_input,
                ),
            )
        except Exception as exc:
            logger.error(f"Failed to create temporal schedule for {definition_id}: {exc}")
            raise RecurringWorkflowValidationError(f"Failed to create schedule: {exc}")

        await self._session.refresh(definition)
        await self._session.commit()
        return definition

    async def update_definition(
        self,
        definition: RecurringWorkflowDefinition,
        *,
        name: str | None = None,
        description: str | None = None,
        enabled: bool | None = None,
        cron: str | None = None,
        timezone: str | None = None,
        target: Mapping[str, Any] | None = None,
        policy: Mapping[str, Any] | None = None,
        scope_ref: str | None = None,
        expected_version: int | None = None,
        actor: User | None = None,
    ) -> RecurringWorkflowDefinition:
        definition = await self._lock_definition_for_update(definition.id)
        if expected_version is not None and int(definition.version or 0) != int(
            expected_version
        ):
            raise RecurringWorkflowConflictError(
                "recurring workflow changed since it was loaded; refresh and retry"
            )

        # Normalize and validate the replacement target before any field of the
        # locked definition is mutated, so a rejected edit leaves the stored
        # target untouched.
        normalized_target: dict[str, Any] | None = None
        if target is not None:
            await self._validate_model_selection_submission(
                target,
                principal=str(
                    getattr(actor, "id", None) or definition.owner_user_id or "system"
                ),
                saved_target=definition.target,
            )
            normalized_target = _normalize_target(
                _json_object(target, field_name="target")
            )
            await require_launch_target_provider_profile_runtime(
                session=self._session,
                target=normalized_target,
            )

        # MoonLadderStudios/MoonMind#4188: a stored retired ManifestIngest
        # target must never be re-enabled or re-dispatched through an
        # update. Reject before any field mutation or adapter side effect
        # so a paused definition cannot restart retired work via
        # unpause/resume or schedule edits. An explicit disable
        # (enabled=False) still proceeds so operators can pause retired
        # definitions, matching the reconcile cutover.
        if enabled is not False:
            effective_target = (
                normalized_target
                if normalized_target is not None
                else definition.target
            )
            if isinstance(effective_target, Mapping):
                effective_workflow_type = str(
                    effective_target.get("workflowType")
                    or effective_target.get("workflow_type")
                    or ""
                ).strip()
                if effective_workflow_type == "MoonMind.ManifestIngest":
                    raise RecurringWorkflowValidationError(
                        "MoonMind.ManifestIngest was retired "
                        "(MoonLadderStudios/MoonMind#4192): the new release does not "
                        "register or launch manifest ingest workflows."
                    )

        if normalized_target is not None:
            current_plan = (
                (definition.target or {}).get("initialParameters") or {}
            ).get("omnigentExecutionPlan")
            next_parameters = normalized_target.get("initialParameters") or {}
            if isinstance(current_plan, Mapping):
                if next_parameters.get("omnigentExecutionPlan") != current_plan:
                    raise RecurringWorkflowValidationError(
                        "editing an admitted Omnigent schedule requires an "
                        "explicit replacement plan"
                    )
            else:
                # MoonLadderStudios/MoonMind#3935: an edit that names Omnigent,
                # or omits the runtime under an Omnigent default, is admitted
                # by the same plan owner as creation instead of storing a
                # plan-less target for the retained session supervisor.
                locked_version = definition.version
                locked_target = definition.target
                admitted_target = await self._admit_omnigent_schedule_target(
                    definition_id=definition.id,
                    target=normalized_target,
                    agent_profile_selection=None,
                    actor=actor,
                )
                if admitted_target is not None:
                    # Artifact persistence may commit its session. Reacquire
                    # the schedule fence before applying the edit.
                    await self._session.refresh(definition, with_for_update=True)
                    if (
                        definition.version != locked_version
                        or definition.target != locked_target
                    ):
                        raise RecurringWorkflowConflictError(
                            "recurring workflow changed since it was loaded; "
                            "refresh and retry"
                        )
                    normalized_target = admitted_target

        changed_schedule = False
        now = datetime.now(UTC)

        cron_normalized = cron
        if cron is not None:
            cron_normalized = str(cron or "").strip()
            parse_cron_expression(cron_normalized)

        if name is not None:
            definition.name = _clean_text(name, field_name="name", required=True) or ""
        if description is not None:
            definition.description = _clean_text(
                description,
                field_name="description",
            )
        if enabled is not None:
            definition.enabled = bool(enabled)
        if cron_normalized is not None:
            definition.cron = cron_normalized
            changed_schedule = True
        if timezone is not None:
            definition.timezone = validate_timezone_name(timezone)
            changed_schedule = True
        if normalized_target is not None:
            definition.target = normalized_target

        policy_obj = None
        if policy is not None:
            normalized_policy_payload = _json_object(policy, field_name="policy")
            policy_obj = _normalize_policy(
                normalized_policy_payload,
                global_max_backfill=_DEFAULT_SCHEDULER_MAX_BACKFILL,
            )
            definition.policy = normalized_policy_payload

        if scope_ref is not None:
            definition.scope_ref = _clean_text(scope_ref, field_name="scopeRef")

        if changed_schedule or enabled is not None:
            basis = now
            if definition.last_scheduled_for is not None:
                basis = max(basis, _coerce_utc(definition.last_scheduled_for))
            definition.next_run_at = compute_next_occurrence(
                cron=definition.cron,
                timezone_name=definition.timezone,
                after=basis,
            )

        try:
            if enabled is False:
                try:
                    await self._adapter.pause_schedule(definition_id=definition.id)
                except ScheduleNotFoundError:
                    logger.info(
                        "Retired schedule already absent for %s; continuing disable",
                        definition.id,
                    )
                # MoonLadderStudios/MoonMind#4188: explicit disable of a
                # stored retired ManifestIngest definition must persist
                # without rebuilding the retired workflow bundle. Pausing
                # above stops new runs; skip update_schedule so the
                # disable commits instead of raising.
                try:
                    _disable_target = (
                        dict(definition.target)
                        if isinstance(definition.target, Mapping)
                        else {}
                    )
                    _disable_workflow_type = str(
                        _disable_target.get("workflowType")
                        or _disable_target.get("workflow_type")
                        or ""
                    ).strip()
                except Exception:
                    _disable_workflow_type = ""
                if _disable_workflow_type == "MoonMind.ManifestIngest":
                    definition.updated_at = now
                    definition.version = int(definition.version or 0) + 1
                    await self._session.flush()
                    await self._session.refresh(definition)
                    await self._session.commit()
                    return definition
            elif enabled is True:
                await self._adapter.unpause_schedule(definition_id=definition.id)

            workflow_type, workflow_input = self._workflow_bundle_for_definition(
                definition
            )
            await self._adapter.update_schedule(
                definition_id=definition.id,
                cron_expression=cron_normalized,
                timezone=timezone,
                overlap_mode=policy_obj.overlap_mode if policy_obj else None,
                catchup_mode=policy_obj.catchup_mode if policy_obj else None,
                jitter_seconds=policy_obj.jitter_seconds if policy_obj else None,
                enabled=enabled,
                note=name if name is not None else None,
                workflow_type=workflow_type,
                workflow_input=workflow_input,
                **await self._workflow_start_metadata(
                    definition_id=definition.id,
                    owner_user_id=definition.owner_user_id,
                    workflow_input=workflow_input,
                ),
            )
        except Exception as exc:
            logger.error(f"Failed to update temporal schedule for {definition.id}: {exc}")
            raise RecurringWorkflowValidationError(f"Failed to update schedule: {exc}")

        definition.updated_at = now
        definition.version = int(definition.version or 0) + 1
        await self._session.flush()
        await self._session.refresh(definition)
        await self._session.commit()
        return definition

    @staticmethod
    def _record_trigger_observation(run, observation):
        if not isinstance(observation, ScheduleTriggerResult):
            return
        if observation.disposition == "skipped":
            run.outcome = RecurringWorkflowRunOutcome.SKIPPED
            run.message = (
                observation.message or "Already running; no new execution started."
            )
        elif (
            observation.disposition == "started"
            and observation.workflow_id
            and observation.run_id
        ):
            run.outcome = RecurringWorkflowRunOutcome.ENQUEUED
            run.message = "Execution started."
        else:
            return
        run.temporal_workflow_id = observation.workflow_id
        run.temporal_run_id = observation.run_id
        run.updated_at = datetime.now(UTC)
        run.dispatch_after = None

    async def create_manual_run(
        self,
        definition: RecurringWorkflowDefinition,
        *,
        request_id: UUID | None = None,
    ) -> RecurringWorkflowRun:
        request_id = request_id or uuid4()
        # Serialize product requests on the existing definition, including
        # response-loss retries with the same request ID.
        await self._session.execute(
            select(RecurringWorkflowDefinition)
            .where(RecurringWorkflowDefinition.id == definition.id)
            .with_for_update()
        )
        existing = await self._session.get(RecurringWorkflowRun, request_id)
        if existing is not None:
            if existing.definition_id != definition.id:
                raise RecurringWorkflowValidationError(
                    "Run request belongs to another definition"
                )
            return existing
        await self._ensure_schedule_action_current(definition)
        now = datetime.now(UTC)
        # The row owns observation before any external effect, including an
        # uncertain RPC response. Never automatically resubmit a pending row.
        run = RecurringWorkflowRun(
            id=request_id,
            definition_id=definition.id,
            scheduled_for=now,
            trigger=RecurringWorkflowRunTrigger.MANUAL,
            outcome=RecurringWorkflowRunOutcome.PENDING_DISPATCH,
            dispatch_attempts=1,
            dispatch_after=now,
            created_at=now,
            updated_at=now,
            message="Request recorded; waiting for Temporal execution evidence.",
        )
        self._session.add(run)
        await self._session.commit()
        try:
            observation = await self._adapter.trigger_schedule(
                definition_id=definition.id,
                request_id=str(run.id),
                scheduled_at=run.scheduled_for,
            )
            self._record_trigger_observation(run, observation)
        except Exception:
            # The service may have accepted a request whose acknowledgement was
            # lost. Take one immediate read-only observation for this exact
            # identity before leaving the row pending: if Temporal accepted
            # the trigger, adopt its evidence now instead of waiting for the
            # background sweep. Never resubmit the trigger here.
            logger.warning(
                "Manual trigger observation pending for %s", run.id, exc_info=True
            )
            try:
                observation = await self._adapter.observe_schedule_trigger(
                    definition_id=definition.id,
                    scheduled_at=run.scheduled_for,
                )
                self._record_trigger_observation(run, observation)
            except Exception:
                logger.warning(
                    "Manual trigger evidence unavailable for %s",
                    run.id,
                    exc_info=True,
                )
        definition.last_scheduled_for = run.scheduled_for
        definition.last_dispatch_status = run.outcome.value
        definition.last_dispatch_error = None
        definition.updated_at = datetime.now(UTC)
        await self._session.commit()
        return run

    async def reconcile_manual_runs(self, *, limit: int = 100) -> int:
        """Observe requests within a bounded budget, without uncertain resubmission."""
        now = datetime.now(UTC)
        runs = (
            (
                await self._session.execute(
                    select(RecurringWorkflowRun)
                    .where(
                        RecurringWorkflowRun.trigger
                        == RecurringWorkflowRunTrigger.MANUAL,
                        or_(
                            RecurringWorkflowRun.outcome
                            == RecurringWorkflowRunOutcome.PENDING_DISPATCH,
                            and_(
                                RecurringWorkflowRun.outcome
                                == RecurringWorkflowRunOutcome.ENQUEUED,
                                or_(
                                    RecurringWorkflowRun.temporal_workflow_id.is_(None),
                                    RecurringWorkflowRun.temporal_run_id.is_(None),
                                ),
                            ),
                        ),
                        or_(
                            RecurringWorkflowRun.dispatch_after.is_(None),
                            RecurringWorkflowRun.dispatch_after <= now,
                        ),
                    )
                    .order_by(RecurringWorkflowRun.dispatch_after)
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
        for run in runs:
            if run.outcome == RecurringWorkflowRunOutcome.ENQUEUED:
                run.outcome = RecurringWorkflowRunOutcome.PENDING_DISPATCH
                run.message = (
                    "Historical request has no exact Temporal execution evidence."
                )
            run.dispatch_after = now + timedelta(seconds=30)
            try:
                async with asyncio.timeout(5):
                    observation = await self._adapter.observe_schedule_trigger(
                        definition_id=run.definition_id, scheduled_at=run.scheduled_for
                    )
                self._record_trigger_observation(run, observation)
            except Exception:
                logger.warning(
                    "Manual trigger evidence unavailable for %s",
                    run.id,
                    exc_info=True,
                )
            if (
                run.outcome == RecurringWorkflowRunOutcome.PENDING_DISPATCH
                and now >= _coerce_utc(run.created_at) + timedelta(minutes=5)
            ):
                # This is an unconfirmed request, not proof of workflow failure
                # or permission to repeat a possibly accepted external effect.
                run.outcome = RecurringWorkflowRunOutcome.DISPATCH_ERROR
                run.message = (
                    "Request acceptance could not be confirmed within five minutes. "
                    "Check schedule history before retrying Run now."
                )
                run.dispatch_after = None
                run.updated_at = now
            # Preserve each observation if the outer sweep budget expires.
            await self._session.commit()
        return len(runs)

    async def delete_definition(
        self,
        definition: RecurringWorkflowDefinition,
    ) -> None:
        try:
            await self._adapter.delete_schedule(definition_id=definition.id)
        except ScheduleNotFoundError:
            logger.info(
                "Temporal schedule already absent while deleting recurring definition %s",
                definition.id,
            )
        except ScheduleAdapterError as exc:
            logger.error(
                "Failed to delete temporal schedule for %s: %s",
                definition.id,
                exc,
            )
            raise RecurringWorkflowValidationError(
                f"Failed to delete schedule: {exc}"
            ) from exc

        await self._session.delete(definition)
        await self._session.commit()

    def _workflow_bundle_for_definition(
        self, dfn: RecurringWorkflowDefinition
    ) -> tuple[str, dict[str, Any]]:
        target_payload = (
            dict(dfn.target) if isinstance(dfn.target, Mapping) else {}
        )
        return self._workflow_bundle_for_target(
            definition_id=dfn.id,
            name=dfn.name or "",
            owner_user_id=dfn.owner_user_id,
            target_payload=_normalize_target(target_payload),
        )

    async def _recreate_temporal_schedule(
        self, dfn: RecurringWorkflowDefinition, policy_obj: RecurringPolicy
    ) -> None:
        workflow_type, workflow_input = self._workflow_bundle_for_definition(dfn)
        try:
            await self._adapter.create_schedule(
                definition_id=dfn.id,
                cron_expression=dfn.cron,
                timezone=dfn.timezone,
                overlap_mode=policy_obj.overlap_mode,
                catchup_mode=policy_obj.catchup_mode,
                jitter_seconds=policy_obj.jitter_seconds,
                enabled=bool(dfn.enabled),
                note=dfn.name or "",
                workflow_type=workflow_type,
                workflow_input=workflow_input,
                **await self._workflow_start_metadata(
                    definition_id=dfn.id,
                    owner_user_id=dfn.owner_user_id,
                    workflow_input=workflow_input,
                ),
            )
        except ScheduleAlreadyExistsError:
            await self._adapter.update_schedule(
                definition_id=dfn.id,
                cron_expression=dfn.cron,
                timezone=dfn.timezone,
                overlap_mode=policy_obj.overlap_mode,
                catchup_mode=policy_obj.catchup_mode,
                jitter_seconds=policy_obj.jitter_seconds,
                enabled=bool(dfn.enabled),
                note=dfn.name or "",
                workflow_type=workflow_type,
                workflow_input=workflow_input,
                **await self._workflow_start_metadata(
                    definition_id=dfn.id,
                    owner_user_id=dfn.owner_user_id,
                    workflow_input=workflow_input,
                ),
            )

    async def reconcile_schedules(self, limit: int = 100) -> int:
        """Sweep db and reconcile temporal schedules where temporal_schedule_id is present."""
        stmt = select(RecurringWorkflowDefinition).where(
            RecurringWorkflowDefinition.enabled == True,
            RecurringWorkflowDefinition.temporal_schedule_id.is_not(None)
        ).limit(limit)

        result = await self._session.execute(stmt)
        definitions = result.scalars().all()
        reconciled = 0

        for dfn in definitions:
            try:
                # #4644: the existing reconciler closes unsupported producers
                # without deleting their saved targets or recreating schedules.
                try:
                    _normalize_target(
                        dfn.target if isinstance(dfn.target, Mapping) else {}
                    )
                except RecurringWorkflowValidationError as exc:
                    try:
                        await self._adapter.pause_schedule(definition_id=dfn.id)
                    except ScheduleNotFoundError:
                        logger.info(
                            "Unsupported schedule already absent for %s", dfn.id
                        )
                    except ScheduleAdapterError as pause_error:
                        logger.warning(
                            "Failed to pause unsupported schedule for %s: %s",
                            dfn.id,
                            pause_error,
                        )
                        continue
                    dfn.enabled = False
                    await self._session.commit()
                    logger.warning("Paused unsupported schedule %s: %s", dfn.id, exc)
                    reconciled += 1
                    continue

                policy_src = dfn.policy if isinstance(dfn.policy, Mapping) else None
                try:
                    policy_obj = _normalize_policy(
                        policy_src,
                        global_max_backfill=_DEFAULT_SCHEDULER_MAX_BACKFILL,
                    )
                except RecurringWorkflowValidationError as exc:
                    logger.warning(
                        "Skipping reconcile for %s: invalid policy: %s", dfn.id, exc
                    )
                    continue

                try:
                    desc = await self._adapter.describe_schedule(definition_id=dfn.id)
                except ScheduleNotFoundError:
                    await self._recreate_temporal_schedule(dfn, policy_obj)
                    reconciled += 1
                    continue
                except ScheduleOperationError as exc:
                    logger.warning("describe_schedule failed for %s: %s", dfn.id, exc)
                    continue

                sched = getattr(desc, "schedule", None)
                if sched is None:
                    logger.warning(
                        "Reconcile: missing schedule on description for %s", dfn.id
                    )
                    continue

                spec = sched.spec
                pol = sched.policy
                st = sched.state

                temporal_cron = (
                    str(spec.cron_expressions[0]).strip()
                    if spec and spec.cron_expressions
                    else ""
                )
                temporal_tz = (spec.time_zone_name or "UTC") if spec else "UTC"
                temporal_jitter = (
                    int(spec.jitter.total_seconds())
                    if spec and getattr(spec, "jitter", None)
                    else 0
                )

                overlap_src = pol.overlap if pol else None
                temporal_overlap = _overlap_mode_from_temporal(overlap_src)

                catchup_td = pol.catchup_window if pol else None
                temporal_catchup = _catchup_mode_from_temporal_window(catchup_td)

                temporal_enabled = not (st.paused if st else False)
                temporal_note = (st.note or "") if st else ""

                db_cron = str(dfn.cron or "").strip()
                db_tz = str(dfn.timezone or "UTC")

                mismatch = (
                    temporal_cron != db_cron
                    or temporal_tz != db_tz
                    or temporal_overlap != policy_obj.overlap_mode
                    or temporal_catchup != policy_obj.catchup_mode
                    or temporal_jitter != policy_obj.jitter_seconds
                    or temporal_enabled != bool(dfn.enabled)
                    or temporal_note.strip() != (dfn.name or "").strip()
                )

                if mismatch:
                    logger.info("Reconcile updating schedule metadata for %s", dfn.id)
                workflow_type, workflow_input = self._workflow_bundle_for_definition(
                    dfn
                )
                action = getattr(sched, "action", None)
                action_mismatch = self._schedule_action_mismatch(
                    action=action,
                    definition_id=dfn.id,
                    workflow_type=workflow_type,
                    workflow_input=workflow_input,
                )

                if mismatch or action_mismatch:
                    logger.info("Reconcile updating schedule for %s", dfn.id)
                    note = (dfn.name or "") if mismatch else None
                    await self._adapter.update_schedule(
                        definition_id=dfn.id,
                        cron_expression=dfn.cron if mismatch else None,
                        timezone=dfn.timezone if mismatch else None,
                        overlap_mode=policy_obj.overlap_mode if mismatch else None,
                        catchup_mode=policy_obj.catchup_mode if mismatch else None,
                        jitter_seconds=policy_obj.jitter_seconds if mismatch else None,
                        enabled=bool(dfn.enabled) if mismatch else None,
                        note=note,
                        workflow_type=workflow_type if action_mismatch else None,
                        workflow_input=workflow_input if action_mismatch else None,
                        **(
                            await self._workflow_start_metadata(
                                definition_id=dfn.id,
                                owner_user_id=dfn.owner_user_id,
                                workflow_input=workflow_input,
                            )
                            if action_mismatch
                            else {}
                        ),
                    )
                    reconciled += 1

            except ScheduleAdapterError as exc:
                logger.warning("Reconciliation adapter error for %s: %s", dfn.id, exc)
            except Exception as exc:
                logger.exception(
                    "Unexpected reconciliation error for %s: %s", dfn.id, exc
                )

        return reconciled

    async def list_runs(
        self,
        *,
        definition_id: UUID,
        limit: int = 200,
    ) -> list[RecurringWorkflowRun]:
        stmt: Select[tuple[RecurringWorkflowRun]] = (
            select(RecurringWorkflowRun)
            .where(RecurringWorkflowRun.definition_id == definition_id)
            .order_by(
                RecurringWorkflowRun.created_at.desc(),
                RecurringWorkflowRun.id.desc(),
            )
            .limit(max(1, min(int(limit), 500)))
        )
        result = await self._session.execute(stmt)
        runs = list(result.scalars().all())

        # Temporal reconciliation could be added here in the future using adapter.describe_schedule
        return runs

    async def started_at_by_workflow_id(
        self,
        workflow_ids: Iterable[str],
    ) -> dict[str, datetime]:
        normalized_ids = sorted(
            {str(item).strip() for item in workflow_ids if str(item).strip()}
        )
        if not normalized_ids:
            return {}
        stmt = select(
            TemporalExecutionRecord.workflow_id,
            TemporalExecutionRecord.started_at,
        ).where(
            TemporalExecutionRecord.workflow_id.in_(normalized_ids),
            TemporalExecutionRecord.started_at.is_not(None),
        ).order_by(
            TemporalExecutionRecord.started_at.asc(),
        )
        rows = (await self._session.execute(stmt)).all()
        return {
            str(workflow_id): started_at
            for workflow_id, started_at in rows
            if started_at
        }

    async def runtime_summary_for_definition(
        self,
        definition: RecurringWorkflowDefinition,
    ) -> RecurringScheduleRuntimeSummary:
        """Return the current Temporal schedule timing/status projection."""

        fallback = RecurringScheduleRuntimeSummary(
            next_run_at=definition.next_run_at,
            last_scheduled_for=definition.last_scheduled_for,
            last_dispatch_status=definition.last_dispatch_status,
            last_dispatch_error=definition.last_dispatch_error,
        )
        if not definition.temporal_schedule_id:
            return fallback

        try:
            description = await self._adapter.describe_schedule(
                definition_id=definition.id
            )
        except ScheduleNotFoundError as exc:
            return RecurringScheduleRuntimeSummary(
                next_run_at=None,
                last_scheduled_for=definition.last_scheduled_for,
                last_dispatch_status=(
                    definition.last_dispatch_status
                    or RecurringWorkflowRunOutcome.DISPATCH_ERROR.value
                ),
                last_dispatch_error=str(exc),
            )
        except ScheduleAdapterError as exc:
            logger.warning(
                "Failed to describe recurring schedule %s for runtime summary: %s",
                definition.id,
                exc,
            )
            return fallback

        schedule = _object_value(description, "schedule")
        state = _object_value(schedule, "state")
        paused = bool(_object_value(state, "paused")) if state is not None else False
        info = _object_value(description, "info")
        next_run_at = None if paused else _first_temporal_datetime(
            info,
            "next_action_times",
            "nextActionTimes",
            "future_action_times",
            "futureActionTimes",
        )
        last_action = _last_temporal_action(info)
        last_scheduled_for = _coerce_optional_utc_datetime(
            _object_value(
                last_action,
                "scheduled_at",
                "scheduledAt",
                "schedule_time",
                "scheduleTime",
            )
        )
        dispatch_status = _dispatch_status_from_temporal_action(last_action)
        dispatch_error = _dispatch_error_from_temporal_action(last_action)

        return RecurringScheduleRuntimeSummary(
            next_run_at=next_run_at,
            last_scheduled_for=last_scheduled_for
            if last_scheduled_for is not None
            else definition.last_scheduled_for,
            last_dispatch_status=dispatch_status or definition.last_dispatch_status,
            last_dispatch_error=dispatch_error or definition.last_dispatch_error,
        )

    async def runtime_summaries_for_definitions(
        self,
        definitions: Iterable[RecurringWorkflowDefinition],
    ) -> dict[UUID, RecurringScheduleRuntimeSummary]:
        definition_list = list(definitions)
        results = await asyncio.gather(
            *(
                self.runtime_summary_for_definition(definition)
                for definition in definition_list
            )
        )
        return dict(zip((definition.id for definition in definition_list), results))

__all__ = [
    "RecurringScheduleRuntimeSummary",
    "RecurringWorkflowAuthorizationError",
    "RecurringWorkflowConflictError",
    "RecurringWorkflowNotFoundError",
    "RecurringWorkflowValidationError",
    "RecurringWorkflowsService",
]
