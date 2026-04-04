"""Integration coverage for local-dev Temporal artifact create/upload/list flow."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import Base
from moonmind.workflows.temporal.artifacts import (
    LocalTemporalArtifactStore,
    TemporalArtifactRepository,
    TemporalArtifactService,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]


@asynccontextmanager
async def _db(tmp_path: Path):
    url = f"sqlite+aiosqlite:///{tmp_path}/temporal_local_dev_integration.db"
    engine = create_async_engine(url, future=True)
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield maker
    finally:
        await engine.dispose()


async def test_local_dev_create_upload_complete_list(tmp_path: Path) -> None:
    async with _db(tmp_path) as maker:
        async with maker() as session:
            service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )
            artifact, _upload = await service.create(
                principal="default-user",
                content_type="text/plain",
                link={
                    "namespace": "moonmind",
                    "workflow_id": "wf-local",
                    "run_id": "run-local",
                    "link_type": "output.primary",
                },
            )
            await service.write_complete(
                artifact_id=artifact.artifact_id,
                principal="default-user",
                payload=b"result",
                content_type="text/plain",
            )
            listed = await service.list_for_execution(
                namespace="moonmind",
                workflow_id="wf-local",
                run_id="run-local",
                principal="default-user",
                link_type="output.primary",
                latest_only=True,
            )
            assert len(listed) == 1
            assert listed[0].artifact_id == artifact.artifact_id
