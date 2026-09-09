import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from moonmind.schemas.temporal_activity_models import ArtifactReadInput, ArtifactWriteCompleteInput
from moonmind.workflows.temporal.artifacts import TemporalArtifactActivities

@pytest.fixture
def mock_service():
    service = AsyncMock()
    # Mock read
    mock_artifact = AsyncMock()
    service.read.return_value = (mock_artifact, b"test payload")
    
    # Mock write_complete
    service.write_complete.return_value = mock_artifact
    return service

@pytest.fixture
def activities(mock_service):
    return TemporalArtifactActivities(mock_service)

@pytest.fixture
def patch_build_artifact_ref():
    with patch("moonmind.workflows.temporal.artifacts.build_artifact_ref") as mock_build:
        mock_build.return_value = {"artifact_id": "test-id"}
        yield mock_build

@pytest.mark.asyncio
async def test_artifact_read_pydantic_model(activities, mock_service):
    request = ArtifactReadInput(
        artifact_ref="test-ref",
        principal="test-principal"
    )
    payload = await activities.artifact_read(request)
    assert payload == b"test payload"
    mock_service.read.assert_called_once_with(artifact_id="test-ref", principal="test-principal")

@pytest.mark.asyncio
async def test_artifact_read_legacy_dict_validation_path(activities, mock_service):
    request = {
        "artifact_ref": "test-ref",
        "principal": "test-principal"
    }
    payload = await activities.artifact_read(request)
    assert payload == b"test payload"
    mock_service.read.assert_called_once_with(artifact_id="test-ref", principal="test-principal")

@pytest.mark.asyncio
async def test_artifact_write_complete_pydantic_model(activities, mock_service, patch_build_artifact_ref):
    request = ArtifactWriteCompleteInput(
        artifact_id="test-id",
        payload=b"test payload",
        principal="test-principal",
        content_type="text/plain"
    )
    await activities.artifact_write_complete(request)
    mock_service.write_complete.assert_called_once_with(
        artifact_id="test-id",
        principal="test-principal",
        payload=b"test payload",
        content_type="text/plain"
    )

@pytest.mark.asyncio
async def test_artifact_write_complete_legacy_dict(activities, mock_service, patch_build_artifact_ref):
    request = {
        "artifact_id": "test-id",
        "payload": "dGVzdCBwYXlsb2Fk", # base64 for "test payload"
        "principal": "test-principal",
        "content_type": "text/plain"
    }
    await activities.artifact_write_complete(request)
    mock_service.write_complete.assert_called_once_with(
        artifact_id="test-id",
        principal="test-principal",
        payload=b"test payload",
        content_type="text/plain"
    )

@pytest.mark.asyncio
async def test_artifact_write_complete_payload_roundtrip_legacy_list_ints(activities, mock_service, patch_build_artifact_ref):
    request = {
        "artifact_id": "test-id",
        "payload": list(b"test payload"),
        "principal": "test-principal",
        "content_type": "text/plain"
    }
    await activities.artifact_write_complete(request)
    mock_service.write_complete.assert_called_once_with(
        artifact_id="test-id",
        principal="test-principal",
        payload=b"test payload",
        content_type="text/plain"
    )

