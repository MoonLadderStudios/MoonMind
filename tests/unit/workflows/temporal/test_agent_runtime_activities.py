"""TDD tests for managed runtime activities — Phase 3 canonical return types.

Validates that agent_runtime_status, agent_runtime_cancel, and
agent_runtime_publish_artifacts return typed Pydantic contracts
(AgentRunStatus, AgentRunResult) instead of dict[str, Any] / None.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from pydantic import BaseModel

from moonmind.rag.context_pack import ContextItem, ContextPack

from moonmind.schemas.agent_runtime_models import (
    AgentRunResult,
    AgentRunStatus,
    ManagedRunRecord,
)
from moonmind.schemas.agent_skill_models import (
    AgentSkillProvenance,
    AgentSkillSourceKind,
    ResolvedSkillEntry,
    ResolvedSkillSet,
)
from moonmind.schemas.managed_session_models import (
    CodexManagedSessionArtifactsPublication,
    CodexManagedSessionBinding,
    CodexManagedSessionHandle,
    CodexManagedSessionRecord,
    CodexManagedSessionSnapshot,
    CodexManagedSessionSummary,
    CodexManagedSessionTurnResponse,
    LaunchCodexManagedSessionRequest,
    ManagedSessionEnsureDockerSidecarResponse,
)
from moonmind.schemas.temporal_activity_models import (
    AgentRuntimeCancelInput,
    AgentRuntimeFetchResultInput,
    AgentRuntimeStatusInput,
)
from moonmind.workflows.temporal import client as temporal_client_module
from moonmind.workflows.temporal import activity_runtime as activity_runtime_module
from moonmind.workflows.temporal.activity_runtime import (
    TemporalActivityRuntimeError,
    TemporalAgentRuntimeActivities,
    TemporalIntegrationActivities,
)
from moonmind.workflows.temporal.runtime.managed_session_controller import (
    ManagedSessionReapResult,
)
from moonmind.workflows.temporal.runtime.managed_session_store import ManagedSessionStore
from moonmind.workflows.temporal.runtime.store import ManagedRunStore

pytestmark = [pytest.mark.asyncio]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_store(tmp_path: Path) -> ManagedRunStore:
    return ManagedRunStore(tmp_path / "run_store")


class _StaticArtifactService:
    def __init__(self, payloads: dict[str, bytes]) -> None:
        self._payloads = payloads

    async def read(
        self,
        *,
        artifact_id: str,
        principal: str,
        allow_restricted_raw: bool,
    ) -> tuple[SimpleNamespace, bytes]:
        del principal, allow_restricted_raw
        return SimpleNamespace(artifact_id=artifact_id), self._payloads[artifact_id]


async def test_execution_notify_completion_skips_when_disabled(monkeypatch) -> None:
    monkeypatch.setattr(
        activity_runtime_module.settings.execution_notifications,
        "enabled",
        False,
    )
    monkeypatch.setattr(
        activity_runtime_module.settings.execution_notifications,
        "webhook_url",
        "https://hooks.example.test/notify",
    )

    activities = TemporalAgentRuntimeActivities()
    result = await activities.execution_notify_completion(
        {"workflowId": "wf-1", "result": {"summary": "done"}}
    )

    assert result == {"status": "skipped", "reason": "disabled"}


async def test_execution_notify_completion_scans_emitted_event_not_internal_payload(
    monkeypatch,
) -> None:
    post_calls: list[dict[str, Any]] = []
    email_calls: list[dict[str, Any]] = []

    class _Response:
        def raise_for_status(self) -> None:
            return None

    class _Client:
        def __init__(self, *, timeout: int) -> None:
            self.timeout = timeout

        async def __aenter__(self) -> "_Client":
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def post(
            self,
            url: str,
            *,
            json: dict[str, Any],
            headers: dict[str, str],
        ) -> _Response:
            post_calls.append({"url": url, "json": json, "headers": headers})
            return _Response()

    def fake_send_email(*_args: Any, **kwargs: Any) -> None:
        email_calls.append(kwargs)

    monkeypatch.setattr(
        activity_runtime_module.settings.security,
        "high_security_mode",
        True,
    )
    monkeypatch.setattr(
        activity_runtime_module.settings.execution_notifications,
        "enabled",
        True,
    )
    monkeypatch.setattr(
        activity_runtime_module.settings.execution_notifications,
        "webhook_url",
        "https://hooks.example.test/notify",
    )
    monkeypatch.setattr(
        activity_runtime_module.settings.execution_notifications,
        "authorization",
        None,
    )
    monkeypatch.setattr(
        activity_runtime_module.settings.execution_notifications,
        "email_to",
        "ops@example.test",
    )
    monkeypatch.setattr(
        activity_runtime_module.settings.execution_notifications,
        "email_from",
        "moonmind@example.test",
    )
    monkeypatch.setattr(
        activity_runtime_module.settings.execution_notifications,
        "smtp_host",
        "smtp.example.test",
    )
    monkeypatch.setattr(
        activity_runtime_module.settings.execution_notifications,
        "smtp_port",
        2525,
    )
    monkeypatch.setattr(
        activity_runtime_module.settings.execution_notifications,
        "smtp_username",
        None,
    )
    monkeypatch.setattr(
        activity_runtime_module.settings.execution_notifications,
        "smtp_password",
        None,
    )
    monkeypatch.setattr(
        activity_runtime_module.settings.execution_notifications,
        "smtp_use_tls",
        False,
    )
    monkeypatch.setattr(
        activity_runtime_module.settings.execution_notifications,
        "smtp_use_ssl",
        False,
    )
    monkeypatch.setattr(
        activity_runtime_module.settings.execution_notifications,
        "timeout_seconds",
        5,
    )
    monkeypatch.setattr(activity_runtime_module.httpx, "AsyncClient", _Client)
    monkeypatch.setattr(
        activity_runtime_module,
        "_send_execution_notification_email",
        fake_send_email,
    )

    activities = TemporalAgentRuntimeActivities()
    result = await activities.execution_notify_completion(
        {
            "workflowId": "wf-secret",
            "status": "completed",
            "result": {
                "summary": "safe completion",
                "metadata": {
                    "agentRunId": "agent-run-1",
                    "internalDiagnostics": "token=blocked-secret-value",
                },
            },
        }
    )

    assert result["status"] == "sent"
    assert "blocked-secret-value" not in json.dumps(result)
    assert post_calls[0]["json"]["summary"] == "safe completion"
    assert post_calls[0]["json"]["agentRunId"] == "agent-run-1"
    assert "internalDiagnostics" not in post_calls[0]["json"]
    assert email_calls


async def test_execution_notify_completion_allows_clean_payload_with_high_security(
    monkeypatch,
) -> None:
    calls: list[dict[str, Any]] = []

    class _Response:
        def raise_for_status(self) -> None:
            return None

    class _Client:
        def __init__(self, *, timeout: int) -> None:
            self.timeout = timeout

        async def __aenter__(self) -> "_Client":
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def post(
            self,
            url: str,
            *,
            json: dict[str, Any],
            headers: dict[str, str],
        ) -> _Response:
            calls.append({"url": url, "json": json, "headers": headers})
            return _Response()

    monkeypatch.setattr(
        activity_runtime_module.settings.security,
        "high_security_mode",
        True,
    )
    monkeypatch.setattr(
        activity_runtime_module.settings.execution_notifications,
        "enabled",
        True,
    )
    monkeypatch.setattr(
        activity_runtime_module.settings.execution_notifications,
        "webhook_url",
        "https://hooks.example.test/notify",
    )
    monkeypatch.setattr(
        activity_runtime_module.settings.execution_notifications,
        "authorization",
        None,
    )
    monkeypatch.setattr(
        activity_runtime_module.settings.execution_notifications,
        "email_to",
        "",
    )
    monkeypatch.setattr(
        activity_runtime_module.settings.execution_notifications,
        "email_from",
        "",
    )
    monkeypatch.setattr(
        activity_runtime_module.settings.execution_notifications,
        "smtp_host",
        "",
    )
    monkeypatch.setattr(
        activity_runtime_module.settings.execution_notifications,
        "timeout_seconds",
        5,
    )
    monkeypatch.setattr(activity_runtime_module.httpx, "AsyncClient", _Client)

    activities = TemporalAgentRuntimeActivities()
    result = await activities.execution_notify_completion(
        {
            "workflowId": "wf-clean",
            "status": "completed",
            "result": {"summary": "clean completion"},
        }
    )

    assert result == {"status": "sent", "target": "https://hooks.example.test/notify"}
    assert calls[0]["json"]["summary"] == "clean completion"


async def test_execution_notify_completion_posts_sanitized_payload(monkeypatch) -> None:
    calls: list[dict[str, Any]] = []

    class _Response:
        def raise_for_status(self) -> None:
            return None

    class _Client:
        def __init__(self, *, timeout: int) -> None:
            self.timeout = timeout

        async def __aenter__(self) -> "_Client":
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def post(
            self,
            url: str,
            *,
            json: dict[str, Any],
            headers: dict[str, str],
        ) -> _Response:
            calls.append({"url": url, "json": json, "headers": headers})
            return _Response()

    monkeypatch.setattr(
        activity_runtime_module.settings.execution_notifications,
        "enabled",
        True,
    )
    monkeypatch.setattr(
        activity_runtime_module.settings.execution_notifications,
        "webhook_url",
        "https://hooks.example.test/notify?token=secret",
    )
    monkeypatch.setattr(
        activity_runtime_module.settings.execution_notifications,
        "authorization",
        "Bearer secret",
    )
    monkeypatch.setattr(
        activity_runtime_module.settings.execution_notifications,
        "timeout_seconds",
        7,
    )
    monkeypatch.setattr(
        activity_runtime_module.settings.execution_notifications,
        "email_to",
        None,
    )
    monkeypatch.setattr(
        activity_runtime_module.settings.execution_notifications,
        "email_from",
        None,
    )
    monkeypatch.setattr(
        activity_runtime_module.settings.execution_notifications,
        "smtp_host",
        None,
    )
    monkeypatch.setattr(activity_runtime_module.httpx, "AsyncClient", _Client)

    activities = TemporalAgentRuntimeActivities()
    result = await activities.execution_notify_completion(
        {
            "workflowId": "wf-1",
            "runId": "run-1",
            "agentId": "codex_cli",
            "agentKind": "managed",
            "status": "completed",
            "result": {
                "summary": "token=secret",
                "failureClass": None,
                "metadata": {"agentRunId": "task-1"},
            },
        }
    )

    assert result == {
        "status": "sent",
        "target": "https://hooks.example.test/notify",
    }
    assert calls[0]["headers"]["Authorization"] == "Bearer secret"
    assert calls[0]["json"]["event"] == "moonmind.execution.completed"
    assert calls[0]["json"]["summary"] == "token=[REDACTED]"
    assert calls[0]["json"]["agentRunId"] == "task-1"


async def test_execution_notify_completion_blocks_secret_before_webhook(
    monkeypatch,
) -> None:
    raw_secret = "unit-test-execution-notification-secret"

    class _FailingClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            raise AssertionError("webhook sender must not be called")

    monkeypatch.setattr(
        activity_runtime_module.settings.execution_notifications,
        "enabled",
        True,
    )
    monkeypatch.setattr(
        activity_runtime_module.settings.execution_notifications,
        "webhook_url",
        "https://hooks.example.test/notify?token=secret",
    )
    monkeypatch.setattr(
        activity_runtime_module.settings.execution_notifications,
        "authorization",
        "Bearer secret",
    )
    monkeypatch.setattr(
        activity_runtime_module.settings.execution_notifications,
        "email_to",
        None,
    )
    monkeypatch.setattr(
        activity_runtime_module.settings.execution_notifications,
        "email_from",
        None,
    )
    monkeypatch.setattr(
        activity_runtime_module.settings.execution_notifications,
        "smtp_host",
        None,
    )
    monkeypatch.setattr(
        activity_runtime_module.settings.security,
        "high_security_mode",
        True,
    )
    monkeypatch.setattr(activity_runtime_module.httpx, "AsyncClient", _FailingClient)

    activities = TemporalAgentRuntimeActivities()
    result = await activities.execution_notify_completion(
        {
            "workflowId": "wf-1",
            "runId": "run-1",
            "agentId": "codex_cli",
            "agentKind": "managed",
            "status": "failed",
            "result": {
                "summary": f"Investigate password={raw_secret}",
                "failureClass": "permanent",
                "metadata": {"agentRunId": "agent-run-1"},
            },
        }
    )

    assert result["status"] == "blocked"
    assert "execution.notification.webhook.payload" in result["reason"]
    assert raw_secret not in result["reason"]
    assert result["target"] == "https://hooks.example.test/notify"


async def test_execution_notify_completion_scans_unredacted_webhook_payload(
    monkeypatch,
) -> None:
    raw_secret = "unit-test-unredacted-scan-secret"

    class _FailingClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            raise AssertionError("webhook sender must not be called")

    def mutating_redact(payload: Any) -> Any:
        if isinstance(payload, dict):
            result = payload.get("result")
            if isinstance(result, dict):
                result["summary"] = "summary=[REDACTED]"
        return payload

    def fake_scan(event: Any, *, surface: str) -> str | None:
        assert surface == "execution.notification.webhook.payload"
        if raw_secret in json.dumps(event, sort_keys=True, default=str):
            return f"Blocked outbound content at {surface}"
        return None

    monkeypatch.setattr(
        activity_runtime_module.settings.execution_notifications,
        "enabled",
        True,
    )
    monkeypatch.setattr(
        activity_runtime_module.settings.execution_notifications,
        "webhook_url",
        "https://hooks.example.test/notify",
    )
    monkeypatch.setattr(
        activity_runtime_module.settings.execution_notifications,
        "email_to",
        None,
    )
    monkeypatch.setattr(
        activity_runtime_module.settings.execution_notifications,
        "email_from",
        None,
    )
    monkeypatch.setattr(
        activity_runtime_module.settings.execution_notifications,
        "smtp_host",
        None,
    )
    monkeypatch.setattr(activity_runtime_module.httpx, "AsyncClient", _FailingClient)
    monkeypatch.setattr(
        activity_runtime_module,
        "redact_sensitive_payload",
        mutating_redact,
    )
    monkeypatch.setattr(
        activity_runtime_module,
        "_scan_execution_notification_before_send",
        fake_scan,
    )

    activities = TemporalAgentRuntimeActivities()
    result = await activities.execution_notify_completion(
        {
            "workflowId": "wf-1",
            "runId": "run-1",
            "agentId": "codex_cli",
            "agentKind": "managed",
            "status": "failed",
            "result": {
                "summary": f"Investigate password={raw_secret}",
                "failureClass": "permanent",
                "metadata": {"agentRunId": "agent-run-1"},
            },
        }
    )

    assert result == {
        "status": "blocked",
        "reason": "Blocked outbound content at execution.notification.webhook.payload",
        "target": "https://hooks.example.test/notify",
    }


async def test_execution_notify_completion_blocks_secret_before_email(
    monkeypatch,
) -> None:
    raw_secret = "unit-test-execution-email-secret"

    class _FailingSMTP:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            raise AssertionError("SMTP sender must not be called")

    monkeypatch.setattr(
        activity_runtime_module.settings.execution_notifications,
        "enabled",
        True,
    )
    monkeypatch.setattr(
        activity_runtime_module.settings.execution_notifications,
        "webhook_url",
        None,
    )
    monkeypatch.setattr(
        activity_runtime_module.settings.execution_notifications,
        "email_to",
        "ops@example.test",
    )
    monkeypatch.setattr(
        activity_runtime_module.settings.execution_notifications,
        "email_from",
        "moonmind@example.test",
    )
    monkeypatch.setattr(
        activity_runtime_module.settings.execution_notifications,
        "smtp_host",
        "smtp.example.test",
    )
    monkeypatch.setattr(
        activity_runtime_module.settings.security,
        "high_security_mode",
        True,
    )
    monkeypatch.setattr(activity_runtime_module.smtplib, "SMTP", _FailingSMTP)

    activities = TemporalAgentRuntimeActivities()
    result = await activities.execution_notify_completion(
        {
            "workflowId": "wf-email",
            "runId": "run-email",
            "agentId": "codex_cli",
            "agentKind": "managed",
            "status": "failed",
            "result": {
                "summary": f"Investigate api_key={raw_secret}",
                "failureClass": "permanent",
            },
        }
    )

    assert result["status"] == "blocked"
    assert "execution.notification.email.payload" in result["reason"]
    assert raw_secret not in result["reason"]
    assert result["target"] == "email:1 recipient"


async def test_execution_notify_completion_treats_fallback_block_as_blocked(
    monkeypatch,
) -> None:
    class _FailingClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            raise AssertionError("webhook sender must not be called")

    monkeypatch.setattr(
        activity_runtime_module.settings.execution_notifications,
        "enabled",
        True,
    )
    monkeypatch.setattr(
        activity_runtime_module.settings.execution_notifications,
        "webhook_url",
        "https://hooks.example.test/notify",
    )
    monkeypatch.setattr(
        activity_runtime_module.settings.execution_notifications,
        "email_to",
        None,
    )
    monkeypatch.setattr(
        activity_runtime_module.settings.execution_notifications,
        "email_from",
        None,
    )
    monkeypatch.setattr(
        activity_runtime_module.settings.execution_notifications,
        "smtp_host",
        None,
    )
    monkeypatch.setattr(activity_runtime_module.httpx, "AsyncClient", _FailingClient)
    monkeypatch.setattr(
        activity_runtime_module,
        "_scan_execution_notification_before_send",
        lambda event, *, surface: f"Blocked outbound content at {surface}",
    )

    activities = TemporalAgentRuntimeActivities()
    result = await activities.execution_notify_completion(
        {"workflowId": "wf-1", "status": "failed"}
    )

    assert result == {
        "status": "blocked",
        "reason": "Blocked outbound content at execution.notification.webhook.payload",
        "target": "https://hooks.example.test/notify",
    }

async def test_execution_notify_completion_blocks_webhook_before_send(
    monkeypatch,
) -> None:
    calls: list[dict[str, Any]] = []

    class _Client:
        def __init__(self, *, timeout: int) -> None:
            self.timeout = timeout

        async def __aenter__(self) -> "_Client":
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def post(self, *args: Any, **kwargs: Any) -> object:
            calls.append({"args": args, "kwargs": kwargs})
            raise AssertionError("webhook send should be blocked")

    monkeypatch.setattr(
        activity_runtime_module.settings.security,
        "high_security_mode",
        True,
    )
    monkeypatch.setattr(
        activity_runtime_module.settings.execution_notifications,
        "enabled",
        True,
    )
    monkeypatch.setattr(
        activity_runtime_module.settings.execution_notifications,
        "webhook_url",
        "https://hooks.example.test/notify",
    )
    monkeypatch.setattr(
        activity_runtime_module.settings.execution_notifications,
        "email_to",
        None,
    )
    monkeypatch.setattr(
        activity_runtime_module.settings.execution_notifications,
        "email_from",
        None,
    )
    monkeypatch.setattr(
        activity_runtime_module.settings.execution_notifications,
        "smtp_host",
        None,
    )
    monkeypatch.setattr(activity_runtime_module.httpx, "AsyncClient", _Client)

    activities = TemporalAgentRuntimeActivities()
    result = await activities.execution_notify_completion(
        {
            "workflowId": "wf-1",
            "result": {"summary": "Use token=super-secret-value"},
        }
    )

    assert result["status"] == "blocked"
    assert "execution.notification.webhook.payload" in result["reason"]
    assert calls == []


async def test_execution_notify_completion_sends_email_channel(monkeypatch) -> None:
    calls: list[dict[str, Any]] = []

    class _SMTP:
        def __init__(self, host: str, port: int, *, timeout: int) -> None:
            calls.append(
                {"action": "connect", "host": host, "port": port, "timeout": timeout}
            )

        def __enter__(self) -> "_SMTP":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def starttls(self) -> None:
            calls.append({"action": "starttls"})

        def login(self, username: str, password: str) -> None:
            calls.append(
                {"action": "login", "username": username, "password": password}
            )

        def send_message(self, message: Any) -> None:
            calls.append(
                {
                    "action": "send_message",
                    "from": message["From"],
                    "to": message["To"],
                    "subject": message["Subject"],
                    "body": message.get_content(),
                }
            )

    monkeypatch.setattr(
        activity_runtime_module.settings.execution_notifications,
        "enabled",
        True,
    )
    monkeypatch.setattr(
        activity_runtime_module.settings.execution_notifications,
        "webhook_url",
        None,
    )
    monkeypatch.setattr(
        activity_runtime_module.settings.execution_notifications,
        "email_to",
        "ops@example.test, owner@example.test",
    )
    monkeypatch.setattr(
        activity_runtime_module.settings.execution_notifications,
        "email_from",
        "moonmind@example.test",
    )
    monkeypatch.setattr(
        activity_runtime_module.settings.execution_notifications,
        "smtp_host",
        "smtp.example.test",
    )
    monkeypatch.setattr(
        activity_runtime_module.settings.execution_notifications,
        "smtp_port",
        2525,
    )
    monkeypatch.setattr(
        activity_runtime_module.settings.execution_notifications,
        "smtp_username",
        "smtp-user",
    )
    monkeypatch.setattr(
        activity_runtime_module.settings.execution_notifications,
        "smtp_password",
        "smtp-secret",
    )
    monkeypatch.setattr(
        activity_runtime_module.settings.execution_notifications,
        "smtp_use_tls",
        True,
    )
    monkeypatch.setattr(
        activity_runtime_module.settings.execution_notifications,
        "smtp_use_ssl",
        False,
    )
    monkeypatch.setattr(
        activity_runtime_module.settings.execution_notifications,
        "timeout_seconds",
        9,
    )
    monkeypatch.setattr(activity_runtime_module.smtplib, "SMTP", _SMTP)

    activities = TemporalAgentRuntimeActivities()
    result = await activities.execution_notify_completion(
        {
            "workflowId": "wf-email",
            "runId": "run-email",
            "agentId": "codex_cli",
            "agentKind": "managed",
            "status": "failed",
            "result": {
                "summary": "password=secret",
                "failureClass": "permanent",
                "metadata": {"agentRunId": "task-email"},
            },
        }
    )

    assert result == {"status": "sent", "target": "email:2 recipients"}
    assert calls[0] == {
        "action": "connect",
        "host": "smtp.example.test",
        "port": 2525,
        "timeout": 9,
    }
    assert {"action": "starttls"} in calls
    assert {
        "action": "login",
        "username": "smtp-user",
        "password": "smtp-secret",
    } in calls
    sent = [call for call in calls if call["action"] == "send_message"][0]
    assert sent["from"] == "moonmind@example.test"
    assert sent["to"] == "ops@example.test, owner@example.test"
    assert sent["subject"] == "MoonMind execution failed: wf-email"
    assert "password=[REDACTED]" in sent["body"]
    assert "task-email" in sent["body"]

async def test_execution_notify_completion_blocks_email_before_smtp(
    monkeypatch,
) -> None:
    calls: list[dict[str, Any]] = []

    class _SMTP:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            calls.append({"args": args, "kwargs": kwargs})
            raise AssertionError("smtp send should be blocked")

    monkeypatch.setattr(
        activity_runtime_module.settings.security,
        "high_security_mode",
        True,
    )
    monkeypatch.setattr(
        activity_runtime_module.settings.execution_notifications,
        "enabled",
        True,
    )
    monkeypatch.setattr(
        activity_runtime_module.settings.execution_notifications,
        "webhook_url",
        None,
    )
    monkeypatch.setattr(
        activity_runtime_module.settings.execution_notifications,
        "email_to",
        "ops@example.test",
    )
    monkeypatch.setattr(
        activity_runtime_module.settings.execution_notifications,
        "email_from",
        "moonmind@example.test",
    )
    monkeypatch.setattr(
        activity_runtime_module.settings.execution_notifications,
        "smtp_host",
        "smtp.example.test",
    )
    monkeypatch.setattr(activity_runtime_module.smtplib, "SMTP", _SMTP)

    activities = TemporalAgentRuntimeActivities()
    result = await activities.execution_notify_completion(
        {
            "workflowId": "wf-email",
            "result": {"summary": "Use password=super-secret-value"},
        }
    )

    assert result["status"] == "blocked"
    assert "execution.notification.email.payload" in result["reason"]
    assert calls == []


async def test_execution_notify_completion_continues_to_email_after_webhook_failure(
    monkeypatch,
) -> None:
    email_calls: list[dict[str, Any]] = []

    class _Client:
        def __init__(self, *, timeout: int) -> None:
            self.timeout = timeout

        async def __aenter__(self) -> "_Client":
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def post(
            self,
            url: str,
            *,
            json: dict[str, Any],
            headers: dict[str, str],
        ) -> object:
            del url, json, headers
            raise RuntimeError("token=secret webhook down")

    def fake_send_email(*_args: Any, **kwargs: Any) -> None:
        email_calls.append(kwargs)

    monkeypatch.setattr(
        activity_runtime_module.settings.execution_notifications,
        "enabled",
        True,
    )
    monkeypatch.setattr(
        activity_runtime_module.settings.execution_notifications,
        "webhook_url",
        "https://hooks.example.test/notify?token=secret",
    )
    monkeypatch.setattr(
        activity_runtime_module.settings.execution_notifications,
        "authorization",
        None,
    )
    monkeypatch.setattr(
        activity_runtime_module.settings.execution_notifications,
        "email_to",
        "ops@example.test",
    )
    monkeypatch.setattr(
        activity_runtime_module.settings.execution_notifications,
        "email_from",
        "moonmind@example.test",
    )
    monkeypatch.setattr(
        activity_runtime_module.settings.execution_notifications,
        "smtp_host",
        "smtp.example.test",
    )
    monkeypatch.setattr(
        activity_runtime_module.settings.execution_notifications,
        "smtp_port",
        2525,
    )
    monkeypatch.setattr(
        activity_runtime_module.settings.execution_notifications,
        "smtp_username",
        None,
    )
    monkeypatch.setattr(
        activity_runtime_module.settings.execution_notifications,
        "smtp_password",
        None,
    )
    monkeypatch.setattr(
        activity_runtime_module.settings.execution_notifications,
        "smtp_use_tls",
        False,
    )
    monkeypatch.setattr(
        activity_runtime_module.settings.execution_notifications,
        "smtp_use_ssl",
        False,
    )
    monkeypatch.setattr(
        activity_runtime_module.settings.execution_notifications,
        "timeout_seconds",
        5,
    )
    monkeypatch.setattr(activity_runtime_module.httpx, "AsyncClient", _Client)
    monkeypatch.setattr(
        activity_runtime_module,
        "_send_execution_notification_email",
        fake_send_email,
    )

    activities = TemporalAgentRuntimeActivities()
    result = await activities.execution_notify_completion(
        {"workflowId": "wf-partial", "status": "completed"}
    )

    assert result == {
        "status": "sent",
        "target": "email:1 recipient",
        "errors": [
            {
                "channel": "webhook",
                "reason": "token=[REDACTED] webhook down",
                "target": "https://hooks.example.test/notify",
            }
        ],
    }
    assert len(email_calls) == 1
    assert email_calls[0]["recipients"] == ["ops@example.test"]


async def test_execution_notify_completion_reports_email_failure_after_webhook_success(
    monkeypatch,
) -> None:
    class _Response:
        def raise_for_status(self) -> None:
            return None

    class _Client:
        def __init__(self, *, timeout: int) -> None:
            self.timeout = timeout

        async def __aenter__(self) -> "_Client":
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def post(
            self,
            url: str,
            *,
            json: dict[str, Any],
            headers: dict[str, str],
        ) -> _Response:
            del url, json, headers
            return _Response()

    def fake_send_email(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("password=secret smtp down")

    monkeypatch.setattr(
        activity_runtime_module.settings.execution_notifications,
        "enabled",
        True,
    )
    monkeypatch.setattr(
        activity_runtime_module.settings.execution_notifications,
        "webhook_url",
        "https://hooks.example.test/notify",
    )
    monkeypatch.setattr(
        activity_runtime_module.settings.execution_notifications,
        "authorization",
        None,
    )
    monkeypatch.setattr(
        activity_runtime_module.settings.execution_notifications,
        "email_to",
        "ops@example.test",
    )
    monkeypatch.setattr(
        activity_runtime_module.settings.execution_notifications,
        "email_from",
        "moonmind@example.test",
    )
    monkeypatch.setattr(
        activity_runtime_module.settings.execution_notifications,
        "smtp_host",
        "smtp.example.test",
    )
    monkeypatch.setattr(
        activity_runtime_module.settings.execution_notifications,
        "smtp_port",
        2525,
    )
    monkeypatch.setattr(
        activity_runtime_module.settings.execution_notifications,
        "smtp_username",
        None,
    )
    monkeypatch.setattr(
        activity_runtime_module.settings.execution_notifications,
        "smtp_password",
        None,
    )
    monkeypatch.setattr(
        activity_runtime_module.settings.execution_notifications,
        "smtp_use_tls",
        False,
    )
    monkeypatch.setattr(
        activity_runtime_module.settings.execution_notifications,
        "smtp_use_ssl",
        False,
    )
    monkeypatch.setattr(
        activity_runtime_module.settings.execution_notifications,
        "timeout_seconds",
        5,
    )
    monkeypatch.setattr(activity_runtime_module.httpx, "AsyncClient", _Client)
    monkeypatch.setattr(
        activity_runtime_module,
        "_send_execution_notification_email",
        fake_send_email,
    )

    activities = TemporalAgentRuntimeActivities()
    result = await activities.execution_notify_completion(
        {"workflowId": "wf-partial", "status": "completed"}
    )

    assert result == {
        "status": "sent",
        "target": "https://hooks.example.test/notify",
        "errors": [
            {
                "channel": "email",
                "reason": "password=[REDACTED] smtp down",
                "target": "email:1 recipient",
            }
        ],
    }


async def test_publish_artifacts_notifies_terminal_result(monkeypatch) -> None:
    calls: list[dict[str, Any]] = []

    async def fake_write_json_artifact(*_args: Any, **_kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(artifact_id=f"art-{len(calls)}")

    async def fake_notify(payload: dict[str, Any]) -> dict[str, str]:
        calls.append(payload)
        return {"status": "sent"}

    monkeypatch.setattr(
        activity_runtime_module,
        "_write_json_artifact",
        fake_write_json_artifact,
    )
    activities = TemporalAgentRuntimeActivities(artifact_service=SimpleNamespace())
    monkeypatch.setattr(activities, "execution_notify_completion", fake_notify)

    result = await activities.agent_runtime_publish_artifacts(
        AgentRunResult(
            summary="completed",
            metadata={
                "agentId": "codex_cli",
                "agentKind": "managed",
                "status": "completed",
                "agentRunId": "task-1",
            },
        )
    )

    assert isinstance(result, AgentRunResult)
    assert calls == [
        {
            "workflowId": "",
            "runId": "",
            "agentId": "codex_cli",
            "agentKind": "managed",
            "status": "completed",
            "result": result.model_dump(mode="json", by_alias=True),
        }
    ]


def _save_record(
    store: ManagedRunStore,
    *,
    run_id: str,
    status: str,
    runtime_id: str = "codex_cli",
    failure_class: str | None = None,
    error_message: str | None = None,
    workspace_path: str | None = None,
) -> None:
    store.save(
        ManagedRunRecord(
            runId=run_id,
            agentId=runtime_id,
            runtimeId=runtime_id,
            status=status,
            startedAt=datetime.now(tz=UTC),
            workspacePath=workspace_path,
            failureClass=failure_class,
            errorMessage=error_message,
        )
    )

def _session_record(session_id: str, *, status: str) -> dict[str, Any]:
    return CodexManagedSessionRecord(
        sessionId=session_id,
        sessionEpoch=1,
        agentRunId="wf-run-1",
        containerId=f"container-{session_id}",
        threadId=f"thread-{session_id}",
        runtimeId="codex_cli",
        imageRef="moonmind:latest",
        controlUrl="http://session-control",
        status=status,
        workspacePath="/work/agent_jobs/wf-run-1/repo",
        sessionWorkspacePath="/work/agent_jobs/wf-run-1/session",
        artifactSpoolPath="/work/agent_jobs/wf-run-1/artifacts",
        startedAt=datetime.now(tz=UTC),
    ).model_dump(mode="json", by_alias=True)

# ---------------------------------------------------------------------------
# T1: agent_runtime_status — typed AgentRunStatus return
# ---------------------------------------------------------------------------

async def test_status_running_record_returns_typed_model(tmp_path: Path) -> None:
    """T1.1 — running record yields typed AgentRunStatus."""
    store = _make_store(tmp_path)
    now = datetime.now(tz=UTC)
    store.save(
        ManagedRunRecord(
            runId="run-1",
            agentId="codex_cli",
            runtimeId="codex_cli",
            status="running",
            startedAt=now,
            lastHeartbeatAt=now + timedelta(seconds=30),
            lastLogAt=now + timedelta(seconds=20),
            lastLogOffset=128,
            stdoutArtifactRef="run-1/stdout.log",
            diagnosticsRef="run-1/diagnostics.json",
            activeTurnId="turn-1",
        )
    )

    activities = TemporalAgentRuntimeActivities(run_store=store)
    result = await activities.agent_runtime_status({"run_id": "run-1", "agent_id": "codex_cli"})

    assert isinstance(result, AgentRunStatus), f"Expected AgentRunStatus, got {type(result)}"
    assert result.status == "running"
    assert result.agent_kind == "managed"
    assert result.metadata["runtimeId"] == "codex_cli"
    assert result.metadata["lastHeartbeatAt"] == (now + timedelta(seconds=30)).isoformat()
    assert result.metadata["lastLogAt"] == (now + timedelta(seconds=20)).isoformat()
    assert result.metadata["lastLogOffset"] == 128
    assert result.metadata["stdoutArtifactRef"] == "run-1/stdout.log"
    assert result.metadata["diagnosticsRef"] == "run-1/diagnostics.json"
    assert result.metadata["activeTurnId"] == "turn-1"

async def test_status_completed_record_returns_typed_model(tmp_path: Path) -> None:
    """T1.2 — completed record yields typed AgentRunStatus with correct status."""
    store = _make_store(tmp_path)
    _save_record(store, run_id="run-2", status="completed")

    activities = TemporalAgentRuntimeActivities(run_store=store)
    result = await activities.agent_runtime_status({"run_id": "run-2", "agent_id": "codex_cli"})

    assert isinstance(result, AgentRunStatus)
    assert result.status == "completed"

async def test_status_failed_record_returns_typed_model_with_metadata(tmp_path: Path) -> None:
    """T1.3 — failed record yields typed AgentRunStatus with runtimeId in metadata."""
    store = _make_store(tmp_path)
    _save_record(
        store,
        run_id="run-3",
        status="failed",
        runtime_id="claude_code",
        failure_class="execution_error",
        error_message="Process exited with code 1",
    )

    activities = TemporalAgentRuntimeActivities(run_store=store)
    result = await activities.agent_runtime_status({"run_id": "run-3", "agent_id": "claude_code"})

    assert isinstance(result, AgentRunStatus)
    assert result.status == "failed"
    assert result.metadata is not None
    assert result.metadata.get("runtimeId") == "claude_code"

async def test_status_no_record_returns_optimistic_running(tmp_path: Path) -> None:
    """T1.4 — missing record in store yields stub AgentRunStatus with status='running'."""
    store = _make_store(tmp_path)

    activities = TemporalAgentRuntimeActivities(run_store=store)
    result = await activities.agent_runtime_status({"run_id": "no-such-run", "agent_id": "codex_cli"})

    assert isinstance(result, AgentRunStatus)
    assert result.status == "running"
    assert result.agent_kind == "managed"

async def test_status_missing_run_id_raises_error(tmp_path: Path) -> None:
    """T1.5 — missing run_id raises TemporalActivityRuntimeError."""
    store = _make_store(tmp_path)
    activities = TemporalAgentRuntimeActivities(run_store=store)

    with pytest.raises(TemporalActivityRuntimeError):
        await activities.agent_runtime_status({"agent_id": "codex_cli"})

async def test_status_accepts_typed_request_model(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    _save_record(store, run_id="typed-status-1", status="running")
    activities = TemporalAgentRuntimeActivities(run_store=store)

    result = await activities.agent_runtime_status(
        AgentRuntimeStatusInput(runId="typed-status-1", agentId="codex_cli")
    )

    assert isinstance(result, AgentRunStatus)
    assert result.run_id == "typed-status-1"

async def test_fetch_result_validates_legacy_dict_to_typed_request(
    tmp_path: Path,
) -> None:
    store = _make_store(tmp_path)
    _save_record(store, run_id="typed-fetch-1", status="completed")
    activities = TemporalAgentRuntimeActivities(run_store=store)

    result = await activities.agent_runtime_fetch_result(
        {
            "run_id": "typed-fetch-1",
            "agent_id": "codex_cli",
            "publish_mode": "none",
            "pr_resolver_expected": True,
        }
    )

    assert isinstance(result, AgentRunResult)

async def test_fetch_result_input_defaults_merge_gate_ownership_false() -> None:
    legacy = AgentRuntimeFetchResultInput.model_validate(
        {"run_id": "typed-fetch-legacy", "pr_resolver_expected": True}
    )
    gated = AgentRuntimeFetchResultInput.model_validate(
        {
            "runId": "typed-fetch-gated",
            "prResolverExpected": True,
            "prResolverMergeGateOwned": True,
        }
    )

    assert legacy.pr_resolver_expected is True
    assert legacy.pr_resolver_merge_gate_owned is False
    assert gated.pr_resolver_merge_gate_owned is True

async def test_cancel_accepts_typed_request_model() -> None:
    activities = TemporalAgentRuntimeActivities()

    result = await activities.agent_runtime_cancel(
        AgentRuntimeCancelInput(agentKind="external", runId="external-run-1")
    )

    assert isinstance(result, AgentRunStatus)
    assert result.run_id == "external-run-1"

async def test_external_agent_run_activity_wrapper_rejects_unknown_fields() -> None:
    activities = TemporalAgentRuntimeActivities()

    with pytest.raises(Exception):
        await activities.integration_jules_status(
            {"runId": "jules-1", "rawProviderPayload": {"status": "done"}}
        )

# ---------------------------------------------------------------------------
# T2: agent_runtime_cancel — typed AgentRunStatus return (not None)
# ---------------------------------------------------------------------------

async def test_cancel_with_supervisor_returns_typed_status(tmp_path: Path) -> None:
    """T2.1 — cancel with supervisor returns AgentRunStatus with status='canceled'."""
    mock_supervisor = AsyncMock()
    mock_supervisor.cancel = AsyncMock()

    activities = TemporalAgentRuntimeActivities(
        run_supervisor=mock_supervisor,
    )
    result = await activities.agent_runtime_cancel({"agent_kind": "managed", "run_id": "run-x"})

    assert isinstance(result, AgentRunStatus), f"Expected AgentRunStatus, got {type(result)}"
    assert result.status == "canceled"
    assert result.agent_kind == "managed"

async def test_cancel_supervisor_exception_still_returns_typed_status(tmp_path: Path) -> None:
    """T2.2 — supervisor.cancel raising an exception still yields AgentRunStatus."""
    mock_supervisor = AsyncMock()
    mock_supervisor.cancel = AsyncMock(side_effect=RuntimeError("supervisor failed"))

    activities = TemporalAgentRuntimeActivities(
        run_supervisor=mock_supervisor,
    )
    result = await activities.agent_runtime_cancel({"agent_kind": "managed", "run_id": "run-y"})

    assert isinstance(result, AgentRunStatus)
    assert result.status == "canceled"

async def test_cancel_no_supervisor_store_path_returns_typed_status(tmp_path: Path) -> None:
    """T2.3 — no supervisor but store update still returns AgentRunStatus."""
    store = _make_store(tmp_path)
    _save_record(store, run_id="run-cancel-store", status="running")

    activities = TemporalAgentRuntimeActivities(run_store=store)
    result = await activities.agent_runtime_cancel({"agent_kind": "managed", "run_id": "run-cancel-store"})

    assert isinstance(result, AgentRunStatus)
    assert result.status == "canceled"

async def test_cancel_external_kind_returns_typed_status(tmp_path: Path) -> None:
    """T2.4 — external/unknown kind path still returns AgentRunStatus (best-effort)."""
    activities = TemporalAgentRuntimeActivities()
    result = await activities.agent_runtime_cancel({"agent_kind": "external", "run_id": "ext-run"})

    assert isinstance(result, AgentRunStatus)
    assert result.status == "canceled"

# ---------------------------------------------------------------------------
# T3: agent_runtime_publish_artifacts — typed AgentRunResult return
# ---------------------------------------------------------------------------

async def test_publish_artifacts_no_service_returns_result_unchanged() -> None:
    """T3.1 — no artifact service configured → passthrough (returns input model)."""
    original = AgentRunResult(summary="done", failure_class=None)
    activities = TemporalAgentRuntimeActivities()  # no artifact_service

    result = await activities.agent_runtime_publish_artifacts(original)

    assert isinstance(result, AgentRunResult)
    assert result.summary == "done"


def test_incomplete_pr_resolver_failure_survives_canonical_serialization() -> None:
    """MoonLadderStudios/MoonMind#3141 workflow-boundary regression."""
    original = AgentRunResult(
        summary="pr-resolver execution incomplete",
        failure_class="execution_error",
        retry_recommendation="continue_same_session",
        metadata={
            "failureCode": "INCOMPLETE_TERMINAL_CONTRACT",
            "missingEvidence": ["var/pr_resolver/result.json"],
        },
    )

    restored = AgentRunResult.model_validate(
        original.model_dump(by_alias=True, mode="json")
    )

    assert restored.failure_class == "execution_error"
    assert restored.retry_recommendation == "continue_same_session"
    assert restored.metadata["failureCode"] == "INCOMPLETE_TERMINAL_CONTRACT"

