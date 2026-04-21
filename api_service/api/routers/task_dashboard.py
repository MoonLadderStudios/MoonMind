"""Routes that serve the MoonMind task dashboard UI shell."""

from __future__ import annotations

import asyncio
import io
import re
import shutil
import tempfile
import zipfile
from collections.abc import Callable
from html import escape
from pathlib import Path, PurePosixPath
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.db.base import get_async_session
from api_service.api.routers.task_dashboard_view_model import (
    build_repository_branch_options,
    build_runtime_config,
)
from api_service.auth_providers import get_current_user
from api_service.db.models import User
from moonmind.config.settings import settings
from moonmind.workflows.skills.resolver import (
    SkillResolutionError,
    list_available_skill_names,
    resolve_skill_markdown_path,
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
    "schedules",
    "settings",
    "skills",
}
_MAX_SKILL_ZIP_BYTES = 10 * 1024 * 1024
_MAX_SKILL_UNCOMPRESSED_BYTES = 25 * 1024 * 1024
_MAX_SKILL_ZIP_ENTRIES = 512

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
        "/tasks/proposals, /tasks/manifests, "
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


class DashboardBranchOption(BaseModel):
    """Serializable Git branch option exposed to dashboard clients."""

    value: str = Field(description="Branch name")
    label: str = Field(description="Display label")
    source: str = Field(description="Branch option source")


class DashboardBranchListResponse(BaseModel):
    """Dashboard response containing branch options for one repository."""

    items: list[DashboardBranchOption] = Field(default_factory=list)
    error: str | None = Field(None)


class _ValidatedSkillZip(BaseModel):
    skill_name: str
    root_prefix: str | None = None


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
    dashboard_config = dict(boot_initial_data.get("dashboardConfig") or {})
    if not dashboard_config:
        dashboard_config = build_runtime_config(current_path)
        boot_initial_data["dashboardConfig"] = dashboard_config

    boot_payload = generate_boot_payload(page, initial_data=boot_initial_data)
    assets_html = _vite_assets_or_error(page)
    if isinstance(assets_html, HTMLResponse):
        return assets_html

    system_config = dict(dashboard_config.get("system") or {})

    return templates.TemplateResponse(
        request,
        "react_dashboard.html",
        {
            "request": request,
            "boot_payload": boot_payload,
            "assets_html": assets_html,
            "current_path": current_path,
            "build_id": system_config.get("buildId"),
        },
    )


def _is_zip_symlink(info: zipfile.ZipInfo) -> bool:
    mode = (info.external_attr >> 16) & 0o170000
    return mode == 0o120000


def _normalize_zip_member(name: str) -> PurePosixPath:
    if "\\" in name:
        raise HTTPException(
            status_code=400,
            detail="Skill zip contains an unsafe path.",
        )
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts:
        raise HTTPException(
            status_code=400,
            detail="Skill zip contains an unsafe path.",
        )
    parts = tuple(part for part in path.parts if part not in ("", "."))
    if not parts:
        raise HTTPException(status_code=400, detail="Skill zip contains an empty path.")
    return PurePosixPath(*parts)


def _is_ignored_zip_member(path: PurePosixPath) -> bool:
    return "__MACOSX" in path.parts or path.name == ".DS_Store"


def _validate_skill_zip(filename: str | None, payload: bytes) -> _ValidatedSkillZip:
    if not payload:
        raise HTTPException(status_code=400, detail="Skill zip file is empty.")
    if len(payload) > _MAX_SKILL_ZIP_BYTES:
        raise HTTPException(status_code=413, detail="Skill zip file is too large.")

    try:
        archive = zipfile.ZipFile(io.BytesIO(payload))
    except zipfile.BadZipFile as exc:
        raise HTTPException(
            status_code=400,
            detail="Uploaded file is not a valid zip archive.",
        ) from exc

    with archive:
        infos = [info for info in archive.infolist() if not info.is_dir()]
        if not infos:
            raise HTTPException(status_code=400, detail="Skill zip must contain files.")
        if len(infos) > _MAX_SKILL_ZIP_ENTRIES:
            raise HTTPException(
                status_code=413,
                detail="Skill zip contains too many files.",
            )

        total_size = 0
        normalized_paths: list[PurePosixPath] = []
        seen_paths: set[PurePosixPath] = set()
        for info in infos:
            if info.flag_bits & 0x1:
                raise HTTPException(
                    status_code=400,
                    detail="Encrypted skill zip entries are not supported.",
                )
            if _is_zip_symlink(info):
                raise HTTPException(
                    status_code=400,
                    detail="Skill zip cannot contain symlinks.",
                )
            total_size += info.file_size
            if total_size > _MAX_SKILL_UNCOMPRESSED_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail="Skill zip expands to too much data.",
                )
            normalized_path = _normalize_zip_member(info.filename)
            if _is_ignored_zip_member(normalized_path):
                continue
            if normalized_path in seen_paths:
                raise HTTPException(
                    status_code=400,
                    detail="Skill zip contains duplicate file paths.",
                )
            seen_paths.add(normalized_path)
            normalized_paths.append(normalized_path)

        if not normalized_paths:
            raise HTTPException(status_code=400, detail="Skill zip must contain skill files.")

        skill_files = [path for path in normalized_paths if path.name == "SKILL.md"]
        root_skill_files = [path for path in skill_files if len(path.parts) == 1]
        top_level_names = {path.parts[0] for path in normalized_paths}
        nested_skill_files = [
            path
            for path in skill_files
            if len(path.parts) >= 2 and path.parts[1:] == ("SKILL.md",)
        ]

        if root_skill_files:
            if len(skill_files) > 1:
                raise HTTPException(
                    status_code=400,
                    detail="Skill zip must contain only one SKILL.md file.",
                )
            fallback_name = Path(filename or "skill").stem
            return _ValidatedSkillZip(skill_name=validate_skill_name(fallback_name))

        if (
            len(top_level_names) != 1
            or len(nested_skill_files) != 1
            or len(skill_files) != 1
        ):
            raise HTTPException(
                status_code=400,
                detail="Skill zip must contain one skill directory with a SKILL.md file.",
            )

        root_prefix = next(iter(top_level_names))
        return _ValidatedSkillZip(
            skill_name=validate_skill_name(root_prefix),
            root_prefix=root_prefix,
        )


