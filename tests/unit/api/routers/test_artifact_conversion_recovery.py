"""Mounted artifact recovery preserves admission and retained accounting."""

from api_service.migrations.account_source import source_user_ids

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select

from api_service.api.routers import agent_runs, sessions, temporal_artifacts
from api_service.auth_providers import get_instance_access, get_instance_access_optional
from api_service.db.base import get_async_session
from api_service.db.models import (
    TemporalArtifact,
    TemporalArtifactRedactionLevel,
    TemporalArtifactUseClaim,
    WorkflowRun,
)
from api_service.services import single_user_conversion as conversion
from moonmind.config.settings import settings
from moonmind.schemas.managed_session_models import CodexManagedSessionRecord
from moonmind.workflows.temporal.artifacts import (
    LocalTemporalArtifactStore, TemporalArtifactRepository, TemporalArtifactService, TemporalArtifactValidationError,
)
from moonmind.workflows.temporal.runtime.managed_session_store import ManagedSessionStore
from tests.helpers.account_source import User
from tests.unit.services.test_artifact_conversion import database, seed, service

pytestmark = pytest.mark.asyncio
DEFAULT_OPERATOR = "00000000-0000-0000-0000-000000000000"


def mounted_app(session, tmp_path):
    app = FastAPI()
    app.include_router(temporal_artifacts.router)
    app.include_router(agent_runs.sessions_router, prefix="/api")
    app.include_router(agent_runs.router, prefix="/api")
    app.include_router(sessions.router, prefix="/api")
    app.dependency_overrides[get_async_session] = lambda: session
    app.dependency_overrides[temporal_artifacts._get_temporal_artifact_service] = (
        lambda: service(session, tmp_path)
    )
    return app


