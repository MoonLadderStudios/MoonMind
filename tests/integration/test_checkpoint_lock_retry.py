"""Real row-lock contention never publishes an unsealed checkpoint."""

import json

import pytest
from sqlalchemy import select

from api_service.db.models import TemporalArtifact, TemporalArtifactLink
from moonmind.config.settings import settings
from moonmind.omnigent.bridge_artifacts import TemporalOmnigentArtifactGateway
from moonmind.workflows.temporal.artifacts import (
    ArtifactAccessContext,
    TemporalArtifactRepository,
    TemporalArtifactUnavailableError,
)
from tests.integration.reliability.test_schedule_input_retention_postgres import context
from tests.unit.omnigent.test_generic_saved_work_index import fixture
from tests.unit.workflows.temporal.test_saved_work_retention_4017 import _service

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]


@pytest.mark.parametrize("release", [True, False])
async def test_checkpoint_retries_real_dependency_row_lock(
    tmp_path, monkeypatch, control_plane_postgres_url, release
):
    monkeypatch.setenv("MOONMIND_TEST_POSTGRES_URL", control_plane_postgres_url)
    monkeypatch.setattr(settings.workflow, "temporal_artifact_backend", "local_fs")
    monkeypatch.setattr(
        settings.workflow, "temporal_artifact_root", str(tmp_path / "artifacts")
    )
    _, objects, _, request = fixture(tmp_path)
    async with context(tmp_path) as sessions:
        gateway = TemporalOmnigentArtifactGateway(sessions)
        archive = await gateway.write_bytes(
            request=request,
            name="checkpoint_archive",
            payload=objects.objects["artifact:archive"],
            link_type="evidence.recovery",
        )
        archive_id = archive.removeprefix("artifact:")
        checkpoint = json.loads(objects.objects["artifact:checkpoint"])
        checkpoint["archiveRef"] = archive
        payload = json.dumps(checkpoint).encode()
        async with sessions() as holder:
            await TemporalArtifactRepository(holder).get_artifact_for_update(archive_id)
            sleeps = []

            async def release_lock(delay):
                sleeps.append(delay)
                # Another transaction sees no partial root/link from failed seal.
                async with sessions() as observer:
                    links = (await observer.scalars(select(TemporalArtifactLink))).all()
                    assert [link.artifact_id for link in links] == [archive_id]
                if release:
                    await holder.rollback()

            monkeypatch.setattr(
                "moonmind.omnigent.bridge_artifacts.asyncio.sleep", release_lock
            )
            kwargs = dict(
                request=request,
                name="checkpoint_manifest",
                payload=payload,
                content_type="application/json",
                link_type="evidence.recovery",
            )
            if release:
                root = await gateway.write_bytes(**kwargs)
                assert len(sleeps) == 1
                assert await gateway.write_bytes(**kwargs) == root
                async with sessions() as observer:
                    closure = await _service(observer, tmp_path).verified_dependencies(
                        root_artifact_id=root.removeprefix("artifact:"),
                        access=ArtifactAccessContext.for_execution(
                            "workflow", principal="service:omnigent-generic-host"
                        ),
                    )
                    assert any(
                        entry.artifact_id == archive_id and entry.required
                        for entry in closure.dependencies
                    )
                    links = (await observer.scalars(select(TemporalArtifactLink))).all()
                    assert len(links) == 2
            else:
                with pytest.raises(TemporalArtifactUnavailableError) as caught:
                    await gateway.write_bytes(**kwargs)
                assert caught.value.artifact_id == archive_id
                assert caught.value.operation == "lock_dependencies"
                assert len(sleeps) == 4
            await holder.rollback()
        assert await gateway.read_bytes(archive) == objects.objects["artifact:archive"]
        async with sessions() as observer:
            rows = (await observer.scalars(select(TemporalArtifact))).all()
            # Failed attempts roll back logical rows; accepted parts stay intact.
            assert len(rows) == (2 if release else 1)
