"""Unit tests for MM-991 Omnigent terminal execution normalization."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest

from moonmind.omnigent.execute import (
    OmnigentContractError,
    OmnigentSessionStillRunningError,
    _agent_items,
    _resolve_agent_id,
    build_omnigent_result,
    normalize_omnigent_observation,
    run_omnigent_execution,
)
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest


def _request() -> AgentExecutionRequest:
    return AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        executionProfileRef="profile:test",
        correlationId="corr-1",
        idempotencyKey="idem-1",
    )


def test_normalize_waiting_with_elicitation_is_internal_awaiting_approval() -> None:
    assert (
        normalize_omnigent_observation(
            {"session": {"status": "waiting"}, "pending_inputs": [{"id": "el_1"}]}
        )
        == "awaiting_approval"
    )


def test_normalize_unknown_status_raises_contract_error() -> None:
    with pytest.raises(OmnigentContractError, match="Unsupported Omnigent status"):
        normalize_omnigent_observation({"session": {"status": "mystery"}})


def test_normalize_nested_response_terminal_status() -> None:
    assert (
        normalize_omnigent_observation(
            {"type": "response.output_item.done", "response": {"status": "completed"}}
        )
        == "completed"
    )
    assert (
        normalize_omnigent_observation(
            {"data": {"response": {"status": "failed"}}}
        )
        == "failed"
    )


def test_build_omnigent_result_is_compact_terminal_success() -> None:
    result = build_omnigent_result(
        request=_request(),
        terminal_status="completed",
        session_id="sess-1",
        agent_id="agent-1",
        final_snapshot={
            "summary": "finished",
            "outputRefs": ["artifact://transcript", "artifact://snapshot"],
            "diagnosticsRef": "artifact://diagnostics",
            "captureManifestRef": "artifact://capture",
        },
        event_count=12,
    )

    assert result.failure_class is None
    assert result.provider_error_code is None
    assert result.output_refs == ["artifact://transcript", "artifact://snapshot"]
    assert result.diagnostics_ref == "artifact://diagnostics"
    assert result.metadata["providerName"] == "omnigent"
    assert result.metadata["normalizedStatus"] == "completed"
    assert result.metadata["captureManifestRef"] == "artifact://capture"


def test_build_omnigent_result_maps_snake_case_metadata() -> None:
    result = build_omnigent_result(
        request=_request(),
        terminal_status="completed",
        session_id="sess-1",
        agent_id="agent-1",
        final_snapshot={
            "summary": "finished",
            "host_type": "external",
            "capture_manifest_ref": "artifact://capture",
            "github_pr_url": "https://github.example/pr/1",
        },
        event_count=1,
    )

    assert result.metadata["hostType"] == "external"
    assert result.metadata["captureManifestRef"] == "artifact://capture"
    assert result.metadata["githubPrUrl"] == "https://github.example/pr/1"


def test_agent_items_ignores_unexpected_payload_shape() -> None:
    assert _agent_items({"items": "unexpected"}) == []


def test_resolve_agent_id_rejects_unknown_requested_name() -> None:
    with pytest.raises(OmnigentContractError, match="could not be resolved"):
        _resolve_agent_id(
            agents_payload={"items": [{"id": "agent-1", "name": "known"}]},
            requested_name="missing",
        )


def test_build_omnigent_result_is_terminal_failure_with_provider_error() -> None:
    result = build_omnigent_result(
        request=_request(),
        terminal_status="failed",
        session_id="sess-1",
        agent_id="agent-1",
        final_snapshot={"summary": "provider failed"},
        event_count=2,
        provider_error_code="omnigent_failed",
    )

    assert result.failure_class == "execution_error"
    assert result.provider_error_code == "omnigent_failed"
    assert result.output_refs == ["omnigent://sessions/sess-1/snapshot/final"]
    assert result.diagnostics_ref == "omnigent://sessions/sess-1/diagnostics"
    assert result.metadata["normalizedStatus"] == "failed"


def test_build_omnigent_result_uses_valid_failure_class_for_timeout() -> None:
    result = build_omnigent_result(
        request=_request(),
        terminal_status="timed_out",
        session_id="sess-1",
        agent_id=None,
        final_snapshot={"summary": "timed out"},
        event_count=1,
    )

    assert result.failure_class == "system_error"
    assert result.metadata["normalizedStatus"] == "timed_out"


@pytest.mark.asyncio
async def test_run_omnigent_execution_waits_for_terminal_result(monkeypatch) -> None:
    created_clients: list[object] = []
    heartbeats: list[dict[str, Any]] = []

    class FakeClient:
        def __init__(self, **_: object) -> None:
            self.posted_events: list[dict[str, object]] = []
            self.stream_started = False
            created_clients.append(self)

        async def list_agents(self) -> dict[str, object]:
            return {"items": [{"id": "agent-1", "name": "codex-native-ui"}]}

        async def create_session(self, payload: dict[str, object]) -> dict[str, object]:
            assert payload["agent_id"] == "agent-1"
            assert payload["labels"]["moonmind.issue"] == "MM-991"
            return {"id": "session-1"}

        async def post_event(
            self,
            session_id: str,
            payload: dict[str, object],
        ) -> dict[str, object]:
            assert session_id == "session-1"
            assert self.stream_started is True
            self.posted_events.append(payload)
            return {"pending_id": "pending-1"}

        async def stream_events(self, session_id: str):
            assert session_id == "session-1"
            self.stream_started = True
            yield {"session": {"status": "running"}}
            yield {"type": "response.completed"}

        async def get_session(self, session_id: str) -> dict[str, object]:
            assert session_id == "session-1"
            return {
                "status": "completed",
                "summary": "done",
                "outputRefs": ["artifact://final"],
                "diagnosticsRef": "artifact://diagnostics",
            }

    monkeypatch.setenv("OMNIGENT_ENABLED", "true")
    monkeypatch.setenv("OMNIGENT_SERVER_URL", "https://omnigent.test")
    monkeypatch.setattr("moonmind.omnigent.execute.OmnigentHttpClient", FakeClient)
    monkeypatch.setattr(
        "moonmind.omnigent.execute._safe_heartbeat",
        lambda details: heartbeats.append(details),
    )

    result = await run_omnigent_execution(
        AgentExecutionRequest(
            agentKind="external",
            agentId="omnigent",
            correlationId="corr-1",
            idempotencyKey="idem-1",
            parameters={
                "title": "Execute Omnigent",
                "omnigent": {
                    "agent": {"agentName": "codex-native-ui"},
                    "prompt": {"text": "Do the task"},
                },
            },
        )
    )

    assert result.summary == "done"
    assert result.output_refs == ["artifact://final"]
    assert result.diagnostics_ref == "artifact://diagnostics"
    assert result.metadata["normalizedStatus"] == "completed"
    assert created_clients
    assert heartbeats
    assert all("normalizedStatus" in heartbeat for heartbeat in heartbeats)
    assert all("eventsCaptured" in heartbeat for heartbeat in heartbeats)


@pytest.mark.asyncio
async def test_run_omnigent_execution_reports_httpx_transport_errors(
    monkeypatch,
) -> None:
    class FakeClient:
        def __init__(self, **_: object) -> None:
            pass

        async def list_agents(self) -> dict[str, object]:
            raise httpx.ConnectError("connection failed")

    monkeypatch.setenv("OMNIGENT_ENABLED", "true")
    monkeypatch.setenv("OMNIGENT_SERVER_URL", "https://omnigent.test")
    monkeypatch.setattr("moonmind.omnigent.execute.OmnigentHttpClient", FakeClient)

    result = await run_omnigent_execution(
        AgentExecutionRequest(
            agentKind="external",
            agentId="omnigent",
            correlationId="corr-1",
            idempotencyKey="idem-1",
            parameters={
                "omnigent": {
                    "agent": {"agentName": "codex-native-ui"},
                    "prompt": {"text": "Do the task"},
                },
            },
        )
    )

    assert result.failure_class == "integration_error"
    assert result.provider_error_code == "omnigent_http_error"
    assert result.metadata["normalizedStatus"] == "failed"


@pytest.mark.asyncio
async def test_run_omnigent_execution_uses_nested_session_parameters(
    monkeypatch,
) -> None:
    captured_session_payloads: list[dict[str, object]] = []

    class FakeClient:
        def __init__(self, **_: object) -> None:
            pass

        async def list_agents(self) -> dict[str, object]:
            raise AssertionError("agentId should avoid list_agents lookup")

        async def create_session(self, payload: dict[str, object]) -> dict[str, object]:
            captured_session_payloads.append(payload)
            return {"id": "session-1"}

        async def post_event(
            self,
            session_id: str,
            payload: dict[str, object],
        ) -> dict[str, object]:
            return {}

        async def stream_events(self, session_id: str):
            yield {"type": "response.completed"}

        async def get_session(self, session_id: str) -> dict[str, object]:
            return {
                "status": "completed",
                "summary": "done",
                "hostType": "external",
                "workspace": "/workspace/repo",
            }

    monkeypatch.setenv("OMNIGENT_ENABLED", "true")
    monkeypatch.setenv("OMNIGENT_SERVER_URL", "https://omnigent.test")
    monkeypatch.setattr("moonmind.omnigent.execute.OmnigentHttpClient", FakeClient)

    result = await run_omnigent_execution(
        AgentExecutionRequest(
            agentKind="external",
            agentId="omnigent",
            correlationId="corr-1",
            idempotencyKey="idem-1",
            parameters={
                "omnigent": {
                    "agent": {"agentId": "agent-1"},
                    "session": {
                        "hostType": "external",
                        "hostId": "host-1",
                        "workspace": "/workspace/repo",
                        "modelOverride": "codex-special",
                        "reasoningEffort": "high",
                    },
                },
            },
        )
    )

    assert captured_session_payloads == [
        {
            "agent_id": "agent-1",
            "title": "MoonMind Agent Task",
            "idempotency_key": "idem-1",
            "labels": {
                "moonmind.correlation_id": "corr-1",
                "moonmind.idempotency_key": "idem-1",
                "moonmind.issue": "MM-991",
            },
            "host_type": "external",
            "workspace": "/workspace/repo",
            "host_id": "host-1",
            "model_override": "codex-special",
            "reasoning_effort": "high",
            "terminal_launch_args": [],
        }
    ]
    assert result.metadata["hostType"] == "external"
    assert result.metadata["workspace"] == "/workspace/repo"


@pytest.mark.asyncio
async def test_run_omnigent_execution_derives_managed_workspace_from_workspace_spec(
    monkeypatch,
) -> None:
    captured_session_payloads: list[dict[str, object]] = []

    class FakeClient:
        def __init__(self, **_: object) -> None:
            pass

        async def list_agents(self) -> dict[str, object]:
            raise AssertionError("agentId should avoid list_agents lookup")

        async def create_session(self, payload: dict[str, object]) -> dict[str, object]:
            captured_session_payloads.append(payload)
            return {"id": "session-1"}

        async def post_event(
            self,
            session_id: str,
            payload: dict[str, object],
        ) -> dict[str, object]:
            return {}

        async def stream_events(self, session_id: str):
            yield {"type": "response.completed"}

        async def get_session(self, session_id: str) -> dict[str, object]:
            return {"status": "completed", "summary": "done"}

    monkeypatch.setenv("OMNIGENT_ENABLED", "true")
    monkeypatch.setenv("OMNIGENT_SERVER_URL", "https://omnigent.test")
    monkeypatch.setattr("moonmind.omnigent.execute.OmnigentHttpClient", FakeClient)

    result = await run_omnigent_execution(
        AgentExecutionRequest(
            agentKind="external",
            agentId="omnigent",
            correlationId="corr-1",
            idempotencyKey="idem-1",
            workspaceSpec={
                "repository": "https://github.com/org/repo",
                "branch": "feature-branch",
            },
            parameters={
                "omnigent": {
                    "agent": {"agentId": "agent-1"},
                    "session": {"hostType": "managed"},
                },
            },
        )
    )

    assert result.failure_class is None
    assert captured_session_payloads[0]["workspace"] == (
        "https://github.com/org/repo#feature-branch"
    )


@pytest.mark.asyncio
async def test_run_omnigent_execution_deletes_session_after_transport_error(
    monkeypatch,
) -> None:
    calls: list[tuple[str, object]] = []

    class FakeClient:
        def __init__(self, **_: object) -> None:
            pass

        async def list_agents(self) -> dict[str, object]:
            return {"items": [{"id": "agent-1", "name": "codex-native-ui"}]}

        async def create_session(self, payload: dict[str, object]) -> dict[str, object]:
            calls.append(("create_session", payload))
            return {"id": "session-1"}

        async def post_event(
            self,
            session_id: str,
            payload: dict[str, object],
        ) -> dict[str, object]:
            raise httpx.ConnectError("provider write failed")

        async def stream_events(self, session_id: str):
            if False:
                yield {}

        async def delete_session(
            self,
            session_id: str,
            *,
            delete_branch: bool = False,
        ) -> dict[str, object]:
            calls.append(
                (
                    "delete_session",
                    {"session_id": session_id, "delete_branch": delete_branch},
                )
            )
            return {}

    monkeypatch.setenv("OMNIGENT_ENABLED", "true")
    monkeypatch.setenv("OMNIGENT_SERVER_URL", "https://omnigent.test")
    monkeypatch.setattr("moonmind.omnigent.execute.OmnigentHttpClient", FakeClient)

    result = await run_omnigent_execution(
        AgentExecutionRequest(
            agentKind="external",
            agentId="omnigent",
            correlationId="corr-1",
            idempotencyKey="idem-1",
            parameters={
                "omnigent": {
                    "agent": {"agentName": "codex-native-ui"},
                    "prompt": {"text": "Do the task"},
                },
            },
        )
    )

    assert result.failure_class == "integration_error"
    assert (
        "delete_session",
        {"session_id": "session-1", "delete_branch": False},
    ) in calls


@pytest.mark.asyncio
async def test_run_omnigent_execution_interrupts_and_stops_on_cancellation(
    monkeypatch,
) -> None:
    calls: list[tuple[str, object]] = []

    class FakeClient:
        def __init__(self, **_: object) -> None:
            pass

        async def list_agents(self) -> dict[str, object]:
            return {"items": [{"id": "agent-1", "name": "codex-native-ui"}]}

        async def create_session(self, payload: dict[str, object]) -> dict[str, object]:
            calls.append(("create_session", payload))
            return {"id": "session-1"}

        async def post_event(
            self,
            session_id: str,
            payload: dict[str, object],
        ) -> dict[str, object]:
            calls.append(("post_event", payload))
            return {}

        async def stream_events(self, session_id: str):
            if False:
                yield {}

        async def get_session(self, session_id: str) -> dict[str, object]:
            calls.append(("get_session", session_id))
            return {"status": "running"}

        async def interrupt(self, session_id: str) -> dict[str, object]:
            calls.append(("interrupt", session_id))
            return {}

        async def stop_session(self, session_id: str) -> dict[str, object]:
            calls.append(("stop_session", session_id))
            return {}

    async def cancel_immediately(_delay: float) -> None:
        raise asyncio.CancelledError()

    monkeypatch.setenv("OMNIGENT_ENABLED", "true")
    monkeypatch.setenv("OMNIGENT_SERVER_URL", "https://omnigent.test")
    monkeypatch.setattr("moonmind.omnigent.execute.OmnigentHttpClient", FakeClient)
    monkeypatch.setattr("moonmind.omnigent.execute.asyncio.sleep", cancel_immediately)

    with pytest.raises(asyncio.CancelledError):
        await run_omnigent_execution(
            AgentExecutionRequest(
                agentKind="external",
                agentId="omnigent",
                correlationId="corr-1",
                idempotencyKey="idem-1",
                parameters={
                    "omnigent": {
                        "agent": {"agentName": "codex-native-ui"},
                        "prompt": {"text": "Do the task"},
                    },
                },
            )
        )

    assert ("interrupt", "session-1") in calls
    assert ("stop_session", "session-1") in calls


@pytest.mark.asyncio
async def test_run_omnigent_execution_posts_instruction_ref_when_prompt_text_is_absent(
    monkeypatch,
) -> None:
    posted_events: list[dict[str, Any]] = []

    class FakeClient:
        def __init__(self, **_: object) -> None:
            pass

        async def list_agents(self) -> dict[str, object]:
            return {"items": [{"id": "agent-1", "name": "codex-native-ui"}]}

        async def create_session(self, payload: dict[str, object]) -> dict[str, object]:
            return {"id": "session-1"}

        async def post_event(
            self,
            session_id: str,
            payload: dict[str, object],
        ) -> dict[str, object]:
            assert session_id == "session-1"
            posted_events.append(payload)
            return {}

        async def stream_events(self, session_id: str):
            assert session_id == "session-1"
            yield {"type": "response.completed"}

        async def get_session(self, session_id: str) -> dict[str, object]:
            assert session_id == "session-1"
            return {"status": "completed", "summary": "done"}

    monkeypatch.setenv("OMNIGENT_ENABLED", "true")
    monkeypatch.setenv("OMNIGENT_SERVER_URL", "https://omnigent.test")
    monkeypatch.setattr("moonmind.omnigent.execute.OmnigentHttpClient", FakeClient)

    result = await run_omnigent_execution(
        AgentExecutionRequest(
            agentKind="external",
            agentId="omnigent",
            correlationId="corr-1",
            idempotencyKey="idem-1",
            parameters={
                "omnigent": {
                    "agent": {"agentName": "codex-native-ui"},
                    "prompt": {"instructionRef": "artifact://instruction"},
                },
            },
        )
    )

    assert result.failure_class is None
    assert result.summary == "done"
    text = posted_events[0]["data"]["content"][0]["text"]
    assert text == "artifact://instruction"


@pytest.mark.asyncio
async def test_run_omnigent_execution_raises_when_stream_ends_still_running(
    monkeypatch,
) -> None:
    class FakeClient:
        def __init__(self, **_: object) -> None:
            pass

        async def list_agents(self) -> dict[str, object]:
            return {"items": [{"id": "agent-1", "name": "codex-native-ui"}]}

        async def create_session(self, payload: dict[str, object]) -> dict[str, object]:
            return {"id": "session-1"}

        async def post_event(
            self,
            session_id: str,
            payload: dict[str, object],
        ) -> dict[str, object]:
            return {}

        async def stream_events(self, session_id: str):
            assert session_id == "session-1"
            if False:
                yield {}

        async def get_session(self, session_id: str) -> dict[str, object]:
            assert session_id == "session-1"
            return {"status": "running"}

    monkeypatch.setenv("OMNIGENT_ENABLED", "true")
    monkeypatch.setenv("OMNIGENT_SERVER_URL", "https://omnigent.test")
    monkeypatch.setattr("moonmind.omnigent.execute.OmnigentHttpClient", FakeClient)

    with pytest.raises(OmnigentSessionStillRunningError):
        await run_omnigent_execution(
            AgentExecutionRequest(
                agentKind="external",
                agentId="omnigent",
                correlationId="corr-1",
                idempotencyKey="idem-1",
                parameters={
                    "omnigent": {
                        "agent": {"agentName": "codex-native-ui"},
                        "prompt": {"text": "Do the task"},
                    },
                },
            )
        )


@pytest.mark.asyncio
async def test_run_omnigent_execution_reuses_heartbeat_session_on_retry(
    monkeypatch,
) -> None:
    calls: list[str] = []

    class FakeClient:
        def __init__(self, **_: object) -> None:
            pass

        async def list_agents(self) -> dict[str, object]:
            return {"items": [{"id": "agent-1", "name": "codex-native-ui"}]}

        async def create_session(self, payload: dict[str, object]) -> dict[str, object]:
            calls.append("create_session")
            return {"id": "new-session"}

        async def post_event(
            self,
            session_id: str,
            payload: dict[str, object],
        ) -> dict[str, object]:
            calls.append("post_event")
            return {}

        async def stream_events(self, session_id: str):
            assert session_id == "existing-session"
            yield {"type": "response.completed"}

        async def get_session(self, session_id: str) -> dict[str, object]:
            return {"status": "completed", "summary": "reattached"}

    monkeypatch.setenv("OMNIGENT_ENABLED", "true")
    monkeypatch.setenv("OMNIGENT_SERVER_URL", "https://omnigent.test")
    monkeypatch.setattr("moonmind.omnigent.execute.OmnigentHttpClient", FakeClient)
    monkeypatch.setattr(
        "moonmind.omnigent.execute._heartbeat_state",
        lambda: {
            "omnigentSessionId": "existing-session",
            "firstMessagePosted": True,
        },
    )

    result = await run_omnigent_execution(
        AgentExecutionRequest(
            agentKind="external",
            agentId="omnigent",
            correlationId="corr-1",
            idempotencyKey="idem-1",
            parameters={
                "omnigent": {
                    "agent": {"agentName": "codex-native-ui"},
                    "prompt": {"text": "continue"},
                },
            },
        )
    )

    assert result.summary == "reattached"
    assert calls == []