@pytest.mark.parametrize("creator", ["historical", "operator"])
@pytest.mark.parametrize("streamed", [False, True])
async def test_session_and_direct_recovery_share_trusted_operator_scope(
    tmp_path,
    monkeypatch,
    creator,
    streamed,
):
    monkeypatch.setenv("AUTH_PROVIDER", "disabled")
    monkeypatch.setenv("DEFAULT_USER_ID", str(DEFAULT_OPERATOR or ""))
    monkeypatch.setenv("MOONMIND_AGENT_RUNTIME_STORE", str(tmp_path / "runtime"))
    monkeypatch.setattr(settings.feature_flags, "session_api_compat_enabled", True)
    metadata_principals = set()
    read_metadata = TemporalArtifactService.get_metadata

    async def observe_metadata(self, *, artifact_id, principal, **kwargs):
        metadata_principals.add(principal)
        return await read_metadata(self, artifact_id=artifact_id, principal=principal, **kwargs)

    monkeypatch.setattr(TemporalArtifactService, "get_metadata", observe_metadata)
    async with database(tmp_path) as maker:
        async with maker() as session:
            original_id, historical_owner = await seed(session, tmp_path)
            owner = historical_owner if creator == "historical" else "operator"
            artifacts = service(session, tmp_path)
            rows = {}
            for label, principal, metadata in [
                ("main", owner, {}), ("quarantined", owner, {"quarantine": True}),
                ("machine", "service:session-producer", {}),
            ]:
                row, _ = await artifacts.create(principal=principal,
                    content_type="application/octet-stream",
                    redaction_level=TemporalArtifactRedactionLevel.RESTRICTED,
                    metadata_json=metadata)
                row = await artifacts.write_complete(artifact_id=row.artifact_id,
                    principal=principal, payload=f"session {label} bytes".encode(),
                    content_type="application/octet-stream")
                rows[label] = row.artifact_id
            assert (await conversion.run_guarded_upgrade(session, operator_authorized=True)).published
            main = await session.get(TemporalArtifact, rows["main"])
            assert main.operator_recovery_granted is (creator == "historical")
            quota = await TemporalArtifactRepository(session).quota_usage_for_scope(owner)
        record = CodexManagedSessionRecord(
            sessionId="sess:retained:codex_cli", sessionEpoch=1, agentRunId="retained-agent",
            containerId="retained-container", threadId="retained-thread", runtimeId="codex_cli",
            imageRef="moonmind:latest", controlUrl="docker-exec://retained-container",
            status="degraded", workspacePath="/retained/repo", sessionWorkspacePath="/retained/session",
            artifactSpoolPath="/retained/artifacts", latestSummaryRef=rows["main"],
            latestCheckpointRef=rows["quarantined"], diagnosticsRef=rows["machine"],
            startedAt=datetime.now(UTC), updatedAt=datetime.now(UTC))
        ManagedSessionStore(tmp_path / "runtime" / "managed_sessions").save(record)
        if streamed:
            def no_local_path(*_args, **_kwargs):
                raise TemporalArtifactValidationError("path unavailable")
            monkeypatch.setattr(LocalTemporalArtifactStore, "read_path", no_local_path)
        async with maker() as session:
            app = mounted_app(session, tmp_path)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                        base_url="http://localhost") as client:
                # The same admitted request must work through the direct
                # artifact path and the durable workflow UI session projection.
                response = await client.get(f"/api/artifacts/{rows['main']}/download")
                assert response.status_code == 200, response.text
                assert response.content == b"session main bytes"
                response = await client.get(f"/api/artifacts/{rows['main']}?include_download=true")
                assert response.status_code == 200, response.text
                assert response.json()["raw_access_allowed"] is True
                response = await client.post(f"/api/artifacts/{rows['main']}/presign-download")
                assert response.status_code == 200, response.text
                for action in ("content", "download"):
                    response = await client.get(
                        f"/api/sessions/{record.session_id}/resources/{rows['main']}/{action}")
                    assert response.status_code == 200, response.text
                    assert response.content == b"session main bytes"
                    assert response.headers["x-content-type-options"] == "nosniff"
                    assert "sandbox" in response.headers["content-security-policy"]
                    if action == "download":
                        assert response.headers["content-disposition"].startswith("attachment;")
                    for label, expected in [("quarantined", 409), ("machine", 403)]:
                        response = await client.get(
                            f"/api/sessions/{record.session_id}/resources/{rows[label]}/{action}")
                        assert response.status_code == expected, response.text
                response = await client.get(
                    f"/api/sessions/{record.session_id}/resources/{original_id}/content")
                assert response.status_code == 404, response.text
                response = await client.get(
                    f"/api/agent-runs/{record.agent_run_id}/artifact-sessions/{record.session_id}")
                assert response.status_code == 200, response.text
                projected = {item["artifact_id"]: item
                             for group in response.json()["grouped_artifacts"]
                             for item in group["artifacts"]}
                assert projected[rows["main"]]["raw_access_allowed"] is True
                assert projected[rows["quarantined"]]["raw_access_allowed"] is False
                assert projected[rows["machine"]]["raw_access_allowed"] is False
                snapshot = await client.get(f"/api/sessions/{record.session_id}")
                assert snapshot.status_code == 200, snapshot.text
                projected = {item["artifact_id"]: item
                             for group in snapshot.json()["artifactRefs"]["groupedArtifacts"]
                             for item in group["artifacts"]}
                assert projected[rows["main"]]["raw_access_allowed"] is True
                assert projected[rows["quarantined"]]["raw_access_allowed"] is False
                assert projected[rows["machine"]]["raw_access_allowed"] is False
                assert metadata_principals == {DEFAULT_OPERATOR}
                for headers in [
                    {"X-MoonMind-Execution-Fanout": "v1"},
                    {"X-MoonMind-Execution-Fanout": "v1", "X-MoonMind-Admitted-Principal": "operator"},
                ]:
                    response = await client.get(
                        f"/api/sessions/{record.session_id}/resources/{rows['main']}/content", headers=headers)
                    assert response.status_code == 401, response.text
            claims = (await session.execute(select(TemporalArtifactUseClaim))).scalars().all()
            assert {c.owner_principal for c in claims if c.operation_kind == "download"} == {DEFAULT_OPERATOR}
            assert {c.owner_principal for c in claims if c.request_id == "ongoing-recovery"} == {historical_owner}
            assert await TemporalArtifactRepository(session).quota_usage_for_scope(owner) == quota
            assert (await session.get(TemporalArtifact, rows["main"])).created_by_principal == owner
            assert (await session.get(TemporalArtifact, rows["main"])).operator_recovery_granted is (creator == "historical")
            assert UUID(DEFAULT_OPERATOR) not in await source_user_ids(session)
            # UUID identity and caller headers alone do not mint the private
            # request admission proof, for either literal or historical owners.
            untrusted = mounted_app(session, tmp_path)
            local_identity = User(id=UUID(DEFAULT_OPERATOR), email="operator@example.invalid")
            untrusted.dependency_overrides[get_instance_access] = lambda: local_identity
            untrusted.dependency_overrides[get_instance_access_optional] = lambda: local_identity
            untrusted.dependency_overrides[get_instance_access_optional] = lambda: local_identity
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=untrusted),
                                        base_url="http://localhost") as client:
                for path in [f"/api/artifacts/{rows['main']}/download",
                             f"/api/sessions/{record.session_id}/resources/{rows['main']}/content"]:
                    response = await client.get(path, headers={"X-MoonMind-Admitted-Principal": "operator"})
                    assert response.status_code == 401, response.text


