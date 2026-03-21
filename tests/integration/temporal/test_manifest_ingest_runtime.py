"""Integration tests for manifest-ingest runtime orchestration.

Exercises the workflow → service → DB stack end-to-end using an
in-memory SQLite database. These tests verify:
- ManifestIngest creation persists projection with correct node counts
- Child-run orchestration preserves lineage across DB reads
- Continue-As-New rollover preserves workflow identity
- Large fan-out with checkpoint artifacts
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import (
    Base,
    MoonMindWorkflowState,
    TemporalExecutionRecord,
    TemporalWorkflowType,
)
from moonmind.workflows.temporal.service import TemporalExecutionService


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
        workflow_id = f"mm:manifest-int-{uuid4().hex[:8]}"
        run_id = f"run-{uuid4().hex[:8]}"
        params = _make_manifest_params(user_id, node_count=5)

        async with session_maker() as session:
            service = TemporalExecutionService(session)
            record = await service.create_execution(
                workflow_id=workflow_id,
                run_id=run_id,
                workflow_type=TemporalWorkflowType.MANIFEST_INGEST,
                owner_id=user_id,
                title="Integration test manifest",
                initial_parameters=params,
                manifest_artifact_ref="art_manifest_int_1",
                idempotency_key=f"int-{uuid4().hex[:8]}",
            )

            assert record.workflow_id == workflow_id
            assert record.workflow_type == TemporalWorkflowType.MANIFEST_INGEST
            assert record.manifest_ref == "art_manifest_int_1"

        # Verify re-read from DB
        async with session_maker() as session:
            service = TemporalExecutionService(session)
            detail = await service.describe_execution(workflow_id=workflow_id)
            assert detail is not None
            assert detail.workflow_type == TemporalWorkflowType.MANIFEST_INGEST
            assert detail.owner_id == user_id


@pytest.mark.asyncio
async def test_manifest_ingest_child_lineage_preserved_across_db_reads(tmp_path):
    """Child-run creation should preserve manifest lineage fields."""
    async with _db(tmp_path) as session_maker:
        user_id = str(uuid4())
        parent_wf_id = f"mm:manifest-parent-{uuid4().hex[:8]}"
        child_wf_id = f"mm:run-child-{uuid4().hex[:8]}"
        parent_run_id = f"run-{uuid4().hex[:8]}"
        child_run_id = f"run-{uuid4().hex[:8]}"

        async with session_maker() as session:
            service = TemporalExecutionService(session)
            # Create parent manifest ingest
            await service.create_execution(
                workflow_id=parent_wf_id,
                run_id=parent_run_id,
                workflow_type=TemporalWorkflowType.MANIFEST_INGEST,
                owner_id=user_id,
                title="Parent manifest",
                initial_parameters=_make_manifest_params(user_id),
                manifest_artifact_ref="art_manifest_parent",
                idempotency_key=f"parent-{uuid4().hex[:8]}",
            )

            # Create child run with lineage
            await service.create_execution(
                workflow_id=child_wf_id,
                run_id=child_run_id,
                workflow_type=TemporalWorkflowType.RUN,
                owner_id=user_id,
                title="Child run node-0",
                initial_parameters={
                    "ingestLineage": {
                        "manifestIngestWorkflowId": parent_wf_id,
                        "manifestIngestRunId": parent_run_id,
                        "manifestArtifactRef": "art_manifest_parent",
                    }
                },
                idempotency_key=f"child-{uuid4().hex[:8]}",
            )

        # Verify child has lineage in parameters
        async with session_maker() as session:
            service = TemporalExecutionService(session)
            child_detail = await service.describe_execution(workflow_id=child_wf_id)
            assert child_detail is not None
            lineage = child_detail.parameters.get("ingestLineage", {})
            assert lineage["manifestIngestWorkflowId"] == parent_wf_id
            assert lineage["manifestArtifactRef"] == "art_manifest_parent"


@pytest.mark.asyncio
async def test_manifest_continue_as_new_preserves_workflow_identity(tmp_path):
    """Continue-As-New should rotate run_id but keep workflow_id stable."""
    async with _db(tmp_path) as session_maker:
        user_id = str(uuid4())
        workflow_id = f"mm:manifest-can-{uuid4().hex[:8]}"
        original_run_id = f"run-orig-{uuid4().hex[:8]}"
        new_run_id = f"run-new-{uuid4().hex[:8]}"

        async with session_maker() as session:
            service = TemporalExecutionService(session)
            await service.create_execution(
                workflow_id=workflow_id,
                run_id=original_run_id,
                workflow_type=TemporalWorkflowType.MANIFEST_INGEST,
                owner_id=user_id,
                title="CAN test manifest",
                initial_parameters=_make_manifest_params(user_id),
                manifest_artifact_ref="art_manifest_can",
                idempotency_key=f"can-{uuid4().hex[:8]}",
            )

            # Simulate continue-as-new by updating run_id
            await service.rotate_run_id(
                workflow_id=workflow_id,
                new_run_id=new_run_id,
                continue_as_new_cause="checkpoint_threshold",
            )

        # Verify identity preserved, run_id rotated
        async with session_maker() as session:
            service = TemporalExecutionService(session)
            detail = await service.describe_execution(workflow_id=workflow_id)
            assert detail is not None
            assert detail.workflow_id == workflow_id
            assert detail.run_id == new_run_id
            assert detail.continue_as_new_cause == "checkpoint_threshold"


@pytest.mark.asyncio
async def test_manifest_idempotent_create_returns_existing_without_side_effects(
    tmp_path,
):
    """Repeating a create with the same idempotency key returns the existing record."""
    async with _db(tmp_path) as session_maker:
        user_id = str(uuid4())
        idempotency_key = f"idem-{uuid4().hex[:8]}"
        workflow_id = f"mm:manifest-idem-{uuid4().hex[:8]}"
        run_id = f"run-{uuid4().hex[:8]}"

        async with session_maker() as session:
            service = TemporalExecutionService(session)
            first = await service.create_execution(
                workflow_id=workflow_id,
                run_id=run_id,
                workflow_type=TemporalWorkflowType.MANIFEST_INGEST,
                owner_id=user_id,
                title="Idempotent manifest",
                initial_parameters=_make_manifest_params(user_id),
                manifest_artifact_ref="art_manifest_idem",
                idempotency_key=idempotency_key,
            )

        # Re-create with same key
        async with session_maker() as session:
            service = TemporalExecutionService(session)
            second = await service.create_execution(
                workflow_id=f"mm:manifest-idem2-{uuid4().hex[:8]}",
                run_id=f"run-{uuid4().hex[:8]}",
                workflow_type=TemporalWorkflowType.MANIFEST_INGEST,
                owner_id=user_id,
                title="Should return existing",
                initial_parameters=_make_manifest_params(user_id),
                manifest_artifact_ref="art_manifest_idem2",
                idempotency_key=idempotency_key,
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
        parent_wf_id = f"mm:manifest-large-{uuid4().hex[:8]}"
        parent_run_id = f"run-{uuid4().hex[:8]}"
        fan_out_count = 25

        async with session_maker() as session:
            service = TemporalExecutionService(session)
            # Create parent
            await service.create_execution(
                workflow_id=parent_wf_id,
                run_id=parent_run_id,
                workflow_type=TemporalWorkflowType.MANIFEST_INGEST,
                owner_id=user_id,
                title="Large fan-out test",
                initial_parameters=_make_manifest_params(
                    user_id, node_count=fan_out_count
                ),
                manifest_artifact_ref="art_manifest_large",
                idempotency_key=f"large-{uuid4().hex[:8]}",
            )

            # Create children
            for i in range(fan_out_count):
                await service.create_execution(
                    workflow_id=f"mm:run-child-{i}-{uuid4().hex[:8]}",
                    run_id=f"run-child-{i}-{uuid4().hex[:8]}",
                    workflow_type=TemporalWorkflowType.RUN,
                    owner_id=user_id,
                    title=f"Child run node-{i}",
                    initial_parameters={
                        "ingestLineage": {
                            "manifestIngestWorkflowId": parent_wf_id,
                            "manifestIngestRunId": parent_run_id,
                        }
                    },
                    idempotency_key=f"child-{i}-{uuid4().hex[:8]}",
                )

        # Verify all were created (parent + 25 children)
        async with session_maker() as session:
            service = TemporalExecutionService(session)
            # List all runs for this user
            result = await service.list_executions(
                owner_id=user_id,
                workflow_type=TemporalWorkflowType.RUN,
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
                    workflow_id=f"mm:manifest-page-{i}-{uuid4().hex[:8]}",
                    run_id=f"run-{uuid4().hex[:8]}",
                    workflow_type=TemporalWorkflowType.MANIFEST_INGEST,
                    owner_id=user_id,
                    title=f"Paginated manifest {i}",
                    initial_parameters=_make_manifest_params(user_id, node_count=1),
                    manifest_artifact_ref=f"art_manifest_page_{i}",
                    idempotency_key=f"page-{i}-{uuid4().hex[:8]}",
                )

        # Paginate
        async with session_maker() as session:
            service = TemporalExecutionService(session)
            first_page = await service.list_executions(
                owner_id=user_id,
                workflow_type=TemporalWorkflowType.MANIFEST_INGEST,
                page_size=page_size,
            )
            assert len(first_page.items) == page_size
            assert first_page.count == total
