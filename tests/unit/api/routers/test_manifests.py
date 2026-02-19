"""Router-level unit tests for manifest registry endpoints."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from api_service.api.routers import manifests as manifests_router
from api_service.services.manifests_service import ManifestRegistryNotFoundError
from moonmind.workflows.agent_queue.manifest_contract import ManifestContractError
from moonmind.workflows.agent_queue.service import AgentQueueValidationError


def _record(**overrides):
    now = datetime.now(UTC)
    base = {
        "name": "demo",
        "version": "v0",
        "content": "version: 'v0'\\nmetadata:\\n  name: demo\\n",
        "content_hash": "sha256:abc",
        "updated_at": now,
        "last_run_job_id": uuid4(),
        "last_run_status": "queued",
        "last_run_started_at": now,
        "last_run_finished_at": None,
        "state_json": {"foo": "bar"},
        "state_updated_at": now,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


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


@pytest.mark.asyncio
async def test_create_manifest_run_returns_queue_metadata() -> None:
    """create_manifest_run should include job id and queue metadata."""

    job = SimpleNamespace(
        id=uuid4(),
        type="manifest",
        payload={"requiredCapabilities": ["manifest"], "manifestHash": "sha256:def"},
    )
    service = AsyncMock()
    service.submit_manifest_run.return_value = job
    user = SimpleNamespace(id=uuid4())

    response = await manifests_router.create_manifest_run(
        name="demo",
        payload=manifests_router.ManifestRunRequest(action="run"),
        service=service,
        user=user,
    )

    assert response.job_id == job.id
    assert response.queue.required_capabilities == ["manifest"]
    service.submit_manifest_run.assert_awaited_once()


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
    service.submit_manifest_run.side_effect = AgentQueueValidationError("bad job")
    user = SimpleNamespace(id=uuid4())

    with pytest.raises(HTTPException) as exc:
        await manifests_router.create_manifest_run(
            name="demo",
            payload=manifests_router.ManifestRunRequest(),
            service=service,
            user=user,
        )
    assert exc.value.status_code == 422
