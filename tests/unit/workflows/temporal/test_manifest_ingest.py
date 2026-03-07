"""Unit tests for bounded manifest-ingest runtime helpers."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker


@pytest.fixture(autouse=True)
def mock_temporal_client_adapter(monkeypatch):
    import uuid
    from dataclasses import dataclass
    from moonmind.workflows.temporal.client import TemporalClientAdapter

    @dataclass(frozen=True, slots=True)
    class DummyWorkflowStartResult:
        workflow_id: str
        run_id: str

    async def mock_start_workflow(self, *args, **kwargs):
        workflow_id = kwargs.get("workflow_id") or "mm:dummy"
        return DummyWorkflowStartResult(
            workflow_id=workflow_id, run_id=str(uuid.uuid4())
        )

    monkeypatch.setattr(TemporalClientAdapter, "start_workflow", mock_start_workflow)


from api_service.db.models import Base, MoonMindWorkflowState, TemporalWorkflowType
from moonmind.workflows.temporal import (
    LocalTemporalArtifactStore,
    TemporalArtifactRepository,
    TemporalArtifactService,
    TemporalExecutionService,
    TemporalManifestActivities,
    compile_manifest_plan,
    plan_nodes_to_runtime_nodes,
    start_manifest_child_runs,
)
from moonmind.workflows.temporal.manifest_ingest import (
    DEFAULT_MANIFEST_MAX_CONCURRENCY,
    apply_manifest_update,
    build_manifest_status_snapshot,
    initialize_manifest_projection,
    list_manifest_nodes,
)

MANIFEST_YAML = """
version: "v0"
metadata:
  name: "demo"
embeddings:
  provider: "openai"
vectorStore:
  type: "qdrant"
dataSources:
  - id: "repo-docs"
    type: "GithubRepositoryReader"
run:
  dryRun: false
