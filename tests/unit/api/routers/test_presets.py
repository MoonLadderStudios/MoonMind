"""Router tests for preset endpoints."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_service.api.routers.presets import (
    _get_catalog_service,
    _get_save_service,
    router,
)
from api_service.auth_providers import get_instance_access
from moonmind.config.settings import settings


def _build_app() -> tuple[TestClient, AsyncMock, AsyncMock]:
    app = FastAPI()
    app.include_router(router)
    catalog = AsyncMock()
    saver = AsyncMock()
    app.dependency_overrides[_get_catalog_service] = lambda: catalog
    app.dependency_overrides[_get_save_service] = lambda: saver
    app.dependency_overrides[get_instance_access] = lambda: None
    settings.feature_flags.preset_catalog = True
    settings.feature_flags.disable_preset_catalog = False
    return TestClient(app, client=("127.0.0.1", 50000)), catalog, saver


def test_list_templates_success() -> None:
    client, catalog, _ = _build_app()
    catalog.list_templates.return_value = [
        {
            "slug": "example",
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

    response = client.get("/api/presets")

    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["slug"] == "example"
    assert item["inputSchema"]["required"] == ["goal"]
    assert item["inputs"][0]["name"] == "goal"
    assert "steps" not in item


def test_list_templates_returns_declared_workflow_publish() -> None:
    client, catalog, _ = _build_app()
    catalog.list_templates.return_value = [
        {
            "slug": "tracker-only",
            "title": "Tracker Only",
            "description": "desc",
            "workflowPublish": {"mode": "none"},
        },
        {
            "slug": "no-policy",
            "title": "No Policy",
            "description": "desc",
        },
    ]

    response = client.get("/api/presets")

    assert response.status_code == 200
    items = {item["slug"]: item for item in response.json()["items"]}
    assert items["tracker-only"]["workflowPublish"] == {"mode": "none"}
    assert items["no-policy"]["workflowPublish"] is None


def test_list_templates_has_no_human_scope_when_omitted() -> None:
    client, catalog, _ = _build_app()
    catalog.list_templates.return_value = []

    response = client.get("/api/presets")

    assert response.status_code == 200
    kwargs = catalog.list_templates.await_args.kwargs
    assert "scope" not in kwargs
    assert "scope_ref" not in kwargs
    assert "user_id" not in kwargs


def test_expand_template_success() -> None:
    client, catalog, _ = _build_app()
    catalog.expand_template.return_value = {
        "steps": [{"id": "tpl:demo:1.0.0:01:abcd1234", "instructions": "do work"}],
        "composition": {
            "slug": "demo",
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
        "publish": {
            "mode": "none",
            "mergeAutomation": {
                "enabled": True,
                "finishMode": "fix_only",
            },
        },
    }

    response = client.post(
        "/api/presets/demo:expand",
        json={"inputs": {}, "options": {"enforceStepLimit": True}},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["appliedTemplate"]["slug"] == "demo"
    assert payload["composition"]["slug"] == "demo"
    assert payload["capabilities"] == ["codex", "git"]
    assert payload["publish"] == {
        "mode": "none",
        "mergeAutomation": {
            "enabled": True,
            "finishMode": "fix_only",
        },
    }


def test_save_from_workflow_success() -> None:
    client, _, saver = _build_app()
    saver.save_from_workflow.return_value = {
        "slug": "saved-template",
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
        response = client.get("/api/presets")
    finally:
        settings.feature_flags.disable_preset_catalog = False

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "preset_catalog_disabled"


@pytest.mark.parametrize("key", ["scope", "scopeRef", "scope_ref"])
@pytest.mark.parametrize("method,path", [
    ("GET", "/api/presets"),
    ("GET", "/api/presets/demo"),
    ("POST", "/api/presets"),
    ("POST", "/api/presets/save-from-workflow"),
    ("POST", "/api/presets/demo:expand"),
    ("POST", "/api/presets/demo:favorite"),
    ("DELETE", "/api/presets/demo:favorite"),
    ("PUT", "/api/presets/demo/review"),
    ("DELETE", "/api/presets/demo"),
])
def test_retired_scope_query_is_rejected_before_catalog_use(key, method, path):
    client, catalog, saver = _build_app()

    response = client.request(method, path, params={key: ""}, json={})

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "legacy_preset_requires_refresh"
    assert not catalog.mock_calls
    assert not saver.mock_calls


@pytest.mark.parametrize("method", ["GET", "POST"])
@pytest.mark.parametrize("mode", ["accounts", "oidc"])
@pytest.mark.parametrize("session_admitted", [True, False])
def test_presets_preserve_direct_authenticated_session_admission(
    monkeypatch, session_admitted, mode, method
):
    from fastapi import HTTPException

    from api_service import auth_providers
    from api_service.db.base import get_async_session
    from moonmind.security import auth_modes_4120

    monkeypatch.setattr(auth_modes_4120, "get_request_production_mode", lambda: mode)
    app = FastAPI()
    app.include_router(router)
    catalog = AsyncMock()
    catalog.list_templates.return_value = []
    app.dependency_overrides[_get_catalog_service] = lambda: catalog
    session = object()
    app.dependency_overrides[get_async_session] = lambda: session
    monkeypatch.setattr(auth_providers, "is_disabled_local_mode", lambda: False)
    resolver = AsyncMock(return_value=object())
    if not session_admitted:
        resolver.side_effect = HTTPException(401, detail={"code": "auth_invalid"})
    monkeypatch.setattr(
        auth_providers,
        "_validate_oidc_cookie_user"
        if mode == "oidc"
        else "_resolve_session_principal",
        resolver,
    )
    monkeypatch.setattr(settings.feature_flags, "preset_catalog", True)
    monkeypatch.setattr(settings.feature_flags, "disable_preset_catalog", False)

    # A supported authenticated session may arrive directly, with neither
    # loopback nor trusted-ingress headers. Its qualified resolver owns access.
    client = TestClient(app, client=("203.0.113.9", 50000))
    path = "/api/presets" if method == "GET" else "/api/presets/example:favorite"
    response = client.request(
        method, path, headers={"cookie": "session=opaque"}, json={} if method == "POST" else None
    )
    expected_status = 200 if method == "GET" else 204
    assert response.status_code == (expected_status if session_admitted else 401)
    resolver.assert_awaited_once()
    assert resolver.await_args.args[1] is session
    assert resolver.await_args.kwargs == ({} if mode == "oidc" else {"optional": False})
    operation = catalog.list_templates if method == "GET" else catalog.set_favorite
    if session_admitted:
        operation.assert_awaited_once()
    else:
        assert not catalog.mock_calls


@pytest.mark.parametrize("peer", ["127.0.0.1", "203.0.113.9"])
def test_presets_keep_account_free_local_admission_without_loading_user(
    monkeypatch, peer
):
    from api_service import auth_providers
    from api_service.db.base import get_async_session

    app = FastAPI()
    app.include_router(router)
    catalog = AsyncMock()
    catalog.list_templates.return_value = []
    app.dependency_overrides[_get_catalog_service] = lambda: catalog
    app.dependency_overrides[get_async_session] = lambda: object()
    monkeypatch.setattr(auth_providers, "is_disabled_local_mode", lambda: True)
    monkeypatch.setattr(
        auth_providers, "_disabled_account_free_allowed", AsyncMock(return_value=True)
    )
    load_user = AsyncMock(side_effect=AssertionError("must not load a person"))
    monkeypatch.setattr(auth_providers, "_load_disabled_user", load_user)
    monkeypatch.setattr(settings.feature_flags, "preset_catalog", True)
    monkeypatch.setattr(settings.feature_flags, "disable_preset_catalog", False)
    response = TestClient(app, base_url="http://localhost", client=(peer, 50000)).get(
        "/api/presets"
    )
    assert response.status_code == (200 if peer == "127.0.0.1" else 401)
    load_user.assert_not_awaited()
    if peer == "127.0.0.1":
        catalog.list_templates.assert_awaited_once()
    else:
        catalog.list_templates.assert_not_awaited()
