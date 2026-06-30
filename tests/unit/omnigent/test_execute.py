"""Unit tests for MM-1030 Omnigent terminal execution normalization."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

from moonmind.omnigent.execute import (
    LocalOmnigentArtifactGateway,
    OmnigentArtifactError,
    OmnigentContractError,
    OmnigentCaptureBundle,
    OmnigentSessionStillRunningError,
    _agent_items,
    _resolve_agent_id,
    build_omnigent_result,
    normalize_omnigent_observation,
    run_omnigent_execution,
)
from moonmind.omnigent.store import OmnigentDigestMismatchError
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest


def _request() -> AgentExecutionRequest:
    return AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        executionProfileRef="profile:test",
        correlationId="corr-1",
        idempotencyKey="idem-1",
    )


def _bundle(**overrides: Any) -> OmnigentCaptureBundle:
    payload = {
        "output_refs": ["artifact://omnigent/corr-1/output.omnigent.snapshot.final.json"],
        "diagnostics_ref": "artifact://omnigent/corr-1/diagnostics.omnigent.json",
        "capture_manifest_ref": "artifact://omnigent/corr-1/output.omnigent.capture_manifest.json",
        "metadata_refs": {
            "captureManifestRef": (
                "artifact://omnigent/corr-1/output.omnigent.capture_manifest.json"
            )
        },
    }
    payload.update(overrides)
    return OmnigentCaptureBundle(**payload)


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
        capture_bundle=_bundle(
            output_refs=["artifact://transcript", "artifact://snapshot"],
            diagnostics_ref="artifact://diagnostics",
            capture_manifest_ref="artifact://capture",
            metadata_refs={"captureManifestRef": "artifact://capture"},
        ),
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
        capture_bundle=_bundle(),
    )

    assert result.metadata["hostType"] == "external"
    assert result.metadata["captureManifestRef"].startswith("artifact://omnigent/")
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
        capture_bundle=_bundle(),
        provider_error_code="omnigent_failed",
    )

    assert result.failure_class == "execution_error"
    assert result.provider_error_code == "omnigent_failed"
    assert all(not ref.startswith("omnigent://") for ref in result.output_refs)
    assert not result.diagnostics_ref.startswith("omnigent://")
    assert result.metadata["normalizedStatus"] == "failed"


def test_build_omnigent_result_uses_valid_failure_class_for_timeout() -> None:
    result = build_omnigent_result(
        request=_request(),
        terminal_status="timed_out",
        session_id="sess-1",
        agent_id=None,
        final_snapshot={"summary": "timed out"},
        event_count=1,
        capture_bundle=_bundle(),
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
            assert payload["labels"]["moonmind.issue"] == "MM-1059"
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
                    "session": {"allowEmptyWorkspace": True},
                    "prompt": {"text": "Do the task"},
                },
            },
        )
    )

    assert result.summary == "done"
    assert result.output_refs
    assert all(ref.startswith("artifact://omnigent/") for ref in result.output_refs)
    assert result.diagnostics_ref.startswith("artifact://omnigent/")
    assert result.metadata["normalizedStatus"] == "completed"
    assert created_clients
    assert heartbeats
    assert all("normalizedStatus" in heartbeat for heartbeat in heartbeats)
    assert all("eventsCaptured" in heartbeat for heartbeat in heartbeats)
    event_types = [
        heartbeat.get("eventType")
        for heartbeat in heartbeats
        if "eventType" in heartbeat
    ]
    assert event_types == ["", "response.completed"]


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
                    "session": {"allowEmptyWorkspace": True},
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
                "moonmind.issue": "MM-1059",
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
async def test_run_omnigent_execution_preserves_session_after_transport_error(
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
                    "session": {"allowEmptyWorkspace": True},
                    "prompt": {"text": "Do the task"},
                },
            },
        )
    )

    assert result.failure_class == "integration_error"
    assert result.diagnostics_ref.startswith("artifact://omnigent/")
    assert all(call[0] != "delete_session" for call in calls)


@pytest.mark.asyncio
async def test_run_omnigent_execution_harvests_before_delete_on_cancellation(
    monkeypatch,
    tmp_path,
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

        async def list_changed_files(self, session_id: str) -> dict[str, object]:
            calls.append(("list_changed_files", session_id))
            return {"items": [{"path": "src/app.py"}]}

        async def list_workspace_files(self, session_id: str) -> dict[str, object]:
            calls.append(("list_workspace_files", session_id))
            return {"items": [{"path": "src/app.py", "type": "file"}]}

        async def get_workspace_file(self, session_id: str, path: str) -> bytes:
            calls.append(("get_workspace_file", path))
            return b"print('cancelled')\n"

        async def get_workspace_diff(self, session_id: str, path: str) -> bytes:
            calls.append(("get_workspace_diff", path))
            return b"diff --git a/src/app.py b/src/app.py\n"

        async def list_session_files(self, session_id: str) -> dict[str, object]:
            calls.append(("list_session_files", session_id))
            return {"items": []}

        async def delete_session(
            self,
            session_id: str,
            *,
            delete_branch: bool = False,
        ) -> dict[str, object]:
            calls.append(("delete_session", session_id))
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
                        "session": {"allowEmptyWorkspace": True},
                        "prompt": {"text": "Do the task"},
                        "capture": {"deleteOmnigentSessionAfterHarvest": True},
                    },
                },
            ),
            artifact_gateway=LocalOmnigentArtifactGateway(root=tmp_path),
        )

    assert ("interrupt", "session-1") in calls
    assert ("stop_session", "session-1") in calls
    assert ("list_changed_files", "session-1") in calls
    assert ("delete_session", "session-1") in calls
    assert calls.index(("list_changed_files", "session-1")) < calls.index(
        ("delete_session", "session-1")
    )
    manifest_path = tmp_path / "corr-1" / "output.omnigent.capture_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["terminalStatus"] == "canceled"
    assert manifest["patchUnavailable"] is False


@pytest.mark.asyncio
async def test_run_omnigent_execution_dereferences_instruction_ref_when_prompt_text_is_absent(
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
                    "session": {"allowEmptyWorkspace": True},
                    "prompt": {"instructionRef": "artifact://instruction"},
                },
            },
        ),
        artifact_gateway=LocalOmnigentArtifactGateway(
            readable_refs={"artifact://instruction": "Dereferenced instruction body"}
        ),
    )

    assert result.failure_class is None
    assert result.summary == "done"
    text = posted_events[0]["data"]["content"][0]["text"]
    assert "Dereferenced instruction body" in text
    assert "artifact://instruction" not in text


@pytest.mark.asyncio
async def test_run_omnigent_execution_preserves_inline_instruction_ref(
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
            posted_events.append(payload)
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
            instructionRef="Implement the requested change",
            parameters={
                "omnigent": {
                    "agent": {"agentName": "codex-native-ui"},
                    "session": {"allowEmptyWorkspace": True},
                    "prompt": {},
                },
            },
        ),
        artifact_gateway=LocalOmnigentArtifactGateway(),
    )

    assert result.failure_class is None
    text = posted_events[0]["data"]["content"][0]["text"]
    assert "Implement the requested change" in text


@pytest.mark.asyncio
async def test_local_omnigent_artifact_gateway_rejects_traversal_refs(tmp_path) -> None:
    gateway = LocalOmnigentArtifactGateway(root=tmp_path)

    with pytest.raises(OmnigentArtifactError, match="escapes artifact root"):
        await gateway.read_text("artifact://omnigent/../../secret.txt")

    ref = await gateway.write_bytes(
        request=_request(),
        name="../session.log",
        payload=b"evidence",
        link_type="output.omnigent.session_file",
    )

    assert ref == "artifact://omnigent/corr-1/segment/session.log"
    assert (tmp_path / "corr-1" / "segment" / "session.log").read_bytes() == b"evidence"
    assert not (tmp_path.parent / "session.log").exists()


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
                        "session": {"allowEmptyWorkspace": True},
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
                    "session": {"allowEmptyWorkspace": True},
                    "prompt": {"text": "continue"},
                },
            },
        )
    )

    assert result.summary == "reattached"
    assert calls == []


@pytest.mark.asyncio
async def test_run_omnigent_execution_reuses_persisted_session_on_retry(
    monkeypatch,
) -> None:
    calls: list[str] = []

    class Row:
        omnigent_session_id = "persisted-session"
        first_message_state = "posted"

    class Store:
        async def get_or_create(self, **_: object) -> Row:
            return Row()

        async def mark_prepared(self, *_: object, **__: object) -> Row:
            return Row()

        async def mark_terminal(self, *_: object, **__: object) -> Row:
            return Row()

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
            assert session_id == "persisted-session"
            yield {"type": "response.completed"}

        async def get_session(self, session_id: str) -> dict[str, object]:
            return {"status": "completed", "summary": "durably reattached"}

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
                    "session": {"allowEmptyWorkspace": True},
                    "prompt": {"text": "continue"},
                },
            },
        ),
        run_store=Store(),
    )

    assert result.summary == "durably reattached"
    assert calls == []


@pytest.mark.asyncio
async def test_run_omnigent_execution_reconciles_posting_state_without_duplicate_prompt(
    monkeypatch,
) -> None:
    calls: list[str] = []
    marker: dict[str, str] = {}

    class Row:
        omnigent_session_id = "persisted-session"
        first_message_state = "posting"

    class PostedRow:
        omnigent_session_id = "persisted-session"
        first_message_state = "posted"

    class Store:
        async def get_or_create(self, **_: object) -> Row:
            return Row()

        async def mark_prepared(self, *_: object, **__: object) -> Row:
            marker["value"] = str(__["marker"])
            return Row()

        async def mark_posted(self, *_: object, **__: object) -> PostedRow:
            calls.append("mark_posted")
            return PostedRow()

        async def mark_terminal(self, *_: object, **__: object) -> PostedRow:
            return PostedRow()

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
            assert session_id == "persisted-session"
            yield {"type": "response.completed"}

        async def get_session(self, session_id: str) -> dict[str, object]:
            if session_id == "persisted-session":
                return {
                    "status": "completed",
                    "summary": "reconciled",
                    "events": [{"text": marker.get("value", "")}],
                }
            return {"status": "completed", "summary": "child"}

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
                    "session": {"allowEmptyWorkspace": True},
                    "prompt": {"text": "continue"},
                },
            },
        ),
        run_store=Store(),
    )

    assert result.summary == "reconciled"
    assert "mark_posted" in calls
    assert "create_session" not in calls
    assert "post_event" not in calls


@pytest.mark.asyncio
async def test_run_omnigent_execution_fails_closed_when_posting_state_cannot_reconcile(
    monkeypatch,
) -> None:
    calls: list[str] = []

    class Row:
        omnigent_session_id = "persisted-session"
        first_message_state = "posting"

    class Store:
        async def get_or_create(self, **_: object) -> Row:
            return Row()

        async def mark_prepared(self, *_: object, **__: object) -> Row:
            return Row()

        async def mark_posted(self, *_: object, **__: object) -> Row:
            calls.append("mark_posted")
            return Row()

    class FakeClient:
        def __init__(self, **_: object) -> None:
            pass

        async def list_agents(self) -> dict[str, object]:
            return {"items": [{"id": "agent-1", "name": "codex-native-ui"}]}

        async def post_event(
            self,
            session_id: str,
            payload: dict[str, object],
        ) -> dict[str, object]:
            calls.append("post_event")
            return {}

        async def get_session(self, session_id: str) -> dict[str, object]:
            return {"status": "running", "summary": "no marker present"}

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
                    "session": {"allowEmptyWorkspace": True},
                    "prompt": {"text": "continue"},
                },
            },
        ),
        run_store=Store(),
    )

    assert result.failure_class == "execution_error"
    assert result.provider_error_code == "omnigent_first_message_reconcile_failed"
    assert result.diagnostics_ref.startswith("artifact://omnigent/")
    assert "post_event" not in calls
    assert "mark_posted" not in calls


@pytest.mark.asyncio
async def test_run_omnigent_execution_digest_mismatch_is_non_retryable_with_diagnostics(
    monkeypatch,
) -> None:
    class Row:
        omnigent_session_id = "persisted-session"
        first_message_state = "prepared"

    class Store:
        async def get_or_create(self, **_: object) -> Row:
            return Row()

        async def mark_prepared(self, *_: object, **__: object) -> Row:
            raise OmnigentDigestMismatchError("digest changed")

    class FakeClient:
        def __init__(self, **_: object) -> None:
            pass

        async def list_agents(self) -> dict[str, object]:
            return {"items": [{"id": "agent-1", "name": "codex-native-ui"}]}

        async def post_event(
            self,
            session_id: str,
            payload: dict[str, object],
        ) -> dict[str, object]:
            raise AssertionError("digest mismatch must not post")

        async def get_session(self, session_id: str) -> dict[str, object]:
            return {"status": "running", "summary": "existing session"}

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
                    "session": {"allowEmptyWorkspace": True},
                    "prompt": {"text": "changed prompt"},
                },
            },
        ),
        run_store=Store(),
    )

    assert result.failure_class == "execution_error"
    assert result.provider_error_code == "omnigent_first_message_digest_mismatch"
    assert result.diagnostics_ref.startswith("artifact://omnigent/")


@pytest.mark.asyncio
async def test_run_omnigent_execution_harvests_changed_and_session_files(
    monkeypatch,
    tmp_path,
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
            return {"pending_id": "pending-1"}

        async def stream_events(self, session_id: str):
            yield {"type": "response.completed"}

        async def get_session(self, session_id: str) -> dict[str, object]:
            return {
                "status": "completed",
                "summary": "done",
                "githubPrUrl": "https://github.example/org/repo/pull/1",
            }

        async def list_changed_files(self, session_id: str) -> dict[str, object]:
            return {"items": [{"path": "src/app.py"}]}

        async def get_workspace_file(self, session_id: str, path: str) -> bytes:
            return {
                "README.md": b"# Project\n",
                "src/app.py": b"print('changed')\n",
            }[path]

        async def list_workspace_files(self, session_id: str) -> dict[str, object]:
            return {
                "items": [
                    {"path": "README.md", "type": "file"},
                    {"path": "src", "type": "directory"},
                ]
            }

        async def get_workspace_diff(self, session_id: str, path: str) -> bytes:
            assert path == "src/app.py"
            return b"diff --git a/src/app.py b/src/app.py\n"

        async def list_session_files(self, session_id: str) -> dict[str, object]:
            return {"items": [{"id": "file-1", "filename": "session.log"}]}

        async def get_session_file_content(self, session_id: str, file_id: str) -> bytes:
            assert file_id == "file-1"
            return b"session evidence\n"

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
                    "session": {"allowEmptyWorkspace": True},
                    "prompt": {"text": "Do the task"},
                },
            },
        ),
        artifact_gateway=LocalOmnigentArtifactGateway(root=tmp_path),
    )

    assert result.failure_class is None
    assert result.metadata["changedFilesIndexRef"].startswith("artifact://omnigent/")
    assert result.metadata["workspaceFilesIndexRef"].startswith("artifact://omnigent/")
    assert result.metadata["sessionFilesIndexRef"].startswith("artifact://omnigent/")
    assert result.metadata["githubPrUrl"] == "https://github.example/org/repo/pull/1"
    manifest_path = tmp_path / "corr-1" / "output.omnigent.capture_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["workspaceFiles"][0]["path"] == "README.md"
    assert manifest["workspaceDiffs"][0]["path"] == "src/app.py"
    assert manifest["patchUnavailable"] is False


@pytest.mark.asyncio
async def test_run_omnigent_execution_caps_resource_harvest(
    monkeypatch,
    tmp_path,
) -> None:
    changed_fetches: list[str] = []
    session_fetches: list[str] = []

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
            return {"pending_id": "pending-1"}

        async def stream_events(self, session_id: str):
            yield {"type": "response.completed"}

        async def get_session(self, session_id: str) -> dict[str, object]:
            return {"status": "completed", "summary": "done"}

        async def list_changed_files(self, session_id: str) -> dict[str, object]:
            return {"items": [{"path": f"src/file_{index}.py"} for index in range(101)]}

        async def get_workspace_file(self, session_id: str, path: str) -> bytes:
            changed_fetches.append(path)
            return b"changed\n"

        async def list_session_files(self, session_id: str) -> dict[str, object]:
            return {
                "items": [
                    {"id": f"file-{index}", "filename": f"session-{index}.log"}
                    for index in range(101)
                ]
            }

        async def get_session_file_content(self, session_id: str, file_id: str) -> bytes:
            session_fetches.append(file_id)
            return b"session evidence\n"

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
                    "session": {"allowEmptyWorkspace": True},
                    "prompt": {"text": "Do the task"},
                },
            },
        ),
        artifact_gateway=LocalOmnigentArtifactGateway(root=tmp_path),
    )

    assert result.failure_class is None
    assert len(changed_fetches) == 100
    assert changed_fetches[-1] == "src/file_99.py"
    assert len(session_fetches) == 100
    assert session_fetches[-1] == "file-99"


@pytest.mark.asyncio
async def test_run_omnigent_execution_records_missing_resource_harvest_and_child_evidence(
    monkeypatch,
    tmp_path,
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
            return {"pending_id": "pending-1"}

        async def stream_events(self, session_id: str):
            yield {
                "type": "session.child.created",
                "data": {"childSessionId": "child-1"},
            }
            yield {"type": "response.completed"}

        async def get_session(self, session_id: str) -> dict[str, object]:
            if session_id == "child-1":
                return {"status": "completed", "summary": "child done"}
            return {"status": "completed", "summary": "done"}

        async def list_changed_files(self, session_id: str) -> dict[str, object]:
            raise RuntimeError("diff endpoint missing")

        async def list_session_files(self, session_id: str) -> dict[str, object]:
            raise RuntimeError("session file endpoint missing")

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
                    "session": {"allowEmptyWorkspace": True},
                    "prompt": {"text": "Do the task"},
                },
            },
        ),
        artifact_gateway=LocalOmnigentArtifactGateway(root=tmp_path),
    )

    assert result.failure_class is None
    assert result.metadata["childSessionsRef"].startswith("artifact://omnigent/")
    manifest_path = tmp_path / "corr-1" / "output.omnigent.capture_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["patchUnavailable"] is True
    assert manifest["childSessions"] == 1
    assert manifest["childSessionEvidence"][0]["childSessionId"] == "child-1"
    assert "changedFilesUnavailable" in manifest
    assert "workspaceFilesUnavailable" in manifest
    assert "sessionFilesUnavailable" in manifest
