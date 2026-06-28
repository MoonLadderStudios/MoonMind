from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api_service.db.models import Base, OmnigentExternalRun
from moonmind.omnigent.execute import (
    OmnigentSessionStillRunningError,
    run_omnigent_execution,
)
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest


class FakeOmnigentClient:
    def __init__(self, *, snapshot: dict[str, Any] | None = None) -> None:
        self.create_calls = 0
        self.post_calls = 0
        self.session_id = "sess-1"
        self.snapshot = snapshot or {"status": "completed", "items": []}
        self.events = [{"type": "response.completed", "summary": "done"}]
        self.posted_payloads: list[dict[str, Any]] = []

    async def list_agents(self) -> list[dict[str, Any]]:
        return [{"id": "ag-1", "name": "codex-native-ui"}]

    async def create_session(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.create_calls += 1
        return {"id": self.session_id}

    async def get_session(self, session_id: str) -> dict[str, Any]:
        assert session_id == self.session_id
        return self.snapshot

    async def post_event(self, session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        assert session_id == self.session_id
        self.post_calls += 1
        self.posted_payloads.append(payload)
        return {"pending_id": f"pending-{self.post_calls}"}

    async def stream_events(self, session_id: str) -> AsyncIterator[dict[str, Any]]:
        assert session_id == self.session_id
        for event in self.events:
            yield event


@pytest_asyncio.fixture
async def session_factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/omnigent.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield maker
    finally:
        await engine.dispose()


def _request(*, text: str = "do the thing") -> AgentExecutionRequest:
    prompt = {} if text == "" else {"text": text}
    return AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        correlationId="wf-1:run-1:step-1",
        idempotencyKey="idem-1",
        parameters={
            "title": "Omnigent task",
            "omnigent": {
                "agent": {"agentName": "codex-native-ui"},
                "prompt": prompt,
            },
        },
    )


@pytest.mark.asyncio
async def test_reuses_existing_session_and_skips_posted_first_message(
    session_factory, monkeypatch
):
    monkeypatch.setenv("OMNIGENT_ENABLED", "true")
    monkeypatch.setenv("OMNIGENT_SERVER_URL", "http://omnigent.test")

    client = FakeOmnigentClient()
    first = await run_omnigent_execution(
        _request(),
        client=client,
        store=_store(session_factory),
    )
    assert first.failure_class is None
    assert client.create_calls == 1
    assert client.post_calls == 1

    retry_client = FakeOmnigentClient()
    retry = await run_omnigent_execution(
        _request(),
        client=retry_client,
        store=_store(session_factory),
    )
    assert retry.failure_class is None
    assert retry_client.create_calls == 0
    assert retry_client.post_calls == 0

    async with session_factory() as session:
        row = await session.get(OmnigentExternalRun, "idem-1")
        assert row is not None
        assert row.omnigent_session_id == "sess-1"
        assert row.first_message_state == "terminal"
        assert row.first_message_digest is not None
        assert row.first_message_marker is not None
        assert row.target_metadata["endpoint_ref"] == "default"


@pytest.mark.asyncio
async def test_digest_mismatch_fails_fast_as_user_error(session_factory, monkeypatch):
    monkeypatch.setenv("OMNIGENT_ENABLED", "true")
    monkeypatch.setenv("OMNIGENT_SERVER_URL", "http://omnigent.test")

    await run_omnigent_execution(
        _request(text="first"),
        client=FakeOmnigentClient(),
        store=_store(session_factory),
    )
    result = await run_omnigent_execution(
        _request(text="different"),
        client=FakeOmnigentClient(),
        store=_store(session_factory),
    )

    assert result.failure_class == "user_error"
    assert result.provider_error_code == "omnigent_first_message_digest_mismatch"


@pytest.mark.asyncio
async def test_retry_in_posting_state_reconciles_marker_and_skips_post(
    session_factory, monkeypatch
):
    monkeypatch.setenv("OMNIGENT_ENABLED", "true")
    monkeypatch.setenv("OMNIGENT_SERVER_URL", "http://omnigent.test")

    from moonmind.omnigent.store import OmnigentRunStore

    store = OmnigentRunStore(session_factory)
    req = _request()
    from moonmind.omnigent.execute import _first_message_digest
    digest = _first_message_digest("do the thing")
    await store.get_or_create(
        request=req,
        endpoint_ref="default",
        agent_id="ag-1",
        agent_name="codex-native-ui",
        target_metadata={},
    )
    await store.attach_session(req.idempotency_key, "sess-1")
    await store.mark_prepared(
        req.idempotency_key,
        digest=digest,
        marker="MoonMind-Omnigent-Run: marker",
    )
    await store.mark_posting(req.idempotency_key)

    client = FakeOmnigentClient(
        snapshot={"status": "completed", "items": [{"text": digest}]}
    )
    result = await run_omnigent_execution(req, client=client, store=store)

    assert result.failure_class is None
    assert client.create_calls == 0
    assert client.post_calls == 0


@pytest.mark.asyncio
async def test_retry_in_posting_state_fails_closed_when_ambiguous(
    session_factory, monkeypatch
):
    monkeypatch.setenv("OMNIGENT_ENABLED", "true")
    monkeypatch.setenv("OMNIGENT_SERVER_URL", "http://omnigent.test")

    from moonmind.omnigent.store import OmnigentRunStore

    store = OmnigentRunStore(session_factory)
    req = _request()
    from moonmind.omnigent.execute import _first_message_digest
    digest = _first_message_digest("do the thing")
    await store.get_or_create(
        request=req,
        endpoint_ref="default",
        agent_id="ag-1",
        agent_name="codex-native-ui",
        target_metadata={},
    )
    await store.attach_session(req.idempotency_key, "sess-1")
    await store.mark_prepared(req.idempotency_key, digest=digest, marker="marker")
    await store.mark_posting(req.idempotency_key)

    client = FakeOmnigentClient(snapshot={"status": "running"})
    result = await run_omnigent_execution(req, client=client, store=store)

    assert result.failure_class == "integration_error"
    assert result.provider_error_code == "omnigent_first_message_acceptance_ambiguous"
    assert result.metadata["normalizedStatus"] == "failed"
    assert "intervention_requested" not in str(result.model_dump())


@pytest.mark.asyncio
async def test_stream_disconnect_with_active_snapshot_raises_for_retry(
    session_factory, monkeypatch
):
    monkeypatch.setenv("OMNIGENT_ENABLED", "true")
    monkeypatch.setenv("OMNIGENT_SERVER_URL", "http://omnigent.test")

    client = FakeOmnigentClient(snapshot={"status": "running", "items": []})
    client.events = []

    with pytest.raises(OmnigentSessionStillRunningError):
        await run_omnigent_execution(
            _request(),
            client=client,
            store=_store(session_factory),
        )


@pytest.mark.asyncio
async def test_instruction_ref_becomes_first_message_when_prompt_text_is_absent(
    session_factory, monkeypatch
):
    monkeypatch.setenv("OMNIGENT_ENABLED", "true")
    monkeypatch.setenv("OMNIGENT_SERVER_URL", "http://omnigent.test")

    req = _request(text="")
    req.instruction_ref = "artifact://instructions"
    client = FakeOmnigentClient()

    result = await run_omnigent_execution(
        req,
        client=client,
        store=_store(session_factory),
    )

    assert result.failure_class is None
    posted_text = client.posted_payloads[0]["data"]["content"][0]["text"]
    assert posted_text.startswith("artifact://instructions")


@pytest.mark.asyncio
async def test_retry_markerless_prompt_uses_message_text_to_skip_duplicate_post(
    session_factory, monkeypatch
):
    monkeypatch.setenv("OMNIGENT_ENABLED", "true")
    monkeypatch.setenv("OMNIGENT_SERVER_URL", "http://omnigent.test")

    from moonmind.omnigent.execute import _first_message_digest
    from moonmind.omnigent.store import OmnigentRunStore

    store = OmnigentRunStore(session_factory)
    req = _request()
    req.parameters["omnigent"]["prompt"]["includeIdempotencyMarker"] = False
    digest = _first_message_digest("do the thing")
    await store.get_or_create(
        request=req,
        endpoint_ref="default",
        agent_id="ag-1",
        agent_name="codex-native-ui",
        target_metadata={},
    )
    await store.attach_session(req.idempotency_key, "sess-1")
    await store.mark_prepared(req.idempotency_key, digest=digest, marker="marker")
    await store.mark_posting(req.idempotency_key)

    client = FakeOmnigentClient(
        snapshot={"status": "completed", "items": [{"text": "do the thing"}]}
    )
    result = await run_omnigent_execution(req, client=client, store=store)

    assert result.failure_class is None
    assert client.post_calls == 0


def _store(session_factory):
    from moonmind.omnigent.store import OmnigentRunStore

    return OmnigentRunStore(session_factory)