@pytest.mark.parametrize("create_default_artifact", [False, True])
async def test_oidc_artifacts_created_after_conversion_recover_at_account_free_startup(
    tmp_path,
    monkeypatch,
    create_default_artifact,
):
    from api_service import main

    monkeypatch.setenv("AUTH_PROVIDER", "oidc")
    monkeypatch.setenv(
        "DEFAULT_USER_ID",
        str(DEFAULT_OPERATOR if create_default_artifact else None or ""),
    )
    async with database(tmp_path) as maker:
        @asynccontextmanager
        async def startup_session():
            async with maker() as session:
                yield session

        monkeypatch.setattr(main, "get_async_session_context", startup_session)
        async with maker() as session:
            original_id, owner = await seed(session, tmp_path)
            assert owner != DEFAULT_OPERATOR
        assert (await main._run_guarded_single_user_upgrade())["published"]

        # A real account-free phase records reserved-scope consumers without
        # a User row. These must survive a later OIDC -> disabled transition.
        monkeypatch.setenv("AUTH_PROVIDER", "disabled")
        async with maker() as local:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=mounted_app(local, tmp_path)),
                base_url="http://localhost",
            ) as client:
                response = await client.post(f"/api/artifacts/{original_id}/presign-download")
                assert response.status_code == 200, response.text
                if create_default_artifact:
                    response = await client.post("/api/artifacts", json={
                        "content_type": "application/octet-stream", "redaction_level": "restricted"})
                    assert response.status_code == 201, response.text
                    default_id = response.json()["artifact_ref"]["artifact_id"]
                    response = await client.put(f"/api/artifacts/{default_id}/content",
                        content=b"account-free saved bytes",
                        headers={"Content-Type": "application/octet-stream"})
                    assert response.status_code == 200, response.text
            assert UUID(DEFAULT_OPERATOR) not in await source_user_ids(local)
            claims_before = (await local.execute(select(TemporalArtifactUseClaim.__table__).where(
                TemporalArtifactUseClaim.owner_principal == DEFAULT_OPERATOR))).all()
            assert claims_before
            default_rows_before = (await local.execute(select(TemporalArtifact.__table__).where(
                TemporalArtifact.created_by_principal == DEFAULT_OPERATOR))).all()
            assert len(default_rows_before) == (2 if create_default_artifact else 0)
            quota_before = await TemporalArtifactRepository(local).quota_usage_for_scope(DEFAULT_OPERATOR)

        monkeypatch.setenv("AUTH_PROVIDER", "oidc")
        # Normal OIDC writes continue after the prerequisite first starts.
        # The old completion alone must not authorize these new resources.
        later = {}
        async with maker() as writer:
            artifacts = service(writer, tmp_path)
            for label, principal, metadata in [
                ("human", owner, {}),
                ("quarantined", owner, {"quarantine": True}),
                ("machine", "service:producer", {}),
            ]:
                row, _ = await artifacts.create(
                    principal=principal, content_type="application/octet-stream",
                    redaction_level=TemporalArtifactRedactionLevel.RESTRICTED,
                    metadata_json=metadata,
                )
                row = await artifacts.write_complete(
                    artifact_id=row.artifact_id, principal=principal,
                    payload=f"later {label} bytes".encode(),
                    content_type="application/octet-stream",
                )
                assert row.operator_recovery_granted is False
                later[label] = row.artifact_id

        monkeypatch.setenv("AUTH_PROVIDER", "disabled")
        async with maker() as before:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=mounted_app(before, tmp_path)),
                base_url="http://localhost",
            ) as client:
                response = await client.get(f"/api/artifacts/{later['human']}/download")
                assert response.status_code == 503, response.text

        collect = AsyncMock(wraps=conversion.collect_inventory)
        monkeypatch.setattr(conversion, "collect_inventory", collect)
        summary = await main._run_guarded_single_user_upgrade()
        assert summary["published"], summary

        async with maker() as restarted:
            assert (await restarted.execute(select(TemporalArtifactUseClaim.__table__).where(
                TemporalArtifactUseClaim.owner_principal == DEFAULT_OPERATOR))).all() == claims_before
            assert (await restarted.execute(select(TemporalArtifact.__table__).where(
                TemporalArtifact.created_by_principal == DEFAULT_OPERATOR))).all() == default_rows_before
            assert await TemporalArtifactRepository(restarted).quota_usage_for_scope(DEFAULT_OPERATOR) == quota_before
            assert UUID(DEFAULT_OPERATOR) not in await source_user_ids(restarted)
            if create_default_artifact:
                default = await restarted.get(TemporalArtifact, default_id)
                assert default.operator_recovery_granted is False
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=mounted_app(restarted, tmp_path)),
                base_url="http://localhost",
            ) as client:
                artifact_id = later["human"]
                response = await client.get(f"/api/artifacts/{artifact_id}/download")
                assert response.status_code == 200, response.text
                assert response.content == b"later human bytes"
                assert collect.await_count == 2
                if create_default_artifact:
                    response = await client.get(f"/api/artifacts/{default_id}/download")
                    assert response.status_code == 200, response.text
                    assert response.content == b"account-free saved bytes"
                response = await client.get(f"/api/artifacts/{artifact_id}?include_download=true")
                assert response.status_code == 200, response.text
                assert response.json()["raw_access_allowed"] is True
                assert response.json()["created_by_principal"] == owner
                response = await client.post(f"/api/artifacts/{artifact_id}/presign-download")
                assert response.status_code == 200, response.text
                for label, expected in [("quarantined", 409), ("machine", 403)]:
                    artifact_id = later[label]
                    response = await client.get(f"/api/artifacts/{artifact_id}/download")
                    assert response.status_code == expected, response.text
                    response = await client.post(f"/api/artifacts/{artifact_id}/presign-download")
                    assert response.status_code == expected, response.text
                    response = await client.get(f"/api/artifacts/{artifact_id}?include_download=true")
                    assert response.status_code == 200, response.text
                    assert response.json()["raw_access_allowed"] is False
                    assert response.json()["download_url"] is None
            for label in later:
                row = await restarted.get(TemporalArtifact, later[label])
                assert row.operator_recovery_granted is (label != "machine")
                assert row.created_by_principal == ("service:producer" if label == "machine" else owner)

        collect.reset_mock()
        assert (await main._run_guarded_single_user_upgrade())["published"]
        async with maker() as guarded:
            assert (await conversion.startup_guard_decision(guarded)).eligible
        collect.assert_not_awaited()


