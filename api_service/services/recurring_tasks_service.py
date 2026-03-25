"""Service layer for recurring task definitions and Temporal-driven dispatch."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Mapping
from uuid import UUID, uuid4

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from api_service.db.models import (
    RecurringTaskDefinition,
    RecurringTaskRun,
    RecurringTaskRunOutcome,
    RecurringTaskRunTrigger,
    RecurringTaskScopeType,
)
from moonmind.workflows.tasks.job_types import CANONICAL_TASK_JOB_TYPE
from moonmind.workflows.recurring_tasks.cron import (
    compute_next_occurrence,
    parse_cron_expression,
    validate_timezone_name,
)
from moonmind.workflows.temporal.client import TemporalClientAdapter
from moonmind.workflows.temporal.schedule_errors import (
    ScheduleAdapterError,
    ScheduleAlreadyExistsError,
    ScheduleNotFoundError,
    ScheduleOperationError,
)

logger = logging.getLogger(__name__)

_DEFAULT_SCHEDULER_MAX_BACKFILL = 3


class RecurringTaskValidationError(ValueError):
    """Raised when recurring task inputs are invalid."""


class RecurringTaskNotFoundError(RuntimeError):
    """Raised when a recurring definition does not exist."""


class RecurringTaskAuthorizationError(RuntimeError):
    """Raised when a caller does not have access to a recurring definition."""


@dataclass(frozen=True, slots=True)
class RecurringPolicy:
    overlap_mode: str = "skip"
    max_concurrent_runs: int = 1
    catchup_mode: str = "last"
    max_backfill: int = 3
    misfire_grace_seconds: int = 900
    jitter_seconds: int = 0


def _json_object(value: object, *, field_name: str) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return dict(value)
    raise RecurringTaskValidationError(f"{field_name} must be a JSON object")


def _clean_text(
    value: object, *, field_name: str, required: bool = False
) -> str | None:
    text = str(value or "").strip()
    if not text:
        if required:
            raise RecurringTaskValidationError(f"{field_name} is required")
        return None
    return text


def _normalize_scope_type(value: object) -> RecurringTaskScopeType:
    raw = str(value or "").strip().lower() or RecurringTaskScopeType.PERSONAL.value
    try:
        return RecurringTaskScopeType(raw)
    except ValueError as exc:
        raise RecurringTaskValidationError(
            "scopeType must be one of: personal, global"
        ) from exc


def _normalize_schedule_type(value: object) -> str:
    raw = str(value or "").strip().lower() or "cron"
    if raw != "cron":
        raise RecurringTaskValidationError("scheduleType must be 'cron'")
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
        raise RecurringTaskValidationError("policy.overlap.mode must be skip, allow, buffer_one, or cancel_previous")

    max_concurrent_raw = overlap.get("maxConcurrentRuns")
    try:
        max_concurrent = (
            int(max_concurrent_raw) if max_concurrent_raw is not None else 1
        )
    except (TypeError, ValueError) as exc:
        raise RecurringTaskValidationError(
            "policy.overlap.maxConcurrentRuns must be an integer"
        ) from exc
    max_concurrent = max(1, max_concurrent)

    catchup_payload = payload.get("catchup")
    catchup = dict(catchup_payload) if isinstance(catchup_payload, Mapping) else {}
    catchup_mode = str(catchup.get("mode") or "last").strip().lower()
    if catchup_mode not in {"none", "last", "all"}:
        raise RecurringTaskValidationError(
            "policy.catchup.mode must be none, last, or all"
        )

    max_backfill_raw = catchup.get("maxBackfill")
    try:
        max_backfill = int(max_backfill_raw) if max_backfill_raw is not None else 3
    except (TypeError, ValueError) as exc:
        raise RecurringTaskValidationError(
            "policy.catchup.maxBackfill must be an integer"
        ) from exc
    max_backfill = max(1, max_backfill)
    max_backfill = min(max_backfill, max(1, int(global_max_backfill)))

    misfire_raw = payload.get("misfireGraceSeconds", 900)
    try:
        misfire_grace = int(misfire_raw)
    except (TypeError, ValueError) as exc:
        raise RecurringTaskValidationError(
            "policy.misfireGraceSeconds must be an integer"
        ) from exc
    misfire_grace = max(0, misfire_grace)

    jitter_raw = payload.get("jitterSeconds", 0)
    try:
        jitter_seconds = int(jitter_raw)
    except (TypeError, ValueError) as exc:
        raise RecurringTaskValidationError(
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


def _normalize_target(target_payload: Mapping[str, Any]) -> dict[str, Any]:
    target = dict(target_payload)
    kind = str(target.get("kind") or "").strip().lower()
    if kind not in {
        "queue_task",
        "queue_task_template",
        "manifest_run",
    }:
        raise RecurringTaskValidationError(
            "target.kind must be one of: queue_task, queue_task_template, manifest_run"
        )

    if kind == "queue_task":
        job_payload = target.get("job")
        if not isinstance(job_payload, Mapping):
            raise RecurringTaskValidationError("target.job is required for queue_task")
        job = dict(job_payload)
        workflow_type = str(job.get("type") or "").strip().lower() or CANONICAL_TASK_JOB_TYPE
        if workflow_type != CANONICAL_TASK_JOB_TYPE:
            raise RecurringTaskValidationError(
                "target.job.type must be 'task' for queue_task"
            )
        payload = job.get("payload")
        if not isinstance(payload, Mapping):
            raise RecurringTaskValidationError(
                "target.job.payload must be an object for queue_task"
            )
        job["type"] = CANONICAL_TASK_JOB_TYPE
        target["job"] = job

    if kind == "queue_task_template":
        template_payload = target.get("template")
        if not isinstance(template_payload, Mapping):
            raise RecurringTaskValidationError(
                "target.template is required for queue_task_template"
            )
        template = dict(template_payload)
        slug = str(template.get("slug") or "").strip()
        version = str(template.get("version") or "").strip()
        if not slug or not version:
            raise RecurringTaskValidationError(
                "target.template.slug and target.template.version are required"
            )
        inputs_payload = template.get("inputs")
        if inputs_payload is None:
            template["inputs"] = {}
        elif not isinstance(inputs_payload, Mapping):
            raise RecurringTaskValidationError(
                "target.template.inputs must be an object when provided"
            )
        target["template"] = template

    if kind == "manifest_run":
        manifest_name = str(target.get("name") or "").strip()
        if not manifest_name:
            raise RecurringTaskValidationError(
                "target.name is required for manifest_run"
            )
        action = str(target.get("action") or "run").strip().lower() or "run"
        if action not in {"run", "plan"}:
            raise RecurringTaskValidationError(
                "target.action for manifest_run must be run or plan"
            )
        options_payload = target.get("options")
        if options_payload is not None and not isinstance(options_payload, Mapping):
            raise RecurringTaskValidationError(
                "target.options for manifest_run must be an object"
            )
        target["action"] = action

    target["kind"] = kind
    return target


def _coerce_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class RecurringTasksService:
    """CRUD and dispatch helpers for recurring definitions."""

    def __init__(self, session: AsyncSession, *, temporal_client_adapter: TemporalClientAdapter | None = None) -> None:
        self._session = session
        self._adapter = temporal_client_adapter or TemporalClientAdapter()

    async def list_definitions(
        self,
        *,
        scope: str,
        user_id: UUID | None,
        include_disabled: bool = True,
        limit: int = 200,
    ) -> list[RecurringTaskDefinition]:
        scope_type = _normalize_scope_type(scope)
        stmt: Select[tuple[RecurringTaskDefinition]] = select(RecurringTaskDefinition)
        stmt = stmt.where(RecurringTaskDefinition.scope_type == scope_type)
        if scope_type is RecurringTaskScopeType.PERSONAL:
            if user_id is None:
                return []
            stmt = stmt.where(RecurringTaskDefinition.owner_user_id == user_id)
        if not include_disabled:
            stmt = stmt.where(RecurringTaskDefinition.enabled.is_(True))
        stmt = stmt.order_by(
            RecurringTaskDefinition.updated_at.desc(),
            RecurringTaskDefinition.id.desc(),
        ).limit(max(1, min(int(limit), 500)))
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_definition(self, definition_id: UUID) -> RecurringTaskDefinition:
        stmt: Select[tuple[RecurringTaskDefinition]] = (
            select(RecurringTaskDefinition)
            .where(RecurringTaskDefinition.id == definition_id)
            .options(selectinload(RecurringTaskDefinition.runs))
        )
        result = await self._session.execute(stmt)
        definition = result.scalars().first()
        if definition is None:
            raise RecurringTaskNotFoundError(
                f"Recurring task definition '{definition_id}' was not found"
            )
        return definition

    async def require_authorized_definition(
        self,
        *,
        definition_id: UUID,
        user_id: UUID | None,
        can_manage_global: bool,
    ) -> RecurringTaskDefinition:
        definition = await self.get_definition(definition_id)
        if definition.scope_type is RecurringTaskScopeType.GLOBAL:
            if not can_manage_global:
                raise RecurringTaskAuthorizationError(
                    "Operator privileges are required for global schedules"
                )
            return definition

        if definition.scope_type is RecurringTaskScopeType.PERSONAL:
            if user_id is None or definition.owner_user_id != user_id:
                raise RecurringTaskAuthorizationError(
                    "You do not have access to this recurring schedule"
                )
            return definition

        if not can_manage_global:
            raise RecurringTaskAuthorizationError(
                "Operator privileges are required to manage this schedule"
            )
        return definition

    def _expected_workflow_type_for_target_kind(self, kind: str) -> str:
        if kind in {"queue_task", "queue_task_template"}:
            return "MoonMind.Run"
        elif kind == "manifest_run":
            return "MoonMind.ManifestIngest"
        return "MoonMind.Run"

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
    ) -> RecurringTaskDefinition:
        schedule_kind = _normalize_schedule_type(schedule_type)
        cron_normalized = str(cron or "").strip()
        parse_cron_expression(cron_normalized)
        timezone_name = validate_timezone_name(timezone)
        name_text = _clean_text(name, field_name="name", required=True) or ""
        target_payload = _normalize_target(_json_object(target, field_name="target"))
        policy_payload = _json_object(policy, field_name="policy")
        policy_obj = _normalize_policy(
            policy_payload,
            global_max_backfill=_DEFAULT_SCHEDULER_MAX_BACKFILL,
        )
        scope = _normalize_scope_type(scope_type)

        if scope is RecurringTaskScopeType.PERSONAL and owner_user_id is None:
            raise RecurringTaskValidationError(
                "ownerUserId is required for personal schedules"
            )

        now = datetime.now(UTC)
        next_run_at = compute_next_occurrence(
            cron=cron_normalized,
            timezone_name=timezone_name,
            after=now,
        )

        definition_id = uuid4()
        definition = RecurringTaskDefinition(
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

        workflow_type = self._expected_workflow_type_for_target_kind(target_payload.get("kind", ""))
        workflow_input = {
            "title": name_text,
            "ownerUserId": str(owner_user_id) if owner_user_id else None,
            "system": {
                "recurrence": {
                    "definitionId": str(definition_id)
                }
            },
            "recurringTarget": target_payload,
        }
        
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
                memo={"definitionId": str(definition_id)},
            )
        except Exception as exc:
            logger.error(f"Failed to create temporal schedule for {definition_id}: {exc}")
            raise RecurringTaskValidationError(f"Failed to create schedule: {exc}")

        await self._session.refresh(definition)
        await self._session.commit()
        return definition

    async def update_definition(
        self,
        definition: RecurringTaskDefinition,
        *,
        name: str | None = None,
        description: str | None = None,
        enabled: bool | None = None,
        cron: str | None = None,
        timezone: str | None = None,
        target: Mapping[str, Any] | None = None,
        policy: Mapping[str, Any] | None = None,
        scope_ref: str | None = None,
    ) -> RecurringTaskDefinition:
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
        if target is not None:
            definition.target = _normalize_target(
                _json_object(target, field_name="target")
            )
        
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
            # We don't update workflow inputs for target changes yet, since update_schedule
            # doesn't natively support updating action/input args without recreating.
            # Only cron, policy, state are updated in Phase 2
            if enabled is False:
                await self._adapter.pause_schedule(definition_id=definition.id)
            elif enabled is True:
                await self._adapter.unpause_schedule(definition_id=definition.id)
                
            await self._adapter.update_schedule(
                definition_id=definition.id,
                cron_expression=cron_normalized,
                timezone=timezone,
                overlap_mode=policy_obj.overlap_mode if policy_obj else None,
                catchup_mode=policy_obj.catchup_mode if policy_obj else None,
                jitter_seconds=policy_obj.jitter_seconds if policy_obj else None,
                enabled=enabled,
                note=name if name is not None else None,
            )
        except Exception as exc:
            logger.error(f"Failed to update temporal schedule for {definition.id}: {exc}")
            raise RecurringTaskValidationError(f"Failed to update schedule: {exc}")

        definition.updated_at = now
        definition.version = int(definition.version or 0) + 1
        await self._session.flush()
        await self._session.refresh(definition)
        await self._session.commit()
        return definition

    async def create_manual_run(
        self,
        definition: RecurringTaskDefinition,
    ) -> RecurringTaskRun:
        now = datetime.now(UTC)

        try:
            await self._adapter.trigger_schedule(definition_id=definition.id)
        except Exception as exc:
            logger.error(f"Failed to trigger temporal schedule for {definition.id}: {exc}")
            raise RecurringTaskValidationError(f"Failed to trigger schedule: {exc}")

        # In phase 2, we can just insert a stub run for the manual invocation API
        run = RecurringTaskRun(
            id=uuid4(),
            definition_id=definition.id,
            scheduled_for=now,
            trigger=RecurringTaskRunTrigger.MANUAL,
            outcome=RecurringTaskRunOutcome.ENQUEUED,
            dispatch_attempts=1,
            dispatch_after=now,
            created_at=now,
            updated_at=now,
            message="Triggered via Temporal Schedule",
        )
        self._session.add(run)
        await self._session.flush()
        await self._session.refresh(run)
        await self._session.commit()
        return run

    def _workflow_bundle_for_definition(
        self, dfn: RecurringTaskDefinition
    ) -> tuple[str, dict[str, Any]]:
        target_payload = (
            dict(dfn.target) if isinstance(dfn.target, Mapping) else {}
        )
        kind = str(target_payload.get("kind") or "")
        workflow_type = self._expected_workflow_type_for_target_kind(kind)
        name_text = dfn.name or ""
        workflow_input: dict[str, Any] = {
            "title": name_text,
            "ownerUserId": str(dfn.owner_user_id) if dfn.owner_user_id else None,
            "system": {"recurrence": {"definitionId": str(dfn.id)}},
            "recurringTarget": target_payload,
        }
        return workflow_type, workflow_input

    async def _recreate_temporal_schedule(
        self, dfn: RecurringTaskDefinition, policy_obj: RecurringPolicy
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
                memo={"definitionId": str(dfn.id)},
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
            )

    async def reconcile_schedules(self, limit: int = 100) -> int:
        """Sweep db and reconcile temporal schedules where temporal_schedule_id is present."""
        stmt = select(RecurringTaskDefinition).where(
            RecurringTaskDefinition.enabled == True,
            RecurringTaskDefinition.temporal_schedule_id.is_not(None)
        ).limit(limit)

        result = await self._session.execute(stmt)
        definitions = result.scalars().all()
        reconciled = 0

        for dfn in definitions:
            try:
                policy_src = dfn.policy if isinstance(dfn.policy, Mapping) else None
                try:
                    policy_obj = _normalize_policy(
                        policy_src,
                        global_max_backfill=_DEFAULT_SCHEDULER_MAX_BACKFILL,
                    )
                except RecurringTaskValidationError as exc:
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
                    await self._adapter.update_schedule(
                        definition_id=dfn.id,
                        cron_expression=dfn.cron,
                        timezone=dfn.timezone,
                        overlap_mode=policy_obj.overlap_mode,
                        catchup_mode=policy_obj.catchup_mode,
                        jitter_seconds=policy_obj.jitter_seconds,
                        enabled=bool(dfn.enabled),
                        note=dfn.name or "",
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
    ) -> list[RecurringTaskRun]:
        stmt: Select[tuple[RecurringTaskRun]] = (
            select(RecurringTaskRun)
            .where(RecurringTaskRun.definition_id == definition_id)
            .order_by(
                RecurringTaskRun.created_at.desc(),
                RecurringTaskRun.id.desc(),
            )
            .limit(max(1, min(int(limit), 500)))
        )
        result = await self._session.execute(stmt)
        runs = list(result.scalars().all())
        
        # Temporal reconciliation could be added here in the future using adapter.describe_schedule
        return runs

__all__ = [
    "RecurringTaskAuthorizationError",
    "RecurringTaskNotFoundError",
    "RecurringTaskValidationError",
    "RecurringTasksService",
]
