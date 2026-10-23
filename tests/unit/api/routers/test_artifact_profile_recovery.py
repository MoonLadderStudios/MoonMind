"""Mounted profile bundle reads carry admission without changing profile authority."""

import io
import json
from types import SimpleNamespace
from uuid import UUID
from zipfile import ZipFile

import httpx
import pytest
from fastapi import FastAPI

from api_service.api.routers import omnigent_agent_profiles as profiles
from api_service.db.base import get_async_session
from api_service.db.models import (
    OmnigentAgentProfile,
    OmnigentAgentProfileVersion,
    TemporalArtifact,
    TemporalArtifactRedactionLevel,
)
from api_service.services import single_user_conversion as conversion
from tests.unit.api.routers.test_omnigent_agent_profiles import document
from tests.unit.services.test_artifact_conversion import database, seed, service


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "creator", ["historical", "operator", "service:bundle-builder"]
)
async def test_profile_validation_reads_only_admitted_recoverable_bundles(
    tmp_path,
    monkeypatch,
    creator,
):
    zero = conversion.DEPLOYMENT_SUBJECT_ID
    monkeypatch.setenv("AUTH_PROVIDER", "disabled")
    monkeypatch.setenv("DEFAULT_USER_ID", str(zero or ""))
    stream = io.BytesIO()
    with ZipFile(stream, "w") as archive:
        archive.writestr("manifest.json", json.dumps({
            "schemaVersion": "omnigent.agent-bundle.v1", "harness": "codex-native",
            "capabilities": ["session.start"],
        }))
    async with database(tmp_path) as maker:
        async with maker() as session:
            _artifact_id, historical_owner = await seed(session, tmp_path)
            owner = historical_owner if creator == "historical" else creator
            artifacts = service(session, tmp_path)
            artifact, _ = await artifacts.create(principal=owner, content_type="application/zip",
                redaction_level=TemporalArtifactRedactionLevel.RESTRICTED)
            artifact = await artifacts.write_complete(artifact_id=artifact.artifact_id,
                principal=owner, payload=stream.getvalue(), content_type="application/zip")
            artifact_id, digest = artifact.artifact_id, artifact.sha256
            assert (await conversion.run_guarded_upgrade(session, operator_authorized=True)).published
            profile = OmnigentAgentProfile(profile_id="retained-bundle", display_name="Retained",
                owner_id=UUID(historical_owner), visibility="private")
            version = OmnigentAgentProfileVersion(profile_id=profile.profile_id, version=1,
                digest="sha256:" + "a" * 64, created_by=UUID(historical_owner),
                document=profiles._normalized(document(bundleArtifactRef=f"artifact:{artifact_id}",
                    bundleDigest="sha256:" + digest)))
            session.add(profile)
            await session.flush()
            session.add(version)
            await session.commit()
        async with maker() as session:
            app = FastAPI()
            app.include_router(profiles.router)
            app.dependency_overrides[get_async_session] = lambda: session
            app.dependency_overrides[profiles._get_temporal_artifact_service] = lambda: service(session, tmp_path)
            app.dependency_overrides[profiles._require_bridge_enabled] = lambda: SimpleNamespace(host_protocol_mode="proxy")
            app.dependency_overrides[profiles._get_bridge_proxy] = lambda: None
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                        base_url="http://localhost") as client:
                response = await client.post("/api/omnigent/agent-profiles/retained-bundle/validate", json={})
                assert response.status_code == 200, response.text
                checks = {item["name"]: item for item in response.json()["checks"]}
                assert checks["bundle_provenance"]["ready"] is True
                assert checks["bundle_contents"]["ready"] is (creator != "service:bundle-builder")
                # Other readiness owners remain authoritative; no provider is
                # installed by artifact recovery, so this profile is not ready.
                assert checks["provider_profile"]["ready"] is False
                assert response.json()["ready"] is False
                response = await client.post("/api/omnigent/agent-profiles/retained-bundle/smoke", json={})
                assert response.status_code == 200, response.text
                checks = {item["name"]: item for item in response.json()["checks"]}
                assert checks["bundle_contents"]["ready"] is (creator != "service:bundle-builder")
                assert response.json()["ready"] is False
                for operation in ("validate", "smoke", "import-bundle"):
                    for headers in (
                        {"X-MoonMind-Execution-Fanout": "v1"},
                        {"X-MoonMind-Execution-Fanout": "v1", "X-MoonMind-Admitted-Principal": "operator"},
                    ):
                        denied = await client.post(
                            f"/api/omnigent/agent-profiles/retained-bundle/{operation}",
                            json={}, headers=headers,
                        )
                        assert denied.status_code == 401
                artifact = await session.get(TemporalArtifact, artifact_id)
                assert artifact.created_by_principal == owner
                assert artifact.operator_recovery_granted is (creator == "historical")
                artifact.metadata_json = {**artifact.metadata_json, "quarantine": True}
                await session.commit()
                response = await client.post("/api/omnigent/agent-profiles/retained-bundle/validate", json={})
                assert response.status_code == 200, response.text
                checks = {item["name"]: item for item in response.json()["checks"]}
                assert checks["bundle_contents"]["ready"] is False
