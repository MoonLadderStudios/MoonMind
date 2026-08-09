import hashlib
import json
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

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
async def test_execution_terminal_state_reconciles_checkpoint_branch_turn(
    activities,
    mock_service,
    monkeypatch,
):
    import api_service.db.base as db_base
    import api_service.services.checkpoint_branch_service as branch_service
    import moonmind.workflows.temporal.service as temporal_service

    reconciliations: list[dict[str, object]] = []

    class _Session:
        async def commit(self):
            return None

    class _SessionContext:
        async def __aenter__(self):
            return _Session()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class _TemporalExecutionService:
        def __init__(self, session):
            pass

        async def record_terminal_state(self, **kwargs):
            return SimpleNamespace(
                workflow_id="runtime-wf",
                run_id="runtime-run",
                state="completed",
                close_status="completed",
            )

    class _CheckpointBranchService:
        def __init__(self, session):
            pass

        async def reconcile_turn_terminal(self, **kwargs):
            reconciliations.append(dict(kwargs))

    monkeypatch.setattr(db_base, "get_async_session_context", lambda: _SessionContext())
    monkeypatch.setattr(
        temporal_service, "TemporalExecutionService", _TemporalExecutionService
    )
    monkeypatch.setattr(
        branch_service, "CheckpointBranchService", _CheckpointBranchService
    )
    monkeypatch.setattr(activities, "_write_run_digest_best_effort", AsyncMock())

    manifest = {
        "branchId": "branch-1",
        "branchTurnId": "turn-1",
        "contextBundleRef": "artifact://branch/context",
        "contextBundleDigest": "sha256:" + "c" * 64,
    }
    manifest_digest = "sha256:" + hashlib.sha256(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    manifest["artifactManifestDigest"] = manifest_digest
    manifest_payload = json.dumps(
        manifest,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    stored_digest = "sha256:" + hashlib.sha256(manifest_payload).hexdigest()
    mock_service.read.return_value = (
        SimpleNamespace(sha256=stored_digest.removeprefix("sha256:")),
        manifest_payload,
    )

    await activities.execution_record_terminal_state(
        {
            "workflowId": "runtime-wf",
            "state": "completed",
            "closeStatus": "completed",
            "finishOutcomeCode": "PUBLISHED_PR",
            "finishSummary": {
                "checkpointBranchTurn": {
                    "branchId": "branch-1",
                    "branchTurnId": "turn-1",
                    "artifactManifestRef": "artifact://branch/terminal-manifest",
                    "artifactManifestDigest": manifest_digest,
                    "artifactPrincipal": "owner-1",
                    "contextBundleRef": "artifact://branch/context",
                    "contextBundleDigest": "sha256:" + "c" * 64,
                    "evidenceRefs": {
                        "input.branch_turn.instructions.md": "artifact://branch/instructions",
                        "runtime.branch_turn.context_bundle.json": "artifact://branch/context",
                        "checkpoint": "artifact://branch/checkpoint",
                        "cleanup": "artifact://branch/cleanup",
                        "host_lease": "artifact://branch/host-lease",
                        "provider_lease": "artifact://branch/provider-lease",
                        "first_message": "artifact://branch/first-message",
                    },
                    "terminalState": "harvested",
                    "cleanupState": "completed",
                    "hostReleaseState": "released",
                    "profileReleaseState": "released",
                }
            },
        }
    )

    assert reconciliations == [
        {
            "branch_id": "branch-1",
            "branch_turn_id": "turn-1",
            "runtime_agent_run_id": "runtime-wf",
            "status": "verification_required",
            "evidence_refs": {
                "artifact_manifest": "artifact://branch/terminal-manifest",
                "checkpoint": "artifact://branch/checkpoint",
                "cleanup": "artifact://branch/cleanup",
                "host_lease": "artifact://branch/host-lease",
                "provider_lease": "artifact://branch/provider-lease",
                "first_message": "artifact://branch/first-message",
                "input.branch_turn.instructions.md": "artifact://branch/instructions",
                "runtime.branch_turn.context_bundle.json": "artifact://branch/context",
            },
            "terminal_summary": {
                "state": "completed",
                "closeStatus": "completed",
                "finishOutcomeCode": "PUBLISHED_PR",
                "errorCategory": None,
                "terminalState": "harvested",
                "cleanupState": "completed",
                "hostReleaseState": "released",
                "profileReleaseState": "released",
            },
        }
    ]
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


@pytest.mark.asyncio
async def test_write_run_digest_best_effort_runs_sync_indexing_in_executor(
    activities,
    monkeypatch,
):
    import moonmind.memory.run_digest as run_digest_module
    import moonmind.rag.service as rag_service
    import moonmind.rag.settings as rag_settings

    event_loop_thread = threading.get_ident()
    indexing_threads: list[int] = []

    class _Settings:
        def retrieval_execution_reason(self, _environ, *, preferred_transport):
            return True, "enabled"

    class _RetrievalService:
        qdrant_client = object()
        embedding_client = object()

        def __init__(self, *, settings):
            self.settings = settings

    class _TaskHistoryService:
        def __init__(self, *, qdrant_client, embedding_provider):
            self.qdrant_client = qdrant_client
            self.embedding_provider = embedding_provider

        def build_and_upsert_run_digest(self, record):
            indexing_threads.append(threading.get_ident())
            return {"workflowId": record.workflow_id}

    monkeypatch.setattr(
        rag_settings.RagRuntimeSettings,
        "from_env",
        lambda _env: _Settings(),
    )
    monkeypatch.setattr(rag_service, "ContextRetrievalService", _RetrievalService)
    monkeypatch.setattr(run_digest_module, "TaskHistoryService", _TaskHistoryService)

    await activities._write_run_digest_best_effort(
        SimpleNamespace(workflow_id="mm:run:123")
    )

    assert indexing_threads
    assert indexing_threads[0] != event_loop_thread


@pytest.mark.asyncio
async def test_write_run_digest_best_effort_timeout_fails_open(
    activities,
    monkeypatch,
):
    import moonmind.memory.run_digest as run_digest_module
    import moonmind.rag.service as rag_service
    import moonmind.rag.settings as rag_settings

    class _Settings:
        def retrieval_execution_reason(self, _environ, *, preferred_transport):
            return True, "enabled"

    class _RetrievalService:
        qdrant_client = object()
        embedding_client = object()

        def __init__(self, *, settings):
            self.settings = settings

    class _TaskHistoryService:
        def __init__(self, *, qdrant_client, embedding_provider):
            pass

        def build_and_upsert_run_digest(self, record):
            return {"workflowId": record.workflow_id}

    async def _raise_timeout(awaitable, *, timeout):
        awaitable.cancel()
        raise TimeoutError("digest write timed out")

    monkeypatch.setattr(
        rag_settings.RagRuntimeSettings,
        "from_env",
        lambda _env: _Settings(),
    )
    monkeypatch.setattr(rag_service, "ContextRetrievalService", _RetrievalService)
    monkeypatch.setattr(run_digest_module, "TaskHistoryService", _TaskHistoryService)
    monkeypatch.setattr(
        TemporalArtifactActivities._write_run_digest_best_effort.__globals__["asyncio"],
        "wait_for",
        _raise_timeout,
    )

    await activities._write_run_digest_best_effort(
        SimpleNamespace(workflow_id="mm:run:123")
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
