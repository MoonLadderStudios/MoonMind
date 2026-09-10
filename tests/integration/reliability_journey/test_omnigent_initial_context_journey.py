"""Hermetic reliability journey for retired Omnigent initial-context delivery."""

from __future__ import annotations

import hashlib
import json

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import Base
from moonmind.omnigent.bridge_artifacts import LocalOmnigentArtifactGateway
from moonmind.omnigent.bridge_store import OmnigentBridgeSessionStore
from moonmind.omnigent.execute import (
    _first_message_text,
    _resolve_initial_context_message,
)
from moonmind.schemas.agent_runtime_models import (
    AgentExecutionRequest,
    AgentRuntimeStepExecutionLaunch,
)


pytestmark = [pytest.mark.integration, pytest.mark.integration_ci]


def _request() -> AgentExecutionRequest:
    return AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        executionProfileRef="profile:test",
        correlationId="corr-reliability",
        idempotencyKey="idem-reliability",
        parameters={"metadata": {}, "repository": "org/repo"},
        stepExecution=AgentRuntimeStepExecutionLaunch(
            workflowId="mm:wf-reliability",
            runId="run-reliability",
            logicalStepId="implement",
            executionOrdinal=1,
            stepExecutionId="mm:wf-reliability:run-reliability:implement:execution:1",
            runtimeContextPolicy="fresh_agent_run",
        ),
    )


def _message(text: str = "Implement the verified change") -> dict[str, object]:
    return {
        "type": "message",
        "data": {
            "role": "user",
            "content": [{"type": "input_text", "text": text}],
        },
    }


@pytest.mark.asyncio
async def test_context_pack_is_committed_once_and_projected_across_worker_restart(
    tmp_path,
) -> None:
    """Retired native-RAG posture (MoonLadderStudios/MoonMind#4192).

    First-message preparation ships the authored message unchanged with
    disabled retrieval evidence; the prepared message commits once and the
    retry path reuses it without rerunning retrieval (no injection
    entrypoint remains).
    """
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/bridge.db")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    store = OmnigentBridgeSessionStore(sessions)
    gateway = LocalOmnigentArtifactGateway(root=tmp_path)
    request = _request()
    await store.get_or_create(
        request=request,
        endpoint_ref="gateway-owned",
        agent_id="codex",
        agent_name="Codex",
        target_metadata={"hostType": "external"},
    )
    prepared, evidence = await _resolve_initial_context_message(
        request=request,
        first_message=_message(),
        artifact_gateway=gateway,
        run_store=store,
        durable_row=None,
        workspace=str(tmp_path),
    )
    assert _first_message_text(prepared).startswith(
        "Implement the verified change"
    )
    assert "BEGIN_RETRIEVED_CONTEXT" not in _first_message_text(prepared)
    assert evidence["contextPackRef"] is None
    assert evidence["state"] == "disabled"
    assert evidence["firstMessageConsumedContextRef"] is False
    message_digest = hashlib.sha256(
        json.dumps(prepared, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    await store.mark_prepared(
        request.idempotency_key, digest=message_digest, marker="reliability-marker"
    )
    await store.mark_posting(request.idempotency_key)
    await store.mark_posted(
        request.idempotency_key,
        response={"pending_id": "pending-1", "item_id": "message-1"},
    )

    restarted_row = await store.resolve_projection_session(
        step_execution_id=request.step_execution.step_execution_id
    )
    restarted, restarted_evidence = await _resolve_initial_context_message(
        request=_request(),
        first_message=_message("a retry must not replace this"),
        artifact_gateway=LocalOmnigentArtifactGateway(root=tmp_path),
        run_store=store,
        durable_row=restarted_row,
        workspace=str(tmp_path),
    )

    assert restarted == prepared
    assert restarted_evidence == evidence
    assert restarted_row.first_message_state == "posted"
    assert restarted_row.first_message_pending_id == "pending-1"
    projection = restarted_row.metadata_["initialRetrieval"]
    assert projection["contextPackRef"] is None
    assert projection["firstMessageConsumedContextRef"] is False
    assert "BEGIN_RETRIEVED_CONTEXT" not in _first_message_text(restarted)
    assert "retrieval-token" not in json.dumps(projection)
    await engine.dispose()