""".strip()


@asynccontextmanager
async def temporal_db(tmp_path: Path):
    db_url = f"sqlite+aiosqlite:///{tmp_path}/manifest_ingest_runtime.db"
    engine = create_async_engine(db_url, future=True)
    session_maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield session_maker
    finally:
        await engine.dispose()


def _record(**overrides):
    now = datetime.now(UTC)
    payload = {
        "workflow_id": "mm:manifest-1",
        "run_id": "run-1",
        "workflow_type": TemporalWorkflowType.MANIFEST_INGEST,
        "state": MoonMindWorkflowState.EXECUTING,
        "owner_id": "user-1",
        "manifest_ref": "art_manifest_1",
        "plan_ref": "art_plan_1",
        "artifact_refs": ["art_manifest_1", "art_plan_1"],
        "parameters": {
            "requestedBy": {"type": "user", "id": "user-1"},
            "executionPolicy": {
                "failurePolicy": "best_effort",
                "maxConcurrency": 4,
            },
            "manifestNodes": [
                {"nodeId": "node-a", "state": "ready", "title": "A"},
                {
                    "nodeId": "node-b",
                    "state": "running",
                    "title": "B",
                    "childWorkflowId": "mm:run-2",
                    "childRunId": "run-2",
                },
                {
                    "nodeId": "node-c",
                    "state": "failed",
                    "title": "C",
                    "completedAt": now.isoformat(),
                    "resultArtifactRef": "art_result_3",
                },
            ],
        },
        "memo": {
            "manifest_phase": "executing",
            "summary_artifact_ref": "art_summary_1",
            "run_index_artifact_ref": "art_index_1",
            "checkpoint_artifact_ref": "art_checkpoint_1",
        },
        "paused": False,
        "updated_at": now,
    }
    payload.update(overrides)
    return SimpleNamespace(**payload)


def test_initialize_manifest_projection_sets_defaults_for_missing_fields() -> None:
    record = _record(
        owner_id=None,
        parameters={},
        memo={},
        state=MoonMindWorkflowState.INITIALIZING,
        paused=False,
    )

    initialize_manifest_projection(record)

    assert record.parameters["requestedBy"] == {"type": "system", "id": "system"}
    assert record.parameters["executionPolicy"]["maxConcurrency"] == (
        DEFAULT_MANIFEST_MAX_CONCURRENCY
    )
    assert record.memo["manifest_phase"] == "initializing"
    assert record.memo["manifest_counts"] == {
        "pending": 0,
        "ready": 0,
        "running": 0,
        "succeeded": 0,
        "failed": 0,
        "canceled": 0,
    }


def test_build_manifest_status_snapshot_derives_counts_and_refs() -> None:
    record = _record()

    snapshot = build_manifest_status_snapshot(record)

    assert snapshot.workflow_id == "mm:manifest-1"
    assert snapshot.state == "executing"
    assert snapshot.phase == "executing"
    assert snapshot.max_concurrency == 4
    assert snapshot.failure_policy == "best_effort"
    assert snapshot.plan_artifact_ref == "art_plan_1"
    assert snapshot.summary_artifact_ref == "art_summary_1"
    assert snapshot.run_index_artifact_ref == "art_index_1"
    assert snapshot.checkpoint_artifact_ref == "art_checkpoint_1"
    assert snapshot.counts.ready == 1
    assert snapshot.counts.running == 1
    assert snapshot.counts.failed == 1


def test_update_manifest_returns_next_safe_point_and_updates_ref() -> None:
    record = _record()

    response = apply_manifest_update(
        record,
        update_name="UpdateManifest",
        new_manifest_artifact_ref="art_manifest_2",
        mode="REPLACE_FUTURE",
        max_concurrency=None,
        node_ids=None,
    )

    assert response["applied"] == "next_safe_point"
    assert record.manifest_ref == "art_manifest_2"
    assert record.parameters["manifestUpdate"]["mode"] == "REPLACE_FUTURE"
    assert "art_manifest_2" in record.artifact_refs


def test_set_concurrency_updates_execution_policy_immediately() -> None:
    record = _record()

    response = apply_manifest_update(
        record,
        update_name="SetConcurrency",
        new_manifest_artifact_ref=None,
        mode=None,
        max_concurrency=9,
        node_ids=None,
    )

    assert response["applied"] == "immediate"
    assert record.parameters["executionPolicy"]["maxConcurrency"] == 9


def test_pause_and_resume_toggle_paused_state_without_awaiting_external() -> None:
    record = _record()

    paused = apply_manifest_update(
        record,
        update_name="Pause",
        new_manifest_artifact_ref=None,
        mode=None,
        max_concurrency=None,
        node_ids=None,
    )
    resumed = apply_manifest_update(
        record,
        update_name="Resume",
        new_manifest_artifact_ref=None,
        mode=None,
        max_concurrency=None,
        node_ids=None,
    )

    assert paused["message"] == "Manifest ingest paused."
    assert resumed["message"] == "Manifest ingest resumed."
    assert record.paused is False
    assert record.state == MoonMindWorkflowState.EXECUTING


def test_cancel_nodes_marks_mutable_nodes_canceled_and_reports_rejections() -> None:
    record = _record()

    response = apply_manifest_update(
        record,
        update_name="CancelNodes",
        new_manifest_artifact_ref=None,
        mode=None,
        max_concurrency=None,
        node_ids=["node-a", "node-c"],
    )

    assert response["result"]["acceptedNodeIds"] == ["node-a"]
    assert response["result"]["rejectedNodeIds"] == ["node-c"]
    assert record.parameters["manifestNodes"][0]["state"] == "canceled"


def test_retry_nodes_moves_terminal_nodes_back_to_ready() -> None:
    record = _record()

    response = apply_manifest_update(
        record,
        update_name="RetryNodes",
        new_manifest_artifact_ref=None,
        mode=None,
        max_concurrency=None,
        node_ids=["node-c"],
    )

    assert response["result"]["acceptedNodeIds"] == ["node-c"]
    assert record.parameters["manifestNodes"][2]["state"] == "ready"
    assert record.parameters["manifestNodes"][2]["resultArtifactRef"] is None


def test_list_manifest_nodes_supports_state_filter_and_cursor() -> None:
    record = _record(
        parameters={
            "requestedBy": {"type": "user", "id": "user-1"},
            "manifestNodes": [
                {"nodeId": "node-a", "state": "ready"},
                {"nodeId": "node-b", "state": "running"},
                {"nodeId": "node-c", "state": "running"},
            ],
        }
    )

    first_page = list_manifest_nodes(record, state="running", cursor=None, limit=1)
    second_page = list_manifest_nodes(
        record,
        state="running",
        cursor=first_page.next_cursor,
        limit=1,
    )

    assert first_page.count == 2
    assert [item.node_id for item in first_page.items] == ["node-b"]
    assert first_page.next_cursor
    assert [item.node_id for item in second_page.items] == ["node-c"]


def test_list_manifest_nodes_rejects_invalid_cursor() -> None:
    record = _record()

    with pytest.raises(ValueError, match="Invalid cursor"):
        list_manifest_nodes(record, state=None, cursor="not-base64", limit=10)


def test_compile_manifest_plan_produces_stable_node_ids() -> None:
    requested_by = {"type": "user", "id": "user-1"}
    execution_policy = {"failurePolicy": "fail_fast", "maxConcurrency": 3}

    first = compile_manifest_plan(
        manifest_ref="art_manifest_1",
        manifest_payload=MANIFEST_YAML,
        action="run",
        options={"dryRun": True},
        requested_by=requested_by,
        execution_policy=execution_policy,
    )
    second = compile_manifest_plan(
        manifest_ref="art_manifest_1",
        manifest_payload=MANIFEST_YAML,
        action="run",
        options={"dryRun": True},
        requested_by=requested_by,
        execution_policy=execution_policy,
    )

    assert first.manifest_digest == second.manifest_digest
    assert [node.node_id for node in first.nodes] == [
        node.node_id for node in second.nodes
    ]


@pytest.mark.asyncio
async def test_manifest_activities_compile_plan_and_write_summary(
    tmp_path: Path,
) -> None:
    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            artifact_service = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )
            manifest_activities = TemporalManifestActivities(
                artifact_service=artifact_service
            )
            artifact, _upload = await artifact_service.create(
                principal="user-1",
                content_type="application/yaml",
            )
            completed = await artifact_service.write_complete(
                artifact_id=artifact.artifact_id,
                principal="user-1",
                payload=(MANIFEST_YAML + "\n").encode("utf-8"),
                content_type="application/yaml",
            )

            manifest_text = await manifest_activities.manifest_read(
                principal="user-1",
                manifest_ref=completed.artifact_id,
            )
            compile_result = await manifest_activities.manifest_compile(
                principal="user-1",
                manifest_ref=completed.artifact_id,
                manifest_payload=manifest_text,
                action="run",
                options={"dryRun": True},
                requested_by={"type": "user", "id": "user-1"},
                execution_policy={"failurePolicy": "fail_fast", "maxConcurrency": 2},
            )
            runtime_nodes = plan_nodes_to_runtime_nodes(
                compile_result.nodes,
                requested_by={"type": "user", "id": "user-1"},
            )
            (
                summary_ref,
                run_index_ref,
            ) = await manifest_activities.manifest_write_summary(
                principal="user-1",
                workflow_id="mm:manifest-1",
                state="executing",
                phase="executing",
                manifest_ref=completed.artifact_id,
                plan_ref=compile_result.plan_ref.artifact_id,
                nodes=[node.model_dump(by_alias=True) for node in runtime_nodes],
            )

            assert compile_result.plan_ref.artifact_id.startswith("art_")
            assert compile_result.manifest_digest.startswith("sha256:")
            assert runtime_nodes[0].node_id.startswith("node-")
            assert summary_ref.artifact_id.startswith("art_")
            assert run_index_ref.artifact_id.startswith("art_")


@pytest.mark.asyncio
async def test_start_manifest_child_runs_preserves_lineage_and_request_cancel_policy(
    tmp_path: Path,
) -> None:
    async with temporal_db(tmp_path) as session_maker:
        async with session_maker() as session:
            execution_service = TemporalExecutionService(session)
            parent = await execution_service.create_execution(
                workflow_type="MoonMind.ManifestIngest",
                owner_id=uuid4(),
                title="Manifest ingest",
                input_artifact_ref=None,
                plan_artifact_ref="art_plan_1",
                manifest_artifact_ref="art_manifest_1",
                failure_policy="fail_fast",
                initial_parameters={
                    "requestedBy": {"type": "user", "id": "user-1"},
                    "executionPolicy": {
                        "failurePolicy": "fail_fast",
                        "maxConcurrency": 2,
                    },
                },
                idempotency_key="manifest-parent-1",
            )
            nodes = plan_nodes_to_runtime_nodes(
                compile_manifest_plan(
                    manifest_ref="art_manifest_1",
                    manifest_payload=MANIFEST_YAML,
                    action="run",
                    options={},
                    requested_by={"type": "user", "id": "user-1"},
                    execution_policy={
                        "failurePolicy": "fail_fast",
                        "maxConcurrency": 2,
                    },
                ),
                requested_by={"type": "user", "id": "user-1"},
            )

            starts = await start_manifest_child_runs(
                execution_service=execution_service,
                parent_execution=parent,
                requested_by={"type": "user", "id": "user-1"},
                nodes=nodes,
                limit=1,
            )

            assert len(starts) == 1
            child = await execution_service.describe_execution(starts[0].workflow_id)
            assert child.parameters["manifestIngestWorkflowId"] == parent.workflow_id
            assert child.parameters["nodeId"] == nodes[0].node_id
            assert child.parameters["parentClosePolicy"] == "REQUEST_CANCEL"
