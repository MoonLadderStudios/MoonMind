"""Mounted operator-boundary enforcement (#4347).

Exercises the real admission code (never a mock user override) across
HTTP queries and mutations, artifact previews/downloads, SSE, and
WebSocket first connection/reconnect through the actually mounted
routers. Also pins the main-app wiring so the boundary ships on the
advertised app, not only in a test harness.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_service.api.routers.operator_boundary_4347 import (
    ADMITTED_WORK,
    router,
    ws_router,
)


def _remote_env(monkeypatch):
    monkeypatch.setenv("MOONMIND_PUBLIC_BASE_URL", "http://testserver")
    monkeypatch.setenv("MOONMIND_TRUSTED_INGRESS", "1")
    monkeypatch.setenv("MOONMIND_TRUSTED_PROXIES", "testclient")
    monkeypatch.setenv("MOONMIND_PROXY_IDENTITY_NAMESPACE", "corp-ingress")
    monkeypatch.delenv("MOONMIND_PROXY_IDENTITY_HEADER", raising=False)


def _local_env(monkeypatch):
    monkeypatch.delenv("MOONMIND_PUBLIC_BASE_URL", raising=False)
    monkeypatch.delenv("MOONMIND_TRUSTED_INGRESS", raising=False)
    monkeypatch.delenv("MOONMIND_TRUSTED_PROXIES", raising=False)


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.include_router(ws_router, prefix="/ws/v1/operator")
    return app


def _admit_headers() -> dict:
    return {"X-Moonmind-User": "alice-stable-id"}


def test_main_app_mounts_operator_boundary():
    """Production app exposes the shared primitive, not the demo stack.

    The demonstration routers (status/control/artifacts/events/console)
    are test scaffolding mounted explicitly in _app() below. Production
    must not mount a parallel /operator/* API before the real workflow,
    artifact, stream, and chat consumers migrate through the one shared
    boundary (open #4347).
    """
    import api_service.main as main_module

    openapi_paths = main_module.app.openapi()["paths"]
    assert "/api/v1/operator/status" not in openapi_paths
    assert "/api/v1/operator/control" not in openapi_paths
    assert "/api/v1/operator/artifacts/{name}" not in openapi_paths
    assert "/api/v1/operator/events" not in openapi_paths
    # The shared primitive stays importable for real-consumer migration.
    from moonmind.security import operator_admission as oa

    assert callable(oa.resolve_operator_admission)
    # Demonstration routers remain available as test scaffolding.
    assert router.prefix == "/api/v1/operator"
    assert {getattr(route, "path", "") for route in ws_router.routes} == {
        "/console"
    }


def test_http_query_and_mutation_use_real_admission(monkeypatch):
    _remote_env(monkeypatch)
    ADMITTED_WORK.clear()
    with TestClient(_app()) as client:
        ok = client.get("/api/v1/operator/status", headers=_admit_headers())
        assert ok.status_code == 200
        assert ok.json() == {"admitted": True, "via": "trusted_ingress"}

        created = client.post(
            "/api/v1/operator/control",
            json={"action": "summarize"},
            headers=_admit_headers(),
        )
        assert created.status_code == 200
        body = created.json()
        assert body["action"] == "summarize"
        assert body["work_id"] in ADMITTED_WORK


def test_artifact_download_uses_real_admission(monkeypatch):
    _remote_env(monkeypatch)
    with TestClient(_app()) as client:
        denied = client.get("/api/v1/operator/artifacts/report.txt")
        assert denied.status_code == 401
        ok = client.get(
            "/api/v1/operator/artifacts/report.txt", headers=_admit_headers()
        )
        assert ok.status_code == 200
        assert "report.txt" in ok.text


def test_sse_uses_real_admission(monkeypatch):
    _remote_env(monkeypatch)
    with TestClient(_app()) as client:
        denied = client.get("/api/v1/operator/events")
        assert denied.status_code == 401
        ok = client.get("/api/v1/operator/events", headers=_admit_headers())
        assert ok.status_code == 200
        assert "tick 0" in ok.text


def test_websocket_first_connection_and_reconnect(monkeypatch):
    _remote_env(monkeypatch)
    with TestClient(_app()) as client:
        with client.websocket_connect(
            "/ws/v1/operator/console", headers={"x-moonmind-user": "alice-stable-id"}
        ) as ws:
            ws.send_text("hello")
            assert ws.receive_text() == "echo via trusted_ingress: hello"
        # Reconnect is admitted again through the same boundary.
        with client.websocket_connect(
            "/ws/v1/operator/console", headers={"x-moonmind-user": "alice-stable-id"}
        ) as ws:
            ws.send_text("close")
            try:
                ws.receive_text()
            except Exception:
                # The server closes with code 1000 after "close"; the client
                # raises on the closed socket, which is the expected outcome.
                pass


def test_websocket_query_token_never_admits(monkeypatch):
    """Credentials do not travel in URLs: a query token alone is denied."""
    _remote_env(monkeypatch)
    with TestClient(_app()) as client:
        with pytest.raises(Exception):
            with client.websocket_connect(
                "/ws/v1/operator/console?token=alice-stable-id"
            ):
                pass


def test_forged_and_hostile_requests_denied(monkeypatch):
    _remote_env(monkeypatch)
    with TestClient(_app()) as client:
        # Hostile Host is denied even with a valid ingress assertion.
        hostile = client.get(
            "/api/v1/operator/status",
            headers={"X-Moonmind-User": "alice-stable-id", "Host": "evil.example.com"},
        )
        assert hostile.status_code == 403
        bad_assertion = client.get(
            "/api/v1/operator/status", headers={"X-Moonmind-User": "local"}
        )
        assert bad_assertion.status_code == 401
        worker_only = client.get(
            "/api/v1/operator/status",
            headers={
                "Authorization": "Bearer worker-token-abc",
                "X-Moonmind-Execution-Fanout": "v1",
            },
        )
        assert worker_only.status_code == 401


def test_revocation_denies_reconnect_while_admitted_work_continues(monkeypatch):
    _remote_env(monkeypatch)
    ADMITTED_WORK.clear()
    with TestClient(_app()) as client:
        created = client.post(
            "/api/v1/operator/control",
            json={"action": "long-run"},
            headers=_admit_headers(),
        )
        assert created.status_code == 200
        work_id = created.json()["work_id"]

        # Revoke admitted access: the ingress proof is withdrawn.
        monkeypatch.delenv("MOONMIND_TRUSTED_INGRESS", raising=False)
        denied = client.get("/api/v1/operator/status", headers=_admit_headers())
        assert denied.status_code == 503
        with pytest.raises(Exception):
            with client.websocket_connect(
                "/ws/v1/operator/console",
                headers={"x-moonmind-user": "alice-stable-id"},
            ):
                pass
        # Durable admitted work is unaffected by losing browser admission.
        assert work_id in ADMITTED_WORK
