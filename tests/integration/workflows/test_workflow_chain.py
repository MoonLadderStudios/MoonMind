from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db import base as db_base
from api_service.db.models import Base
from moonmind.config.settings import settings
from moonmind.workflows import SpecWorkflowRepository, trigger_spec_workflow_run
from moonmind.workflows.speckit_celery import celery_app
from moonmind.workflows.speckit_celery import models as workflow_models


@pytest.mark.asyncio
async def test_trigger_workflow_chain(tmp_path, monkeypatch):
    """End-to-end test exercising the Celery chain in eager mode."""

    # Configure database to use SQLite for the test run
    db_url = f"sqlite+aiosqlite:///{tmp_path}/workflow.db"
    db_base.DATABASE_URL = db_url
    db_base.engine = create_async_engine(db_url, future=True)
    db_base.async_session_maker = sessionmaker(
        db_base.engine, class_=AsyncSession, expire_on_commit=False
    )

    async with db_base.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Ensure Celery executes tasks synchronously for determinism
    monkeypatch.setitem(celery_app.conf, "task_always_eager", True)
    monkeypatch.setitem(celery_app.conf, "task_eager_propagates", True)

    # Prepare Spec Workflow settings for the test repository
    feature_key = "001-celery-chain-workflow"
    specs_dir = tmp_path / "specs" / feature_key
    specs_dir.mkdir(parents=True)
    (specs_dir / "tasks.md").write_text(
        """
## Phase 3 – User Story 1
- [x] T001 Completed bootstrap
- [ ] T999 Example automation task
""".strip()
        + "\n",
        encoding="utf-8",
    )

    artifacts_root = tmp_path / "artifacts"
    monkeypatch.setattr(settings.spec_workflow, "test_mode", True, raising=False)
    monkeypatch.setattr(
        settings.spec_workflow, "repo_root", str(tmp_path), raising=False
    )
    monkeypatch.setattr(settings.spec_workflow, "tasks_root", "specs", raising=False)
    monkeypatch.setattr(
        settings.spec_workflow, "artifacts_root", str(artifacts_root), raising=False
    )
    monkeypatch.setattr(
        settings.spec_workflow, "default_feature_key", feature_key, raising=False
    )

    triggered = await trigger_spec_workflow_run(feature_key=feature_key)

    async with db_base.async_session_maker() as session:
        repo = SpecWorkflowRepository(session)
        run = await repo.get_run(triggered.run_id, with_relations=True)

    assert run is not None
    assert run.status is workflow_models.SpecWorkflowRunStatus.SUCCEEDED
    assert run.phase is workflow_models.SpecWorkflowRunPhase.COMPLETE
    assert run.branch_name is not None
    assert run.pr_url is not None
    assert run.codex_task_id is not None
    assert run.artifacts
    assert run.task_states

    state_names = {state.task_name: state.status for state in run.task_states}
    assert (
        state_names["discover_next_phase"]
        is workflow_models.SpecWorkflowTaskStatus.SUCCEEDED
    )
    assert (
        state_names["submit_codex_job"]
        is workflow_models.SpecWorkflowTaskStatus.SUCCEEDED
    )
    assert (
        state_names["apply_and_publish"]
        is workflow_models.SpecWorkflowTaskStatus.SUCCEEDED
    )

    # Artifacts should be written to the configured directory
    for artifact in run.artifacts:
        path = Path(artifact.path)
        assert path.exists()
