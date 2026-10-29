"""Independent durable consumer claims compose with the lifecycle owner."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from api_service.db.models import TemporalArtifact, TemporalArtifactStatus
from moonmind.workflows.temporal.artifacts import (
    TemporalArtifactRepository,
    TemporalArtifactService,
)
from tests.artifact_access import access_for_test
from tests.integration import test_execution_input_admission_postgres as admission
from tests.unit.omnigent.test_admitted_input_authority import _put

sessions = admission.sessions
conversion_postgres_url = admission.conversion_postgres_url
pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.integration,
    pytest.mark.reliability_journey,
]


@pytest.mark.parametrize("completed", ["released", "expired"])
@pytest.mark.parametrize("operator_pin", [False, True])
async def test_independent_download_and_restore_protection_survives_one_completion(
    sessions, completed, operator_pin
):
    payload = b"independently retained recovery bytes"
    artifact_id = await _put(
        sessions, payload, owner="operator", workflow_id=None, link_type=""
    )

    async def download():
        async with sessions() as session:
            service = TemporalArtifactService(TemporalArtifactRepository(session))
            _, expires_at, url = await service.presign_download(
                artifact_id=artifact_id, access=access_for_test("operator")
            )
            assert expires_at > datetime.now(UTC)
            assert url

    async def restore():
        async with sessions() as session:
            await TemporalArtifactService(
                TemporalArtifactRepository(session)
            ).acquire_saved_work_use(
                artifact_id=artifact_id,
                principal="operator",
                access=access_for_test("operator"),
                request_id="independent-restore",
                operation_kind="restore",
                ttl_seconds=3600,
            )

    await asyncio.wait_for(asyncio.gather(download(), restore()), 10)
    async with sessions() as session:
        repository = TemporalArtifactRepository(session)
        service = TemporalArtifactService(repository)
        claims = await repository.list_use_claims(artifact_id)
        assert {claim.operation_kind for claim in claims} == {"download", "restore"}
        assert len(claims) == 2
        download_claim = next(
            claim for claim in claims if claim.operation_kind == "download"
        )
        restore_id = next(
            claim.id for claim in claims if claim.operation_kind == "restore"
        )
        if operator_pin:
            await service.pin(
                artifact_id=artifact_id, principal="operator", reason="Retain recovery"
            )
        if completed == "released":
            assert await service.release_saved_work_use(
                artifact_id=artifact_id,
                principal=download_claim.owner_principal,
                request_id=download_claim.request_id,
            )
        else:
            download_claim.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        artifact = await repository.get_artifact(artifact_id)
        artifact.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()

    async with sessions() as session:
        repository = TemporalArtifactRepository(session)
        service = TemporalArtifactService(repository)
        summary = await service.sweep_lifecycle(principal="service:lifecycle")
        assert summary.soft_deleted_count == 0
        claims = await repository.list_use_claims(artifact_id)
        assert [claim.id for claim in claims] == [restore_id]
        assert (await repository.get_pin(artifact_id) is not None) == operator_pin
        artifact = await session.scalar(
            select(TemporalArtifact).where(TemporalArtifact.artifact_id == artifact_id)
        )
        assert artifact.status is TemporalArtifactStatus.COMPLETE
        assert service._store.read_bytes(artifact.storage_key) == payload

    async with sessions() as session:
        service = TemporalArtifactService(TemporalArtifactRepository(session))
        assert await service.release_saved_work_use(
            artifact_id=artifact_id,
            principal="operator",
            request_id="independent-restore",
        )
        summary = await service.sweep_lifecycle(principal="service:lifecycle")
        assert summary.soft_deleted_count == (0 if operator_pin else 1)
        assert (
            await service._repository.get_pin(artifact_id) is not None
        ) == operator_pin
