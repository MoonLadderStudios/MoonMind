"""MM-995 hermetic fake-server coverage for Omnigent streaming execute."""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest

from moonmind.omnigent.execute import InMemoryOmnigentRunStore, run_omnigent_execution
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.workflows.adapters.omnigent_client import OmnigentHttpClient

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]


def _request(**overrides: object) -> AgentExecutionRequest:
    payload = {
        "agentKind": "external",
        "agentId": "omnigent",
        "correlationId": "corr-mm-995",
        "idempotencyKey": "idem-mm-995",
        "workspaceSpec": {
            "repository": "https://github.com/MoonLadderStudios/MoonMind.git",
            "branch": "mm-995",
        },
        "parameters": {
            "title": "MM-995 fake server",
            "omnigent": {
                "agent": {"agentName": "codex-native-ui"},
                "session": {"hostType": "managed"},
                "prompt": {"text": "run fake server proof"},
                "autoApproveElicitations": True,
            },
        },
    }
    payload.update(overrides)
    return AgentExecutionRequest(**payload)


@pytest.mark.parametrize(
    ("scenario", "expected_failure"),
    [
        ("success", None),
        ("managed_launch_delay", None),
        ("stream_disconnect", None),
        ("failed", "execution_error"),
    ],
)
async def test_fake_server_execute_terminal_scenarios(
    scenario: str,
    expected_failure: str | None,
) -> None:
    server = FakeOmnigentServer(scenario=scenario)
    client = OmnigentHttpClient(
        base_url="https://omnigent.fake",
        transport=httpx.MockTransport(server.handle),
    )

    result = await run_omnigent_execution(
        _request(),
        env={"OMNIGENT_ENABLED": "1", "OMNIGENT_SERVER_URL": "https://omnigent.fake"},
        client=client,
        run_store=InMemoryOmnigentRunStore(),
    )

    assert result.failure_class == expected_failure
    assert result.metadata["providerName"] == "omnigent"
    assert result.metadata["jiraIssue"] == "MM-995"
    assert result.metadata["sourceIssue"] == "MM-981"
    assert server.created_sessions[0]["workspace"].endswith("#mm-995")
    assert "host_id" not in server.created_sessions[0]
    assert server.posted_messages == 1
    assert any(ref.endswith("/workspace/app.py") for ref in result.output_refs)
    assert any(ref.endswith("/files/file_1") for ref in result.output_refs)
    if scenario == "stream_disconnect":
        assert result.metadata["streamDisconnected"] is True


async def test_fake_server_external_workspace_and_elicitation_child_harvest() -> None:
    server = FakeOmnigentServer(scenario="elicitation_child")
    client = OmnigentHttpClient(
        base_url="https://omnigent.fake",
        transport=httpx.MockTransport(server.handle),
    )
    request = _request(
        workspaceSpec={},
        parameters={
            "title": "MM-995 external fake server",
            "omnigent": {
                "agent": {"agentId": "ag_codex"},
                "session": {
                    "hostType": "external",
                    "hostId": "host_1",
                    "workspace": "/workspace/repo",
                },
                "prompt": {"text": "approve elicitation"},
                "autoApproveElicitations": True,
            },
        },
    )

    result = await run_omnigent_execution(
        request,
        env={"OMNIGENT_ENABLED": "1", "OMNIGENT_SERVER_URL": "https://omnigent.fake"},
        client=client,
        run_store=InMemoryOmnigentRunStore(),
    )

    assert server.created_sessions[0]["host_id"] == "host_1"
    assert server.created_sessions[0]["workspace"] == "/workspace/repo"
    assert server.resolved_elicitations == ["el_1"]
    assert result.metadata["childSessions"] == ["sess_child"]
    assert result.diagnostics_ref.endswith("/patch-unavailable")


async def test_fake_server_retry_after_session_create_reuses_session() -> None:
    server = FakeOmnigentServer(scenario="success")
    client = OmnigentHttpClient(
        base_url="https://omnigent.fake",
        transport=httpx.MockTransport(server.handle),
    )
    store = InMemoryOmnigentRunStore()
    request = _request()

    first = await run_omnigent_execution(
        request,
        env={"OMNIGENT_ENABLED": "1", "OMNIGENT_SERVER_URL": "https://omnigent.fake"},
        client=client,
        run_store=store,
    )
    second = await run_omnigent_execution(
        request,
        env={"OMNIGENT_ENABLED": "1", "OMNIGENT_SERVER_URL": "https://omnigent.fake"},
        client=client,
        run_store=store,
    )

    assert first.summary == "fake server completed"
    assert second.summary == "fake server completed"
    assert len(server.created_sessions) == 1
    assert server.posted_messages == 1


