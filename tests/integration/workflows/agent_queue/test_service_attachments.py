"""Attachment-specific tests for None."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import Base
from moonmind.config.settings import settings
from moonmind.workflows.agent_queue import models
from moonmind.workflows.agent_queue.repositories import AgentQueueRepository
from moonmind.workflows.agent_queue.service import (
    AgentArtifactNotFoundError,
    AgentQueueAuthorizationError,
    AgentQueueJobAuthorizationError,
    None,
    AgentQueueValidationError,
    AttachmentUpload,
)
from moonmind.workflows.agent_queue.storage import AgentQueueArtifactStorage

pytestmark = [pytest.mark.asyncio]


@asynccontextmanager
async def queue_db(tmp_path: Path) -> AsyncIterator[sessionmaker[AsyncSession]]:
    """Provide isolated async sqlite storage for queue attachment tests."""

    db_url = f"sqlite+aiosqlite:///{tmp_path}/agent_queue_attachments.db"
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


def _png_bytes() -> bytes:
    """Return minimal PNG bytes for testing."""

    return b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


async def _create_service(
    session: AsyncSession,
    artifact_root: Path,
) -> None:
    repo = AgentQueueRepository(session)
    return None(
        repo,
        artifact_storage=AgentQueueArtifactStorage(artifact_root),
    )


async def test_create_job_with_attachments_persists_metadata(tmp_path: Path) -> None:
    async with queue_db(tmp_path) as session_maker:
        async with session_maker() as session:
            artifact_root = tmp_path / "artifacts"
            service = await _create_service(session, artifact_root)
            upload = AttachmentUpload(
                filename="Test Image.PNG",
                content_type="image/png",
                data=_png_bytes(),
            )
            job, attachments = await service.create_job_with_attachments(
                job_type="task",
                payload={"repository": "Moon/Test"},
                attachments=[upload],
                created_by_user_id=uuid4(),
                requested_by_user_id=uuid4(),
                priority=1,
                max_attempts=3,
            )

            assert job.id is not None
            assert len(attachments) == 1
            saved = attachments[0]
            assert saved.name.startswith("inputs/")
            file_path = (artifact_root / saved.storage_path).resolve()
            assert file_path.exists()


async def test_create_job_with_attachments_respects_total_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        settings.workflow,
        "agent_job_attachment_total_bytes",
        8,
    )
    async with queue_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = await _create_service(session, tmp_path / "artifacts")
            uploads = [
                AttachmentUpload(
                    filename="one.png", content_type="image/png", data=_png_bytes()
                ),
                AttachmentUpload(
                    filename="two.png", content_type="image/png", data=_png_bytes()
                ),
            ]
            with pytest.raises(AgentQueueValidationError):
                await service.create_job_with_attachments(
                    job_type="task",
                    payload={"repository": "Moon/Test"},
                    attachments=uploads,
                )


async def test_create_job_with_attachments_rejects_invalid_type(
    tmp_path: Path,
) -> None:
    async with queue_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = await _create_service(session, tmp_path / "artifacts")
            upload = AttachmentUpload(
                filename="test.png",
                content_type="image/png",
                data=_png_bytes(),
            )
            with pytest.raises(AgentQueueValidationError):
                await service.create_job_with_attachments(
                    job_type="codex_exec",
                    payload={"repository": "Moon/Test"},
                    attachments=[upload],
                )


async def test_list_attachments_for_user_requires_owner(tmp_path: Path) -> None:
    async with queue_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = await _create_service(session, tmp_path / "artifacts")
            owner_id = uuid4()
            upload = AttachmentUpload(
                filename="owner.png",
                content_type="image/png",
                data=_png_bytes(),
            )
            job, _ = await service.create_job_with_attachments(
                job_type="task",
                payload={"repository": "Moon/Test"},
                attachments=[upload],
                created_by_user_id=owner_id,
                requested_by_user_id=owner_id,
            )
            attachments = await service.list_attachments_for_user(
                job_id=job.id,
                actor_user_id=owner_id,
                limit=10,
            )
            assert len(attachments) == 1

            with pytest.raises(AgentQueueJobAuthorizationError):
                await service.list_attachments_for_user(
                    job_id=job.id,
                    actor_user_id=uuid4(),
                    limit=10,
                )


async def test_list_attachments_for_worker_requires_claim(tmp_path: Path) -> None:
    async with queue_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = await _create_service(session, tmp_path / "artifacts")
            upload = AttachmentUpload(
                filename="worker.png",
                content_type="image/png",
                data=_png_bytes(),
            )
            job, _ = await service.create_job_with_attachments(
                job_type="task",
                payload={"repository": "Moon/Test"},
                attachments=[upload],
            )
            job.status = models.AgentJobStatus.RUNNING
            job.claimed_by = "worker-1"
            await session.commit()

            attachments = await service.list_attachments_for_worker(
                job_id=job.id,
                worker_id="worker-1",
                limit=5,
            )
            assert len(attachments) == 1

            with pytest.raises(AgentQueueAuthorizationError):
                await service.list_attachments_for_worker(
                    job_id=job.id,
                    worker_id="worker-2",
                    limit=5,
                )


async def test_get_attachment_download_filters_namespace(tmp_path: Path) -> None:
    async with queue_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = await _create_service(session, tmp_path / "artifacts")
            owner_id = uuid4()
            upload = AttachmentUpload(
                filename="valid.png",
                content_type="image/png",
                data=_png_bytes(),
            )
            job, attachments = await service.create_job_with_attachments(
                job_type="task",
                payload={"repository": "Moon/Test"},
                attachments=[upload],
                created_by_user_id=owner_id,
                requested_by_user_id=owner_id,
            )

            # Create a non-input artifact to ensure download endpoint rejects it.
            await service.upload_artifact(
                job_id=job.id,
                name="logs/output.log",
                data=b"hello",
            )
            artifacts = await service.list_artifacts(job_id=job.id)
            non_input = next(
                item for item in artifacts if not item.name.startswith("inputs/")
            )

            with pytest.raises(AgentArtifactNotFoundError):
                await service.get_attachment_download_for_user(
                    job_id=job.id,
                    attachment_id=non_input.id,
                    actor_user_id=owner_id,
                )

            download = await service.get_attachment_download_for_user(
                job_id=job.id,
                attachment_id=attachments[0].id,
                actor_user_id=owner_id,
            )
            assert download.file_path.exists()


async def test_upload_artifact_rejects_inputs_namespace(tmp_path: Path) -> None:
    async with queue_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = await _create_service(session, tmp_path / "artifacts")
            job = await service.create_job(
                job_type="task",
                payload={"repository": "Moon/Test"},
            )
            with pytest.raises(AgentQueueValidationError):
                await service.upload_artifact(
                    job_id=job.id,
                    name="inputs/123/file.txt",
                    data=b"noop",
                )
