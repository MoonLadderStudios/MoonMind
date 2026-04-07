"""Routes that serve the MoonMind task dashboard UI shell."""

from __future__ import annotations

import asyncio
import os
import re
import socket
from html import escape
from collections.abc import Callable
from pathlib import Path
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
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
from api_service.ui_assets import MissionControlUIAssetsError, ui_assets

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
    "proposals",
    "manifests",
    "manifests/new",
    "schedules",
    "settings",
    "skills",
}

# Block legacy top-level segments (e.g. removed queue source, reserved "system" path).
_BLOCKED_TOP_LEVEL_TASK_IDS: set[str] = {
    "queue",
    "temporal",
    "system",
    "workers",
    "secrets",
    "create",
    "tasks-list",
}
_DASHBOARD_ROUTE_NOT_FOUND_DETAIL = {
    "code": "dashboard_route_not_found",
    "message": (
        "Dashboard route was not found. Use /tasks/list, /tasks/{taskId}, "
        "/tasks/new, "
        "/tasks/proposals, /tasks/manifests, /tasks/manifests/new, "
        "/tasks/schedules, /tasks/skills, or /tasks/settings."
    ),
}


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


def _raise_dashboard_route_not_found() -> None:
    raise HTTPException(
        status_code=404,
        detail=_DASHBOARD_ROUTE_NOT_FOUND_DETAIL,
    )


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


def _mission_control_ui_error_response(page: str, detail: str) -> HTMLResponse:
    """503 HTML when Vite assets are missing or incomplete (never a silent blank shell)."""
    body = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Mission Control UI unavailable</title>
</head>
<body>
  <h1>Mission Control UI unavailable</h1>
  <p>Missing or incomplete Vite bundle for the shared Mission Control entrypoint <code>mission-control</code>.</p>
  <p>While rendering Mission Control page <code>{escape(page)}</code>.</p>
  <p>{escape(detail)}</p>
  <p>Rebuild with <code>npm run ui:build</code> or deploy a Docker image that builds the UI from source (see <code>api_service/Dockerfile</code> <code>frontend-builder</code> stage).</p>
