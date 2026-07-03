from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import (
    Base,
    WorkflowCheckpointBranch,
    WorkflowCheckpointBranchArtifact,
    WorkflowCheckpointBranchGitBinding,
    WorkflowCheckpointBranchTurn,
)
from api_service.services.checkpoint_branches import (
    _existing_bindings_by_work_branch,
    prepare_checkpoint_branch_workspace,
)
from moonmind.workflows.checkpoint_branches import CheckpointBranchGitBindingError

pytestmark = [pytest.mark.asyncio]


def _binding_input(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "workflowId": "MM-1087 Workflow",
        "productBranchId": "cbr_MM-1090",
        "branchTurnId": "cbt_MM-1090_1",
        "sourceCheckpointRef": "artifact://checkpoint/root",
        "sourceCheckpointDigest": "sha256:checkpoint",
        "logicalStepId": "Implement MM-1090",
        "label": "Fix Git Isolation",
        "repository": "MoonLadderStudios/MoonMind",
        "baseBranch": "feature/mm-1087-source",
        "baseCommit": "abc1234",
        "workspacePolicy": "apply_previous_execution_diff_to_clean_baseline",
        "creationMode": "from_checkpoint_patch",
        "idempotencyKey": "MM-1090:MM-1087:checkpoint",
        "requestedWorkBranch": (
            "mm/mm-1087-workflow/implement-mm-1090/cp-12345678/"
            "cbr-mm-1090-fix-git-isolation"
        ),
    }
    payload.update(overrides)
    return payload


@pytest_asyncio.fixture()
async def session(tmp_path) -> AsyncSession:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/checkpoint.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async_session_maker = sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    async with async_session_maker() as db_session:
        yield db_session
    await engine.dispose()


async def test_prepare_checkpoint_branch_workspace_persists_binding_and_artifacts(
    session: AsyncSession,
) -> None:
    emitted: list[tuple[str, Mapping[str, Any], str]] = []

    async def write_artifact(
        artifact_kind: str,
        payload: Mapping[str, Any],
        content_type: str,
    ) -> tuple[str, str]:
        emitted.append((artifact_kind, payload, content_type))
        return f"artifact://MM-1090/{artifact_kind}", f"sha256:{artifact_kind}"

    result = await prepare_checkpoint_branch_workspace(
        session=session,
        binding_input=_binding_input(
            headCommit="def5678",
            patchRef="artifact://patches/checkpoint.patch",
            pullRequestUrl="https://github.test/moon/mind/pull/1",
        ),
        known_refs={"feature/mm-1087-source"},
        current_ref="feature/mm-1087-source",
        instruction_ref="artifact://MM-1090/input.branch_turn.instructions.md",
        instruction_digest="sha256:instructions",
        artifact_writer=write_artifact,
        root_workflow_id="MM-1087",
        source_run_id="run-MM-1087",
        source_execution_ordinal=1,
        step_execution_manifest_ref=(
            "artifact://MM-1090/output.branch_turn.step_execution_manifest.json"
        ),
        created_step_execution_id="step-MM-1090",
    )
    await session.commit()

    assert result.branch_id == "cbr_MM-1090"
    assert result.branch_turn_id == "cbt_MM-1090_1"
    assert result.git_work_branch.startswith("mm/mm-1087-workflow/")
    assert [item[0] for item in emitted] == [
        "runtime.branch.workspace_restore.json",
        "runtime.branch.git_binding.json",
    ]
    assert emitted[0][1]["productBranchId"] == "cbr_MM-1090"
    assert emitted[1][1]["productBranchId"] == "cbr_MM-1090"
    assert emitted[0][1]["workspacePolicy"] == (
        "apply_previous_execution_diff_to_clean_baseline"
    )

    branch = await session.get(WorkflowCheckpointBranch, "cbr_MM-1090")
    assert branch is not None
    assert branch.logical_step_id == "Implement MM-1090"
    assert branch.label == "Fix Git Isolation"
    assert branch.workspace_policy == "apply_previous_execution_diff_to_clean_baseline"
    assert branch.git_work_branch == result.git_work_branch
    assert branch.artifact_refs["workspace_restore"] == result.workspace_restore_ref
    assert branch.artifact_refs["git_binding"] == result.git_binding_ref
    assert branch.diagnostics["workspacePolicy"] == branch.workspace_policy

    turn = await session.get(WorkflowCheckpointBranchTurn, "cbt_MM-1090_1")
    assert turn is not None
    assert turn.branch_id == "cbr_MM-1090"
    assert turn.workspace_policy == branch.workspace_policy
    assert turn.git_work_branch == result.git_work_branch
    assert turn.workspace_restore_ref == result.workspace_restore_ref
    assert turn.git_binding_ref == result.git_binding_ref
    assert turn.step_execution_manifest_ref == (
        "artifact://MM-1090/output.branch_turn.step_execution_manifest.json"
    )

    binding = await session.get(WorkflowCheckpointBranchGitBinding, "cbr_MM-1090")
    assert binding is not None
    assert binding.work_branch == result.git_work_branch
    assert binding.branch_id != binding.work_branch
    assert binding.head_commit == "def5678"
    assert binding.patch_ref == "artifact://patches/checkpoint.patch"
    assert binding.pull_request_url == "https://github.test/moon/mind/pull/1"
    assert binding.binding_metadata["gitBindingArtifact"] == result.git_binding_ref
    assert binding.binding_metadata["ownership"]["idempotencyKey"] == (
        "MM-1090:MM-1087:checkpoint"
    )
    assert binding.binding_metadata["workspaceBaseline"]["baseCommit"] == "abc1234"

    artifacts = (
        await session.execute(select(WorkflowCheckpointBranchArtifact))
    ).scalars().all()
    assert {artifact.artifact_kind for artifact in artifacts} == {
        "runtime.branch.workspace_restore.json",
        "runtime.branch.git_binding.json",
    }


