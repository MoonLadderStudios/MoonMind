"""Service layer for recurring task definitions and scheduler dispatch."""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Mapping
from uuid import UUID, uuid4

from sqlalchemy import Select, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from api_service.db.models import (
    RecurringTaskDefinition,
    RecurringTaskRun,
    RecurringTaskRunOutcome,
    RecurringTaskRunTrigger,
    RecurringTaskScopeType,
)
from api_service.services.manifests_service import ManifestsService
from api_service.services.task_templates.catalog import TaskTemplateCatalogService
from moonmind.config.settings import settings
from moonmind.workflows.agent_queue import models as queue_models
from moonmind.workflows.agent_queue.job_types import (
    CANONICAL_TASK_JOB_TYPE,
    HOUSEKEEPING_JOB_TYPE,
    MANIFEST_JOB_TYPE,
)
from moonmind.workflows.agent_queue.repositories import AgentQueueRepository
from moonmind.workflows.agent_queue.service import AgentQueueService
from moonmind.workflows.recurring_tasks.cron import (
    CronExpressionError,
    compute_next_occurrence,
    parse_cron_expression,
    validate_timezone_name,
)


class RecurringTaskValidationError(ValueError):
    """Raised when recurring task inputs are invalid."""


class RecurringTaskNotFoundError(RuntimeError):
    """Raised when a recurring definition does not exist."""


class RecurringTaskAuthorizationError(RuntimeError):
    """Raised when a caller does not have access to a recurring definition."""


@dataclass(frozen=True, slots=True)
class RecurringDispatchResult:
    scheduled_runs: int
    dispatched_runs: int


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


def _clean_text(value: object, *, field_name: str, required: bool = False) -> str | None:
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
            "scopeType must be one of: personal, team, global"
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
    if overlap_mode not in {"skip", "allow"}:
        raise RecurringTaskValidationError("policy.overlap.mode must be skip or allow")

    max_concurrent_raw = overlap.get("maxConcurrentRuns")
    try:
        max_concurrent = int(max_concurrent_raw) if max_concurrent_raw is not None else 1
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
        raise RecurringTaskValidationError("policy.jitterSeconds must be an integer") from exc
    jitter_seconds = max(0, jitter_seconds)

    return RecurringPolicy(
        overlap_mode=overlap_mode,
        max_concurrent_runs=max_concurrent,
        catchup_mode=catchup_mode,
        max_backfill=max_backfill,
        misfire_grace_seconds=misfire_grace,
        jitter_seconds=jitter_seconds,
    )


def _normalize_target(target_payload: Mapping[str, Any]) -> dict[str, Any]:
    target = dict(target_payload)
    kind = str(target.get("kind") or "").strip().lower()
    if kind not in {
        "queue_task",
        "queue_task_template",
        "manifest_run",
        "housekeeping",
    }:
        raise RecurringTaskValidationError(
            "target.kind must be one of: queue_task, queue_task_template, manifest_run, housekeeping"
        )

    if kind == "queue_task":
        job_payload = target.get("job")
        if not isinstance(job_payload, Mapping):
            raise RecurringTaskValidationError("target.job is required for queue_task")
        job = dict(job_payload)
        job_type = str(job.get("type") or "").strip().lower() or CANONICAL_TASK_JOB_TYPE
        if job_type != CANONICAL_TASK_JOB_TYPE:
            raise RecurringTaskValidationError("target.job.type must be 'task' for queue_task")
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
            raise RecurringTaskValidationError("target.name is required for manifest_run")
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

    if kind == "housekeeping":
        action = str(target.get("action") or "").strip()
        if not action:
            raise RecurringTaskValidationError(
                "target.action is required for housekeeping targets"
            )
        args_payload = target.get("args")
        if args_payload is not None and not isinstance(args_payload, Mapping):
            raise RecurringTaskValidationError("target.args must be an object")

    target["kind"] = kind
    return target