async def test_publish_artifacts_none_input_returns_none() -> None:
    """T3.3 — None input returns None."""
    activities = TemporalAgentRuntimeActivities()
    result = await activities.agent_runtime_publish_artifacts(None)
    assert result is None

async def test_publish_artifacts_stamps_step_metadata_when_context_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_metadata: list[dict[str, object] | None] = []

    async def fake_write_json_artifact(
        _service: object,
        *,
        principal: str,
        payload: object,
        execution_ref: object = None,
        metadata_json: dict[str, object] | None = None,
    ) -> SimpleNamespace:
        del principal, payload, execution_ref
        captured_metadata.append(metadata_json)
        return SimpleNamespace(artifact_id=f"art_{len(captured_metadata)}")

    monkeypatch.setattr(
        activity_runtime_module,
        "_write_json_artifact",
        fake_write_json_artifact,
    )

    activities = TemporalAgentRuntimeActivities(artifact_service=object())
    result = await activities.agent_runtime_publish_artifacts(
        AgentRunResult(
            summary="done",
            metadata={
                "moonmind": {
                    "stepLedger": {
                        "logicalStepId": "delegate-agent",
                        "attempt": 2,
                        "scope": "step",
                    }
                }
            },
        )
    )

    assert isinstance(result, AgentRunResult)
    assert len(captured_metadata) == 2
    assert captured_metadata[0]["step_id"] == "delegate-agent"
    assert captured_metadata[0]["attempt"] == 2
    assert captured_metadata[0]["scope"] == "step"
    assert captured_metadata[1]["step_id"] == "delegate-agent"

async def test_fetch_result_exposes_task_run_and_runtime_artifact_metadata(
    tmp_path: Path,
) -> None:
    store = _make_store(tmp_path)
    store.save(
        ManagedRunRecord(
            runId="550e8400-e29b-41d4-a716-446655440000",
            workflowId="wf-parent-1",
            agentId="codex_cli",
            runtimeId="codex_cli",
            status="completed",
            startedAt=datetime.now(tz=UTC),
            stdoutArtifactRef="art_stdout_1",
            stderrArtifactRef="art_stderr_1",
            mergedLogArtifactRef="art_merged_1",
            diagnosticsRef="art_diag_1",
        )
    )

    activities = TemporalAgentRuntimeActivities(run_store=store)
    result = await activities.agent_runtime_fetch_result(
        {"run_id": "550e8400-e29b-41d4-a716-446655440000", "agent_id": "codex_cli"}
    )

    assert isinstance(result, AgentRunResult)
    assert result.metadata["agentRunId"] == "550e8400-e29b-41d4-a716-446655440000"
    assert result.metadata["stdoutArtifactRef"] == "art_stdout_1"
    assert result.metadata["stderrArtifactRef"] == "art_stderr_1"
    assert result.metadata["mergedLogArtifactRef"] == "art_merged_1"
    assert result.metadata["diagnosticsRef"] == "art_diag_1"

# ---------------------------------------------------------------------------
# T4: session-oriented agent_runtime activities — typed managed-session returns
# ---------------------------------------------------------------------------

async def test_launch_session_requires_session_controller() -> None:
    activities = TemporalAgentRuntimeActivities()

    with pytest.raises(
        TemporalActivityRuntimeError,
        match="session_controller is required for agent_runtime.launch_session",
    ):
        await activities.agent_runtime_launch_session(
            {
                "agentRunId": "task-1",
                "sessionId": "sess-1",
                "threadId": "thread-1",
                "workspacePath": "/work/task/repo",
                "sessionWorkspacePath": "/work/task/session",
                "artifactSpoolPath": "/work/task/artifacts",
                "codexHomePath": "/work/task/codex-home",
                "imageRef": "moonmind:latest",
            }
        )

async def test_launch_session_delegates_to_remote_session_controller() -> None:
    controller = AsyncMock()
    controller.launch_session = AsyncMock(
        return_value=CodexManagedSessionHandle(
            sessionState={
                "sessionId": "sess-1",
                "sessionEpoch": 1,
                "containerId": "ctr-1",
                "threadId": "thread-1",
            },
            status="ready",
            imageRef="moonmind:latest",
        )
    )
    activities = TemporalAgentRuntimeActivities(session_controller=controller)

    result = await activities.agent_runtime_launch_session(
        {
            "agentRunId": "task-1",
            "sessionId": "sess-1",
            "threadId": "thread-1",
            "workspacePath": "/work/task/repo",
            "sessionWorkspacePath": "/work/task/session",
            "artifactSpoolPath": "/work/task/artifacts",
            "codexHomePath": "/work/task/codex-home",
            "imageRef": "moonmind:latest",
        }
    )

    assert isinstance(result, CodexManagedSessionHandle)
    assert result.session_state.container_id == "ctr-1"
    controller.launch_session.assert_awaited_once()

async def test_mm866_ensure_docker_sidecar_delegates_to_remote_controller() -> None:
    controller = AsyncMock()
    controller.ensure_docker_sidecar = AsyncMock(
        return_value=ManagedSessionEnsureDockerSidecarResponse(
            state="ready",
            dockerHost="unix:///var/run/moonmind-docker/docker.sock",
            mode="sidecar-dind",
            composeAvailable=True,
            daemon={"ready": True, "version": "27.0.0"},
        )
    )
    activities = TemporalAgentRuntimeActivities(session_controller=controller)

    result = await activities.agent_runtime_ensure_docker_sidecar(
        {
            "sessionId": "sess-1",
            "sessionEpoch": 1,
            "containerId": "ctr-1",
            "threadId": "thread-1",
            "reason": "repo uses docker compose for tests",
            "composeRequired": True,
        }
    )

    assert result.state == "ready"
    assert result.compose_available is True
    controller.ensure_docker_sidecar.assert_awaited_once()

async def test_launch_session_heartbeats_while_waiting_for_remote_session_controller(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from temporalio import activity as temporal_activity

    heartbeats: list[dict[str, Any]] = []

    async def _slow_launch_session(
        _request: Any,
    ) -> CodexManagedSessionHandle:
        await asyncio.sleep(0.03)
        return CodexManagedSessionHandle(
            sessionState={
                "sessionId": "sess-1",
                "sessionEpoch": 1,
                "containerId": "ctr-1",
                "threadId": "thread-1",
            },
            status="ready",
            imageRef="moonmind:latest",
        )

    monkeypatch.setattr(
        activity_runtime_module,
        "_SESSION_CONTROLLER_HEARTBEAT_INTERVAL_SECONDS",
        0.01,
    )
    monkeypatch.setattr(temporal_activity, "in_activity", lambda: True)
    monkeypatch.setattr(temporal_activity, "heartbeat", heartbeats.append)

    controller = AsyncMock()
    controller.launch_session = AsyncMock(side_effect=_slow_launch_session)
    activities = TemporalAgentRuntimeActivities(session_controller=controller)

    result = await activities.agent_runtime_launch_session(
        {
            "agentRunId": "task-1",
            "sessionId": "sess-1",
            "threadId": "thread-1",
            "workspacePath": "/work/task/repo",
            "sessionWorkspacePath": "/work/task/session",
            "artifactSpoolPath": "/work/task/artifacts",
            "codexHomePath": "/work/task/codex-home",
            "imageRef": "moonmind:latest",
        }
    )

    assert isinstance(result, CodexManagedSessionHandle)
    assert result.status == "ready"
    assert heartbeats
    assert all(
        heartbeat == {
            "activityType": "agent_runtime.launch_session",
            "agentRunId": "task-1",
            "runtimeFamily": "codex",
            "sessionId": "sess-1",
            "threadId": "thread-1",
        }
        for heartbeat in heartbeats
    )

async def test_launch_session_uses_github_descriptor_from_activity_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "ghp-ambient-token")
    controller = AsyncMock()
    controller.launch_session = AsyncMock(
        return_value=CodexManagedSessionHandle(
            sessionState={
                "sessionId": "sess-1",
                "sessionEpoch": 1,
                "containerId": "ctr-1",
                "threadId": "thread-1",
            },
            status="ready",
            imageRef="moonmind:latest",
        )
    )
    activities = TemporalAgentRuntimeActivities(session_controller=controller)

    await activities.agent_runtime_launch_session(
        {
            "agentRunId": "task-1",
            "sessionId": "sess-1",
            "threadId": "thread-1",
            "workspacePath": "/work/task/repo",
            "sessionWorkspacePath": "/work/task/session",
            "artifactSpoolPath": "/work/task/artifacts",
            "codexHomePath": "/work/task/codex-home",
            "imageRef": "moonmind:latest",
            "environment": {"PATH": "/usr/bin"},
            "workspaceSpec": {"repository": "MoonLadderStudios/private-repo"},
        }
    )

    launched_request = controller.launch_session.await_args.args[0]
    assert launched_request.environment["PATH"] == "/usr/bin"
    assert "GITHUB_TOKEN" not in launched_request.environment
    assert "GIT_TERMINAL_PROMPT" not in launched_request.environment
    assert launched_request.github_credential is not None
    assert launched_request.github_credential.source == "environment"
    assert launched_request.github_credential.env_var == "GITHUB_TOKEN"

