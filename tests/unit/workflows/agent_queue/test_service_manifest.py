"""Manifest-specific unit tests for the Agent Queue service."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import Base
from moonmind.workflows.agent_queue.repositories import AgentQueueRepository
from moonmind.workflows.agent_queue.service import (
    AgentQueueService,
    AgentQueueValidationError,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.speckit]


def _tests_root() -> Path:
    path = Path(__file__).resolve()
    try:
        idx = path.parts.index("tests")
    except ValueError as exc:  # pragma: no cover - defensive
        raise RuntimeError("tests directory not found") from exc
    return Path(*path.parts[: idx + 1])


FIXTURE_ROOT = _tests_root() / "fixtures" / "manifests" / "phase0"
INLINE_MANIFEST = (FIXTURE_ROOT / "inline.yaml").read_text()


@asynccontextmanager
async def queue_db(tmp_path: Path):
    """Provide an isolated async sqlite database for service tests."""

    db_path = tmp_path / "service_manifest.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", future=True)
    async_session_maker = sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    try:
        yield async_session_maker
    finally:
        await engine.dispose()


async def test_create_manifest_job_normalizes_payload(tmp_path: Path) -> None:
    """Manifest jobs should persist normalized hashes, versions, and capabilities."""

    async with queue_db(tmp_path) as session_maker:
        async with session_maker() as session:
            repo = AgentQueueRepository(session)
            service = AgentQueueService(repo)
            payload = {
                "manifest": {
                    "name": "demo-manifest",
                    "action": "run",
                    "source": {"kind": "inline", "content": INLINE_MANIFEST},
                    "options": {"dryRun": True},
                }
            }

            job = await service.create_job(job_type="manifest", payload=payload)

    normalized = job.payload
    assert normalized["manifestHash"].startswith("sha256:")
    assert normalized["manifestVersion"] == "v0"
    assert normalized["requiredCapabilities"] == [
        "manifest",
        "embeddings",
        "openai",
        "qdrant",
        "github",
    ]
    assert normalized["manifest"]["source"]["kind"] == "inline"
    assert "content" in normalized["manifest"]["source"]
    assert normalized["effectiveRunConfig"]["dryRun"] is True


async def test_create_manifest_job_rejects_name_mismatch(tmp_path: Path) -> None:
    """Manifest normalization should reject metadata/name mismatches."""

    bad_yaml = INLINE_MANIFEST.replace("demo-manifest", "other-manifest")
    async with queue_db(tmp_path) as session_maker:
        async with session_maker() as session:
            repo = AgentQueueRepository(session)
            service = AgentQueueService(repo)
            payload = {
                "manifest": {
                    "name": "demo-manifest",
                    "action": "run",
                    "source": {"kind": "inline", "content": bad_yaml},
                }
            }
            with pytest.raises(AgentQueueValidationError, match="must match metadata"):
                await service.create_job(job_type="manifest", payload=payload)
