"""Operator-visible runtime-provider migration route.

Source issue: MoonLadderStudios/MoonMind#3833 (required work 10).
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_service.api.routers import (
    omnigent_runtime_provider_migration as migration_router,
)
from api_service.auth_providers import get_current_user

_CODEX_GATE = "MOONMIND_OMNIGENT_GENERIC_CODEX_QUALIFIED"
_ROLLBACK_ENV = "MOONMIND_OMNIGENT_RUNTIME_PROVIDER_ROLLBACK"


def _client(*, superuser: bool = True) -> TestClient:
    app = FastAPI()
    app.include_router(migration_router.router)
    app.dependency_overrides[get_current_user()] = lambda: SimpleNamespace(
        id=None, is_superuser=superuser
    )
    return TestClient(app)


def test_route_reports_every_combination_with_rollout_state(monkeypatch):
    monkeypatch.setenv(_CODEX_GATE, "true")
    monkeypatch.delenv(_ROLLBACK_ENV, raising=False)
    response = _client().get("/api/omnigent/runtime-provider-migration")
    assert response.status_code == 200
    payload = response.json()
    assert payload["schemaVersion"] == (
        "moonmind.omnigent-runtime-provider-migration-status.v1"
    )
    assert payload["policyVersion"].startswith(
        "moonmind.omnigent-runtime-provider-rollout/"
    )
    rows = {row["targetId"]: row for row in payload["combinations"]}
    assert "codex.generic-omnigent" in rows
    assert "codex.direct" in rows
    codex = rows["codex.generic-omnigent"]
    assert codex["rolloutState"] == "new_work_default"
    assert codex["defaultStatus"] == "default_for_new_work"
    assert codex["hostClassRef"] == "omnigent-codex@1"
    assert codex["runtimePackRef"] == "codex-native-pack@1"
    assert codex["executionRealizerRef"] == "generic-omnigent-host@1"
    assert codex["rollbackAvailable"] is True
    assert rows["codex.direct"]["compatibilityPathStatus"] == (
        "active_compatibility"
    )


def test_route_reflects_active_rollback_controls(monkeypatch):
    monkeypatch.setenv(_CODEX_GATE, "true")
    monkeypatch.setenv(_ROLLBACK_ENV, "stop_new_generic_codex_admission")
    payload = _client().get("/api/omnigent/runtime-provider-migration").json()
    assert payload["activeRollbackControls"] == [
        "stop_new_generic_codex_admission"
    ]
    rows = {row["targetId"]: row for row in payload["combinations"]}
    assert "stop_new_generic_codex_admission" in (
        rows["codex.generic-omnigent"]["activeRollbackControls"]
    )
    assert rows["claude.generic-omnigent"]["activeRollbackControls"] == []


def test_route_never_leaks_credentials_or_launch_authority(monkeypatch):
    monkeypatch.setenv(_CODEX_GATE, "true")
    monkeypatch.delenv(_ROLLBACK_ENV, raising=False)
    body = _client().get("/api/omnigent/runtime-provider-migration").text
    for forbidden in (
        "token",
        "secret",
        "password",
        "apiKey",
        "providerSessionId",
        "/home/",
        "/var/run/docker",
        "@sha256:",
        "imageRef",
    ):
        assert forbidden not in body, forbidden
    # The payload is still a complete, parseable status document.
    assert json.loads(body)["combinations"]


def test_route_requires_settings_catalog_read(monkeypatch):
    monkeypatch.delenv(_ROLLBACK_ENV, raising=False)
    response = _client(superuser=False).get(
        "/api/omnigent/runtime-provider-migration"
    )
    assert response.status_code == 403
    assert "settings.catalog.read" in response.json()["detail"]


def test_invalid_rollout_configuration_is_reported_not_silently_ignored(
    monkeypatch,
):
    monkeypatch.setenv(
        "MOONMIND_OMNIGENT_RUNTIME_PROVIDER_ROLLOUT", "{not json"
    )
    with pytest.raises(ValueError, match="not valid JSON"):
        _client().get("/api/omnigent/runtime-provider-migration")