async def test_launch_session_preserves_request_scoped_github_token_for_controller() -> None:
    token = "ghp_request_scoped_token_12345678901234567890"
    controller = AsyncMock()
    controller.launch_session = AsyncMock(
        return_value=CodexManagedSessionHandle(
            sessionState={
                "sessionId": "sess-1",
                "sessionEpoch": 1,
                "containerId": "ctr-1",
                "threadId": "thread-1",
            },
            status="ready",
            imageRef="moonmind:latest",
        )
    )
    activities = TemporalAgentRuntimeActivities(session_controller=controller)

    await activities.agent_runtime_launch_session(
        {
            "agentRunId": "task-1",
            "sessionId": "sess-1",
            "threadId": "thread-1",
            "workspacePath": "/work/task/repo",
            "sessionWorkspacePath": "/work/task/session",
            "artifactSpoolPath": "/work/task/artifacts",
            "codexHomePath": "/work/task/codex-home",
            "imageRef": "moonmind:latest",
            "environment": {"GITHUB_TOKEN": token},
            "workspaceSpec": {"repository": "MoonLadderStudios/private-repo"},
        }
    )

    launched_request = controller.launch_session.await_args.args[0]
    assert launched_request.environment["GITHUB_TOKEN"] == token
    assert "GIT_TERMINAL_PROMPT" not in launched_request.environment
    assert launched_request.github_credential is not None
    assert launched_request.github_credential.source == "environment"
    assert launched_request.github_credential.env_var == "GITHUB_TOKEN"

async def test_launch_session_injects_moonmind_url_from_activity_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MOONMIND_URL", "http://api:8000")
    controller = AsyncMock()
    controller.launch_session = AsyncMock(
        return_value=CodexManagedSessionHandle(
            sessionState={
                "sessionId": "sess-1",
                "sessionEpoch": 1,
                "containerId": "ctr-1",
                "threadId": "thread-1",
            },
            status="ready",
            imageRef="moonmind:latest",
        )
    )
    activities = TemporalAgentRuntimeActivities(session_controller=controller)

    await activities.agent_runtime_launch_session(
        {
            "agentRunId": "task-1",
            "sessionId": "sess-1",
            "threadId": "thread-1",
            "workspacePath": "/work/task/repo",
            "sessionWorkspacePath": "/work/task/session",
            "artifactSpoolPath": "/work/task/artifacts",
            "codexHomePath": "/work/task/codex-home",
            "imageRef": "moonmind:latest",
            "environment": {"PATH": "/usr/bin"},
        }
    )

    launched_request = controller.launch_session.await_args.args[0]
    assert launched_request.environment["PATH"] == "/usr/bin"
    assert launched_request.environment["MOONMIND_URL"] == "http://api:8000"

async def test_launch_session_injects_unreal_image_refs_from_activity_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pinned = "ghcr.io/moonladderstudios/tactics-ue-base@sha256:abc123"
    monkeypatch.setenv("MOONMIND_UNREAL_ENGINE_IMAGE", pinned)
    monkeypatch.setenv("MOONMIND_DOCKER_PREFLIGHT_IMAGE_REF", pinned)
    controller = AsyncMock()
    controller.launch_session = AsyncMock(
        return_value=CodexManagedSessionHandle(
            sessionState={
                "sessionId": "sess-1",
                "sessionEpoch": 1,
                "containerId": "ctr-1",
                "threadId": "thread-1",
            },
            status="ready",
            imageRef="moonmind:latest",
        )
    )
    activities = TemporalAgentRuntimeActivities(session_controller=controller)

    await activities.agent_runtime_launch_session(
        {
            "agentRunId": "task-1",
            "sessionId": "sess-1",
            "threadId": "thread-1",
            "workspacePath": "/work/task/repo",
            "sessionWorkspacePath": "/work/task/session",
            "artifactSpoolPath": "/work/task/artifacts",
            "codexHomePath": "/work/task/codex-home",
            "imageRef": "moonmind:latest",
            "environment": {"PATH": "/usr/bin"},
        }
    )

    launched_request = controller.launch_session.await_args.args[0]
    assert launched_request.environment["MOONMIND_UNREAL_ENGINE_IMAGE"] == pinned
    assert launched_request.environment["MOONMIND_DOCKER_PREFLIGHT_IMAGE_REF"] == pinned

async def test_launch_session_uses_github_descriptor_for_managed_secret_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    from moonmind.config.settings import settings as app_settings

    monkeypatch.setattr(app_settings.github, "github_token_secret_ref", None)
    controller = AsyncMock()
    controller.launch_session = AsyncMock(
        return_value=CodexManagedSessionHandle(
            sessionState={
                "sessionId": "sess-1",
                "sessionEpoch": 1,
                "containerId": "ctr-1",
                "threadId": "thread-1",
            },
            status="ready",
            imageRef="moonmind:latest",
        )
    )
    activities = TemporalAgentRuntimeActivities(session_controller=controller)

    await activities.agent_runtime_launch_session(
        {
            "agentRunId": "task-1",
            "sessionId": "sess-1",
            "threadId": "thread-1",
            "workspacePath": "/work/task/repo",
            "sessionWorkspacePath": "/work/task/session",
            "artifactSpoolPath": "/work/task/artifacts",
            "codexHomePath": "/work/task/codex-home",
            "imageRef": "moonmind:latest",
            "workspaceSpec": {"repository": "MoonLadderStudios/private-repo"},
        }
    )

    launched_request = controller.launch_session.await_args.args[0]
    assert "GITHUB_TOKEN" not in launched_request.environment
    assert "GIT_TERMINAL_PROMPT" not in launched_request.environment
    assert launched_request.github_credential is not None
    assert launched_request.github_credential.source == "managed_secret"
    assert launched_request.github_credential.required is False

async def test_launch_session_preserves_explicit_github_secret_ref_descriptor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from moonmind.config.settings import settings as app_settings

    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr(
        app_settings.github,
        "github_token_secret_ref",
        "env://MM320_GITHUB_PAT",
    )
    controller = AsyncMock()
    controller.launch_session = AsyncMock(
        return_value=CodexManagedSessionHandle(
            sessionState={
                "sessionId": "sess-1",
                "sessionEpoch": 1,
                "containerId": "ctr-1",
                "threadId": "thread-1",
            },
            status="ready",
            imageRef="moonmind:latest",
        )
    )
    activities = TemporalAgentRuntimeActivities(session_controller=controller)

    await activities.agent_runtime_launch_session(
        {
            "agentRunId": "task-1",
            "sessionId": "sess-1",
            "threadId": "thread-1",
            "workspacePath": "/work/task/repo",
            "sessionWorkspacePath": "/work/task/session",
            "artifactSpoolPath": "/work/task/artifacts",
            "codexHomePath": "/work/task/codex-home",
            "imageRef": "moonmind:latest",
            "workspaceSpec": {"repository": "MoonLadderStudios/private-repo"},
        }
    )

    launched_request = controller.launch_session.await_args.args[0]
    assert launched_request.github_credential is not None
    assert launched_request.github_credential.source == "secret_ref"
    assert (
        launched_request.github_credential.secret_ref
        == "env://MM320_GITHUB_PAT"
    )
    assert "GITHUB_TOKEN" not in launched_request.environment

async def test_launch_session_redacts_github_token_in_failure_details() -> None:
    controller = AsyncMock()
    controller.launch_session = AsyncMock(
        side_effect=RuntimeError(
            "docker run -e GITHUB_TOKEN=ghp_inline_secret_token_12345678901234567890 failed"
        )
    )
    activities = TemporalAgentRuntimeActivities(session_controller=controller)

    with pytest.raises(
        TemporalActivityRuntimeError,
        match="agent_runtime\\.launch_session failed:",
    ) as exc_info:
        await activities.agent_runtime_launch_session(
            {
                "agentRunId": "task-1",
                "sessionId": "sess-1",
                "threadId": "thread-1",
                "workspacePath": "/work/task/repo",
                "sessionWorkspacePath": "/work/task/session",
                "artifactSpoolPath": "/work/task/artifacts",
                "codexHomePath": "/work/task/codex-home",
                "imageRef": "moonmind:latest",
                "environment": {"PATH": "/usr/bin"},
            }
        )

    message = str(exc_info.value)
    assert "ghp_inline_secret_token_12345678901234567890" not in message
    assert "[REDACTED]" in message

async def test_launch_session_materializes_profile_into_request_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-123")
    controller = AsyncMock()
    controller.launch_session = AsyncMock(
        return_value=CodexManagedSessionHandle(
            sessionState={
                "sessionId": "sess-1",
                "sessionEpoch": 1,
                "containerId": "ctr-1",
                "threadId": "thread-1",
            },
            status="ready",
            imageRef="moonmind:latest",
        )
    )
    activities = TemporalAgentRuntimeActivities(session_controller=controller)
    codex_home_path = tmp_path / "task-1" / ".moonmind" / "codex-home"

    await activities.agent_runtime_launch_session(
        {
            "request": {
                "agentRunId": "task-1",
                "sessionId": "sess-1",
                "threadId": "thread-1",
                "workspacePath": str(tmp_path / "task-1" / "repo"),
                "sessionWorkspacePath": str(tmp_path / "task-1" / "session"),
                "artifactSpoolPath": str(tmp_path / "task-1" / "artifacts"),
                "codexHomePath": str(codex_home_path),
                "imageRef": "moonmind:latest",
                "environment": {"MANAGED_ACCOUNT_LABEL": "Codex CLI via OpenRouter"},
            },
            "profile": {
                "runtimeId": "codex_cli",
                "profileId": "codex_openrouter_qwen36_plus",
                "providerId": "openrouter",
                "credentialSource": "secret_ref",
                "envTemplate": {
                    "OPENAI_BASE_URL": "https://openrouter.ai/api/v1",
                    "OPENROUTER_API_KEY": {
                        "from_secret_ref": "provider_api_key"
                    },
                },
                "secretRefs": {
                    "provider_api_key": "env://OPENROUTER_API_KEY"
                },
                "homePathOverrides": {
                    "CODEX_HOME": "{{runtime_support_dir}}/codex-home"
                },
                "fileTemplates": [
                    {
                        "path": "{{runtime_support_dir}}/codex-home/config.toml",
                        "contentTemplate": {"model": "qwen/qwen3.6-plus"},
                        "format": "toml",
                    }
                ],
            },
        }
    )

    launched_request = controller.launch_session.await_args.args[0]
    assert launched_request.environment["OPENROUTER_API_KEY"] == "sk-or-123"
    assert launched_request.environment["OPENAI_BASE_URL"] == "https://openrouter.ai/api/v1"
    assert launched_request.environment["CODEX_HOME"] == str(codex_home_path)
    assert launched_request.environment["MANAGED_ACCOUNT_LABEL"] == "Codex CLI via OpenRouter"
    assert (codex_home_path / "config.toml").is_file()
    assert "qwen/qwen3.6-plus" in (codex_home_path / "config.toml").read_text(
        encoding="utf-8"
    )

async def test_launch_session_returns_safe_auth_diagnostics_metadata(
    tmp_path: Path,
) -> None:
    controller = AsyncMock()
    controller.launch_session = AsyncMock(
        return_value=CodexManagedSessionHandle(
            sessionState={
                "sessionId": "sess-1",
                "sessionEpoch": 1,
                "containerId": "ctr-1",
                "threadId": "thread-1",
            },
            status="ready",
            imageRef="moonmind:latest",
            metadata={"vendorThreadId": "vendor-thread-1"},
        )
    )
    activities = TemporalAgentRuntimeActivities(session_controller=controller)
    codex_home_path = tmp_path / "task-1" / ".moonmind" / "codex-home"

    result = await activities.agent_runtime_launch_session(
        {
            "request": {
                "agentRunId": "task-1",
                "sessionId": "sess-1",
                "threadId": "thread-1",
                "workspacePath": str(tmp_path / "task-1" / "repo"),
                "sessionWorkspacePath": str(tmp_path / "task-1" / "session"),
                "artifactSpoolPath": str(tmp_path / "task-1" / "artifacts"),
                "codexHomePath": str(codex_home_path),
                "imageRef": "moonmind:latest",
                "environment": {"MANAGED_AUTH_VOLUME_PATH": "/home/app/.codex-auth"},
            },
            "profile": {
                "runtimeId": "codex_cli",
                "profileId": "codex-oauth",
                "providerId": "openai",
                "credentialSource": "oauth_volume",
                "runtimeMaterializationMode": "oauth_home",
                "volumeRef": "codex_auth_volume",
                "volumeMountPath": "/home/app/.codex-auth",
            },
        }
    )

    diagnostics = result.metadata["authDiagnostics"]
    assert result.metadata["vendorThreadId"] == "vendor-thread-1"
    assert diagnostics == {
        "component": "managed_session_controller",
        "readiness": "ready",
        "profileRef": "codex-oauth",
        "runtimeId": "codex_cli",
        "providerId": "openai",
        "credentialSource": "oauth_volume",
        "runtimeMaterializationMode": "oauth_home",
        "volumeRef": "codex_auth_volume",
        "authMountTarget": "/home/app/.codex-auth",
        "codexHomePath": str(codex_home_path),
    }
    assert "auth.json" not in str(result.metadata)
    assert "token=" not in str(result.metadata)

async def test_launch_session_accepts_mapping_response_before_auth_diagnostics(
    tmp_path: Path,
) -> None:
    controller = AsyncMock()
    controller.launch_session = AsyncMock(
        return_value={
            "sessionState": {
                "sessionId": "sess-1",
                "sessionEpoch": 1,
                "containerId": "ctr-1",
                "threadId": "thread-1",
            },
            "status": "ready",
            "imageRef": "moonmind:latest",
            "metadata": {"vendorThreadId": "vendor-thread-1"},
        }
    )
    activities = TemporalAgentRuntimeActivities(session_controller=controller)
    codex_home_path = tmp_path / "task-1" / ".moonmind" / "codex-home"

    result = await activities.agent_runtime_launch_session(
        {
            "request": {
                "agentRunId": "task-1",
                "sessionId": "sess-1",
                "threadId": "thread-1",
                "workspacePath": str(tmp_path / "task-1" / "repo"),
                "sessionWorkspacePath": str(tmp_path / "task-1" / "session"),
                "artifactSpoolPath": str(tmp_path / "task-1" / "artifacts"),
                "codexHomePath": str(codex_home_path),
                "imageRef": "moonmind:latest",
            },
            "profile": {
                "runtimeId": "codex_cli",
                "profileId": "codex-oauth",
                "providerId": "openai",
                "credentialSource": "oauth_volume",
                "runtimeMaterializationMode": "oauth_home",
                "volumeRef": "codex_auth_volume",
                "volumeMountPath": "/home/app/.codex-auth",
            },
        }
    )

    assert isinstance(result, CodexManagedSessionHandle)
    assert result.metadata["vendorThreadId"] == "vendor-thread-1"
    assert result.metadata["authDiagnostics"]["profileRef"] == "codex-oauth"
    assert result.metadata["authDiagnostics"]["readiness"] == "ready"

async def test_launch_session_failure_reports_sanitized_auth_diagnostics(
    tmp_path: Path,
) -> None:
    controller = AsyncMock()
    controller.launch_session = AsyncMock(
        side_effect=RuntimeError(
            "MANAGED_AUTH_VOLUME_PATH /home/app/.codex-auth/auth.json token=sk-test-secret failed"
        )
    )
    activities = TemporalAgentRuntimeActivities(session_controller=controller)

    with pytest.raises(
        TemporalActivityRuntimeError,
        match="component=managed_session_controller",
    ) as exc_info:
        await activities.agent_runtime_launch_session(
            {
                "request": {
                    "agentRunId": "task-1",
                    "sessionId": "sess-1",
                    "threadId": "thread-1",
                    "workspacePath": str(tmp_path / "task-1" / "repo"),
                    "sessionWorkspacePath": str(tmp_path / "task-1" / "session"),
                    "artifactSpoolPath": str(tmp_path / "task-1" / "artifacts"),
                    "codexHomePath": str(
                        tmp_path / "task-1" / ".moonmind" / "codex-home"
                    ),
                    "imageRef": "moonmind:latest",
                    "environment": {"MANAGED_AUTH_VOLUME_PATH": "/home/app/.codex-auth"},
                },
                "profile": {
                    "runtimeId": "codex_cli",
                    "profileId": "codex-oauth",
                    "providerId": "openai",
                    "credentialSource": "oauth_volume",
                    "runtimeMaterializationMode": "oauth_home",
                    "volumeRef": "codex_auth_volume",
                    "volumeMountPath": "/home/app/.codex-auth",
                },
            }
        )

    message = str(exc_info.value)
    assert "sk-test-secret" not in message
    assert "/home/app/.codex-auth/auth.json" not in message
    assert "[REDACTED]" in message
    assert "[REDACTED_AUTH_PATH]" in message

async def test_launch_session_rejects_structured_secret_ref_values(
    tmp_path: Path,
) -> None:
    controller = AsyncMock()
    controller.launch_session = AsyncMock()
    activities = TemporalAgentRuntimeActivities(session_controller=controller)

    with pytest.raises(
        ValueError,
        match="profile.secretRefs.provider_api_key must be a string secret reference",
    ):
        await activities.agent_runtime_launch_session(
            {
                "request": {
                    "agentRunId": "task-1",
                    "sessionId": "sess-1",
                    "threadId": "thread-1",
                    "workspacePath": str(tmp_path / "task-1" / "repo"),
                    "sessionWorkspacePath": str(tmp_path / "task-1" / "session"),
                    "artifactSpoolPath": str(tmp_path / "task-1" / "artifacts"),
                    "codexHomePath": str(
                        tmp_path / "task-1" / ".moonmind" / "codex-home"
                    ),
                    "imageRef": "moonmind:latest",
                },
                "profile": {
                    "runtimeId": "codex_cli",
                    "envTemplate": {
                        "OPENROUTER_API_KEY": {
                            "from_secret_ref": "provider_api_key"
                        }
                    },
                    "secretRefs": {
                        "provider_api_key": {
                            "ref": "env://OPENROUTER_API_KEY"
                        }
                    },
                },
            }
        )

    controller.launch_session.assert_not_awaited()

async def test_load_session_snapshot_queries_session_workflow_via_client_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow_handle = AsyncMock()
    workflow_handle.query = AsyncMock(
        return_value=CodexManagedSessionSnapshot(
            binding=CodexManagedSessionBinding(
                workflowId="wf-task-1:session:codex_cli",
                agentRunId="wf-task-1",
                sessionId="sess:wf-task-1:codex_cli",
                sessionEpoch=1,
                runtimeId="codex_cli",
                executionProfileRef="codex-default",
            ),
            status="active",
            containerId="ctr-1",
            threadId="thread-1",
            activeTurnId=None,
            terminationRequested=False,
        ).model_dump(mode="json", by_alias=True)
    )
    created_adapters: list[object] = []

    class _FakeTemporalClientAdapter:
        def __init__(self) -> None:
            created_adapters.append(self)

        async def get_workflow_handle(self, workflow_id: str) -> AsyncMock:
            assert workflow_id == "wf-task-1:session:codex_cli"
            return workflow_handle

    monkeypatch.setattr(
        temporal_client_module,
        "TemporalClientAdapter",
        _FakeTemporalClientAdapter,
    )

    activities = TemporalAgentRuntimeActivities()
    result = await activities.agent_runtime_load_session_snapshot(
        {
            "workflowId": "wf-task-1:session:codex_cli",
            "agentRunId": "wf-task-1",
            "sessionId": "sess:wf-task-1:codex_cli",
            "sessionEpoch": 1,
            "runtimeId": "codex_cli",
            "executionProfileRef": "codex-default",
        }
    )

    assert isinstance(result, CodexManagedSessionSnapshot)
    assert result.binding.workflow_id == "wf-task-1:session:codex_cli"
    assert result.container_id == "ctr-1"
    assert len(created_adapters) == 1
    workflow_handle.query.assert_awaited_once_with("get_status")

async def test_load_session_snapshot_reuses_client_adapter_across_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow_handle = AsyncMock()
    workflow_handle.query = AsyncMock(
        return_value=CodexManagedSessionSnapshot(
            binding=CodexManagedSessionBinding(
                workflowId="wf-task-1:session:codex_cli",
                agentRunId="wf-task-1",
                sessionId="sess:wf-task-1:codex_cli",
                sessionEpoch=1,
                runtimeId="codex_cli",
                executionProfileRef="codex-default",
            ),
            status="active",
            containerId="ctr-1",
            threadId="thread-1",
            activeTurnId=None,
            terminationRequested=False,
        ).model_dump(mode="json", by_alias=True)
    )
    created_adapters: list[object] = []

    class _FakeTemporalClientAdapter:
        def __init__(self) -> None:
            created_adapters.append(self)

        async def get_workflow_handle(self, workflow_id: str) -> AsyncMock:
            assert workflow_id == "wf-task-1:session:codex_cli"
            return workflow_handle

    monkeypatch.setattr(
        temporal_client_module,
        "TemporalClientAdapter",
        _FakeTemporalClientAdapter,
    )

    activities = TemporalAgentRuntimeActivities()
    payload = {
        "workflowId": "wf-task-1:session:codex_cli",
        "agentRunId": "wf-task-1",
        "sessionId": "sess:wf-task-1:codex_cli",
        "sessionEpoch": 1,
        "runtimeId": "codex_cli",
        "executionProfileRef": "codex-default",
    }

    await activities.agent_runtime_load_session_snapshot(payload)
    await activities.agent_runtime_load_session_snapshot(payload)

    assert len(created_adapters) == 1
    assert workflow_handle.query.await_count == 2

async def test_session_status_delegates_to_remote_session_controller() -> None:
    controller = AsyncMock()
    controller.session_status = AsyncMock(
        return_value=CodexManagedSessionHandle(
            sessionState={
                "sessionId": "sess-1",
                "sessionEpoch": 1,
                "containerId": "ctr-1",
                "threadId": "thread-1",
            },
            status="busy",
        )
    )
    activities = TemporalAgentRuntimeActivities(session_controller=controller)

    result = await activities.agent_runtime_session_status(
        {
            "sessionId": "sess-1",
            "sessionEpoch": 1,
            "containerId": "ctr-1",
            "threadId": "thread-1",
        }
    )

    assert isinstance(result, CodexManagedSessionHandle)
    assert result.status == "busy"

async def test_send_turn_accepts_base_model_payloads_and_preserves_concrete_type() -> None:
    class _SendTurnEnvelope(BaseModel):
        session_id: str
        session_epoch: int
        container_id: str
        thread_id: str
        instructions: str

    controller = AsyncMock()
    controller.send_turn = AsyncMock(
        return_value=CodexManagedSessionTurnResponse(
            sessionState={
                "sessionId": "sess-1",
                "sessionEpoch": 1,
                "containerId": "ctr-1",
                "threadId": "thread-1",
                "activeTurnId": "turn-1",
            },
            turnId="turn-1",
            status="running",
        )
    )
    activities = TemporalAgentRuntimeActivities(session_controller=controller)

    result = await activities.agent_runtime_send_turn(
        _SendTurnEnvelope(
            session_id="sess-1",
            session_epoch=1,
            container_id="ctr-1",
            thread_id="thread-1",
            instructions="Inspect the workspace",
        )
    )

    assert isinstance(result, CodexManagedSessionTurnResponse)
    validated_request = controller.send_turn.await_args.args[0]
    assert validated_request.__class__.__name__ == "SendCodexManagedSessionTurnRequest"
    assert validated_request.instructions == "Inspect the workspace"
    assert result.turn_id == "turn-1"

