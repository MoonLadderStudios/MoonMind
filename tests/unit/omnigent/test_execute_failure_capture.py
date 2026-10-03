"""Preserve provider work when an execution's SSE transport fails."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from moonmind.omnigent.bridge_artifacts import LocalOmnigentArtifactGateway
from moonmind.omnigent.execute import run_omnigent_execution
from moonmind.omnigent.transport import OmnigentTransportPool
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "pooled,resources_available",
    [(False, True), (True, True), (False, False)],
    ids=["owned", "pooled", "capture-unavailable"],
)
async def test_stream_failure_captures_work_without_repeating_provider_effects(
    monkeypatch, tmp_path, pooled, resources_available
) -> None:
    posted = asyncio.Event()
    requests: list[tuple[str, str]] = []
    clients: list[httpx.AsyncClient] = []
    session_path = "/v1/sessions/session-1"
    resources_path = f"{session_path}/resources"
    workspace_path = f"{resources_path}/environments/default"
    saved_content = b"saved work from the accepted turn\n"
    saved_patch = b"diff --git a/work.txt b/work.txt\n+saved work\n"

    class InterruptedStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            # Observation is reserved before dispatch, so wait until the
            # provider confirms that this execution's one message was accepted.
            await posted.wait()
            raise httpx.ReadError("stream disconnected after accepted work")
            if False:
                yield b""

    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        requests.append((request.method, path))
        assert request.url.host == "omnigent.test"
        assert request.headers["Authorization"] == "Bearer test-capture-token"
        if request.method == "POST" and path == "/v1/sessions":
            return httpx.Response(200, json={"id": "session-1"})
        if request.method == "POST" and path == f"{session_path}/events":
            assert json.loads(request.content)["type"] == "message"
            posted.set()
            return httpx.Response(200, json={"pending_id": "pending-1"})
        if request.method != "GET":
            raise AssertionError(f"unexpected provider effect: {request.method} {path}")
        if path == session_path:
            return httpx.Response(200, json={"status": "idle", "items": []})
        if path == f"{session_path}/stream":
            return httpx.Response(
                200,
                headers={"Content-Type": "text/event-stream"},
                stream=InterruptedStream(),
            )
        if path.startswith(resources_path) and not resources_available:
            return httpx.Response(503, json={"error": "resources unavailable"})
        if path == f"{workspace_path}/changes":
            return httpx.Response(200, json={"items": [{"path": "work.txt"}]})
        if path == f"{workspace_path}/filesystem":
            return httpx.Response(
                200, json={"items": [{"path": "work.txt", "type": "file"}]}
            )
        if path == f"{workspace_path}/filesystem/work.txt":
            return httpx.Response(200, content=saved_content)
        if path == f"{workspace_path}/diff/work.txt":
            return httpx.Response(200, content=saved_patch)
        if path == f"{resources_path}/files":
            return httpx.Response(
                200, json={"items": [{"id": "file-1", "filename": "session.log"}]}
            )
        if path == f"{resources_path}/files/file-1/content":
            return httpx.Response(200, content=b"session evidence\n")
        raise AssertionError(f"unexpected provider read: {path}")

    original_client = httpx.AsyncClient

    def create_client(**kwargs):
        client = original_client(transport=httpx.MockTransport(handle), **kwargs)
        clients.append(client)
        return client

    monkeypatch.setenv("OMNIGENT_ENABLED", "true")
    monkeypatch.setenv("OMNIGENT_SERVER_URL", "https://omnigent.test")
    monkeypatch.setenv("OMNIGENT_API_TOKEN", "test-capture-token")
    monkeypatch.setattr("moonmind.omnigent.transport.httpx.AsyncClient", create_client)
    pool = OmnigentTransportPool() if pooled else None
    gateway = LocalOmnigentArtifactGateway(root=tmp_path)
    try:
        result = await asyncio.wait_for(
            run_omnigent_execution(
                AgentExecutionRequest(
                    agentKind="external",
                    agentId="omnigent",
                    correlationId="capture-corr",
                    idempotencyKey="capture-idem",
                    parameters={
                        "omnigent": {
                            "agent": {"agentId": "agent-1"},
                            "session": {"allowEmptyWorkspace": True},
                            "prompt": {"text": "Do the task"},
                        }
                    },
                ),
                artifact_gateway=gateway,
                transport_pool=pool,
            ),
            timeout=5,
        )

        assert result.failure_class == "integration_error"
        assert result.provider_error_code == "omnigent_http_error"
        assert result.retry_recommendation is None
        assert "stream disconnected after accepted work" in result.summary
        diagnostics = json.loads(await gateway.read_text(result.diagnostics_ref))
        assert (
            "stream disconnected after accepted work"
            in diagnostics["diagnostics"]["message"]
        )
        manifest = json.loads(
            await gateway.read_text(result.metadata["captureManifestRef"])
        )
        assert manifest["terminalStatus"] == "failed"
        assert manifest["omnigentSessionId"] == "session-1"
        assert manifest["patchUnavailable"] is (not resources_available), manifest
        if resources_available:
            assert manifest["evidenceCompleteness"]["status"] == "complete"
            assert (
                await gateway.read_bytes(manifest["changedFiles"][0]["artifactRef"])
                == saved_content
            )
            assert (
                await gateway.read_bytes(manifest["workspaceFiles"][0]["artifactRef"])
                == saved_content
            )
            assert (
                await gateway.read_bytes(manifest["workspaceDiffs"][0]["artifactRef"])
                == saved_patch
            )
            assert (
                await gateway.read_bytes(manifest["sessionFiles"][0]["artifactRef"])
                == b"session evidence\n"
            )
        else:
            assert manifest["evidenceCompleteness"]["status"] == "degraded"
            for key in (
                "changedFilesUnavailable",
                "workspaceFilesUnavailable",
                "sessionFilesUnavailable",
            ):
                assert "503" in manifest[key]
                assert "closed" not in manifest[key]
        external_state = json.loads(
            await gateway.read_text(result.metadata["externalStateRef"])
        )
        assert external_state["firstMessage"]["posted"] is True
        assert external_state["patchEvidence"]["patchUnavailable"] is (
            not resources_available
        )
        assert [item for item in requests if item[0] != "GET"] == [
            ("POST", "/v1/sessions"),
            ("POST", f"{session_path}/events"),
        ]
        if pooled:
            assert len(clients) == 1
            assert not clients[0].is_closed
        else:
            assert all(client.is_closed for client in clients)
    finally:
        if pool is not None:
            await pool.aclose()