async def test_prepare_checkpoint_branch_workspace_reuses_matching_binding(
    session: AsyncSession,
) -> None:
    emitted_count = 0

    async def write_artifact(
        artifact_kind: str,
        payload: Mapping[str, Any],
        content_type: str,
    ) -> tuple[str, None]:
        nonlocal emitted_count
        emitted_count += 1
        return f"artifact://MM-1090/{emitted_count}/{artifact_kind}", None

    kwargs = {
        "session": session,
        "binding_input": _binding_input(),
        "known_refs": {"feature/mm-1087-source"},
        "current_ref": "feature/mm-1087-source",
        "instruction_ref": "artifact://MM-1090/input.branch_turn.instructions.md",
        "instruction_digest": "sha256:instructions",
        "artifact_writer": write_artifact,
    }

    first = await prepare_checkpoint_branch_workspace(**kwargs)
    second = await prepare_checkpoint_branch_workspace(**kwargs)

    assert second.git_work_branch == first.git_work_branch
    assert emitted_count == 4
    artifacts = (
        await session.execute(select(WorkflowCheckpointBranchArtifact))
    ).scalars().all()
    assert len(artifacts) == 2


async def test_prepare_checkpoint_branch_workspace_validates_input_before_query(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail_query(*args: object, **kwargs: object) -> dict[str, dict[str, str]]:
        raise AssertionError("input validation must run before database lookup")

    monkeypatch.setattr(
        "api_service.services.checkpoint_branches._existing_bindings_by_work_branch",
        fail_query,
    )

    async def write_artifact(
        artifact_kind: str,
        payload: Mapping[str, Any],
        content_type: str,
    ) -> tuple[str, None]:
        raise AssertionError("invalid input must fail before artifact emission")

    with pytest.raises(CheckpointBranchGitBindingError) as exc_info:
        await prepare_checkpoint_branch_workspace(
            session=session,
            binding_input={**_binding_input(), "repository": None},
            known_refs={"feature/mm-1087-source"},
            current_ref="feature/mm-1087-source",
            instruction_ref="artifact://MM-1090/input.branch_turn.instructions.md",
            instruction_digest="sha256:instructions",
            artifact_writer=write_artifact,
        )

    assert exc_info.value.failure_code == "invalid_binding"


@pytest.mark.parametrize(
    ("overrides", "failure_code"),
    [
        ({"resolvedBaseCommit": "def5678"}, "git_base_commit_mismatch"),
        (
            {
                "creationMode": "fresh_from_source_branch",
                "workspacePolicy": "restore_pre_execution",
            },
            "workspace_policy_incompatible",
        ),
        (
            {
                "creationMode": "external_provider_state",
                "workspacePolicy": "continue_from_previous_execution",
                "providerWorkspaceRef": "provider://workspace/123",
            },
            "provider_continuation_unsupported",
        ),
    ],
)
async def test_prepare_checkpoint_branch_workspace_fails_before_artifacts_for_unsafe_launch(
    session: AsyncSession,
    overrides: dict[str, object],
    failure_code: str,
) -> None:
    async def write_artifact(
        artifact_kind: str,
        payload: Mapping[str, Any],
        content_type: str,
    ) -> tuple[str, None]:
        raise AssertionError("unsafe input must fail before artifact emission")

    with pytest.raises(CheckpointBranchGitBindingError) as exc_info:
        await prepare_checkpoint_branch_workspace(
            session=session,
            binding_input=_binding_input(**overrides),
            known_refs={"feature/mm-1087-source"},
            current_ref="feature/mm-1087-source",
            instruction_ref="artifact://MM-1090/input.branch_turn.instructions.md",
            instruction_digest="sha256:instructions",
            artifact_writer=write_artifact,
        )

    assert exc_info.value.failure_code == failure_code


async def test_existing_bindings_match_repository_case_insensitively(
    session: AsyncSession,
) -> None:
    session.add(
        WorkflowCheckpointBranch(
            branch_id="cbr_MM-1090",
            workflow_id="MM-1087",
            source_checkpoint_boundary="after_execution",
            source_checkpoint_ref="artifact://checkpoint/root",
            workspace_policy="apply_previous_execution_diff_to_clean_baseline",
        )
    )
    session.add(
        WorkflowCheckpointBranchGitBinding(
            branch_id="cbr_MM-1090",
            repository="MoonLadderStudios/MoonMind",
            base_branch="feature/mm-1087-source",
            base_commit="abc1234",
            work_branch="mm/mm-1087/implement/cp-12345678/cbr-mm-1090",
            workspace_policy="apply_previous_execution_diff_to_clean_baseline",
            creation_mode="from_checkpoint_patch",
            binding_metadata={
                "ownership": {
                    "idempotencyKey": "MM-1090:MM-1087:checkpoint",
                    "baseBranch": "feature/mm-1087-source",
                    "baseCommit": "abc1234",
                    "workspacePolicy": (
                        "apply_previous_execution_diff_to_clean_baseline"
                    ),
                    "creationMode": "from_checkpoint_patch",
                }
            },
        )
    )
    await session.flush()

    bindings = await _existing_bindings_by_work_branch(
        session=session,
        repository="moonladderstudios/moonmind",
    )

    assert "mm/mm-1087/implement/cp-12345678/cbr-mm-1090" in bindings
    assert (
        bindings["mm/mm-1087/implement/cp-12345678/cbr-mm-1090"]["idempotencyKey"]
        == "MM-1090:MM-1087:checkpoint"
    )


async def test_prepare_checkpoint_branch_workspace_reuses_requested_work_branch_for_turn(
    session: AsyncSession,
) -> None:
    async def write_artifact(
        artifact_kind: str,
        payload: Mapping[str, Any],
        content_type: str,
    ) -> tuple[str, None]:
        return f"artifact://MM-1090/{artifact_kind}", None

    first = await prepare_checkpoint_branch_workspace(
        session=session,
        binding_input=_binding_input(),
        known_refs={"feature/mm-1087-source"},
        current_ref="feature/mm-1087-source",
        instruction_ref="artifact://MM-1090/input.branch_turn.instructions.md",
        instruction_digest="sha256:instructions",
        artifact_writer=write_artifact,
    )

    second = await prepare_checkpoint_branch_workspace(
        session=session,
        binding_input=_binding_input(
            branchTurnId="cbt_2",
            creationMode="from_checkpoint_worktree",
            idempotencyKey="MM-1090:continue",
            requestedWorkBranch=first.git_work_branch,
            workspacePolicy="continue_from_previous_execution",
        ),
        known_refs={"feature/mm-1087-source"},
        current_ref="feature/mm-1087-source",
        instruction_ref="artifact://MM-1090/input.branch_turn.instructions.md",
        instruction_digest="sha256:instructions",
        artifact_writer=write_artifact,
    )

    assert second.git_work_branch == first.git_work_branch


async def test_prepare_checkpoint_branch_workspace_rejects_mismatched_collision(
    session: AsyncSession,
) -> None:
    session.add(
        WorkflowCheckpointBranch(
            branch_id="cbr_other",
            workflow_id="MM-1087",
            source_checkpoint_boundary="after_execution",
            source_checkpoint_ref="artifact://checkpoint/other",
            workspace_policy="apply_previous_execution_diff_to_clean_baseline",
            git_repository="MoonLadderStudios/MoonMind",
            git_base_branch="feature/mm-1087-source",
            git_work_branch=(
                "mm/mm-1087-workflow/implement-mm-1090/cp-12345678/"
                "cbr-mm-1090-fix-git-isolation"
            ),
        )
    )
    session.add(
        WorkflowCheckpointBranchGitBinding(
            branch_id="cbr_other",
            repository="MoonLadderStudios/MoonMind",
            base_branch="feature/mm-1087-source",
            work_branch=(
                "mm/mm-1087-workflow/implement-mm-1090/cp-12345678/"
                "cbr-mm-1090-fix-git-isolation"
            ),
            workspace_policy="apply_previous_execution_diff_to_clean_baseline",
            creation_mode="from_checkpoint_patch",
        )
    )
    await session.flush()

    async def write_artifact(
        artifact_kind: str,
        payload: Mapping[str, Any],
        content_type: str,
    ) -> tuple[str, None]:
        raise AssertionError("collision must fail before artifact emission")

    with pytest.raises(CheckpointBranchGitBindingError) as exc_info:
        await prepare_checkpoint_branch_workspace(
            session=session,
            binding_input=_binding_input(),
            known_refs={"feature/mm-1087-source"},
            current_ref="feature/mm-1087-source",
            instruction_ref="artifact://MM-1090/input.branch_turn.instructions.md",
            instruction_digest="sha256:instructions",
            artifact_writer=write_artifact,
        )

    assert exc_info.value.failure_code == "git_branch_collision"
