"""Router tests for preset endpoints."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_service.api.routers.presets import (
    _get_catalog_service,
    _get_save_service,
    router,
)
from api_service.auth_providers import get_current_user
from moonmind.config.settings import settings

def _mock_user():
    return SimpleNamespace(
        id=uuid4(),
        email="template-tester@example.com",
        is_superuser=True,
    )

def _override_user_dependencies(app: FastAPI) -> None:
    dependencies = {
        dep.call
        for route in router.routes
        if route.dependant is not None
        for dep in route.dependant.dependencies
        if dep.call.__name__ == "_current_user_fallback"
    }
    if not dependencies:
        dependencies = {get_current_user()}
    for dependency in dependencies:
        app.dependency_overrides[dependency] = _mock_user

def _build_app() -> tuple[TestClient, AsyncMock, AsyncMock]:
    app = FastAPI()
    app.include_router(router)
    catalog = AsyncMock()
    saver = AsyncMock()
    app.dependency_overrides[_get_catalog_service] = lambda: catalog
    app.dependency_overrides[_get_save_service] = lambda: saver
    _override_user_dependencies(app)
    settings.feature_flags.preset_catalog = True
    settings.feature_flags.disable_preset_catalog = False
    return TestClient(app), catalog, saver

def test_list_templates_success() -> None:
    client, catalog, _ = _build_app()
    catalog.list_templates.return_value = [
        {
            "slug": "example",
            "scope": "global",
            "scopeRef": None,
            "title": "Example",
            "description": "desc",
            "tags": ["demo"],
            "isFavorite": False,
            "recentAppliedAt": None,
            "requiredCapabilities": ["codex"],
            "inputs": [
                {
                    "name": "goal",
                    "label": "Goal",
                    "type": "text",
                    "required": True,
                }
            ],
            "inputSchema": {
                "type": "object",
                "required": ["goal"],
                "properties": {"goal": {"type": "string", "title": "Goal"}},
            },
            "uiSchema": {},
            "defaults": {},
            "releaseStatus": "active",
            "steps": [{"instructions": "do work"}],
            "annotations": {},
            "reviewedBy": None,
            "reviewedAt": None,
        }
    ]

    response = client.get("/api/presets", params={"scope": "global"})

    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["slug"] == "example"
    assert item["inputSchema"]["required"] == ["goal"]
    assert item["inputs"][0]["name"] == "goal"
    assert "steps" not in item

def test_list_templates_defaults_to_personal_scope_when_omitted() -> None:
    client, catalog, _ = _build_app()
    catalog.list_templates.return_value = []

    response = client.get("/api/presets")

    assert response.status_code == 200
    kwargs = catalog.list_templates.await_args.kwargs
    assert kwargs["scope"] == "personal"
    assert kwargs["scope_ref"] is not None

def test_expand_template_success() -> None:
    client, catalog, _ = _build_app()
    catalog.expand_template.return_value = {
        "steps": [{"id": "tpl:demo:1.0.0:01:abcd1234", "instructions": "do work"}],
        "composition": {
            "slug": "demo",
            "scope": "global",
            "stepIds": ["tpl:demo:1.0.0:01:abcd1234"],
            "includes": [],
        },
        "appliedTemplate": {
            "slug": "demo",
            "inputs": {},
            "stepIds": ["tpl:demo:1.0.0:01:abcd1234"],
            "appliedAt": "2026-02-18T00:00:00+00:00",
        },
        "capabilities": ["codex", "git"],
        "warnings": [],
    }

    response = client.post(
        "/api/presets/demo:expand",
        params={"scope": "global"},
        json={"inputs": {}, "options": {"enforceStepLimit": True}},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["appliedTemplate"]["slug"] == "demo"
    assert payload["composition"]["slug"] == "demo"
    assert payload["capabilities"] == ["codex", "git"]

def test_save_from_workflow_success() -> None:
    client, _, saver = _build_app()
    saver.save_from_workflow.return_value = {
        "slug": "saved-template",
        "scope": "personal",
        "scopeRef": str(uuid4()),
        "title": "Saved Template",
        "description": "Saved",
        "tags": [],
        "isFavorite": False,
        "recentAppliedAt": None,
        "requiredCapabilities": [],
        "releaseStatus": "draft",
        "inputs": [],
        "steps": [{"instructions": "run"}],
        "annotations": {},
        "reviewedBy": None,
        "reviewedAt": None,
    }

    response = client.post(
        "/api/presets/save-from-workflow",
        json={
            "scope": "personal",
            "title": "Saved Template",
            "description": "Saved",
            "steps": [{"instructions": "run"}],
            "suggestedInputs": [],
            "tags": [],
        },
    )

    assert response.status_code == 201
    assert response.json()["slug"] == "saved-template"

def test_list_templates_returns_404_when_catalog_disabled() -> None:
    client, _, _ = _build_app()
    settings.feature_flags.disable_preset_catalog = True

    try:
        response = client.get("/api/presets", params={"scope": "global"})
    finally:
        settings.feature_flags.disable_preset_catalog = False

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "preset_catalog_disabled"