async def test_send_turn_delegates_to_remote_session_controller() -> None:
    controller = AsyncMock()
    controller.send_turn = AsyncMock(
        return_value=CodexManagedSessionTurnResponse(
            sessionState={
                "sessionId": "sess-1",
                "sessionEpoch": 1,
                "containerId": "ctr-1",
                "threadId": "thread-1",
                "activeTurnId": "turn-1",
            },
            turnId="turn-1",
            status="running",
        )
    )
    activities = TemporalAgentRuntimeActivities(session_controller=controller)

    result = await activities.agent_runtime_send_turn(
        {
            "sessionId": "sess-1",
            "sessionEpoch": 1,
            "containerId": "ctr-1",
            "threadId": "thread-1",
            "instructions": "Inspect the workspace",
            "environment": {
                "MOONMIND_ACTIVE_SKILLS_DIR": (
                    "/work/runtime/skills_active/snapshot-retry"
                ),
                "MOONMIND_STEP_EXECUTION_ID": "workflow:step:execution:2",
            },
        }
    )

    assert isinstance(result, CodexManagedSessionTurnResponse)
    assert result.turn_id == "turn-1"
    validated_request = controller.send_turn.await_args.args[0]
    assert validated_request.environment == {
        "MOONMIND_ACTIVE_SKILLS_DIR": "/work/runtime/skills_active/snapshot-retry",
        "MOONMIND_STEP_EXECUTION_ID": "workflow:step:execution:2",
    }

async def test_send_turn_transient_failure_preserves_diagnostic_metadata() -> None:
    failure_metadata = {
        "reason": "codex app-server turn/completed produced no assistant output",
        "failureClass": "transient",
        "failureCause": "app_server_protocol_empty_turn",
        "turnFailureEvidence": {"schemaVersion": "v1", "runtimeLogExcerpts": []},
    }
    controller = AsyncMock()
    controller.send_turn = AsyncMock(
        return_value=CodexManagedSessionTurnResponse(
            sessionState={
                "sessionId": "sess-1",
                "sessionEpoch": 1,
                "containerId": "ctr-1",
                "threadId": "thread-1",
                "activeTurnId": None,
            },
            turnId="turn-1",
            status="failed",
            metadata=failure_metadata,
        )
    )
    activities = TemporalAgentRuntimeActivities(session_controller=controller)

    with pytest.raises(
        activity_runtime_module.temporal_exceptions.ApplicationError
    ) as exc_info:
        await activities.agent_runtime_send_turn(
            {
                "sessionId": "sess-1",
                "sessionEpoch": 1,
                "containerId": "ctr-1",
                "threadId": "thread-1",
                "instructions": "Inspect the workspace",
            }
        )

    assert exc_info.value.type == "CodexTransientTurnError"
    assert exc_info.value.non_retryable is True
    assert exc_info.value.details == (failure_metadata,)


async def test_send_turn_other_transient_failure_remains_retryable() -> None:
    failure_metadata = {
        "reason": "codex app-server temporarily unavailable",
        "failureClass": "transient",
    }
    controller = AsyncMock()
    controller.send_turn = AsyncMock(
        return_value=CodexManagedSessionTurnResponse(
            sessionState={
                "sessionId": "sess-1",
                "sessionEpoch": 1,
                "containerId": "ctr-1",
                "threadId": "thread-1",
                "activeTurnId": None,
            },
            turnId="turn-1",
            status="failed",
            metadata=failure_metadata,
        )
    )
    activities = TemporalAgentRuntimeActivities(session_controller=controller)

    with pytest.raises(
        activity_runtime_module.temporal_exceptions.ApplicationError
    ) as exc_info:
        await activities.agent_runtime_send_turn(
            {
                "sessionId": "sess-1",
                "sessionEpoch": 1,
                "containerId": "ctr-1",
                "threadId": "thread-1",
                "instructions": "Inspect the workspace",
            }
        )

    assert exc_info.value.type == "CodexTransientTurnError"
    assert exc_info.value.non_retryable is False
    assert exc_info.value.details == (failure_metadata,)

async def test_send_turn_heartbeats_while_waiting_for_remote_session_controller(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from temporalio import activity as temporal_activity

    heartbeats: list[dict[str, Any]] = []

    async def _slow_send_turn(
        _request: Any,
    ) -> CodexManagedSessionTurnResponse:
        await asyncio.sleep(0.03)
        return CodexManagedSessionTurnResponse(
            sessionState={
                "sessionId": "sess-1",
                "sessionEpoch": 1,
                "containerId": "ctr-1",
                "threadId": "thread-1",
                "activeTurnId": "turn-1",
            },
            turnId="turn-1",
            status="completed",
        )

    monkeypatch.setattr(
        activity_runtime_module,
        "_SESSION_CONTROLLER_HEARTBEAT_INTERVAL_SECONDS",
        0.01,
    )
    monkeypatch.setattr(temporal_activity, "in_activity", lambda: True)
    monkeypatch.setattr(temporal_activity, "heartbeat", heartbeats.append)

    controller = AsyncMock()
    controller.send_turn = AsyncMock(side_effect=_slow_send_turn)
    activities = TemporalAgentRuntimeActivities(session_controller=controller)

    result = await activities.agent_runtime_send_turn(
        {
            "sessionId": "sess-1",
            "sessionEpoch": 1,
            "containerId": "ctr-1",
            "threadId": "thread-1",
            "instructions": "Inspect the workspace",
        }
    )

    assert isinstance(result, CodexManagedSessionTurnResponse)
    assert result.status == "completed"
    assert heartbeats
    assert all(
        heartbeat["activityType"] == "agent_runtime.send_turn"
        for heartbeat in heartbeats
    )

async def test_await_with_activity_heartbeats_accepts_existing_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from temporalio import activity as temporal_activity

    monkeypatch.setattr(temporal_activity, "in_activity", lambda: False)

    async def _complete() -> str:
        await asyncio.sleep(0)
        return "done"

    task = asyncio.create_task(_complete())
    result = await activity_runtime_module._await_with_activity_heartbeats(
        task,
        heartbeat_payload={"activityType": "agent_runtime.send_turn"},
    )

    assert result == "done"

async def test_steer_turn_delegates_to_remote_session_controller() -> None:
    controller = AsyncMock()
    controller.steer_turn = AsyncMock(
        return_value=CodexManagedSessionTurnResponse(
            sessionState={
                "sessionId": "sess-1",
                "sessionEpoch": 1,
                "containerId": "ctr-1",
                "threadId": "thread-1",
                "activeTurnId": "turn-1",
            },
            turnId="turn-1",
            status="running",
        )
    )
    activities = TemporalAgentRuntimeActivities(session_controller=controller)

    result = await activities.agent_runtime_steer_turn(
        {
            "sessionId": "sess-1",
            "sessionEpoch": 1,
            "containerId": "ctr-1",
            "threadId": "thread-1",
            "turnId": "turn-1",
            "instructions": "Focus on the failing test",
        }
    )

    assert isinstance(result, CodexManagedSessionTurnResponse)
    assert result.status == "running"

async def test_interrupt_turn_delegates_to_remote_session_controller() -> None:
    controller = AsyncMock()
    controller.interrupt_turn = AsyncMock(
        return_value=CodexManagedSessionTurnResponse(
            sessionState={
                "sessionId": "sess-1",
                "sessionEpoch": 1,
                "containerId": "ctr-1",
                "threadId": "thread-1",
            },
            turnId="turn-1",
            status="interrupted",
        )
    )
    activities = TemporalAgentRuntimeActivities(session_controller=controller)

    result = await activities.agent_runtime_interrupt_turn(
        {
            "sessionId": "sess-1",
            "sessionEpoch": 1,
            "containerId": "ctr-1",
            "threadId": "thread-1",
            "turnId": "turn-1",
        }
    )

    assert isinstance(result, CodexManagedSessionTurnResponse)
    assert result.status == "interrupted"

async def test_clear_session_delegates_to_remote_session_controller() -> None:
    controller = AsyncMock()
    controller.clear_session = AsyncMock(
        return_value=CodexManagedSessionHandle(
            sessionState={
                "sessionId": "sess-1",
                "sessionEpoch": 2,
                "containerId": "ctr-1",
                "threadId": "thread-2",
            },
            status="ready",
        )
    )
    activities = TemporalAgentRuntimeActivities(session_controller=controller)

    result = await activities.agent_runtime_clear_session(
        {
            "sessionId": "sess-1",
            "sessionEpoch": 1,
            "containerId": "ctr-1",
            "threadId": "thread-1",
            "newThreadId": "thread-2",
        }
    )

    assert isinstance(result, CodexManagedSessionHandle)
    assert result.session_state.session_epoch == 2

async def test_terminate_session_delegates_to_remote_session_controller() -> None:
    controller = AsyncMock()
    controller.terminate_session = AsyncMock(
        return_value=CodexManagedSessionHandle(
            sessionState={
                "sessionId": "sess-1",
                "sessionEpoch": 2,
                "containerId": "ctr-1",
                "threadId": "thread-2",
            },
            status="terminated",
        )
    )
    activities = TemporalAgentRuntimeActivities(session_controller=controller)

    result = await activities.agent_runtime_terminate_session(
        {
            "sessionId": "sess-1",
            "sessionEpoch": 2,
            "containerId": "ctr-1",
            "threadId": "thread-2",
        }
    )

    assert isinstance(result, CodexManagedSessionHandle)
    assert result.status == "terminated"

async def test_fetch_session_summary_delegates_to_remote_session_controller() -> None:
    controller = AsyncMock()
    controller.fetch_session_summary = AsyncMock(
        return_value=CodexManagedSessionSummary(
            sessionState={
                "sessionId": "sess-1",
                "sessionEpoch": 2,
                "containerId": "ctr-1",
                "threadId": "thread-2",
            },
            latestSummaryRef="art-summary",
            latestCheckpointRef="art-checkpoint",
            latestControlEventRef="art-control",
        )
    )
    activities = TemporalAgentRuntimeActivities(session_controller=controller)

    result = await activities.agent_runtime_fetch_session_summary(
        {
            "sessionId": "sess-1",
            "sessionEpoch": 2,
            "containerId": "ctr-1",
            "threadId": "thread-2",
        }
    )

    assert isinstance(result, CodexManagedSessionSummary)
    assert result.latest_summary_ref == "art-summary"
    assert result.latest_checkpoint_ref == "art-checkpoint"
    assert result.latest_control_event_ref == "art-control"

async def test_publish_session_artifacts_delegates_to_remote_session_controller() -> None:
    controller = AsyncMock()
    controller.publish_session_artifacts = AsyncMock(
        return_value=CodexManagedSessionArtifactsPublication(
            sessionState={
                "sessionId": "sess-1",
                "sessionEpoch": 2,
                "containerId": "ctr-1",
                "threadId": "thread-2",
            },
            publishedArtifactRefs=("art-summary", "art-checkpoint"),
            latestSummaryRef="art-summary",
            latestCheckpointRef="art-checkpoint",
            latestControlEventRef="art-control",
        )
    )
    activities = TemporalAgentRuntimeActivities(session_controller=controller)

    result = await activities.agent_runtime_publish_session_artifacts(
        {
            "sessionId": "sess-1",
            "sessionEpoch": 2,
            "containerId": "ctr-1",
            "threadId": "thread-2",
        }
    )

    assert isinstance(result, CodexManagedSessionArtifactsPublication)
    assert result.published_artifact_refs == ("art-summary", "art-checkpoint")
    assert result.latest_checkpoint_ref == "art-checkpoint"
    assert result.latest_control_event_ref == "art-control"

# ---------------------------------------------------------------------------
# T5: agent_runtime_fetch_result — typed AgentRunResult return
# ---------------------------------------------------------------------------

async def test_fetch_result_completed_returns_typed_model(tmp_path: Path) -> None:
    """T5.1 — completed run returns typed AgentRunResult with failure_class=None."""
    store = _make_store(tmp_path)
    _save_record(store, run_id="fr-1", status="completed")

    activities = TemporalAgentRuntimeActivities(run_store=store)
    result = await activities.agent_runtime_fetch_result({"run_id": "fr-1"})

    assert isinstance(result, AgentRunResult), f"Expected AgentRunResult, got {type(result)}"
    assert result.failure_class is None

async def test_fetch_result_adds_managed_session_assistant_text_to_metadata(
    tmp_path: Path,
) -> None:
    store = _make_store(tmp_path)
    store.save(
        ManagedRunRecord(
            runId="fr-session-report",
            agentId="codex_cli",
            runtimeId="codex_cli",
            status="completed",
            startedAt=datetime.now(tz=UTC),
            sessionId="sess:fr-session-report:codex_cli",
            sessionEpoch=1,
            containerId="ctr-session-report",
            threadId="thread-session-report",
        )
    )
    controller = AsyncMock()
    controller.fetch_session_summary = AsyncMock(
        return_value=CodexManagedSessionSummary(
            sessionState={
                "sessionId": "sess:fr-session-report:codex_cli",
                "sessionEpoch": 1,
                "containerId": "ctr-session-report",
                "threadId": "thread-session-report",
            },
            metadata={
                "lastAssistantText": (
                    "# Implementation check\n\n"
                    "The requested Docker Compose update behavior is partially implemented."
                )
            },
        )
    )

    activities = TemporalAgentRuntimeActivities(
        run_store=store,
        session_controller=controller,
    )
    result = await activities.agent_runtime_fetch_result(
        {"run_id": "fr-session-report", "agent_id": "codex_cli"}
    )

    assert result.summary == "Completed with status completed"
    assert result.metadata["lastAssistantText"].startswith("# Implementation check")
    assert result.metadata["operator_summary"].startswith("# Implementation check")
    controller.fetch_session_summary.assert_awaited_once()

async def test_fetch_result_does_not_use_stale_session_text_for_failed_runs(
    tmp_path: Path,
) -> None:
    store = _make_store(tmp_path)
    store.save(
        ManagedRunRecord(
            runId="fr-session-failed",
            agentId="codex_cli",
            runtimeId="codex_cli",
            status="failed",
            startedAt=datetime.now(tz=UTC),
            errorMessage="Process exited with code 1",
            failureClass="execution_error",
            sessionId="sess:fr-session-failed:codex_cli",
            sessionEpoch=1,
            containerId="ctr-session-failed",
            threadId="thread-session-failed",
        )
    )
    controller = AsyncMock()
    controller.fetch_session_summary = AsyncMock(
        return_value=CodexManagedSessionSummary(
            sessionState={
                "sessionId": "sess:fr-session-failed:codex_cli",
                "sessionEpoch": 1,
                "containerId": "ctr-session-failed",
                "threadId": "thread-session-failed",
            },
            metadata={
                "lastAssistantText": "# Stale success report\n\nThis should not be used."
            },
        )
    )

    activities = TemporalAgentRuntimeActivities(
        run_store=store,
        session_controller=controller,
    )
    result = await activities.agent_runtime_fetch_result(
        {"run_id": "fr-session-failed", "agent_id": "codex_cli"}
    )

    assert result.summary == "Process exited with code 1"
    assert result.failure_class == "execution_error"
    assert "lastAssistantText" not in result.metadata
    assert "operator_summary" not in result.metadata
    controller.fetch_session_summary.assert_not_awaited()

async def test_fetch_result_reads_managed_runtime_local_stdout_ref(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = "36064b4d-1adc-4a48-8ac6-9b6224d0394a"
    artifact_root = tmp_path / "managed-runtime-artifacts"
    stdout_path = artifact_root / run_id / "stdout.log"
    stdout_path.parent.mkdir(parents=True)
    stdout_path.write_text(
        "You've hit your session limit · resets 3:20am (UTC)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        activity_runtime_module,
        "_managed_runtime_artifact_root",
        lambda: artifact_root,
    )

    store = _make_store(tmp_path)
    store.save(
        ManagedRunRecord(
            runId=run_id,
            agentId="claude_code",
            runtimeId="claude_code",
            status="failed",
            startedAt=datetime.now(tz=UTC),
            errorMessage="Provider API rate limit exceeded",
            failureClass="integration_error",
            providerErrorCode="429",
            stdoutArtifactRef=f"{run_id}/stdout.log",
        )
    )

    activities = TemporalAgentRuntimeActivities(run_store=store)
    result = await activities.agent_runtime_fetch_result(
        {"run_id": run_id, "agent_id": "claude_code"}
    )

    assert result.summary == "Provider API rate limit exceeded"
    assert result.failure_class == "integration_error"
    assert result.provider_error_code == "429"
    assert result.metadata["operator_summary"] == (
        "You've hit your session limit · resets 3:20am (UTC)"
    )

async def test_fetch_result_failed_returns_typed_model(tmp_path: Path) -> None:
    """T5.2 — failed run returns typed AgentRunResult with correct failure_class."""
    store = _make_store(tmp_path)
    _save_record(
        store,
        run_id="fr-2",
        status="failed",
        failure_class="execution_error",
        error_message="Process exited with code 1",
    )

    activities = TemporalAgentRuntimeActivities(run_store=store)
    result = await activities.agent_runtime_fetch_result({"run_id": "fr-2"})

    assert isinstance(result, AgentRunResult)
    assert result.failure_class == "execution_error"

async def test_fetch_result_forwards_pr_resolver_expected_flag(tmp_path: Path) -> None:
    """T5.3 — pr-resolver expectation reaches the managed adapter."""
    from unittest.mock import patch

    store = _make_store(tmp_path)
    _save_record(store, run_id="fr-pr", status="completed")

    activities = TemporalAgentRuntimeActivities(run_store=store)
    with patch(
        "moonmind.workflows.temporal.activity_runtime.ManagedAgentAdapter",
        autospec=True,
    ) as mock_adapter_cls:
        adapter = mock_adapter_cls.return_value
        adapter.fetch_result = AsyncMock(
            return_value=AgentRunResult(
                summary="blocked",
                failure_class="user_error",
            )
        )

        result = await activities.agent_runtime_fetch_result(
            {
                "run_id": "fr-pr",
                "pr_resolver_expected": True,
                "pr_resolver_merge_gate_owned": True,
            }
        )

        adapter.fetch_result.assert_awaited_once_with(
            "fr-pr",
            pr_resolver_expected=True,
            pr_resolver_merge_gate_owned=True,
        )
        assert result.failure_class == "user_error"


async def test_fetch_result_preserves_legacy_pr_resolver_gate_ownership(
    tmp_path: Path,
) -> None:
    """Missing ownership field keeps old scheduled fetch_result payload semantics."""
    from unittest.mock import patch

    store = _make_store(tmp_path)
    _save_record(store, run_id="fr-pr-legacy", status="completed")

    activities = TemporalAgentRuntimeActivities(run_store=store)
    with patch(
        "moonmind.workflows.temporal.activity_runtime.ManagedAgentAdapter",
        autospec=True,
    ) as mock_adapter_cls:
        adapter = mock_adapter_cls.return_value
        adapter.fetch_result = AsyncMock(return_value=AgentRunResult(summary="ok"))

        result = await activities.agent_runtime_fetch_result(
            {"run_id": "fr-pr-legacy", "pr_resolver_expected": True}
        )

        adapter.fetch_result.assert_awaited_once_with(
            "fr-pr-legacy",
            pr_resolver_expected=True,
            pr_resolver_merge_gate_owned=True,
        )
        assert result.summary == "ok"


async def test_fetch_result_skips_infrastructure_push_for_jules_runtime(
    tmp_path: Path,
) -> None:
    from unittest.mock import patch

    store = _make_store(tmp_path)
    _save_record(store, run_id="fr-jules", status="completed", runtime_id="jules-api")

    activities = TemporalAgentRuntimeActivities(run_store=store)
    push_branch = AsyncMock(return_value={"push_status": "pushed"})
    activities._push_workspace_branch = push_branch

    with patch(
        "moonmind.workflows.temporal.activity_runtime.ManagedAgentAdapter",
        autospec=True,
    ) as mock_adapter_cls:
        adapter = mock_adapter_cls.return_value
        adapter.fetch_result = AsyncMock(
            return_value=AgentRunResult(summary="Jules completed")
        )

        result = await activities.agent_runtime_fetch_result(
            {"run_id": "fr-jules", "publishMode": "pr"}
        )

    assert result.failure_class is None
    push_branch.assert_not_awaited()


async def test_fetch_result_reverifies_and_clears_pr_not_found_when_merged(
    tmp_path: Path,
) -> None:
    """Regression: when pr-resolver reports pr_not_found but the PR is
    actually merged on GitHub, the activity must re-verify and clear
    the failure rather than surfacing execution_error.

    Guards against the managed-session auth gap where gh inside the
    codex container can't authenticate and misreports pr_not_found.
    """
    from unittest.mock import patch

    store = _make_store(tmp_path)
    _save_record(store, run_id="fr-reverify", status="completed")

    activities = TemporalAgentRuntimeActivities(run_store=store)
    with patch(
        "moonmind.workflows.temporal.activity_runtime.ManagedAgentAdapter",
        autospec=True,
    ) as mock_adapter_cls:
        adapter = mock_adapter_cls.return_value
        adapter.fetch_result = AsyncMock(
            return_value=AgentRunResult(
                summary=(
                    "pr-resolver reported status 'failed'; pr_not_found; "
                    "next_step=manual_review"
                ),
                failure_class="execution_error",
            )
        )

        with patch.object(
            activities,
            "_reverify_pr_merged_state",
            return_value={
                "number": 1543,
                "state": "MERGED",
                "url": "https://github.com/org/repo/pull/1543",
                "mergedAt": "2026-04-17T23:48:24Z",
            },
        ) as mock_reverify:
            result = await activities.agent_runtime_fetch_result(
                {
                    "run_id": "fr-reverify",
                    "pr_resolver_expected": True,
                    "target_branch": "main",
                    "head_branch": "mm-398-e3573b0c",
                }
            )

    mock_reverify.assert_called_once_with(
        run_id="fr-reverify",
        head_branch="mm-398-e3573b0c",
        base_branch="main",
        github_token=None,
    )
    assert result.failure_class is None, (
        "PR re-verified as merged: failure_class must be cleared"
    )
    assert "#1543" in (result.summary or "")
    assert result.metadata.get("prResolverReverified") is True
    assert result.metadata.get("mergeAutomationDisposition") == "already_merged"
    assert (
        result.metadata.get("pull_request_url")
        == "https://github.com/org/repo/pull/1543"
    )
    assert "pr_not_found" in (
        result.metadata.get("prResolverStaleSummary") or ""
    )

async def test_fetch_result_preserves_failure_when_reverify_returns_none(
    tmp_path: Path,
) -> None:
    """When re-verify does not confirm a merged PR (e.g. PR open,
    lookup failed, or no target_branch), the original failure must
    be preserved unchanged."""
    from unittest.mock import patch

    store = _make_store(tmp_path)
    _save_record(store, run_id="fr-preserve", status="completed")

    activities = TemporalAgentRuntimeActivities(run_store=store)
    original_result = AgentRunResult(
        summary=(
            "pr-resolver reported status 'failed'; pr_not_found; "
            "next_step=manual_review"
        ),
        failure_class="execution_error",
    )
    with patch(
        "moonmind.workflows.temporal.activity_runtime.ManagedAgentAdapter",
        autospec=True,
    ) as mock_adapter_cls:
        adapter = mock_adapter_cls.return_value
        adapter.fetch_result = AsyncMock(return_value=original_result)

        with patch.object(
            activities, "_reverify_pr_merged_state", return_value=None,
        ) as mock_reverify:
            result = await activities.agent_runtime_fetch_result(
                {
                    "run_id": "fr-preserve",
                    "pr_resolver_expected": True,
                    "target_branch": "main",
                    "head_branch": "mm-398-e3573b0c",
                }
            )

    mock_reverify.assert_called_once()
    assert result.failure_class == "execution_error"
    assert "pr_not_found" in (result.summary or "")
    assert result.metadata.get("prResolverReverified") is None

async def test_fetch_result_reverifies_blocked_resolver_by_pr_number_when_merged(
    tmp_path: Path,
) -> None:
    """Regression: resolver runs can omit head_branch in fetch_result input.

    When the stable run id carries the PR number and GitHub confirms that PR is
    merged, stale resolver states such as ci_running must not fail merge
    automation.
    """
    from unittest.mock import patch

    run_id = "resolver:pr:1727:head:623c3697e576:h:54dc00462b516f8d:1"
    store = _make_store(tmp_path)
    _save_record(store, run_id=run_id, status="completed")

    activities = TemporalAgentRuntimeActivities(run_store=store)
    with patch(
        "moonmind.workflows.temporal.activity_runtime.ManagedAgentAdapter",
        autospec=True,
    ) as mock_adapter_cls:
        adapter = mock_adapter_cls.return_value
        adapter.fetch_result = AsyncMock(
            return_value=AgentRunResult(
                summary=(
                    "pr-resolver reported status 'blocked'; ci_running; "
                    "next_step=retry_finalize_after_backoff"
                ),
                failure_class="user_error",
            )
        )

        with patch.object(
            activities,
            "_reverify_pr_merged_state",
            return_value={
                "number": 1727,
                "state": "MERGED",
                "url": "https://github.com/org/repo/pull/1727",
                "mergedAt": "2026-04-24T00:55:48Z",
            },
        ) as mock_reverify:
            result = await activities.agent_runtime_fetch_result(
                {
                    "run_id": run_id,
                    "pr_resolver_expected": True,
                }
            )

    mock_reverify.assert_called_once_with(
        run_id=run_id,
        head_branch=None,
        base_branch=None,
        github_token=None,
    )
    assert result.failure_class is None
    assert "#1727" in (result.summary or "")
    assert result.metadata.get("prResolverReverified") is True
    assert result.metadata.get("mergeAutomationDisposition") == "already_merged"
    assert "ci_running" in (
        result.metadata.get("prResolverStaleSummary") or ""
    )

async def test_fetch_result_skips_reverify_without_head_branch(
    tmp_path: Path,
) -> None:
    """No head_branch means re-verify has no source PR key — skip
    the call entirely rather than waste a gh subprocess invocation."""
    from unittest.mock import patch

    store = _make_store(tmp_path)
    _save_record(store, run_id="fr-skip", status="completed")

    activities = TemporalAgentRuntimeActivities(run_store=store)
    with patch(
        "moonmind.workflows.temporal.activity_runtime.ManagedAgentAdapter",
        autospec=True,
    ) as mock_adapter_cls:
        adapter = mock_adapter_cls.return_value
        adapter.fetch_result = AsyncMock(
            return_value=AgentRunResult(
                summary="pr-resolver reported status 'failed'; pr_not_found",
                failure_class="execution_error",
            )
        )

        with patch.object(
            activities, "_reverify_pr_merged_state",
        ) as mock_reverify:
            result = await activities.agent_runtime_fetch_result(
                {
                    "run_id": "fr-skip",
                    "pr_resolver_expected": True,
                    "target_branch": "main",
                }
            )

    mock_reverify.assert_not_called()
    assert result.failure_class == "execution_error"

async def test_reverify_pr_merged_state_queries_pr_number_from_run_id(
    tmp_path: Path,
) -> None:
    """The activity can recover when fetch_result omitted the head branch."""
    import subprocess
    from unittest.mock import patch

    run_id = "resolver:pr:1727:head:623c3697e576:h:54dc00462b516f8d:1"
    workspace = tmp_path / "repo"
    workspace.mkdir()
    store = _make_store(tmp_path)
    _save_record(
        store,
        run_id=run_id,
        status="completed",
        workspace_path=str(workspace),
    )
    activities = TemporalAgentRuntimeActivities(run_store=store)
    calls: list[list[str]] = []

    def _mock_run(args: list[str], **_kwargs: Any) -> subprocess.CompletedProcess:
        calls.append(args)
        return subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout=(
                '{"number":1727,"state":"MERGED",'
                '"url":"https://github.com/org/repo/pull/1727",'
                '"mergedAt":"2026-04-24T00:55:48Z",'
                '"baseRefName":"main","headRefName":"mm-491-d125a4e3"}'
            ),
            stderr="",
        )

    with (
        patch.object(
            activities, "_detect_repo_from_workspace", return_value="org/repo",
        ),
        patch("subprocess.run", side_effect=_mock_run),
    ):
        merged_pr = activities._reverify_pr_merged_state(
            run_id=run_id,
            head_branch=None,
            base_branch=None,
        )

    assert merged_pr is not None
    assert merged_pr["number"] == 1727
    assert calls[0][:4] == ["gh", "pr", "view", "1727"]

async def test_reverify_pr_merged_state_queries_head_and_base_branch(
    tmp_path: Path,
) -> None:
    """Regression: re-verify must query the PR source branch and constrain
    by the expected base branch so a merged PR is not missed or confused
    with another PR from the same source branch."""
    import subprocess
    from unittest.mock import patch

    workspace = tmp_path / "repo"
    workspace.mkdir()
    store = _make_store(tmp_path)
    _save_record(
        store,
        run_id="fr-direct-reverify",
        status="completed",
        workspace_path=str(workspace),
    )
    activities = TemporalAgentRuntimeActivities(run_store=store)
    calls: list[list[str]] = []

    def _mock_run(args: list[str], **_kwargs: Any) -> subprocess.CompletedProcess:
        calls.append(args)
        return subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout=(
                '[{"number":1543,"state":"MERGED",'
                '"url":"https://github.com/org/repo/pull/1543",'
                '"mergedAt":"2026-04-17T23:48:24Z",'
                '"baseRefName":"main","headRefName":"mm-398-e3573b0c"}]'
            ),
            stderr="",
        )

    with (
        patch.object(
            activities, "_detect_repo_from_workspace", return_value="org/repo",
        ),
        patch("subprocess.run", side_effect=_mock_run),
    ):
        merged_pr = activities._reverify_pr_merged_state(
            run_id="fr-direct-reverify",
            head_branch="mm-398-e3573b0c",
            base_branch="main",
        )

    assert merged_pr is not None
    assert merged_pr["number"] == 1543
    gh_args = calls[0]
    assert gh_args[gh_args.index("--head") + 1] == "mm-398-e3573b0c"
    assert gh_args[gh_args.index("--base") + 1] == "main"