@pytest.mark.asyncio
async def test_execution_record_terminal_state_indexes_run_digest_best_effort(
    activities,
    monkeypatch,
):
    """MM-762: terminal-state activity triggers Plane B run digest writeback."""

    import api_service.db.base as db_base
    import moonmind.workflows.temporal.service as temporal_service

    record = SimpleNamespace(
        workflow_id="mm:run:123",
        run_id="temporal-run-1",
        state="completed",
        close_status="completed",
    )
    service_calls: list[dict[str, object]] = []
    digest_calls: list[str] = []

    class _SessionContext:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class _TemporalExecutionService:
        def __init__(self, session):
            self.session = session

        async def record_terminal_state(self, **kwargs):
            service_calls.append(dict(kwargs))
            return record

    async def _fake_digest_writeback(target):
        digest_calls.append(target.workflow_id)

    monkeypatch.setattr(db_base, "get_async_session_context", lambda: _SessionContext())
    monkeypatch.setattr(
        temporal_service,
        "TemporalExecutionService",
        _TemporalExecutionService,
    )
    monkeypatch.setattr(
        activities,
        "_write_run_digest_best_effort",
        _fake_digest_writeback,
    )

    result = await activities.execution_record_terminal_state(
        {
            "workflowId": "mm:run:123",
            "state": "completed",
            "closeStatus": "completed",
            "summary": "Workflow completed successfully",
            "finishOutcomeCode": "PUBLISHED_PR",
            "finishSummary": {
                "schemaVersion": "v1",
                "finishOutcome": {
                    "code": "PUBLISHED_PR",
                    "stage": "publish",
                    "reason": "published pull request",
                },
            },
        }
    )

    assert service_calls == [
        {
            "workflow_id": "mm:run:123",
            "state": "completed",
            "close_status": "completed",
            "summary": "Workflow completed successfully",
            "error_category": None,
            "finish_outcome_code": "PUBLISHED_PR",
            "finish_summary": {
                "schemaVersion": "v1",
                "finishOutcome": {
                    "code": "PUBLISHED_PR",
                    "stage": "publish",
                    "reason": "published pull request",
                },
            },
        }
    ]
    assert digest_calls == ["mm:run:123"]
    assert result == {
        "workflowId": "mm:run:123",
        "state": "completed",
        "closeStatus": "completed",
    }


@pytest.mark.asyncio
async def test_execution_terminal_projection_lag_does_not_overwrite_success(
    activities,
    monkeypatch,
):
    """Internally-started children may close before their projection row exists."""

    import api_service.db.base as db_base
    import moonmind.workflows.temporal.service as temporal_service

    class _SessionContext:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class _TemporalExecutionService:
        def __init__(self, session):
            self.session = session

        async def record_terminal_state(self, **_kwargs):
            raise temporal_service.TemporalExecutionNotFoundError("projection pending")

    digest_write = AsyncMock()
    monkeypatch.setattr(db_base, "get_async_session_context", lambda: _SessionContext())
    monkeypatch.setattr(
        temporal_service,
        "TemporalExecutionService",
        _TemporalExecutionService,
    )
    monkeypatch.setattr(
        activities,
        "_write_run_digest_best_effort",
        digest_write,
    )

    result = await activities.execution_record_terminal_state(
        {
            "workflowId": "resolver:child:3199",
            "state": "completed",
            "closeStatus": "completed",
            "summary": "PR merged and remotely verified",
        }
    )

    assert result == {
        "workflowId": "resolver:child:3199",
        "state": "completed",
        "closeStatus": "completed",
        "projectionDeferred": True,
        "reasonCode": "temporal_projection_pending",
    }
    digest_write.assert_not_awaited()


def _terminal_record(**overrides: object) -> SimpleNamespace:
    """Authoritative terminal execution record shape for digest tests."""

    record = {
        "workflow_id": "mm:run:123",
        "run_id": "temporal-run-1",
        "namespace": "default",
        "workflow_type": "MoonMind.UserWorkflow",
        "state": "completed",
        "close_status": "completed",
        "title": "Implement MM-762 run digests",
        "memo": {
            "summary": "Workflow completed successfully",
            "summary_artifact_ref": "art_summary",
        },
        "parameters": {
            "task": {"git": {"repository": "MoonLadderStudios/MoonMind"}},
            "publishMode": "pr",
        },
        "search_attributes": {"mm_agent_run_id": "agent-run-1"},
        "artifact_refs": ["art_summary", "art_patch"],
        "input_ref": "art_input",
        "plan_ref": "art_plan",
        "manifest_ref": "art_manifest",
    }
    record.update(overrides)
    return SimpleNamespace(**record)


