"""MM-995 unit coverage for Omnigent execute boundary behavior."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import AsyncIterator, Mapping
from typing import Any

import pytest

import moonmind.omnigent.execute as execute_module
from moonmind.omnigent.execute import (
    InMemoryOmnigentRunStore,
    OmnigentExecutionError,
    OmnigentRunRecord,
    normalize_omnigent_state,
    run_omnigent_execution,
)
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest


def _request(**overrides: object) -> AgentExecutionRequest:
    payload = {
        "agentKind": "external",
        "agentId": "omnigent",
        "correlationId": "corr-mm-995",
        "idempotencyKey": "idem-mm-995",
        "parameters": {
            "title": "MM-995 task",
            "omnigent": {
                "agent": {"agentId": "ag_1"},
                "session": {"allowEmptyWorkspace": True},
                "prompt": {"text": "prove the boundary"},
            },
        },
    }
    payload.update(overrides)
    return AgentExecutionRequest(**payload)


def test_normalize_omnigent_state_rejects_unknown_status() -> None:
    assert normalize_omnigent_state("succeeded") == "completed"
    assert normalize_omnigent_state("awaiting_input") == "awaiting_approval"

    with pytest.raises(ValueError, match="Unsupported status"):
        normalize_omnigent_state("provider_new_state")


def _prompt_digest(prompt: str = "prove the boundary") -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


@pytest.mark.asyncio
async def test_ambiguous_posting_reconciliation_fails_closed() -> None:
    store = InMemoryOmnigentRunStore()
    store.put(
        OmnigentRunRecord(
            idempotency_key="idem-mm-995",
            session_id="sess_existing",
            prompt_digest=_prompt_digest(),
            first_message_state="posting",
        )
    )
    client = _UnitClient(snapshot={"id": "sess_existing", "status": "running"})

    with pytest.raises(OmnigentExecutionError, match="failed closed") as exc:
        await run_omnigent_execution(
            _request(),
            env={"OMNIGENT_ENABLED": "1", "OMNIGENT_SERVER_URL": "https://fake"},
            client=client,
            run_store=store,
        )

    assert exc.value.failure_class == "integration_error"
    assert client.posted_events == []


@pytest.mark.asyncio
async def test_reused_mapping_skips_duplicate_first_message_when_reconciled() -> None:
    request = _request()
    prompt_digest = _prompt_digest()
    store = InMemoryOmnigentRunStore()
    store.put(
        OmnigentRunRecord(
            idempotency_key=request.idempotency_key,
            session_id="sess_existing",
            prompt_digest=prompt_digest,
            first_message_state="posting",
        )
    )
    client = _UnitClient(
        snapshot={
            "id": "sess_existing",
            "status": "completed",
            "summary": "done",
            "items": [{"metadata": {"moonmindPromptDigest": prompt_digest}}],
        }
    )

    result = await run_omnigent_execution(
        request,
        env={"OMNIGENT_ENABLED": "1", "OMNIGENT_SERVER_URL": "https://fake"},
        client=client,
        run_store=store,
    )

    assert result.summary == "done"
    assert client.created_sessions == []
    assert client.posted_events == []


@pytest.mark.asyncio
async def test_cancelled_execute_interrupts_then_stops_session() -> None:
    client = _UnitClient(snapshot={"id": "sess_1", "status": "running"}, hang=True)
    task = asyncio.create_task(
        run_omnigent_execution(
            _request(),
            env={"OMNIGENT_ENABLED": "1", "OMNIGENT_SERVER_URL": "https://fake"},
            client=client,
            run_store=InMemoryOmnigentRunStore(),
        )
    )
    await client.session_created.wait()

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        _ = await task

    assert client.cancel_events == ["interrupt", "stop_session"]


@pytest.mark.asyncio
async def test_cancelled_execute_suppresses_best_effort_cancel_errors() -> None:
    client = _UnitClient(
        snapshot={"id": "sess_1", "status": "running"},
        hang=True,
        fail_cancel=True,
    )
    task = asyncio.create_task(
        run_omnigent_execution(
            _request(),
            env={"OMNIGENT_ENABLED": "1", "OMNIGENT_SERVER_URL": "https://fake"},
            client=client,
            run_store=InMemoryOmnigentRunStore(),
        )
    )
    await client.session_created.wait()

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        _ = await task

    assert client.cancel_events == ["interrupt", "stop_session"]


@pytest.mark.asyncio
async def test_stream_heartbeats_and_fails_closed_on_non_terminal_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    heartbeats: list[dict[str, Any]] = []
    monkeypatch.setattr(execute_module.activity, "heartbeat", heartbeats.append)
    client = _UnitClient(
        snapshot={"id": "sess_1", "status": "running"},
        events=[{"type": "status", "status": "running"}],
    )

    with pytest.raises(OmnigentExecutionError, match="before terminal"):
        await run_omnigent_execution(
            _request(),
            env={"OMNIGENT_ENABLED": "1", "OMNIGENT_SERVER_URL": "https://fake"},
            client=client,
            run_store=InMemoryOmnigentRunStore(),
        )

    phases = [heartbeat["phase"] for heartbeat in heartbeats]
    assert phases == [
        "session_created",
        "first_message_posting",
        "first_message_posted",
        "stream_event",
    ]
    assert heartbeats[-1]["eventsCaptured"] == 1


@pytest.mark.asyncio
async def test_harvest_output_refs_does_not_download_file_content() -> None:
    client = _UnitClient(
        snapshot={"id": "sess_1", "status": "completed"},
        changed_files={"changes": [{"path": "app.py"}]},
        session_files={"files": [{"id": "file_1"}]},
    )

    result = await run_omnigent_execution(
        _request(),
        env={"OMNIGENT_ENABLED": "1", "OMNIGENT_SERVER_URL": "https://fake"},
        client=client,
        run_store=InMemoryOmnigentRunStore(),
    )

    assert any(ref.endswith("/workspace/app.py") for ref in result.output_refs)
    assert any(ref.endswith("/files/file_1") for ref in result.output_refs)
    assert client.workspace_downloads == []
    assert client.session_file_downloads == []


@pytest.mark.asyncio
async def test_concurrent_same_key_execution_creates_one_session() -> None:
    client = _UnitClient(
        snapshot={
            "id": "sess_1",
            "status": "completed",
            "items": [{"metadata": {"moonmindPromptDigest": _prompt_digest()}}],
        },
        create_delay=0.01,
    )
    store = InMemoryOmnigentRunStore()

    first, second = await asyncio.gather(
        run_omnigent_execution(
            _request(),
            env={"OMNIGENT_ENABLED": "1", "OMNIGENT_SERVER_URL": "https://fake"},
            client=client,
            run_store=store,
        ),
        run_omnigent_execution(
            _request(),
            env={"OMNIGENT_ENABLED": "1", "OMNIGENT_SERVER_URL": "https://fake"},
            client=client,
            run_store=store,
        ),
    )

    assert first.failure_class is None
    assert second.failure_class is None
    assert len(client.created_sessions) == 1
    assert len(client.posted_events) == 1


class _UnitClient:
    def __init__(
        self,
        *,
        snapshot: Mapping[str, Any],
        hang: bool = False,
        events: list[dict[str, Any]] | None = None,
        fail_cancel: bool = False,
        changed_files: Mapping[str, Any] | None = None,
        session_files: Mapping[str, Any] | None = None,
        create_delay: float = 0.0,
    ) -> None:
        self.snapshot = dict(snapshot)
        self.hang = hang
        self.events = list(events or [])
        self.fail_cancel = fail_cancel
        self.changed_files = dict(changed_files or {"changes": []})
        self.session_files = dict(session_files or {"files": []})
        self.create_delay = create_delay
        self.session_created = asyncio.Event()
        self.created_sessions: list[dict[str, Any]] = []
        self.posted_events: list[dict[str, Any]] = []
        self.cancel_events: list[str] = []
        self.workspace_downloads: list[str] = []
        self.session_file_downloads: list[str] = []

    async def list_agents(self) -> list[dict[str, Any]]:
        return [{"id": "ag_1", "name": "codex"}]

    async def create_session(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        if self.create_delay:
            await asyncio.sleep(self.create_delay)
        self.created_sessions.append(dict(payload))
        self.session_created.set()
        return {"id": "sess_1"}

    async def get_session(self, session_id: str) -> dict[str, Any]:
        return dict(self.snapshot)

    async def post_event(
        self,
        session_id: str,
        event: Mapping[str, Any],
    ) -> dict[str, Any]:
        self.posted_events.append(dict(event))
        return {"pending_id": "pending_1"}

    async def stream_events(self, session_id: str) -> AsyncIterator[dict[str, Any]]:
        if self.hang:
            await asyncio.Event().wait()
        for event in self.events:
            yield event

    async def resolve_elicitation(
        self,
        session_id: str,
        elicitation_id: str,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        return {"ok": True}

    async def list_changed_files(self, session_id: str) -> dict[str, Any]:
        return dict(self.changed_files)

    async def get_workspace_file(self, session_id: str, path: str) -> bytes:
        self.workspace_downloads.append(path)
        return b""

    async def list_session_files(self, session_id: str) -> dict[str, Any]:
        return dict(self.session_files)

    async def get_session_file_content(self, session_id: str, file_id: str) -> bytes:
        self.session_file_downloads.append(file_id)
        return b""

    async def interrupt(self, session_id: str) -> dict[str, Any]:
        self.cancel_events.append("interrupt")
        if self.fail_cancel:
            raise RuntimeError("interrupt failed")
        return {"ok": True}

    async def stop_session(self, session_id: str) -> dict[str, Any]:
        self.cancel_events.append("stop_session")
        if self.fail_cancel:
            raise RuntimeError("stop failed")
        return {"ok": True}
