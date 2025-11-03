from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db import base as db_base
from api_service.db.models import Base
from moonmind.config.settings import settings
from moonmind.workflows import (
    SpecWorkflowRepository,
    retry_spec_workflow_run,
    trigger_spec_workflow_run,
)
from moonmind.workflows.adapters.github_client import GitHubPublishResult
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


@pytest.mark.asyncio
async def test_retry_failed_workflow_chain(tmp_path, monkeypatch):
    """Ensure a failed workflow run can be retried from the failing task."""

    db_url = f"sqlite+aiosqlite:///{tmp_path}/workflow_retry.db"
    db_base.DATABASE_URL = db_url
    db_base.engine = create_async_engine(db_url, future=True)
    db_base.async_session_maker = sessionmaker(
        db_base.engine, class_=AsyncSession, expire_on_commit=False
    )

    async with db_base.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    monkeypatch.setitem(celery_app.conf, "task_always_eager", True)
    monkeypatch.setitem(celery_app.conf, "task_eager_propagates", True)

    feature_key = "001-celery-chain-workflow"
    specs_dir = tmp_path / "specs" / feature_key
    specs_dir.mkdir(parents=True)
    (specs_dir / "tasks.md").write_text(
        """
## Phase 3 – User Story 1
- [ ] T123 Trigger retry path
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

    fail_state = {"calls": 0}

    def _fake_github_client():
        class _Client:
            def publish(
                self,
                *,
                feature_key: str,
                task_identifier: str,
                patch_path,
                artifacts_dir,
            ) -> GitHubPublishResult:
                fail_state["calls"] += 1
                if fail_state["calls"] == 1:
                    raise RuntimeError("simulated publish failure")

                branch_name = f"{feature_key}/{(task_identifier or 'retry').lower()}"
                pr_url = f"https://example.com/{feature_key}/retry"
                response_path = (
                    artifacts_dir / f"{branch_name.replace('/', '_')}_retry_pr.json"
                )
                response_path.write_text("{}", encoding="utf-8")
                return GitHubPublishResult(
                    branch_name=branch_name,
                    pr_url=pr_url,
                    response_path=response_path,
                )

        return _Client()

    monkeypatch.setattr(
        "moonmind.workflows.speckit_celery.tasks._build_github_client",
        _fake_github_client,
    )

    triggered = await trigger_spec_workflow_run(feature_key=feature_key)

    async with db_base.async_session_maker() as session:
        repo = SpecWorkflowRepository(session)
        failed_run = await repo.get_run(triggered.run_id, with_relations=True)

    assert failed_run is not None
    assert failed_run.status is workflow_models.SpecWorkflowRunStatus.FAILED
    publish_attempts = [
        state
        for state in failed_run.task_states
        if state.task_name == "apply_and_publish"
    ]
    assert publish_attempts
    assert any(
        state.status is workflow_models.SpecWorkflowTaskStatus.FAILED
        for state in publish_attempts
    )
    assert fail_state["calls"] == 1

    retried = await retry_spec_workflow_run(
        failed_run.id, notes="Retry after fixing credentials"
    )

    async with db_base.async_session_maker() as session:
        repo = SpecWorkflowRepository(session)
        completed = await repo.get_run(retried.run_id, with_relations=True)

    assert completed is not None
    assert completed.status is workflow_models.SpecWorkflowRunStatus.SUCCEEDED
    publish_attempts = [
        state
        for state in completed.task_states
        if state.task_name == "apply_and_publish"
    ]
    assert any(state.attempt == 2 for state in publish_attempts)
    assert any(
        state.attempt == 1
        and state.status is workflow_models.SpecWorkflowTaskStatus.FAILED
        for state in publish_attempts
    )
    assert any(
        state.attempt == 2
        and state.status is workflow_models.SpecWorkflowTaskStatus.SUCCEEDED
        for state in publish_attempts
    )
    assert completed.credential_audit is not None
    assert completed.credential_audit.notes == "Retry after fixing credentials"
    assert fail_state["calls"] == 2
