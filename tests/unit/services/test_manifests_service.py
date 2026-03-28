"""Unit tests for the manifest registry service."""

from __future__ import annotations

from contextlib import asynccontextmanager
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

import pytest
import yaml
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import Base
from api_service.services.manifests_service import (
    ManifestRegistryNotFoundError,
    ManifestsService,
)
from moonmind.config.settings import settings
from moonmind.workflows.temporal import (
    LocalTemporalArtifactStore,
    TemporalArtifactRepository,
    TemporalArtifactService,
    TemporalExecutionService,
)

pytestmark = [pytest.mark.asyncio]


def _tests_root() -> Path:
    path = Path(__file__).resolve()
    try:
        idx = path.parts.index("tests")
    except ValueError as exc:  # pragma: no cover - defensive
        raise RuntimeError("tests directory not found") from exc
    return Path(*path.parts[: idx + 1])


FIXTURE_ROOT = _tests_root() / "fixtures" / "manifests" / "phase0"
REGISTRY_MANIFEST = (FIXTURE_ROOT / "registry.yaml").read_text()
REGISTRY_MANIFEST_OBJ = yaml.safe_load(REGISTRY_MANIFEST)


def _manifest_with_name(name: str) -> str:
    """Return a copy of the fixture manifest with an updated metadata name."""

    manifest = deepcopy(REGISTRY_MANIFEST_OBJ)
    manifest["metadata"]["name"] = name
    return yaml.safe_dump(manifest, sort_keys=False)


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
            service = ManifestsService(session)

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

async def test_submit_manifest_run_starts_temporal_execution_with_artifact_ref(
    tmp_path: Path, monkeypatch
) -> None:
    """submit_manifest_run should stage registry YAML as an artifact and create a Temporal ingest execution."""
    monkeypatch.setattr(settings.temporal_dashboard, "submit_enabled", True)

    async with manifest_db(tmp_path) as session_maker:
        async with session_maker() as session:
            artifact_service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "temporal-artifacts"),
            )
            execution_service = TemporalExecutionService(session)
            service = ManifestsService(
                session,
                execution_service=execution_service,
                artifact_service=artifact_service,
            )

            user_id = uuid4()
            await service.upsert_manifest(name="demo", content=REGISTRY_MANIFEST)
            submitted = await service.submit_manifest_run(
                name="demo",
                action="run",
                options={"dryRun": True},
                user_id=user_id,
                title="Registry ingest",
                failure_policy="fail_fast",
                max_concurrency=25,
                tags={"env": "test"},
                idempotency_key="manifest-demo-1",
            )

            assert submitted.source == "temporal"
            assert submitted.workflow_id is not None
            assert submitted.run_id is not None
            assert submitted.workflow_type == "MoonMind.ManifestIngest"
            assert submitted.temporal_status == "running"
            assert submitted.manifest_artifact_ref is not None

            execution = await execution_service.describe_execution(
                submitted.workflow_id
            )
            assert execution.manifest_ref == submitted.manifest_artifact_ref
            assert execution.parameters["manifestName"] == "demo"
            assert execution.parameters["action"] == "run"
            assert execution.parameters["options"] == {"dryRun": True}
            assert execution.parameters["manifestDigest"].startswith("sha256:")
            assert execution.parameters["manifestNodes"]
            assert execution.parameters["executionPolicy"]["maxConcurrency"] == 25
            assert (
                execution.parameters["executionPolicy"]["failurePolicy"] == "fail_fast"
            )
            assert execution.parameters["requestedBy"]["id"] == str(user_id)
            assert execution.search_attributes["mm_entry"] == "manifest"
            assert execution.search_attributes["mm_owner_type"] == "user"
            assert execution.search_attributes["mm_state"] == "executing"
            assert execution.plan_ref is not None
            assert execution.memo["summary_artifact_ref"].startswith("art_")
            assert execution.memo["run_index_artifact_ref"].startswith("art_")
            assert "manifest:" not in str(execution.memo).lower()
            assert REGISTRY_MANIFEST not in str(execution.memo)

            first_node = execution.parameters["manifestNodes"][0]
            assert first_node["childWorkflowId"].startswith("mm:")
            child_execution = await execution_service.describe_execution(
                first_node["childWorkflowId"]
            )
            assert (
                child_execution.parameters["manifestIngestWorkflowId"]
                == execution.workflow_id
            )
            assert child_execution.parameters["parentClosePolicy"] == "REQUEST_CANCEL"

            record = await service.get_manifest("demo")
            assert record is not None
            assert record.last_run_job_id is None
            assert record.last_run_source == "temporal"
            assert record.last_run_workflow_id == submitted.workflow_id
            assert record.last_run_temporal_run_id == submitted.run_id
            assert record.last_run_manifest_ref == submitted.manifest_artifact_ref
            assert record.last_run_status == "executing"