async def test_fake_server_managed_workspace_validation_stops_before_transport(
) -> None:
    server = FakeOmnigentServer(scenario="success")
    client = OmnigentHttpClient(
        base_url="https://omnigent.fake",
        transport=httpx.MockTransport(server.handle),
    )
    request = _request(
        parameters={
            "omnigent": {
                "agent": {"agentId": "ag_codex"},
                "session": {"hostType": "managed", "workspace": "/host/path"},
            }
        }
    )

    with pytest.raises(ValueError, match="git repository URL"):
        await run_omnigent_execution(
            request,
            env={
                "OMNIGENT_ENABLED": "1",
                "OMNIGENT_SERVER_URL": "https://omnigent.fake",
            },
            client=client,
            run_store=InMemoryOmnigentRunStore(),
        )

    assert server.created_sessions == []


@dataclass
class FakeOmnigentServer:
    scenario: str
    created_sessions: list[dict[str, Any]] = field(default_factory=list)
    posted_messages: int = 0
    resolved_elicitations: list[str] = field(default_factory=list)
    session_id: str = "sess_1"
    prompt_digest: str | None = None

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "GET" and path == "/api/agents":
            return httpx.Response(
                200,
                json={"agents": [{"id": "ag_codex", "name": "codex-native-ui"}]},
            )
        if request.method == "POST" and path == "/v1/sessions":
            payload = _json_body(request)
            self.created_sessions.append(payload)
            return httpx.Response(200, json={"id": self.session_id})
        if request.method == "POST" and path.endswith("/events"):
            payload = _json_body(request)
            if payload.get("type") == "message":
                self.posted_messages += 1
                metadata = payload.get("metadata") or {}
                self.prompt_digest = metadata.get("moonmindPromptDigest")
                return httpx.Response(200, json={"pending_id": "pending_1"})
            return httpx.Response(200, json={"ok": True})
        if request.method == "GET" and path == f"/v1/sessions/{self.session_id}/stream":
            if self.scenario == "stream_disconnect":
                return httpx.Response(503, json={"error": "disconnect"})
            return httpx.Response(
                200,
                content="\n".join(self._stream_lines()).encode("utf-8"),
                headers={"content-type": "text/event-stream"},
            )
        if request.method == "GET" and path == f"/v1/sessions/{self.session_id}":
            status = "failed" if self.scenario == "failed" else "completed"
            return httpx.Response(
                200,
                json={
                    "id": self.session_id,
                    "status": status,
                    "summary": (
                        "fake server failed"
                        if self.scenario == "failed"
                        else "fake server completed"
                    ),
                    "items": [
                        {"metadata": {"moonmindPromptDigest": self.prompt_digest}}
                    ],
                },
            )
        if request.method == "GET" and path.endswith(
            "/resources/environments/default/changes"
        ):
            return httpx.Response(200, json={"changes": [{"path": "app.py"}]})
        if (
            request.method == "GET"
            and "/resources/environments/default/filesystem/" in path
        ):
            return httpx.Response(200, content=b"print('ok')\n")
        if request.method == "GET" and path.endswith("/resources/files"):
            return httpx.Response(200, json={"files": [{"id": "file_1"}]})
        if request.method == "GET" and path.endswith("/resources/files/file_1/content"):
            return httpx.Response(200, content=b"artifact")
        if request.method == "POST" and "/elicitations/el_1/resolve" in path:
            self.resolved_elicitations.append("el_1")
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(404, json={"error": path})

    def _stream_lines(self) -> Iterator[str]:
        if self.scenario == "managed_launch_delay":
            yield _sse({"type": "status", "status": "launching"})
            yield _sse({"type": "status", "status": "running"})
        if self.scenario == "elicitation_child":
            yield _sse({"type": "elicitation_request", "id": "el_1"})
            yield _sse({"type": "child_session_created", "session_id": "sess_child"})
        if self.scenario == "failed":
            yield _sse({"type": "failed", "status": "failed"})
        else:
            yield _sse({"type": "completed", "status": "completed"})


def _json_body(request: httpx.Request) -> dict[str, Any]:
    return json.loads(request.content.decode("utf-8") or "{}")


def _sse(payload: dict[str, Any]) -> str:
    return "data: " + json.dumps(payload)