async def test_mounted_converted_historical_owner_recovers_without_changing_claim_scope(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("AUTH_PROVIDER", "disabled")
    monkeypatch.setenv("DEFAULT_USER_ID", str(DEFAULT_OPERATOR or ""))
    async with database(tmp_path) as maker:
        async with maker() as session:
            artifact_id, owner = await seed(session, tmp_path)
            assert owner != DEFAULT_OPERATOR
            assert (await conversion.run_guarded_upgrade(
                session, operator_authorized=True)).published
        # The request authenticates through the real optional-user boundary;
        # the historical creator is not the transient local operator UUID.
        async with maker() as session:
            app = mounted_app(session, tmp_path)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                        base_url="http://localhost") as client:
                response = await client.get(f"/api/artifacts/{artifact_id}/download")
                assert response.status_code == 200, response.text
                assert response.content == b"retained bytes"
                # Exercise the streaming fallback using the real service and
                # bytes; only the storage backend's path capability differs.
                def no_local_path(*_args, **_kwargs):
                    raise TemporalArtifactValidationError("path unavailable")

                with monkeypatch.context() as backend:
                    backend.setattr(LocalTemporalArtifactStore, "read_path", no_local_path)
                    response = await client.get(f"/api/artifacts/{artifact_id}/download")
                    assert response.status_code == 200, response.text
                    assert response.content == b"retained bytes"
                response = await client.get(f"/api/artifacts/{artifact_id}?include_download=true")
                assert response.status_code == 200, response.text
                assert response.json()["raw_access_allowed"] is True
                assert response.json()["created_by_principal"] == owner
                assert response.json()["download_url"].endswith("/download")
                response = await client.post(f"/api/artifacts/{artifact_id}/presign-download")
                assert response.status_code == 200, response.text
                # Mutation responses use the same read policy as GET, without
                # replacing the normal principal used by the mutation itself.
                response = await client.post(f"/api/artifacts/{artifact_id}/pin",
                    json={"reason": "operator retention"})
                assert response.status_code == 200, response.text
                assert response.json()["raw_access_allowed"] is True
                response = await client.delete(f"/api/artifacts/{artifact_id}/pin")
                assert response.status_code == 200, response.text
                assert response.json()["raw_access_allowed"] is True
                response = await client.post(f"/api/artifacts/{artifact_id}/links", json={
                    "namespace": "default", "workflow_id": "retained-workflow",
                    "run_id": "retained-run", "link_type": "output"})
                assert response.status_code == 200, response.text
                assert response.json()["raw_access_allowed"] is True
                # A caller's marker or purported admission headers cannot
                # substitute for successful operator admission, even loopback.
                for headers in [{"X-MoonMind-Execution-Fanout": "v1"},
                                {"X-MoonMind-Execution-Fanout": "v1",
                                 "X-MoonMind-Admitted-Principal": "operator",
                                 "Authorization": "Bearer forged"}]:
                    response = await client.get(
                        f"/api/artifacts/{artifact_id}/download", headers=headers)
                    assert response.status_code == 401, response.text
            claims = (await session.execute(select(TemporalArtifactUseClaim))).scalars().all()
            assert {c.owner_principal for c in claims if c.request_id == "ongoing-recovery"} == {owner}
            assert {c.owner_principal for c in claims if c.operation_kind == "download"} == {DEFAULT_OPERATOR}
            assert (await session.get(TemporalArtifact, artifact_id)).created_by_principal == owner
            assert UUID(DEFAULT_OPERATOR) not in await source_user_ids(session)
            row = await session.get(TemporalArtifact, artifact_id)
            row.metadata_json = {**row.metadata_json, "quarantine": True}
            await session.commit()
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                        base_url="http://localhost") as client:
                response = await client.get(f"/api/artifacts/{artifact_id}/download")
                assert response.status_code == 409, response.text
                response = await client.post(f"/api/artifacts/{artifact_id}/presign-download")
                assert response.status_code == 409, response.text
                response = await client.get(f"/api/artifacts/{artifact_id}?include_download=true")
                assert response.status_code == 200, response.text
                assert response.json()["raw_access_allowed"] is False
                assert response.json()["download_url"] is None


