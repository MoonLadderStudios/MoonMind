"""Unit tests for the manifest registry service."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import Base
from api_service.services.manifests_service import (
    ManifestRegistryNotFoundError,
    ManifestsService,
)
from moonmind.workflows.agent_queue import models as queue_models
from moonmind.workflows.agent_queue.job_types import MANIFEST_JOB_TYPE

pytestmark = [pytest.mark.asyncio, pytest.mark.speckit]


def _tests_root() -> Path:
    path = Path(__file__).resolve()
    try:
        idx = path.parts.index("tests")
    except ValueError as exc:  # pragma: no cover - defensive
        raise RuntimeError("tests directory not found") from exc
    return Path(*path.parts[: idx + 1])


FIXTURE_ROOT = _tests_root() / "fixtures" / "manifests" / "phase0"
REGISTRY_MANIFEST = (FIXTURE_ROOT / "registry.yaml").read_text()


@asynccontextmanager
async def manifest_db(tmp_path: Path):
    """Provide an isolated async sqlite database for registry tests."""

    db_path = tmp_path / "manifest_registry.db"
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


async def test_upsert_manifest_persists_normalized_hash_and_version(
    tmp_path: Path,
) -> None:
    """ManifestsService.upsert_manifest should hash and version manifests deterministically."""

    async with manifest_db(tmp_path) as session_maker:
        async with session_maker() as session:
            queue_service = SimpleNamespace(create_job=None)
            service = ManifestsService(session, queue_service)  # type: ignore[arg-type]

            record = await service.upsert_manifest(
                name="demo", content=REGISTRY_MANIFEST
            )
            assert record.name == "demo"
            assert record.version == "v0"
            assert record.content_hash.startswith("sha256:")
            first_hash = record.content_hash

            updated = await service.upsert_manifest(
                name="demo", content=REGISTRY_MANIFEST
            )
            assert updated.id == record.id
            assert updated.content_hash == first_hash


async def test_submit_manifest_run_enqueues_queue_job_and_updates_registry(
    tmp_path: Path,
) -> None:
    """submit_manifest_run should enqueue manifest jobs with derived payloads."""

    async with manifest_db(tmp_path) as session_maker:
        async with session_maker() as session:
            job_id = uuid4()
            queue_service = SimpleNamespace()

            async def _create_job(**kwargs):
                assert kwargs["job_type"] == MANIFEST_JOB_TYPE
                payload = kwargs["payload"]
                assert payload["manifest"]["source"]["kind"] == "registry"
                return SimpleNamespace(
                    id=job_id,
                    type=MANIFEST_JOB_TYPE,
                    status=queue_models.AgentJobStatus.QUEUED,
                    payload={
                        "manifestHash": "sha256:abc",
                        "requiredCapabilities": ["manifest"],
                    },
                    created_at=datetime.now(UTC),
                )

            queue_service.create_job = _create_job  # type: ignore[attr-defined]
            service = ManifestsService(session, queue_service)  # type: ignore[arg-type]

            await service.upsert_manifest(name="demo", content=REGISTRY_MANIFEST)
            job = await service.submit_manifest_run(
                name="demo",
                action="run",
                options={"dryRun": True},
                user_id=uuid4(),
            )

            assert job.id == job_id
            record = await service.get_manifest("demo")
            assert record is not None
            assert record.last_run_job_id == job_id
            assert record.last_run_status == queue_models.AgentJobStatus.QUEUED.value


async def test_update_manifest_state_persists_checkpoint_and_run_metadata(
    tmp_path: Path,
) -> None:
    """update_manifest_state should persist state_json and optional run metadata."""

    async with manifest_db(tmp_path) as session_maker:
        async with session_maker() as session:
            queue_service = SimpleNamespace(create_job=None)
            service = ManifestsService(session, queue_service)  # type: ignore[arg-type]

            await service.upsert_manifest(name="demo", content=REGISTRY_MANIFEST)
            last_job_id = uuid4()
            updated = await service.update_manifest_state(
                name="demo",
                state_json={"docs": {"cursor": "abc", "docHashes": {"doc-1": "h1"}}},
                last_run_job_id=last_job_id,
                last_run_status="succeeded",
            )

            assert updated.state_json == {
                "docs": {"cursor": "abc", "docHashes": {"doc-1": "h1"}}
            }
            assert updated.last_run_job_id == last_job_id
            assert updated.last_run_status == "succeeded"
            assert updated.state_updated_at is not None


async def test_list_manifests_returns_ordered_and_limited_results(
    tmp_path: Path,
) -> None:
    """list_manifests should return manifests ordered by name and respect the limit."""

    async with manifest_db(tmp_path) as session_maker:
        async with session_maker() as session:
            queue_service = SimpleNamespace(create_job=None)
            service = ManifestsService(session, queue_service)  # type: ignore[arg-type]

            # Insert manifests in a random order
            # Note: upsert_manifest validates that the YAML metadata.name matches the arg.
            await service.upsert_manifest(
                name="zebra", content=REGISTRY_MANIFEST.replace('"demo"', '"zebra"')
            )
            await service.upsert_manifest(
                name="apple", content=REGISTRY_MANIFEST.replace('"demo"', '"apple"')
            )
            await service.upsert_manifest(
                name="mango", content=REGISTRY_MANIFEST.replace('"demo"', '"mango"')
            )

            # Test basic listing and ordering
            results = await service.list_manifests()
            assert [r.name for r in results] == ["apple", "mango", "zebra"]

            # Test limit
            limited_results = await service.list_manifests(limit=2)
            assert [r.name for r in limited_results] == ["apple", "mango"]


async def test_list_manifests_filters_by_search_pattern(
    tmp_path: Path,
) -> None:
    """list_manifests should filter results when a search pattern is provided."""

    async with manifest_db(tmp_path) as session_maker:
        async with session_maker() as session:
            queue_service = SimpleNamespace(create_job=None)
            service = ManifestsService(session, queue_service)  # type: ignore[arg-type]

            await service.upsert_manifest(
                name="app-demo",
                content=REGISTRY_MANIFEST.replace('"demo"', '"app-demo"'),
            )
            await service.upsert_manifest(
                name="app-test",
                content=REGISTRY_MANIFEST.replace('"demo"', '"app-test"'),
            )
            await service.upsert_manifest(
                name="backend", content=REGISTRY_MANIFEST.replace('"demo"', '"backend"')
            )

            # Test exact prefix search
            results = await service.list_manifests(search="app-")
            assert [r.name for r in results] == ["app-demo", "app-test"]

            # Test case-insensitivity
            results_ci = await service.list_manifests(search="APP-")
            assert [r.name for r in results_ci] == ["app-demo", "app-test"]

            # Test no matches
            empty_results = await service.list_manifests(search="frontend")
            assert empty_results == []


async def test_require_manifest_raises_not_found_for_missing_entry(
    tmp_path: Path,
) -> None:
    """require_manifest should raise ManifestRegistryNotFoundError if manifest does not exist."""

    async with manifest_db(tmp_path) as session_maker:
        async with session_maker() as session:
            queue_service = SimpleNamespace(create_job=None)
            service = ManifestsService(session, queue_service)  # type: ignore[arg-type]

            with pytest.raises(ManifestRegistryNotFoundError) as exc_info:
                await service.require_manifest("nonexistent-manifest")

            assert "Manifest 'nonexistent-manifest' was not found" in str(
                exc_info.value
            )
