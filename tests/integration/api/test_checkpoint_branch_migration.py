"""Integration coverage for the consolidated checkpoint branch graph migration."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import (
    Base,
    TemporalExecutionCanonicalRecord,
    TemporalWorkflowType,
    WorkflowCheckpointBranchArtifact,
)
from api_service.services.checkpoint_branch_service import (
    CheckpointBranchService,
    build_branch_turn_launch_idempotency_key,
)

pytestmark = [pytest.mark.integration, pytest.mark.integration_ci]


def _load_migration_module():
    migration_path = (
        Path(__file__).resolve().parents[3]
        / "api_service"
        / "migrations"
        / "versions"
        / "333_checkpoint_branch_graph.py"
    )
    spec = importlib.util.spec_from_file_location(
        "mm_checkpoint_branch_graph",
        migration_path,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_checkpoint_branch_migration_creates_graph_and_idempotency_ledger(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration = _load_migration_module()
    engine = sa.create_engine("sqlite:///:memory:")
    metadata = sa.MetaData()
    sa.Table(
        "temporal_execution_sources",
        metadata,
        sa.Column("workflow_id", sa.String(255), primary_key=True),
    )
    metadata.create_all(engine)

    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO temporal_execution_sources (workflow_id) "
                "VALUES ('mm:wf-branch')"
            )
        )
        context = MigrationContext.configure(conn)
        monkeypatch.setattr(migration, "op", Operations(context))

        migration.upgrade()

        inspector = sa.inspect(conn)
        assert {
            "workflow_checkpoint_branches",
            "workflow_checkpoint_branch_turns",
            "workflow_checkpoint_branch_git_bindings",
            "workflow_checkpoint_branch_artifacts",
            "workflow_checkpoint_branch_operations",
        }.issubset(set(inspector.get_table_names()))

        conn.execute(
            sa.text(
                "INSERT INTO workflow_checkpoint_branches "
                "(branch_id, workflow_id, root_workflow_id, source_run_id, "
                "source_checkpoint_boundary, source_checkpoint_ref, label, "
                "workspace_policy, runtime_context_policy, idempotency_key) "
                "VALUES ('cbr_test', 'mm:wf-branch', 'mm:wf-branch', "
                "'run-branch', 'after_execution', "
                "'artifact://checkpoints/after-implement', 'Branch', "
                "'apply_previous_execution_diff_to_clean_baseline', "
                "'fresh_agent_run', 'mm-1091:create')"
            )
        )
        row = conn.execute(
            sa.text(
                "SELECT state, branch_kind FROM workflow_checkpoint_branches "
                "WHERE branch_id = 'cbr_test'"
            )
        ).one()
        assert row.state == "created"
        assert row.branch_kind == "root"

        conn.execute(
            sa.text(
                "INSERT INTO workflow_checkpoint_branch_operations "
                "(operation_id, workflow_id, branch_id, operation, "
                "idempotency_key, request_digest, response_payload) "
                "VALUES (:operation_id, 'mm:wf-branch', 'cbr_test', "
                "'checkpoint_branch.create', 'mm-1091:create', "
                "'sha256:request', '{}')"
            ),
            {"operation_id": uuid4().hex},
        )

        with pytest.raises(IntegrityError):
            conn.execute(
                sa.text(
                    "INSERT INTO workflow_checkpoint_branch_operations "
                    "(operation_id, workflow_id, branch_id, operation, "
                    "idempotency_key, request_digest, response_payload) "
                    "VALUES (:operation_id, 'mm:wf-branch', 'cbr_test', "
                    "'checkpoint_branch.create', 'mm-1091:create', "
                    "'sha256:request', '{}')"
                ),
                {"operation_id": uuid4().hex},
            )


@pytest.mark.asyncio
async def test_checkpoint_branch_launch_persists_minimum_artifact_refs_without_duplicates(
    tmp_path,
) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/launch.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_maker() as session:
        session.add(
            TemporalExecutionCanonicalRecord(
                workflow_id="mm:wf-branch",
                run_id="run-branch",
                workflow_type=TemporalWorkflowType.USER_WORKFLOW,
                entry="api",
            )
        )
        await session.commit()
        service = CheckpointBranchService(session)
        graph = await service.create_branch_graph(
            {
                "branchId": "cbr-integration",
                "source": {
                    "workflowId": "mm:wf-branch",
                    "runId": "run-branch",
                    "logicalStepId": "implement",
                    "sourceExecutionOrdinal": 2,
                    "checkpointBoundary": "after_execution",
                    "checkpointRef": "artifact://checkpoints/after-implement",
                    "checkpointDigest": "sha256:checkpoint",
                },
                "label": "Integration branch",
                "workspacePolicy": "apply_previous_execution_diff_to_clean_baseline",
                "runtimeContextPolicy": "fresh_agent_run",
                "instructionRef": "artifact://instructions/integration",
                "instructionDigest": "sha256:instructions",
                "idempotencyKey": "mm-1100:cbr-integration:create",
            }
        )
        turn_id = graph.turns[0].branch_turn_id
        launch_key = build_branch_turn_launch_idempotency_key(
            workflow_id="mm:wf-branch",
            branch_id="cbr-integration",
            branch_turn_id=turn_id,
        )
        launch_args = {
            "workflow_id": "mm:wf-branch",
            "branch_id": "cbr-integration",
            "branch_turn_id": turn_id,
            "context_bundle_ref": "artifact://context/integration",
            "step_execution_manifest_ref": "artifact://manifest/integration",
            "checkpoint_ref": "artifact://checkpoint/integration",
            "diagnostics_ref": "artifact://diagnostics/integration",
            "agent_request_ref": "artifact://agent-request/integration",
            "agent_result_ref": "artifact://agent-result/integration",
            "created_step_execution_id": (
                "mm:wf-branch:run-branch:implement:execution:3"
            ),
            "idempotency_key": launch_key,
        }

        await service.launch_turn(**launch_args)
        await service.launch_turn(**launch_args)
        await session.commit()

        artifacts = (
            await session.execute(
                select(WorkflowCheckpointBranchArtifact).where(
                    WorkflowCheckpointBranchArtifact.branch_turn_id == turn_id
                )
            )
        ).scalars().all()

    await engine.dispose()

    assert sorted(artifact.artifact_kind for artifact in artifacts) == [
        "input.branch_turn.instructions.md",
        "output.branch_turn.checkpoint.json",
        "output.branch_turn.diagnostics.json",
        "output.branch_turn.step_execution_manifest.json",
        "runtime.branch_turn.agent_request.json",
        "runtime.branch_turn.agent_result.json",
        "runtime.branch_turn.context_bundle.json",
    ]
