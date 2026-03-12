"""Unit tests for bounded manifest-ingest runtime helpers."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

import moonmind.workflows.temporal.manifest_ingest as manifest_ingest_module
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

DEFAULT_MANIFEST_MAX_CONCURRENCY = (
    manifest_ingest_module.DEFAULT_MANIFEST_MAX_CONCURRENCY
)
ManifestIngestValidationError = manifest_ingest_module.ManifestIngestValidationError
ManifestIngestWorkflow = manifest_ingest_module.ManifestIngestWorkflow
_apply_manifest_node_update = manifest_ingest_module._apply_manifest_node_update
_resolve_workflow_requested_by = manifest_ingest_module._resolve_workflow_requested_by
_runtime_manifest_nodes = manifest_ingest_module._runtime_manifest_nodes
apply_manifest_update = manifest_ingest_module.apply_manifest_update
build_manifest_status_snapshot = manifest_ingest_module.build_manifest_status_snapshot
initialize_manifest_projection = manifest_ingest_module.initialize_manifest_projection
list_manifest_nodes = manifest_ingest_module.list_manifest_nodes

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


def test_resolve_workflow_requested_by_rejects_owner_mismatch() -> None:
    with pytest.raises(
        ManifestIngestValidationError,
        match="immutable workflow owner metadata",
    ):
        _resolve_workflow_requested_by(
            {"requestedBy": {"type": "user", "id": "user-2"}},
            owner_id="user-1",
        )


def test_runtime_manifest_nodes_preserve_dependencies_and_requester() -> None:
    nodes = _runtime_manifest_nodes(
        [
            {
                "nodeId": "node-a",
                "title": "A",
                "sourceType": "github",
                "sourceId": "repo-docs",
                "requiredCapabilities": ["repo.read"],
                "runtimeHints": {"taskQueueClass": "activity_routed"},
                "dependencies": ["node-b"],
            }
        ],
        requested_by={"type": "user", "id": "user-1"},
    )

    assert nodes == [
        {
            "nodeId": "node-a",
            "state": "ready",
            "title": "A",
            "workflowType": "MoonMind.Run",
            "childWorkflowId": None,
            "childRunId": None,
            "resultArtifactRef": None,
            "requestedBy": {"type": "user", "id": "user-1"},
            "startedAt": None,
            "completedAt": None,
            "dependencies": ["node-b"],
            "runtimeHints": {"taskQueueClass": "activity_routed"},
            "requiredCapabilities": ["repo.read"],
            "sourceType": "github",
            "sourceId": "repo-docs",
        }
    ]


def test_apply_manifest_node_update_replace_future_preserves_started_nodes() -> None:
    updated = _apply_manifest_node_update(
        {
            "node-running": {"nodeId": "node-running", "state": "running"},
            "node-ready": {"nodeId": "node-ready", "state": "ready"},
        },
        updated_nodes=[
            {"nodeId": "node-running", "state": "ready"},
            {"nodeId": "node-fresh", "state": "ready"},
        ],
        mode="REPLACE_FUTURE",
    )

    assert updated == {
        "node-running": {"nodeId": "node-running", "state": "running"},
        "node-fresh": {"nodeId": "node-fresh", "state": "ready"},
    }


def test_manifest_workflow_run_uses_owner_principal_and_child_owner_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    activity_calls: list[tuple[str, dict[str, object]]] = []
    child_calls: list[dict[str, object]] = []

    monkeypatch.setattr(
        manifest_ingest_module.workflow,
        "info",
        lambda: SimpleNamespace(
            workflow_id="mm:manifest-1",
            run_id="run-1",
            search_attributes={"mm_owner_id": "user-1"},
        ),
    )

    async def fake_execute_activity(name: str, *, args, **_kwargs):
        payload = dict(args[0])
        activity_calls.append((name, payload))
        if name == "manifest_read":
            return MANIFEST_YAML
        if name == "manifest_compile":
            return {
                "plan_ref": "art_plan_1",
                "nodes": [
                    {
                        "nodeId": "node-a",
                        "title": "A",
                        "sourceType": "github",
                        "sourceId": "repo-docs",
                        "requiredCapabilities": [],
                        "runtimeHints": {},
                        "dependencies": [],
                    }
                ],
            }
        if name == "manifest_write_summary":
            return ("art_summary_1", "art_index_1")
        raise AssertionError(f"unexpected activity {name}")

    async def fake_execute_child_workflow(_name: str, *, args, **_kwargs):
        child_calls.append(dict(args[0]))
        return {"output_artifact_ref": "art_result_1"}

    async def fake_wait_condition(_predicate):
        return None

    monkeypatch.setattr(
        manifest_ingest_module.workflow,
        "execute_activity",
        fake_execute_activity,
    )
    monkeypatch.setattr(
        manifest_ingest_module.workflow,
        "execute_child_workflow",
        fake_execute_child_workflow,
    )
    monkeypatch.setattr(
        manifest_ingest_module.workflow,
        "wait_condition",
        fake_wait_condition,
    )

    workflow_instance = ManifestIngestWorkflow()
    result = asyncio.run(
        workflow_instance.run(
            {
                "manifestArtifactRef": "art_manifest_1",
                "requestedBy": {"type": "user", "id": "user-1"},
            }
        )
    )

    assert result["status"] == "succeeded"
    assert activity_calls[0] == (
        "manifest_read",
        {"principal": "user-1", "manifest_ref": "art_manifest_1"},
    )
    assert activity_calls[1][1]["principal"] == "user-1"
    assert activity_calls[2][1]["principal"] == "user-1"
    assert child_calls == [
        {
            "workflow_type": "MoonMind.Run",
            "owner_id": "user-1",
            "title": "A",
            "input_artifact_ref": "art_manifest_1",
            "plan_artifact_ref": "art_plan_1",
            "manifest_artifact_ref": None,
            "initial_parameters": {
                "manifestIngestWorkflowId": "mm:manifest-1",
                "manifestIngestRunId": "run-1",
                "manifestArtifactRef": "art_manifest_1",
                "nodeId": "node-a",
                "requestedBy": {"type": "user", "id": "user-1"},
                "runtimeHints": {
                    "manifestNodeState": "running",
                    "workflowType": "MoonMind.Run",
                },
                "parentClosePolicy": "REQUEST_CANCEL",
            },
        }
    ]


def test_manifest_workflow_cancel_nodes_cancels_running_tasks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeTask:
        def __init__(self) -> None:
            self.canceled = False

        def cancel(self) -> None:
            self.canceled = True

    monkeypatch.setattr(
        manifest_ingest_module.workflow,
        "info",
        lambda: SimpleNamespace(
            workflow_id="mm:manifest-1",
            run_id="run-1",
            search_attributes={"mm_owner_id": "user-1"},
        ),
    )

    workflow_instance = ManifestIngestWorkflow()
    workflow_instance._nodes = {
        "node-a": {"nodeId": "node-a", "state": "pending"},
        "node-b": {"nodeId": "node-b", "state": "running"},
        "node-c": {"nodeId": "node-c", "state": "failed"},
    }
    task = FakeTask()
    workflow_instance._running_tasks = {"node-b": task}

    response = asyncio.run(
        workflow_instance.cancel_nodes({"nodeIds": ["node-a", "node-b", "missing"]})
    )

    assert workflow_instance._nodes["node-a"]["state"] == "canceled"
    assert task.canceled is True
    assert response["result"] == {
        "acceptedNodeIds": ["node-a", "node-b"],
        "rejectedNodeIds": ["missing"],
    }


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
            assert "dataSources" in manifest_text
            compile_result = await manifest_activities.manifest_compile(
                principal="user-1",
                manifest_ref=completed.artifact_id,
                action="run",
                options={"dryRun": True},
                requested_by={"type": "user", "id": "user-1"},
                execution_policy={"failurePolicy": "fail_fast", "maxConcurrency": 2},
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
            )
            _summary_artifact, summary_payload = await artifact_service.read(
                artifact_id=summary_ref.artifact_id,
                principal="user-1",
                allow_restricted_raw=True,
            )
            _index_artifact, run_index_payload = await artifact_service.read(
                artifact_id=run_index_ref.artifact_id,
                principal="user-1",
                allow_restricted_raw=True,
            )
            summary_data = json.loads(summary_payload.decode("utf-8"))
            run_index_data = json.loads(run_index_payload.decode("utf-8"))

            assert compile_result.plan_ref.artifact_id.startswith("art_")
            assert compile_result.manifest_digest.startswith("sha256:")
            assert summary_ref.artifact_id.startswith("art_")
            assert run_index_ref.artifact_id.startswith("art_")
            assert summary_data["counts"]["ready"] == 1
            assert len(run_index_data["items"]) == 1


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
