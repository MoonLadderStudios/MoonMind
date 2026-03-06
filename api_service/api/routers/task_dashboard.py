"""Routes that serve the MoonMind task dashboard UI shell."""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.api.routers.agent_queue import _get_service, list_jobs
from api_service.api.routers.task_dashboard_view_model import build_runtime_config
from api_service.auth_providers import get_current_user
from api_service.db.base import get_async_session
from api_service.db.models import User
from moonmind.config.settings import settings
from moonmind.schemas.agent_queue_models import JobListResponse
from moonmind.workflows.agent_queue.service import AgentQueueService
from moonmind.workflows.orchestrator.repositories import OrchestratorRepository
from moonmind.workflows.orchestrator.skill_executor import list_runnable_skill_names
from moonmind.workflows.skills.resolver import list_available_skill_names
from moonmind.workflows.temporal import (
    TemporalExecutionNotFoundError,
    TemporalExecutionService,
)
from moonmind.workflows.tasks.source_mapping import (
    TaskResolutionAmbiguousError,
    TaskResolutionNotFoundError,
    TaskSourceMappingService,
)

router = APIRouter(prefix="", tags=["task-dashboard"])

TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

_SAFE_DETAIL_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SAFE_TASK_ID_SEGMENT = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_SAFE_TEMPORAL_WORKFLOW_ID_SEGMENT = re.compile(
    r"^mm:[A-Za-z0-9][A-Za-z0-9._:-]{0,123}$"
)
_STATIC_PATHS = {
    "list",
    "queue",
    "queue/new",
    "new",
    "create",
    "orchestrator",
    "orchestrator/new",
    "proposals",
    "manifests",
    "manifests/new",
    "schedules",
    "schedules/new",
    "temporal",
    "settings",
}

_PATH_ALIASES = {
    "new": "queue/new",
    "create": "queue/new",
}
_BLOCKED_TOP_LEVEL_TASK_IDS = {"speckit"}


class DashboardSkillOption(BaseModel):
    """Serializable skill option exposed to dashboard clients."""

    id: str = Field(description="Skill identifier")


class DashboardSkillListResponse(BaseModel):
    """Dashboard response containing available skill options."""

    items: dict[str, list[str]]
    legacy_items: list[DashboardSkillOption] = Field(
        default_factory=list, alias="legacyItems"
    )


class DashboardTaskSourceResponse(BaseModel):
    """Canonical source metadata for a unified dashboard task id."""

    task_id: str = Field(..., alias="taskId")
    source: str = Field(..., alias="source")
    source_label: str = Field(..., alias="sourceLabel")
    detail_path: str = Field(..., alias="detailPath")

class TaskSourceResolutionResponse(BaseModel):
    """Canonical source lookup for unified `/tasks/{taskId}` resolution."""

    task_id: str = Field(..., alias="taskId")
    source: Literal["queue", "orchestrator", "temporal"] = Field(..., alias="source")
    entry: str | None = Field(None, alias="entry")
    workflow_id: str | None = Field(None, alias="workflowId")


def _is_dynamic_detail(path: str, source: str) -> bool:
    parts = path.split("/")
    return (
        len(parts) == 2
        and parts[0] == source
        and _is_safe_detail_segment(parts[1])
        and parts[1].lower() != "new"
    )


def _is_safe_detail_segment(segment: str) -> bool:
    text = segment.strip()
    if not text:
        return False
    if text in {".", ".."}:
        return False
    return _SAFE_DETAIL_SEGMENT.fullmatch(text) is not None


def _is_temporal_task_id(path: str) -> bool:
    return path.startswith("mm:") and _is_safe_detail_segment(path)


def _parse_task_uuid(task_id: str) -> UUID | None:
    try:
        return UUID(str(task_id))
    except (TypeError, ValueError):
        return None


def _is_execution_admin(user: User | None) -> bool:
    return bool(user and getattr(user, "is_superuser", False))


