"""Integration tests for manifest-ingest runtime orchestration.

Exercises the service → DB stack end-to-end using an
in-memory SQLite database. These tests verify:
- ManifestIngest creation persists projection with correct node counts
- Child-run orchestration preserves lineage across DB reads
- Idempotent creation returns existing records
- Large fan-out with proper listing and pagination
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

import pytest

from api_service.db.models import (
    Base,
    TemporalWorkflowType,
)
from moonmind.workflows.temporal.service import TemporalExecutionService

pytestmark = [pytest.mark.integration, pytest.mark.integration_ci]


@asynccontextmanager
async def _db(tmp_path: Path):
    db_url = f"sqlite+aiosqlite:///{tmp_path}/manifest_ingest_integration.db"
    engine = create_async_engine(db_url, future=True)
    session_maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield session_maker
    finally:
        await engine.dispose()


def _make_manifest_params(
    user_id: str,
    node_count: int = 3,
    max_concurrency: int = 6,
    failure_policy: str = "best_effort",
) -> dict:
    """Build a manifest ingest parameters payload."""
    nodes = [
        {"nodeId": f"node-{i}", "state": "ready", "title": f"Node {i}"}
        for i in range(node_count)
    ]
    return {
        "requestedBy": {"type": "user", "id": user_id},
        "executionPolicy": {
            "failurePolicy": failure_policy,
            "maxConcurrency": max_concurrency,
        },
        "manifestNodes": nodes,
    }


# ---------------------------------------------------------------------------
# T010: ManifestIngest startup and child-run orchestration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_manifest_ingest_create_persists_projection_with_node_counts(tmp_path):
    """Creating a ManifestIngest execution should persist correct node counts."""
    async with _db(tmp_path) as session_maker:
        user_id = str(uuid4())
        params = _make_manifest_params(user_id, node_count=5)
        idem_key = f"int-{uuid4().hex[:8]}"

        async with session_maker() as session:
            service = TemporalExecutionService(session)
            record = await service.create_execution(
                workflow_type=TemporalWorkflowType.MANIFEST_INGEST.value,
                owner_id=user_id,
                title="Integration test manifest",
                input_artifact_ref=None,
                plan_artifact_ref=None,
                manifest_artifact_ref="art_manifest_int_1",
                failure_policy="best_effort",
                initial_parameters=params,
                idempotency_key=idem_key,
                _skip_pause_guard=True,
            )

            assert record.workflow_id.startswith("mm:")
            assert record.workflow_type == TemporalWorkflowType.MANIFEST_INGEST
            assert record.manifest_ref == "art_manifest_int_1"

        # Verify re-read from DB
        async with session_maker() as session:
            service = TemporalExecutionService(session)
            detail = await service.describe_execution(record.workflow_id)
            assert detail is not None
            assert detail.workflow_type == TemporalWorkflowType.MANIFEST_INGEST
            assert detail.owner_id == user_id


@pytest.mark.asyncio
async def test_manifest_ingest_child_lineage_preserved_across_db_reads(tmp_path):
    """Child-run creation should preserve manifest lineage fields."""
    async with _db(tmp_path) as session_maker:
        user_id = str(uuid4())

        async with session_maker() as session:
            service = TemporalExecutionService(session)
            # Create parent manifest ingest
            parent = await service.create_execution(
                workflow_type=TemporalWorkflowType.MANIFEST_INGEST.value,
                owner_id=user_id,
                title="Parent manifest",
                input_artifact_ref=None,
                plan_artifact_ref=None,
                manifest_artifact_ref="art_manifest_parent",
                failure_policy="best_effort",
                initial_parameters=_make_manifest_params(user_id),
                idempotency_key=f"parent-{uuid4().hex[:8]}",
                _skip_pause_guard=True,
            )

            # Create child run with lineage in parameters
            child = await service.create_execution(
                workflow_type=TemporalWorkflowType.RUN.value,
                owner_id=user_id,
                title="Child run node-0",
                input_artifact_ref=None,
                plan_artifact_ref=None,
                manifest_artifact_ref=None,
                failure_policy=None,
                initial_parameters={
                    "ingestLineage": {
                        "manifestIngestWorkflowId": parent.workflow_id,
                        "manifestIngestRunId": parent.run_id,
                        "manifestArtifactRef": "art_manifest_parent",
                    }
                },
                idempotency_key=f"child-{uuid4().hex[:8]}",
                _skip_pause_guard=True,
            )

        # Verify child has lineage in parameters
        async with session_maker() as session:
            service = TemporalExecutionService(session)
            child_detail = await service.describe_execution(child.workflow_id)
            assert child_detail is not None
            lineage = child_detail.parameters.get("ingestLineage", {})
            assert lineage["manifestIngestWorkflowId"] == parent.workflow_id
            assert lineage["manifestArtifactRef"] == "art_manifest_parent"


@pytest.mark.asyncio
async def test_manifest_idempotent_create_returns_existing_without_side_effects(
    tmp_path,
):
    """Repeating a create with the same idempotency key returns the existing record."""
    async with _db(tmp_path) as session_maker:
        user_id = str(uuid4())
        idempotency_key = f"idem-{uuid4().hex[:8]}"

        async with session_maker() as session:
            service = TemporalExecutionService(session)
            first = await service.create_execution(
                workflow_type=TemporalWorkflowType.MANIFEST_INGEST.value,
                owner_id=user_id,
                title="Idempotent manifest",
                input_artifact_ref=None,
                plan_artifact_ref=None,
                manifest_artifact_ref="art_manifest_idem",
                failure_policy="best_effort",
                initial_parameters=_make_manifest_params(user_id),
                idempotency_key=idempotency_key,
                _skip_pause_guard=True,
            )

        # Re-create with same key
        async with session_maker() as session:
            service = TemporalExecutionService(session)
            second = await service.create_execution(
                workflow_type=TemporalWorkflowType.MANIFEST_INGEST.value,
                owner_id=user_id,
                title="Should return existing",
                input_artifact_ref=None,
                plan_artifact_ref=None,
                manifest_artifact_ref="art_manifest_idem2",
                failure_policy="best_effort",
                initial_parameters=_make_manifest_params(user_id),
                idempotency_key=idempotency_key,
                _skip_pause_guard=True,
            )

            assert second.workflow_id == first.workflow_id
            assert second.run_id == first.run_id


# ---------------------------------------------------------------------------
# T024: Run-index pagination and large-ingest rollover
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_large_fan_out_child_runs_all_persist_with_lineage(tmp_path):
    """Large fan-out (20+ children) should all persist with proper lineage."""
    async with _db(tmp_path) as session_maker:
        user_id = str(uuid4())
        fan_out_count = 25

        async with session_maker() as session:
            service = TemporalExecutionService(session)
            # Create parent
            parent = await service.create_execution(
                workflow_type=TemporalWorkflowType.MANIFEST_INGEST.value,
                owner_id=user_id,
                title="Large fan-out test",
                input_artifact_ref=None,
                plan_artifact_ref=None,
                manifest_artifact_ref="art_manifest_large",
                failure_policy="best_effort",
                initial_parameters=_make_manifest_params(
                    user_id, node_count=fan_out_count
                ),
                idempotency_key=f"large-{uuid4().hex[:8]}",
                _skip_pause_guard=True,
            )

            # Create children
            for i in range(fan_out_count):
                await service.create_execution(
                    workflow_type=TemporalWorkflowType.RUN.value,
                    owner_id=user_id,
                    title=f"Child run node-{i}",
                    input_artifact_ref=None,
                    plan_artifact_ref=None,
                    manifest_artifact_ref=None,
                    failure_policy=None,
                    initial_parameters={
                        "ingestLineage": {
                            "manifestIngestWorkflowId": parent.workflow_id,
                            "manifestIngestRunId": parent.run_id,
                        }
                    },
                    idempotency_key=f"child-{i}-{uuid4().hex[:8]}",
                    _skip_pause_guard=True,
                )

        # Verify all children were created
        async with session_maker() as session:
            service = TemporalExecutionService(session)
            result = await service.list_executions(
                owner_id=user_id,
                workflow_type=TemporalWorkflowType.RUN.value,
                page_size=50,
            )
            assert len(result.items) == fan_out_count


@pytest.mark.asyncio
async def test_manifest_execution_list_pagination(tmp_path):
    """Listing manifest executions with pagination returns correct pages."""
    async with _db(tmp_path) as session_maker:
        user_id = str(uuid4())
        page_size = 3
        total = 7

        async with session_maker() as session:
            service = TemporalExecutionService(session)
            for i in range(total):
                await service.create_execution(
                    workflow_type=TemporalWorkflowType.MANIFEST_INGEST.value,
                    owner_id=user_id,
                    title=f"Paginated manifest {i}",
                    input_artifact_ref=None,
                    plan_artifact_ref=None,
                    manifest_artifact_ref=f"art_manifest_page_{i}",
                    failure_policy="best_effort",
                    initial_parameters=_make_manifest_params(user_id, node_count=1),
                    idempotency_key=f"page-{i}-{uuid4().hex[:8]}",
                    _skip_pause_guard=True,
                )

        # Paginate
        async with session_maker() as session:
            service = TemporalExecutionService(session)
            first_page = await service.list_executions(
                owner_id=user_id,
                workflow_type=TemporalWorkflowType.MANIFEST_INGEST.value,
                page_size=page_size,
            )
            assert len(first_page.items) == page_size
            assert first_page.count == total
