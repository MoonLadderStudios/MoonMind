"""GPU resource round trip across the container-job transports and record.

Qualification for MoonLadderStudios/MoonMind#3779: a generic GPU resource
request submitted over authenticated HTTP or MCP must persist in the durable
API-owned record, replay idempotently, and be projected back as a bounded
observation -- and an unsupported GPU resource must be refused before execution
with the same stable generic class on both transports.

The image, command, and capability values here are fixture data only.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api_service.api.routers import container_jobs as http_router
from api_service.api.routers import mcp_tools as mcp_tools_router
from api_service.auth_providers import get_current_user
from api_service.db.models import Base, ContainerJobRecord
from api_service.services.container_jobs import ContainerJobService
from moonmind.schemas.container_job_models import (
    ContainerJobActivityRequest,
    ContainerJobFailureClass,
    ContainerJobState,
    GpuObservation,
)

pytestmark = [pytest.mark.asyncio]

CURRENT_USER_DEP = get_current_user()
_OWNER_ID = "11111111-1111-1111-1111-111111111111"
FIXTURE_IMAGE = "docker.io/library/qualification-fixture:1.0.0"
FIXTURE_COMMAND = ("sh", "-lc", "probe --emit report.json")
GPU_REQUEST = {
    "vendor": "nvidia",
    "count": 2,
    "capabilities": ["utility", "compute"],
}


def _submit_arguments(
    source: str,
    *,
    gpu: dict[str, Any] | None = None,
    key: str = "idem-gpu-1",
) -> dict[str, Any]:
    resources: dict[str, Any] = {"cpuMillis": 100, "memoryMiB": 64}
    if gpu is not None:
        resources["gpu"] = gpu
    return {
        "idempotencyKey": key,
        "source": {"source": source, "callerRequestId": "req-1"},
        "spec": {
            "image": FIXTURE_IMAGE,
            "command": list(FIXTURE_COMMAND),
            "workspaceRef": {"kind": "sandbox", "workspaceId": "run"},
            "resources": resources,
        },
    }


@pytest_asyncio.fixture
async def session_factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/gpu-jobs.db")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
def temporal():
    adapter = AsyncMock()
    adapter.start_container_job.return_value = None
    adapter.signal_container_job_cancel.return_value = None
    return adapter


def _install_real_service(monkeypatch, module, session_factory, temporal) -> None:
    """Route a transport at the real durable service over a real session."""

    async def session_dependency():
        async with session_factory() as session:
            yield session

    monkeypatch.setattr(
        module,
        "ContainerJobService",
        lambda session, artifacts=None: ContainerJobService(
            session, temporal=temporal, artifacts=None
        ),
    )
    monkeypatch.setattr(
        module, "get_temporal_artifact_service", lambda session: None, raising=False
    )
    return session_dependency


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setattr(
        mcp_tools_router.settings.feature_flags, "container_jobs_enabled", True
    )
    monkeypatch.setattr(
        http_router.settings.feature_flags, "container_jobs_enabled", True
    )


@pytest.fixture
def http_app(monkeypatch, session_factory, temporal) -> FastAPI:
    dependency = _install_real_service(
        monkeypatch, http_router, session_factory, temporal
    )
    app = FastAPI()
    app.include_router(http_router.router)
    app.dependency_overrides[CURRENT_USER_DEP] = lambda: SimpleNamespace(id=_OWNER_ID)
    app.dependency_overrides[http_router.get_async_session] = dependency
    return app


@pytest.fixture
def mcp_app(monkeypatch, session_factory, temporal) -> FastAPI:
    dependency = _install_real_service(
        monkeypatch, mcp_tools_router, session_factory, temporal
    )
    app = FastAPI()
    app.include_router(mcp_tools_router.router, prefix="/api")
    app.dependency_overrides[CURRENT_USER_DEP] = lambda: SimpleNamespace(id=_OWNER_ID)
    monkeypatch.setattr(mcp_tools_router, "_jira_registry", None)
    monkeypatch.setattr(mcp_tools_router, "_jules_registry", None)
    app.dependency_overrides[mcp_tools_router.get_async_session] = dependency
    return app


async def _record(session_factory, job_id: str) -> ContainerJobRecord:
    async with session_factory() as session:
        result = await session.execute(
            select(ContainerJobRecord).where(ContainerJobRecord.job_id == job_id)
        )
        return result.scalar_one()


async def _project(
    monkeypatch, session_factory, *, job_id: str, gpu: GpuObservation | None
) -> None:
    """Run the trusted worker's projection writer for one job."""

    from moonmind.workflows.temporal import worker_runtime

    @asynccontextmanager
    async def session_context():
        async with session_factory() as session:
            yield session

    monkeypatch.setattr(worker_runtime, "get_async_session_context", session_context)
    write = worker_runtime._container_job_projection_writer("docker-engine", "system")
    request = ContainerJobActivityRequest.model_validate(
        {
            "jobId": job_id,
            "ownershipToken": f"{job_id}:v1",
            "request": _submit_arguments("http", gpu=GPU_REQUEST),
            "state": ContainerJobState.SUCCEEDED.value,
            "exitCode": 0,
            "gpuObservation": (
                gpu.model_dump(mode="json", by_alias=True, exclude_none=True)
                if gpu is not None
                else None
            ),
        }
    )
    await write(request)


