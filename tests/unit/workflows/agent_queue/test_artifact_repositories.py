"""Unit tests for queue artifact repository and service behavior."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import Base
from moonmind.workflows.agent_queue.repositories import (
    AgentArtifactJobMismatchError,
    AgentJobNotFoundError,
    AgentQueueRepository,
)
from moonmind.workflows.agent_queue.service import (
    AgentQueueAuthorizationError,
    AgentQueueService,
    AgentQueueValidationError,
)
from moonmind.workflows.agent_queue.storage import AgentQueueArtifactStorage

pytestmark = [pytest.mark.asyncio, pytest.mark.speckit]


@asynccontextmanager
async def queue_db(tmp_path: Path):
    """Provide isolated async sqlite storage for queue artifact tests."""

    db_url = f"sqlite+aiosqlite:///{tmp_path}/agent_queue_artifacts.db"
    engine = create_async_engine(db_url, future=True)
    async_session_maker = sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    try:
        yield async_session_maker
    finally:
        await engine.dispose()


async def test_create_and_list_artifact_metadata(tmp_path: Path) -> None:
    """Artifact metadata should persist and list by job id."""

    async with queue_db(tmp_path) as session_maker:
        async with session_maker() as session:
            repo = AgentQueueRepository(session)
            job = await repo.create_job(
                job_type="codex_exec",
                payload={"instruction": "upload"},
            )
            await repo.commit()

            await repo.create_artifact(
                job_id=job.id,
                name="logs/output.log",
                storage_path=f"{job.id}/logs/output.log",
                size_bytes=12,
                content_type="text/plain",
                digest="sha256:abc123",
            )
            await repo.commit()

            artifacts = await repo.list_artifacts(job_id=job.id)

    assert len(artifacts) == 1
    assert artifacts[0].name == "logs/output.log"
    assert artifacts[0].storage_path == f"{job.id}/logs/output.log"


async def test_get_artifact_for_job_rejects_mismatch(tmp_path: Path) -> None:
    """Artifacts should not be retrievable under the wrong job id."""

    async with queue_db(tmp_path) as session_maker:
        async with session_maker() as session:
            repo = AgentQueueRepository(session)
            job_a = await repo.create_job(
                job_type="codex_exec",
                payload={"instruction": "a"},
            )
            job_b = await repo.create_job(
                job_type="codex_exec",
                payload={"instruction": "b"},
            )
            await repo.commit()

            artifact = await repo.create_artifact(
                job_id=job_a.id,
                name="logs/a.log",
                storage_path=f"{job_a.id}/logs/a.log",
                size_bytes=3,
            )
            await repo.commit()

            with pytest.raises(AgentArtifactJobMismatchError):
                await repo.get_artifact_for_job(
                    job_id=job_b.id, artifact_id=artifact.id
                )


async def test_service_rejects_oversized_upload(tmp_path: Path) -> None:
    """Service should reject payloads larger than configured max bytes."""

    artifact_root = tmp_path / "artifacts"
    async with queue_db(tmp_path) as session_maker:
        async with session_maker() as session:
            repo = AgentQueueRepository(session)
            job = await repo.create_job(
                job_type="codex_exec",
                payload={"instruction": "upload"},
            )
            await repo.commit()

            service = AgentQueueService(
                repo,
                artifact_storage=AgentQueueArtifactStorage(artifact_root),
                artifact_max_bytes=4,
            )

            with pytest.raises(AgentQueueValidationError):
                await service.upload_artifact(
                    job_id=job.id,
                    name="logs/too-large.log",
                    data=b"12345",
                )


async def test_service_rejects_traversal_name(tmp_path: Path) -> None:
    """Service should reject traversal artifact names."""

    artifact_root = tmp_path / "artifacts"
    async with queue_db(tmp_path) as session_maker:
        async with session_maker() as session:
            repo = AgentQueueRepository(session)
            job = await repo.create_job(
                job_type="codex_exec",
                payload={"instruction": "upload"},
            )
            await repo.commit()

            service = AgentQueueService(
                repo,
                artifact_storage=AgentQueueArtifactStorage(artifact_root),
                artifact_max_bytes=1024,
            )

            with pytest.raises(AgentQueueValidationError):
                await service.upload_artifact(
                    job_id=job.id,
                    name="../escape.log",
                    data=b"ok",
                )


async def test_service_missing_job_does_not_write_file(tmp_path: Path) -> None:
    """Missing jobs should fail before any artifact bytes are written."""

    artifact_root = tmp_path / "artifacts"
    async with queue_db(tmp_path) as session_maker:
        async with session_maker() as session:
            repo = AgentQueueRepository(session)
            service = AgentQueueService(
                repo,
                artifact_storage=AgentQueueArtifactStorage(artifact_root),
                artifact_max_bytes=1024,
            )

            with pytest.raises(AgentJobNotFoundError):
                await service.upload_artifact(
                    job_id=uuid4(),
                    name="logs/output.log",
                    data=b"ok",
                )

    assert not artifact_root.exists()


async def test_service_upload_requires_claimed_worker_ownership(tmp_path: Path) -> None:
    """Worker-bound uploads should enforce active claim ownership."""

    artifact_root = tmp_path / "artifacts"
    async with queue_db(tmp_path) as session_maker:
        async with session_maker() as session:
            repo = AgentQueueRepository(session)
            job = await repo.create_job(
                job_type="codex_exec",
                payload={"instruction": "upload"},
            )
            await repo.commit()
            await repo.claim_job(worker_id="worker-1", lease_seconds=30)
            await repo.commit()

            service = AgentQueueService(
                repo,
                artifact_storage=AgentQueueArtifactStorage(artifact_root),
                artifact_max_bytes=1024,
            )

            with pytest.raises(AgentQueueAuthorizationError):
                await service.upload_artifact(
                    job_id=job.id,
                    name="logs/output.log",
                    data=b"ok",
                    worker_id="worker-2",
                )
