from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.api.routers.workflows import _get_repository
from api_service.auth_providers import get_current_user
from api_service.db import base as db_base
from api_service.db.models import Base
from api_service.main import app
from moonmind.config.settings import settings
from moonmind.schemas.workflow_models import (
    CodexPreflightResultModel,
    CodexShardListResponse,
    SpecWorkflowRunModel,
    WorkflowArtifactModel,
    WorkflowRunCollectionResponse,
    WorkflowTaskStateModel,
)
from moonmind.workflows.adapters.github_client import GitHubPublishResult
from moonmind.workflows.speckit_celery import celery_app
from moonmind.workflows.speckit_celery import models as workflow_models
from moonmind.workflows.speckit_celery import tasks as workflow_tasks
from moonmind.workflows.speckit_celery.repositories import PaginatedSpecWorkflowRuns
from moonmind.workflows.speckit_celery.workspace import (
    generate_branch_name,
    sanitize_branch_component,
)

TEST_REPOSITORY = "MoonLadderStudios/MoonMind"


def _build_sample_run(now: datetime | None = None) -> SimpleNamespace:
    now = now or datetime(2024, 5, 1, tzinfo=UTC)
    run_id = uuid4()
    task_states = [
        SimpleNamespace(
            id=uuid4(),
            workflow_run_id=run_id,
            task_name="discover_next_phase",
            status=workflow_models.SpecWorkflowTaskStatus.SUCCEEDED,
            attempt=1,
            payload={"status": "succeeded"},
            message="Discovery completed",
            artifact_paths=["/artifacts/discover.jsonl"],
            started_at=now,
            finished_at=now,
            created_at=now,
            updated_at=now,
        ),
        SimpleNamespace(
            id=uuid4(),
            workflow_run_id=run_id,
            task_name="apply_and_publish",
            status=workflow_models.SpecWorkflowTaskStatus.RUNNING,
            attempt=2,
            payload={"status": "running"},
            message=None,
            artifact_paths=["/artifacts/apply.log"],
            started_at=now,
            finished_at=None,
            created_at=now,
            updated_at=now,
        ),
    ]
    artifacts = [
        SimpleNamespace(
            id=uuid4(),
            workflow_run_id=run_id,
            artifact_type=workflow_models.WorkflowArtifactType.CODEX_LOGS,
            path="/artifacts/codex_logs.jsonl",
            content_type="application/json",
            size_bytes=2048,
            digest="sha256:deadbeef",
            created_at=now,
        ),
        SimpleNamespace(
            id=uuid4(),
            workflow_run_id=run_id,
            artifact_type=workflow_models.WorkflowArtifactType.GH_PR_RESPONSE,
            path="/artifacts/pr.json",
            content_type="application/json",
            size_bytes=1024,
            digest=None,
            created_at=now,
        ),
    ]
    credential_audit = SimpleNamespace(
        codex_status=workflow_models.CodexCredentialStatus.VALID,
        github_status=workflow_models.GitHubCredentialStatus.VALID,
        checked_at=now,
        notes="token ok",
    )
    return SimpleNamespace(
        id=run_id,
        feature_key="US2-monitoring",
        status=workflow_models.SpecWorkflowRunStatus.RUNNING,
        phase=workflow_models.SpecWorkflowRunPhase.SUBMIT,
        repository=TEST_REPOSITORY,
        branch_name="US2-monitoring/20240501/1234abcd",
        pr_url="https://example.com/pr/123",
        codex_task_id="codex-123",
        codex_queue="codex-0",
        codex_volume="codex_auth_0",
        codex_preflight_status=workflow_models.CodexPreflightStatus.PASSED,
        codex_preflight_message="ready",
        codex_logs_path="/artifacts/codex_logs.jsonl",
        codex_patch_path="/artifacts/patch.diff",
        celery_chain_id="celery-abc123",
        requested_by_user_id=uuid4(),
        created_by=uuid4(),
        current_task_name=workflow_models.SpecWorkflowTaskName.SUBMIT,
        started_at=now,
        finished_at=None,
        completed_at=None,
        artifacts_path="/artifacts",
        created_at=now,
        updated_at=now,
        task_states=task_states,
        artifacts=artifacts,
        credential_audit=credential_audit,
    )


