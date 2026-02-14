"""Unit tests for queue MCP tool registry dispatch."""

from __future__ import annotations

import base64
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from moonmind.mcp.tool_registry import (
    QueueToolExecutionContext,
    QueueToolRegistry,
    ToolArgumentsValidationError,
    ToolNotFoundError,
)
from moonmind.workflows.agent_queue import models
from moonmind.workflows.agent_queue.service import AgentQueueValidationError

pytestmark = [pytest.mark.asyncio, pytest.mark.speckit]


def _build_job(status: models.AgentJobStatus = models.AgentJobStatus.QUEUED):
    now = datetime.now(UTC)
    return SimpleNamespace(
        id=uuid4(),
        type="codex_exec",
        status=status,
        priority=10,
        payload={"instruction": "run"},
        created_by_user_id=uuid4(),
        requested_by_user_id=uuid4(),
        affinity_key="repo/moonmind",
        claimed_by=None,
        lease_expires_at=None,
        next_attempt_at=None,
        attempt=1,
        max_attempts=3,
        result_summary=None,
        error_message=None,
        artifacts_path=None,
        started_at=None,
        finished_at=None,
        created_at=now,
        updated_at=now,
    )


def _build_artifact(job_id=None):
    now = datetime.now(UTC)
    return SimpleNamespace(
        id=uuid4(),
        job_id=job_id or uuid4(),
        name="logs/output.log",
        content_type="text/plain",
        size_bytes=5,
        digest="sha256:abc",
        storage_path="job/logs/output.log",
        created_at=now,
    )


def _build_context(service: SimpleNamespace) -> QueueToolExecutionContext:
    return QueueToolExecutionContext(service=service, user_id=uuid4())


def _build_service() -> SimpleNamespace:
    return SimpleNamespace(
        create_job=AsyncMock(),
        claim_job=AsyncMock(),
        heartbeat=AsyncMock(),
        complete_job=AsyncMock(),
        fail_job=AsyncMock(),
        get_job=AsyncMock(),
        list_jobs=AsyncMock(),
        upload_artifact=AsyncMock(),
    )


def test_list_tools_is_deterministic_and_complete() -> None:
    """Registry discovery should include expected queue tools in deterministic order."""

    registry = QueueToolRegistry()

    tools = registry.list_tools()
    names = [tool.name for tool in tools]

    assert names == sorted(names)
    assert "queue.enqueue" in names
    assert "queue.claim" in names
    assert "queue.upload_artifact" in names


async def test_call_tool_unknown_name_raises() -> None:
    """Unknown tools should raise ToolNotFoundError."""

    registry = QueueToolRegistry()
    service = _build_service()

    with pytest.raises(ToolNotFoundError):
        await registry.call_tool(
            tool="queue.not_real",
            arguments={},
            context=_build_context(service),
        )


async def test_call_tool_invalid_arguments_raise_validation() -> None:
    """Invalid argument payloads should raise ToolArgumentsValidationError."""

    registry = QueueToolRegistry()
    service = _build_service()

    with pytest.raises(ToolArgumentsValidationError):
        await registry.call_tool(
            tool="queue.claim",
            arguments={},
            context=_build_context(service),
        )


async def test_queue_list_dispatch_uses_service_and_rest_shape() -> None:
    """queue.list should dispatch to service and return REST-equivalent envelope."""

    registry = QueueToolRegistry()
    service = _build_service()
    service.list_jobs.return_value = [_build_job()]

    result = await registry.call_tool(
        tool="queue.list",
        arguments={"status": "queued", "limit": 5},
        context=_build_context(service),
    )

    assert "items" in result
    assert len(result["items"]) == 1
    assert result["items"][0]["status"] == "queued"
    service.list_jobs.assert_awaited_once()


async def test_queue_claim_forwards_worker_capabilities() -> None:
    """queue.claim should forward workerCapabilities to queue service."""

    registry = QueueToolRegistry()
    service = _build_service()
    service.claim_job.return_value = None

    result = await registry.call_tool(
        tool="queue.claim",
        arguments={
            "workerId": "executor-01",
            "leaseSeconds": 60,
            "workerCapabilities": ["codex", "git"],
        },
        context=_build_context(service),
    )

    assert result == {"job": None}
    called = service.claim_job.await_args.kwargs
    assert called["worker_capabilities"] == ["codex", "git"]


async def test_queue_upload_artifact_rejects_invalid_base64() -> None:
    """Optional upload tool should reject invalid base64 payloads."""

    registry = QueueToolRegistry()
    service = _build_service()

    with pytest.raises(AgentQueueValidationError):
        await registry.call_tool(
            tool="queue.upload_artifact",
            arguments={
                "jobId": str(uuid4()),
                "name": "logs/output.log",
                "contentBase64": "@@invalid@@",
            },
            context=_build_context(service),
        )


async def test_queue_upload_artifact_decodes_payload_and_dispatches() -> None:
    """Upload tool should decode base64 content before calling service."""

    registry = QueueToolRegistry()
    service = _build_service()
    job_id = uuid4()
    service.upload_artifact.return_value = _build_artifact(job_id=job_id)

    result = await registry.call_tool(
        tool="queue.upload_artifact",
        arguments={
            "jobId": str(job_id),
            "name": "logs/output.log",
            "contentBase64": base64.b64encode(b"hello").decode("utf-8"),
            "contentType": "text/plain",
        },
        context=_build_context(service),
    )

    assert result["jobId"] == str(job_id)
    called_kwargs = service.upload_artifact.await_args.kwargs
    assert called_kwargs["data"] == b"hello"