async def test_reverify_pr_merged_state_rejects_malformed_json(
    tmp_path: Path,
) -> None:
    """Malformed gh stdout is a parse failure, not an unhandled activity error."""
    import subprocess
    from unittest.mock import patch

    workspace = tmp_path / "repo"
    workspace.mkdir()
    store = _make_store(tmp_path)
    _save_record(
        store,
        run_id="fr-bad-json",
        status="completed",
        workspace_path=str(workspace),
    )
    activities = TemporalAgentRuntimeActivities(run_store=store)

    with (
        patch.object(
            activities, "_detect_repo_from_workspace", return_value="org/repo",
        ),
        patch(
            "subprocess.run",
            return_value=subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout="not json",
                stderr="",
            ),
        ),
    ):
        merged_pr = activities._reverify_pr_merged_state(
            run_id="fr-bad-json",
            head_branch="feature/source",
            base_branch="main",
        )

    assert merged_pr is None

async def test_fetch_result_string_request_defaults_pr_resolver_expected_false(
    tmp_path: Path,
) -> None:
    """T5.4 — string request path must not call mapping-only accessors."""
    from unittest.mock import patch

    store = _make_store(tmp_path)
    _save_record(store, run_id="fr-string", status="completed")

    activities = TemporalAgentRuntimeActivities(run_store=store)
    with patch(
        "moonmind.workflows.temporal.activity_runtime.ManagedAgentAdapter",
        autospec=True,
    ) as mock_adapter_cls:
        adapter = mock_adapter_cls.return_value
        adapter.fetch_result = AsyncMock(return_value=AgentRunResult(summary="ok"))

        result = await activities.agent_runtime_fetch_result("fr-string")

        adapter.fetch_result.assert_awaited_once_with(
            "fr-string",
            pr_resolver_expected=False,
            pr_resolver_merge_gate_owned=False,
        )
        assert result.summary == "ok"

async def test_fetch_result_no_record_returns_empty_typed_model(tmp_path: Path) -> None:
    """T5.5 — no record in store returns empty AgentRunResult (not None, not dict)."""
    store = _make_store(tmp_path)

    activities = TemporalAgentRuntimeActivities(run_store=store)
    result = await activities.agent_runtime_fetch_result({"run_id": "no-such"})

    assert isinstance(result, AgentRunResult)

async def test_fetch_result_missing_run_id_raises_error(tmp_path: Path) -> None:
    """T5.6 — missing run_id raises TemporalActivityRuntimeError."""
    store = _make_store(tmp_path)
    activities = TemporalAgentRuntimeActivities(run_store=store)

    with pytest.raises(TemporalActivityRuntimeError):
        await activities.agent_runtime_fetch_result({"agent_id": "codex_cli"})

# ---------------------------------------------------------------------------
# Boundary & Serialization tests
# ---------------------------------------------------------------------------

from datetime import timedelta
from temporalio import workflow
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker, UnsandboxedWorkflowRunner

@workflow.defn(name="AgentRuntimeStatusBoundaryTest")
class AgentRuntimeStatusBoundaryTest:
    @workflow.run
    async def run(self, input_dict: dict) -> AgentRunStatus:
        return await workflow.execute_activity(
            "agent_runtime.status",
            input_dict,
            start_to_close_timeout=timedelta(minutes=1),
        )

@workflow.defn(name="AgentRuntimeFetchResultBoundaryTest")
class AgentRuntimeFetchResultBoundaryTest:
    @workflow.run
    async def run(self, input_dict: dict) -> AgentRunResult:
        return await workflow.execute_activity(
            "agent_runtime.fetch_result",
            input_dict,
            start_to_close_timeout=timedelta(minutes=1),
        )

@workflow.defn(name="AgentRuntimeBuildLaunchContextBoundaryTest")
class AgentRuntimeBuildLaunchContextBoundaryTest:
    @workflow.run
    async def run(self, input_dict: dict) -> dict[str, Any]:
        return await workflow.execute_activity(
            "agent_runtime.build_launch_context",
            input_dict,
            start_to_close_timeout=timedelta(minutes=1),
        )

@workflow.defn(name="AgentRuntimeCancelBoundaryTest")
class AgentRuntimeCancelBoundaryTest:
    @workflow.run
    async def run(self, input_dict: dict) -> AgentRunStatus:
        return await workflow.execute_activity(
            "agent_runtime.cancel",
            input_dict,
            start_to_close_timeout=timedelta(minutes=1),
        )

@workflow.defn(name="AgentRuntimePublishArtifactsBoundaryTest")
class AgentRuntimePublishArtifactsBoundaryTest:
    @workflow.run
    async def run(self, input_dict: dict) -> AgentRunResult | None:
        return await workflow.execute_activity(
            "agent_runtime.publish_artifacts",
            input_dict,
            start_to_close_timeout=timedelta(minutes=1),
        )

async def test_agent_runtime_status_temporal_boundary(tmp_path: Path) -> None:
    """Validate Temporal boundary serialization for typed Pydantic return matches contract."""
    from moonmind.workflows.temporal.activity_catalog import TemporalActivityCatalog

    store = _make_store(tmp_path)
    _save_record(store, run_id="boundary-1", status="completed")

    activities_impl = TemporalAgentRuntimeActivities(run_store=store)
    from temporalio import activity

    @activity.defn(name="agent_runtime.status")
    async def _agent_runtime_status_wrapper(request: dict) -> AgentRunStatus:
        return await activities_impl.agent_runtime_status(request)

    handlers = [_agent_runtime_status_wrapper]

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="boundary-test-queue",
            workflows=[AgentRuntimeStatusBoundaryTest],
            activities=handlers,
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            result = await env.client.execute_workflow(
                AgentRuntimeStatusBoundaryTest.run,
                {"run_id": "boundary-1", "agent_id": "codex_cli"},
                id="boundary-test-status",
                task_queue="boundary-test-queue",
            )

            assert isinstance(result, AgentRunStatus)
            assert result.status == "completed"

@pytest.mark.asyncio
async def test_agent_runtime_build_launch_context_temporal_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "ghs_test_token")
    monkeypatch.setenv("MOONMIND_ALLOW_LOCAL_ENCRYPTION_KEY_GENERATION", "1")
    activities_impl = TemporalAgentRuntimeActivities()
    from temporalio import activity

    @activity.defn(name="agent_runtime.build_launch_context")
    async def _agent_runtime_build_launch_context_wrapper(
        request: dict,
    ) -> dict[str, Any]:
        return await activities_impl.agent_runtime_build_launch_context(request)

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="boundary-test-queue-build-launch-context",
            workflows=[AgentRuntimeBuildLaunchContextBoundaryTest],
            activities=[_agent_runtime_build_launch_context_wrapper],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            result = await env.client.execute_workflow(
                AgentRuntimeBuildLaunchContextBoundaryTest.run,
                {
                    "profile": {
                        "profile_id": "proxy-prof",
                        "credential_source": "secret_ref",
                        "tags": ["proxy-first"],
                        "provider_id": "anthropic",
                        "secret_refs": {"anthropic_api_key": "db://123"},
                    },
                    "runtime_for_profile": "claude_code",
                    "workflow_id": "wf-boundary",
                    "default_credential_source": "secret_ref",
                },
                id="boundary-test-build-launch-context",
                task_queue="boundary-test-queue-build-launch-context",
            )

            assert result["profile_id"] == "proxy-prof"
            assert "MOONMIND_PROXY_TOKEN" in result["delta_env_overrides"]
            assert "GITHUB_TOKEN" in result["passthrough_env_keys"]

