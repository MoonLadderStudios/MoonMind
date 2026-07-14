"""HTTP + MCP transport parity for the container-job lifecycle (MoonMind#3259).

These tests prove both authenticated transports call the same
``ContainerJobService`` methods, share readiness gating and error
classification, and expose the same five asynchronous operations.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from api_service.api.routers import container_jobs as http_router
from api_service.api.routers import mcp_tools as mcp_tools_router
from api_service.auth_providers import get_current_user
from api_service.services.container_jobs import (
    ContainerJobIdempotencyConflictError,
    ContainerJobNotFoundError,
)
from moonmind.mcp.tool_registry import QueueToolRegistry
from moonmind.schemas.container_job_models import (
    AuxiliaryOutcome,
    ContainerJobAccepted,
    ContainerJobArtifactPage,
    ContainerJobCancelResult,
    ContainerJobLogPage,
    ContainerJobStatus,
)

pytestmark = [pytest.mark.asyncio]

CURRENT_USER_DEP = get_current_user()
_OWNER_ID = "11111111-1111-1111-1111-111111111111"
_JOB_ID = "container-job:" + "0" * 32


def _submit_arguments(source: str) -> dict[str, Any]:
    return {
        "idempotencyKey": "idem-1",
        "source": {"source": source, "callerRequestId": "req-1"},
        "spec": {
            "image": "alpine",
            "workspaceRef": {"kind": "sandbox", "workspaceId": "run"},
            "resources": {"cpuMillis": 100, "memoryMiB": 64},
        },
    }


class _FakeService:
    """Records calls and returns canonical contract models."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.not_found: set[str] = set()

    async def submit(self, *, owner, request):
        self.calls.append(("submit", (owner, request)))
        if request.source.source == "conflict":
            raise ContainerJobIdempotencyConflictError("different request")
        return ContainerJobAccepted(
            jobId=_JOB_ID, replayed=False, createdAt=datetime.now(timezone.utc)
        )

    async def status(self, *, owner, job_id):
        self.calls.append(("status", (owner, job_id)))
        if job_id in self.not_found:
            raise ContainerJobNotFoundError(job_id)
        return ContainerJobStatus(
            jobId=job_id, state="running", updatedAt=datetime.now(timezone.utc)
        )

    async def logs(self, *, owner, job_id, query=None):
        self.calls.append(("logs", (owner, job_id, query)))
        return ContainerJobLogPage(jobId=job_id, entries=[], nextCursor=None)

    async def artifacts(self, *, owner, job_id, cursor=None, limit=200):
        self.calls.append(("artifacts", (owner, job_id, cursor, limit)))
        return ContainerJobArtifactPage(
            jobId=job_id,
            artifacts=[],
            nextCursor=None,
            publication=AuxiliaryOutcome(state="not_attempted"),
        )

    async def cancel(self, *, owner, job_id, request):
        self.calls.append(("cancel", (owner, job_id, request)))
        return ContainerJobCancelResult(
            jobId=job_id, state="canceling", accepted=True, replayed=False
        )


def _install_fake_service(monkeypatch, module, service) -> None:
    monkeypatch.setattr(
        module, "ContainerJobService", lambda session, artifacts=None: service
    )
    monkeypatch.setattr(
        module, "get_temporal_artifact_service", lambda session: None, raising=False
    )


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setattr(
        mcp_tools_router.settings.feature_flags, "container_jobs_enabled", True
    )


def _empty_namespace() -> SimpleNamespace:
    return SimpleNamespace()


# --------------------------------------------------------------------------- MCP


@pytest.fixture
def mcp_app(monkeypatch) -> FastAPI:
    app = FastAPI()
    app.include_router(mcp_tools_router.router, prefix="/api")
    app.dependency_overrides[CURRENT_USER_DEP] = lambda: SimpleNamespace(id=_OWNER_ID)
    monkeypatch.setattr(mcp_tools_router, "_queue_registry", QueueToolRegistry())
    monkeypatch.setattr(mcp_tools_router, "_jira_registry", None)
    monkeypatch.setattr(mcp_tools_router, "_jules_registry", None)
    app.dependency_overrides[mcp_tools_router.get_async_session] = _empty_namespace
    return app


def _mcp_headers() -> dict[str, str]:
    return {"Accept": "application/json"}


async def test_mcp_tools_list_gated_by_readiness(mcp_app, monkeypatch) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=mcp_app), base_url="http://testserver"
    ) as client:
        disabled = await client.post(
            "/api/mcp",
            headers=_mcp_headers(),
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        )
        assert disabled.status_code == 200
        names = {t["name"] for t in disabled.json()["result"]["tools"]}
        assert not any(n.startswith("container.") for n in names)

        monkeypatch.setattr(
            mcp_tools_router.settings.feature_flags, "container_jobs_enabled", True
        )
        ready = await client.post(
            "/api/mcp",
            headers=_mcp_headers(),
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        )
        names = {t["name"] for t in ready.json()["result"]["tools"]}
        assert {
            "container.submit",
            "container.status",
            "container.logs",
            "container.artifacts",
            "container.cancel",
        }.issubset(names)


async def test_mcp_submit_routes_to_service(mcp_app, monkeypatch, enabled) -> None:
    service = _FakeService()
    _install_fake_service(monkeypatch, mcp_tools_router, service)
    async with AsyncClient(
        transport=ASGITransport(app=mcp_app), base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/api/mcp",
            headers=_mcp_headers(),
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "container.submit",
                    "arguments": _submit_arguments("mcp"),
                },
            },
        )
    assert response.status_code == 200
    result = response.json()["result"]
    assert result["isError"] is False
    assert result["structuredContent"]["jobId"] == _JOB_ID
    assert [c[0] for c in service.calls] == ["submit"]


