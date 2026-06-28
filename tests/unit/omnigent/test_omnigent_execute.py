"""MM-994 Omnigent execute lifecycle and safety tests."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from moonmind.omnigent.execute import run_omnigent_execution
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest


class _FakeOmnigentClient:
    instances: list["_FakeOmnigentClient"] = []

    def __init__(self, **_kwargs: Any) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.statuses = ["running", "running", "completed"]
        _FakeOmnigentClient.instances.append(self)

    async def list_agents(self) -> list[dict[str, Any]]:
        self.calls.append(("list_agents", None))
        return [{"id": "ag_default", "name": "codex-native-ui"}]

    async def create_session(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(("create_session", payload))
        return {"id": "sess_123", "status": "running"}

    async def get_session(self, session_id: str) -> dict[str, Any]:
        self.calls.append(("get_session", session_id))
        status = self.statuses.pop(0) if self.statuses else "completed"
        return {"id": session_id, "status": status, "summary": "done"}

    async def post_event(
        self,
        session_id: str,
        event: dict[str, Any],
    ) -> dict[str, Any]:
        self.calls.append(("post_event", {"session_id": session_id, "event": event}))
        return {"ok": True}

    async def stream_events(self, session_id: str):
        self.calls.append(("stream_events", session_id))
        yield {"type": "response.output_text.delta", "text": "done"}

    async def list_changed_files(self, session_id: str) -> dict[str, Any]:
        self.calls.append(("list_changed_files", session_id))
        return {"files": [{"path": "README.md"}]}

    async def get_workspace_file(self, session_id: str, path: str) -> bytes:
        self.calls.append(("get_workspace_file", {"session_id": session_id, "path": path}))
        return b"updated"

    async def list_session_files(self, session_id: str) -> dict[str, Any]:
        self.calls.append(("list_session_files", session_id))
        return {"files": [{"id": "file_1", "filename": "result.txt"}]}

    async def get_session_file_content(self, session_id: str, file_id: str) -> bytes:
        self.calls.append(
            ("get_session_file_content", {"session_id": session_id, "file_id": file_id})
        )
        return b"session output"

    async def interrupt(self, session_id: str) -> dict[str, Any]:
        self.calls.append(("interrupt", session_id))
        return {"ok": True}

    async def stop_session(self, session_id: str) -> dict[str, Any]:
        self.calls.append(("stop_session", session_id))
        return {"ok": True}

    async def delete_session(
        self,
        session_id: str,
        *,
        delete_branch: bool = False,
    ) -> dict[str, Any]:
        self.calls.append(
            ("delete_session", {"session_id": session_id, "delete_branch": delete_branch})
        )
        return {"ok": True}


@pytest.fixture(autouse=True)
def _omnigent_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OMNIGENT_ENABLED", "1")
    monkeypatch.setenv("OMNIGENT_SERVER_URL", "https://omnigent.test")
    monkeypatch.setenv("OMNIGENT_API_TOKEN", "omnigent-secret-token")
    monkeypatch.setenv("OMNIGENT_DEFAULT_AGENT_NAME", "codex-native-ui")
    monkeypatch.setenv("OMNIGENT_POLL_INTERVAL_SECONDS", "0.01")
    monkeypatch.setenv("OMNIGENT_CANCEL_GRACE_SECONDS", "0.01")
    monkeypatch.setenv("MOONMIND_OMNIGENT_ARTIFACT_ROOT", str(tmp_path))
    _FakeOmnigentClient.instances.clear()


def _request(parameters: dict[str, Any] | None = None) -> AgentExecutionRequest:
    return AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        correlationId="corr-1",
        idempotencyKey="idem-1",
        parameters=parameters
        or {
            "title": "MM-994 task",
            "description": "Complete the bounded task.",
            "omnigent": {
                "agent": {"agentId": "ag_1"},
                "session": {
                    "hostType": "managed",
                    "workspace": "https://github.com/org/repo#main",
                },
            },
        },
    )


@pytest.mark.asyncio
async def test_omnigent_execute_posts_streams_harvests_and_preserves_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("moonmind.omnigent.execute.OmnigentHttpClient", _FakeOmnigentClient)
    monkeypatch.setattr("moonmind.omnigent.execute.activity.heartbeat", lambda *_a, **_k: None)

    result = await run_omnigent_execution(_request())

    client = _FakeOmnigentClient.instances[0]
    call_names = [name for name, _ in client.calls]
    assert call_names.index("create_session") < call_names.index("post_event")
    assert "stream_events" in call_names
    assert "list_changed_files" in call_names
    assert "list_session_files" in call_names
    assert "delete_session" not in call_names
    assert result.failure_class is None
    assert result.metadata["sourceIssue"] == "MM-994"
    assert result.output_refs
    assert all(ref.startswith("file:") for ref in result.output_refs)


@pytest.mark.asyncio
async def test_omnigent_delete_branch_requires_explicit_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("moonmind.omnigent.execute.OmnigentHttpClient", _FakeOmnigentClient)

    result = await run_omnigent_execution(
        _request(
            {
                "omnigent": {
                    "agent": {"agentId": "ag_1"},
                    "capture": {
                        "deleteOmnigentSessionAfterHarvest": True,
                        "deleteBranch": True,
                    },
                }
            }
        )
    )

    assert result.failure_class == "user_error"
    assert result.provider_error_code == "omnigent_delete_branch_policy_required"
    assert _FakeOmnigentClient.instances == []


@pytest.mark.asyncio
async def test_omnigent_optional_delete_uses_explicit_delete_branch_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("moonmind.omnigent.execute.OmnigentHttpClient", _FakeOmnigentClient)
    monkeypatch.setattr("moonmind.omnigent.execute.activity.heartbeat", lambda *_a, **_k: None)

    await run_omnigent_execution(
        _request(
            {
                "omnigent": {
                    "agent": {"agentId": "ag_1"},
                    "capture": {"deleteOmnigentSessionAfterHarvest": True},
                }
            }
        )
    )

    client = _FakeOmnigentClient.instances[0]
    assert ("delete_session", {"session_id": "sess_123", "delete_branch": False}) in client.calls


@pytest.mark.asyncio
async def test_omnigent_cancellation_interrupts_then_stops_active_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("moonmind.omnigent.execute.OmnigentHttpClient", _FakeOmnigentClient)
    monkeypatch.setattr("moonmind.omnigent.execute.activity.heartbeat", lambda *_a, **_k: None)
    sleep_calls = 0

    async def fake_sleep(_delay: float) -> None:
        nonlocal sleep_calls
        sleep_calls += 1
        if sleep_calls == 1:
            raise asyncio.CancelledError()

    monkeypatch.setattr("moonmind.omnigent.execute.asyncio.sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        await run_omnigent_execution(_request())

    client = _FakeOmnigentClient.instances[0]
    call_names = [name for name, _ in client.calls]
    assert "interrupt" in call_names
    assert "stop_session" in call_names
    assert call_names.index("interrupt") < call_names.index("stop_session")
    assert "delete_session" not in call_names


@pytest.mark.asyncio
async def test_omnigent_rejects_raw_secret_values_and_redacts_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("moonmind.omnigent.execute.OmnigentHttpClient", _FakeOmnigentClient)

    result = await run_omnigent_execution(
        _request(
            {
                "omnigent": {
                    "agent": {"agentId": "ag_1"},
                    "session": {"hostType": "managed"},
                    "note": "Bearer abcdefghijklmnopqrstuvwxyz123456",
                }
            }
        )
    )

    assert result.failure_class == "user_error"
    assert result.provider_error_code == "omnigent_raw_secret_rejected"
    assert "abcdefghijklmnopqrstuvwxyz" not in result.model_dump_json(by_alias=True)
    diagnostics_path = Path(result.diagnostics_ref.removeprefix("file:"))
    assert "abcdefghijklmnopqrstuvwxyz" not in diagnostics_path.read_text()


@pytest.mark.asyncio
async def test_omnigent_rejects_v1_session_reuse_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("moonmind.omnigent.execute.OmnigentHttpClient", _FakeOmnigentClient)

    result = await run_omnigent_execution(
        _request({"omnigent": {"session": {"reuseSessionId": "sess_existing"}}})
    )

    assert result.failure_class == "user_error"
    assert result.provider_error_code == "omnigent_v1_non_goal"
    assert _FakeOmnigentClient.instances == []


@pytest.mark.asyncio
async def test_omnigent_timeout_maps_to_canonical_timed_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class TimeoutClient(_FakeOmnigentClient):
        async def create_session(self, payload: dict[str, Any]) -> dict[str, Any]:
            self.calls.append(("create_session", payload))
            raise asyncio.TimeoutError("request timeout token=hidden")

    monkeypatch.setattr("moonmind.omnigent.execute.OmnigentHttpClient", TimeoutClient)

    result = await run_omnigent_execution(_request())

    assert result.failure_class == "timed_out"
    assert result.provider_error_code == "omnigent_timed_out"
    assert "token=hidden" not in result.model_dump_json(by_alias=True)