def _completed_digest_artifact(artifact_id: str = "art_run_digest_1"):
    from api_service.db import models as db_models

    artifact = SimpleNamespace(
        artifact_id=artifact_id,
        status=db_models.TemporalArtifactStatus.COMPLETE,
        created_by_principal="service:temporal-finalize",
        metadata_json={
            "name": "run_digest.json",
            "workflowId": "mm:run:123",
            "runId": "temporal-run-1",
            "recordKind": "run_digest",
            "schemaVersion": "run_digest_artifact/v1",
            "idempotencyKey": "run_digest:mm:run:123:temporal-run-1",
        },
    )
    return artifact


@pytest.mark.asyncio
async def test_write_run_digest_persists_bounded_output_summary_artifact(
    activities,
    mock_service,
):
    """#4109: the digest is a durable bounded artifact, not a vector upsert."""

    import json as json_module

    from api_service.db import models as db_models

    mock_service.list_for_execution.return_value = []
    created = SimpleNamespace(artifact_id="art_run_digest_new")
    mock_service.create.return_value = (created, None)
    completed = SimpleNamespace(
        artifact_id="art_run_digest_new",
        status=db_models.TemporalArtifactStatus.COMPLETE,
    )
    mock_service.write_complete.return_value = completed

    artifact_id = await activities._write_run_digest_best_effort(
        _terminal_record()
    )

    assert artifact_id == "art_run_digest_new"
    mock_service.list_for_execution.assert_awaited_once_with(
        namespace="default",
        workflow_id="mm:run:123",
        run_id="temporal-run-1",
        principal="service:temporal-finalize",
        link_type="output.summary",
    )
    _, create_kwargs = mock_service.create.await_args
    assert create_kwargs["principal"] == "service:temporal-finalize"
    assert create_kwargs["content_type"] == "application/json"
    assert (
        create_kwargs["retention_class"]
        is db_models.TemporalArtifactRetentionClass.LONG
    )
    link = create_kwargs["link"]
    assert link.namespace == "default"
    assert link.workflow_id == "mm:run:123"
    assert link.run_id == "temporal-run-1"
    assert link.link_type == "output.summary"
    metadata = create_kwargs["metadata_json"]
    assert metadata["name"] == "run_digest.json"
    assert metadata["recordKind"] == "run_digest"
    assert metadata["schemaVersion"] == "run_digest_artifact/v1"
    assert metadata["workflowId"] == "mm:run:123"
    assert metadata["runId"] == "temporal-run-1"
    assert metadata["idempotencyKey"] == "run_digest:mm:run:123:temporal-run-1"
    _, write_kwargs = mock_service.write_complete.await_args
    assert write_kwargs["artifact_id"] == "art_run_digest_new"
    assert write_kwargs["principal"] == "service:temporal-finalize"
    payload = json_module.loads(write_kwargs["payload"].decode("utf-8"))
    assert payload["schemaVersion"] == "run_digest_artifact/v1"
    assert payload["source"] == "run_digest:mm:run:123"
    assert payload["digest"]["workflowId"] == "mm:run:123"
    assert payload["digest"]["runId"] == "temporal-run-1"
    assert payload["digest"]["evidence"]["summaryArtifactRef"] == "art_summary"
    assert payload["digest"]["evidence"]["artifactRefs"] == [
        "art_summary",
        "art_patch",
    ]


@pytest.mark.asyncio
async def test_write_run_digest_retry_reuses_completed_artifact(
    activities,
    mock_service,
):
    """#4109: activity retry must not duplicate the digest artifact."""

    mock_service.list_for_execution.return_value = [_completed_digest_artifact()]

    artifact_id = await activities._write_run_digest_best_effort(
        _terminal_record()
    )

    assert artifact_id == "art_run_digest_1"
    mock_service.create.assert_not_awaited()
    mock_service.write_complete.assert_not_awaited()


@pytest.mark.asyncio
async def test_write_run_digest_ignores_foreign_owned_artifact(
    activities,
    mock_service,
):
    """#4109: caller-controlled metadata alone never suppresses the digest."""

    from api_service.db import models as db_models

    foreign = _completed_digest_artifact(artifact_id="art_spoofed")
    foreign.created_by_principal = "user:mallory"
    mock_service.list_for_execution.return_value = [foreign]
    created = SimpleNamespace(artifact_id="art_run_digest_new")
    mock_service.create.return_value = (created, None)
    mock_service.write_complete.return_value = SimpleNamespace(
        artifact_id="art_run_digest_new",
        status=db_models.TemporalArtifactStatus.COMPLETE,
    )

    artifact_id = await activities._write_run_digest_best_effort(
        _terminal_record()
    )

    assert artifact_id == "art_run_digest_new"
    mock_service.create.assert_awaited_once()