@pytest.mark.asyncio
async def test_agent_runtime_build_launch_context_prefers_proxy_for_supported_secret_ref(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from api_service.db.models import ProviderCredentialSource, RuntimeMaterializationMode

    monkeypatch.setenv("MOONMIND_ALLOW_LOCAL_ENCRYPTION_KEY_GENERATION", "1")
    activities_impl = TemporalAgentRuntimeActivities()

    result = await activities_impl.agent_runtime_build_launch_context(
        {
            "profile": {
                "profile_id": "claude-anthropic",
                "credential_source": ProviderCredentialSource.SECRET_REF,
                "runtime_materialization_mode": RuntimeMaterializationMode.ENV_BUNDLE,
                "provider_id": "anthropic",
                "secret_refs": {"provider_api_key": "db://anthropic"},
            },
            "runtime_for_profile": "claude_code",
            "workflow_id": "wf-proxy-default",
            "default_credential_source": "secret_ref",
        }
    )

    env_overrides = result["delta_env_overrides"]
    assert "MOONMIND_PROXY_TOKEN" in env_overrides
    assert "ANTHROPIC_BASE_URL" in env_overrides
    assert "proxy/anthropic" in env_overrides["ANTHROPIC_BASE_URL"]
    assert "db://anthropic" not in env_overrides.values()

@pytest.mark.asyncio
async def test_agent_runtime_build_launch_context_keeps_minimax_direct_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MOONMIND_ALLOW_LOCAL_ENCRYPTION_KEY_GENERATION", "1")
    activities_impl = TemporalAgentRuntimeActivities()

    result = await activities_impl.agent_runtime_build_launch_context(
        {
            "profile": {
                "profile_id": "claude-minimax",
                "credential_source": "secret_ref",
                "runtime_materialization_mode": "env_bundle",
                "provider_id": "minimax",
                "secret_refs": {"provider_api_key": "db://minimax"},
                "env_template": {
                    "ANTHROPIC_BASE_URL": "https://api.minimax.io/anthropic",
                    "ANTHROPIC_AUTH_TOKEN": {
                        "from_secret_ref": "provider_api_key",
                    },
                },
            },
            "runtime_for_profile": "claude_code",
            "workflow_id": "wf-minimax-direct",
            "default_credential_source": "secret_ref",
        }
    )

    env_overrides = result["delta_env_overrides"]
    assert "MOONMIND_PROXY_TOKEN" not in env_overrides
    assert "ANTHROPIC_BASE_URL" not in env_overrides
    assert "ANTHROPIC_AUTH_TOKEN" not in env_overrides

@pytest.mark.asyncio
async def test_agent_runtime_fetch_result_temporal_boundary(tmp_path: Path) -> None:
    from unittest.mock import patch

    store = _make_store(tmp_path)
    _save_record(store, run_id="boundary-1", status="completed")

    activities_impl = TemporalAgentRuntimeActivities(run_store=store)
    from temporalio import activity

    @activity.defn(name="agent_runtime.fetch_result")
    async def _agent_runtime_fetch_wrapper(request: dict) -> AgentRunResult:
        res = await activities_impl.agent_runtime_fetch_result(request)
        if hasattr(res, "model_copy"):
            return res.model_copy()
        return res

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="boundary-test-queue-fetch",
            workflows=[AgentRuntimeFetchResultBoundaryTest],
            activities=[_agent_runtime_fetch_wrapper],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            with patch("moonmind.workflows.temporal.activity_runtime.ManagedAgentAdapter", autospec=True) as MockAdapter:
                instance = MockAdapter.return_value
                instance.fetch_result = AsyncMock(return_value=AgentRunResult(summary="ok", failure_class=None))

                result = await env.client.execute_workflow(
                    AgentRuntimeFetchResultBoundaryTest.run,
                    {
                        "run_id": "boundary-1",
                        "agent_id": "claude",
                        "pr_resolver_expected": True,
                    },
                    id="boundary-test-fetch",
                    task_queue="boundary-test-queue-fetch",
                )

                assert isinstance(result, AgentRunResult)
                assert result.summary == "ok"
                instance.fetch_result.assert_awaited_once_with(
                    "boundary-1",
                    pr_resolver_expected=True,
                    pr_resolver_merge_gate_owned=True,
                )

@pytest.mark.asyncio
async def test_agent_runtime_cancel_temporal_boundary() -> None:
    from unittest.mock import MagicMock
    mock_supervisor = AsyncMock()
    mock_supervisor.cancel = AsyncMock()
    activities_impl = TemporalAgentRuntimeActivities(
        run_store=MagicMock(),
        run_supervisor=mock_supervisor,
    )
    from temporalio import activity

    @activity.defn(name="agent_runtime.cancel")
    async def _agent_runtime_cancel_wrapper(request: dict) -> AgentRunStatus:
        return await activities_impl.agent_runtime_cancel(request)

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="boundary-test-queue-cancel",
            workflows=[AgentRuntimeCancelBoundaryTest],
            activities=[_agent_runtime_cancel_wrapper],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            result = await env.client.execute_workflow(
                AgentRuntimeCancelBoundaryTest.run,
                {"run_id": "c-1", "agent_id": "c"},
                id="boundary-test-cancel",
                task_queue="boundary-test-queue-cancel",
            )

            assert isinstance(result, AgentRunStatus)
            assert result.status == "canceled"

@pytest.mark.asyncio
async def test_agent_runtime_publish_temporal_boundary() -> None:
    from unittest.mock import MagicMock
    activities_impl = TemporalAgentRuntimeActivities(run_store=MagicMock())
    from temporalio import activity

    @activity.defn(name="agent_runtime.publish_artifacts")
    async def _agent_runtime_publish_wrapper(request: dict) -> AgentRunResult | None:
        return await activities_impl.agent_runtime_publish_artifacts(None)

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="boundary-test-queue-pub",
            workflows=[AgentRuntimePublishArtifactsBoundaryTest],
            activities=[_agent_runtime_publish_wrapper],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            result = await env.client.execute_workflow(
                AgentRuntimePublishArtifactsBoundaryTest.run,
                {},
                id="boundary-test-pub",
                task_queue="boundary-test-queue-pub",
            )

            assert result is None

@workflow.defn(name="AgentRuntimeLaunchSessionBoundaryTest")
class AgentRuntimeLaunchSessionBoundaryTest:
    @workflow.run
    async def run(self, input_dict: dict) -> CodexManagedSessionHandle:
        return await workflow.execute_activity(
            "agent_runtime.launch_session",
            input_dict,
            start_to_close_timeout=timedelta(minutes=1),
        )

@workflow.defn(name="AgentRuntimeSendTurnBoundaryTest")
class AgentRuntimeSendTurnBoundaryTest:
    @workflow.run
    async def run(self, input_dict: dict) -> CodexManagedSessionTurnResponse:
        return await workflow.execute_activity(
            "agent_runtime.send_turn",
            input_dict,
            start_to_close_timeout=timedelta(minutes=1),
        )

@workflow.defn(name="AgentRuntimePrepareTurnInstructionsBoundaryTest")
class AgentRuntimePrepareTurnInstructionsBoundaryTest:
    @workflow.run
    async def run(self, input_dict: dict) -> str:
        return await workflow.execute_activity(
            "agent_runtime.prepare_turn_instructions",
            input_dict,
            start_to_close_timeout=timedelta(minutes=1),
        )

@pytest.mark.asyncio
async def test_agent_runtime_launch_session_temporal_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from temporalio import activity

    monkeypatch.setenv("GITHUB_TOKEN", "ghs-boundary-token")
    captured_request: dict[str, Any] = {}

    async def _capture_launch_session(
        request: LaunchCodexManagedSessionRequest,
    ) -> CodexManagedSessionHandle:
        captured_request["request"] = request
        return CodexManagedSessionHandle(
            sessionState={
                "sessionId": "sess-boundary",
                "sessionEpoch": 1,
                "containerId": "ctr-boundary",
                "threadId": "thread-1",
            },
            status="ready",
            imageRef="moonmind:latest",
        )

    controller = AsyncMock()
    controller.launch_session = AsyncMock(side_effect=_capture_launch_session)
    activities_impl = TemporalAgentRuntimeActivities(session_controller=controller)

    @activity.defn(name="agent_runtime.launch_session")
    async def _agent_runtime_launch_session_wrapper(
        request: dict,
    ) -> CodexManagedSessionHandle:
        return await activities_impl.agent_runtime_launch_session(request)

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="boundary-test-queue-launch-session",
            workflows=[AgentRuntimeLaunchSessionBoundaryTest],
            activities=[_agent_runtime_launch_session_wrapper],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            result = await env.client.execute_workflow(
                AgentRuntimeLaunchSessionBoundaryTest.run,
                {
                    "agentRunId": "task-1",
                    "sessionId": "sess-boundary",
                    "threadId": "thread-1",
                    "workspacePath": "/work/task/repo",
                    "sessionWorkspacePath": "/work/task/session",
                    "artifactSpoolPath": "/work/task/artifacts",
                    "codexHomePath": "/work/task/codex-home",
                    "imageRef": "moonmind:latest",
                    "workspaceSpec": {"repository": "MoonLadderStudios/private-repo"},
                },
                id="boundary-test-launch-session",
                task_queue="boundary-test-queue-launch-session",
            )

            assert isinstance(result, CodexManagedSessionHandle)
            assert result.session_state.container_id == "ctr-boundary"
            launch_request = captured_request["request"]
            assert "GITHUB_TOKEN" not in launch_request.environment
            assert "GIT_TERMINAL_PROMPT" not in launch_request.environment
            assert launch_request.github_credential is not None
            assert launch_request.github_credential.source == "environment"
            assert launch_request.github_credential.env_var == "GITHUB_TOKEN"

@pytest.mark.asyncio
async def test_agent_runtime_send_turn_temporal_boundary() -> None:
    from temporalio import activity

    controller = AsyncMock()
    controller.send_turn = AsyncMock(
        return_value=CodexManagedSessionTurnResponse(
            sessionState={
                "sessionId": "sess-boundary",
                "sessionEpoch": 1,
                "containerId": "ctr-boundary",
                "threadId": "thread-1",
                "activeTurnId": "turn-1",
            },
            turnId="turn-1",
            status="running",
        )
    )
    activities_impl = TemporalAgentRuntimeActivities(session_controller=controller)

    @activity.defn(name="agent_runtime.send_turn")
    async def _agent_runtime_send_turn_wrapper(
        request: dict,
    ) -> CodexManagedSessionTurnResponse:
        return await activities_impl.agent_runtime_send_turn(request)

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="boundary-test-queue-send-turn",
            workflows=[AgentRuntimeSendTurnBoundaryTest],
            activities=[_agent_runtime_send_turn_wrapper],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            result = await env.client.execute_workflow(
                AgentRuntimeSendTurnBoundaryTest.run,
                {
                    "sessionId": "sess-boundary",
                    "sessionEpoch": 1,
                    "containerId": "ctr-boundary",
                    "threadId": "thread-1",
                    "instructions": "Inspect the repo state",
                    "environment": {
                        "MOONMIND_ACTIVE_SKILLS_DIR": (
                            "/work/runtime/skills_active/snapshot-retry"
                        ),
                        "MOONMIND_STEP_EXECUTION_ID": (
                            "workflow:step:execution:2"
                        ),
                    },
                },
                id="boundary-test-send-turn",
                task_queue="boundary-test-queue-send-turn",
            )

            assert isinstance(result, CodexManagedSessionTurnResponse)
            assert result.turn_id == "turn-1"
            validated_request = controller.send_turn.await_args.args[0]
            assert validated_request.environment == {
                "MOONMIND_ACTIVE_SKILLS_DIR": (
                    "/work/runtime/skills_active/snapshot-retry"
                ),
                "MOONMIND_STEP_EXECUTION_ID": "workflow:step:execution:2",
            }

@pytest.mark.asyncio
async def test_agent_runtime_prepare_turn_instructions_injects_context(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class _FakeContextInjectionService:
        async def inject_context(
            self,
            *,
            request: Any,
            workspace_path: Path,
        ) -> None:
            assert workspace_path == tmp_path
            request.instruction_ref = "Injected context instruction"

    monkeypatch.setattr(
        "moonmind.rag.context_injection.ContextInjectionService",
        _FakeContextInjectionService,
    )
    class _FakeSessionController:
        def __init__(self) -> None:
            self.repaired_workspace_paths: list[str] = []

        async def ensure_repo_artifacts_writable_by_runtime_user(
            self,
            workspace_path: str,
        ) -> None:
            self.repaired_workspace_paths.append(workspace_path)

    session_controller = _FakeSessionController()
    activities = TemporalAgentRuntimeActivities(
        session_controller=session_controller,
    )

    result = await activities.agent_runtime_prepare_turn_instructions(
        {
            "request": {
                "agentKind": "managed",
                "agentId": "codex",
                "correlationId": "corr-1",
                "idempotencyKey": "idem-1",
                "instructionRef": "artifact:instructions",
                "parameters": {"publishMode": "none"},
            },
            "workspacePath": str(tmp_path),
        }
    )

    assert result.startswith("Injected context instruction")
    assert "Managed Codex CLI note:" in result
    assert session_controller.repaired_workspace_paths == [str(tmp_path)]

@pytest.mark.asyncio
@pytest.mark.parametrize(
    "skill_parameters",
    [
        {
            "selectedSkill": "jira-issue-creator",
        },
        {
            "metadata": {
                "moonmind": {
                    "selectedSkill": "jira-issue-creator",
                },
            },
        },
    ],
)
async def test_agent_runtime_prepare_turn_instructions_adds_jira_tool_hint(
    skill_parameters: dict[str, Any],
) -> None:
    activities = TemporalAgentRuntimeActivities()

    result = await activities.agent_runtime_prepare_turn_instructions(
        {
            "request": {
                "agentKind": "managed",
                "agentId": "codex",
                "correlationId": "corr-1",
                "idempotencyKey": "idem-1",
                "parameters": {
                    "instructions": "Create Jira stories from the breakdown.",
                    "publishMode": "none",
                    "storyBreakdownPath": "artifacts/story-breakdowns/demo/stories.json",
                    **skill_parameters,
                },
            },
        }
    )

    assert "MoonMind trusted Jira tools:" in result
    assert "artifacts/story-breakdowns/demo/stories.json" in result
    assert "`$MOONMIND_URL`" in result
    assert "POST $MOONMIND_URL/mcp/tools/call" in result
    assert "jira.create_issue" in result
    assert "Managed Codex CLI note:" in result


@pytest.mark.asyncio
async def test_agent_runtime_prepare_turn_instructions_adds_batch_jira_search_hint() -> None:
    activities = TemporalAgentRuntimeActivities()

    result = await activities.agent_runtime_prepare_turn_instructions(
        {
            "request": {
                "agentKind": "managed",
                "agentId": "codex",
                "correlationId": "corr-batch",
                "idempotencyKey": "idem-batch",
                "parameters": {
                    "instructions": (
                        "Resolve project THOR issues in Selected for Development "
                        "and queue child workflows."
                    ),
                    "publishMode": "none",
                    "metadata": {
                        "moonmind": {
                            "selectedSkill": "batch-workflows",
                        },
                    },
                },
            },
        }
    )

    assert "MoonMind trusted Jira tools:" in result
    assert "GET $MOONMIND_URL/mcp/tools" in result
    assert "POST $MOONMIND_URL/mcp/tools/call" in result
    assert "jira.search_issues" in result
    example_line = next(
        line
        for line in result.splitlines()
        if line.startswith("- Example batch search call: ")
    )
    example_payload = json.loads(example_line.split("`", 2)[1])
    assert example_payload["arguments"]["jql"] == (
        'project = <PROJECT_KEY> AND status = "<STATUS>"'
    )
    assert "do not request an external Jira/Atlassian connector" in result
    assert "do not wait for connector discovery" in result
    assert "Managed Codex CLI note:" in result


async def test_agent_runtime_prepare_turn_instructions_adds_pr_resolver_blocker_hint() -> None:
    activities = TemporalAgentRuntimeActivities()

    result = await activities.agent_runtime_prepare_turn_instructions(
        {
            "skipSkillMaterialization": True,
            "request": {
                "agentKind": "managed",
                "agentId": "codex",
                "correlationId": "corr-1",
                "idempotencyKey": "idem-1",
                "parameters": {
                    "instructions": "Resolve the pull request.",
                    "publishMode": "none",
                    "selectedSkill": "pr-resolver",
                    "mergeGate": {
                        "pullRequestUrl": "https://github.com/org/repo/pull/1791",
                        "pr": {
                            "mergeable": "CONFLICTING",
                            "mergeStateStatus": "DIRTY",
                        },
                        "ci": {
                            "isRunning": False,
                            "hasFailures": False,
                            "signalQuality": "missing",
                            "statusCheckRollupCount": 0,
                        },
                        "commentsSummary": {
                            "fetchSucceeded": True,
                            "hasActionableComments": False,
                        },
                    },
                },
            },
        }
    )

    assert "MoonMind PR resolver initial state:" in result
    assert "https://github.com/org/repo/pull/1791" in result
    assert "mergeable=CONFLICTING" in result
    assert "mergeStateStatus=DIRTY" in result
    assert "Initial blocker hint: merge_conflicts, ci_unavailable" in result
    assert "Managed Codex CLI note:" in result


@pytest.mark.asyncio
async def test_pr_resolver_resolve_selector_activity_preserves_canonical_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from moonmind.workflows.adapters.github_service import PullRequestSelectorResult

    async def resolve_selector(self, *, repo: str, selector: str, github_token=None):
        assert repo == "MoonLadderStudios/MoonMind"
        assert selector == "feature/mm-1200"
        return PullRequestSelectorResult(
            resolved=True,
            prNumber=3192,
            prUrl="https://github.com/MoonLadderStudios/MoonMind/pull/3192",
            selectorType="branch",
            reasonCode="resolved",
            summary="resolved",
        )

    monkeypatch.setattr(
        "moonmind.workflows.adapters.github_service.GitHubService.resolve_pull_request_selector",
        resolve_selector,
    )
    activities = TemporalIntegrationActivities()

    result = await activities.pr_resolver_resolve_selector(
        {
            "repository": "MoonLadderStudios/MoonMind",
            "selector": "feature/mm-1200",
        }
    )

    assert result["resolved"] is True
    assert result["prNumber"] == 3192
    assert result["prUrl"].endswith("/pull/3192")


@pytest.mark.asyncio
async def test_agent_runtime_prepare_turn_instructions_adds_jira_pr_verify_tool_hint() -> None:
    activities = TemporalAgentRuntimeActivities()

    result = await activities.agent_runtime_prepare_turn_instructions(
        {
            "request": {
                "agentKind": "managed",
                "agentId": "codex",
                "correlationId": "corr-1",
                "idempotencyKey": "idem-1",
                "parameters": {
                    "instructions": "Verify KANDY-2558 against PR #635.",
                    "publishMode": "none",
                    "metadata": {
                        "moonmind": {
                            "selectedSkill": "jira-pr-verify",
                        },
                    },
                },
            },
        }
    )

    assert "MoonMind trusted Jira tools:" in result
    assert "`$MOONMIND_URL`" in result
    assert "POST $MOONMIND_URL/mcp/tools/call" in result
    assert "jira.get_issue" in result
    assert "Verify KANDY-2558 against PR #635." in result
    assert "Managed Codex CLI note:" in result

@pytest.mark.asyncio
async def test_agent_runtime_prepare_turn_instructions_adds_jira_verify_tool_hint() -> None:
    activities = TemporalAgentRuntimeActivities()

    result = await activities.agent_runtime_prepare_turn_instructions(
        {
            "request": {
                "agentKind": "managed",
                "agentId": "codex",
                "correlationId": "corr-1",
                "idempotencyKey": "idem-1",
                "parameters": {
                    "instructions": "Verify KANDY-3607 against this branch.",
                    "publishMode": "none",
                    "metadata": {
                        "moonmind": {
                            "selectedSkill": "jira-verify",
                        },
                    },
                },
            },
        }
    )

    assert "MoonMind trusted Jira tools:" in result
    assert "`$MOONMIND_URL`" in result
    assert "POST $MOONMIND_URL/mcp/tools/call" in result
    assert "jira.get_issue" in result
    assert "jira.add_comment" in result
    assert "PASS, PARTIAL, FAIL, or BLOCKED" in result
    assert "Verify KANDY-3607 against this branch." in result
    assert "Managed Codex CLI note:" in result

@pytest.mark.asyncio
async def test_agent_runtime_prepare_turn_instructions_adds_jira_issue_updater_tool_hint() -> None:
    activities = TemporalAgentRuntimeActivities()

    result = await activities.agent_runtime_prepare_turn_instructions(
        {
            "request": {
                "agentKind": "managed",
                "agentId": "codex",
                "correlationId": "corr-1",
                "idempotencyKey": "idem-1",
                "parameters": {
                    "instructions": "Transition THOR-352 to In Progress.",
                    "publishMode": "none",
                    "metadata": {
                        "moonmind": {
                            "selectedSkill": "jira-issue-updater",
                        },
                    },
                },
            },
        }
    )

    assert "MoonMind trusted Jira tools:" in result
    assert "`$MOONMIND_URL`" in result
    assert "POST $MOONMIND_URL/mcp/tools/call" in result
    assert "jira.get_issue" in result
    assert "jira.get_transitions" in result
    assert "jira.transition_issue" in result
    assert "Transition THOR-352 to In Progress." in result
    assert "Managed Codex CLI note:" in result


@pytest.mark.asyncio
async def test_agent_runtime_prepare_turn_instructions_adds_step_boundary() -> None:
    activities = TemporalAgentRuntimeActivities()

    result = await activities.agent_runtime_prepare_turn_instructions(
        {
            "request": {
                "agentKind": "managed",
                "agentId": "codex",
                "correlationId": "corr-step-boundary",
                "idempotencyKey": "idem-step-boundary",
                "parameters": {
                    "instructions": "Classify request and resume point.",
                    "publishMode": "none",
                    "metadata": {
                        "moonmind": {
                            "stepLedger": {
                                "logicalStepId": "tpl:jira-orchestrate:1.0.0:04:demo",
                                "attempt": 1,
                                "scope": "step",
                            },
                        },
                    },
                },
            },
        }
    )

    assert "MoonMind managed step boundary:" in result
    assert "Execute only this current plan step." in result
    assert (
        "Repository `AGENTS.md` autonomy instructions apply only within this "
        "current step boundary."
    ) in result
    assert "Always finish with a brief assistant message" in result
    assert "complete the current task instruction" in result
    assert "verification requested by that instruction" in result
    assert "make the requested commit when that instruction asks for one" in result
    assert result.index("Classify request and resume point.") < result.index(
        "MoonMind managed step boundary:"
    )
    assert result.index("MoonMind managed step boundary:") < result.index(
        "MoonMind retrieval capability:"
    )
    assert result.index("MoonMind retrieval capability:") < result.index(
        "Managed Codex CLI note:"
    )


@pytest.mark.asyncio
async def test_agent_runtime_prepare_turn_instructions_materializes_selected_skill_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    managed_root = tmp_path / "agent_jobs"
    monkeypatch.setenv("MOONMIND_AGENT_RUNTIME_STORE", str(managed_root))

    job_root = managed_root / "job-1"
    workspace = job_root / "repo"
    workspace.mkdir(parents=True)
    skill_body = b"---\nname: pr-resolver\ndescription: active\n---\nactive resolver body\n"
    resolved_skillset = ResolvedSkillSet(
        snapshot_id="skillset-pr-resolver",
        resolved_at=datetime.now(UTC),
        skills=[
            ResolvedSkillEntry(
                skill_name="pr-resolver",
                content_ref="art-pr-resolver-body",
                content_digest="sha256:" + hashlib.sha256(skill_body).hexdigest(),
                provenance=AgentSkillProvenance(
                    source_kind=AgentSkillSourceKind.BUILT_IN
                ),
            )
        ],
    )
    artifact_service = _StaticArtifactService(
        {
            "art-pr-resolver-snapshot": resolved_skillset.model_dump_json().encode(
                "utf-8"
            ),
            "art-pr-resolver-body": skill_body,
        }
    )

    activities = TemporalAgentRuntimeActivities(artifact_service=artifact_service)
    result = await activities.agent_runtime_prepare_turn_instructions(
        {
            "request": {
                "agentKind": "managed",
                "agentId": "codex",
                "correlationId": "corr-1",
                "idempotencyKey": "idem-1",
                "resolvedSkillsetRef": "art-pr-resolver-snapshot",
                "parameters": {
                    "title": "Verify PR resolver outcome",
                    "instructions": "Resolve the PR.",
                    "publishMode": "none",
                    "metadata": {
                        "moonmind": {
                            "selectedSkill": "pr-resolver",
                        },
                    },
                },
            },
            "workspacePath": str(workspace),
        }
    )

    assert result.startswith("Active MoonMind skill snapshot:")
    assert "Full active MoonMind skill content is available at:" in result
    assert ".agents/skills/pr-resolver/SKILL.md" in result
    backing_skill = (
        job_root
        / "runtime"
        / "skills_active"
        / "skillset-pr-resolver"
        / "pr-resolver"
        / "SKILL.md"
    )
    assert backing_skill.read_text(encoding="utf-8").endswith("active resolver body\n")
    visible_skills = workspace / ".agents" / "skills"
    assert visible_skills.is_symlink()
    assert (visible_skills / "pr-resolver" / "SKILL.md").read_text(
        encoding="utf-8"
    ).endswith("active resolver body\n")
    assert not (workspace / "skills_active").exists()


@pytest.mark.asyncio
async def test_agent_runtime_prepare_turn_instructions_materializes_verifier_skill_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    managed_root = tmp_path / "agent_jobs"
    monkeypatch.setenv("MOONMIND_AGENT_RUNTIME_STORE", str(managed_root))
    job_root = managed_root / "job-verify"
    workspace = job_root / "repo"
    workspace.mkdir(parents=True)
    stale_root = job_root / "runtime" / "skills_active" / "skillset-stale"
    stale_root.mkdir(parents=True)
    (stale_root / "_manifest.json").write_text(
        '{"snapshot_id": "skillset-stale"}\n',
        encoding="utf-8",
    )
    agents_projection = workspace / ".agents" / "skills"
    gemini_projection = workspace / ".gemini" / "skills"
    agents_projection.parent.mkdir(parents=True)
    gemini_projection.parent.mkdir(parents=True)
    agents_projection.symlink_to(stale_root)
    gemini_projection.symlink_to(stale_root)
    skill_body = (
        b"---\n"
        b"name: moonspec-verify\n"
        b"description: Verify MoonSpec implementation evidence\n"
        b"---\n"
        b"Verifier instructions.\n"
    )
    resolved_skillset = ResolvedSkillSet(
        snapshot_id="skillset-verify",
        resolved_at=datetime.now(UTC),
        skills=[
            ResolvedSkillEntry(
                skill_name="moonspec-verify",
                content_ref="art-moonspec-verify-body",
                content_digest="sha256:" + hashlib.sha256(skill_body).hexdigest(),
                provenance=AgentSkillProvenance(
                    source_kind=AgentSkillSourceKind.BUILT_IN
                ),
            )
        ],
    )
    activities = TemporalAgentRuntimeActivities(
        artifact_service=_StaticArtifactService(
            {
                "art-moonspec-verify-snapshot": resolved_skillset.model_dump_json().encode(
                    "utf-8"
                ),
                "art-moonspec-verify-body": skill_body,
            }
        )
    )

    result = await activities.agent_runtime_prepare_turn_instructions(
        {
            "request": {
                "agentKind": "managed",
                "agentId": "codex",
                "correlationId": "corr-verify",
                "idempotencyKey": "idem-verify",
                "resolvedSkillsetRef": "art-moonspec-verify-snapshot",
                "parameters": {
                    "instructions": "Run final verification.",
                    "metadata": {
                        "moonmind": {
                            "selectedSkill": "moonspec-verify",
                        },
                    },
                },
            },
            "workspacePath": str(workspace),
        }
    )

    assert result.startswith("Active MoonMind skill snapshot:")
    assert "Run final verification." in result
    backing_path = job_root / "runtime" / "skills_active" / "skillset-verify"
    skill_doc = backing_path / "moonspec-verify" / "SKILL.md"
    assert str(skill_doc) in result
    assert ".agents/skills/moonspec-verify/SKILL.md" not in result
    assert not agents_projection.exists()
    assert not agents_projection.is_symlink()
    assert not gemini_projection.exists()
    assert not gemini_projection.is_symlink()
    assert skill_doc.read_bytes() == skill_body
    manifest = json.loads((backing_path / "_manifest.json").read_text(encoding="utf-8"))
    assert manifest["snapshot_id"] == "skillset-verify"
    assert manifest["visible_path"] == str(backing_path)
    assert manifest["skills"][0]["name"] == "moonspec-verify"


@pytest.mark.asyncio
async def test_agent_runtime_prepare_turn_instructions_can_skip_skill_materialization(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "agent_jobs" / "job-1" / "repo"
    workspace.mkdir(parents=True)

    class _FailingMaterializer:
        def __init__(self, **_kwargs: Any) -> None:
            raise AssertionError("skill materializer should not be constructed")

    monkeypatch.setattr(
        activity_runtime_module,
        "AgentSkillMaterializer",
        _FailingMaterializer,
    )

    activities = TemporalAgentRuntimeActivities()
    result = await activities.agent_runtime_prepare_turn_instructions(
        {
            "request": {
                "agentKind": "managed",
                "agentId": "codex",
                "correlationId": "corr-1",
                "idempotencyKey": "idem-1",
                "parameters": {
                    "instructions": "Resolve the PR.",
                    "publishMode": "none",
                    "metadata": {
                        "moonmind": {
                            "selectedSkill": "pr-resolver",
                        },
                    },
                },
            },
            "workspacePath": str(workspace),
            "includePreparedRequestMetadata": True,
            "skipSkillMaterialization": True,
        }
    )

    assert isinstance(result, dict)
    assert result["instructions"].startswith("Resolve the PR.")
    assert "Active MoonMind skill snapshot:" not in result["instructions"]
    assert result["durableRetrievalMetadata"] == {}
    assert not (workspace / ".agents").exists()


@pytest.mark.asyncio
async def test_agent_runtime_prepare_turn_instructions_warns_skills_on_demand_disabled(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        activity_runtime_module.settings.workflow,
        "skills_on_demand_enabled",
        False,
    )
    workspace = tmp_path / "agent_jobs" / "job-1" / "repo"
    workspace.mkdir(parents=True)

    activities = TemporalAgentRuntimeActivities()
    result = await activities.agent_runtime_prepare_turn_instructions(
        {
            "request": {
                "agentKind": "managed",
                "agentId": "codex",
                "correlationId": "corr-1",
                "idempotencyKey": "idem-1",
                "parameters": {
                    "instructions": "Use the active skill only.",
                    "metadata": {
                        "moonmind": {
                            "selectedSkill": "moonspec-implement",
                        },
                    },
                },
            },
            "workspacePath": str(workspace),
            "includePreparedRequestMetadata": True,
            "skipSkillMaterialization": True,
        }
    )

    instructions = result["instructions"]
    assert "Skills On Demand is disabled for this run." in instructions
    assert "Use only the active Skills already available" in instructions


@pytest.mark.asyncio
async def test_agent_runtime_prepare_turn_instructions_exposes_on_demand_commands_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        activity_runtime_module.settings.workflow,
        "skills_on_demand_enabled",
        True,
    )
    workspace = tmp_path / "agent_jobs" / "job-1" / "repo"
    workspace.mkdir(parents=True)

    activities = TemporalAgentRuntimeActivities()
    result = await activities.agent_runtime_prepare_turn_instructions(
        {
            "request": {
                "agentKind": "managed",
                "agentId": "codex",
                "correlationId": "corr-1",
                "idempotencyKey": "idem-1",
                "parameters": {
                    "instructions": "Use the active skill.",
                    "metadata": {
                        "moonmind": {
                            "selectedSkill": "moonspec-implement",
                        },
                    },
                },
            },
            "workspacePath": str(workspace),
            "includePreparedRequestMetadata": True,
            "skipSkillMaterialization": True,
        }
    )

    instructions = result["instructions"]
    assert "Skills On Demand is enabled." in instructions
    assert "moonmind.skills.query" in instructions
    assert "moonmind.skills.request" in instructions


@pytest.mark.asyncio
async def test_agent_runtime_prepare_turn_instructions_fails_before_launch_when_projection_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    managed_root = tmp_path / "agent_jobs"
    monkeypatch.setenv("MOONMIND_AGENT_RUNTIME_STORE", str(managed_root))
    workspace = managed_root / "job-1" / "repo"
    workspace.mkdir(parents=True)
    resolved_skillset = ResolvedSkillSet(
        snapshot_id="skillset-pr-resolver",
        resolved_at=datetime.now(UTC),
        skills=[
            ResolvedSkillEntry(
                skill_name="pr-resolver",
                content_ref="art-pr-resolver-body",
                content_digest="sha256:test",
                provenance=AgentSkillProvenance(
                    source_kind=AgentSkillSourceKind.BUILT_IN
                ),
            )
        ],
    )
    artifact_service = _StaticArtifactService(
        {
            "art-pr-resolver-snapshot": resolved_skillset.model_dump_json().encode(
                "utf-8"
            ),
            "art-pr-resolver-body": b"active resolver body\n",
        }
    )

    class _NoopMaterializer:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        async def materialize(self, **_kwargs: Any) -> None:
            return None

    monkeypatch.setattr(
        activity_runtime_module,
        "AgentSkillMaterializer",
        _NoopMaterializer,
    )
    activities = TemporalAgentRuntimeActivities(artifact_service=artifact_service)

    with pytest.raises(
        TemporalActivityRuntimeError,
        match=r"active skills visiblePath metadata is missing",
    ):
        await activities.agent_runtime_prepare_turn_instructions(
            {
                "request": {
                    "agentKind": "managed",
                    "agentId": "codex",
                    "correlationId": "corr-1",
                    "idempotencyKey": "idem-1",
                    "resolvedSkillsetRef": "art-pr-resolver-snapshot",
                    "parameters": {
                        "instructions": "Resolve the PR.",
                        "metadata": {
                            "moonmind": {
                                "selectedSkill": "pr-resolver",
                            },
                        },
                    },
                },
                "workspacePath": str(workspace),
            }
        )


@pytest.mark.asyncio
async def test_agent_runtime_selected_skill_projection_rejects_non_mapping_manifest(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "repo"
    skill_dir = workspace / ".agents" / "skills" / "pr-resolver"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("resolver body\n", encoding="utf-8")
    (workspace / ".agents" / "skills" / "_manifest.json").write_text(
        "[]\n",
        encoding="utf-8",
    )
    resolved_skillset = ResolvedSkillSet(
        snapshot_id="skillset-pr-resolver",
        resolved_at=datetime.now(UTC),
        skills=[
            ResolvedSkillEntry(
                skill_name="pr-resolver",
                content_ref="art-pr-resolver-body",
                content_digest="sha256:test",
                provenance=AgentSkillProvenance(
                    source_kind=AgentSkillSourceKind.BUILT_IN
                ),
            )
        ],
    )

    with pytest.raises(
        TemporalActivityRuntimeError,
        match="active skill manifest is unreadable",
    ):
        TemporalAgentRuntimeActivities._validate_selected_skill_projection(
            visible_path=workspace / ".agents" / "skills",
            selected_skill="pr-resolver",
            resolved_skillset=resolved_skillset,
        )


@pytest.mark.asyncio
async def test_agent_runtime_selected_skill_projection_enforces_repo_source_policy(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "repo"
    skill_dir = workspace / ".agents" / "skills" / "repo-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("repo skill body\n", encoding="utf-8")
    (workspace / ".agents" / "skills" / "_manifest.json").write_text(
        json.dumps(
            {
                "snapshot_id": "skillset-repo",
                "skills": [
                    {
                        "name": "repo-skill",
                        "source_kind": "repo",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    resolved_skillset = ResolvedSkillSet(
        snapshot_id="skillset-repo",
        resolved_at=datetime.now(UTC),
        skills=[
            ResolvedSkillEntry(
                skill_name="repo-skill",
                content_ref="art-repo-skill-body",
                content_digest="sha256:test",
                provenance=AgentSkillProvenance(
                    source_kind=AgentSkillSourceKind.REPO,
                    source_path=".agents/skills/repo-skill",
                ),
            )
        ],
        policy_summary={
            "repo_skills_allowed": False,
            "local_skills_allowed": True,
        },
    )

    with pytest.raises(
        TemporalActivityRuntimeError,
        match="repo skill source for 'repo-skill' is disabled by skill source policy",
    ):
        TemporalAgentRuntimeActivities._validate_selected_skill_projection(
            visible_path=workspace / ".agents" / "skills",
            selected_skill="repo-skill",
            resolved_skillset=resolved_skillset,
        )


@pytest.mark.asyncio
async def test_agent_runtime_selected_skill_projection_enforces_local_source_policy(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "repo"
    skill_dir = workspace / ".agents" / "skills" / "local-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("local skill body\n", encoding="utf-8")
    (workspace / ".agents" / "skills" / "_manifest.json").write_text(
        json.dumps(
            {
                "snapshot_id": "skillset-local",
                "skills": [
                    {
                        "name": "local-skill",
                        "source_kind": "local",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    resolved_skillset = ResolvedSkillSet(
        snapshot_id="skillset-local",
        resolved_at=datetime.now(UTC),
        skills=[
            ResolvedSkillEntry(
                skill_name="local-skill",
                content_ref="art-local-skill-body",
                content_digest="sha256:test",
                provenance=AgentSkillProvenance(
                    source_kind=AgentSkillSourceKind.LOCAL,
                    source_path=".agents/skills/local/local-skill",
                ),
            )
        ],
        policy_summary={
            "repo_skills_allowed": True,
            "local_skills_allowed": False,
        },
    )

    with pytest.raises(
        TemporalActivityRuntimeError,
        match="local skill source for 'local-skill' is disabled by skill source policy",
    ):
        TemporalAgentRuntimeActivities._validate_selected_skill_projection(
            visible_path=workspace / ".agents" / "skills",
            selected_skill="local-skill",
            resolved_skillset=resolved_skillset,
        )


@pytest.mark.asyncio
async def test_agent_runtime_selected_skill_projection_rejects_repo_skill_with_missing_policy_summary(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "repo"
    skill_dir = workspace / ".agents" / "skills" / "repo-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("repo skill body\n", encoding="utf-8")
    (workspace / ".agents" / "skills" / "_manifest.json").write_text(
        json.dumps(
            {
                "snapshot_id": "skillset-repo",
                "skills": [
                    {
                        "name": "repo-skill",
                        "source_kind": "repo",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    resolved_skillset = ResolvedSkillSet(
        snapshot_id="skillset-repo",
        resolved_at=datetime.now(UTC),
        skills=[
            ResolvedSkillEntry(
                skill_name="repo-skill",
                content_ref="art-repo-skill-body",
                content_digest="sha256:test",
                provenance=AgentSkillProvenance(
                    source_kind=AgentSkillSourceKind.REPO,
                    source_path=".agents/skills/repo-skill",
                ),
            )
        ],
    )
    resolved_skillset.policy_summary = None  # type: ignore[assignment]

    with pytest.raises(
        TemporalActivityRuntimeError,
        match="repo skill source for 'repo-skill' is disabled by skill source policy",
    ):
        TemporalAgentRuntimeActivities._validate_selected_skill_projection(
            visible_path=workspace / ".agents" / "skills",
            selected_skill="repo-skill",
            resolved_skillset=resolved_skillset,
        )


@pytest.mark.asyncio
async def test_agent_runtime_prepare_turn_instructions_enforces_dependency_source_policy_before_materialization(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    managed_root = tmp_path / "agent_jobs"
    monkeypatch.setenv("MOONMIND_AGENT_RUNTIME_STORE", str(managed_root))
    workspace = managed_root / "job-1" / "repo"
    workspace.mkdir(parents=True)
    resolved_skillset = ResolvedSkillSet(
        snapshot_id="skillset-with-repo-dependency",
        resolved_at=datetime.now(UTC),
        skills=[
            ResolvedSkillEntry(
                skill_name="pr-resolver",
                content_ref="art-pr-resolver-body",
                content_digest="sha256:test",
                provenance=AgentSkillProvenance(
                    source_kind=AgentSkillSourceKind.BUILT_IN
                ),
            ),
            ResolvedSkillEntry(
                skill_name="repo-helper",
                content_ref="art-repo-helper-body",
                content_digest="sha256:test",
                provenance=AgentSkillProvenance(
                    source_kind=AgentSkillSourceKind.REPO,
                    source_path=".agents/skills/repo-helper",
                ),
            ),
        ],
        policy_summary={
            "repo_skills_allowed": False,
            "local_skills_allowed": True,
        },
    )
    artifact_service = _StaticArtifactService(
        {
            "art-pr-resolver-snapshot": resolved_skillset.model_dump_json().encode(
                "utf-8"
            ),
        }
    )

    class _UnexpectedMaterializer:
        def __init__(self, **_kwargs: Any) -> None:
            raise AssertionError("skill materializer should not be constructed")

    monkeypatch.setattr(
        activity_runtime_module,
        "AgentSkillMaterializer",
        _UnexpectedMaterializer,
    )
    activities = TemporalAgentRuntimeActivities(artifact_service=artifact_service)

    with pytest.raises(
        TemporalActivityRuntimeError,
        match="repo skill source for 'repo-helper' is disabled by skill source policy",
    ):
        await activities.agent_runtime_prepare_turn_instructions(
            {
                "request": {
                    "agentKind": "managed",
                    "agentId": "codex",
                    "correlationId": "corr-1",
                    "idempotencyKey": "idem-1",
                    "resolvedSkillsetRef": "art-pr-resolver-snapshot",
                    "parameters": {
                        "instructions": "Resolve the PR.",
                        "metadata": {
                            "moonmind": {
                                "selectedSkill": "pr-resolver",
                            },
                        },
                    },
                },
                "workspacePath": str(workspace),
            }
        )


@pytest.mark.asyncio
async def test_agent_runtime_prepare_turn_instructions_preserves_checked_in_skills_before_projection(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    managed_root = tmp_path / "agent_jobs"
    monkeypatch.setenv("MOONMIND_AGENT_RUNTIME_STORE", str(managed_root))
    workspace = managed_root / "job-1" / "repo"
    checked_in_skill = workspace / ".agents" / "skills" / "pr-resolver"
    checked_in_skill.mkdir(parents=True)
    (checked_in_skill / "SKILL.md").write_text(
        "checked-in source input\n",
        encoding="utf-8",
    )
    skill_body = b"resolved active body\n"
    resolved_skillset = ResolvedSkillSet(
        snapshot_id="skillset-pr-resolver",
        resolved_at=datetime.now(UTC),
        skills=[
            ResolvedSkillEntry(
                skill_name="pr-resolver",
                content_ref="art-pr-resolver-body",
                content_digest="sha256:" + hashlib.sha256(skill_body).hexdigest(),
                provenance=AgentSkillProvenance(
                    source_kind=AgentSkillSourceKind.BUILT_IN
                ),
            )
        ],
    )
    artifact_service = _StaticArtifactService(
        {
            "art-pr-resolver-snapshot": resolved_skillset.model_dump_json().encode(
                "utf-8"
            ),
            "art-pr-resolver-body": skill_body,
        }
    )
    activities = TemporalAgentRuntimeActivities(artifact_service=artifact_service)

    result = await activities.agent_runtime_prepare_turn_instructions(
        {
            "request": {
                "agentKind": "managed",
                "agentId": "codex",
                "correlationId": "corr-1",
                "idempotencyKey": "idem-1",
                "resolvedSkillsetRef": "art-pr-resolver-snapshot",
                "parameters": {
                    "instructions": "Resolve the PR.",
                    "metadata": {
                        "moonmind": {
                            "selectedSkill": "pr-resolver",
                        },
                    },
                },
            },
            "workspacePath": str(workspace),
        }
    )

    assert result.startswith("Active MoonMind skill snapshot:")
    visible_skills = workspace / ".agents" / "skills"
    assert visible_skills.is_dir()
    assert not visible_skills.is_symlink()
    assert (visible_skills / "pr-resolver" / "SKILL.md").read_text(
        encoding="utf-8"
    ) == "checked-in source input\n"
    active_skill = (
        managed_root
        / "job-1"
        / "runtime"
        / "skills_active"
        / "skillset-pr-resolver"
        / "pr-resolver"
        / "SKILL.md"
    )
    assert active_skill.read_text(encoding="utf-8") == "resolved active body\n"
    assert str(active_skill) in result
    assert "repo-authored source" in result


@pytest.mark.asyncio
async def test_agent_runtime_prepare_turn_instructions_requires_skillset_ref_for_selected_skill(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    managed_root = tmp_path / "agent_jobs"
    monkeypatch.setenv("MOONMIND_AGENT_RUNTIME_STORE", str(managed_root))
    workspace = managed_root / "job-1" / "repo"
    workspace.mkdir(parents=True)

    activities = TemporalAgentRuntimeActivities()

    with pytest.raises(TemporalActivityRuntimeError) as exc_info:
        await activities.agent_runtime_prepare_turn_instructions(
            {
                "request": {
                    "agentKind": "managed",
                    "agentId": "codex",
                    "correlationId": "corr-1",
                    "idempotencyKey": "idem-1",
                    "parameters": {
                        "instructions": "Resolve the PR.",
                        "metadata": {
                            "moonmind": {
                                "selectedSkill": "pr-resolver",
                            },
                        },
                    },
                },
                "workspacePath": str(workspace),
            }
        )

    assert "requires request.resolvedSkillsetRef" in str(exc_info.value)


@pytest.mark.asyncio
async def test_agent_runtime_prepare_turn_instructions_skips_skill_snapshot_for_external_workspace(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    managed_root = tmp_path / "agent_jobs"
    monkeypatch.setenv("MOONMIND_AGENT_RUNTIME_STORE", str(managed_root))
    workspace = tmp_path / "external" / "repo"
    workspace.mkdir(parents=True)

    activities = TemporalAgentRuntimeActivities()
    result = await activities.agent_runtime_prepare_turn_instructions(
        {
            "request": {
                "agentKind": "managed",
                "agentId": "codex",
                "correlationId": "corr-1",
                "idempotencyKey": "idem-1",
                "parameters": {
                    "instructions": "Resolve the PR.",
                    "publishMode": "none",
                    "metadata": {
                        "moonmind": {
                            "selectedSkill": "pr-resolver",
                        },
                    },
                },
            },
            "workspacePath": str(workspace),
        }
    )

    assert not result.startswith("Active MoonMind skill snapshot:")
    assert ".agents/skills/pr-resolver/SKILL.md" not in result
    assert not (workspace.parent / "runtime" / "skills_active").exists()
    assert not (workspace / "skills_active").exists()
    assert not (workspace / ".agents" / "skills").exists()


@pytest.mark.asyncio
async def test_agent_runtime_prepare_turn_instructions_treats_auto_skill_as_no_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    empty_skill_root = tmp_path / "empty_skills"
    empty_skill_root.mkdir()
    monkeypatch.setattr(
        "moonmind.workflows.skills.resolver.settings.workflow.skills_local_mirror_root",
        str(empty_skill_root),
    )
    monkeypatch.setattr(
        "moonmind.workflows.skills.resolver.settings.workflow.skills_legacy_mirror_root",
        str(empty_skill_root),
    )
    managed_root = tmp_path / "agent_jobs"
    monkeypatch.setenv("MOONMIND_AGENT_RUNTIME_STORE", str(managed_root))

    job_root = managed_root / "job-1"
    workspace = job_root / "repo"
    workspace.mkdir(parents=True)

    activities = TemporalAgentRuntimeActivities()
    result = await activities.agent_runtime_prepare_turn_instructions(
        {
            "request": {
                "agentKind": "managed",
                "agentId": "codex",
                "correlationId": "corr-1",
                "idempotencyKey": "idem-1",
                "parameters": {
                    "instructions": "Use the default runtime behavior.",
                    "publishMode": "none",
                    "metadata": {
                        "moonmind": {
                            "selectedSkill": "auto",
                        },
                    },
                },
            },
            "workspacePath": str(workspace),
        }
    )

    assert result.startswith("Use the default runtime behavior.")
    assert "Active MoonMind skill snapshot:" not in result
    assert not (job_root / "runtime" / "skills_active").exists()
    assert not (workspace / ".agents" / "skills").exists()


async def test_publish_path_filter_excludes_generated_skill_projection_symlink(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "repo"
    backing = tmp_path / "runtime" / "skills_active"
    backing.mkdir(parents=True)
    projection = workspace / ".agents" / "skills"
    projection.parent.mkdir(parents=True)
    projection.symlink_to(backing, target_is_directory=True)

    assert TemporalAgentRuntimeActivities._should_exclude_publish_path(
        ".agents/skills",
        workspace=workspace,
    )


async def test_publish_path_filter_normalizes_relative_workspace_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "repo"
    backing = tmp_path / "runtime" / "skills_active"
    backing.mkdir(parents=True)
    projection = workspace / ".agents" / "skills"
    projection.parent.mkdir(parents=True)
    projection.symlink_to(backing, target_is_directory=True)
    monkeypatch.chdir(tmp_path)

    assert TemporalAgentRuntimeActivities._should_exclude_publish_path(
        ".agents/skills/pr-resolver/SKILL.md",
        workspace=Path("repo"),
    )


async def test_publish_path_filter_excludes_generated_compatibility_skill_links(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "repo"
    backing = tmp_path / "runtime" / "skills_active"
    backing.mkdir(parents=True)
    gemini_projection = workspace / ".gemini" / "skills"
    repo_projection = workspace / "skills_active"
    gemini_projection.parent.mkdir(parents=True)
    gemini_projection.symlink_to(backing, target_is_directory=True)
    repo_projection.symlink_to(backing, target_is_directory=True)

    assert TemporalAgentRuntimeActivities._should_exclude_publish_path(
        ".gemini/skills",
        workspace=workspace,
    )
    assert TemporalAgentRuntimeActivities._should_exclude_publish_path(
        ".gemini/skills/pr-resolver/SKILL.md",
        workspace=workspace,
    )
    assert TemporalAgentRuntimeActivities._should_exclude_publish_path(
        "skills_active/pr-resolver/SKILL.md",
        workspace=workspace,
    )


async def test_commit_workspace_changes_filters_skill_projection_from_string_workspace(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "repo"
    backing = tmp_path / "runtime" / "skills_active"
    backing.mkdir(parents=True)
    projection = workspace / ".agents" / "skills"
    projection.parent.mkdir(parents=True)
    projection.symlink_to(backing, target_is_directory=True)
    activities = TemporalAgentRuntimeActivities()
    recorded_calls: list[tuple[object, ...]] = []
    call_count = 0

    async def _mock_exec(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        recorded_calls.append(args)
        proc = AsyncMock()
        if call_count == 1:
            proc.communicate = AsyncMock(
                return_value=(b" D .agents/skills/pr-resolver/SKILL.md\0", b"")
            )
        elif call_count == 2:
            proc.communicate = AsyncMock(
                return_value=(b".agents/skills/pr-resolver/SKILL.md\n", b"")
            )
        else:
            raise AssertionError(f"Unexpected subprocess call #{call_count}: {args!r}")
        proc.returncode = 0
        return proc

    original_env = dict()
    with pytest.MonkeyPatch.context() as patcher:
        patcher.setattr(asyncio, "create_subprocess_exec", _mock_exec)
        result = await activities._commit_workspace_changes_if_needed(
            str(workspace),
            run_id="run-1",
            env=original_env,
        )

    assert result == {}
    assert len(recorded_calls) == 2


async def test_commit_workspace_changes_commits_only_publishable_staged_paths(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    activities = TemporalAgentRuntimeActivities()
    recorded_calls: list[tuple[object, ...]] = []
    call_count = 0

    async def _mock_exec(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        recorded_calls.append(args)
        proc = AsyncMock()
        if call_count == 1:
            proc.communicate = AsyncMock(
                return_value=(b"D  publishable.txt\0D  CLAUDE.md\0", b"")
            )
        elif call_count == 2:
            proc.communicate = AsyncMock(
                return_value=(b"publishable.txt\nCLAUDE.md\n", b"")
            )
        elif call_count == 3:
            proc.communicate = AsyncMock(return_value=(b"[main abc123] commit\n", b""))
        else:
            raise AssertionError(f"Unexpected subprocess call #{call_count}: {args!r}")
        proc.returncode = 0
        return proc

    with pytest.MonkeyPatch.context() as patcher:
        patcher.setattr(asyncio, "create_subprocess_exec", _mock_exec)
        result = await activities._commit_workspace_changes_if_needed(
            str(workspace),
            run_id="run-1",
            env={},
            commit_message="publish result",
        )

    assert result == {"push_commit_message": "publish result"}
    assert recorded_calls[0][-4:] == (
        "status",
        "--porcelain=v1",
        "-z",
        "--untracked-files=all",
    )
    assert recorded_calls[1][-3:] == ("diff", "--cached", "--name-only")
    assert recorded_calls[2][-5:] == (
        "commit",
        "-m",
        "publish result",
        "--",
        "publishable.txt",
    )
    assert "CLAUDE.md" not in recorded_calls[2]


async def test_publish_path_filter_allows_checked_in_skill_directory(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "repo"
    skill_file = workspace / ".agents" / "skills" / "pr-resolver" / "SKILL.md"
    skill_file.parent.mkdir(parents=True)
    skill_file.write_text("# Repo Skill\n", encoding="utf-8")

    assert not TemporalAgentRuntimeActivities._should_exclude_publish_path(
        ".agents/skills/pr-resolver/SKILL.md",
        workspace=workspace,
    )


@pytest.mark.asyncio
async def test_agent_runtime_prepare_turn_instructions_includes_context_artifact_reference(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async def _fake_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    def _fake_retrieve(self, request):
        return (
            ContextPack(
                items=[ContextItem(score=0.9, source="docs/spec.md", text="retrieved text")],
                filters={"repo": "moonmind"},
                budgets={},
                usage={"tokens": 8, "latency_ms": 4},
                transport="direct",
                context_text="Retrieved context snippet",
                retrieved_at="2026-04-24T00:00:00Z",
                telemetry_id="tid-1",
            ),
            None,
        )

    monkeypatch.setattr("moonmind.rag.context_injection.asyncio.to_thread", _fake_to_thread)
    monkeypatch.setattr(
        "moonmind.rag.context_injection.ContextInjectionService._retrieve_context_pack",
        _fake_retrieve,
    )

    activities = TemporalAgentRuntimeActivities()

    result = await activities.agent_runtime_prepare_turn_instructions(
        {
            "request": {
                "agentKind": "managed",
                "agentId": "codex",
                "correlationId": "corr-1",
                "idempotencyKey": "idem-1",
                "instructionRef": "artifact:instructions",
                "parameters": {"publishMode": "none"},
            },
            "workspacePath": str(tmp_path),
        }
    )

    assert "BEGIN_RETRIEVED_CONTEXT" in result
    assert "Retrieved context artifact: artifacts/context/" in result
    assert str(tmp_path) not in result


@pytest.mark.asyncio
@pytest.mark.parametrize("metadata_only", [False, True])
async def test_agent_runtime_prepare_turn_instructions_returns_durable_retrieval_metadata_when_requested(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    metadata_only: bool,
) -> None:
    async def _fake_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    def _fake_retrieve(self, request):
        return (
            ContextPack(
                items=[ContextItem(score=0.9, source="docs/spec.md", text="retrieved text")],
                filters={"repo": "moonmind"},
                budgets={},
                usage={"tokens": 8, "latency_ms": 4},
                transport="direct",
                context_text="Retrieved context snippet",
                retrieved_at="2026-04-24T00:00:00Z",
                telemetry_id="tid-1",
            ),
            None,
        )

    monkeypatch.setattr("moonmind.rag.context_injection.asyncio.to_thread", _fake_to_thread)
    monkeypatch.setattr(
        "moonmind.rag.context_injection.ContextInjectionService._retrieve_context_pack",
        _fake_retrieve,
    )

    activities = TemporalAgentRuntimeActivities()

    result = await activities.agent_runtime_prepare_turn_instructions(
        {
            "request": {
                "agentKind": "managed",
                "agentId": "codex",
                "correlationId": "corr-1",
                "idempotencyKey": "idem-1",
                "instructionRef": "artifact:instructions",
                "parameters": {"publishMode": "none"},
            },
            "workspacePath": str(tmp_path),
            "includePreparedRequestMetadata": True,
            "metadataOnly": metadata_only,
        }
    )

    assert isinstance(result, dict)
    if metadata_only:
        assert "instructions" not in result
    else:
        assert "BEGIN_RETRIEVED_CONTEXT" in result["instructions"]
    assert result["durableRetrievalMetadata"]["latestContextPackRef"].startswith(
        "artifacts/context/"
    )
    assert result["durableRetrievalMetadata"]["retrievalDurabilityAuthority"] == "artifact_ref"


@pytest.mark.asyncio
async def test_agent_runtime_prepare_turn_instructions_requires_workspace_for_instruction_ref() -> None:
    activities = TemporalAgentRuntimeActivities()

    with pytest.raises(
        TemporalActivityRuntimeError,
        match=(
            "payload.workspace_path or payload.workspacePath is required "
            "when request.instructionRef is set"
        ),
    ):
        await activities.agent_runtime_prepare_turn_instructions(
            {
                "request": {
                    "agentKind": "managed",
                    "agentId": "codex",
                    "correlationId": "corr-1",
                    "idempotencyKey": "idem-1",
                    "instructionRef": "artifact:instructions",
                    "parameters": {"publishMode": "none"},
                }
            }
        )

@pytest.mark.asyncio
async def test_agent_runtime_prepare_turn_instructions_temporal_boundary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from temporalio import activity

    class _FakeContextInjectionService:
        async def inject_context(
            self,
            *,
            request: Any,
            workspace_path: Path,
        ) -> None:
            assert workspace_path == tmp_path
            request.instruction_ref = "Injected context instruction"

    monkeypatch.setattr(
        "moonmind.rag.context_injection.ContextInjectionService",
        _FakeContextInjectionService,
    )
    activities_impl = TemporalAgentRuntimeActivities()

    @activity.defn(name="agent_runtime.prepare_turn_instructions")
    async def _agent_runtime_prepare_turn_instructions_wrapper(
        request: dict,
    ) -> str:
        return await activities_impl.agent_runtime_prepare_turn_instructions(request)

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="boundary-test-queue-prepare-turn-instructions",
            workflows=[AgentRuntimePrepareTurnInstructionsBoundaryTest],
            activities=[_agent_runtime_prepare_turn_instructions_wrapper],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            result = await env.client.execute_workflow(
                AgentRuntimePrepareTurnInstructionsBoundaryTest.run,
                {
                    "request": {
                        "agentKind": "managed",
                        "agentId": "codex",
                        "correlationId": "corr-1",
                        "idempotencyKey": "idem-1",
                        "instructionRef": "artifact:instructions",
                        "parameters": {"publishMode": "none"},
                    },
                    "workspacePath": str(tmp_path),
                },
                id="boundary-test-prepare-turn-instructions",
                task_queue="boundary-test-queue-prepare-turn-instructions",
            )

            assert result.startswith("Injected context instruction")
            assert "Managed Codex CLI note:" in result

async def test_agent_runtime_reconcile_managed_sessions_returns_bounded_summary() -> None:
    class _Controller:
        async def reconcile(self) -> list[dict[str, Any]]:
            return [
                _session_record("sess-ready", status="ready"),
                _session_record("sess-stale-degraded", status="degraded"),
                _session_record("sess-orphaned-container", status="degraded"),
            ]

        async def reap_orphan_session_containers(self) -> ManagedSessionReapResult:
            return ManagedSessionReapResult(
                scanned_containers=4,
                reaped_session_ids=("sess-orphaned-container",),
                reaped_containers=2,
                skipped_active=1,
                skipped_recent=1,
                forced_stale=1,
                scanned_volumes=5,
                reaped_volumes=3,
                skipped_active_volumes=1,
                skipped_recent_volumes=1,
            )

    activities = TemporalAgentRuntimeActivities(session_controller=_Controller())

    result = await activities.agent_runtime_reconcile_managed_sessions({})

    assert result == {
        "managedSessionRecordsReconciled": 3,
        "degradedSessionRecords": 2,
        "sessionIds": [
            "sess-ready",
            "sess-stale-degraded",
            "sess-orphaned-container",
        ],
        "truncated": False,
        "orphanContainersReaped": 2,
        "orphanSessionIdsReaped": ["sess-orphaned-container"],
        "orphanReapSkippedRecent": 1,
        "orphanReapForcedStale": 1,
        "orphanVolumesScanned": 5,
        "orphanVolumesReaped": 3,
        "orphanVolumeReapSkippedActive": 1,
        "orphanVolumeReapSkippedRecent": 1,
    }


async def test_agent_runtime_reconcile_orphan_reap_failure_is_best_effort() -> None:
    class _Controller:
        async def reconcile(self) -> list[dict[str, Any]]:
            return [_session_record("sess-ready", status="ready")]

        async def reap_orphan_session_containers(self) -> ManagedSessionReapResult:
            raise RuntimeError("docker daemon unavailable")

    activities = TemporalAgentRuntimeActivities(session_controller=_Controller())

    result = await activities.agent_runtime_reconcile_managed_sessions({})

    # A reap failure must not fail reconcile; reattach results still surface.
    assert result["managedSessionRecordsReconciled"] == 1
    assert result["orphanContainersReaped"] == 0
    assert result["orphanSessionIdsReaped"] == []
    assert result["orphanReapSkippedRecent"] == 0
    assert result["orphanReapForcedStale"] == 0
    assert result["orphanVolumesScanned"] == 0
    assert result["orphanVolumesReaped"] == 0

async def test_agent_runtime_cleanup_managed_runtime_files_activity_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_root = tmp_path / "agent_jobs"
    run_root = runtime_root / "run-mm-949"
    run_root.mkdir(parents=True)
    old = datetime(2026, 4, 1, 12, 0, tzinfo=UTC)
    os_epoch = old.timestamp()
    run_root.joinpath("repo").mkdir()
    run_root.joinpath("repo", "README.md").write_text("done\n", encoding="utf-8")
    run_root.touch()
    os.utime(run_root, (os_epoch, os_epoch))
    run_store = ManagedRunStore(runtime_root / "managed_runs")
    run_store.save(
        ManagedRunRecord(
            runId="run-mm-949",
            workflowId="mm:workflow-mm-949",
            agentId="agent-1",
            runtimeId="codex-cli",
            status="completed",
            startedAt=old - timedelta(hours=1),
            finishedAt=old,
            workspacePath=str(run_root / "repo"),
        )
    )
    monkeypatch.setenv("MOONMIND_AGENT_RUNTIME_STORE", str(runtime_root))
    activities = TemporalAgentRuntimeActivities(run_store=run_store)

    result = await activities.agent_runtime_cleanup_managed_runtime_files(
        {
            "config": {
                "enabled": True,
                "dryRun": True,
                "runtimeStoreRoot": str(runtime_root),
                "artifactRoot": str(runtime_root / "artifacts"),
                "lockPath": str(runtime_root / ".janitor.lock"),
                "workspaceRetentionDays": 30,
                "artifactRetentionDays": 30,
                "recordRetentionDays": None,
                "graceSeconds": 3600,
                "maxDeletePaths": 25,
                "maxDeleteBytes": None,
            }
        }
    )

    assert result["disabled"] is False
    assert result["dryRun"] is True
    assert result["scannedRunRecords"] == 1
    assert result["decisions"][0]["classification"] == "eligible"
    assert result["decisions"][0]["reason"] == "dry-run would delete"


async def test_agent_runtime_cleanup_managed_runtime_files_uses_docker_references(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_root = tmp_path / "agent_jobs"
    run_root = runtime_root / "run-mm-949"
    run_root.mkdir(parents=True)
    old = datetime(2026, 4, 1, 12, 0, tzinfo=UTC)
    os.utime(run_root, (old.timestamp(), old.timestamp()))
    run_store = ManagedRunStore(runtime_root / "managed_runs")
    run_store.save(
        ManagedRunRecord(
            runId="run-mm-949",
            workflowId="mm:workflow-mm-949",
            agentId="agent-1",
            runtimeId="codex-cli",
            status="completed",
            startedAt=old - timedelta(hours=1),
            finishedAt=old,
            workspacePath=str(run_root / "repo"),
        )
    )
    monkeypatch.setenv("MOONMIND_AGENT_RUNTIME_STORE", str(runtime_root))

    class _Controller:
        async def collect_managed_runtime_cleanup_docker_references(
            self,
        ) -> dict[str, object]:
            return {"activeMountPaths": [str(run_root)]}

    activities = TemporalAgentRuntimeActivities(
        run_store=run_store,
        session_controller=_Controller(),
    )

    result = await activities.agent_runtime_cleanup_managed_runtime_files(
        {
            "config": {
                "enabled": True,
                "dryRun": False,
                "runtimeStoreRoot": str(runtime_root),
                "artifactRoot": str(runtime_root / "artifacts"),
                "lockPath": str(runtime_root / ".janitor.lock"),
                "workspaceRetentionDays": 30,
                "artifactRetentionDays": 30,
                "recordRetentionDays": None,
                "graceSeconds": 3600,
                "maxDeletePaths": 25,
                "maxDeleteBytes": None,
            }
        }
    )

    assert result["decisions"][0]["classification"] == "protected_active"
    assert result["decisions"][0]["reason"] == "live Docker reference"
    assert run_root.exists()


async def test_agent_runtime_cleanup_managed_runtime_files_fails_closed_without_docker_scan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_root = tmp_path / "agent_jobs"
    run_root = runtime_root / "run-mm-949"
    run_root.mkdir(parents=True)
    old = datetime(2026, 4, 1, 12, 0, tzinfo=UTC)
    os.utime(run_root, (old.timestamp(), old.timestamp()))
    run_store = ManagedRunStore(runtime_root / "managed_runs")
    run_store.save(
        ManagedRunRecord(
            runId="run-mm-949",
            workflowId="mm:workflow-mm-949",
            agentId="agent-1",
            runtimeId="codex-cli",
            status="completed",
            startedAt=old - timedelta(hours=1),
            finishedAt=old,
            workspacePath=str(run_root / "repo"),
        )
    )
    monkeypatch.setenv("MOONMIND_AGENT_RUNTIME_STORE", str(runtime_root))
    activities = TemporalAgentRuntimeActivities(run_store=run_store)

    result = await activities.agent_runtime_cleanup_managed_runtime_files(
        {
            "config": {
                "enabled": True,
                "dryRun": False,
                "runtimeStoreRoot": str(runtime_root),
                "artifactRoot": str(runtime_root / "artifacts"),
                "lockPath": str(runtime_root / ".janitor.lock"),
                "workspaceRetentionDays": 30,
                "artifactRetentionDays": 30,
                "recordRetentionDays": None,
                "graceSeconds": 3600,
                "maxDeletePaths": 25,
                "maxDeleteBytes": None,
            }
        }
    )

    assert result["decisions"][0]["classification"] == "protected_active"
    assert result["decisions"][0]["reason"] == "docker reference scan unavailable"
    assert result["errors"] == ["docker reference scan unavailable"]
    assert run_root.exists()


@pytest.mark.asyncio
async def test_agent_runtime_reconcile_managed_sessions_uses_bounded_heartbeating(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Controller:
        async def reconcile(self) -> list[dict[str, Any]]:
            return [
                _session_record(f"sess-{index}", status="degraded")
                for index in range(60)
            ]

        async def reap_orphan_session_containers(self) -> ManagedSessionReapResult:
            return ManagedSessionReapResult()

    heartbeat_payloads: list[dict[str, Any]] = []

    async def _fake_await_with_activity_heartbeats(
        awaitable: Any,
        *,
        heartbeat_payload: dict[str, Any],
        interval_seconds: float | None = None,
    ) -> Any:
        del interval_seconds
        heartbeat_payloads.append(dict(heartbeat_payload))
        return await awaitable

    monkeypatch.setattr(
        activity_runtime_module,
        "_await_with_activity_heartbeats",
        _fake_await_with_activity_heartbeats,
    )
    activities = TemporalAgentRuntimeActivities(session_controller=_Controller())

    result = await activities.agent_runtime_reconcile_managed_sessions({})

    # Both the reattach pass and the orphan reap pass are heartbeated.
    assert heartbeat_payloads == [
        {"activityType": "agent_runtime.reconcile_managed_sessions"},
        {"activityType": "agent_runtime.reconcile_managed_sessions"},
    ]
    assert result["managedSessionRecordsReconciled"] == 60
    assert result["degradedSessionRecords"] == 60
    assert len(result["sessionIds"]) == 50
    assert result["orphanContainersReaped"] == 0
    assert result["truncated"] is True


async def test_agent_runtime_cleanup_managed_runtime_files_returns_observability_result(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from moonmind.workflows.temporal.runtime import cleanup as cleanup_module

    run_store = ManagedRunStore(tmp_path / "managed_runs")
    cleanup_calls: list[dict[str, object]] = []

    class _CleanupResult:
        def to_dict(self) -> dict[str, Any]:
            return {
                "disabled": False,
                "dryRun": True,
                "eligibleRoots": 1,
                "candidateSamples": [
                    {
                        "resource_class": "workspace_root",
                        "safe_path": "store:workspaces/mm-workflow",
                        "classification": "eligible",
                        "reason": "all retained-state safety gates passed",
                        "estimated_bytes": 42,
                    }
                ],
                "metrics": {"resource.workspace_root.eligible": 1},
            }

    def _fake_cleanup_managed_runtime_files(
        *,
        run_store: ManagedRunStore,
        session_store: ManagedSessionStore,
        config: Any,
        docker_reference_provider: Any,
        progress_callback: Any,
    ) -> _CleanupResult:
        cleanup_calls.append(
            {
                "run_store": run_store,
                "session_store_root": session_store.store_root,
                "runtime_store_root": config.runtime_store_root,
                "docker_reference_provider": docker_reference_provider,
                "progress_callback": callable(progress_callback),
            }
        )
        return _CleanupResult()

    monkeypatch.setattr(
        cleanup_module,
        "cleanup_managed_runtime_files",
        _fake_cleanup_managed_runtime_files,
    )

    activities = TemporalAgentRuntimeActivities(
        run_store=run_store,
    )

    result = await activities.agent_runtime_cleanup_managed_runtime_files(
        {
            "config": {
                "enabled": True,
                "dryRun": True,
                "runtimeStoreRoot": str(tmp_path),
                "artifactRoot": str(tmp_path / "artifacts"),
                "lockPath": str(tmp_path / ".janitor.lock"),
            }
        }
    )

    assert cleanup_calls == [
        {
            "run_store": run_store,
            "session_store_root": tmp_path / "managed_sessions",
            "runtime_store_root": tmp_path,
            "docker_reference_provider": None,
            "progress_callback": True,
        }
    ]
    assert result["eligibleRoots"] == 1
    assert result["candidateSamples"][0]["safe_path"] == "store:workspaces/mm-workflow"
    assert result["metrics"] == {"resource.workspace_root.eligible": 1}

async def test_agent_runtime_session_request_logs_bounded_telemetry_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    log_contexts: list[dict[str, Any]] = []
    monkeypatch.setattr(
        activity_runtime_module.logger,
        "info",
        lambda _message, **kwargs: log_contexts.append(
            dict(kwargs.get("extra", {}).get("managed_session", {}))
        ),
    )

    validated = TemporalAgentRuntimeActivities._validate_session_request(
        {
            "sessionId": "sess:wf-run-1:codex_cli",
            "sessionEpoch": 1,
            "containerId": "container-1",
            "threadId": "thread-1",
            "instructions": "Write a private implementation plan",
        },
        activity_type="agent_runtime.send_turn",
        model_type=activity_runtime_module.SendCodexManagedSessionTurnRequest,
    )
    raw_context = activity_runtime_module._managed_session_telemetry_context(
        {
            "sessionId": "sess:wf-run-1:codex_cli",
            "sessionEpoch": 1,
            "containerId": "container-1",
            "threadId": "thread-1",
            "instructions": "Write a private implementation plan",
            "rawLog": "terminal scrollback",
            "token": "ghp_secret_token",
        },
        activity_type="agent_runtime.send_turn",
    )

    assert validated.session_id == "sess:wf-run-1:codex_cli"
    assert log_contexts == [
        {
            "activityType": "agent_runtime.send_turn",
            "sessionId": "sess:wf-run-1:codex_cli",
            "sessionEpoch": 1,
            "containerId": "container-1",
            "threadId": "thread-1",
        }
    ]
    assert raw_context == log_contexts[0]
    rendered = str(log_contexts)
    assert "Write a private implementation plan" not in rendered
    assert "terminal scrollback" not in rendered
    assert "ghp_secret_token" not in rendered

async def test_managed_session_telemetry_context_uses_trusted_activity_type() -> None:
    raw_context = activity_runtime_module._managed_session_telemetry_context(
        {
            "activityType": "payload.controlled_activity",
            "sessionId": "sess:wf-run-1:codex_cli",
        },
        activity_type="agent_runtime.send_turn",
    )

    assert raw_context == {
        "activityType": "agent_runtime.send_turn",
        "sessionId": "sess:wf-run-1:codex_cli",
    }

async def test_launch_session_materializes_claude_oauth_home_environment(
    tmp_path: Path,
) -> None:
    controller = AsyncMock()
    controller.launch_session = AsyncMock(
        return_value=CodexManagedSessionHandle(
            sessionState={
                "sessionId": "sess-claude-1",
                "sessionEpoch": 1,
                "containerId": "ctr-claude-1",
                "threadId": "thread-claude-1",
            },
            status="ready",
            imageRef="moonmind:latest",
            metadata={"vendorThreadId": "vendor-thread-claude-1"},
        )
    )
    activities = TemporalAgentRuntimeActivities(session_controller=controller)
    codex_home_path = tmp_path / "task-1" / ".moonmind" / "codex-home"

    result = await activities.agent_runtime_launch_session(
        {
            "request": {
                "agentRunId": "task-1",
                "sessionId": "sess-claude-1",
                "threadId": "thread-claude-1",
                "workspacePath": str(tmp_path / "task-1" / "repo"),
                "sessionWorkspacePath": str(tmp_path / "task-1" / "session"),
                "artifactSpoolPath": str(tmp_path / "task-1" / "artifacts"),
                "codexHomePath": str(codex_home_path),
                "imageRef": "moonmind:latest",
                "environment": {
                    "ANTHROPIC_API_KEY": "ambient-anthropic-key",
                    "CLAUDE_API_KEY": "ambient-claude-key",
                    "OPENAI_API_KEY": "ambient-openai-key",
                },
            },
            "profile": {
                "runtimeId": "claude_code",
                "profileId": "claude_anthropic",
                "providerId": "anthropic",
                "credentialSource": "oauth_volume",
                "runtimeMaterializationMode": "oauth_home",
                "volumeRef": "claude_auth_volume",
                "volumeMountPath": "/home/app/.claude",
                "clearEnvKeys": [
                    "ANTHROPIC_API_KEY",
                    "CLAUDE_API_KEY",
                    "OPENAI_API_KEY",
                ],
            },
        }
    )

    validated_request = controller.launch_session.await_args.args[0]
    environment = validated_request.environment
    assert environment["MANAGED_AUTH_VOLUME_PATH"] == "/home/app/.claude"
    assert environment["CLAUDE_HOME"] == "/home/app/.claude"
    assert environment["CLAUDE_VOLUME_PATH"] == "/home/app/.claude"
    assert "ANTHROPIC_API_KEY" not in environment
    assert "CLAUDE_API_KEY" not in environment
    assert "OPENAI_API_KEY" not in environment
    diagnostics = result.metadata["authDiagnostics"]
    assert diagnostics["profileRef"] == "claude_anthropic"
    assert diagnostics["runtimeId"] == "claude_code"
    assert diagnostics["volumeRef"] == "claude_auth_volume"
    assert diagnostics["authMountTarget"] == "/home/app/.claude"

async def test_launch_session_failure_redacts_claude_auth_paths(
    tmp_path: Path,
) -> None:
    controller = AsyncMock()
    controller.launch_session = AsyncMock(
        side_effect=RuntimeError(
            "/home/app/.claude/credentials.json token=claude-secret failed"
        )
    )
    activities = TemporalAgentRuntimeActivities(session_controller=controller)

    with pytest.raises(
        TemporalActivityRuntimeError,
        match="component=managed_session_controller",
    ) as exc_info:
        await activities.agent_runtime_launch_session(
            {
                "request": {
                    "agentRunId": "task-1",
                    "sessionId": "sess-claude-1",
                    "threadId": "thread-claude-1",
                    "workspacePath": str(tmp_path / "task-1" / "repo"),
                    "sessionWorkspacePath": str(tmp_path / "task-1" / "session"),
                    "artifactSpoolPath": str(tmp_path / "task-1" / "artifacts"),
                    "codexHomePath": str(
                        tmp_path / "task-1" / ".moonmind" / "codex-home"
                    ),
                    "imageRef": "moonmind:latest",
                },
                "profile": {
                    "runtimeId": "claude_code",
                    "profileId": "claude_anthropic",
                    "providerId": "anthropic",
                    "credentialSource": "oauth_volume",
                    "runtimeMaterializationMode": "oauth_home",
                    "volumeRef": "claude_auth_volume",
                    "volumeMountPath": "/home/app/.claude",
                },
            }
        )

    message = str(exc_info.value)
    assert "claude-secret" not in message
    assert "/home/app/.claude/credentials.json" not in message
    assert "[REDACTED]" in message
    assert "[REDACTED_AUTH_PATH]" in message

async def test_launch_session_claude_auth_diagnostics_do_not_alias_workspace_or_artifacts(
    tmp_path: Path,
) -> None:
    controller = AsyncMock()
    controller.launch_session = AsyncMock(
        return_value=CodexManagedSessionHandle(
            sessionState={
                "sessionId": "sess-claude-2",
                "sessionEpoch": 1,
                "containerId": "ctr-claude-2",
                "threadId": "thread-claude-2",
            },
            status="ready",
            imageRef="moonmind:latest",
            metadata={},
        )
    )
    activities = TemporalAgentRuntimeActivities(session_controller=controller)
    workspace_path = str(tmp_path / "task-2" / "repo")
    artifact_spool_path = str(tmp_path / "task-2" / "artifacts")

    result = await activities.agent_runtime_launch_session(
        {
            "request": {
                "agentRunId": "task-2",
                "sessionId": "sess-claude-2",
                "threadId": "thread-claude-2",
                "workspacePath": workspace_path,
                "sessionWorkspacePath": str(tmp_path / "task-2" / "session"),
                "artifactSpoolPath": artifact_spool_path,
                "codexHomePath": str(tmp_path / "task-2" / ".moonmind" / "codex-home"),
                "imageRef": "moonmind:latest",
            },
            "profile": {
                "runtimeId": "claude_code",
                "profileId": "claude_anthropic",
                "providerId": "anthropic",
                "credentialSource": "oauth_volume",
                "runtimeMaterializationMode": "oauth_home",
                "volumeRef": "claude_auth_volume",
                "volumeMountPath": "/home/app/.claude",
            },
        }
    )

    diagnostics = result.metadata["authDiagnostics"]
    assert diagnostics["authMountTarget"] == "/home/app/.claude"
    assert diagnostics["authMountTarget"] != workspace_path
    assert diagnostics["authMountTarget"] != artifact_spool_path
    assert diagnostics["volumeRef"] == "claude_auth_volume"

@pytest.mark.asyncio
async def test_agent_runtime_prepare_turn_instructions_adds_retrieval_capability_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RAG_ENABLED", "1")
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    monkeypatch.delenv("MOONMIND_RETRIEVAL_URL", raising=False)

    activities = TemporalAgentRuntimeActivities()

    result = await activities.agent_runtime_prepare_turn_instructions(
        {
            "request": {
                "agentKind": "managed",
                "agentId": "codex",
                "correlationId": "corr-rag-1",
                "idempotencyKey": "idem-rag-1",
                "parameters": {
                    "instructions": "Implement MM-506.",
                    "publishMode": "none",
                },
            },
        }
    )

    assert "MoonMind retrieval capability:" in result
    assert "moonmind rag search" in result
    assert "Managed Codex CLI note:" in result


@pytest.mark.asyncio
async def test_agent_runtime_prepare_turn_instructions_reports_disabled_retrieval_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RAG_ENABLED", "0")

    activities = TemporalAgentRuntimeActivities()

    result = await activities.agent_runtime_prepare_turn_instructions(
        {
            "request": {
                "agentKind": "managed",
                "agentId": "codex",
                "correlationId": "corr-rag-2",
                "idempotencyKey": "idem-rag-2",
                "parameters": {
                    "instructions": "Implement MM-506.",
                    "publishMode": "none",
                },
            },
        }
    )

    assert "MoonMind retrieval capability:" in result
    assert "currently unavailable" in result
    assert "rag_disabled" in result


@pytest.mark.asyncio
async def test_terminal_evidence_activity_fails_completed_prose_without_artifact(
    tmp_path: Path,
) -> None:
    activities = TemporalAgentRuntimeActivities()
    result = await activities.agent_runtime_evaluate_terminal_evidence(
        {
            "workspacePath": str(tmp_path),
            "terminalContract": {
                "contractId": "batch_workflows_fanout.v1",
                "relativePath": "artifacts/batch-workflows-result.json",
                "expectedSchemaVersion": "moonmind.batch-workflows-result.v1",
                "executionRef": "step-1",
            },
            "result": {"summary": "Everything completed successfully."},
        }
    )
    assert result.failure_class == "execution_error"
    assert result.metadata["terminalContractAuthority"] == "MoonMind.AgentRun"
    assert result.metadata["failureCode"] == "INCOMPLETE_TERMINAL_CONTRACT"


@pytest.mark.asyncio
async def test_terminal_evidence_activity_enriches_existing_helper_failure(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "repo"
    spool = tmp_path / "spool"
    targets = workspace / "artifacts/batch-workflows-targets.json"
    targets.parent.mkdir(parents=True)
    targets.write_text("[]", encoding="utf-8")
    spool.mkdir()
    (spool / "batch-workflows-result.json").write_text(
        json.dumps(
            {
                "schemaVersion": "moonmind.batch-workflows-result.v1",
                "contractId": "batch_workflows_fanout.v1",
                "executionRef": "step-1",
                "targetsSha256": hashlib.sha256(b"[]").hexdigest(),
                "status": "partial_failure",
                "requested": 2,
                "created": 1,
                "queued": [{"executionId": "child-1"}],
                "skipped": [],
                "errors": [{"ref": "MM-2", "error": "unavailable"}],
            }
        ),
        encoding="utf-8",
    )
    activities = TemporalAgentRuntimeActivities()
    result = await activities.agent_runtime_evaluate_terminal_evidence(
        {
            "workspacePath": str(workspace),
            "artifactSpoolPath": str(spool),
            "terminalContract": {
                "contractId": "batch_workflows_fanout.v1",
                "relativePath": "artifacts/batch-workflows-result.json",
                "expectedSchemaVersion": "moonmind.batch-workflows-result.v1",
                "executionRef": "step-1",
            },
            "result": {
                "summary": "helper exited 1",
                "failureClass": "execution_error",
                "providerErrorCode": "process_exit_1",
            },
        }
    )
    assert result.summary == "helper exited 1"
    assert result.provider_error_code == "process_exit_1"
    assert result.metadata["failureCode"] == "BATCH_FANOUT_PARTIAL_FAILURE"
    assert result.metadata["queuedChildren"] == [{"executionId": "child-1"}]


@pytest.mark.asyncio
async def test_terminal_evidence_activity_classifies_batch_input_failure_as_user_error(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "repo"
    spool = tmp_path / "spool"
    workspace.mkdir()
    spool.mkdir()
    message = "Range 3370-3425 contains 56 issues; maximum is 25."
    (spool / "batch-workflows-result.json").write_text(
        json.dumps(
            {
                "schemaVersion": "moonmind.batch-workflows-result.v1",
                "contractId": "batch_workflows_fanout.v1",
                "executionRef": "step-invalid-range",
                "targetsSha256": None,
                "status": "failed",
                "requested": 56,
                "created": 0,
                "queued": [],
                "skipped": [],
                "errors": [
                    {"code": "BATCH_FANOUT_INPUT_INVALID", "error": message}
                ],
                "failure": {
                    "code": "BATCH_FANOUT_INPUT_INVALID",
                    "message": message,
                },
            }
        ),
        encoding="utf-8",
    )

    activities = TemporalAgentRuntimeActivities()
    result = await activities.agent_runtime_evaluate_terminal_evidence(
        {
            "workspacePath": str(workspace),
            "artifactSpoolPath": str(spool),
            "terminalContract": {
                "contractId": "batch_workflows_fanout.v1",
                "relativePath": "artifacts/batch-workflows-result.json",
                "expectedSchemaVersion": "moonmind.batch-workflows-result.v1",
                "executionRef": "step-invalid-range",
            },
            "result": {
                "summary": "helper exited 2",
                "failureClass": "execution_error",
                "providerErrorCode": "process_exit_2",
            },
        }
    )

    assert result.failure_class == "user_error"
    assert result.provider_error_code == "BATCH_FANOUT_INPUT_INVALID"
    assert result.summary == message
    assert result.metadata["terminalContractMissingEvidence"] == []


@pytest.mark.asyncio
async def test_terminal_evidence_activity_does_not_trust_rejected_failure_metadata(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "repo"
    spool = tmp_path / "spool"
    workspace.mkdir()
    spool.mkdir()
    (spool / "batch-workflows-result.json").write_text(
        json.dumps(
            {
                "schemaVersion": "moonmind.batch-workflows-result.v1",
                "contractId": "batch_workflows_fanout.v1",
                "executionRef": "step-malformed-input-failure",
                "targetsSha256": None,
                "status": "failed",
                "requested": 1,
                "created": 1,
                "queued": [{"executionId": "unexpected-child"}],
                "skipped": [],
                "errors": [
                    {
                        "code": "BATCH_FANOUT_INPUT_INVALID",
                        "error": "Invalid range.",
                    }
                ],
                "failure": {
                    "code": "BATCH_FANOUT_INPUT_INVALID",
                    "message": "Invalid range.",
                },
            }
        ),
        encoding="utf-8",
    )

    result = await TemporalAgentRuntimeActivities().agent_runtime_evaluate_terminal_evidence(
        {
            "workspacePath": str(workspace),
            "artifactSpoolPath": str(spool),
            "terminalContract": {
                "contractId": "batch_workflows_fanout.v1",
                "relativePath": "artifacts/batch-workflows-result.json",
                "expectedSchemaVersion": "moonmind.batch-workflows-result.v1",
                "executionRef": "step-malformed-input-failure",
            },
            "result": {"summary": "Malformed terminal result."},
        }
    )

    assert result.failure_class == "execution_error"
    assert result.provider_error_code == "INVALID_TERMINAL_EVIDENCE"
    assert result.metadata["failureCode"] == "INVALID_TERMINAL_EVIDENCE"