</body>
</html>"""
    return HTMLResponse(status_code=503, content=body, media_type="text/html")


def _vite_assets_or_error(page: str) -> HTMLResponse | str:
    try:
        return ui_assets("mission-control")
    except MissionControlUIAssetsError as exc:
        return _mission_control_ui_error_response(page, str(exc))


def _render_react_page(
    request: Request,
    page: str,
    current_path: str,
    initial_data: dict | None = None,
    *,
    data_wide_panel: bool = False,
) -> HTMLResponse:
    boot_initial_data = dict(initial_data or {})
    boot_layout = dict(boot_initial_data.get("layout") or {})
    boot_layout["dataWidePanel"] = data_wide_panel
    boot_initial_data["layout"] = boot_layout

    boot_payload = generate_boot_payload(page, initial_data=boot_initial_data)
    assets_html = _vite_assets_or_error(page)
    if isinstance(assets_html, HTMLResponse):
        return assets_html

    docker_image_tag = os.environ.get("CODEX_CLI_VERSION", "").strip() or "latest"
    hostname = socket.gethostname()

    return templates.TemplateResponse(
        request,
        "react_dashboard.html",
        {
            "request": request,
            "boot_payload": boot_payload,
            "assets_html": assets_html,
            "current_path": current_path,
            "docker_image_tag": docker_image_tag,
            "hostname": hostname,
        },
    )


@router.get("/tasks/secrets")
async def task_secrets_route(
    request: Request,
    _user: User = Depends(get_current_user()),
) -> RedirectResponse:
    """Redirect the legacy secrets page into unified settings."""
    return RedirectResponse(url="/tasks/settings?section=providers-secrets", status_code=307)

@router.get("/tasks", name="task_dashboard_root")
async def task_dashboard_root(
    request: Request,
    _user: User = Depends(get_current_user()),
) -> RedirectResponse:
    """Serve the dashboard root page."""

    return RedirectResponse(url="/tasks/list")


@router.get("/tasks/proposals", response_class=HTMLResponse)
async def task_proposals_route(
    request: Request,
    _user: User = Depends(get_current_user()),
) -> HTMLResponse:
    """Serve the React-powered proposals page."""
    return _render_react_page(request, "proposals", "/tasks/proposals", data_wide_panel=True)


@router.get("/tasks/schedules", response_class=HTMLResponse)
async def task_schedules_route(
    request: Request,
    _user: User = Depends(get_current_user()),
) -> HTMLResponse:
    """Serve the React-powered schedules page."""
    return _render_react_page(request, "schedules", "/tasks/schedules")


@router.get("/tasks/manifests", response_class=HTMLResponse)
async def task_manifests_route(
    request: Request,
    _user: User = Depends(get_current_user()),
) -> HTMLResponse:
    """Serve the React-powered manifests page."""
    return _render_react_page(request, "manifests", "/tasks/manifests")


@router.get("/tasks/manifests/new", response_class=HTMLResponse)
async def task_manifest_submit_route(
    request: Request,
    _user: User = Depends(get_current_user()),
) -> HTMLResponse:
    """Serve the React-powered manifest submit page."""
    current_path = "/tasks/manifests/new"
    return _render_react_page(
        request,
        "manifest-submit",
        current_path,
        initial_data={"dashboardConfig": build_runtime_config(current_path)},
    )


@router.get("/tasks/list", response_class=HTMLResponse)
async def task_list_route(
    request: Request,
    _user: User = Depends(get_current_user()),
) -> HTMLResponse:
    """Serve the React-powered tasks list page."""
    list_path = "/tasks/list"
    return _render_react_page(
        request,
        "tasks-list",
        list_path,
        initial_data={"dashboardConfig": build_runtime_config(list_path)},
        data_wide_panel=True,
    )


@router.get("/tasks/tasks-list")
async def task_tasks_list_route(
    request: Request,
    _user: User = Depends(get_current_user()),
) -> RedirectResponse:
    """Redirect the legacy tasks-list alias into the canonical list route."""
    return RedirectResponse(url="/tasks/list", status_code=307)


@router.get("/tasks/workers")
async def task_workers_route(
    request: Request,
    _user: User = Depends(get_current_user()),
) -> RedirectResponse:
    """Redirect the legacy workers page into unified settings."""
    return RedirectResponse(url="/tasks/settings?section=operations", status_code=307)


@router.get("/tasks/settings", response_class=HTMLResponse)
async def task_settings_route(
    request: Request,
    _user: User = Depends(get_current_user()),
) -> HTMLResponse:
    """Serve the React-powered settings page."""
    runtime_config = build_runtime_config("/tasks/settings")
    initial_data = {
        "workerPause": {
            "get": "/api/system/worker-pause",
            "post": "/api/system/worker-pause",
        },
        "runtimeConfig": runtime_config,
    }
    return _render_react_page(
        request,
        "settings",
        "/tasks/settings",
        initial_data=initial_data,
    )


@router.get("/tasks/new", response_class=HTMLResponse)
async def task_create_route(
    request: Request,
    _user: User = Depends(get_current_user()),
) -> HTMLResponse:
    """Serve the React-powered task create page."""
    current_path = "/tasks/new"
    return _render_react_page(
        request,
        "task-create",
        current_path,
        initial_data={"dashboardConfig": build_runtime_config(current_path)},
    )


@router.get("/tasks/create")
async def task_create_alias_route(
    request: Request,
    _user: User = Depends(get_current_user()),
) -> RedirectResponse:
    """Redirect the legacy create alias into the canonical create route."""
    return RedirectResponse(url="/tasks/new", status_code=307)


@router.get("/tasks/skills", response_class=HTMLResponse)
async def task_skills_route(
    request: Request,
    _user: User = Depends(get_current_user()),
) -> HTMLResponse:
    """Serve the React-powered skills page."""
    return _render_react_page(request, "skills", "/tasks/skills")


@router.get("/tasks/{dashboard_path:path}", response_class=HTMLResponse)
async def task_dashboard_route(
    request: Request,
    dashboard_path: str,
    _user: User = Depends(get_current_user()),
) -> HTMLResponse:
    """Serve dashboard sub-routes from one HTML shell."""

    normalized = dashboard_path.strip("/")
    if not _is_allowed_path(normalized) or normalized in _STATIC_PATHS:
        _raise_dashboard_route_not_found()

    detail_path = f"/tasks/{normalized}"
    return _render_react_page(
        request,
        "task-detail",
        detail_path,
        initial_data={"dashboardConfig": build_runtime_config(detail_path)},
    )


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
