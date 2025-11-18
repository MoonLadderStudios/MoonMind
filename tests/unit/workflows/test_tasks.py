"""Unit tests for Spec Kit workflow serializers."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import celery.app.task as celery_task
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import Base
from moonmind.workflows.speckit_celery import models, repositories, tasks
from moonmind.workflows.speckit_celery.celeryconfig import get_codex_shard_router
from moonmind.workflows.speckit_celery.serializers import (
    serialize_run,
    serialize_task_state,
    serialize_task_summary,
)


def _make_state(
    *,
    workflow_run_id,
    task_name: str,
    status: models.SpecWorkflowTaskStatus,
    attempt: int = 1,
    payload: dict[str, object] | None = None,
    created_at: datetime | None = None,
    updated_at: datetime | None = None,
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
) -> models.SpecWorkflowTaskState:
    created = created_at or datetime.now(UTC)
    return models.SpecWorkflowTaskState(
        id=uuid4(),
        workflow_run_id=workflow_run_id,
        task_name=task_name,
        status=status,
        attempt=attempt,
        payload=payload,
        started_at=started_at,
        finished_at=finished_at,
        created_at=created,
        updated_at=updated_at or created,
    )


def test_serialize_task_state_includes_temporal_fields():
    """Task state serialization should surface temporal metadata."""

    run_id = uuid4()
    now = datetime.now(UTC)
    state = _make_state(
        workflow_run_id=run_id,
        task_name="discover_next_phase",
        status=models.SpecWorkflowTaskStatus.RUNNING,
        attempt=1,
        payload={"status": "running"},
        started_at=now,
        created_at=now,
        updated_at=now + timedelta(seconds=5),
    )

    serialized = serialize_task_state(state)

    assert serialized["taskName"] == "discover_next_phase"
    assert serialized["status"] == models.SpecWorkflowTaskStatus.RUNNING.value
    assert serialized["payload"]["status"] == "running"
    assert serialized["createdAt"].endswith("+00:00")
    assert serialized["updatedAt"].endswith("+00:00")


def test_serialize_task_summary_collapses_attempts():
    """Only the latest attempt per task should be returned in summaries."""

    run_id = uuid4()
    base = datetime(2025, 1, 1, tzinfo=UTC)
    states = [
        _make_state(
            workflow_run_id=run_id,
            task_name="discover_next_phase",
            status=models.SpecWorkflowTaskStatus.QUEUED,
            created_at=base,
            updated_at=base,
        ),
        _make_state(
            workflow_run_id=run_id,
            task_name="discover_next_phase",
            status=models.SpecWorkflowTaskStatus.SUCCEEDED,
            created_at=base,
            updated_at=base + timedelta(minutes=1),
        ),
        _make_state(
            workflow_run_id=run_id,
            task_name="submit_codex_job",
            status=models.SpecWorkflowTaskStatus.RUNNING,
            created_at=base,
            updated_at=base + timedelta(minutes=2),
        ),
        _make_state(
            workflow_run_id=run_id,
            task_name="apply_and_publish",
            status=models.SpecWorkflowTaskStatus.FAILED,
            attempt=1,
            created_at=base,
            updated_at=base + timedelta(minutes=3),
        ),
        _make_state(
            workflow_run_id=run_id,
            task_name="apply_and_publish",
            status=models.SpecWorkflowTaskStatus.RUNNING,
            attempt=2,
            created_at=base,
            updated_at=base + timedelta(minutes=4),
        ),
    ]

    summary = serialize_task_summary(states)

    assert [item["taskName"] for item in summary] == [
        "discover_next_phase",
        "submit_codex_job",
        "apply_and_publish",
    ]

    apply_entry = next(
        item for item in summary if item["taskName"] == "apply_and_publish"
    )
    assert apply_entry["attempt"] == 2
    assert apply_entry["status"] == models.SpecWorkflowTaskStatus.RUNNING.value


def test_serialize_run_includes_summary_and_paths():
    """Run serialization should include task summary and artifact paths."""

    run_id = uuid4()
    now = datetime.now(UTC)
    run = models.SpecWorkflowRun(
        id=run_id,
        feature_key="001-celery-chain-workflow",
        celery_chain_id="celery-123",
        status=models.SpecWorkflowRunStatus.RUNNING,
        phase=models.SpecWorkflowRunPhase.SUBMIT,
        branch_name=None,
        pr_url=None,
        codex_task_id="codex-42",
        codex_logs_path="/tmp/logs.jsonl",
        codex_patch_path=None,
        artifacts_path="/tmp/artifacts",
        created_by=None,
        started_at=now,
        finished_at=None,
        created_at=now,
        updated_at=now,
    )

    state = _make_state(
        workflow_run_id=run_id,
        task_name="discover_next_phase",
        status=models.SpecWorkflowTaskStatus.SUCCEEDED,
        payload={"status": "succeeded"},
        started_at=now,
        finished_at=now + timedelta(minutes=1),
        created_at=now,
        updated_at=now + timedelta(minutes=1),
    )
    run.task_states = [state]

    serialized_full = serialize_run(run, include_tasks=True)
    assert serialized_full["codexLogsPath"] == "/tmp/logs.jsonl"
    assert serialized_full["taskSummary"]
    assert serialized_full["tasks"][0]["taskName"] == "discover_next_phase"

    serialized_min = serialize_run(run, include_tasks=False, task_states=[state])
    assert "tasks" not in serialized_min
    assert serialized_min["taskSummary"]
    assert serialized_min["taskSummary"][0]["taskName"] == "discover_next_phase"


def test_base_context_includes_codex_volume(monkeypatch):
    """Base workflow context should surface the configured Codex volume."""

    now = datetime.now(UTC)
    run = models.SpecWorkflowRun(
        id=uuid4(),
        feature_key="001-celery-oauth-volumes",
        celery_chain_id=None,
        status=models.SpecWorkflowRunStatus.PENDING,
        phase=models.SpecWorkflowRunPhase.DISCOVER,
        branch_name=None,
        pr_url=None,
        codex_task_id=None,
        codex_queue=None,
        codex_volume=None,
        codex_preflight_status=None,
        codex_preflight_message=None,
        codex_logs_path=None,
        codex_patch_path=None,
        artifacts_path=None,
        created_by=None,
        started_at=None,
        finished_at=None,
        created_at=now,
        updated_at=now,
    )

    monkeypatch.setattr(
        tasks.settings.spec_workflow,
        "codex_volume_name",
        "codex_auth_fallback",
        raising=False,
    )

    context = tasks._base_context(run)
    assert context["codex_volume"] == "codex_auth_fallback"

    run.codex_volume = "codex_auth_primary"
    context = tasks._base_context(run)
    assert context["codex_volume"] == "codex_auth_primary"


def test_submit_codex_job_preflight_failure(monkeypatch):
    """Pre-flight failures should mark the run failed and surface remediation."""

    run_id = uuid4()
    now = datetime.now(UTC)
    run = models.SpecWorkflowRun(
        id=run_id,
        feature_key="001-celery-oauth-volumes",
        celery_chain_id=None,
        status=models.SpecWorkflowRunStatus.PENDING,
        phase=models.SpecWorkflowRunPhase.DISCOVER,
        branch_name=None,
        pr_url=None,
        codex_task_id=None,
        codex_queue=None,
        codex_volume=None,
        codex_preflight_status=None,
        codex_preflight_message=None,
        codex_logs_path=None,
        codex_patch_path=None,
        artifacts_path="var/artifacts/spec_workflows",
        created_by=None,
        started_at=None,
        finished_at=None,
        created_at=now,
        updated_at=now,
    )

    commits: list[int] = []

    @asynccontextmanager
    async def dummy_session_context():
        class DummySession:
            async def commit(self) -> None:
                commits.append(1)

        yield DummySession()

    monkeypatch.setattr(tasks, "get_async_session_context", dummy_session_context)

    repo_instances: list[object] = []

    class DummyRepo:
        def __init__(self, session):
            self.session = session
            self.update_calls: list[dict[str, object]] = []

        async def get_run(self, workflow_run_id):
            assert workflow_run_id == run_id
            return run

        async def update_run(self, workflow_run_id, **changes):
            assert workflow_run_id == run_id
            self.update_calls.append(changes)
            for key, value in changes.items():
                setattr(run, key, value)
            return run

    def repo_factory(session):
        repo = DummyRepo(session)
        repo_instances.append(repo)
        return repo

    monkeypatch.setattr(tasks, "SpecWorkflowRepository", repo_factory)

    task_updates: list[dict[str, object]] = []

    async def fake_update_task_state(*_, **kwargs):
        task_updates.append(kwargs)
        return object()

    monkeypatch.setattr(tasks, "_update_task_state", fake_update_task_state)

    async def fake_ensure_credentials_validated(*_args, **_kwargs):
        return None

    monkeypatch.setattr(
        tasks,
        "_ensure_credentials_validated",
        fake_ensure_credentials_validated,
    )

    failure_result = tasks.CodexPreflightResult(
        status=models.CodexPreflightStatus.FAILED,
        message="Codex login required",
        volume="codex_auth_0",
        exit_code=1,
    )

    monkeypatch.setattr(
        tasks,
        "_run_codex_preflight_check",
        lambda: failure_result,
    )

    def fail_if_client_built():
        raise AssertionError("Codex client should not be built when pre-flight fails")

    monkeypatch.setattr(tasks, "_build_codex_client", fail_if_client_built)
    monkeypatch.setattr(
        tasks.settings.spec_workflow, "codex_volume_name", "codex_auth_0", raising=False
    )

    context = {"run_id": str(run_id), "feature_key": run.feature_key, "task": {}}

    with pytest.raises(RuntimeError) as excinfo:
        tasks.submit_codex_job(context)

    assert "Codex login required" in str(excinfo.value)
    assert context["codex_preflight_status"] == models.CodexPreflightStatus.FAILED.value
    assert context["codex_volume"] == "codex_auth_0"

    repo = repo_instances[0]
    assert any(
        call.get("codex_preflight_status") == models.CodexPreflightStatus.FAILED
        for call in repo.update_calls
    )
    assert repo.update_calls[-1]["status"] == models.SpecWorkflowRunStatus.FAILED

    assert task_updates[-1]["payload"]["code"] == "codex_preflight_failed"
    assert commits.count(1) >= 2


def test_submit_codex_job_preflight_skipped(monkeypatch, tmp_path):
    """Skipped pre-flight checks should allow submission to proceed."""

    run_id = uuid4()
    now = datetime.now(UTC)
    run = models.SpecWorkflowRun(
        id=run_id,
        feature_key="001-celery-oauth-volumes",
        celery_chain_id=None,
        status=models.SpecWorkflowRunStatus.PENDING,
        phase=models.SpecWorkflowRunPhase.DISCOVER,
        branch_name=None,
        pr_url=None,
        codex_task_id=None,
        codex_queue=None,
        codex_volume=None,
        codex_preflight_status=None,
        codex_preflight_message=None,
        codex_logs_path=None,
        codex_patch_path=None,
        artifacts_path=str(tmp_path),
        created_by=None,
        started_at=None,
        finished_at=None,
        created_at=now,
        updated_at=now,
    )

    commits: list[int] = []

    @asynccontextmanager
    async def dummy_session_context():
        class DummySession:
            async def commit(self) -> None:
                commits.append(1)

        yield DummySession()

    monkeypatch.setattr(tasks, "get_async_session_context", dummy_session_context)

    class DummyRepo:
        def __init__(self, session):
            self.session = session
            self.update_calls: list[dict[str, object]] = []
            self.artifacts: list[dict[str, object]] = []

        async def get_run(self, workflow_run_id):
            assert workflow_run_id == run_id
            return run

        async def update_run(self, workflow_run_id, **changes):
            assert workflow_run_id == run_id
            self.update_calls.append(changes)
            for key, value in changes.items():
                setattr(run, key, value)
            return run

        async def add_artifact(self, workflow_run_id, **kwargs):
            assert workflow_run_id == run_id
            self.artifacts.append(kwargs)

    repo_instances: list[DummyRepo] = []

    def repo_factory(session):
        repo = DummyRepo(session)
        repo_instances.append(repo)
        return repo

    monkeypatch.setattr(tasks, "SpecWorkflowRepository", repo_factory)

    task_updates: list[dict[str, object]] = []

    async def fake_update_task_state(*_, **kwargs):
        task_updates.append(kwargs)
        return object()

    monkeypatch.setattr(tasks, "_update_task_state", fake_update_task_state)

    async def fake_ensure_credentials_validated(*_args, **_kwargs):
        return None

    monkeypatch.setattr(
        tasks,
        "_ensure_credentials_validated",
        fake_ensure_credentials_validated,
    )

    skip_result = tasks.CodexPreflightResult(
        status=models.CodexPreflightStatus.SKIPPED,
        message="Codex auth volume not configured",
        volume=None,
    )

    monkeypatch.setattr(
        tasks,
        "_run_codex_preflight_check",
        lambda: skip_result,
    )

    class DummyClient:
        def submit(self, **_kwargs):
            return tasks.CodexSubmissionResult(
                task_id="codex-123",
                logs_path=tmp_path / "codex-123.jsonl",
                summary="submitted",
            )

    monkeypatch.setattr(tasks, "_build_codex_client", lambda: DummyClient())
    monkeypatch.setattr(
        tasks.settings.spec_workflow,
        "codex_volume_name",
        "codex_auth_default",
        raising=False,
    )

    context = {"run_id": str(run_id), "feature_key": run.feature_key, "task": {}}

    result = tasks.submit_codex_job(context)

    assert result["codex_task_id"] == "codex-123"
    assert result["codex_preflight_status"] == models.CodexPreflightStatus.SKIPPED.value
    assert result["codex_volume"] == "codex_auth_default"

    assert repo_instances, "Repository factory should have been invoked"
    update_calls = repo_instances[0].update_calls
    assert any(
        call.get("codex_preflight_status") == models.CodexPreflightStatus.SKIPPED
        for call in update_calls
    )
    assert all(
        call.get("status") != models.SpecWorkflowRunStatus.FAILED
        for call in update_calls
    )

    assert task_updates[-1]["status"] == models.SpecWorkflowTaskStatus.SUCCEEDED


def test_codex_routing_deterministic_queue_selection(monkeypatch):
    """Codex tasks should hash to a stable queue based on the affinity key."""

    calls: list[dict[str, object]] = []

    def capture_apply_async(
        self,
        args=None,
        kwargs=None,
        task_id=None,
        producer=None,
        link=None,
        link_error=None,
        shadow=None,
        **options,
    ):
        calls.append({"args": args, "kwargs": kwargs, "options": options})
        return object()

    monkeypatch.setattr(
        celery_task.Task,
        "apply_async",
        capture_apply_async,
        raising=False,
    )

    base_context = {
        "feature_key": "001-celery-oauth-volumes",
        "artifacts_path": "var/artifacts/spec_workflows",
        "task": {"taskId": "T020"},
    }
    context_one = dict(base_context)
    context_one["run_id"] = str(uuid4())
    context_two = dict(base_context)
    context_two["run_id"] = str(uuid4())

    affinity_key = tasks._derive_codex_affinity_key(dict(context_one))
    assert tasks._derive_codex_affinity_key(dict(context_two)) == affinity_key
    router = get_codex_shard_router()
    expected_queue = router.queue_for_key(affinity_key)

    tasks.submit_codex_job.apply_async((context_one,))
    tasks.submit_codex_job.apply_async((context_two,))

    assert len(calls) == 2
    for call in calls:
        queued_context = call["args"][0]
        assert call["options"]["queue"] == expected_queue
        assert queued_context["codex_queue"] == expected_queue
        assert queued_context["codex_affinity_key"] == affinity_key
        assert queued_context["codex_shard_index"] == router.shard_for_key(affinity_key)


def test_codex_routing_reuses_existing_queue(monkeypatch):
    """Downstream Codex tasks should honor the queue established during submit."""

    calls: list[dict[str, object]] = []

    def capture_apply_async(
        self,
        args=None,
        kwargs=None,
        task_id=None,
        producer=None,
        link=None,
        link_error=None,
        shadow=None,
        **options,
    ):
        calls.append({"args": args, "kwargs": kwargs, "options": options})
        return object()

    monkeypatch.setattr(
        celery_task.Task,
        "apply_async",
        capture_apply_async,
        raising=False,
    )

    context = {
        "run_id": str(uuid4()),
        "feature_key": "001-celery-oauth-volumes",
        "artifacts_path": "var/artifacts/spec_workflows",
        "task": {"taskId": "T021"},
    }

    tasks.submit_codex_job.apply_async((context,))
    assert calls, "submit_codex_job should invoke apply_async"
    submit_queue = calls[-1]["options"]["queue"]
    assert submit_queue.startswith("codex-")
    assert context["codex_queue"] == submit_queue

    calls.clear()
    tasks.apply_and_publish.apply_async((context,))
    assert calls, "apply_and_publish should invoke apply_async"
    publish_queue = calls[-1]["options"]["queue"]
    assert publish_queue == submit_queue
    routed_context = calls[-1]["args"][0]
    assert routed_context["codex_queue"] == submit_queue
    assert routed_context["codex_shard_index"] == context["codex_shard_index"]


@pytest.mark.asyncio
async def test_list_codex_shard_health_includes_volume_and_preflight():
    """Repository shard health view should merge volume and latest run metadata."""

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async with async_session() as session:
            repo = repositories.SpecWorkflowRepository(session)
            volume = models.CodexAuthVolume(
                name="codex_auth_5",
                worker_affinity="celery-codex-5",
                status=models.CodexAuthVolumeStatus.NEEDS_AUTH,
                notes="requires validation",
            )
            shard = models.CodexWorkerShard(
                queue_name="codex-5",
                volume_name=volume.name,
                status=models.CodexWorkerShardStatus.ACTIVE,
                worker_hostname="worker-5",
            )
            now = datetime.now(UTC)
            run = models.SpecWorkflowRun(
                id=uuid4(),
                feature_key="003-celery-oauth-volumes",
                status=models.SpecWorkflowRunStatus.RUNNING,
                phase=models.SpecWorkflowRunPhase.SUBMIT,
                codex_queue=shard.queue_name,
                codex_volume=volume.name,
                codex_preflight_status=models.CodexPreflightStatus.PASSED,
                codex_preflight_message="Codex login status check passed",
                created_at=now,
                updated_at=now,
            )
            session.add_all([volume, shard, run])
            await session.commit()

            health = await repo.list_codex_shard_health()
            assert len(health) == 1
            entry = health[0]
            assert entry.queue_name == "codex-5"
            assert entry.volume_name == volume.name
            assert entry.volume_status == models.CodexAuthVolumeStatus.NEEDS_AUTH
            assert entry.volume_worker_affinity == "celery-codex-5"
            assert entry.latest_run_id == run.id
            assert entry.latest_preflight_status == models.CodexPreflightStatus.PASSED
            assert entry.latest_preflight_message == "Codex login status check passed"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_upsert_credential_audit_loadable_via_repository():
    """Credential audits should be retrievable via get_run relationships."""

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async with async_session() as session:
            repo = repositories.SpecWorkflowRepository(session)
            run = models.SpecWorkflowRun(
                id=uuid4(),
                feature_key="credential-audit-loadable",
                status=models.SpecWorkflowRunStatus.PENDING,
                phase=models.SpecWorkflowRunPhase.DISCOVER,
            )
            session.add(run)
            await session.commit()

            await repo.upsert_credential_audit(
                workflow_run_id=run.id,
                codex_status=models.CodexCredentialStatus.VALID,
                github_status=models.GitHubCredentialStatus.INVALID,
                notes="GitHub token expired",
            )
            await session.commit()

            refreshed = await repo.get_run(run.id, with_relations=True)
            assert refreshed is not None
            assert refreshed.credential_audit is not None
            assert (
                refreshed.credential_audit.github_status
                is models.GitHubCredentialStatus.INVALID
            )
            assert refreshed.credential_audit.notes == "GitHub token expired"
    finally:
        await engine.dispose()