async def test_http_submit_persists_and_projects_the_gpu_resource(
    http_app, monkeypatch, session_factory, enabled
) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=http_app), base_url="http://testserver"
    ) as client:
        submitted = await client.post(
            "/api/v1/container-jobs",
            json=_submit_arguments("http", gpu=GPU_REQUEST),
        )
        assert submitted.status_code == 200
        job_id = submitted.json()["jobId"]

        record = await _record(session_factory, job_id)
        # The durable request is the caller's own semantic GPU request,
        # normalized to one canonical serialization.
        assert record.request_json["spec"]["resources"]["gpu"] == {
            "vendor": "nvidia",
            "count": 2,
            "capabilities": ["compute", "utility"],
        }
        assert record.gpu_observation_json is None

        await _project(
            monkeypatch,
            session_factory,
            job_id=job_id,
            gpu=GpuObservation(
                vendor="nvidia",
                count=2,
                capabilities=("compute", "utility"),
                backendSupported=True,
                launched=True,
            ),
        )

        status = await client.get(f"/api/v1/container-jobs/{job_id}")

    assert status.status_code == 200
    payload = status.json()
    assert payload["state"] == "succeeded"
    assert payload["gpu"] == {
        "vendor": "nvidia",
        "count": 2,
        "capabilities": ["compute", "utility"],
        "backendSupported": True,
        "launched": True,
        "failureClass": None,
    }
    # The public projection carries no Docker flag, device path, or endpoint.
    assert "--gpus" not in status.text
    assert "deviceRequest" not in status.text


async def test_replayed_gpu_submission_keeps_one_durable_identity(
    http_app, session_factory, enabled
) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=http_app), base_url="http://testserver"
    ) as client:
        first = await client.post(
            "/api/v1/container-jobs",
            json=_submit_arguments("http", gpu=GPU_REQUEST),
        )
        # The same semantic request in a different capability order replays.
        replay = await client.post(
            "/api/v1/container-jobs",
            json=_submit_arguments(
                "http",
                gpu={"vendor": "nvidia", "count": 2, "capabilities": ["compute", "utility"]},
            ),
        )
        conflicting = await client.post(
            "/api/v1/container-jobs",
            json=_submit_arguments(
                "http", gpu={"vendor": "nvidia", "count": 4}
            ),
        )

    assert first.status_code == 200
    assert replay.status_code == 200
    assert replay.json()["jobId"] == first.json()["jobId"]
    assert replay.json()["replayed"] is True
    assert conflicting.status_code == 409


async def test_cpu_only_status_projects_no_gpu_observation(
    http_app, monkeypatch, session_factory, enabled
) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=http_app), base_url="http://testserver"
    ) as client:
        submitted = await client.post(
            "/api/v1/container-jobs", json=_submit_arguments("http")
        )
        job_id = submitted.json()["jobId"]
        record = await _record(session_factory, job_id)
        assert "gpu" not in record.request_json["spec"]["resources"]

        await _project(monkeypatch, session_factory, job_id=job_id, gpu=None)
        status = await client.get(f"/api/v1/container-jobs/{job_id}")

    assert status.status_code == 200
    assert status.json().get("gpu") is None
    record = await _record(session_factory, job_id)
    assert record.gpu_observation_json is None


