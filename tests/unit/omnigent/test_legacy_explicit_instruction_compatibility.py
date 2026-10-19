"""Retained disabled-mode explicit prompts keep their existing consumer."""

from __future__ import annotations

import hashlib

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api_service.db.models import Base
from moonmind.config.settings import settings
from moonmind.omnigent.bridge_artifacts import TemporalOmnigentArtifactGateway
from moonmind.omnigent.execute import _build_omnigent_first_message
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.workflows.temporal.artifacts import (
    TemporalArtifactRepository,
    TemporalArtifactService,
)


@pytest.mark.asyncio
async def test_retained_disabled_explicit_prompt_needs_no_fabricated_admission(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(settings.oidc, "AUTH_PROVIDER", "disabled")
    monkeypatch.setattr(settings.workflow, "temporal_artifact_backend", "local_fs")
    monkeypatch.setattr(
        settings.workflow, "temporal_artifact_root", str(tmp_path / "blobs")
    )
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/instructions.db")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        body = b"Preserve the saved work and finish the assertion."
        async with sessions() as session:
            service = TemporalArtifactService(TemporalArtifactRepository(session))
            artifact, _ = await service.create(
                principal="retained-capture-owner",
                content_type="text/plain",
                size_bytes=len(body),
                sha256=hashlib.sha256(body).hexdigest(),
            )
            await service.write_complete(
                artifact_id=artifact.artifact_id,
                principal="retained-capture-owner",
                payload=body,
                content_type="text/plain",
            )
            ref = f"artifact:{artifact.artifact_id}"
        # Existing histories have no prospective association. Do not fabricate
        # a canonical Run, admission snapshot, or artifact grant to read them.
        request = AgentExecutionRequest(
            agentKind="external",
            agentId="omnigent",
            correlationId="historical",
            idempotencyKey="historical-step",
        )
        message = await _build_omnigent_first_message(
            request=request,
            prompt={"instructionRef": ref},
            artifact_gateway=TemporalOmnigentArtifactGateway(sessions),
        )
        assert message["data"]["content"][0]["text"] == body.decode()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_inline_instruction_keeps_existing_first_message_behavior():
    from unittest.mock import AsyncMock

    request = AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        correlationId="inline",
        idempotencyKey="inline-step",
        instructionRef="Keep the inline instruction.",
    )
    gateway = AsyncMock()
    message = await _build_omnigent_first_message(
        request=request, prompt={}, artifact_gateway=gateway
    )
    assert message["data"]["content"][0]["text"] == "Keep the inline instruction."
    gateway.read_text.assert_not_awaited()