async def test_mcp_disabled_returns_backend_unavailable(mcp_app, monkeypatch) -> None:
    service = _FakeService()
    _install_fake_service(monkeypatch, mcp_tools_router, service)
    async with AsyncClient(
        transport=ASGITransport(app=mcp_app), base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/api/mcp",
            headers=_mcp_headers(),
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "container.status", "arguments": {"jobId": _JOB_ID}},
            },
        )
    result = response.json()["result"]
    assert result["isError"] is True
    assert result["structuredContent"]["code"] == "backend_unavailable"
    assert service.calls == []


async def test_mcp_not_found_is_classified(mcp_app, monkeypatch, enabled) -> None:
    service = _FakeService()
    service.not_found.add(_JOB_ID)
    _install_fake_service(monkeypatch, mcp_tools_router, service)
    async with AsyncClient(
        transport=ASGITransport(app=mcp_app), base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/api/mcp",
            headers=_mcp_headers(),
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "container.status", "arguments": {"jobId": _JOB_ID}},
            },
        )
    result = response.json()["result"]
    assert result["isError"] is True
    assert result["structuredContent"]["code"] == "job_not_found"


async def test_mcp_invalid_request_is_classified(mcp_app, monkeypatch, enabled) -> None:
    service = _FakeService()
    _install_fake_service(monkeypatch, mcp_tools_router, service)
    bad = _submit_arguments("mcp")
    bad["spec"]["image"] = ""  # violates min_length
    async with AsyncClient(
        transport=ASGITransport(app=mcp_app), base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/api/mcp",
            headers=_mcp_headers(),
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "container.submit", "arguments": bad},
            },
        )
    result = response.json()["result"]
    assert result["isError"] is True
    assert result["structuredContent"]["code"] == "invalid_request"
    assert service.calls == []


# -------------------------------------------------------------------------- HTTP


@pytest.fixture
def http_app(monkeypatch) -> FastAPI:
    app = FastAPI()
    app.include_router(http_router.router)
    app.dependency_overrides[CURRENT_USER_DEP] = lambda: SimpleNamespace(id=_OWNER_ID)
    app.dependency_overrides[http_router.get_async_session] = _empty_namespace
    return app


async def test_http_and_mcp_expose_same_operations(http_app, monkeypatch, enabled) -> None:
    service = _FakeService()
    _install_fake_service(monkeypatch, http_router, service)
    async with AsyncClient(
        transport=ASGITransport(app=http_app), base_url="http://testserver"
    ) as client:
        submit = await client.post(
            "/api/v1/container-jobs", json=_submit_arguments("http")
        )
        assert submit.status_code == 200
        assert submit.json()["jobId"] == _JOB_ID

        st = await client.get(f"/api/v1/container-jobs/{_JOB_ID}")
        assert st.status_code == 200 and st.json()["state"] == "running"

        logs = await client.get(f"/api/v1/container-jobs/{_JOB_ID}/logs?limit=5")
        assert logs.status_code == 200 and logs.json()["entries"] == []

        arts = await client.get(f"/api/v1/container-jobs/{_JOB_ID}/artifacts")
        assert arts.status_code == 200
        assert arts.json()["publication"]["state"] == "not_attempted"

        cancel = await client.post(
            f"/api/v1/container-jobs/{_JOB_ID}/cancel",
            json={"idempotencyKey": "c-1"},
        )
        assert cancel.status_code == 200 and cancel.json()["accepted"] is True

    assert [c[0] for c in service.calls] == [
        "submit",
        "status",
        "logs",
        "artifacts",
        "cancel",
    ]


async def test_http_disabled_returns_503(http_app, monkeypatch) -> None:
    service = _FakeService()
    _install_fake_service(monkeypatch, http_router, service)
    async with AsyncClient(
        transport=ASGITransport(app=http_app), base_url="http://testserver"
    ) as client:
        response = await client.get(f"/api/v1/container-jobs/{_JOB_ID}")
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "backend_unavailable"


async def test_http_not_found_maps_to_404(http_app, monkeypatch, enabled) -> None:
    service = _FakeService()
    service.not_found.add(_JOB_ID)
    _install_fake_service(monkeypatch, http_router, service)
    async with AsyncClient(
        transport=ASGITransport(app=http_app), base_url="http://testserver"
    ) as client:
        response = await client.get(f"/api/v1/container-jobs/{_JOB_ID}")
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "job_not_found"


async def test_http_invalid_submit_maps_to_422(http_app, monkeypatch, enabled) -> None:
    service = _FakeService()
    _install_fake_service(monkeypatch, http_router, service)
    bad = _submit_arguments("http")
    bad["spec"]["image"] = ""  # violates min_length
    async with AsyncClient(
        transport=ASGITransport(app=http_app), base_url="http://testserver"
    ) as client:
        response = await client.post("/api/v1/container-jobs", json=bad)
    assert response.status_code == 422
    assert service.calls == []


async def test_http_submit_rejects_authority_and_credential_fields(
    http_app, monkeypatch, enabled
) -> None:
    service = _FakeService()
    _install_fake_service(monkeypatch, http_router, service)
    async with AsyncClient(
        transport=ASGITransport(app=http_app), base_url="http://testserver"
    ) as client:
        for forbidden in ("dockerHost", "labels", "registryCredentials"):
            payload = _submit_arguments("http")
            payload[forbidden] = "x"
            response = await client.post("/api/v1/container-jobs", json=payload)
            assert response.status_code == 422, forbidden
    assert service.calls == []