async def test_mcp_submit_persists_the_gpu_resource(
    mcp_app, session_factory, enabled
) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=mcp_app), base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/api/mcp",
            headers={"Accept": "application/json"},
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "container.submit",
                    "arguments": _submit_arguments("mcp", gpu=GPU_REQUEST),
                },
            },
        )

    result = response.json()["result"]
    assert result["isError"] is False
    record = await _record(session_factory, result["structuredContent"]["jobId"])
    assert record.request_json["spec"]["resources"]["gpu"]["capabilities"] == [
        "compute",
        "utility",
    ]


async def test_mcp_submit_schema_publishes_the_bounded_capability_values(
    mcp_app, enabled
) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=mcp_app), base_url="http://testserver"
    ) as client:
        listed = await client.post(
            "/api/mcp",
            headers={"Accept": "application/json"},
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        )

    tools = {tool["name"]: tool for tool in listed.json()["result"]["tools"]}
    schema = tools["container.submit"]["inputSchema"]
    gpu_schema = schema["$defs"]["WorkloadGpuRequest"]
    assert gpu_schema["properties"]["vendor"]["const"] == "nvidia"
    assert "capabilities" in gpu_schema["properties"]
    assert "devices" not in gpu_schema["properties"]


@pytest.mark.parametrize(
    ("gpu", "expected"),
    [
        ({"vendor": "amd", "count": 1}, ContainerJobFailureClass.GPU_VENDOR_UNSUPPORTED),
        ({"count": 0}, ContainerJobFailureClass.GPU_COUNT_UNSUPPORTED),
        (
            {"count": 1, "capabilities": ["render"]},
            ContainerJobFailureClass.GPU_REQUEST_INVALID,
        ),
    ],
)
async def test_http_refuses_an_unsupported_gpu_resource_with_a_stable_class(
    http_app, session_factory, enabled, gpu: dict[str, Any], expected
) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=http_app), base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/api/v1/container-jobs", json=_submit_arguments("http", gpu=gpu)
        )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == expected.value
    # Refused before any durable identity or Temporal handoff exists.
    async with session_factory() as session:
        assert (await session.execute(select(ContainerJobRecord))).all() == []


@pytest.mark.parametrize(
    ("gpu", "expected"),
    [
        ({"vendor": "amd", "count": 1}, ContainerJobFailureClass.GPU_VENDOR_UNSUPPORTED),
        ({"count": 0}, ContainerJobFailureClass.GPU_COUNT_UNSUPPORTED),
        (
            {"count": 1, "capabilities": ["render"]},
            ContainerJobFailureClass.GPU_REQUEST_INVALID,
        ),
    ],
)
async def test_mcp_refuses_an_unsupported_gpu_resource_with_a_stable_class(
    mcp_app, enabled, gpu: dict[str, Any], expected
) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=mcp_app), base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/api/mcp",
            headers={"Accept": "application/json"},
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "container.submit",
                    "arguments": _submit_arguments("mcp", gpu=gpu),
                },
            },
        )

    result = response.json()["result"]
    assert result["isError"] is True
    assert result["structuredContent"]["code"] == expected.value


async def test_refusal_response_never_echoes_the_rejected_value(
    http_app, enabled
) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=http_app), base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/api/v1/container-jobs",
            json=_submit_arguments(
                "http", gpu={"count": 1, "capabilities": ["not-a-capability"]}
            ),
        )

    assert response.status_code == 422
    assert "not-a-capability" not in response.text


async def test_ordinary_invalid_submission_is_not_classified_as_a_gpu_refusal(
    http_app, enabled
) -> None:
    bad = _submit_arguments("http", gpu=GPU_REQUEST)
    bad["spec"]["image"] = ""
    async with AsyncClient(
        transport=ASGITransport(app=http_app), base_url="http://testserver"
    ) as client:
        response = await client.post("/api/v1/container-jobs", json=bad)

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_request"