def _is_allowed_path(path: str) -> bool:
    if not path:
        return False
    if path in _BLOCKED_TOP_LEVEL_TASK_IDS:
        return False
    if _SAFE_TASK_ID_SEGMENT.fullmatch(path):
        return True
    if _is_temporal_task_id(path):
        return True
    if path in _STATIC_PATHS:
        return True
    if "/" not in path and _is_safe_detail_segment(path):
        return True
    return any(
        _is_dynamic_detail(path, source)
        for source in (
            "queue",
            "orchestrator",
            "proposals",
            "manifests",
            "schedules",
            "temporal",
        )
    )


def _normalize_dashboard_path(path: str) -> str:
    normalized = path.strip("/")
    return _PATH_ALIASES.get(normalized, normalized)


def _resolve_user_dependency_overrides() -> list[Callable[..., object]]:
    """Return auth dependencies so tests can override them consistently."""

    dependencies: list[Callable[..., object]] = []
    for route in router.routes:
        dependant = getattr(route, "dependant", None)
        if dependant is None:
            continue
        for dependency in dependant.dependencies:
            call = dependency.call
            if call is None:
                continue
            if call.__name__ == "_current_user_fallback" or call.__name__.startswith(
                "current_"
            ):
                dependencies.append(call)

    if not dependencies:
        dependencies.append(get_current_user())
    return dependencies


async def _get_temporal_service(
    session: AsyncSession = Depends(get_async_session),
) -> TemporalExecutionService:
    return TemporalExecutionService(
        session,
        namespace=settings.temporal.namespace,
        run_continue_as_new_step_threshold=(
            settings.temporal.run_continue_as_new_step_threshold
        ),
        manifest_continue_as_new_phase_threshold=(
            settings.temporal.manifest_continue_as_new_phase_threshold
        ),
    )


def _build_task_source_response(
    *,
    task_id: str,
    source: str,
) -> DashboardTaskSourceResponse:
    source_label = {
        "queue": "Queue",
        "orchestrator": "Orchestrator",
        "temporal": "Temporal",
    }.get(source, source.title())
    return DashboardTaskSourceResponse(
        taskId=task_id,
        source=source,
        sourceLabel=source_label,
        detailPath=f"/tasks/{task_id}?source={source}",
    )


async def _resolve_dashboard_task_source(
    *,
    task_id: str,
    queue_service: AgentQueueService,
    session: AsyncSession,
    temporal_service: TemporalExecutionService,
    user: User,
) -> DashboardTaskSourceResponse | None:
    task_uuid: UUID | None = None
    try:
        task_uuid = UUID(task_id)
    except ValueError:
        task_uuid = None

    if task_uuid is not None:
        queue_job = await queue_service.get_job(task_uuid)
        if queue_job is not None:
            return _build_task_source_response(task_id=task_id, source="queue")

        orchestrator_run = await OrchestratorRepository(session).get_run(task_uuid)
        if orchestrator_run is not None:
            return _build_task_source_response(task_id=task_id, source="orchestrator")

    try:
        execution = await temporal_service.describe_execution(task_id)
    except TemporalExecutionNotFoundError:
        return None

    if bool(getattr(user, "is_superuser", False)):
        return _build_task_source_response(task_id=task_id, source="temporal")

    owner_id = getattr(execution, "owner_id", None)
    user_id = getattr(user, "id", None)
    if owner_id is not None and user_id is not None and str(owner_id) == str(user_id):
        return _build_task_source_response(task_id=task_id, source="temporal")
    return None


def _render_dashboard(request: Request, current_path: str) -> HTMLResponse:
    config = build_runtime_config(current_path)
    return templates.TemplateResponse(
        request,
        "task_dashboard.html",
        {
            "request": request,
            "dashboard_config": config,
            "current_path": current_path,
        },
    )


@router.get("/tasks", response_class=HTMLResponse, name="task_dashboard_root")
async def task_dashboard_root(
    request: Request,
    _user: User = Depends(get_current_user()),
) -> HTMLResponse:
    """Serve the dashboard root page."""

    return _render_dashboard(request, "/tasks")