class _FakeRepo:
    def __init__(self, run_obj):
        self.run = run_obj

    async def list_runs(
        self,
        *,
        status=None,
        feature_key=None,
        created_by=None,
        cursor=None,
        limit=25,
        with_relations=False,
    ):
        if status and self.run.status != status:
            return PaginatedSpecWorkflowRuns(items=[], next_cursor=None)
        if feature_key and self.run.feature_key != feature_key:
            return PaginatedSpecWorkflowRuns(items=[], next_cursor=None)
        if created_by and self.run.created_by != created_by:
            return PaginatedSpecWorkflowRuns(items=[], next_cursor=None)
        run_copy = self.run
        if not with_relations:
            run_copy = SimpleNamespace(**vars(self.run))
            run_copy.task_states = []
            run_copy.artifacts = []
        return PaginatedSpecWorkflowRuns(items=[run_copy][:limit], next_cursor=None)

    async def list_task_states_for_runs(self, run_ids):
        if run_ids and self.run.id not in run_ids:
            return {}
        return {self.run.id: list(self.run.task_states)}

    async def get_run(self, run_id, with_relations=True):
        if run_id != self.run.id:
            return None
        if not with_relations:
            run_copy = SimpleNamespace(**vars(self.run))
            run_copy.task_states = []
            run_copy.artifacts = []
            return run_copy
        return self.run


@pytest.mark.asyncio
async def test_create_workflow_run_contract_idempotent_branch(tmp_path, monkeypatch):
    """Ensure POST /runs returns contract payloads and deterministic branches."""

    original_db_url = db_base.DATABASE_URL
    original_engine = db_base.engine
    original_session_maker = db_base.async_session_maker
    db_url = f"sqlite+aiosqlite:///{tmp_path}/workflow_contract.db"
    db_base.DATABASE_URL = db_url
    db_base.engine = create_async_engine(db_url, future=True)
    db_base.async_session_maker = sessionmaker(
        db_base.engine, class_=AsyncSession, expire_on_commit=False
    )

    async with db_base.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    branch_history: dict[str, str] = {}
    run_store: dict = {}
    test_timestamp = datetime(2024, 1, 1, tzinfo=UTC)

    async def _fake_trigger_spec_workflow_run(
        *,
        feature_key=None,
        created_by=None,
        requested_by_user_id=None,
        force_phase=None,
        repository=None,
    ):
        del (
            created_by,
            requested_by_user_id,
            force_phase,
        )  # unused in this contract test
        key = feature_key or settings.spec_workflow.default_feature_key
        run_id = uuid4()
        branch_name = branch_history.setdefault(
            key, generate_branch_name(run_id, prefix=key, timestamp=test_timestamp)
        )

        run = workflow_models.SpecWorkflowRun(
            id=run_id,
            feature_key=key,
            repository=repository,
            branch_name=branch_name,
            status=workflow_models.SpecWorkflowRunStatus.PENDING,
            phase=workflow_models.SpecWorkflowRunPhase.DISCOVER,
            created_at=test_timestamp,
            updated_at=test_timestamp,
        )
        run_store[run_id] = run

        return SimpleNamespace(
            run=run, celery_chain_id=f"celery-{len(run_store)}", run_id=run_id
        )

    class _FakeRepo:
        def __init__(self, store):
            self._store = store

        async def get_run(self, run_id, with_relations=True):
            del with_relations
            return self._store.get(run_id)

    monkeypatch.setattr(
        "api_service.api.routers.workflows.trigger_spec_workflow_run",
        _fake_trigger_spec_workflow_run,
    )
    monkeypatch.setitem(celery_app.conf, "task_always_eager", True)
    monkeypatch.setitem(celery_app.conf, "task_eager_propagates", True)

    app.dependency_overrides[_get_repository] = lambda: _FakeRepo(run_store)
    test_user = SimpleNamespace(id=uuid4())
    app.dependency_overrides[get_current_user] = lambda: test_user

    feature_key = "FR-008/idempotent-branch"

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            response = await client.post(
                "/api/workflows/speckit/runs",
                json={"repository": TEST_REPOSITORY, "featureKey": feature_key},
            )
            assert response.status_code == 202
            run_model = SpecWorkflowRunModel.model_validate(response.json())
            assert run_model.status == workflow_models.SpecWorkflowRunStatus.PENDING
            assert run_model.phase == workflow_models.SpecWorkflowRunPhase.DISCOVER
            assert run_model.repository == TEST_REPOSITORY

            assert run_model.branch_name is not None
            branch_parts = run_model.branch_name.split("/")
            assert len(branch_parts) == 3
            sanitized_key, date_str, run_id_part = branch_parts
            assert sanitized_key == sanitize_branch_component(feature_key)
            assert date_str.isdigit() and len(date_str) == 8
            assert len(run_id_part) == 8 and all(
                char in "0123456789abcdef" for char in run_id_part.lower()
            )

            second_response = await client.post(
                "/api/workflows/speckit/runs",
                json={"repository": TEST_REPOSITORY, "featureKey": feature_key},
            )
            assert second_response.status_code == 202
            second_model = SpecWorkflowRunModel.model_validate(second_response.json())
            assert second_model.branch_name == run_model.branch_name
            assert second_model.id != run_model.id
    finally:
        await db_base.engine.dispose()
        db_base.DATABASE_URL = original_db_url
        db_base.engine = original_engine
        db_base.async_session_maker = original_session_maker
        app.dependency_overrides.pop(_get_repository, None)
        app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.asyncio
