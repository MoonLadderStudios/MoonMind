"""Routes that serve the MoonMind task dashboard UI shell."""

from __future__ import annotations

import json
import os
from collections.abc import Callable

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from api_service.api.routers.task_dashboard_view_model import build_runtime_config
from api_service.auth_providers import get_current_user
from api_service.db.models import User

router = APIRouter(prefix="", tags=["task-dashboard"])

TEMPLATES_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "templates",
)
templates = Jinja2Templates(directory=TEMPLATES_DIR)

_STATIC_PATHS = {
    "queue",
    "queue/new",
    "speckit",
    "speckit/new",
    "orchestrator",
    "orchestrator/new",
}


def _is_dynamic_detail(path: str, source: str) -> bool:
    parts = path.split("/")
    return (
        len(parts) == 2
        and parts[0] == source
        and bool(parts[1].strip())
        and parts[1].strip().lower() != "new"
    )


def _is_allowed_path(path: str) -> bool:
    if not path:
        return False
    if path in _STATIC_PATHS:
        return True
    return any(
        _is_dynamic_detail(path, source)
        for source in ("queue", "speckit", "orchestrator")
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


def _render_dashboard(request: Request, current_path: str) -> HTMLResponse:
    config = build_runtime_config(current_path)
    config_json = json.dumps(config)
    return templates.TemplateResponse(
        request,
        "task_dashboard.html",
        {
            "request": request,
            "dashboard_config": config,
            "dashboard_config_json": config_json,
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

    normalized = dashboard_path.strip("/")
    if not _is_allowed_path(normalized):
        raise HTTPException(
            status_code=404,
            detail={
                "code": "dashboard_route_not_found",
                "message": "Dashboard route was not found.",
            },
        )

    return _render_dashboard(request, f"/tasks/{normalized}")


__all__ = ["router", "_is_allowed_path", "_resolve_user_dependency_overrides"]
