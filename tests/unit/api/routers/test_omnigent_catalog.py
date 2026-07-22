"""MoonLadderStudios/MoonMind#3451 catalog boundary coverage."""

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_service.api.routers import omnigent_catalog as catalog
from api_service.auth_providers import get_current_user
from api_service.db.base import get_async_session


class _Scalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return _Scalars(self._rows)


class _Session:
    def __init__(self, profiles, bindings=()):
        self._results = iter((_Result(profiles), _Result(bindings)))

    async def execute(self, _statement):
        return next(self._results)


def _profile(**overrides):
    values = {
        "profile_id": "codex-oauth",
        "account_label": "OpenAI subscription",
        "provider_label": "OpenAI",
        "provider_id": "openai",
        "credential_source": SimpleNamespace(value="oauth_volume"),
        "runtime_materialization_mode": SimpleNamespace(value="oauth_home"),
        "rate_limit_policy": SimpleNamespace(value="queue"),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _config(*, enabled=True):
    return SimpleNamespace(
        enabled=enabled,
        host_protocol_mode="upstream_omnigent_server_proxy",
        readiness=lambda **_kwargs: {
            "conformanceState": "ready" if enabled else "disabled"
        },
    )


def _app(monkeypatch, *, session, enabled=True, launch_ready=True):
    monkeypatch.setattr(catalog, "get_bridge_config", lambda: _config(enabled=enabled))
    monkeypatch.setattr(catalog, "_secret_ref_results_for_rows", lambda rows: {r.profile_id: {} for r in rows})

    async def statuses(*_args, **_kwargs):
        return {}

    monkeypatch.setattr(catalog, "_managed_secret_statuses_for_rows", statuses)
    monkeypatch.setattr(
        catalog,
        "_provider_profile_readiness",
        lambda *_args, **_kwargs: {"launch_ready": launch_ready},
    )
    monkeypatch.setattr(
        catalog,
        "resolve_container_backend_settings",
        lambda: SimpleNamespace(enabled=True),
    )
    monkeypatch.setenv("OMNIGENT_IMAGE_REF", "registry.test/server@sha256:" + "1" * 64)
    monkeypatch.setenv("OMNIGENT_HOST_IMAGE_REF", "registry.test/host@sha256:" + "2" * 64)
    app = FastAPI()
    app.include_router(catalog.router)
    app.dependency_overrides[get_current_user()] = lambda: SimpleNamespace(
        id=None, is_superuser=True
    )
    app.dependency_overrides[get_async_session] = lambda: session
    return app


def test_ready_catalog_lists_only_launch_ready_codex_oauth_profiles(monkeypatch):
    profiles = [
        _profile(),
        _profile(profile_id="api-key", credential_source=SimpleNamespace(value="secret_ref")),
    ]
    client = TestClient(_app(monkeypatch, session=_Session(profiles)))

    response = client.get("/api/omnigent/codex-catalog-readiness")

    assert response.status_code == 200
    body = response.json()
    assert body["schemaVersion"] == "moonmind.omnigent-codex-readiness.v1"
    assert body["available"] is True
    assert body["hostModes"] == ["on_demand_docker"]
    assert body["eligibleProviderProfiles"] == [{
        "profileId": "codex-oauth",
        "label": "OpenAI subscription",
        "providerId": "openai",
        "busy": False,
        "queueWhenBusy": True,
    }]


def test_catalog_returns_actionable_bounded_redacted_gates(monkeypatch):
    secret = "github_pat_SHOULD_NOT_ESCAPE"
    profile = _profile(account_label=secret)
    client = TestClient(_app(
        monkeypatch, session=_Session([profile]), enabled=False, launch_ready=True
    ))

    response = client.get("/api/omnigent/codex-catalog-readiness")

    body = response.json()
    assert body["available"] is False
    assert {reason["code"] for reason in body["gateReasons"]} >= {
        "bridge_disabled"
    }
    assert all(reason["message"] and reason["remediationHref"] for reason in body["gateReasons"])
    assert secret not in response.text
    for forbidden in ("volume", "hostId", "docker.sock", "token=", "header", "environment"):
        assert forbidden not in response.text


def test_catalog_requires_authentication():
    app = FastAPI()
    app.include_router(catalog.router)
    response = TestClient(app).get("/api/omnigent/codex-catalog-readiness")
    assert response.status_code in {401, 403}