def _coerce_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class RecurringTasksService:
    """CRUD and scheduler helpers for recurring definitions."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        queue_service: AgentQueueService | None = None,
    ) -> None:
        self._session = session
        self._queue_repository = AgentQueueRepository(session)
        self._queue_service = queue_service or AgentQueueService(self._queue_repository)
        self._manifests_service = ManifestsService(session, self._queue_service)
        self._template_catalog = TaskTemplateCatalogService(session)

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
                "Operator privileges are required for team schedules"
            )
        return definition

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

        definition = RecurringTaskDefinition(
            id=uuid4(),
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
            created_at=now,
            updated_at=now,
            version=1,
        )
        self._session.add(definition)
        await self._session.flush()
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

        if name is not None:
            definition.name = _clean_text(name, field_name="name", required=True) or ""
        if description is not None:
            definition.description = _clean_text(
                description,
                field_name="description",
            )
        if enabled is not None:
            definition.enabled = bool(enabled)
        if cron is not None:
            cron_normalized = str(cron or "").strip()
            parse_cron_expression(cron_normalized)
            definition.cron = cron_normalized
            changed_schedule = True
        if timezone is not None:
            definition.timezone = validate_timezone_name(timezone)
            changed_schedule = True
        if target is not None:
            definition.target = _normalize_target(
                _json_object(target, field_name="target")
            )
        if policy is not None:
            definition.policy = _json_object(policy, field_name="policy")
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
        scheduled_for = now

        for _ in range(5):
            run = RecurringTaskRun(
                id=uuid4(),
                definition_id=definition.id,
                scheduled_for=scheduled_for,
                trigger=RecurringTaskRunTrigger.MANUAL,
                outcome=RecurringTaskRunOutcome.PENDING_DISPATCH,
                dispatch_attempts=0,
                dispatch_after=now,
                created_at=now,
                updated_at=now,
            )
            self._session.add(run)
            try:
                await self._session.flush()
            except IntegrityError:
                await self._session.rollback()
                scheduled_for = scheduled_for + timedelta(microseconds=1)
                continue
            await self._session.refresh(run)
            await self._session.commit()
            return run

        raise RecurringTaskValidationError(
            "failed to create manual run due to repeated schedule collisions"
        )

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
        return list(result.scalars().all())

    def _apply_for_update_lock(
        self,
        stmt: Select[tuple[Any]],
    ) -> Select[tuple[Any]]:
        bind = self._session.get_bind()
        if bind is None:
            return stmt
        if bind.dialect.name == "postgresql":
            return stmt.with_for_update(skip_locked=True)
        return stmt

    async def _insert_run_if_missing(
        self,
        *,
        definition_id: UUID,
        scheduled_for: datetime,
        trigger: RecurringTaskRunTrigger,
        dispatch_after: datetime,
    ) -> int:
        now = datetime.now(UTC)
        values = {
            "id": uuid4(),
            "definition_id": definition_id,
            "scheduled_for": _coerce_utc(scheduled_for),
            "trigger": trigger,
            "outcome": RecurringTaskRunOutcome.PENDING_DISPATCH,
            "dispatch_attempts": 0,
            "dispatch_after": _coerce_utc(dispatch_after),
            "created_at": now,
            "updated_at": now,
        }

        bind = self._session.get_bind()
        dialect_name = bind.dialect.name if bind is not None else ""

        if dialect_name == "postgresql":
            stmt = pg_insert(RecurringTaskRun).values(**values)
            stmt = stmt.on_conflict_do_nothing(
                index_elements=["definition_id", "scheduled_for"]
            )
            result = await self._session.execute(stmt)
            return int(result.rowcount or 0)

        if dialect_name == "sqlite":
            stmt = sqlite_insert(RecurringTaskRun).values(**values)
            stmt = stmt.on_conflict_do_nothing(
                index_elements=["definition_id", "scheduled_for"]
            )
            result = await self._session.execute(stmt)
            return int(result.rowcount or 0)

        existing_stmt = select(RecurringTaskRun.id).where(
            RecurringTaskRun.definition_id == definition_id,
            RecurringTaskRun.scheduled_for == _coerce_utc(scheduled_for),
        )
        existing = await self._session.execute(existing_stmt)
        if existing.first() is not None:
            return 0
        self._session.add(RecurringTaskRun(**values))
        return 1

    def _compute_due_occurrences(
        self,
        *,
        definition: RecurringTaskDefinition,
        now: datetime,
        policy: RecurringPolicy,
    ) -> tuple[list[datetime], datetime]:
        if definition.next_run_at is None:
            next_run = compute_next_occurrence(
                cron=definition.cron,
                timezone_name=definition.timezone,
                after=now,
            )
            return [], next_run

        due_candidates: list[datetime] = []
        cursor = _coerce_utc(definition.next_run_at)
        safety_limit = max(500, policy.max_backfill * 20)

        while cursor <= now and len(due_candidates) < safety_limit:
            due_candidates.append(cursor)
            cursor = compute_next_occurrence(
                cron=definition.cron,
                timezone_name=definition.timezone,
                after=cursor,
            )

        if not due_candidates:
            return [], cursor

        if policy.catchup_mode in {"none", "last"}:
            selected = [due_candidates[-1]]
        else:
            selected = due_candidates[-policy.max_backfill :]

        return selected, cursor

    async def schedule_due_definitions(
        self,
        *,
        now: datetime | None = None,
        batch_size: int | None = None,
        max_backfill: int | None = None,
    ) -> int:
        reference_now = _coerce_utc(now or datetime.now(UTC))
        batch = max(1, int(batch_size or settings.spec_workflow.scheduler_batch_size))
        global_backfill = max(
            1,
            int(max_backfill or settings.spec_workflow.scheduler_max_backfill),
        )

        stmt: Select[tuple[RecurringTaskDefinition]] = (
            select(RecurringTaskDefinition)
            .where(
                RecurringTaskDefinition.enabled.is_(True),
                RecurringTaskDefinition.next_run_at.is_not(None),
                RecurringTaskDefinition.next_run_at <= reference_now,
            )
            .order_by(
                RecurringTaskDefinition.next_run_at.asc(),
                RecurringTaskDefinition.id.asc(),
            )
            .limit(batch)
        )
        stmt = self._apply_for_update_lock(stmt)
        result = await self._session.execute(stmt)
        due_definitions = list(result.scalars().all())

        scheduled_count = 0
        for definition in due_definitions:
            policy = _normalize_policy(
                definition.policy,
                global_max_backfill=global_backfill,
            )
            selected_occurrences, next_future = self._compute_due_occurrences(
                definition=definition,
                now=reference_now,
                policy=policy,
            )

            for scheduled_for in selected_occurrences:
                jitter = random.randint(0, policy.jitter_seconds) if policy.jitter_seconds else 0
                dispatch_after = scheduled_for + timedelta(seconds=jitter)
                inserted = await self._insert_run_if_missing(
                    definition_id=definition.id,
                    scheduled_for=scheduled_for,
                    trigger=RecurringTaskRunTrigger.SCHEDULE,
                    dispatch_after=dispatch_after,
                )
                scheduled_count += inserted

            if selected_occurrences:
                definition.last_scheduled_for = selected_occurrences[-1]
            definition.next_run_at = next_future
            definition.updated_at = reference_now

        await self._session.commit()
        return scheduled_count

    async def _count_active_runs(
        self,
        *,
        definition_id: UUID,
        current_run_id: UUID,
    ) -> int:
        stmt: Select[tuple[RecurringTaskRun]] = select(RecurringTaskRun).where(
            RecurringTaskRun.definition_id == definition_id,
            RecurringTaskRun.id != current_run_id,
            RecurringTaskRun.outcome.in_(
                (
                    RecurringTaskRunOutcome.PENDING_DISPATCH,
                    RecurringTaskRunOutcome.DISPATCH_ERROR,
                    RecurringTaskRunOutcome.ENQUEUED,
                )
            ),
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        active = 0
        for row in rows:
            if row.outcome in {
                RecurringTaskRunOutcome.PENDING_DISPATCH,
                RecurringTaskRunOutcome.DISPATCH_ERROR,
            }:
                active += 1
                continue

            if row.queue_job_id is None:
                active += 1
                continue

            queue_job = await self._session.get(queue_models.AgentJob, row.queue_job_id)
            if queue_job is None:
                continue
            if queue_job.status in {
                queue_models.AgentJobStatus.QUEUED,
                queue_models.AgentJobStatus.RUNNING,
            }:
                active += 1
        return active

    def _recurrence_payload(
        self,
        *,
        definition: RecurringTaskDefinition,
        run: RecurringTaskRun,
    ) -> dict[str, Any]:
        return {
            "definitionId": str(definition.id),
            "runId": str(run.id),
            "scheduledFor": _coerce_utc(run.scheduled_for).isoformat().replace(
                "+00:00", "Z"
            ),
        }

    @staticmethod
    def _merge_recurrence_system(
        payload: Mapping[str, Any] | None,
        *,
        recurrence: Mapping[str, Any],
    ) -> dict[str, Any]:
        body = dict(payload or {})
        system_payload = body.get("system")
        system = dict(system_payload) if isinstance(system_payload, Mapping) else {}
        system["recurrence"] = dict(recurrence)
        body["system"] = system
        return body

    async def _find_existing_queue_job_for_run(
        self,
        *,
        run_id: UUID,
        job_type: str,
        scan_limit: int = 1000,
    ) -> queue_models.AgentJob | None:
        jobs = await self._queue_repository.list_jobs(job_type=job_type, limit=scan_limit)
        target_id = str(run_id)
        for job in jobs:
            payload = dict(job.payload or {})
            system_node = payload.get("system")
            if not isinstance(system_node, Mapping):
                continue
            recurrence_node = system_node.get("recurrence")
            if not isinstance(recurrence_node, Mapping):
                continue
            run_value = str(recurrence_node.get("runId") or "").strip()
            if run_value == target_id:
                return job
        return None

    async def _dispatch_queue_task(
        self,
        *,
        definition: RecurringTaskDefinition,
        run: RecurringTaskRun,
        target: Mapping[str, Any],
    ) -> queue_models.AgentJob:
        job_payload = dict(target.get("job") or {})
        payload = dict(job_payload.get("payload") or {})
        payload = self._merge_recurrence_system(
            payload,
            recurrence=self._recurrence_payload(definition=definition, run=run),
        )

        priority_raw = job_payload.get("priority")
        max_attempts_raw = job_payload.get("maxAttempts")
        try:
            priority = int(priority_raw) if priority_raw is not None else 0
        except (TypeError, ValueError):
            priority = 0
        try:
            max_attempts = int(max_attempts_raw) if max_attempts_raw is not None else 3
        except (TypeError, ValueError):
            max_attempts = 3

        affinity_key = job_payload.get("affinityKey")
        affinity = str(affinity_key).strip() if affinity_key is not None else None
        if affinity == "":
            affinity = None

        return await self._queue_service.create_job(
            job_type=CANONICAL_TASK_JOB_TYPE,
            payload=payload,
            priority=priority,
            created_by_user_id=definition.owner_user_id,
            requested_by_user_id=definition.owner_user_id,
            affinity_key=affinity,
            max_attempts=max(1, max_attempts),
        )

    async def _dispatch_queue_task_template(
        self,
        *,
        definition: RecurringTaskDefinition,
        run: RecurringTaskRun,
        target: Mapping[str, Any],
    ) -> queue_models.AgentJob:
        template = dict(target.get("template") or {})
        slug = str(template.get("slug") or "").strip()
        version = str(template.get("version") or "").strip()
        inputs = dict(template.get("inputs") or {})
        scope = str(template.get("scope") or "global").strip().lower() or "global"
        scope_ref_raw = template.get("scopeRef")
        scope_ref = str(scope_ref_raw).strip() if scope_ref_raw is not None else None

        expanded = await self._template_catalog.expand_template(
            slug=slug,
            scope=scope,
            scope_ref=scope_ref,
            version=version,
            inputs=inputs,
            context={
                "recurrence": self._recurrence_payload(definition=definition, run=run),
                "definition": {
                    "id": str(definition.id),
                    "name": definition.name,
                    "scope": definition.scope_type.value,
                },
            },
            user_id=definition.owner_user_id,
        )

        job_defaults = dict(target.get("jobDefaults") or {})
        task_defaults = dict(target.get("taskDefaults") or {})

        payload = dict(task_defaults)
        task_payload_raw = payload.get("task")
        task_payload = dict(task_payload_raw) if isinstance(task_payload_raw, Mapping) else {}

        if not isinstance(task_payload.get("publish"), Mapping):
            task_payload["publish"] = {"mode": "none"}
        if not isinstance(task_payload.get("skill"), Mapping):
            task_payload["skill"] = {"id": "auto", "args": {}}
        if not str(task_payload.get("instructions") or "").strip():
            task_payload["instructions"] = f"Scheduled task: {definition.name}"

        task_payload["steps"] = list(expanded.get("steps") or [])

        applied = []
        existing_applied = task_payload.get("appliedStepTemplates")
        if isinstance(existing_applied, list):
            applied.extend(existing_applied)
        applied_template = expanded.get("appliedTemplate")
        if isinstance(applied_template, Mapping):
            applied.append(dict(applied_template))
        if applied:
            task_payload["appliedStepTemplates"] = applied

        payload["task"] = task_payload
        payload = self._merge_recurrence_system(
            payload,
            recurrence=self._recurrence_payload(definition=definition, run=run),
        )

        caps: list[str] = []
        existing_caps = payload.get("requiredCapabilities")
        if isinstance(existing_caps, list):
            caps.extend(str(item).strip().lower() for item in existing_caps if str(item).strip())
        expanded_caps = expanded.get("capabilities")
        if isinstance(expanded_caps, list):
            caps.extend(str(item).strip().lower() for item in expanded_caps if str(item).strip())
        if caps:
            payload["requiredCapabilities"] = list(dict.fromkeys(caps))

        if "repository" not in payload or not str(payload.get("repository") or "").strip():
            payload["repository"] = str(
                settings.spec_workflow.github_repository or "MoonLadderStudios/MoonMind"
            ).strip()

        priority_raw = job_defaults.get("priority")
        max_attempts_raw = job_defaults.get("maxAttempts")
        affinity_raw = job_defaults.get("affinityKey")
        try:
            priority = int(priority_raw) if priority_raw is not None else 0
        except (TypeError, ValueError):
            priority = 0
        try:
            max_attempts = int(max_attempts_raw) if max_attempts_raw is not None else 3
        except (TypeError, ValueError):
            max_attempts = 3

        affinity = str(affinity_raw).strip() if affinity_raw is not None else None
        if affinity == "":
            affinity = None

        return await self._queue_service.create_job(
            job_type=CANONICAL_TASK_JOB_TYPE,
            payload=payload,
            priority=priority,
            created_by_user_id=definition.owner_user_id,
            requested_by_user_id=definition.owner_user_id,
            affinity_key=affinity,
            max_attempts=max(1, max_attempts),
        )

    async def _dispatch_manifest_run(
        self,
        *,
        definition: RecurringTaskDefinition,
        run: RecurringTaskRun,
        target: Mapping[str, Any],
    ) -> queue_models.AgentJob:
        name = str(target.get("name") or "").strip()
        action = str(target.get("action") or "run").strip().lower() or "run"
        options_payload = target.get("options")
        options = dict(options_payload) if isinstance(options_payload, Mapping) else None

        return await self._manifests_service.submit_manifest_run(
            name=name,
            action=action,
            options=options,
            user_id=definition.owner_user_id,
            system_payload={
                "recurrence": self._recurrence_payload(definition=definition, run=run)
            },
        )

    async def _dispatch_housekeeping(
        self,
        *,
        definition: RecurringTaskDefinition,
        run: RecurringTaskRun,
        target: Mapping[str, Any],
    ) -> queue_models.AgentJob:
        payload = {
            "housekeeping": {
                "action": str(target.get("action") or "").strip(),
                "args": dict(target.get("args") or {}),
            }
        }
        payload = self._merge_recurrence_system(
            payload,
            recurrence=self._recurrence_payload(definition=definition, run=run),
        )
        return await self._queue_service.create_job(
            job_type=HOUSEKEEPING_JOB_TYPE,
            payload=payload,
            priority=0,
            created_by_user_id=definition.owner_user_id,
            requested_by_user_id=definition.owner_user_id,
            max_attempts=1,
        )

    async def _dispatch_run(
        self,
        *,
        definition: RecurringTaskDefinition,
        run: RecurringTaskRun,
        now: datetime,
        policy: RecurringPolicy,
    ) -> int:
        scheduled_for = _coerce_utc(run.scheduled_for)
        if policy.misfire_grace_seconds > 0 and now - scheduled_for > timedelta(
            seconds=policy.misfire_grace_seconds
        ):
            run.outcome = RecurringTaskRunOutcome.SKIPPED
            run.message = "Skipped due to misfire grace threshold"
            run.updated_at = now
            definition.last_dispatch_status = "skipped"
            definition.last_dispatch_error = run.message[:2000]
            definition.updated_at = now
            return 1

        if policy.overlap_mode == "skip":
            active_count = await self._count_active_runs(
                definition_id=definition.id,
                current_run_id=run.id,
            )
            if active_count >= policy.max_concurrent_runs:
                run.outcome = RecurringTaskRunOutcome.SKIPPED
                run.message = "Skipped due to overlap policy"
                run.updated_at = now
                definition.last_dispatch_status = "skipped"
                definition.last_dispatch_error = run.message[:2000]
                definition.updated_at = now
                return 1

        target = dict(definition.target or {})
        kind = str(target.get("kind") or "").strip().lower()

        if kind == "queue_task":
            expected_job_type = CANONICAL_TASK_JOB_TYPE
        elif kind == "queue_task_template":
            expected_job_type = CANONICAL_TASK_JOB_TYPE
        elif kind == "manifest_run":
            expected_job_type = MANIFEST_JOB_TYPE
        elif kind == "housekeeping":
            expected_job_type = HOUSEKEEPING_JOB_TYPE
        else:
            run.outcome = RecurringTaskRunOutcome.DISPATCH_ERROR
            run.dispatch_attempts = int(run.dispatch_attempts or 0) + 1
            run.dispatch_after = now + timedelta(seconds=60)
            run.message = f"Unsupported target kind: {kind or '<empty>'}"
            run.updated_at = now
            definition.last_dispatch_status = "error"
            definition.last_dispatch_error = run.message[:2000]
            definition.updated_at = now
            return 1

        existing_job = await self._find_existing_queue_job_for_run(
            run_id=run.id,
            job_type=expected_job_type,
        )
        if existing_job is not None:
            run.outcome = RecurringTaskRunOutcome.ENQUEUED
            run.queue_job_id = existing_job.id
            run.queue_job_type = existing_job.type
            run.message = None
            run.updated_at = now
            definition.last_dispatch_status = "enqueued"
            definition.last_dispatch_error = None
            definition.updated_at = now
            return 1

        try:
            if kind == "queue_task":
                job = await self._dispatch_queue_task(
                    definition=definition,
                    run=run,
                    target=target,
                )
            elif kind == "queue_task_template":
                job = await self._dispatch_queue_task_template(
                    definition=definition,
                    run=run,
                    target=target,
                )
            elif kind == "manifest_run":
                job = await self._dispatch_manifest_run(
                    definition=definition,
                    run=run,
                    target=target,
                )
            else:
                job = await self._dispatch_housekeeping(
                    definition=definition,
                    run=run,
                    target=target,
                )
        except Exception as exc:
            attempt = int(run.dispatch_attempts or 0) + 1
            backoff_seconds = min(15 * (2 ** max(0, attempt - 1)), 900)
            run.outcome = RecurringTaskRunOutcome.DISPATCH_ERROR
            run.dispatch_attempts = attempt
            run.dispatch_after = now + timedelta(seconds=backoff_seconds)
            run.message = str(exc)[:2000]
            run.updated_at = now
            definition.last_dispatch_status = "error"
            definition.last_dispatch_error = run.message[:2000]
            definition.updated_at = now
            return 1

        run.outcome = RecurringTaskRunOutcome.ENQUEUED
        run.dispatch_attempts = int(run.dispatch_attempts or 0) + 1
        run.queue_job_id = job.id
        run.queue_job_type = job.type
        run.message = None
        run.updated_at = now
        definition.last_dispatch_status = "enqueued"
        definition.last_dispatch_error = None
        definition.updated_at = now
        return 1

    async def dispatch_pending_runs(
        self,
        *,
        now: datetime | None = None,
        batch_size: int | None = None,
    ) -> int:
        reference_now = _coerce_utc(now or datetime.now(UTC))
        batch = max(1, int(batch_size or settings.spec_workflow.scheduler_batch_size))

        stmt: Select[tuple[RecurringTaskRun]] = (
            select(RecurringTaskRun)
            .where(
                RecurringTaskRun.outcome.in_(
                    (
                        RecurringTaskRunOutcome.PENDING_DISPATCH,
                        RecurringTaskRunOutcome.DISPATCH_ERROR,
                    )
                ),
                or_(
                    RecurringTaskRun.dispatch_after.is_(None),
                    RecurringTaskRun.dispatch_after <= reference_now,
                ),
            )
            .options(selectinload(RecurringTaskRun.definition))
            .order_by(
                RecurringTaskRun.dispatch_after.asc().nullsfirst(),
                RecurringTaskRun.created_at.asc(),
                RecurringTaskRun.id.asc(),
            )
            .limit(batch)
        )
        stmt = self._apply_for_update_lock(stmt)
        result = await self._session.execute(stmt)
        pending_runs = list(result.scalars().all())

        dispatched = 0
        max_backfill = max(1, int(settings.spec_workflow.scheduler_max_backfill))
        for run in pending_runs:
            definition = run.definition
            if definition is None:
                run.outcome = RecurringTaskRunOutcome.DISPATCH_ERROR
                run.dispatch_attempts = int(run.dispatch_attempts or 0) + 1
                run.dispatch_after = reference_now + timedelta(seconds=60)
                run.message = "Missing recurring definition"
                run.updated_at = reference_now
                dispatched += 1
                continue

            policy = _normalize_policy(
                definition.policy,
                global_max_backfill=max_backfill,
            )
            dispatched += await self._dispatch_run(
                definition=definition,
                run=run,
                now=reference_now,
                policy=policy,
            )

        await self._session.commit()
        return dispatched

    async def run_scheduler_tick(
        self,
        *,
        now: datetime | None = None,
        batch_size: int | None = None,
        max_backfill: int | None = None,
    ) -> RecurringDispatchResult:
        scheduled = await self.schedule_due_definitions(
            now=now,
            batch_size=batch_size,
            max_backfill=max_backfill,
        )
        dispatched = await self.dispatch_pending_runs(
            now=now,
            batch_size=batch_size,
        )
        return RecurringDispatchResult(
            scheduled_runs=scheduled,
            dispatched_runs=dispatched,
        )


__all__ = [
    "RecurringDispatchResult",
    "RecurringTaskAuthorizationError",
    "RecurringTaskNotFoundError",
    "RecurringTaskValidationError",
    "RecurringTasksService",
]
