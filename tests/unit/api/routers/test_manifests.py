"""Router-level unit tests for manifest registry endpoints."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI, HTTPException, status
from fastapi.testclient import TestClient

from api_service.api.routers import manifests as manifests_router
from api_service.api.routers.worker_auth import _WorkerRequestAuth
from moonmind.workflows.tasks.manifest_contract import ManifestContractError
from api_service.services.manifests_service import ManifestRegistryNotFoundError


def _record(**overrides):
    now = datetime.now(UTC)
    base = {
        "name": "demo",
        "version": "v0",
        "content": "version: 'v0'\\nmetadata:\\n  name: demo\\n",
        "content_hash": "sha256:abc",
        "updated_at": now,
        "last_run_job_id": uuid4(),
        "last_run_source": "queue",
        "last_run_status": "queued",
        "last_run_workflow_id": None,
        "last_run_temporal_run_id": None,
        "last_run_manifest_ref": None,
        "last_run_started_at": now,
        "last_run_finished_at": None,
        "state_json": {"foo": "bar"},
        "state_updated_at": now,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.fixture
def client() -> tuple[TestClient, AsyncMock]:
    app = FastAPI()
    app.include_router(manifests_router.router)
    mock_service = AsyncMock()
    app.dependency_overrides[manifests_router._get_service] = lambda: mock_service
    with TestClient(app) as test_client:
        yield test_client, mock_service


def _worker_auth(**overrides: object) -> _WorkerRequestAuth:
    base = {
        "auth_source": "worker_token",
        "worker_id": "worker-1",
        "allowed_repositories": (),
        "allowed_job_types": (),
        "capabilities": (),
        "token_id": None,
    }
    base.update(overrides)
    return _WorkerRequestAuth(**base)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_list_manifests_serializes_records() -> None:
    """list_manifests should return summaries for registry entries."""

    service = AsyncMock()
    service.list_manifests.return_value = [_record()]
    user = SimpleNamespace(id=uuid4())

    response = await manifests_router.list_manifests(
        limit=10,
        search=None,
        service=service,
        _user=user,
    )

    assert response.items[0].name == "demo"
    assert response.items[0].content_hash == "sha256:abc"
    assert response.items[0].last_run_source == "queue"
    assert response.items[0].last_run_status == "queued"
    service.list_manifests.assert_awaited_once_with(limit=10, search=None)


@pytest.mark.asyncio
async def test_get_manifest_not_found_raises_404() -> None:
    """get_manifest should raise when registry entry missing."""

    service = AsyncMock()
    service.get_manifest.return_value = None
    user = SimpleNamespace(id=uuid4())

    with pytest.raises(HTTPException) as exc:
        await manifests_router.get_manifest(
            name="missing",
            service=service,
            _user=user,
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_get_manifest_returns_detail() -> None:
    """get_manifest should preserve manifest detail payload shape."""

    record = _record()
    service = AsyncMock()
    service.get_manifest.return_value = record
    user = SimpleNamespace(id=uuid4())

    response = await manifests_router.get_manifest(
        name="demo",
        service=service,
        _user=user,
    )

    assert response.name == "demo"
    assert response.content_hash == "sha256:abc"
    assert response.last_run is not None
    assert response.last_run.source == "queue"
    assert response.last_run.status == "queued"
    assert response.state.state_json == {"foo": "bar"}
    service.get_manifest.assert_awaited_once_with("demo")


@pytest.mark.asyncio
async def test_upsert_manifest_returns_detail() -> None:
    """upsert_manifest should return detail response."""

    record = _record()
    service = AsyncMock()
    service.upsert_manifest.return_value = record
    user = SimpleNamespace(id=uuid4())

    response = await manifests_router.upsert_manifest(
        name="demo",
        payload=manifests_router.ManifestUpsertRequest(content=record.content),
        service=service,
        _user=user,
    )

    assert response.name == "demo"
    assert response.state.state_json == {"foo": "bar"}
    service.upsert_manifest.assert_awaited_once()


@pytest.mark.asyncio
async def test_upsert_manifest_validation_error() -> None:
    """Manifest validation errors should propagate as HTTP 422."""

    service = AsyncMock()
    service.upsert_manifest.side_effect = ManifestContractError("invalid")
    user = SimpleNamespace(id=uuid4())

    with pytest.raises(HTTPException) as exc:
        await manifests_router.upsert_manifest(
            name="demo",
            payload=manifests_router.ManifestUpsertRequest(content="bad"),
            service=service,
            _user=user,
        )
    assert exc.value.status_code == 422
    assert exc.value.detail == {"code": "invalid_manifest", "message": "invalid"}


@pytest.mark.asyncio
async def test_create_manifest_run_returns_temporal_execution_metadata() -> None:
    """create_manifest_run should return Temporal identity when runtime submit succeeds."""

    submission = SimpleNamespace(
        source="temporal",
        status="initializing",
        workflow_id="mm:manifest-1",
        run_id="run-1",
        workflow_type="MoonMind.ManifestIngest",
        temporal_status="running",
        manifest_artifact_ref="art_123",
    )
    service = AsyncMock()
    service.submit_manifest_run.return_value = submission
    user = SimpleNamespace(id=uuid4())

    response = await manifests_router.create_manifest_run(
        name="demo",
        payload=manifests_router.ManifestRunRequest(action="run"),
        service=service,
        user=user,
    )

    assert response.source == "temporal"
    assert response.job_id is None
    assert response.queue is None
    assert response.execution is not None
    assert response.execution.workflow_id == "mm:manifest-1"
    assert response.execution.task_id == "mm:manifest-1"
    assert response.execution.temporal_run_id == "run-1"
    assert response.execution.temporal_status == "running"
    assert response.execution.manifest_artifact_ref == "art_123"
    service.submit_manifest_run.assert_awaited_once()
    assert service.submit_manifest_run.await_args.kwargs["name"] == "demo"
    assert service.submit_manifest_run.await_args.kwargs["action"] == "run"
    assert service.submit_manifest_run.await_args.kwargs["options"] is None


@pytest.mark.asyncio
async def test_create_manifest_run_not_found() -> None:
    """Missing registry entries should return 404."""

    service = AsyncMock()
    service.submit_manifest_run.side_effect = ManifestRegistryNotFoundError("missing")
    user = SimpleNamespace(id=uuid4())

    with pytest.raises(HTTPException) as exc:
        await manifests_router.create_manifest_run(
            name="demo",
            payload=manifests_router.ManifestRunRequest(),
            service=service,
            user=user,
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_create_manifest_run_validation_error() -> None:
    """Queue validation errors should surface as HTTP 422."""

    service = AsyncMock()
    service.submit_manifest_run.side_effect = ManifestContractError("bad job")
    user = SimpleNamespace(id=uuid4())

    with pytest.raises(HTTPException) as exc:
        await manifests_router.create_manifest_run(
            name="demo",
            payload=manifests_router.ManifestRunRequest(),
            service=service,
            user=user,
        )
    assert exc.value.status_code == 422
    assert exc.value.detail == {"code": "invalid_manifest_job", "message": "bad job"}


@pytest.mark.asyncio
async def test_update_manifest_state_returns_detail() -> None:
    """update_manifest_state should serialize persisted checkpoint data."""

    record = _record(state_json={"docs": {"cursor": "abc"}})
    service = AsyncMock()
    service.update_manifest_state.return_value = record

    payload = manifests_router.ManifestStateUpdateRequest(
        state_json={"docs": {"cursor": "abc"}},
        last_run_status="succeeded",
    )
    response = await manifests_router.update_manifest_state(
        name="demo",
        payload=payload,
        service=service,
        worker_auth=_worker_auth(),
    )

    assert response.name == "demo"
    assert response.state.state_json == {"docs": {"cursor": "abc"}}
    service.update_manifest_state.assert_awaited_once()


@pytest.mark.asyncio
async def test_update_manifest_state_not_found() -> None:
    """Missing manifests should return 404 from state update endpoint."""

    service = AsyncMock()
    service.update_manifest_state.side_effect = ManifestRegistryNotFoundError("missing")

    with pytest.raises(HTTPException) as exc:
        await manifests_router.update_manifest_state(
            name="missing",
            payload=manifests_router.ManifestStateUpdateRequest(state_json={}),
            service=service,
            worker_auth=_worker_auth(),
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_update_manifest_state_requires_worker_token() -> None:
    """Manifest state callbacks should reject non-worker auth."""

    service = AsyncMock()

    with pytest.raises(HTTPException) as exc:
        await manifests_router.update_manifest_state(
            name="demo",
            payload=manifests_router.ManifestStateUpdateRequest(state_json={}),
            service=service,
            worker_auth=_worker_auth(auth_source="oidc", worker_id=None),
        )

    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "worker_not_authorized"


def test_create_manifest_run_http_validation_rejects_invalid_action(
    client: tuple[TestClient, AsyncMock],
) -> None:
    """HTTP requests with unsupported action values must fail before service submit."""

    test_client, service = client

    response = test_client.post(
        "/api/manifests/demo/runs",
        json={"action": "evaluate"},
    )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    detail = response.json()["detail"]
    assert detail[0]["loc"][-1] == "action"
    service.submit_manifest_run.assert_not_awaited()


def test_create_manifest_run_http_response_preserves_queue_metadata(
    client: tuple[TestClient, AsyncMock],
) -> None:
    """HTTP run submissions should return queue metadata fields without transforms."""

    test_client, service = client
    queue_job = SimpleNamespace(
        id=uuid4(),
        type="manifest",
        payload={
            "requiredCapabilities": ["manifest", "qdrant"],
            "manifestHash": "sha256:abc123",
        },
    )
    service.submit_manifest_run.return_value = SimpleNamespace(
        source="queue",
        status="queued",
        job=queue_job,
    )

    response = test_client.post(
        "/api/manifests/demo/runs",
        json={"action": " PLAN "},
    )

    assert response.status_code == status.HTTP_201_CREATED
    body = response.json()
    assert body["source"] == "queue"
    assert body["jobId"] == str(queue_job.id)
    assert body["queue"]["requiredCapabilities"] == ["manifest", "qdrant"]
    assert body["queue"]["manifestHash"] == "sha256:abc123"
    called = service.submit_manifest_run.await_args.kwargs
    assert called["action"] == "plan"


def test_create_manifest_run_http_response_includes_temporal_execution_fields(
    client: tuple[TestClient, AsyncMock],
) -> None:
    """HTTP run submissions should surface Temporal task identity for manifest ingests."""

    test_client, service = client
    service.submit_manifest_run.return_value = SimpleNamespace(
        source="temporal",
        status="initializing",
        workflow_id="mm:manifest-123",
        run_id="run-123",
        workflow_type="MoonMind.ManifestIngest",
        temporal_status="running",
        manifest_artifact_ref="art_123",
    )

    response = test_client.post(
        "/api/manifests/demo/runs",
        json={
            "action": "run",
            "title": "Manifest ingest",
            "failurePolicy": "fail_fast",
            "maxConcurrency": 20,
            "tags": {"env": "test"},
            "idempotencyKey": "manifest-run-1",
        },
    )

    assert response.status_code == status.HTTP_201_CREATED
    body = response.json()
    assert body["source"] == "temporal"
    assert body["jobId"] is None
    assert body["queue"] is None
    assert body["execution"]["workflowId"] == "mm:manifest-123"
    assert body["execution"]["taskId"] == "mm:manifest-123"
    assert body["execution"]["temporalRunId"] == "run-123"
    assert body["execution"]["manifestArtifactRef"] == "art_123"
    called = service.submit_manifest_run.await_args.kwargs
    assert called["title"] == "Manifest ingest"
    assert called["failure_policy"] == "fail_fast"
    assert called["max_concurrency"] == 20
    assert called["tags"] == {"env": "test"}
    assert called["idempotency_key"] == "manifest-run-1"