async def test_submit_manifest_run_reuses_idempotent_execution_without_side_effects(
    tmp_path: Path, monkeypatch
) -> None:
    """submit_manifest_run should return the original Temporal submission for idempotent retries."""
    monkeypatch.setattr(settings.temporal_dashboard, "submit_enabled", True)

    async with manifest_db(tmp_path) as session_maker:
        async with session_maker() as session:
            artifact_service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "temporal-artifacts"),
            )
            execution_service = TemporalExecutionService(session)
            service = ManifestsService(
                session,
                execution_service=execution_service,
                artifact_service=artifact_service,
            )

            user_id = uuid4()
            await service.upsert_manifest(name="demo", content=REGISTRY_MANIFEST)
            first = await service.submit_manifest_run(
                name="demo",
                action="run",
                options={"dryRun": True},
                user_id=user_id,
                idempotency_key="manifest-demo-repeat",
            )
            first_execution_count = (
                await execution_service.list_executions(
                    workflow_type=None,
                    state=None,
                    owner_id=None,
                    page_size=200,
                    next_page_token=None,
                )
            ).count

            second = await service.submit_manifest_run(
                name="demo",
                action="run",
                options={"dryRun": True},
                user_id=user_id,
                idempotency_key="manifest-demo-repeat",
            )
            second_execution_count = (
                await execution_service.list_executions(
                    workflow_type=None,
                    state=None,
                    owner_id=None,
                    page_size=200,
                    next_page_token=None,
                )
            ).count

            assert second.workflow_id == first.workflow_id
            assert second.run_id == first.run_id
            assert second.manifest_artifact_ref == first.manifest_artifact_ref
            assert second_execution_count == first_execution_count

            execution = await execution_service.describe_execution(first.workflow_id)
            assert execution.manifest_ref == first.manifest_artifact_ref


async def test_update_manifest_state_persists_checkpoint_and_run_metadata(
    tmp_path: Path,
) -> None:
    """update_manifest_state should persist state_json and optional run metadata."""

    async with manifest_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = ManifestsService(session)

            await service.upsert_manifest(name="demo", content=REGISTRY_MANIFEST)
            last_job_id = uuid4()
            updated = await service.update_manifest_state(
                name="demo",
                state_json={"docs": {"cursor": "abc", "docHashes": {"doc-1": "h1"}}},
                last_run_job_id=last_job_id,
                last_run_status="completed",
            )

            assert updated.state_json == {
                "docs": {"cursor": "abc", "docHashes": {"doc-1": "h1"}}
            }
            assert updated.last_run_job_id == last_job_id
            assert updated.last_run_status == "completed"
            assert updated.state_updated_at is not None


async def test_list_manifests_returns_ordered_and_limited_results(
    tmp_path: Path,
) -> None:
    """list_manifests should return manifests ordered by name and respect the limit."""

    async with manifest_db(tmp_path) as session_maker:
        async with session_maker() as session:
            service = ManifestsService(session)

            # Insert manifests in a random order
            # Note: upsert_manifest validates that the YAML metadata.name matches the arg.
            await service.upsert_manifest(
                name="zebra", content=_manifest_with_name("zebra")
            )
            await service.upsert_manifest(
                name="apple", content=_manifest_with_name("apple")
            )
            await service.upsert_manifest(
                name="mango", content=_manifest_with_name("mango")
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
            service = ManifestsService(session)

            await service.upsert_manifest(
                name="app-demo", content=_manifest_with_name("app-demo")
            )
            await service.upsert_manifest(
                name="app-test", content=_manifest_with_name("app-test")
            )
            await service.upsert_manifest(
                name="backend", content=_manifest_with_name("backend")
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
            service = ManifestsService(session)

            with pytest.raises(ManifestRegistryNotFoundError) as exc_info:
                await service.require_manifest("nonexistent-manifest")

            assert "Manifest 'nonexistent-manifest' was not found" in str(
                exc_info.value
            )
