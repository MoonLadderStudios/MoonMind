from __future__ import annotations

import pytest

from moonmind.workflows.temporal import activity_runtime as activity_runtime_module
from moonmind.workflows.temporal.activity_runtime import TemporalIntegrationActivities


class _Response:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return self._payload


class _Client:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    async def __aenter__(self) -> _Client:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def get(self, _url: str) -> _Response:
        return _Response(self._payload)


@pytest.mark.asyncio
async def test_capability_preflight_reports_exact_ready_worker(monkeypatch) -> None:
    readiness = {
        "ready": True,
        "workflowTypes": ["MoonMind.UserWorkflow", "MoonMind.PRResolver"],
        "taskQueues": ["mm.workflow"],
        "registryFingerprints": ["sha256:registry"],
        "buildIds": ["build-3199"],
    }
    monkeypatch.setattr(
        activity_runtime_module.httpx,
        "AsyncClient",
        lambda **_kwargs: _Client(readiness),
    )

    result = await TemporalIntegrationActivities().worker_verify_workflow_capability(
        {"workflowType": "MoonMind.PRResolver", "taskQueue": "mm.workflow"}
    )

    assert result["available"] is True
    assert result["registryFingerprint"] == "sha256:registry"
    assert result["buildId"] == "build-3199"
    assert result["agentExecutionLaunched"] is False


@pytest.mark.asyncio
async def test_capability_preflight_fails_closed_for_registration_mismatch(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        activity_runtime_module.httpx,
        "AsyncClient",
        lambda **_kwargs: _Client(
            {
                "ready": True,
                "workflowTypes": ["MoonMind.UserWorkflow"],
                "taskQueues": ["mm.workflow"],
                "registryFingerprints": ["sha256:old"],
                "buildIds": ["old-build"],
            }
        ),
    )

    result = await TemporalIntegrationActivities().worker_verify_workflow_capability(
        {"workflowType": "MoonMind.PRResolver", "taskQueue": "mm.workflow"}
    )

    assert result["available"] is False
    assert result["status"] == "blocked_operator"
    assert result["reasonCode"] == "worker_capability_unavailable"
    assert result["observedWorkerBuilds"] == ["old-build"]
    assert result["agentExecutionLaunched"] is False


@pytest.mark.asyncio
async def test_capability_preflight_handles_null_readiness_collections(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        activity_runtime_module.httpx,
        "AsyncClient",
        lambda **_kwargs: _Client(
            {
                "ready": True,
                "workflowTypes": None,
                "taskQueues": None,
                "registryFingerprints": None,
                "buildIds": None,
                "children": None,
            }
        ),
    )

    result = await TemporalIntegrationActivities().worker_verify_workflow_capability(
        {"workflowType": "MoonMind.PRResolver", "taskQueue": "mm.workflow"}
    )

    assert result["available"] is False
    assert result["status"] == "blocked_operator"
    assert result["observedRegistryFingerprints"] == []
    assert result["observedWorkerBuilds"] == []
