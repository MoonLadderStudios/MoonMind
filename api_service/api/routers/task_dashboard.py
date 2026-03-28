"""Routes that serve the MoonMind task dashboard UI shell."""

from __future__ import annotations

import asyncio
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

from api_service.db.base import get_async_session
from api_service.api.routers.task_dashboard_view_model import build_runtime_config
from api_service.auth_providers import get_current_user
from api_service.db.models import User
from moonmind.config.settings import settings
from moonmind.workflows.skills.resolver import (
    SkillResolutionError,
    list_available_skill_names,
    resolve_skills_local_mirror_root,
    validate_skill_name,
)

from api_service.ui_boot import generate_boot_payload
from api_service.ui_assets import ui_assets

from moonmind.workflows.temporal import (
    TemporalExecutionNotFoundError,
    TemporalExecutionService,
)

router = APIRouter(prefix="", tags=["task-dashboard"])

TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

_SAFE_DETAIL_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SAFE_TASK_ID_SEGMENT = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_STATIC_PATHS = {
    "list",
    "new",
    "create",
    "proposals",
    "manifests",
    "manifests/new",
    "schedules",
    "settings",
    "skills",
    "workers",
}

_PATH_ALIASES = {
    "create": "new",
}
# Block legacy top-level segments (e.g. removed queue source, reserved "system" path).
_BLOCKED_TOP_LEVEL_TASK_IDS: set[str] = {"queue", "temporal", "system"}


class CreateSkillRequest(BaseModel):
    """Payload for creating a new skill via the dashboard."""

    name: str = Field(..., description="The name of the new skill")
    markdown: str = Field(..., description="The markdown content of the new skill")


class DashboardSkillOption(BaseModel):
    """Serializable skill option exposed to dashboard clients."""

    id: str = Field(description="Skill identifier")
    markdown: str | None = Field(None, description="Markdown content of the skill, if requested")


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
    source: Literal["temporal"] = Field(..., alias="source")
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
            "proposals",
            "manifests",
            "schedules",
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


@router.get("/tasks/proposals", response_class=HTMLResponse)
async def task_proposals_route(
    request: Request,
    _user: User = Depends(get_current_user()),
) -> HTMLResponse:
    """Serve the React-powered proposals page."""
    current_path = "/tasks/proposals"
    boot_payload = generate_boot_payload("proposals")
    assets_html = ui_assets("proposals")

    return templates.TemplateResponse(
        request,
        "react_dashboard.html",
        {
            "request": request,
            "boot_payload": boot_payload,
            "assets_html": assets_html,
            "current_path": current_path,
        },
    )


@router.get("/tasks/schedules", response_class=HTMLResponse)
async def task_schedules_route(
    request: Request,
    _user: User = Depends(get_current_user()),
) -> HTMLResponse:
    """Serve the React-powered schedules page."""
    current_path = "/tasks/schedules"
    boot_payload = generate_boot_payload("schedules")
    assets_html = ui_assets("schedules")

    return templates.TemplateResponse(
        request,
        "react_dashboard.html",
        {
            "request": request,
            "boot_payload": boot_payload,
            "assets_html": assets_html,
            "current_path": current_path,
        },
    )


@router.get("/tasks/manifests", response_class=HTMLResponse)
async def task_manifests_route(
    request: Request,
    _user: User = Depends(get_current_user()),
) -> HTMLResponse:
    """Serve the React-powered manifests page."""
    current_path = "/tasks/manifests"
    boot_payload = generate_boot_payload("manifests")
    assets_html = ui_assets("manifests")

    return templates.TemplateResponse(
        request,
        "react_dashboard.html",
        {
            "request": request,
            "boot_payload": boot_payload,
            "assets_html": assets_html,
            "current_path": current_path,
        },
    )


@router.get("/tasks/tasks-list", response_class=HTMLResponse)
async def task_tasks_list_route(
    request: Request,
    _user: User = Depends(get_current_user()),
) -> HTMLResponse:
    """Serve the React-powered tasks-list page."""
    current_path = "/tasks/tasks-list"
    boot_payload = generate_boot_payload("tasks-list")
    assets_html = ui_assets("tasks-list")

    return templates.TemplateResponse(
        request,
        "react_dashboard.html",
        {
            "request": request,
            "boot_payload": boot_payload,
            "assets_html": assets_html,
            "current_path": current_path,
        },
    )