@pytest.mark.asyncio
async def test_write_run_digest_retry_resumes_pending_artifact(
    activities,
    mock_service,
):
    """#4109: an interrupted attempt is completed, not duplicated."""

    from api_service.db import models as db_models

    pending = _completed_digest_artifact(artifact_id="art_run_digest_pending")
    pending.status = db_models.TemporalArtifactStatus.PENDING_UPLOAD
    mock_service.list_for_execution.return_value = [pending]
    mock_service.write_complete.return_value = SimpleNamespace(
        artifact_id="art_run_digest_pending",
        status=db_models.TemporalArtifactStatus.COMPLETE,
    )

    artifact_id = await activities._write_run_digest_best_effort(
        _terminal_record()
    )

    assert artifact_id == "art_run_digest_pending"
    mock_service.create.assert_not_awaited()
    _, write_kwargs = mock_service.write_complete.await_args
    assert write_kwargs["artifact_id"] == "art_run_digest_pending"
    assert write_kwargs["principal"] == "service:temporal-finalize"


@pytest.mark.asyncio
async def test_write_run_digest_failure_fails_open(
    activities,
    mock_service,
    caplog,
):
    """#4109: summary-persistence failure never breaks terminal recording."""

    mock_service.list_for_execution.return_value = []
    mock_service.create.side_effect = RuntimeError("artifact store unavailable")

    with caplog.at_level("WARNING", logger="moonmind.workflows.temporal.artifacts"):
        result = await activities._write_run_digest_best_effort(_terminal_record())

    assert result is None
    assert "Run digest persistence failed" in caplog.text


@pytest.mark.asyncio
async def test_write_run_digest_timeout_fails_open(
    activities,
    monkeypatch,
):
    async def _raise_timeout(awaitable, *, timeout):
        awaitable.cancel()
        raise TimeoutError("digest write timed out")

    monkeypatch.setattr(
        TemporalArtifactActivities._write_run_digest_best_effort.__globals__["asyncio"],
        "wait_for",
        _raise_timeout,
    )

    result = await activities._write_run_digest_best_effort(_terminal_record())

    assert result is None


@pytest.mark.asyncio
async def test_write_run_digest_constructs_no_vector_client(
    activities,
    mock_service,
    monkeypatch,
):
    """#4109: terminal digest persistence needs no vector Qdrant/embedding path."""

    import sys

    mock_service.list_for_execution.return_value = []
    created = SimpleNamespace(artifact_id="art_run_digest_novec")
    mock_service.create.return_value = (created, None)
    mock_service.write_complete.return_value = SimpleNamespace(
        artifact_id="art_run_digest_novec"
    )

    blocked = (
        "moonmind.rag.service",
        "moonmind.rag.embedding",
        "moonmind.rag.qdrant_client",
        "moonmind.rag.long_term_memory",
        "qdrant_client",
        "mem0",
    )
    real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else None

    def _guarded_import(name, *args, **kwargs):
        if name in blocked or any(
            name == entry or name.startswith(entry + ".") for entry in blocked
        ):
            raise AssertionError(f"vector dependency imported: {name}")
        return real_import(name, *args, **kwargs) if real_import else None

    monkeypatch.delenv("QDRANT_URL", raising=False)
    monkeypatch.delenv("QDRANT_ENABLED", raising=False)
    monkeypatch.delenv("MEMORY_LONG_TERM", raising=False)
    monkeypatch.delenv("MEM0_API_KEY", raising=False)
    monkeypatch.setattr("builtins.__import__", _guarded_import)
    for module_name in [name for name in sys.modules if name in blocked]:
        monkeypatch.delitem(sys.modules, module_name, raising=False)

    artifact_id = await TemporalArtifactActivities(
        mock_service
    )._write_run_digest_best_effort(_terminal_record())

    assert artifact_id == "art_run_digest_novec"


