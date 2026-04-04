#!/bin/bash
cat << 'INNER_EOF' > api_service/api/routers/task_dashboard.py
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from api_service.core.dependencies import (
    get_current_user,
    get_optional_current_user,
    get_templates,
)
from moonmind.schemas.user import User
from moonmind.settings.resolver import get_settings
from moonmind.workflows.skills.resolver import (
    list_available_skill_names,
    resolve_skills_local_mirror_root,
    validate_skill_name,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["task_dashboard"])

# Static sub-paths that map to the main SPA template route
_STATIC_PATHS = {
    "list",
    "create",
    "new",
    "proposals",
    "manifests",
    "manifests/new",
    "schedules",
    "settings",
    "skills",
    "workers",
}


class DashboardConfigResponse(BaseModel):
    """Configuration data exposed to the frontend dashboard script."""

    is_authenticated: bool
    user: dict[str, str] | None


class DashboardSkillOption(BaseModel):
    """Serializable skill option exposed to dashboard clients."""

    id: str = Field(description="Skill identifier")
    markdown: str | None = Field(None, description="Markdown content of the skill, if requested")


class DashboardSkillListResponse(BaseModel):
    """Response containing grouped skill lists for UI consumption."""

    items: dict[str, list[str]] = Field(description="Grouped available skill identifiers")
    legacyItems: list[DashboardSkillOption] = Field(description="Sorted legacy skill options")


class CreateSkillRequest(BaseModel):
    """Request payload to create a local skill via the dashboard."""

    name: str = Field(..., description="The name of the new skill.")
    markdown: str = Field(..., description="The Markdown content for SKILL.md")


@router.get("/tasks/{subpath:path}", response_class=HTMLResponse, include_in_schema=False)
async def task_dashboard_route(
    request: Request,
    subpath: str = "",
    _user: User | None = Depends(get_optional_current_user()),
    templates: dict = Depends(get_templates()),
) -> HTMLResponse | JSONResponse:
    """Server-side rendering route for the core task dashboard interface."""
    logger.debug("Received request for dashboard route: /tasks/%s", subpath)

    base_path = f"{request.url.scheme}://{request.headers.get('host')}"
    base_api_path = f"{base_path}/api"

    settings = get_settings()

    if not subpath or subpath in _STATIC_PATHS:
        return templates.TemplateResponse(
            request=request,
            name="task_dashboard.html",
            context={
                "base_path": base_path,
                "base_api_path": base_api_path,
                "workflow_api_path": f"{base_api_path}/workflows",
                "telemetry_settings": {
                    "enabled": settings.telemetry.enabled,
                    "service_name": settings.telemetry.service_name,
                    "traces_endpoint": settings.telemetry.traces_endpoint,
                },
            },
        )

    return JSONResponse(
        status_code=404,
        content={
            "detail": (
                "Dashboard route was not found. Use /tasks/list, /tasks/{taskId}, "
                "/tasks/create, /tasks/new, "
                "/tasks/proposals, /tasks/manifests, /tasks/manifests/new, "
                "/tasks/schedules, /tasks/workers, /tasks/skills, or /tasks/settings."
            ),
        },
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
                markdown_content = skill_file.read_text(encoding="utf-8")
        legacy_items.append(DashboardSkillOption(id=skill_id, markdown=markdown_content))

    return DashboardSkillListResponse(
        items={
            "worker": worker_skills,
        },
        legacyItems=legacy_items,
    )


@router.post("/api/tasks/skills", status_code=201)
async def create_local_skill(
    request: CreateSkillRequest,
    _user: User = Depends(get_current_user()),
):
    """Creates a new local skill directory and SKILL.md file."""
    try:
        validate_skill_name(request.name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    skills_root = resolve_skills_local_mirror_root()
    skill_dir = skills_root / request.name

    if skill_dir.exists():
        raise HTTPException(status_code=409, detail=f"Skill '{request.name}' already exists.")

    try:
        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_file = skill_dir / "SKILL.md"
        skill_file.write_text(request.markdown, encoding="utf-8")
    except Exception as e:
        logger.error("Failed to create skill file %s: %s", request.name, e)
        raise HTTPException(status_code=500, detail="Failed to write skill file.")

    return {"message": "Skill created successfully", "id": request.name}


@router.get("/api/tasks/config", response_model=DashboardConfigResponse)
async def get_dashboard_config(
    user: User | None = Depends(get_optional_current_user()),
) -> DashboardConfigResponse:
    """Returns configuration variables required to initialize the dashboard frontend script."""
    logger.debug(
        "Fetching dashboard config. Authenticated: %s, User email: %s",
        user is not None,
        getattr(user, "email", None),
    )
    if user:
        return DashboardConfigResponse(
            is_authenticated=True,
            user={"email": user.email},
        )
    return DashboardConfigResponse(is_authenticated=False, user=None)


def get_real_client_ip(request: Request, x_forwarded_for: Annotated[str | None, Header()] = None):
    if x_forwarded_for:
        return x_forwarded_for.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


@router.get("/api/tasks/ip")
async def get_client_ip(
    request: Request,
    x_forwarded_for: Annotated[str | None, Header()] = None,
):
    """Return the requesting client IP address for local debugging and verification."""
    client_ip = get_real_client_ip(request, x_forwarded_for)
    return {"ip": client_ip}
INNER_EOF