@router.get("/tasks/list", response_class=HTMLResponse)
async def task_list_route(
    request: Request,
    _user: User = Depends(get_current_user()),
) -> HTMLResponse:
    """Serve the React-powered tasks list page."""
    current_path = "/tasks/list"
    boot_payload = generate_boot_payload("tasks-list")
    assets_html = ui_assets("tasks-list")

    return templates.TemplateResponse(
        request,
        "react_dashboard.html",
        {
            "request": request,
            "boot_payload": boot_payload,
            "assets_html": assets_html,
            "current_path": current_path,
        },
    )


@router.get("/tasks/workers", response_class=HTMLResponse)
async def task_workers_route(
    request: Request,
    _user: User = Depends(get_current_user()),
) -> HTMLResponse:
    """Serve the React-powered workers page."""
    current_path = "/tasks/workers"
    boot_payload = generate_boot_payload("workers")
    assets_html = ui_assets("workers")

    return templates.TemplateResponse(
        request,
        "react_dashboard.html",
        {
            "request": request,
            "boot_payload": boot_payload,
            "assets_html": assets_html,
            "current_path": current_path,
        },
    )


@router.get("/tasks/settings", response_class=HTMLResponse)
async def task_settings_route(
    request: Request,
    _user: User = Depends(get_current_user()),
) -> HTMLResponse:
    """Serve the React-powered settings page."""
    current_path = "/tasks/settings"
    boot_payload = generate_boot_payload("settings")
    assets_html = ui_assets("settings")

    return templates.TemplateResponse(
        request,
        "react_dashboard.html",
        {
            "request": request,
            "boot_payload": boot_payload,
            "assets_html": assets_html,
            "current_path": current_path,
        },
    )


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
                    "/tasks/create, /tasks/new, "
                    "/tasks/proposals, /tasks/manifests, /tasks/manifests/new, "
                    "/tasks/schedules, /tasks/workers, /tasks/skills, or /tasks/settings."
                ),
            },
        )

    # Exclude creation paths from the task detail React shell, so they fall back to the legacy shell
    if normalized not in ("new", "create", "manifests/new"):
        if _is_safe_detail_segment(normalized) or _is_dynamic_detail(normalized, "temporal") or _is_dynamic_detail(normalized, "proposals") or _is_dynamic_detail(normalized, "manifests") or _is_dynamic_detail(normalized, "schedules"):
            current_path = f"/tasks/{normalized}"
            boot_payload = generate_boot_payload("task-detail")
            assets_html = ui_assets("task-detail")
            return templates.TemplateResponse(
                request,
                "react_dashboard.html",
                {
                    "request": request,
                    "boot_payload": boot_payload,
                    "assets_html": assets_html,
                    "current_path": current_path,
                },
            )

    return _render_dashboard(request, f"/tasks/{normalized}")


@router.get("/api/tasks/skills", response_model=DashboardSkillListResponse)
async def list_dashboard_skills(
    include_content: bool = Query(False, alias="includeContent"),
    _user: User = Depends(get_current_user()),
) -> DashboardSkillListResponse:
    """List currently available skills for task dashboard submission forms."""

    worker_skills = list(list_available_skill_names())
    legacy_sorted = sorted(set(worker_skills), key=str)

    legacy_items = []
    skills_root = resolve_skills_local_mirror_root()

    for skill_id in legacy_sorted:
        markdown_content = None
        if include_content:
            skill_dir = skills_root / skill_id
            skill_file = skill_dir / "SKILL.md"
            if skill_file.exists():
                markdown_content = await asyncio.to_thread(skill_file.read_text, encoding="utf-8")
        legacy_items.append(DashboardSkillOption(id=skill_id, markdown=markdown_content))

    return DashboardSkillListResponse(
        items={
            "worker": worker_skills,
        },
        legacyItems=legacy_items,
    )


@router.post(
    "/api/tasks/skills",
    status_code=201,
)
async def create_dashboard_skill(
    request: CreateSkillRequest,
    _user: User = Depends(get_current_user()),
) -> dict[str, str]:
    """Create a new local skill from the dashboard."""

    try:
        validated_name = validate_skill_name(request.name)
    except SkillResolutionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    skills_root = resolve_skills_local_mirror_root()
    skill_dir = skills_root / validated_name

    try:
        skill_dir.mkdir(parents=True, exist_ok=False)
    except FileExistsError as exc:
        raise HTTPException(
            status_code=409,
            detail=f"Skill '{validated_name}' already exists locally.",
        ) from exc
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text(request.markdown, encoding="utf-8")

    return {"status": "success"}


__all__ = [
    "router",
    "_is_allowed_path",
    "_resolve_user_dependency_overrides",
]
