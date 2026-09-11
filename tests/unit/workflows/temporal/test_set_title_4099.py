"""Service-level regression tests for MoonLadderStudios/MoonMind#4099.

Exercises the repaired public ``SetTitle`` path on the canonical record:
shared validation, explicit provenance, revision guard, ``mm_title`` search
tokens, and no-op idempotency without touching the record.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import (
    Base,
    MoonMindWorkflowState,
    TemporalExecutionCanonicalRecord,
)
from moonmind.config.settings import settings
from moonmind.workflows.temporal.service import (
    TemporalExecutionService,
    TemporalExecutionValidationError,
)


def _valid_user_workflow_parameters() -> dict[str, object]:
    return {"workflow": {"instructions": "Test workflow fixture."}}


@pytest.fixture
def mock_client_adapter():
    adapter = MagicMock()
    adapter.start_workflow = AsyncMock()
    adapter.describe_workflow = AsyncMock(return_value=None)
    adapter.update_workflow = AsyncMock()
    adapter.signal_workflow = AsyncMock()
    adapter.cancel_workflow = AsyncMock()
    adapter.terminate_workflow = AsyncMock()
    return adapter


@asynccontextmanager
async def temporal_db(tmp_path):
    original_artifact_backend = settings.workflow.temporal_artifact_backend
    original_artifact_root = settings.workflow.temporal_artifact_root
    settings.workflow.temporal_artifact_backend = "local_fs"
    settings.workflow.temporal_artifact_root = str(tmp_path / "artifacts")
    db_url = f"sqlite+aiosqlite:///{tmp_path}/temporal_lifecycle.db"
    engine = create_async_engine(db_url, future=True)
    session_factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    try:
        async with session_factory() as session:
            yield session
    finally:
        await engine.dispose()
        settings.workflow.temporal_artifact_backend = original_artifact_backend
        settings.workflow.temporal_artifact_root = original_artifact_root


async def _create(service, **overrides):
    kwargs = {
        "workflow_type": "MoonMind.UserWorkflow",
        "owner_id": uuid4(),
        "title": "GitHub Issue Search and Implement",
        "input_artifact_ref": None,
        "plan_artifact_ref": None,
        "manifest_artifact_ref": None,
        "failure_policy": None,
        "initial_parameters": _valid_user_workflow_parameters(),
        "idempotency_key": None,
    }
    kwargs.update(overrides)
    return await service.create_execution(**kwargs)


@pytest.mark.asyncio
async def test_create_execution_seeds_shared_title_state(tmp_path, mock_client_adapter):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session, client_adapter=mock_client_adapter)
        created = await _create(service)

        # An explicit caller title stays explicit (and protected from
        # automatic enrichment) with the frozen base equal to itself.
        assert created.memo["title"] == "GitHub Issue Search and Implement"
        assert created.memo["titleBase"] == "GitHub Issue Search and Implement"
        assert created.memo["titleProvenance"] == "user_explicit"
        assert created.memo["titleSource"] == "user_explicit"
        assert created.memo["titleRevision"] == 0


def _opt_in_search_parameters() -> dict[str, object]:
    return {
        "workflow": {
            "instructions": "Search GitHub issues, then implement the match.",
            "taskTemplate": {
                "slug": "github-issue-search-and-implement",
                "scope": "global",
            },
            "titleEnrichment": {
                "enabled": True,
                "provider": "github",
                "sourceTool": "github.load_issue_preset_brief",
                "targetStyle": "base-colon-hash",
                "presetTitle": "GitHub Issue Search and Implement",
                "presetSlug": "github-issue-search-and-implement",
            },
        }
    }


@pytest.mark.asyncio
async def test_create_execution_freezes_preset_base_for_opt_in_search(
    tmp_path, mock_client_adapter
):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session, client_adapter=mock_client_adapter)
        created = await _create(
            service,
            title=None,
            initial_parameters=_opt_in_search_parameters(),
        )

        # Pending selection shows the frozen preset base unchanged, eligible
        # for later "<base>: #<number>" enrichment.
        assert created.memo["title"] == "GitHub Issue Search and Implement"
        assert created.memo["titleBase"] == "GitHub Issue Search and Implement"
        assert created.memo["titleProvenance"] == "generated"
        assert created.memo["titleSource"] == "preset_template"
        assert created.memo["titleRevision"] == 0


@pytest.mark.asyncio
async def test_create_execution_explicit_title_wins_over_preset_base(
    tmp_path, mock_client_adapter
):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session, client_adapter=mock_client_adapter)
        created = await _create(
            service,
            title="Operator choice",
            initial_parameters=_opt_in_search_parameters(),
        )

        assert created.memo["title"] == "Operator choice"
        assert created.memo["titleBase"] == "Operator choice"
        assert created.memo["titleProvenance"] == "user_explicit"


@pytest.mark.asyncio
async def test_set_title_repairs_memo_provenance_revision_and_tokens(
    tmp_path, mock_client_adapter
):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session, client_adapter=mock_client_adapter)
        created = await _create(service)

        response = await service.update_execution(
            workflow_id=created.workflow_id,
            update_name="SetTitle",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            parameters_patch=None,
            title="My manually chosen workflow title",
            idempotency_key="set-title-4099",
        )

        assert response["accepted"] is True
        updated = await service.describe_execution(created.workflow_id)
        assert updated.memo["title"] == "My manually chosen workflow title"
        assert updated.memo["titleSource"] == "user_explicit"
        assert updated.memo["titleProvenance"] == "user_explicit"
        assert updated.memo["titleRevision"] == 1
        assert updated.memo["titleBase"] == "GitHub Issue Search and Implement"
        assert updated.search_attributes["mm_title"] == [
            "my",
            "manually",
            "chosen",
            "workflow",
            "title",
        ]
        mock_client_adapter.update_workflow.assert_awaited_once()
        args = mock_client_adapter.update_workflow.await_args.args
        assert args[1] == "SetTitle"
        assert args[2]["title"] == "My manually chosen workflow title"


@pytest.mark.asyncio
async def test_set_title_noop_does_not_touch_record(tmp_path, mock_client_adapter):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session, client_adapter=mock_client_adapter)
        created = await _create(service, title="Operator choice")

        first = await service.update_execution(
            workflow_id=created.workflow_id,
            update_name="SetTitle",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            parameters_patch=None,
            title="Operator choice",
            idempotency_key="set-title-first",
        )
        assert first["accepted"] is True
        before = await service.describe_execution(created.workflow_id)
        before_updated_at = before.updated_at

        second = await service.update_execution(
            workflow_id=created.workflow_id,
            update_name="SetTitle",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            parameters_patch=None,
            title="Operator choice",
            idempotency_key="set-title-second",
        )
        assert second["accepted"] is True
        after = await service.describe_execution(created.workflow_id)
        assert after.memo["titleRevision"] == before.memo["titleRevision"]
        assert after.updated_at == before_updated_at


@pytest.mark.asyncio
async def test_set_title_rejects_empty_and_unsafe_titles(
    tmp_path, mock_client_adapter
):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session, client_adapter=mock_client_adapter)
        created = await _create(service)

        for bad_title in ("", "   ", "bad\x00title"):
            with pytest.raises(TemporalExecutionValidationError):
                await service.update_execution(
                    workflow_id=created.workflow_id,
                    update_name="SetTitle",
                    input_artifact_ref=None,
                    plan_artifact_ref=None,
                    parameters_patch=None,
                    title=bad_title,
                    idempotency_key=f"bad-{len(bad_title)}",
                )
        unchanged = await service.describe_execution(created.workflow_id)
        assert unchanged.memo["title"] == "GitHub Issue Search and Implement"
        assert unchanged.state is MoonMindWorkflowState.INITIALIZING


@pytest.mark.asyncio
async def test_terminal_rerun_restarts_generated_title_from_base(
    tmp_path, mock_client_adapter
):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session, client_adapter=mock_client_adapter)
        created = await _create(
            service,
            title=None,
            initial_parameters=_opt_in_search_parameters(),
        )
        assert created.memo["title"] == "GitHub Issue Search and Implement"

        # Simulate the "<base>: #<number>" enrichment applied during the run.
        canonical = await session.get(
            TemporalExecutionCanonicalRecord, created.workflow_id
        )
        memo = dict(canonical.memo)
        memo["title"] = "GitHub Issue Search and Implement: #4054"
        memo["titleRevision"] = 1
        canonical.memo = memo
        await session.commit()

        await service.cancel_execution(
            workflow_id=created.workflow_id,
            reason="done",
            graceful=True,
        )
        response = await service.update_execution(
            workflow_id=created.workflow_id,
            update_name="RequestRerun",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            parameters_patch=None,
            title=None,
            idempotency_key="rerun-from-base-4099",
        )

        assert response["accepted"] is True
        rerun = await service.describe_execution(response["workflow_id"])
        assert rerun.workflow_id != created.workflow_id
        assert rerun.memo["title"] == "GitHub Issue Search and Implement"
        assert rerun.memo["titleBase"] == "GitHub Issue Search and Implement"
        assert rerun.memo["titleProvenance"] == "generated"
        assert rerun.memo["titleRevision"] == 0


@pytest.mark.asyncio
async def test_terminal_rerun_preserves_explicit_title(
    tmp_path, mock_client_adapter
):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session, client_adapter=mock_client_adapter)
        created = await _create(
            service,
            title="Operator choice",
            initial_parameters=_opt_in_search_parameters(),
        )
        assert created.memo["titleProvenance"] == "user_explicit"

        await service.cancel_execution(
            workflow_id=created.workflow_id,
            reason="done",
            graceful=True,
        )
        response = await service.update_execution(
            workflow_id=created.workflow_id,
            update_name="RequestRerun",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            parameters_patch=None,
            title=None,
            idempotency_key="rerun-explicit-4099",
        )

        assert response["accepted"] is True
        rerun = await service.describe_execution(response["workflow_id"])
        assert rerun.memo["title"] == "Operator choice"
        assert rerun.memo["titleProvenance"] == "user_explicit"


@pytest.mark.asyncio
async def test_set_title_same_text_still_marks_explicit(tmp_path, mock_client_adapter):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session, client_adapter=mock_client_adapter)
        created = await _create(
            service,
            title=None,
            initial_parameters=_opt_in_search_parameters(),
        )
        assert created.memo["titleProvenance"] == "generated"

        response = await service.update_execution(
            workflow_id=created.workflow_id,
            update_name="SetTitle",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            parameters_patch=None,
            title="GitHub Issue Search and Implement",
            idempotency_key="same-text-explicit",
        )
        assert response["accepted"] is True
        updated = await service.describe_execution(created.workflow_id)
        assert updated.memo["title"] == "GitHub Issue Search and Implement"
        assert updated.memo["titleProvenance"] == "user_explicit"
        assert updated.memo["titleRevision"] == 1
