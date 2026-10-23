"""Release probes authenticate through the deployed dashboard's real operator admission gate."""

from __future__ import annotations

import asyncio
import json
import socket

import pytest
import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from api_service.db import base as db_base
from api_service.db.models import Base
from api_service.services.single_user_conversion import run_guarded_upgrade
from moonmind.workflows.skills.deployment_surface import verify_surface
from tests.support.isolated_postgres import isolated_postgres
from tests.integration import test_single_user_conversion_postgres_4346 as source_pg

conversion_postgres_url = source_pg.conversion_postgres_url

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.integration,
    pytest.mark.reliability_journey,
]


@pytest.mark.parametrize(
    "auth_selector", [None, "", "accounts"], ids=["omitted", "empty", "retired"]
)
async def test_ingress_qualifies_protected_operator_surface(
    tmp_path, monkeypatch, auth_selector, conversion_postgres_url
):
    monkeypatch.setenv("MOONMIND_TEST_POSTGRES_URL", conversion_postgres_url)
    if auth_selector is None:
        monkeypatch.delenv("AUTH_PROVIDER", raising=False)
    else:
        monkeypatch.setenv("AUTH_PROVIDER", auth_selector)
    monkeypatch.setenv("MOONMIND_PUBLIC_BASE_URL", "https://operator.example")
    monkeypatch.setenv("MOONMIND_API_PUBLISH_HOST", "127.0.0.1")
    monkeypatch.setenv("MOONMIND_TRUSTED_INGRESS", "1")
    monkeypatch.setenv("MOONMIND_TRUSTED_PROXIES", "127.0.0.1")
    monkeypatch.delenv("MOONMIND_UI_DEV_SERVER_URL", raising=False)
    dist = tmp_path / "dist"
    (dist / ".vite").mkdir(parents=True)
    (dist / "assets").mkdir()
    (dist / "assets" / "dashboard.js").write_text("console.log('qualified build');")
    manifest = dist / ".vite" / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "entrypoints/dashboard.tsx": {
                    "file": "assets/dashboard.js",
                    "isEntry": True,
                },
            }
        )
    )
    monkeypatch.setenv("VITE_MANIFEST_PATH", str(manifest))

    from api_service.api.routers.workflow_console import router

    app = FastAPI()
    app.include_router(router)
    app.mount("/static/workflow_console/dist", StaticFiles(directory=dist))
    observations = []

    @app.middleware("http")
    async def trusted_ingress(request, call_next):
        # External ingress authentication is a fixture. The backend still uses
        # its real transport, Host, conversion and operator admission boundary.
        admitted = request.cookies.get("ingress_test_session") == "admitted"
        headers = [(key, value) for key, value in request.scope["headers"]
                   if key.lower() not in {b"host", b"x-moonmind-user"}]
        headers.append((b"host", b"operator.example"))
        if admitted:
            headers.append((b"x-moonmind-user", b"ingress-operator"))
        request.scope["headers"] = headers
        return await call_next(request)

    @app.middleware("http")
    async def observe(request, call_next):
        response = await call_next(request)
        observations.append((request.url.path, response.status_code))
        return response

    @app.get("/healthz")
    async def healthz():
        return {"status": "ok"}

    async with isolated_postgres(list(Base.metadata.sorted_tables)) as sessions:
        monkeypatch.setattr(db_base, "async_session_maker", sessions)
        async with sessions() as session:
            assert (
                await run_guarded_upgrade(session, operator_authorized=True)
            ).published
        authorized_headers = [{"Cookie": "ingress_test_session=admitted"}]
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        listener.setblocking(False)
        base_url = f"http://127.0.0.1:{listener.getsockname()[1]}"
        server = uvicorn.Server(uvicorn.Config(app, lifespan="off", log_level="error"))
        task = asyncio.create_task(server.serve(sockets=[listener]))
        try:
            async with asyncio.timeout(10):
                while not server.started:
                    await asyncio.sleep(0.01)
            for denied in (
                {},
                {
                    "Cookie": "mm_session_dev=retired-session",
                },
                {"Authorization": "Bearer invalid-session"},
            ):
                observations.clear()
                with pytest.raises(RuntimeError, match="HTTP 401"):
                    await asyncio.to_thread(verify_surface, base_url, headers=denied)
                assert ("/workflows", 401) in observations
                assert ("/api/ui/info", 200) not in observations

            for headers in authorized_headers:
                observations.clear()
                receipt = await asyncio.to_thread(
                    verify_surface, base_url, headers=headers
                )
                assert receipt["status"] == "verified"
                assert ("/workflows", 200) in observations
                assert ("/api/ui/info", 200) in observations
                assert any(
                    "/assets/" in path and status == 200
                    for path, status in observations
                )
                assert "Cookie" not in json.dumps(receipt)
                assert "Authorization" not in json.dumps(receipt)

            # A fresh request rechecks the installed ingress allowlist.
            monkeypatch.setenv("MOONMIND_TRUSTED_PROXIES", "192.0.2.10")
            for headers in authorized_headers:
                with pytest.raises(RuntimeError, match="HTTP 401"):
                    await asyncio.to_thread(verify_surface, base_url, headers=headers)
        finally:
            server.should_exit = True
            await asyncio.wait_for(task, timeout=10)
            listener.close()
