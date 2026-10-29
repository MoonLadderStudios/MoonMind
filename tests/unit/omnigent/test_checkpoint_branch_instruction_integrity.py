"""A durable branch claim does not admit corrupt instruction bytes."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from api_service.db.models import (
    TemporalArtifact,
    TemporalArtifactLink,
    TemporalArtifactUseClaim,
    WorkflowCheckpointBranch,
    WorkflowCheckpointBranchTurn,
)
from api_service.services.checkpoint_branch_turn_execution import (
    CheckpointBranchTurnExecutionOwner,
    CheckpointBranchTurnLaunchError,
)
from tests.artifact_access import access_for_test
from tests.unit.omnigent.test_checkpoint_branch_instruction_authority import (  # noqa: F401
    auth_mode,
)
from tests.unit.omnigent.test_checkpoint_branch_instruction_authority import (  # noqa: F401
    instruction_authority,
)


@pytest.mark.asyncio
async def test_corrupt_instruction_bytes_create_no_grant_or_launch(
    instruction_authority,  # noqa: F811
):
    fixture = instruction_authority
    artifact_id = fixture.ref.removeprefix("artifact://")
    async with fixture.sessions() as session:
        artifact = await session.get(TemporalArtifact, artifact_id)
        original_digest = artifact.sha256
        service = fixture.artifacts(session)
        service._store.write_bytes(
            artifact.storage_key,
            b"Corrupt bytes do not match the admitted instruction.",
            content_type=artifact.content_type,
        )

    client = SimpleNamespace(start_workflow=AsyncMock())
    async with fixture.sessions() as session:
        owner = CheckpointBranchTurnExecutionOwner(
            session,
            principal=fixture.producer,
            client=client,
            access=access_for_test(fixture.producer),
        )
        with pytest.raises(CheckpointBranchTurnLaunchError, match="bytes changed"):
            await owner._start_claimed_turn(
                branch=await session.get(WorkflowCheckpointBranch, "branch"),
                turn=await session.get(WorkflowCheckpointBranchTurn, "turn"),
                binding=SimpleNamespace(base_commit="base"),
            )
    client.start_workflow.assert_not_awaited()

    # Inspect durable effects from another session, not a rolled-back identity map.
    async with fixture.sessions() as session:
        artifact = await session.get(TemporalArtifact, artifact_id)
        assert artifact.sha256 == original_digest
        assert artifact.created_by_principal == fixture.producer
        for model in (TemporalArtifactLink, TemporalArtifactUseClaim):
            assert not (
                await session.scalars(
                    select(model).where(model.artifact_id == artifact_id)
                )
            ).all()
