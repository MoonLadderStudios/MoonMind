"""Expired discovery -> authenticated HTTP -> persisted exact-profile admission."""

import asyncio
import copy
import socket
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import pytest_asyncio
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api_service.db import base as db_base
from api_service.db.models import (
    Base, ManagedAgentProviderProfile, OmnigentAgentProfile,
    OmnigentAgentProfileUsage, OmnigentAgentProfileVersion,
    OmnigentUpstreamAgentProjection,
)
from api_service.services import omnigent_agent_profile_service as inventory_service
from api_service.services.omnigent_agent_profile_selection import resolve_agent_profile_snapshot
from tests.integration.reliability.helpers import load_replay
from tests.unit.services.test_omnigent_agent_profile_selection import (
    _MM3788_V2_DOCUMENT, _Session,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.reliability_journey]


@pytest_asyncio.fixture(params=["v1", "v2"])
async def inventory_boundary(monkeypatch, tmp_path, request):
    replay = load_replay("omnigent-stale-inventory", "manifest.json")
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/inventory.db")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    monkeypatch.setattr(db_base, "async_session_maker", sessions)
    monkeypatch.setenv("OMNIGENT_ENABLED", "true")
    monkeypatch.setenv("OMNIGENT_GENERIC_HOST_ENABLED", "false")
    monkeypatch.delenv("OMNIGENT_BRIDGE_CONFIG_PATH", raising=False)
    monkeypatch.setenv("OMNIGENT_API_TOKEN", "fixture-inventory-credential")
    upstream = FastAPI()
    state = SimpleNamespace(
        rows=[], calls=0, fail=False, blocked=asyncio.Event(), mode="normal",
    )

    @upstream.get("/v1/agents")
    async def agents(request: Request):
        assert request.headers["authorization"] == "Bearer fixture-inventory-credential"
        state.calls += 1
        if state.mode == "timeout":
            await state.blocked.wait()
        if state.fail:
            return JSONResponse({"error": "fixture-inventory-credential"}, status_code=503)
        return {"data": state.rows, "has_more": False}

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(upstream, log_level="error"))
    server_task = asyncio.create_task(server.serve(sockets=[listener]))
    monkeypatch.setenv("OMNIGENT_SERVER_URL", f"http://127.0.0.1:{listener.getsockname()[1]}")
    async with asyncio.timeout(3):
        while not server.started:
            await asyncio.sleep(0.01)

    # Reuse the established launch-ready profile fixture, but persist actual ORM
    # records: identity-map refresh, commit ownership and usage are not mocked.
    fixture = _Session()
    document = copy.deepcopy(fixture.version.document)
    document["source"] = {
        "upstreamId": replay["upstreamId"], "upstreamVersion": replay["upstreamVersion"],
    }
    if request.param == "v2":
        source = document["source"]
        document = copy.deepcopy(_MM3788_V2_DOCUMENT)
        document["source"] = {
            **source, "kind": "upstream", "upstreamSnapshotDigest": "sha256:" + "b" * 64,
        }
        document["requirements"] = {"moonmind": {"required": ["session.start"]}}
    row = {
        "id": replay["upstreamId"], "version": replay["upstreamVersion"],
        "harness": "codex-native", "capabilities": ["session.start"],
    }
    state.rows = [row]
    projection_id = inventory_service.projection_identity("default", row["id"], row["version"])
    old_time = datetime.now(timezone.utc) - timedelta(seconds=replay["inventoryAgeSeconds"])
    async with sessions() as session:
        session.add(OmnigentAgentProfile(
            profile_id="team-codex", display_name="Selected profile", visibility="workspace",
            state="active", active_version=2,
        ))
        session.add(OmnigentAgentProfileVersion(
            profile_id="team-codex", version=2, digest=fixture.version.digest,
            document=document, validation_result={"ready": True},
        ))
        provider_fields = {
            key: value for key, value in vars(fixture.provider).items()
            if key in ManagedAgentProviderProfile.__table__.columns
        }
        session.add(ManagedAgentProviderProfile(**provider_fields))
        session.add(OmnigentUpstreamAgentProjection(
            projection_id=projection_id, endpoint_ref="default", bridge_mode="proxy",
            upstream_id=row["id"], upstream_version=row["version"],
            metadata_snapshot=row, available=True, compatible=True,
            last_successful_sync_at=old_time, last_attempt_at=old_time,
        ))
        await session.commit()
    try:
        yield sessions, state, projection_id
    finally:
        state.blocked.set()
        server.should_exit = True
        await asyncio.wait_for(server_task, 3)
        listener.close()
        await engine.dispose()


