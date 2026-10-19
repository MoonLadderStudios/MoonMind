"""Real trusted-header identity composes with optional artifact admission."""

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import Depends, FastAPI, Request

from api_service import auth_providers
from api_service.api.routers.worker_auth import _require_worker_auth
from api_service.db import base as db_base
from api_service.db.models import (
    TemporalArtifact,
    TemporalArtifactRedactionLevel,
    User,
    UserExternalIdentity,
)
from api_service.services import single_user_conversion as conversion
from moonmind.config.settings import settings
from moonmind.schemas.managed_session_models import CodexManagedSessionRecord
from moonmind.security import auth_modes_4120
from moonmind.workflows.temporal.runtime.managed_session_store import (
    ManagedSessionStore,
)
from tests.unit.api.routers.test_artifact_conversion_recovery import mounted_app
from tests.unit.services.test_artifact_conversion import database, seed, service


@pytest.mark.asyncio
@pytest.mark.parametrize("configured_header", [None, "X-Verified-Subject", "   "])
async def test_trusted_header_artifact_and_session_admission(
    tmp_path, monkeypatch, configured_header
):
    monkeypatch.setattr(auth_modes_4120, "_ACTIVE_PRODUCTION_MODE", "header")
    monkeypatch.setattr(settings.feature_flags, "session_api_compat_enabled", True)
    monkeypatch.setenv("MOONMIND_AGENT_RUNTIME_STORE", str(tmp_path / "runtime"))
    monkeypatch.setenv("MOONMIND_TRUSTED_INGRESS", "1")
    monkeypatch.setenv("MOONMIND_TRUSTED_PROXIES", "10.20.0.5")
    monkeypatch.setenv("MOONMIND_TRUSTED_PROXY_NAMESPACE", "artifact-tests")
    monkeypatch.setenv(
        "MOONMIND_SESSION_SECRET", "header-admission-test-secret-material-" * 3
    )
    monkeypatch.delenv("MOONMIND_TRUSTED_PROXY_ALLOW_UNKNOWN_USERS", raising=False)
    if configured_header is None:
        monkeypatch.delenv("MOONMIND_TRUSTED_PROXY_HEADER", raising=False)
    else:
        monkeypatch.setenv("MOONMIND_TRUSTED_PROXY_HEADER", configured_header)
    header = (configured_header or "").strip() or "X-MoonMind-Proxy-User"
    async with database(tmp_path) as maker:
        async with maker() as session:
            artifact_id, owner = await seed(session, tmp_path)
            session.add(
                UserExternalIdentity(
                    user_id=UUID(owner),
                    issuer="proxy:artifact-tests",
                    subject="retained-owner",
                )
            )
            await session.commit()
            assert (
                await conversion.run_guarded_upgrade(session, operator_authorized=True)
            ).published
            other = User(id=uuid4(), email="other@example.invalid")
            session.add(other)
            await session.flush()
            session.add(
                UserExternalIdentity(
                    user_id=other.id,
                    issuer="proxy:artifact-tests",
                    subject="other-owner",
                )
            )
            artifacts = service(session, tmp_path)
            literal, _ = await artifacts.create(
                principal="operator",
                content_type="application/octet-stream",
                redaction_level=TemporalArtifactRedactionLevel.RESTRICTED,
            )
            await artifacts.write_complete(
                artifact_id=literal.artifact_id,
                principal="operator",
                payload=b"literal operator bytes",
                content_type="application/octet-stream",
            )
            literal_id = literal.artifact_id
            await session.commit()
        record = CodexManagedSessionRecord(
            sessionId="sess:header:codex_cli",
            sessionEpoch=1,
            agentRunId="header-agent",
            containerId="header-container",
            threadId="header-thread",
            runtimeId="codex_cli",
            imageRef="moonmind:latest",
            controlUrl="docker-exec://header-container",
            status="degraded",
            workspacePath="/retained/repo",
            sessionWorkspacePath="/retained/session",
            artifactSpoolPath="/retained/artifacts",
            latestSummaryRef=artifact_id,
            startedAt=datetime.now(UTC),
            updatedAt=datetime.now(UTC),
        )
        ManagedSessionStore(tmp_path / "runtime" / "managed_sessions").save(record)

        @asynccontextmanager
        async def identity_session():
            async with maker() as identity_db:
                yield identity_db

        monkeypatch.setattr(db_base, "get_async_session_context", identity_session)
        async with maker() as session:
            app = mounted_app(session, tmp_path)
            # These routers were imported during test collection. Bind the same
            # real dependency production selects when registering in header
            # mode; neither the optional resolver nor any returned User is fake.
            header_dependency = auth_providers.get_current_user()
            assert header_dependency.__name__ == "_header_dependency"
            app.dependency_overrides[auth_providers._strict_current_user] = (
                header_dependency
            )

            @app.get("/test/strict-admission")
            async def strict_admission(
                request: Request, user=Depends(header_dependency)
            ):
                return {
                    "id": str(user.id),
                    "recovery": auth_providers.get_operator_recovery_principal(request),
                }

            @app.get("/test/optional-admission")
            async def optional_admission(
                user=Depends(auth_providers.get_current_user_optional()),
            ):
                return {"id": str(user.id) if user else None}

            @app.get("/test/worker-admission")
            async def worker_admission(_auth=Depends(_require_worker_auth)):
                return {"admitted": True}

            transport = httpx.ASGITransport(app=app, client=("10.20.0.5", 1234))
            async with httpx.AsyncClient(
                transport=transport, base_url="http://moonmind.test"
            ) as client:
                headers = {header: "retained-owner"}
                response = await client.get("/test/strict-admission", headers=headers)
                assert response.status_code == 200, response.text
                assert response.json() == {"id": owner, "recovery": None}
                response = await client.get(
                    f"/api/artifacts/{artifact_id}/download", headers=headers
                )
                assert response.status_code == 200, response.text
                assert response.content == b"retained bytes"
                for action in ("content", "download"):
                    response = await client.get(
                        f"/api/sessions/{record.session_id}/resources/{artifact_id}/{action}",
                        headers=headers,
                    )
                    assert response.status_code == 200, response.text
                    assert response.content == b"retained bytes"
                response = await client.get(
                    f"/api/sessions/{record.session_id}", headers=headers
                )
                assert response.status_code == 200, response.text
                groups = response.json()["artifactRefs"]["groupedArtifacts"]
                assert any(
                    a["artifact_id"] == artifact_id and a["raw_access_allowed"]
                    for group in groups
                    for a in group["artifacts"]
                )
                response = await client.get(
                    f"/api/artifacts/{artifact_id}?include_download=true",
                    headers=headers,
                )
                assert response.json()["raw_access_allowed"] is True
                response = await client.post(
                    f"/api/artifacts/{artifact_id}/presign-download", headers=headers
                )
                assert response.status_code == 200, response.text
                # Header admission does not mint the disabled operator grant,
                # even for a converted artifact or a literal operator creator.
                for ref, asserted in [
                    (artifact_id, "other-owner"),
                    (literal_id, "retained-owner"),
                ]:
                    response = await client.get(
                        f"/api/artifacts/{ref}/download",
                        headers={
                            header: asserted,
                            "X-MoonMind-Admitted-Principal": "operator",
                        },
                    )
                    assert response.status_code == 403, response.text
                for bad, expected in [
                    (None, 401),
                    ("", 401),
                    ("unknown", 403),
                    ("local", 401),
                ]:
                    response = await client.get(
                        f"/api/artifacts/{artifact_id}/download",
                        headers={} if bad is None else {header: bad},
                    )
                    assert response.status_code == expected, response.text
                for extra in [
                    {"X-MoonMind-Execution-Fanout": "v1"},
                    {
                        "X-MoonMind-Execution-Fanout": "v1",
                        "X-MoonMind-Admitted-Principal": "operator",
                    },
                ]:
                    response = await client.get(
                        f"/api/sessions/{record.session_id}/resources/{artifact_id}/content",
                        headers={**headers, **extra},
                    )
                    assert response.status_code == 401, response.text
                response = await client.get("/test/optional-admission")
                assert response.json() == {"id": None}
                response = await client.get(
                    "/test/worker-admission",
                    headers={"X-MoonMind-Worker-Token": "retired-token"},
                )
                assert response.status_code == 410, response.text
                response = await client.get(
                    "/test/optional-admission",
                    headers={"Authorization": "Bearer invalid"},
                )
                assert response.status_code == 401, response.text
                row = await session.get(TemporalArtifact, artifact_id)
                row.metadata_json = {**row.metadata_json, "quarantine": True}
                await session.commit()
                response = await client.get(
                    f"/api/artifacts/{artifact_id}/download", headers=headers
                )
                assert response.status_code == 409, response.text
                account = await session.get(User, UUID(owner))
                account.is_active = False
                await session.commit()
                response = await client.get(
                    f"/api/artifacts/{artifact_id}", headers=headers
                )
                assert response.status_code == 403, response.text
                assert response.json()["detail"]["code"] == "inactive"
            untrusted = httpx.ASGITransport(app=app, client=("198.51.100.4", 1234))
            async with httpx.AsyncClient(
                transport=untrusted, base_url="http://moonmind.test"
            ) as client:
                response = await client.get(
                    f"/api/artifacts/{artifact_id}", headers={header: "retained-owner"}
                )
                assert response.status_code == 401, response.text
                assert response.json()["detail"]["code"] == "untrusted_ingress"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["accounts", "oidc"])
async def test_proxy_assertion_does_not_select_another_auth_mode(
    tmp_path, monkeypatch, mode
):
    monkeypatch.setattr(auth_modes_4120, "_ACTIVE_PRODUCTION_MODE", mode)
    monkeypatch.setenv(
        "MOONMIND_SESSION_SECRET", "header-mode-isolation-test-secret-" * 3
    )
    async with database(tmp_path) as maker, maker() as session:
        app = FastAPI()

        async def get_session():
            yield session

        app.dependency_overrides[db_base.get_async_session] = get_session

        @app.get("/optional")
        async def optional(
            request: Request, user=Depends(auth_providers.get_current_user_optional())
        ):
            return {
                "id": str(user.id) if user else None,
                "recovery": auth_providers.get_operator_recovery_principal(request),
            }

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://moonmind.test"
        ) as client:
            headers = {"X-MoonMind-Proxy-User": "retained-owner"}
            response = await client.get("/optional", headers=headers)
            assert response.status_code == 200, response.text
            assert response.json() == {"id": None, "recovery": None}
            response = await client.get(
                "/optional", headers={**headers, "Authorization": "Bearer invalid"}
            )
            assert response.status_code == 401, response.text