async def test_old_completion_cannot_expose_unresolved_history(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("AUTH_PROVIDER", "disabled")
    monkeypatch.setenv("DEFAULT_USER_ID", str(DEFAULT_OPERATOR or ""))
    async with database(tmp_path) as maker:
        async with maker() as session:
            artifact_id, owner = await seed(session, tmp_path)
            session.add(WorkflowRun(feature_key="retained", requested_by_user_id=UUID(int=999)))
            session.add(conversion.SingleUserConversionRun(preflight_digest="previously-published",
                status="complete", result_json={"published": True,
                    "decision": {"disposition": "eligible_conversion"}}))
            await session.commit()
            result = await conversion.run_guarded_upgrade(session, operator_authorized=True)
            assert not result.published
            assert result.decision.reason_code == "unknown_principal"
            assert not (await session.get(TemporalArtifact, artifact_id)).operator_recovery_granted
            app = mounted_app(session, tmp_path)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                        base_url="http://localhost") as client:
                response = await client.get("/api/artifacts/collection")
                assert response.status_code == 503, response.text
                response = await client.get(f"/api/artifacts/{artifact_id}")
                assert response.status_code == 503, response.text
                assert response.json()["detail"] == "conversion_required"
                response = await client.get(f"/api/artifacts/{artifact_id}/download",
                    headers={"X-MoonMind-Admitted-Principal": "operator"})
                assert response.status_code == 503, response.text


async def test_default_uuid_without_server_admission_cannot_use_conversion_grant(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("AUTH_PROVIDER", "oidc")
    async with database(tmp_path) as maker:
        async with maker() as session:
            artifact_id, _owner = await seed(session, tmp_path)
            assert (await conversion.run_guarded_upgrade(session, operator_authorized=True)).published
            app = mounted_app(session, tmp_path)
            # This simulates an authenticated UUID, not disabled-mode admission.
            # The actual artifact principal and recovery dependencies stay intact.
            authenticated = User(
                id=UUID(DEFAULT_OPERATOR), email="other@example.invalid", is_active=True)
            app.dependency_overrides[get_instance_access_optional] = lambda: authenticated
            app.dependency_overrides[get_instance_access_optional] = lambda: authenticated
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                        base_url="http://localhost") as client:
                response = await client.get(f"/api/artifacts/{artifact_id}/download",
                    headers={"X-MoonMind-Admitted-Principal": "operator"})
                assert response.status_code == 401, response.text
                response = await client.get(f"/api/artifacts/{artifact_id}")
                assert response.status_code == 401
                assert response.json()["detail"]["code"] == "auth_required"