@pytest.mark.asyncio
async def test_execution_record_terminal_state_survives_digest_failure(
    activities,
    mock_service,
    monkeypatch,
):
    """#4109: completed agent/publication work is not rerun when the digest fails."""

    import api_service.db.base as db_base
    import moonmind.workflows.temporal.service as temporal_service

    record = _terminal_record()
    service_calls: list[dict[str, object]] = []

    class _SessionContext:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class _TemporalExecutionService:
        def __init__(self, session):
            self.session = session

        async def record_terminal_state(self, **kwargs):
            service_calls.append(dict(kwargs))
            return record

    monkeypatch.setattr(db_base, "get_async_session_context", lambda: _SessionContext())
    monkeypatch.setattr(
        temporal_service,
        "TemporalExecutionService",
        _TemporalExecutionService,
    )
    # The real best-effort digest step runs against the artifact service; its
    # failure must not escape as an activity failure.
    mock_service.list_for_execution.return_value = []
    mock_service.create.side_effect = RuntimeError("digest store unavailable")

    result = await activities.execution_record_terminal_state(
        {
            "workflowId": "mm:run:123",
            "state": "completed",
            "closeStatus": "completed",
            "summary": "Workflow completed successfully",
        }
    )

    assert result == {
        "workflowId": "mm:run:123",
        "state": "completed",
        "closeStatus": "completed",
    }
    assert len(service_calls) == 1
    assert service_calls[0]["workflow_id"] == "mm:run:123"
    # Only the terminal record plus the failed digest attempt ran: no agent or
    # publication activity was (re)started by the auxiliary failure.
    mock_service.create.assert_awaited_once()


def test_run_digest_artifact_fixture_reads_without_vector_database():
    """#4109: historical digest artifacts stay readable; replay shape is stable."""

    import json
    from pathlib import Path

    from moonmind.memory.run_digest import (
        RUN_DIGEST_ARTIFACT_SCHEMA_VERSION,
        RunDigest,
    )

    fixture = (
        Path(__file__).parent
        / "fixtures"
        / "run_digest_artifact_v1.json"
    )
    envelope = json.loads(fixture.read_text(encoding="utf-8"))

    assert envelope["payload"]["schemaVersion"] == RUN_DIGEST_ARTIFACT_SCHEMA_VERSION
    digest = RunDigest.model_validate(envelope["payload"]["digest"])
    assert digest.workflow_id == "mm:run:123"
    assert digest.run_id == "temporal-run-1"
    assert digest.namespace_id == "default"
    assert digest.repo == "MoonLadderStudios/MoonMind"
    assert digest.evidence.summary_artifact_ref == "art_summary"
    assert digest.evidence.artifact_refs == ("art_summary", "art_patch")
    artifact = envelope["artifact"]
    assert artifact["link_type"] == "output.summary"
    assert artifact["status"] == "COMPLETE"
    assert artifact["metadata"]["idempotencyKey"] == (
        "run_digest:mm:run:123:temporal-run-1"
    )

@pytest.mark.asyncio
async def test_artifact_publish_report_bundle_delegates_to_service(
    activities, mock_service
):
    """MM-461: Activity facade should expose report bundle publication."""

    expected = {
        "report_bundle_v": 1,
        "primary_report_ref": {"artifact_ref_v": 1, "artifact_id": "art_primary"},
        "evidence_refs": [],
        "report_type": "unit_test_report",
        "report_scope": "final",
    }
    mock_service.publish_report_bundle.return_value = expected

    request = {
        "principal": "workflow-producer",
        "namespace": "moonmind",
        "workflow_id": "wf-report",
        "run_id": "run-report",
        "report_type": "unit_test_report",
        "report_scope": "final",
        "primary": {"payload": "# Final report", "content_type": "text/markdown"},
    }

    result = await activities.artifact_publish_report_bundle(request)

    assert result == expected
    mock_service.publish_report_bundle.assert_awaited_once_with(**request)