@router.get("/tasks/{dashboard_path:path}", response_class=HTMLResponse)
async def task_dashboard_route(
    request: Request,
    dashboard_path: str,
    _user: User = Depends(get_current_user()),
) -> HTMLResponse:
    """Serve dashboard sub-routes from one HTML shell."""

    normalized = _normalize_dashboard_path(dashboard_path)
    if not _is_allowed_path(normalized):
        raise HTTPException(
            status_code=404,
            detail={
                "code": "dashboard_route_not_found",
                "message": (
                    "Dashboard route was not found. Use /tasks/list, /tasks/{taskId}, "
                    "/tasks/queue, /tasks/queue/new, /tasks/create, /tasks/new, "
                    "/tasks/orchestrator, /tasks/orchestrator/new, "
                    "/tasks/proposals, /tasks/manifests, /tasks/manifests/new, "
                    "/tasks/schedules, /tasks/schedules/new, /tasks/temporal, "
                    "or /tasks/settings."
                ),
            },
        )

    return _render_dashboard(request, f"/tasks/{normalized}")


@router.get("/api/tasks/skills", response_model=DashboardSkillListResponse)
async def list_dashboard_skills(
    _user: User = Depends(get_current_user()),
) -> DashboardSkillListResponse:
    """List currently available skills for task dashboard submission forms."""

    worker_skills = list(list_available_skill_names())
    orchestrator_skills = list(list_runnable_skill_names())
    legacy_sorted = sorted(
        set(worker_skills).union(orchestrator_skills),
        key=str,
    )
    return DashboardSkillListResponse(
        items={
            "worker": worker_skills,
            "orchestrator": orchestrator_skills,
        },
        legacyItems=[DashboardSkillOption(id=skill_id) for skill_id in legacy_sorted],
    )


@router.get(
    "/api/tasks/{task_id}/resolution",
    response_model=TaskSourceResolutionResponse,
)
async def resolve_dashboard_task_source(
    task_id: str,
    *,
    source_hint: Literal["queue", "orchestrator", "temporal"] | None = Query(
        None, alias="source"
    ),
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(get_current_user()),
) -> TaskSourceResolutionResponse:
    """Resolve a unified task handle to its canonical execution source."""

    service = TaskSourceMappingService(session)
    try:
        resolved = await service.resolve_task(
            task_id=task_id,
            source_hint=source_hint,
            user=user,
        )
    except TaskResolutionNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "task_not_found",
                "message": str(exc),
            },
        ) from exc
    except TaskResolutionAmbiguousError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "ambiguous_task_source",
                "message": str(exc),
                "sources": sorted(exc.sources),
            },
        ) from exc

    return TaskSourceResolutionResponse(
        taskId=task_id,
        source=resolved.source,
        entry=resolved.entry,
        workflowId=resolved.workflow_id,
    )


@router.get(
    "/api/tasks",
    response_model=JobListResponse,
    response_model_exclude={"items": {"__all__": {"finish_summary"}}},
)
async def list_dashboard_tasks(
    *,
    status_filter: str | None = Query(None, alias="status"),
    type_filter: str | None = Query(None, alias="type"),
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = Query(None, alias="cursor"),
    offset: int | None = Query(None, ge=0),
    summary: bool = Query(False, alias="summary"),
    service: AgentQueueService = Depends(_get_service),
    _user: User = Depends(get_current_user()),
) -> JobListResponse:
    """Task-centric alias for queue task listing with cursor pagination support."""

    return await list_jobs(
        status_filter=status_filter,
        type_filter=type_filter,
        limit=limit,
        cursor=cursor,
        offset=offset,
        summary=summary,
        service=service,
        _user=_user,
    )
@router.get(
    "/api/tasks/{task_id}/source",
    response_model=DashboardTaskSourceResponse,
)
async def resolve_dashboard_task_source(
    task_id: str,
    queue_service: AgentQueueService = Depends(_get_service),
    session: AsyncSession = Depends(get_async_session),
    temporal_service: TemporalExecutionService = Depends(_get_temporal_service),
    _user: User = Depends(get_current_user()),
) -> DashboardTaskSourceResponse:
    """Resolve a canonical dashboard task id to its backing source."""

    resolved = await _resolve_dashboard_task_source(
        task_id=task_id,
        queue_service=queue_service,
        session=session,
        temporal_service=temporal_service,
        user=_user,
    )
    if resolved is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "task_source_not_found",
                "message": f"Task {task_id} was not found in dashboard sources.",
            },
    )
    return resolved


__all__ = [
    "router",
    "_is_allowed_path",
    "_resolve_user_dependency_overrides",
]
