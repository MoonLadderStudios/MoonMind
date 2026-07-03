"""Integration boundary coverage for checkpoint branch git preparation."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import (
    Base,
    WorkflowCheckpointBranch,
    WorkflowCheckpointBranchGitBinding,
    WorkflowCheckpointBranchTurn,
)
from api_service.services.checkpoint_branches import prepare_checkpoint_branch_workspace
from moonmind.workflows.checkpoint_branches import CheckpointBranchGitBindingError

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]


async def test_checkpoint_branch_preparation_persists_isolated_binding_before_launch(
    tmp_path,
) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/branch.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    emitted: list[tuple[str, Mapping[str, Any], str]] = []

    async def write_artifact(
        artifact_kind: str,
        payload: Mapping[str, Any],
        content_type: str,
    ) -> tuple[str, str]:
        emitted.append((artifact_kind, payload, content_type))
        return f"artifact://prepared/{artifact_kind}", f"sha256:{artifact_kind}"

    async with session_factory() as session:
        result = await prepare_checkpoint_branch_workspace(
            session=session,
            binding_input={
                "workflowId": "mm:wf-branch",
                "productBranchId": "cbr_1101",
                "branchTurnId": "cbt_1101",
                "sourceCheckpointRef": "artifact://checkpoints/after-implement",
                "sourceCheckpointDigest": "sha256:checkpoint",
                "logicalStepId": "implement",
                "label": "MM-1101",
                "repository": "MoonLadderStudios/MoonMind",
                "baseBranch": "feature/mm-1101-source",
                "baseCommit": "abc1234",
                "resolvedBaseCommit": "abc1234",
                "workspacePolicy": "apply_previous_execution_diff_to_clean_baseline",
                "creationMode": "from_checkpoint_patch",
                "idempotencyKey": "mm-1101:create",
            },
            known_refs={"feature/mm-1101-source"},
            current_ref="feature/mm-1101-source",
            instruction_ref="artifact://instructions/mm-1101",
            instruction_digest="sha256:instructions",
            artifact_writer=write_artifact,
            root_workflow_id="mm:wf-branch",
            source_run_id="run-branch",
            source_execution_ordinal=2,
        )
        await session.commit()

        branch = await session.get(WorkflowCheckpointBranch, "cbr_1101")
        turn = await session.get(WorkflowCheckpointBranchTurn, "cbt_1101")
        binding = await session.get(WorkflowCheckpointBranchGitBinding, "cbr_1101")

    await engine.dispose()

    assert branch is not None
    assert turn is not None
    assert binding is not None
    assert result.git_work_branch.startswith("mm/mm-wf-branch/implement/cp-")
    assert binding.work_branch == result.git_work_branch
    assert binding.work_branch != binding.branch_id
    assert branch.git_work_branch == result.git_work_branch
    assert turn.git_work_branch == result.git_work_branch
    assert branch.diagnostics["workspaceBaseline"] == turn.diagnostics[
        "workspaceBaseline"
    ]
    assert binding.binding_metadata["workspaceBaseline"] == branch.diagnostics[
        "workspaceBaseline"
    ]
    assert [item[0] for item in emitted] == [
        "runtime.branch.workspace_restore.json",
        "runtime.branch.git_binding.json",
    ]


@pytest.mark.parametrize(
    ("overrides", "known_refs", "current_ref", "failure_code"),
    [
        ({"requestedWorkBranch": "main"}, {"feature/mm-1101-source"}, "feature/mm-1101-source", "protected_branch_ref"),
        ({}, {"feature/mm-1101-source"}, "HEAD", "detached_head"),
        ({"baseBranch": "missing"}, {"feature/mm-1101-source"}, "feature/mm-1101-source", "unknown_ref"),
        ({"resolvedBaseCommit": "def5678"}, {"feature/mm-1101-source"}, "feature/mm-1101-source", "git_base_commit_mismatch"),
        (
            {
                "workspacePolicy": "continue_from_previous_execution",
                "creationMode": "from_checkpoint_patch",
            },
            {"feature/mm-1101-source"},
            "feature/mm-1101-source",
            "workspace_policy_incompatible",
        ),
        (
            {
                "workspacePolicy": "continue_from_previous_execution",
                "creationMode": "external_provider_state",
                "providerWorkspaceRef": "provider://workspace/123",
            },
            {"feature/mm-1101-source"},
            "feature/mm-1101-source",
            "provider_continuation_unsupported",
        ),
    ],
)
async def test_checkpoint_branch_preparation_fails_closed_before_artifact_emission(
    tmp_path,
    overrides: dict[str, object],
    known_refs: set[str],
    current_ref: str,
    failure_code: str,
) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/branch.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    async def write_artifact(
        artifact_kind: str,
        payload: Mapping[str, Any],
        content_type: str,
    ) -> tuple[str, str]:
        raise AssertionError("unsafe launch must fail before artifact emission")

    binding_input: dict[str, object] = {
        "workflowId": "mm:wf-branch",
        "productBranchId": "cbr_1101",
        "branchTurnId": "cbt_1101",
        "sourceCheckpointRef": "artifact://checkpoints/after-implement",
        "sourceCheckpointDigest": "sha256:checkpoint",
        "logicalStepId": "implement",
        "label": "MM-1101",
        "repository": "MoonLadderStudios/MoonMind",
        "baseBranch": "feature/mm-1101-source",
        "baseCommit": "abc1234",
        "resolvedBaseCommit": "abc1234",
        "workspacePolicy": "apply_previous_execution_diff_to_clean_baseline",
        "creationMode": "from_checkpoint_patch",
        "idempotencyKey": "mm-1101:create",
    }
    binding_input.update(overrides)

    async with session_factory() as session:
        with pytest.raises(CheckpointBranchGitBindingError) as exc_info:
            await prepare_checkpoint_branch_workspace(
                session=session,
                binding_input=binding_input,
                known_refs=known_refs,
                current_ref=current_ref,
                instruction_ref="artifact://instructions/mm-1101",
                instruction_digest="sha256:instructions",
                artifact_writer=write_artifact,
            )

    await engine.dispose()
    assert exc_info.value.failure_code == failure_code