async def test_monitor_workflow_contract_endpoints(monkeypatch):
    """Contract coverage for run listing and detail retrieval endpoints."""

    run = _build_sample_run()
    run_id = run.id

    app.dependency_overrides[_get_repository] = lambda: _FakeRepo(run)
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=uuid4())

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        list_response = await client.get(
            "/api/workflows/speckit/runs", params={"includeTasks": True}
        )
        assert list_response.status_code == 200
        collection = WorkflowRunCollectionResponse.model_validate(list_response.json())
        assert len(collection.items) == 1
        listed = collection.items[0]
        assert listed.id == run_id
        assert listed.task_summary
        assert listed.tasks
        assert listed.artifacts == []

        detail_response = await client.get(
            f"/api/workflows/speckit/runs/{run_id}",
            params={"includeArtifacts": True},
        )
        assert detail_response.status_code == 200
        detail_model = SpecWorkflowRunModel.model_validate(detail_response.json())
        assert detail_model.id == run_id
        assert detail_model.artifacts
        assert any(task.attempt == 2 for task in detail_model.tasks)
        assert detail_model.credential_audit is not None
        assert (
            detail_model.credential_audit.codex_status
            == workflow_models.CodexCredentialStatus.VALID
        )

    app.dependency_overrides.pop(_get_repository, None)
    app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.asyncio
@pytest.mark.xfail(reason="Task listing endpoint not implemented yet", strict=False)
async def test_workflow_task_listing_contract(monkeypatch):
    """Pending contract test for /runs/{id}/tasks until implementation lands."""

    run = _build_sample_run()

    app.dependency_overrides[_get_repository] = lambda: _FakeRepo(run)
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=uuid4())

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get(f"/api/workflows/speckit/runs/{run.id}/tasks")
        assert response.status_code == 200
        payload = response.json()
        assert payload["runId"] == str(run.id)
        tasks = payload["tasks"]
        assert tasks
        assert all(
            WorkflowTaskStateModel.model_validate(task).task_name for task in tasks
        )

    app.dependency_overrides.pop(_get_repository, None)
    app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.asyncio