def _write_skill_zip(
    skill_dir: Path,
    payload: bytes,
    validated: _ValidatedSkillZip,
) -> None:
    parent = skill_dir.parent
    parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".skill-upload-", dir=parent) as temp_dir_name:
        temp_dir = Path(temp_dir_name)
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            for info in archive.infolist():
                if info.is_dir():
                    continue
                normalized = _normalize_zip_member(info.filename)
                if _is_ignored_zip_member(normalized):
                    continue
                relative_parts = normalized.parts
                if validated.root_prefix is not None:
                    if relative_parts[0] != validated.root_prefix:
                        raise HTTPException(status_code=400, detail="Skill zip root changed during extraction.")
                    relative_parts = relative_parts[1:]
                target = temp_dir.joinpath(*relative_parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source, target.open("wb") as destination:
                    shutil.copyfileobj(source, destination)

                permissions = (info.external_attr >> 16) & 0o777
                if permissions & 0o111:
                    target.chmod(0o755)

        if not (temp_dir / "SKILL.md").is_file():
            raise HTTPException(status_code=400, detail="Skill zip must contain a SKILL.md file.")
        if skill_dir.exists():
            raise HTTPException(
                status_code=409,
                detail=f"Skill '{validated.skill_name}' already exists locally.",
            )
        temp_dir.rename(skill_dir)


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


@router.get("/tasks/manifests/new", status_code=307, response_class=RedirectResponse)
async def task_manifest_submit_route(
    request: Request,
    _user: User = Depends(get_current_user()),
) -> RedirectResponse:
    """Redirect the legacy manifest submit route into the unified manifests page."""
    return RedirectResponse(url="/tasks/manifests", status_code=307)


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


@router.get("/oauth-terminal", response_class=HTMLResponse)
async def oauth_terminal_route(
    request: Request,
    session_id: str = Query("", alias="session_id"),
    _user: User = Depends(get_current_user()),
) -> HTMLResponse:
    """Serve the OAuth terminal shell launched from Settings."""
    current_path = "/oauth-terminal"
    return _render_react_page(
        request,
        "oauth-terminal",
        current_path,
        initial_data={"sessionId": session_id},
        data_wide_panel=True,
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

    async def _get_skill_option(skill_id: str) -> DashboardSkillOption:
        markdown_content = None
        if include_content:
            skill_file = resolve_skill_markdown_path(skill_id)
            if skill_file is not None:
                markdown_content = await asyncio.to_thread(skill_file.read_text, encoding="utf-8")
        return DashboardSkillOption(id=skill_id, markdown=markdown_content)

    legacy_items = await asyncio.gather(
        *(_get_skill_option(skill_id) for skill_id in legacy_sorted)
    )

    return DashboardSkillListResponse(
        items={
            "worker": worker_skills,
        },
        legacyItems=legacy_items,
    )


@router.get("/api/github/branches", response_model=DashboardBranchListResponse)
async def list_dashboard_github_branches(
    repository: str = Query(..., min_length=1),
    _user: User = Depends(get_current_user()),
) -> DashboardBranchListResponse:
    """List GitHub branches through MoonMind so browsers never call GitHub directly."""

    payload = build_repository_branch_options(repository)
    return DashboardBranchListResponse(**payload)


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


@router.post(
    "/api/tasks/skills/upload",
    status_code=201,
)
async def upload_dashboard_skill_zip(
    file: UploadFile = File(...),
    _user: User = Depends(get_current_user()),
) -> dict[str, str]:
    """Create a new local skill from an uploaded zip bundle."""

    payload = await file.read()
    validated = _validate_skill_zip(file.filename, payload)
    skills_root = resolve_skills_local_mirror_root()
    skill_dir = skills_root / validated.skill_name

    _write_skill_zip(skill_dir, payload, validated)

    return {"status": "success", "skill": validated.skill_name}


__all__ = [
    "router",
    "_is_allowed_path",
    "_resolve_user_dependency_overrides",
]
