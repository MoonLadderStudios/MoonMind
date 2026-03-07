import logging
from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import Base
from api_service.services.manifest_sync_service import ManifestSyncService

MANIFEST_YAML = """
apiVersion: moonmind/v1
kind: Readers
metadata: {}
spec:
  readers:
    - type: DummyReader
      init:
        token: 1
"""

MANIFEST_YAML_MOD = MANIFEST_YAML.replace("token: 1", "token: 2")


@pytest.mark.asyncio
async def test_manifest_sync_detects_changes(tmp_path):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async_session_maker = sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    service = ManifestSyncService(logger=logging.getLogger("test"))

    with patch("api_service.services.manifest_sync_service.ManifestRunner") as MR:
        MR.return_value.run.return_value = {}
        async with async_session_maker() as session:
            status1 = await service.sync_manifest(session, "test", MANIFEST_YAML)
        MR.assert_called_once()
    assert status1.name.lower() == "new"

    with patch("api_service.services.manifest_sync_service.ManifestRunner") as MR:
        MR.return_value.run.return_value = {}
        async with async_session_maker() as session:
            status2 = await service.sync_manifest(session, "test", MANIFEST_YAML)
        MR.assert_not_called()
    assert status2.name.lower() == "unchanged"

    with patch("api_service.services.manifest_sync_service.ManifestRunner") as MR:
        MR.return_value.run.return_value = {}
        async with async_session_maker() as session:
            status3 = await service.sync_manifest(session, "test", MANIFEST_YAML_MOD)
        MR.assert_called_once()
    assert status3.name.lower() == "modified"