async def _select(session):
    return await resolve_agent_profile_snapshot(
        session, selection={
            "profileId": "team-codex", "version": 2, "providerProfileRef": "oauth-team",
            "digest": "sha256:" + "a" * 64,
        }, consumer_type="workflow", consumer_id="new-workflow", user=None,
    )


async def test_stale_admission_refreshes_exact_identity_without_committing_authoring(inventory_boundary):
    sessions, state, projection_id = inventory_boundary
    async with sessions() as session:
        stale = await session.get(OmnigentUpstreamAgentProjection, projection_id)
        assert inventory_service.projection_readiness(stale)["freshness"] == "stale"
        snapshot = await _select(session)
        assert snapshot["agentId"] == "selected-agent"
        assert snapshot["version"] == 2
        assert snapshot["document"]["source"]["upstreamVersion"] == "7"
        assert inventory_service.projection_readiness(stale)["ready"] is True
        assert state.calls == 1
        await session.rollback()
    async with sessions() as session:
        assert await session.scalar(select(OmnigentAgentProfileUsage)) is None
        refreshed = await session.get(OmnigentUpstreamAgentProjection, projection_id)
        assert inventory_service.projection_readiness(refreshed)["ready"] is True
        await _select(session)
        await session.commit()
        assert state.calls == 1, "Fresh inventory must not call upstream again"
    async with sessions() as session:
        usage = await session.scalar(select(OmnigentAgentProfileUsage))
        assert usage.effective_snapshot == snapshot


@pytest.mark.parametrize("fault", ["removed", "version_changed", "incompatible", "capability_removed", "outage", "timeout"])
async def test_refresh_never_admits_unverified_or_substituted_authority(inventory_boundary, monkeypatch, fault):
    sessions, state, projection_id = inventory_boundary
    if fault == "removed":
        state.rows = []
    elif fault == "version_changed":
        state.rows[0]["version"] = "8"
    elif fault == "incompatible":
        state.rows[0]["harness"] = "unknown-harness"
    elif fault == "capability_removed":
        state.rows[0]["capabilities"] = []
    elif fault == "outage":
        state.fail = True
    elif fault == "timeout":
        state.mode = "timeout"
        monkeypatch.setattr(inventory_service, "_INVENTORY_REFRESH_TIMEOUT_SECONDS", 0.05)
    async with sessions() as session:
        with pytest.raises(HTTPException) as caught:
            async with asyncio.timeout(2):
                await _select(session)
        assert caught.value.status_code == 409
        assert "fixture-inventory-credential" not in str(caught.value.detail)
        await session.rollback()
    async with sessions() as session:
        assert await session.scalar(select(OmnigentAgentProfileUsage)) is None
        projection = await session.get(OmnigentUpstreamAgentProjection, projection_id)
        assert inventory_service.projection_readiness(
            projection, required_capabilities=["session.start"],
        )["ready"] is False
        if fault in {"outage", "timeout"}:
            assert "retry submission" in projection.error
            assert "fixture-inventory-credential" not in projection.error
    assert state.calls == 1


async def test_refresh_rejects_unconfigured_endpoint_without_network(inventory_boundary):
    _sessions, state, _projection_id = inventory_boundary
    with pytest.raises(inventory_service.UpstreamInventoryRefreshError, match="configured default"):
        await inventory_service.refresh_upstream_inventory(endpoint_ref="other")
    assert state.calls == 0