@pytest.mark.xfail(reason="Artifact listing endpoint not implemented yet", strict=False)
async def test_workflow_artifact_listing_contract(monkeypatch):
    """Pending contract test for /runs/{id}/artifacts until implementation lands."""

    run = _build_sample_run()
    app.dependency_overrides[_get_repository] = lambda: _FakeRepo(run)
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=uuid4())

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get(f"/api/workflows/speckit/runs/{run.id}/artifacts")
        assert response.status_code == 200
        payload = response.json()
        assert payload["runId"] == str(run.id)
        artifacts = payload.get("artifacts", [])
        assert all(
            WorkflowArtifactModel.model_validate(artifact).artifact_type
            for artifact in artifacts
        )

    app.dependency_overrides.pop(_get_repository, None)
    app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.asyncio
async def test_workflow_endpoints_contract(tmp_path, monkeypatch):
    """Ensure the workflow API adheres to the documented contract."""

    original_db_url = db_base.DATABASE_URL
    original_engine = db_base.engine
    original_session_maker = db_base.async_session_maker
    db_url = f"sqlite+aiosqlite:///{tmp_path}/workflow_contract.db"
    db_base.DATABASE_URL = db_url
    db_base.engine = create_async_engine(db_url, future=True)
    db_base.async_session_maker = sessionmaker(
        db_base.engine, class_=AsyncSession, expire_on_commit=False
    )

    async with db_base.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with db_base.async_session_maker() as session:
        volume = workflow_models.CodexAuthVolume(
            name="codex_auth_0",
            worker_affinity="celery-codex-0",
            status=workflow_models.CodexAuthVolumeStatus.NEEDS_AUTH,
        )
        shard = workflow_models.CodexWorkerShard(
            queue_name="codex-0",
            volume_name=volume.name,
            status=workflow_models.CodexWorkerShardStatus.ACTIVE,
        )
        session.add_all([volume, shard])
        await session.commit()

    feature_key = "001-celery-chain-workflow"
    specs_dir = tmp_path / "specs" / feature_key
    specs_dir.mkdir(parents=True)
    (specs_dir / "tasks.md").write_text(
        """
## Phase 3 – User Story 1
- [ ] T050 Contract test task
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
        settings.spec_workflow, "codex_volume_name", "codex_auth_0", raising=False
    )
    monkeypatch.setattr(settings.spec_workflow, "codex_shards", 1, raising=False)
    monkeypatch.setitem(celery_app.conf, "task_always_eager", True)
    monkeypatch.setitem(celery_app.conf, "task_eager_propagates", True)

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

    preflight_calls = {"count": 0}

    def _fake_preflight_check(*, volume_name=None, timeout=60):
        preflight_calls["count"] += 1
        return workflow_tasks.CodexPreflightResult(
            status=workflow_models.CodexPreflightStatus.PASSED,
            message="Codex login status check passed",
            volume=volume_name or "codex_auth_0",
        )

    monkeypatch.setattr(
        "moonmind.workflows.speckit_celery.tasks._run_codex_preflight_check",
        _fake_preflight_check,
    )

    app.state.settings = settings

    test_user = SimpleNamespace(id=uuid4())
    app.dependency_overrides[get_current_user] = lambda: test_user

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            response = await client.post(
                "/api/workflows/speckit/runs",
                json={"repository": TEST_REPOSITORY, "featureKey": feature_key},
            )
            assert response.status_code == 202
            run_model = SpecWorkflowRunModel.model_validate(response.json())
            assert run_model.status == workflow_models.SpecWorkflowRunStatus.FAILED
            assert run_model.phase == workflow_models.SpecWorkflowRunPhase.PUBLISH
            assert run_model.credential_audit is not None
            assert run_model.credential_audit.notes is None

            list_response = await client.get("/api/workflows/speckit/runs")
            assert list_response.status_code == 200
            collection = WorkflowRunCollectionResponse.model_validate(
                list_response.json()
            )
            assert collection.items
            assert collection.items[0].id == run_model.id

            detail_response = await client.get(
                f"/api/workflows/speckit/runs/{run_model.id}"
            )
            assert detail_response.status_code == 200
            detail_model = SpecWorkflowRunModel.model_validate(detail_response.json())
            assert detail_model.status == workflow_models.SpecWorkflowRunStatus.FAILED
            assert any(
                task.task_name == "apply_and_publish"
                and task.status == workflow_models.SpecWorkflowTaskStatus.FAILED
                for task in detail_model.tasks
            )

            retry_payload = {"notes": "Retry after rotating token"}
            retry_response = await client.post(
                f"/api/workflows/speckit/runs/{run_model.id}/retry",
                json=retry_payload,
            )
            assert retry_response.status_code == 202
            retry_model = SpecWorkflowRunModel.model_validate(retry_response.json())
            assert retry_model.status == workflow_models.SpecWorkflowRunStatus.SUCCEEDED
            assert retry_model.phase == workflow_models.SpecWorkflowRunPhase.COMPLETE
            assert retry_model.credential_audit is not None
            assert retry_model.credential_audit.notes == "Retry after rotating token"
            assert any(
                task.task_name == "apply_and_publish" and task.attempt == 2
                for task in retry_model.tasks
            )

            final_detail = await client.get(
                f"/api/workflows/speckit/runs/{run_model.id}"
            )
            assert final_detail.status_code == 200
            final_model = SpecWorkflowRunModel.model_validate(final_detail.json())
            assert final_model.status == workflow_models.SpecWorkflowRunStatus.SUCCEEDED
            assert final_model.branch_name is not None
            assert final_model.pr_url is not None
            assert fail_state["calls"] == 2

            shard_response = await client.get("/api/workflows/speckit/codex/shards")
            assert shard_response.status_code == 200
            shard_model = CodexShardListResponse.model_validate(shard_response.json())
            assert any(shard.queue_name == "codex-0" for shard in shard_model.shards)
            assert any(
                shard.volume_name == "codex_auth_0" for shard in shard_model.shards
            )

            preflight_response = await client.post(
                f"/api/workflows/speckit/runs/{run_model.id}/codex/preflight",
                json={},
            )
            assert preflight_response.status_code == 200
            preflight_model = CodexPreflightResultModel.model_validate(
                preflight_response.json()
            )
            assert preflight_model.status == workflow_models.CodexPreflightStatus.PASSED
            assert preflight_model.volume_name == "codex_auth_0"
            assert preflight_model.queue_name == "codex-0"
            assert preflight_calls["count"] == 1

            cached_response = await client.post(
                f"/api/workflows/speckit/runs/{run_model.id}/codex/preflight",
                json={},
            )
            assert cached_response.status_code == 200
            cached_model = CodexPreflightResultModel.model_validate(
                cached_response.json()
            )
            assert cached_model.checked_at == preflight_model.checked_at
            assert preflight_calls["count"] == 1

            forced_response = await client.post(
                f"/api/workflows/speckit/runs/{run_model.id}/codex/preflight",
                json={"forceRefresh": True},
            )
            assert forced_response.status_code == 200
            forced_model = CodexPreflightResultModel.model_validate(
                forced_response.json()
            )
            assert preflight_calls["count"] == 2
            assert forced_model.checked_at >= cached_model.checked_at

            invalid_affinity = await client.post(
                f"/api/workflows/speckit/runs/{run_model.id}/codex/preflight",
                json={"affinityKey": "bad key"},
            )
            assert invalid_affinity.status_code == 422
            assert preflight_calls["count"] == 2

            refreshed = await client.get("/api/workflows/speckit/codex/shards")
            refreshed_model = CodexShardListResponse.model_validate(refreshed.json())
            target_shard = next(
                shard
                for shard in refreshed_model.shards
                if shard.queue_name == "codex-0"
            )
            assert (
                target_shard.volume_status
                == workflow_models.CodexAuthVolumeStatus.READY
            )
    finally:
        await db_base.engine.dispose()
        db_base.DATABASE_URL = original_db_url
        db_base.engine = original_engine
        db_base.async_session_maker = original_session_maker
        app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.asyncio
async def test_workflow_run_retry_handles_credential_error(monkeypatch, tmp_path):
    """Retry should expose credential failures instead of restarting Codex submissions."""

    db_url = f"sqlite+aiosqlite:///{tmp_path}/workflow_api.db"
    engine = create_async_engine(db_url, future=True)
    async_session_maker = sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )

    monkeypatch.setattr(db_base, "DATABASE_URL", db_url)
    monkeypatch.setattr(db_base, "engine", engine)
    monkeypatch.setattr(db_base, "async_session_maker", async_session_maker)
    monkeypatch.setattr(
        _get_repository, "__defaults__", (lambda: async_session_maker(),)
    )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    feature_key = "001-celery-chain-workflow"
    specs_dir = tmp_path / "specs" / feature_key
    specs_dir.mkdir(parents=True)
    (specs_dir / "tasks.md").write_text(
        """
## Phase 3 – User Story 1
- [ ] T050 Contract test task
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
        settings.spec_workflow, "codex_volume_name", "codex_auth_0", raising=False
    )
    monkeypatch.setattr(settings.spec_workflow, "codex_shards", 1, raising=False)
    monkeypatch.setitem(celery_app.conf, "task_always_eager", True)
    monkeypatch.setitem(celery_app.conf, "task_eager_propagates", True)

    def _failing_github_client():
        class _Client:
            def publish(self, **_kwargs):
                raise RuntimeError("simulated publish failure")

        return _Client()

    monkeypatch.setattr(
        "moonmind.workflows.speckit_celery.tasks._build_github_client",
        _failing_github_client,
    )

    monkeypatch.setattr(
        "moonmind.workflows.speckit_celery.tasks._run_codex_preflight_check",
        lambda *_, **__: workflow_tasks.CodexPreflightResult(
            status=workflow_models.CodexPreflightStatus.PASSED,
            message="Codex login status check passed",
            volume="codex_auth_0",
        ),
    )

    app.state.settings = settings
    test_user = SimpleNamespace(id=uuid4())
    app.dependency_overrides[get_current_user] = lambda: test_user

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            response = await client.post(
                "/api/workflows/speckit/runs",
                json={"repository": TEST_REPOSITORY, "featureKey": feature_key},
            )
            assert response.status_code == 202
            run_model = SpecWorkflowRunModel.model_validate(response.json())
            assert run_model.status == workflow_models.SpecWorkflowRunStatus.FAILED

            def _invalidate_credentials(*_args, **_kwargs):
                audit = workflow_models.CredentialAuditResult(
                    codex_status=workflow_models.CodexCredentialStatus.INVALID,
                    github_status=workflow_models.GitHubCredentialStatus.INVALID,
                    notes="GitHub token expired",
                )
                raise workflow_tasks.CredentialValidationError(
                    audit, "Credential validation failed"
                )

            monkeypatch.setattr(
                workflow_tasks, "_validate_credentials", _invalidate_credentials
            )

            retry_response = await client.post(
                f"/api/workflows/speckit/runs/{run_model.id}/retry", json={}
            )
            assert retry_response.status_code == 202
            retry_model = SpecWorkflowRunModel.model_validate(retry_response.json())
            assert retry_model.status == workflow_models.SpecWorkflowRunStatus.FAILED
            assert any(
                task.task_name == "apply_and_publish"
                and task.status == workflow_models.SpecWorkflowTaskStatus.FAILED
                and task.attempt == 2
                for task in retry_model.tasks
            )
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()
