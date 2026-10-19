"""Mounted artifact recovery preserves admission and retained accounting."""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select

from api_service.api.routers import temporal_artifacts
from api_service.auth_providers import get_current_user_optional
from api_service.db.base import get_async_session
from api_service.db.models import (
    TemporalArtifact, TemporalArtifactRedactionLevel, TemporalArtifactUseClaim,
    User, WorkflowRun,
)
from api_service.services import single_user_conversion as conversion
from moonmind.config.settings import settings
from moonmind.security import auth_modes_4120
from moonmind.workflows.temporal.artifacts import (
    LocalTemporalArtifactStore, TemporalArtifactRepository, TemporalArtifactValidationError,
)
from tests.unit.services.test_artifact_conversion import database, seed, service

pytestmark = pytest.mark.asyncio
DEFAULT_OPERATOR = "00000000-0000-0000-0000-000000000000"


def mounted_app(session, tmp_path):
    app = FastAPI()
    app.include_router(temporal_artifacts.router)
    app.dependency_overrides[get_async_session] = lambda: session
    app.dependency_overrides[temporal_artifacts._get_temporal_artifact_service] = (
        lambda: service(session, tmp_path)
    )
    return app


@pytest.mark.parametrize("create_default_artifact", [False, True])
async def test_oidc_artifacts_created_after_conversion_recover_at_account_free_startup(
    tmp_path, monkeypatch, create_default_artifact,
):
    from api_service import main

    monkeypatch.setattr(auth_modes_4120, "_ACTIVE_PRODUCTION_MODE", "oidc")
    monkeypatch.setattr(settings.oidc, "DEFAULT_USER_ID", DEFAULT_OPERATOR if create_default_artifact else None)
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
        monkeypatch.setattr(auth_modes_4120, "_ACTIVE_PRODUCTION_MODE", "disabled")
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
            assert await local.get(User, UUID(DEFAULT_OPERATOR)) is None
            claims_before = (await local.execute(select(TemporalArtifactUseClaim.__table__).where(
                TemporalArtifactUseClaim.owner_principal == DEFAULT_OPERATOR))).all()
            assert claims_before
            default_rows_before = (await local.execute(select(TemporalArtifact.__table__).where(
                TemporalArtifact.created_by_principal == DEFAULT_OPERATOR))).all()
            assert len(default_rows_before) == (2 if create_default_artifact else 0)
            quota_before = await TemporalArtifactRepository(local).quota_usage_for_scope(DEFAULT_OPERATOR)

        monkeypatch.setattr(auth_modes_4120, "_ACTIVE_PRODUCTION_MODE", "oidc")
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

        monkeypatch.setattr(auth_modes_4120, "_ACTIVE_PRODUCTION_MODE", "disabled")
        async with maker() as before:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=mounted_app(before, tmp_path)),
                base_url="http://localhost",
            ) as client:
                response = await client.get(f"/api/artifacts/{later['human']}/download")
                assert response.status_code == 403, response.text

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
            assert await restarted.get(User, UUID(DEFAULT_OPERATOR)) is None
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
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(auth_modes_4120, "_ACTIVE_PRODUCTION_MODE", "disabled")
    monkeypatch.setattr(settings.oidc, "DEFAULT_USER_ID", DEFAULT_OPERATOR)
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
            assert await session.get(User, UUID(DEFAULT_OPERATOR)) is None
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


async def test_old_admission_survives_unsupported_mixed_history_without_raw_grant(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(auth_modes_4120, "_ACTIVE_PRODUCTION_MODE", "disabled")
    monkeypatch.setattr(settings.oidc, "DEFAULT_USER_ID", DEFAULT_OPERATOR)
    async with database(tmp_path) as maker:
        async with maker() as session:
            artifact_id, owner = await seed(session, tmp_path)
            session.add(WorkflowRun(feature_key="retained", requested_by_user_id=UUID(owner)))
            session.add(conversion.SingleUserConversionRun(preflight_digest="previously-published",
                status="complete", result_json={"published": True,
                    "decision": {"disposition": "eligible_conversion"}}))
            await session.commit()
            result = await conversion.run_guarded_upgrade(session, operator_authorized=True)
            assert not result.published
            assert result.decision.reason_code == "missing_transform_coverage"
            assert not (await session.get(TemporalArtifact, artifact_id)).operator_recovery_granted
            app = mounted_app(session, tmp_path)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                        base_url="http://localhost") as client:
                response = await client.get("/api/artifacts/collection")
                assert response.status_code == 200, response.text
                response = await client.get(f"/api/artifacts/{artifact_id}")
                assert response.status_code == 200, response.text
                assert response.json()["raw_access_allowed"] is False
                response = await client.get(f"/api/artifacts/{artifact_id}/download",
                    headers={"X-MoonMind-Admitted-Principal": "operator"})
                assert response.status_code == 403, response.text


async def test_default_uuid_without_server_admission_cannot_use_conversion_grant(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(auth_modes_4120, "_ACTIVE_PRODUCTION_MODE", "oidc")
    async with database(tmp_path) as maker:
        async with maker() as session:
            artifact_id, _owner = await seed(session, tmp_path)
            assert (await conversion.run_guarded_upgrade(
                session, operator_authorized=True)).published
            app = mounted_app(session, tmp_path)
            # This simulates an authenticated UUID, not disabled-mode admission.
            # The actual artifact principal and recovery dependencies stay intact.
            app.dependency_overrides[get_current_user_optional()] = lambda: User(
                id=UUID(DEFAULT_OPERATOR), email="other@example.invalid", is_active=True)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                        base_url="http://localhost") as client:
                response = await client.get(f"/api/artifacts/{artifact_id}/download",
                    headers={"X-MoonMind-Admitted-Principal": "operator"})
                assert response.status_code == 403, response.text
                response = await client.get(f"/api/artifacts/{artifact_id}")
                assert response.json()["raw_access_allowed"] is False