@pytest.mark.parametrize("cache_state", ["missing", "error"])
async def test_admission_recovers_missing_or_failed_discovery(inventory_boundary, cache_state):
    sessions, state, projection_id = inventory_boundary
    async with sessions() as session:
        projection = await session.get(OmnigentUpstreamAgentProjection, projection_id)
        if cache_state == "missing":
            await session.delete(projection)
        else:
            projection.last_successful_sync_at = datetime.now(timezone.utc)
            projection.error = "earlier refresh failed"
        await session.commit()
    async with sessions() as session:
        assert (await _select(session))["agentId"] == "selected-agent"
        await session.commit()
    assert state.calls == 1


async def test_older_failure_cannot_invalidate_a_newer_success(inventory_boundary):
    sessions, _state, projection_id = inventory_boundary
    attempted_at = datetime.now(timezone.utc)
    await inventory_service.refresh_upstream_inventory()
    async with sessions() as session:
        await inventory_service.record_upstream_sync_failure(
            session, endpoint_ref="default", bridge_mode="proxy",
            error="an earlier attempt failed", now=attempted_at,
        )
    async with sessions() as session:
        projection = await session.get(OmnigentUpstreamAgentProjection, projection_id)
        assert inventory_service.projection_readiness(projection)["ready"] is True
        assert projection.error is None


async def test_refresh_covers_all_launchable_harnesses(inventory_boundary, monkeypatch, tmp_path):
    from api_service.db.models import (
        OmnigentHarnessCatalogSnapshotRecord, OmnigentHarnessTrustRecord,
    )
    from moonmind.omnigent.bootstrap.models import ResolvedOmnigentDeploymentState
    from moonmind.omnigent.bootstrap.store import save_resolved_state
    from moonmind.omnigent.harness_platform.catalog import create_catalog_snapshot

    sessions, state, _projection_id = inventory_boundary
    host_image = "example/host@sha256:" + "1" * 64
    server_image = "example/server@sha256:" + "2" * 64
    monkeypatch.setenv("OMNIGENT_GENERIC_HOST_ENABLED", "true")
    monkeypatch.setenv("OMNIGENT_OPENCODE_ENABLED", "true")
    monkeypatch.setenv("OMNIGENT_OPENCODE_HOST_IMAGE_REF", host_image)
    monkeypatch.setenv("OMNIGENT_IMAGE_REF", server_image)
    monkeypatch.setenv("MOONMIND_OMNIGENT_RESOLVED_IMAGES_PATH", str(tmp_path / "images.json"))
    save_resolved_state(ResolvedOmnigentDeploymentState(
        server_image_ref=server_image, opencode_host_image_ref=host_image,
        details={"opencodeHostCompatibility": {
            "status": "ready", "serverImageRef": server_image, "hostImageRef": host_image,
        }},
    ))
    catalog = create_catalog_snapshot(
        endpointRef="default", omnigentVersion="0.12.0",
        omnigentBuildDigest="sha256:" + "2" * 64, sourceDigest="sha256:" + "3" * 64,
        harnesses=[inventory_service._synthetic_opencode_harness_row()],
    )
    async with sessions() as session:
        session.add(OmnigentHarnessCatalogSnapshotRecord(
            catalog_ref=catalog.catalogRef, endpoint_ref="default",
            omnigent_version=catalog.omnigentVersion,
            omnigent_build_digest=catalog.omnigentBuildDigest,
            observed_at=catalog.observedAt, source_digest=catalog.sourceDigest,
            snapshot_json=catalog.model_dump(mode="json", by_alias=True),
        ))
        session.add(OmnigentHarnessTrustRecord(
            implementation_ref=catalog.harnesses[0].implementation.implementation_ref(),
            harness_id="opencode-native", catalog_ref=catalog.catalogRef, trust_state="core_trusted",
        ))
        await session.commit()
    harnesses = {"codex-native", "claude-native", "opencode-native"}
    state.rows = [{"id": harness, "version": "1", "harness": harness} for harness in harnesses]
    await inventory_service.refresh_upstream_inventory()
    async with sessions() as session:
        for harness in harnesses:
            projection = await session.get(
                OmnigentUpstreamAgentProjection,
                inventory_service.projection_identity("default", harness, "1"),
            )
            assert inventory_service.projection_readiness(
                projection, harness=harness, required_capabilities=["session.start"],
            )["ready"] is True
