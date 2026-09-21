"""Historical Manifest reads through the real ORM and response schemas.

MoonLadderStudios/MoonMind#4189 (MR2/MR6): complements
``test_manifest_historical_reads_4189.py`` (serializer doubles carrying
degraded lifecycle values) and the #4188 gap-closure tests (valid-state
rows through entry decoders and mutation rejection). These tests persist
old-release ``MoonMind.ManifestIngest`` rows in a real database, read
them back through ORM enum decoding, and run the full detail + list
serializers behind the list/detail response schemas. That proves
historical readability is independent of current launchable types
without retaining the workflow class, admitting arbitrary unknown
types, or synthesizing system ownership.

Lost-evidence rule (issue REQ-02): a terminal old-release row whose
summary was never written (or arrived late/absent) must stay readable
with its stored identity/refs/timestamps intact while unavailable
evidence stays unavailable — never fabricated completion.
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


@asynccontextmanager
async def _projection_db(tmp_path: Path):
    from api_service.db.models import Base
    from moonmind.config.settings import settings

    original_backend = settings.workflow.temporal_artifact_backend
    original_root = settings.workflow.temporal_artifact_root
    settings.workflow.temporal_artifact_backend = "local_fs"
    settings.workflow.temporal_artifact_root = str(tmp_path / "artifacts")
    db_url = f"sqlite+aiosqlite:///{tmp_path}/manifest_projection_4189.db"
    engine = create_async_engine(db_url, future=True)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        async with factory() as session:
            yield session
    finally:
        await engine.dispose()
        settings.workflow.temporal_artifact_backend = original_backend
        settings.workflow.temporal_artifact_root = original_root


async def _insert_historical_manifest_row(
    session,
    *,
    state,
    close_status,
    finish_summary_json=None,
    finish_outcome_code=None,
):
    """Persist an old-release ManifestIngest execution row (lost summary by default)."""
    from api_service.db.models import (
        TemporalExecutionOwnerType,
        TemporalExecutionRecord,
        TemporalWorkflowType,
    )

    now = datetime.now(UTC)
    workflow_id = f"mm:historical-manifest:{uuid4().hex[:8]}"
    run_id = f"run-historical-{uuid4().hex[:8]}"
    record = TemporalExecutionRecord(
        workflow_id=workflow_id,
        run_id=run_id,
        namespace="moonmind",
        workflow_type=TemporalWorkflowType.MANIFEST_INGEST,
        owner_id="user-123",
        owner_type=TemporalExecutionOwnerType.USER,
        state=state,
        close_status=close_status,
        entry="manifest",
        search_attributes={
            "mm_owner_id": "user-123",
            "mm_owner_type": "user",
            "mm_entry": "manifest",
            "mm_state": "completed",
            "mm_repo": "Moon/Mind",
        },
        memo={
            "title": "Historical manifest ingest",
            "summary": "Completed on the old release.",
        },
        artifact_refs=["art_historical_1"],
        finish_outcome_code=finish_outcome_code,
        finish_summary_json=finish_summary_json,
        input_ref=None,
        plan_ref="art_plan_historical",
        manifest_ref="artifact://manifest/historical",
        parameters={
            "requestedBy": {"type": "user", "id": "user-1"},
            "manifestNodes": [{"nodeId": "node-a", "state": "succeeded"}],
        },
        created_at=now,
        started_at=now,
        updated_at=now,
        closed_at=now,
    )
    session.add(record)
    await session.commit()
    await session.refresh(record)
    return record


@pytest.mark.asyncio
async def test_detail_read_preserves_identity_and_admin_lineage(
    tmp_path: Path,
) -> None:
    """ORM-decoded historical rows keep identity/refs; admins keep lineage."""
    from api_service.api.routers.executions import _serialize_execution
    from api_service.db.models import (
        MoonMindWorkflowState,
        TemporalExecutionCloseStatus,
        TemporalExecutionRecord,
    )
    from moonmind.config.settings import settings

    settings.temporal_dashboard.actions_enabled = True
    admin = SimpleNamespace(is_superuser=True)
    async with _projection_db(tmp_path) as session:
        inserted = await _insert_historical_manifest_row(
            session,
            state=MoonMindWorkflowState.COMPLETED,
            close_status=TemporalExecutionCloseStatus.COMPLETED,
        )
        stored = await session.get(
            TemporalExecutionRecord, inserted.workflow_id
        )
        assert stored is not None
        # ORM enum decoding preserves the retired type without re-registering it.
        assert stored.workflow_type.value == "MoonMind.ManifestIngest"

        model = _serialize_execution(stored, user=admin)

        assert model.workflow_id == inserted.workflow_id
        assert model.run_id == inserted.run_id
        assert model.workflow_type == "MoonMind.ManifestIngest"
        assert model.entry == "manifest"
        assert model.owner_id == "user-123"
        assert model.dashboard_status == "completed"
        assert model.status == "completed"
        # Immutable lineage refs survive for authorized readers.
        assert model.manifest_artifact_ref == "artifact://manifest/historical"
        assert model.plan_artifact_ref == "art_plan_historical"
        # Timestamps are the stored ones, not "now".
        assert model.created_at == inserted.created_at
        assert model.closed_at == inserted.closed_at
        # The retired row never re-enables recreate affordances.
        assert model.actions.can_rerun is False
        assert model.actions.can_edit_for_rerun is False
        assert model.actions.can_update_inputs is False


@pytest.mark.asyncio
async def test_detail_read_lost_summary_stays_unavailable(
    tmp_path: Path,
) -> None:
    """A terminal row without summary evidence must not fabricate completion."""
    from api_service.api.routers.executions import _serialize_execution
    from api_service.db.models import (
        MoonMindWorkflowState,
        TemporalExecutionCloseStatus,
        TemporalExecutionRecord,
    )
    from moonmind.config.settings import settings

    settings.temporal_dashboard.actions_enabled = True
    admin = SimpleNamespace(is_superuser=True)
    async with _projection_db(tmp_path) as session:
        inserted = await _insert_historical_manifest_row(
            session,
            state=MoonMindWorkflowState.COMPLETED,
            close_status=TemporalExecutionCloseStatus.COMPLETED,
            finish_summary_json=None,
            finish_outcome_code=None,
        )
        stored = await session.get(
            TemporalExecutionRecord, inserted.workflow_id
        )
        assert stored is not None

        model = _serialize_execution(stored, user=admin)

        assert model.dashboard_status == "completed"
        # No summary was ever written: no summary artifact or outcome code
        # may be synthesized to fill the gap.
        assert model.summary_artifact_ref is None
        assert model.finish_outcome_code is None
        # Stored evidence that does exist is still presented.
        assert model.artifact_refs == ["art_historical_1"]
        assert model.manifest_artifact_ref == "artifact://manifest/historical"


@pytest.mark.asyncio
async def test_detail_read_withholds_raw_refs_from_non_admin(
    tmp_path: Path,
) -> None:
    """Current owner/raw-access policy applies to historical rows."""
    from api_service.api.routers.executions import _serialize_execution
    from api_service.db.models import (
        MoonMindWorkflowState,
        TemporalExecutionCloseStatus,
        TemporalExecutionRecord,
    )
    from moonmind.config.settings import settings

    settings.temporal_dashboard.actions_enabled = True
    reader = SimpleNamespace(is_superuser=False)
    async with _projection_db(tmp_path) as session:
        inserted = await _insert_historical_manifest_row(
            session,
            state=MoonMindWorkflowState.COMPLETED,
            close_status=TemporalExecutionCloseStatus.COMPLETED,
        )
        stored = await session.get(
            TemporalExecutionRecord, inserted.workflow_id
        )
        assert stored is not None

        model = _serialize_execution(stored, user=reader)

        assert model.entry == "manifest"
        assert model.workflow_id == inserted.workflow_id
        assert model.manifest_artifact_ref is None
        assert model.run_index_artifact_ref is None
        assert model.checkpoint_artifact_ref is None


@pytest.mark.asyncio
async def test_list_read_keeps_historical_row_visible(
    tmp_path: Path,
) -> None:
    """The list projection keeps retired rows instead of hiding them."""
    from api_service.api.routers.executions import _serialize_execution_list_item
    from api_service.db.models import (
        MoonMindWorkflowState,
        TemporalExecutionCloseStatus,
        TemporalExecutionRecord,
    )

    async with _projection_db(tmp_path) as session:
        inserted = await _insert_historical_manifest_row(
            session,
            state=MoonMindWorkflowState.FAILED,
            close_status=TemporalExecutionCloseStatus.FAILED,
        )
        stored = await session.get(
            TemporalExecutionRecord, inserted.workflow_id
        )
        assert stored is not None

        item = _serialize_execution_list_item(stored)

        assert item.workflow_id == inserted.workflow_id
        assert item.workflow_type == "MoonMind.ManifestIngest"
        assert item.entry == "manifest"
        assert item.dashboard_status == "failed"
        assert item.status == "failed"
