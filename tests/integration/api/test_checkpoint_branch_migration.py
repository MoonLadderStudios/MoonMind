"""Integration coverage for the consolidated checkpoint branch graph migration."""

from __future__ import annotations

import asyncio
import importlib.util
import os
import shutil
import socket
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.api.routers.executions import _get_service, router
from api_service.auth_providers import get_current_user
from api_service.db.base import get_async_session
from api_service.db.models import (
    Base,
    TemporalExecutionCanonicalRecord,
    TemporalWorkflowType,
    WorkflowCheckpointBranch,
    WorkflowCheckpointBranchArtifact,
    WorkflowCheckpointBranchGitBinding,
    WorkflowCheckpointBranchOperation,
    WorkflowCheckpointBranchTurn,
)
from api_service.services.checkpoint_branch_service import (
    CheckpointBranchService,
    build_branch_turn_launch_idempotency_key,
)
from api_service.services.checkpoint_branch_turn_execution import (
    CheckpointBranchTurnExecutionOwner,
)

pytestmark = [pytest.mark.integration, pytest.mark.integration_ci]


@pytest.fixture
def checkpoint_branch_postgres_url():
    """Provide isolated PostgreSQL without a manually managed test service."""

    configured = os.getenv("MOONMIND_TEST_POSTGRES_URL", "").strip()
    if configured:
        if configured.startswith("postgresql://"):
            configured = configured.replace(
                "postgresql://", "postgresql+asyncpg://", 1
            )
        if not configured.startswith("postgresql+asyncpg://"):
            pytest.fail("MOONMIND_TEST_POSTGRES_URL must use PostgreSQL")
        yield configured
        return

    initdb_path = shutil.which("initdb")
    if initdb_path is None:
        candidates = sorted(Path("/usr/lib/postgresql").glob("*/bin/initdb"))
        initdb_path = str(candidates[-1]) if candidates else None
    if initdb_path is None:
        pytest.fail(
            "PostgreSQL test binaries are unavailable in the Python test image"
        )
    initdb = str(Path(initdb_path))
    pg_ctl = str(Path(initdb).with_name("pg_ctl"))
    data_root = Path(tempfile.mkdtemp(prefix="moonmind-checkpoint-postgres-"))
    data_dir = data_root / "data"
    log_path = data_root / "postgres.log"
    command_prefix: list[str] = []
    if os.geteuid() == 0:
        # docker-compose.test.yaml intentionally runs pytest as root, while
        # PostgreSQL refuses to initialize as root. Keep the isolated cluster
        # owned by the package-created postgres account in that path.
        shutil.chown(data_root, user="postgres", group="postgres")
        command_prefix = ["runuser", "--user", "postgres", "--"]
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    subprocess.run(
        [
            *command_prefix,
            initdb,
            "--pgdata",
            str(data_dir),
            "--username",
            "postgres",
            "--auth",
            "trust",
            "--encoding",
            "UTF8",
            "--no-locale",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        [
            *command_prefix,
            pg_ctl,
            "--pgdata",
            str(data_dir),
            "--log",
            str(log_path),
            "--options",
            f"-F -p {port} -h 127.0.0.1 -k {data_dir}",
            "--wait",
            "start",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    try:
        yield f"postgresql+asyncpg://postgres@127.0.0.1:{port}/postgres"
    finally:
        subprocess.run(
            [
                *command_prefix,
                pg_ctl,
                "--pgdata",
                str(data_dir),
                "--mode",
                "immediate",
                "--wait",
                "stop",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        shutil.rmtree(data_root, ignore_errors=True)


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
        claim_args = {
            "workflow_id": "mm:wf-branch",
            "branch_id": "cbr-integration",
            "branch_turn_id": turn_id,
            "context_bundle_ref": "artifact://context/integration",
            "step_execution_manifest_ref": "artifact://manifest/integration",
            "diagnostics_ref": "artifact://diagnostics/integration",
            "agent_request_ref": "artifact://agent-request/integration",
            "created_step_execution_id": (
                "mm:wf-branch:run-branch:implement:execution:3"
            ),
            "runtime_agent_run_id": "mm:wf-branch:agent:branch-turn",
            "execution_workflow_id": "checkpoint-branch-turn:integration",
            "launch_idempotency_key": launch_key,
        }

        await service.claim_turn_execution(**claim_args)
        await service.claim_turn_execution(**claim_args)
        finalize_args = {
            "workflow_id": "mm:wf-branch",
            "branch_id": "cbr-integration",
            "branch_turn_id": turn_id,
            "outcome": "succeeded",
            "agent_result_ref": "artifact://agent-result/integration",
            "diagnostics_ref": "artifact://terminal-diagnostics/integration",
            "checkpoint_ref": "artifact://checkpoint/integration",
            "checkpoint_digest": "sha256:terminal-checkpoint",
            "provider_session_id": "omnigent-session-integration",
            "output_refs": ["artifact://output/integration"],
            "terminal_disposition": "verification_pending",
        }
        await service.finalize_turn_execution(**finalize_args)
        await service.finalize_turn_execution(**finalize_args)
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
        "output.branch_turn.launch_diagnostics.json",
        "output.branch_turn.step_execution_manifest.json",
        "runtime.branch_turn.agent_request.json",
        "runtime.branch_turn.agent_result.json",
        "runtime.branch_turn.context_bundle.json",
    ]


@pytest.mark.asyncio
async def test_public_checkpoint_branch_launch_is_idempotent_under_postgres_race(
    monkeypatch: pytest.MonkeyPatch,
    checkpoint_branch_postgres_url: str,
) -> None:
    """Exercise the public launch ledger and owner under real row locking."""

    engine = create_async_engine(checkpoint_branch_postgres_url)
    assert engine.dialect.name == "postgresql"
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    suffix = uuid4().hex
    workflow_id = f"mm:wf-checkpoint-launch-race-{suffix}"
    run_id = f"run-checkpoint-launch-race-{suffix}"
    branch_id = f"cbr-launch-race-{suffix}"
    turn_id = f"cbt-launch-race-{suffix}"
    request_key = f"checkpoint-launch-race-{suffix}"
    user = SimpleNamespace(
        id=uuid4(),
        email="checkpoint-launch-race@example.test",
        is_superuser=True,
        roles=[],
    )
    source = TemporalExecutionCanonicalRecord(
        workflow_id=workflow_id,
        run_id=run_id,
        namespace="default",
        workflow_type=TemporalWorkflowType.USER_WORKFLOW,
        owner_id=str(user.id),
        owner_type="user",
        state="executing",
        entry="api",
        search_attributes={
            "mm_owner_id": str(user.id),
            "mm_owner_type": "user",
        },
        memo={},
        parameters={},
        artifact_refs=[],
    )
    async with sessions() as session:
        session.add(source)
        await session.commit()
        service = CheckpointBranchService(session)
        await service.create_branch_graph(
            {
                "branchId": branch_id,
                "branchTurnId": turn_id,
                "source": {
                    "workflowId": workflow_id,
                    "runId": run_id,
                    "logicalStepId": "implement",
                    "sourceExecutionOrdinal": 2,
                    "checkpointBoundary": "after_execution",
                    "checkpointRef": f"artifact://checkpoint/{suffix}",
                    "checkpointDigest": "sha256:" + "1" * 64,
                },
                "label": "PostgreSQL launch race",
                "workspacePolicy": (
                    "apply_previous_execution_diff_to_clean_baseline"
                ),
                "runtimeContextPolicy": "fresh_agent_run",
                "instructionRef": f"artifact://instruction/{suffix}",
                "instructionDigest": "sha256:" + "2" * 64,
                "idempotencyKey": f"graph-{suffix}",
            }
        )
        await service.configure_server_launch_authority(
            workflow_id=workflow_id,
            branch_id=branch_id,
            branch_turn_id=turn_id,
            repository="MoonLadderStudios/MoonMind",
            base_branch="main",
            base_commit="abc123",
            work_branch=f"feature/checkpoint-launch-race-{suffix}",
            provider_profile_ref="profile-race",
        )
        await session.commit()

    omnigent_payload = {
        "executionProfileRef": "profile-race",
        "launchPolicyRef": "codex-on-demand@1",
        "idempotencyKey": f"source-message-{suffix}",
        "sourceBranch": "main",
        "publicationState": "none",
    }
    omnigent_checkpoint = SimpleNamespace(
        workflow_id=workflow_id,
        step_execution_id=f"{workflow_id}:source-step",
        bridge_session_id=f"bridge-{suffix}",
        omnigent_session_id=None,
        execution_profile_ref="profile-race",
        launch_policy_ref="codex-on-demand@1",
        execution_plan_ref=None,
        idempotency_key=f"source-message-{suffix}",
        source_branch="main",
        publication_state="none",
        model_dump=lambda **_kwargs: dict(omnigent_payload),
    )
    checkpoint = SimpleNamespace(omnigent=omnigent_checkpoint)
    profile = SimpleNamespace(
        profile_id="profile-race",
        runtime_id="codex_cli",
        default_model="gpt-test",
        default_effort="high",
    )
    policy_snapshot = {
        "boundaries": {"execution": {"profileRef": "omnigent-codex@1"}}
    }

    async def validated_authority(_owner, **_kwargs):
        return checkpoint, profile, policy_snapshot

    async def stable_artifact(
        _owner,
        *,
        content_type: str,
        payload: bytes,
        kind: str,
        branch_turn_id: str,
    ) -> str:
        del content_type, payload
        return f"artifact://postgres-race/{branch_turn_id}/{kind}"

    started_workflow_ids: set[str] = set()
    start_attempts = 0

    async def record_start(_owner, *, branch, turn, binding) -> None:
        nonlocal start_attempts
        del branch, binding
        start_attempts += 1
        started_workflow_ids.add(turn.diagnostics["executionWorkflowId"])

    monkeypatch.setattr(
        CheckpointBranchTurnExecutionOwner,
        "_validate_source_authority",
        validated_authority,
    )
    monkeypatch.setattr(
        CheckpointBranchTurnExecutionOwner,
        "_write_artifact",
        stable_artifact,
    )
    monkeypatch.setattr(
        CheckpointBranchTurnExecutionOwner,
        "_start_claimed_turn",
        record_start,
    )

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[_get_service] = lambda: SimpleNamespace(
        describe_execution=AsyncMock(return_value=source)
    )

    async def session_override():
        async with sessions() as session:
            yield session

    app.dependency_overrides[get_async_session] = session_override
    user_dependencies = {
        dependency.call
        for route_item in router.routes
        if route_item.dependant is not None
        for dependency in route_item.dependant.dependencies
        if getattr(dependency.call, "__name__", "") == "_current_user_fallback"
    } or {get_current_user()}
    for dependency in user_dependencies:
        app.dependency_overrides[dependency] = lambda: user

    endpoint = (
        f"/api/executions/{workflow_id}/checkpoint-branches/{branch_id}/"
        f"turns/{turn_id}/launch"
    )
    body = {"idempotencyKey": request_key, "expectedBranchHeadVersion": 1}
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        first, second = await asyncio.gather(
            client.post(endpoint, json=body),
            client.post(endpoint, json=body),
        )

    assert first.status_code == second.status_code == 200
    assert first.json()["createdStepExecutionId"] == second.json()[
        "createdStepExecutionId"
    ]
    assert first.json()["runtimeAgentRunId"] == second.json()[
        "runtimeAgentRunId"
    ]
    assert len(started_workflow_ids) == 1
    assert start_attempts == 2

    async with sessions() as session:
        turn = await session.get(WorkflowCheckpointBranchTurn, turn_id)
        operations = list(
            (
                await session.execute(
                    select(WorkflowCheckpointBranchOperation).where(
                        WorkflowCheckpointBranchOperation.workflow_id == workflow_id,
                        WorkflowCheckpointBranchOperation.idempotency_key == request_key,
                    )
                )
            ).scalars()
        )
        assert turn is not None
        assert turn.created_step_execution_id == first.json()[
            "createdStepExecutionId"
        ]
        assert len(operations) == 1

        await session.execute(
            delete(WorkflowCheckpointBranchOperation).where(
                WorkflowCheckpointBranchOperation.workflow_id == workflow_id
            )
        )
        await session.execute(
            delete(WorkflowCheckpointBranchArtifact).where(
                WorkflowCheckpointBranchArtifact.branch_id == branch_id
            )
        )
        await session.execute(
            delete(WorkflowCheckpointBranchGitBinding).where(
                WorkflowCheckpointBranchGitBinding.branch_id == branch_id
            )
        )
        await session.execute(
            delete(WorkflowCheckpointBranchTurn).where(
                WorkflowCheckpointBranchTurn.branch_id == branch_id
            )
        )
        await session.execute(
            delete(WorkflowCheckpointBranch).where(
                WorkflowCheckpointBranch.branch_id == branch_id
            )
        )
        await session.execute(
            delete(TemporalExecutionCanonicalRecord).where(
                TemporalExecutionCanonicalRecord.workflow_id == workflow_id
            )
        )
        await session.commit()

    app.dependency_overrides.clear()
    await engine.dispose()


@pytest.mark.asyncio
async def test_checkpoint_branch_compare_and_promotion_audit_evidence_persists(
    tmp_path,
) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/promotion.db")
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
        for branch_id in ("cbr-left", "cbr-right"):
            session.add(
                WorkflowCheckpointBranch(
                    branch_id=branch_id,
                    workflow_id="mm:wf-branch",
                    root_workflow_id="mm:wf-branch",
                    source_run_id="run-branch",
                    source_checkpoint_boundary="after_execution",
                    source_checkpoint_ref="artifact://checkpoints/base",
                    label=branch_id,
                    workspace_policy="apply_previous_execution_diff_to_clean_baseline",
                    runtime_context_policy="fresh_agent_run",
                    state="succeeded",
                    branch_kind="root",
                    current_head_step_execution_id=f"{branch_id}:execution:1",
                    current_head_checkpoint_ref=f"artifact://checkpoints/{branch_id}",
                    current_head_commit=f"{branch_id}-head",
                )
            )
        comparison_refs = {
            "output.branch_comparison.summary.json": (
                "artifact://checkpoint-branch-comparisons/mm:wf-branch/cbr-left/"
                "digest/output.branch_comparison.summary.json"
            ),
            "output.branch_comparison.left_diff.patch": (
                "artifact://checkpoint-branch-comparisons/mm:wf-branch/cbr-left/"
                "digest/output.branch_comparison.left_diff.patch"
            ),
            "output.branch_comparison.right_diff.patch": (
                "artifact://checkpoint-branch-comparisons/mm:wf-branch/cbr-left/"
                "digest/output.branch_comparison.right_diff.patch"
            ),
            "output.branch_comparison.range_diff.patch": (
                "artifact://checkpoint-branch-comparisons/mm:wf-branch/cbr-left/"
                "digest/output.branch_comparison.range_diff.patch"
            ),
            "output.branch_comparison.diagnostics.json": (
                "artifact://checkpoint-branch-comparisons/mm:wf-branch/cbr-left/"
                "digest/output.branch_comparison.diagnostics.json"
            ),
        }
        session.add(
            WorkflowCheckpointBranchOperation(
                workflow_id="mm:wf-branch",
                branch_id="cbr-left",
                operation="checkpoint_branch.compare",
                idempotency_key="mm-1103:compare",
                request_digest="sha256:compare-request",
                response_payload={
                    "recordType": "checkpoint_branch_comparison",
                    "branchId": "cbr-left",
                    "againstBranchId": "cbr-right",
                    "artifactRefs": comparison_refs,
                    "diagnosticsRefs": [
                        comparison_refs[
                            "output.branch_comparison.diagnostics.json"
                        ]
                    ],
                },
            )
        )
        for artifact_kind, artifact_ref in comparison_refs.items():
            session.add(
                WorkflowCheckpointBranchArtifact(
                    branch_id="cbr-left",
                    artifact_kind=artifact_kind,
                    artifact_ref=artifact_ref,
                    digest="sha256:compare",
                )
            )
        session.add(
            WorkflowCheckpointBranchOperation(
                workflow_id="mm:wf-branch",
                branch_id="cbr-left",
                operation="checkpoint_branch.promote",
                idempotency_key="mm-1103:promote-rejected",
                request_digest="sha256:promote-rejected",
                response_payload={
                    "outcome": "rejected",
                    "code": "side_effect_policy_blocked",
                    "reason": "gate_evidence_not_passing",
                    "branchId": "cbr-left",
                },
            )
        )
        session.add(
            WorkflowCheckpointBranchArtifact(
                branch_id="cbr-left",
                artifact_kind="output.branch_promotion.record.json",
                artifact_ref="artifact://checkpoint-branch-promotions/record",
                digest="sha256:promotion",
            )
        )
        session.add(
            WorkflowCheckpointBranchArtifact(
                branch_id="cbr-left",
                artifact_kind="output.branch_promotion.downstream_invalidation.json",
                artifact_ref="artifact://checkpoint-branch-promotions/invalidation",
                digest="sha256:promotion",
            )
        )
        await session.commit()

        operations = (
            await session.execute(
                select(WorkflowCheckpointBranchOperation).order_by(
                    WorkflowCheckpointBranchOperation.idempotency_key
                )
            )
        ).scalars().all()
        artifacts = (
            await session.execute(
                select(WorkflowCheckpointBranchArtifact).where(
                    WorkflowCheckpointBranchArtifact.branch_id == "cbr-left"
                )
            )
        ).scalars().all()

    await engine.dispose()

    assert [operation.operation for operation in operations] == [
        "checkpoint_branch.compare",
        "checkpoint_branch.promote",
    ]
    assert operations[0].response_payload["artifactRefs"] == comparison_refs
    assert operations[1].response_payload["outcome"] == "rejected"
    assert sorted(artifact.artifact_kind for artifact in artifacts) == [
        "output.branch_comparison.diagnostics.json",
        "output.branch_comparison.left_diff.patch",
        "output.branch_comparison.range_diff.patch",
        "output.branch_comparison.right_diff.patch",
        "output.branch_comparison.summary.json",
        "output.branch_promotion.downstream_invalidation.json",
        "output.branch_promotion.record.json",
    ]
