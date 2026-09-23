"""Persisted catalog evidence authorizes compatible releases across builds."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api_service.db import base
from api_service.db.models import Base
from moonmind.omnigent import deployment_identity
from moonmind.omnigent.bootstrap import store
from moonmind.omnigent.bootstrap.models import ResolvedOmnigentDeploymentState
from moonmind.omnigent.harness_platform.catalog import create_catalog_snapshot
from moonmind.omnigent.harness_platform.catalog_service import (
    DbHarnessCatalogRepository,
    HarnessCatalogSyncResult,
)

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.integration,
    pytest.mark.reliability_journey,
]


@pytest.mark.parametrize(
    "case",
    [
        "patch",
        "minor",
        "missing",
        "foreign_endpoint",
        "foreign_build",
        "stale_observation",
    ],
)
async def test_admitted_catalog_survives_server_build_change(
    tmp_path, monkeypatch, case
):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/catalog.db")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        maker = async_sessionmaker(engine, expire_on_commit=False)
        monkeypatch.setattr(base, "async_session_maker", maker)
        catalog = create_catalog_snapshot(
            endpointRef="foreign" if case == "foreign_endpoint" else "default",
            omnigentVersion="0.13.0",
            omnigentBuildDigest="sha256:"
            + ("c" if case == "foreign_build" else "a") * 64,
            sourceDigest="sha256:" + "1" * 64,
            observedAt=datetime.now(UTC),
            harnesses=[],
        )
        repository = DbHarnessCatalogRepository(maker)
        if case != "missing":
            await repository.persist(HarnessCatalogSyncResult(catalog, (), {}))
        # A newer catalog must never replace the admitted version evidence.
        newer = create_catalog_snapshot(
            endpointRef="default",
            omnigentVersion="0.14.0",
            omnigentBuildDigest="sha256:" + "b" * 64,
            sourceDigest="sha256:" + "2" * 64,
            observedAt=datetime.now(UTC),
            harnesses=[],
        )
        await repository.persist(HarnessCatalogSyncResult(newer, (), {}))
        monkeypatch.setenv(
            "MOONMIND_OMNIGENT_RESOLVED_IMAGES_PATH", str(tmp_path / "images.json")
        )
        monkeypatch.delenv("OMNIGENT_BUILD_DIGEST", raising=False)
        monkeypatch.delenv("OMNIGENT_IMAGE_REF", raising=False)
        store.save_resolved_state(
            ResolvedOmnigentDeploymentState(
                serverImageRef="server@sha256:" + "b" * 64,
                details={
                    "opencodeHostCompatibility": {
                        "serverVersion": "0.14.0" if case == "minor" else "0.13.9",
                        "serverBuildDigest": "sha256:"
                        + ("c" if case == "stale_observation" else "b") * 64,
                    }
                },
            )
        )
        plan = SimpleNamespace(
            executionRealizerRef="generic-omnigent-host@1",
            endpointRef="default",
            harnessCatalogRef=catalog.catalogRef,
            supportIdentity=SimpleNamespace(
                omnigentServerBuildRef="sha256:" + "a" * 64
            ),
            hostImageRef="original-host@sha256:" + "d" * 64,
        )
        before = repr(plan)
        if case in {"patch", "minor"}:
            await deployment_identity.assert_plan_matches_deployed_runtime(plan)
        else:
            error = (
                deployment_identity.OmnigentDeploymentNotReady
                if case in {"missing", "stale_observation"}
                else deployment_identity.OmnigentDeploymentIdentityConflict
            )
            with pytest.raises(error):
                await deployment_identity.assert_plan_matches_deployed_runtime(plan)
        assert repr(plan) == before
    finally:
        await engine.dispose()
