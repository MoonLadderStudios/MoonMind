"""Release probes authenticate through the deployed dashboard's real session gate."""

from __future__ import annotations

import asyncio
import json
import secrets
import socket
from uuid import uuid4

import pytest
import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from api_service.auth_providers import build_moonmind_control_plane_config
from api_service.db import base as db_base
from api_service.db.models import MoonmindSession, MoonmindUserSessionGeneration, User
from api_service.services.session_store import (
    DbAccountStore,
    DbRevocationStore,
    mint_and_record_session,
)
from moonmind.config.settings import settings
from moonmind.security import auth_modes_4120 as auth_modes
from moonmind.security.omnigent_auth_qualification import ValidatedIdentity
from moonmind.workflows.skills.deployment_surface import verify_surface
from tests.support.isolated_postgres import isolated_postgres

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.integration,
    pytest.mark.reliability_journey,
]


@pytest.mark.parametrize("secure_cookie", [False, True])
@pytest.mark.parametrize(
    "auth_selector", [None, "", "accounts"], ids=["omitted", "empty", "explicit"]
)
async def test_accounts_session_qualifies_protected_operator_surface(
    tmp_path, monkeypatch, secure_cookie, auth_selector
):
    # Supply deployment configuration and a disposable DB through the normal
    # substrate. No principal dependency or session validator is overridden.
    monkeypatch.setattr(auth_modes, "_ACTIVE_PRODUCTION_MODE", "accounts")
    monkeypatch.setattr(settings.oidc, "AUTH_PROVIDER", auth_selector or "")
    if auth_selector is None:
        monkeypatch.delenv("AUTH_PROVIDER", raising=False)
    else:
        monkeypatch.setenv("AUTH_PROVIDER", auth_selector)
    monkeypatch.setenv("MOONMIND_SESSION_SECRET", secrets.token_hex(32))
    if secure_cookie:
        monkeypatch.delenv("MOONMIND_REQUIRE_SECURE_COOKIES", raising=False)
    else:
        monkeypatch.setenv("MOONMIND_REQUIRE_SECURE_COOKIES", "0")
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
    async def observe(request, call_next):
        response = await call_next(request)
        observations.append((request.url.path, response.status_code))
        return response

    @app.get("/healthz")
    async def healthz():
        return {"status": "ok"}

    tables = [
        User.__table__,
        MoonmindUserSessionGeneration.__table__,
        MoonmindSession.__table__,
    ]
    async with isolated_postgres(tables) as sessions:
        monkeypatch.setattr(db_base, "async_session_maker", sessions)
        config = build_moonmind_control_plane_config()
        assert config.mode == "accounts"
        async with sessions() as session:
            user = User(
                id=uuid4(),
                email="release-operator@example.invalid",
                hashed_password=None,
                is_active=True,
                is_superuser=False,
                is_verified=True,
            )
            session.add(user)
            await session.commit()
            session_token, _ = await mint_and_record_session(
                ValidatedIdentity(issuer="moonmind-accounts", subject=user.email),
                DbAccountStore(session),
                DbRevocationStore(session),
                config,
                session,
            )
        authorized_headers = [
            {"Cookie": f"{config.cookie_name}={session_token}"},
            {"Authorization": f"Bearer {session_token}"},
        ]
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
                None,
                {"Cookie": f"{config.cookie_name}=invalid-session"},
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
                assert session_token not in json.dumps(receipt)

            # Revocation remains authoritative even for a previously qualified
            # origin and a cryptographically valid cookie.
            async with sessions() as session:
                await DbRevocationStore(session).revoke_all_for_user(user.id)
                await session.commit()
            for headers in authorized_headers:
                with pytest.raises(RuntimeError, match="HTTP 401"):
                    await asyncio.to_thread(verify_surface, base_url, headers=headers)
        finally:
            server.should_exit = True
            await asyncio.wait_for(task, timeout=10)
            listener.close()
