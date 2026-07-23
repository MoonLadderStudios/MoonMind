from __future__ import annotations

import json
import logging
from unittest.mock import AsyncMock, MagicMock, Mock, call, patch
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from temporalio.client import WorkflowExecutionDescription, WorkflowExecutionStatus

from api_service.db.models import (
    Base,
    MoonMindWorkflowState,
    SettingsOverride,
    TemporalArtifact,
    TemporalArtifactLink,
    TemporalArtifactEncryption,
    TemporalArtifactRedactionLevel,
    TemporalArtifactRetentionClass,
    TemporalArtifactStatus,
    TemporalArtifactStorageBackend,
    TemporalArtifactUploadMode,
    TemporalExecutionCanonicalRecord,
    TemporalExecutionCloseStatus,
    TemporalExecutionOwnerType,
    TemporalExecutionProjectionSourceMode,
    TemporalExecutionProjectionSyncState,
    TemporalExecutionRecord,
    TemporalExecutionRemediationLink,
    TemporalWorkflowType,
)
from moonmind.config.settings import settings
from moonmind.workflows.temporal.service import (
    TemporalExecutionNotFoundError,
    TemporalExecutionRecoveryCheckpointError,
    TemporalExecutionService,
    TemporalExecutionValidationError,
    _get_managed_session_store_root,
    _visibility_runtime_from_parameters,
    _visibility_skill_from_parameters,
)
from moonmind.workflows.temporal.hard_switch_cutover import RENAMED_USER_WORKFLOW_TYPE
from moonmind.schemas.temporal_models import (
    CreateExecutionRequest,
    FAILED_RUN_RECOVERY_MANIFEST_CONTENT_TYPE,
    RecoveryCheckpointModel,
    has_user_workflow_plan_source,
)
from moonmind.schemas.managed_session_models import CodexManagedSessionRecord
from moonmind.schemas.workflow_recovery_models import (
    WorkflowRecoveryTargetModel,
    deterministic_recovery_creation_key,
)
from moonmind.workflows.executions.runtime_capabilities import (
    RuntimeExecutionCapabilities,
    resolve_runtime_execution_capabilities,
)
from moonmind.statuses.compat import (
    canonicalize_finish_outcome_code_alias,
    canonicalize_workflow_state_alias,
    normalize_no_commit_finish_summary,
)
from moonmind.workflows.temporal.runtime.managed_session_store import ManagedSessionStore


def test_legacy_no_changes_aliases_are_quarantined_in_compat_helpers() -> None:
    assert canonicalize_workflow_state_alias("no_changes") == "no_commit"
    assert canonicalize_finish_outcome_code_alias("NO_CHANGES") == "NO_COMMIT"
    assert canonicalize_finish_outcome_code_alias(None) is None
    assert normalize_no_commit_finish_summary(
        {
            "finishOutcome": {"code": "NO_CHANGES", "reason": "No local changes"},
            "publish": {
                "reasonCode": "no_changes",
                "reason_code": "no_changes",
            },
        }
    ) == {
        "finishOutcome": {
            "code": "NO_COMMIT",
            "reason": "No repository commit was needed.",
        },
        "publish": {
            "reasonCode": "no_commit",
            "reason_code": "no_commit",
            "reason": "No repository changes were available to commit or publish.",
        },
    }


def test_finish_outcome_compat_boundary_observes_alias_fields(
    caplog: pytest.LogCaptureFixture,
) -> None:
    logger = logging.getLogger("tests.temporal_status_compat")

    with caplog.at_level(logging.WARNING, logger=logger.name):
        assert (
            canonicalize_finish_outcome_code_alias("NO_CHANGES", logger=logger)
            == "NO_COMMIT"
        )

    [record] = caplog.records
    assert record.getMessage() == "Observed legacy status alias"
    assert record.domain == "finish_outcome"
    assert record.alias == "NO_CHANGES"
    assert record.canonical == "NO_COMMIT"


def test_finish_summary_compat_boundary_observes_publish_reason_alias_fields(
    caplog: pytest.LogCaptureFixture,
) -> None:
    logger = logging.getLogger("tests.temporal_finish_summary_compat")

    with caplog.at_level(logging.WARNING, logger=logger.name):
        assert normalize_no_commit_finish_summary(
            {"publish": {"reasonCode": "no_changes"}},
            logger=logger,
        ) == {
            "publish": {
                "reasonCode": "no_commit",
                "reason": "No repository changes were available to commit or publish.",
            }
        }

    [record] = caplog.records
    assert record.getMessage() == "Observed legacy status alias"
    assert record.domain == "publish_reason"
    assert record.alias == "no_changes"
    assert record.canonical == "no_commit"


def _valid_user_workflow_parameters() -> dict[str, object]:
    return {"workflow": {"instructions": "Test workflow fixture."}}


@pytest.mark.asyncio
async def test_create_execution_synthesizes_jira_implement_title_metadata(
    tmp_path, mock_client_adapter
):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session, client_adapter=mock_client_adapter)

        created = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=uuid4(),
            title="Load Jira preset brief",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters={
                "workflow": {
                    "instructions": "Implement Jira issue MM-123.",
                    "taskTemplate": {"slug": "jira-implement"},
                    "inputs": {
                        "jira_issue": {
                            "key": "MM-123",
                            "summary": "Fix OAuth redirect handling",
                        }
                    },
                    "steps": [
                        {"title": "Load Jira preset brief"},
                        {"title": "Implement issue"},
                    ],
                }
            },
            idempotency_key=None,
            integration="jira",
        )

        assert created.memo["title"] == (
            "Jira Implement: MM-123 — Fix OAuth redirect handling"
        )
        assert created.memo["titleSource"] == "integration_target"
        assert created.memo["titleConfidence"] == "high"
        assert created.search_attributes["mm_title"] == [
            "jira",
            "implement",
            "mm",
            "123",
            "fix",
            "oauth",
            "redirect",
            "handling",
        ]


@pytest.mark.asyncio
async def test_create_execution_synthesizes_github_issue_preset_title_metadata(
    tmp_path, mock_client_adapter
):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session, client_adapter=mock_client_adapter)

        created = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=uuid4(),
            title="GitHub Issue Implement",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters={
                "workflow": {
                    "title": "GitHub Issue Implement",
                    "instructions": "Implement the selected GitHub issue.",
                    "taskTemplate": {"slug": "github-issue-implement"},
                    "inputs": {
                        "github_issue": {
                            "repository": "MoonLadderStudios/MoonMind",
                            "number": 3143,
                            "title": "Improve generated workflow titles",
                        },
                        "github_issue_ref": "MoonLadderStudios/MoonMind#3143",
                    },
                }
            },
            idempotency_key=None,
            integration="github",
        )

        assert created.memo["title"] == (
            "GitHub Issue Implement: MoonLadderStudios/MoonMind#3143 — "
            "Improve generated workflow titles"
        )
        assert created.memo["titleSource"] == "integration_target"
        assert created.memo["titleConfidence"] == "high"
        assert "3143" in created.search_attributes["mm_title"]


def _write_mm730_cutover_files(tmp_path):
    release_notes = tmp_path / "MM-730-release-notes.md"
    release_notes.write_text(
        "MoonMind no longer exposes Tasks as a product/runtime concept. "
        "Use Workflow Execution, workflowId, runId, and Step Execution.\n\n"
        "Compatibility redirects and task-shaped aliases are not kept.\n",
        encoding="utf-8",
    )
    cutover_record = tmp_path / "MM-730-cutover.json"
    cutover_record.write_text(
        json.dumps(
            {
                "jiraIssueKey": "MM-730",
                "releaseMode": "coordinated_branch_release",
                "legacyWorkflowType": "MoonMind.Run",
                "newWorkflowType": "MoonMind.UserWorkflow",
                "releaseNotesPath": str(release_notes),
                "environments": [
                    {
                        "name": "ci",
                        "decision": "drain",
                        "recordedAt": "2026-05-24T00:00:00Z",
                    }
                ],
                "affectedContracts": [
                    {
                        "kind": "workflow",
                        "owner": "MoonMind.UserWorkflow",
                        "strategy": "Use renamed workflow type after cutover.",
                    },
                    {
                        "kind": "activity",
                        "owner": "MoonMind.UserWorkflow activities",
                        "strategy": "Use renamed activity payloads after cutover.",
                    },
                    {
                        "kind": "signal",
                        "owner": "MoonMind.UserWorkflow signals",
                        "strategy": "Use renamed signal shapes after cutover.",
                    },
                    {
                        "kind": "update",
                        "owner": "MoonMind.UserWorkflow updates",
                        "strategy": "Use renamed update shapes after cutover.",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    return release_notes, cutover_record


def test_visibility_helpers_ignore_empty_parameters() -> None:
    assert _visibility_runtime_from_parameters(None) is None
    assert _visibility_runtime_from_parameters({}) is None
    assert _visibility_skill_from_parameters(None) is None
    assert _visibility_skill_from_parameters({}) is None

@pytest.fixture
def mock_client_adapter():
    adapter = MagicMock()
    adapter.start_workflow = AsyncMock()
    adapter.describe_workflow = AsyncMock()
    adapter.update_workflow = AsyncMock()
    adapter.signal_workflow = AsyncMock()
    adapter.cancel_workflow = AsyncMock()
    adapter.terminate_workflow = AsyncMock()
    return adapter

@asynccontextmanager
async def temporal_db(tmp_path):
    original_artifact_backend = settings.workflow.temporal_artifact_backend
    original_artifact_root = settings.workflow.temporal_artifact_root
    settings.workflow.temporal_artifact_backend = "local_fs"
    settings.workflow.temporal_artifact_root = str(tmp_path / "artifacts")
    db_url = f"sqlite+aiosqlite:///{tmp_path}/temporal_lifecycle.db"
    engine = create_async_engine(db_url, future=True)
    session_factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    try:
        async with session_factory() as session:
            yield session
    finally:
        await engine.dispose()
        settings.workflow.temporal_artifact_backend = original_artifact_backend
        settings.workflow.temporal_artifact_root = original_artifact_root

async def _create_temporal_artifact(
    session: AsyncSession,
    *,
    artifact_id: str,
    status: TemporalArtifactStatus,
) -> None:
    session.add(
        TemporalArtifact(
            artifact_id=artifact_id,
            storage_key=f"tests/{artifact_id}.json",
            storage_backend=TemporalArtifactStorageBackend.S3,
            encryption=TemporalArtifactEncryption.NONE,
            status=status,
            retention_class=TemporalArtifactRetentionClass.STANDARD,
            redaction_level=TemporalArtifactRedactionLevel.NONE,
            upload_mode=TemporalArtifactUploadMode.SINGLE_PUT,
            metadata_json={},
        )
    )
    await session.commit()


def test_create_execution_request_rejects_user_workflow_without_plan_source():
    with pytest.raises(ValidationError, match="requires non-empty instructions"):
        CreateExecutionRequest.model_validate(
            {
                "workflowType": "MoonMind.UserWorkflow",
                "title": "Run",
                "initialParameters": {},
            }
        )


def test_create_execution_request_accepts_workflow_skills_plan_source():
    request = CreateExecutionRequest.model_validate(
        {
            "workflowType": "MoonMind.UserWorkflow",
            "title": "Run skill",
            "initialParameters": {
                "workflow": {
                    "skills": {
                        "include": [{"name": "pr-resolver"}],
                    },
                },
            },
        }
    )

    assert request.workflow_type == "MoonMind.UserWorkflow"


def test_user_workflow_plan_source_accepts_pydantic_artifact_refs():
    class ArtifactRefModel(BaseModel):
        artifact_id: str

    assert has_user_workflow_plan_source(
        initial_parameters={},
        input_artifact_ref=ArtifactRefModel(artifact_id="art_input"),
        plan_artifact_ref=None,
    )


@pytest.mark.asyncio
async def test_create_execution_rejects_user_workflow_without_plan_source_before_start(
    tmp_path, mock_client_adapter
):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(
            session,
            client_adapter=mock_client_adapter,
        )

        with pytest.raises(
            TemporalExecutionValidationError,
            match="requires non-empty instructions",
        ):
            await service.create_execution(
                workflow_type="MoonMind.UserWorkflow",
                owner_id=uuid4(),
                title="Run",
                input_artifact_ref=None,
                plan_artifact_ref=None,
                manifest_artifact_ref=None,
                failure_policy=None,
                initial_parameters={},
                idempotency_key=None,
            )

        mock_client_adapter.start_workflow.assert_not_awaited()
        records = (
            await session.execute(select(TemporalExecutionCanonicalRecord))
        ).scalars().all()
        assert records == []


@pytest.mark.asyncio
async def test_create_execution_initializes_lifecycle_search_attributes(tmp_path):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)
        owner_id = uuid4()

        record = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=owner_id,
            title="My run",
            input_artifact_ref="artifact://input/1",
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters={"foo": "bar"},
            idempotency_key="create-1",
        )

        assert record.workflow_id.startswith("mm:")
        assert record.search_attributes["mm_owner_type"] == "user"
        assert record.state is MoonMindWorkflowState.INITIALIZING
        assert record.owner_type is TemporalExecutionOwnerType.USER
        assert record.search_attributes["mm_owner_id"] == str(owner_id)
        assert record.search_attributes["mm_owner_type"] == "user"
        assert record.search_attributes["mm_state"] == "initializing"
        assert record.search_attributes["mm_entry"] == "user_workflow"
        assert record.memo["title"] == "My run"
        assert record.memo["input_ref"] == "artifact://input/1"
        assert record.sync_state is TemporalExecutionProjectionSyncState.FRESH
        assert (
            record.source_mode
            is TemporalExecutionProjectionSourceMode.TEMPORAL_AUTHORITATIVE
        )

        source = await session.get(TemporalExecutionCanonicalRecord, record.workflow_id)
        assert source is not None
        assert source.run_id == record.run_id


@pytest.mark.asyncio
async def test_create_execution_snapshots_moonspec_environment_publish_action(
    tmp_path,
    mock_client_adapter,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        settings.workflow,
        "moonspec_environment_blocked_publish_action",
        "draft_pr",
    )
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(
            session,
            client_adapter=mock_client_adapter,
        )

        record = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=uuid4(),
            title="My run",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters=_valid_user_workflow_parameters(),
            idempotency_key="create-moonspec-draft-policy",
        )

        assert (
            record.parameters["moonspecEnvironmentBlockedPublishAction"]
            == "draft_pr"
        )
        start_args = mock_client_adapter.start_workflow.await_args.kwargs[
            "input_args"
        ]
        assert (
            start_args["initial_parameters"][
                "moonspecEnvironmentBlockedPublishAction"
            ]
            == "draft_pr"
        )


@pytest.mark.asyncio
async def test_create_execution_writes_runtime_and_primary_skill_search_attributes(tmp_path):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)

        record = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=uuid4(),
            title="Skill run",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters={
                "targetRuntime": "codex_cli",
                "workflow": {
                    "instructions": "Resolve the issue.",
                    "tool": {"type": "skill", "name": "pr-resolver"},
                },
            },
            idempotency_key=None,
        )

        assert record.search_attributes["mm_target_runtime"] == "codex_cli"
        assert record.search_attributes["mm_target_skill"] == "pr-resolver"


@pytest.mark.asyncio
async def test_create_execution_omits_blank_runtime_and_skill_search_attributes(tmp_path):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)

        record = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=uuid4(),
            title="No target run",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters={
                "targetRuntime": " ",
                "targetSkill": "",
                "workflow": {"instructions": "Resolve the issue."},
            },
            idempotency_key=None,
        )

        assert "mm_target_runtime" not in record.search_attributes
        assert "mm_target_skill" not in record.search_attributes


@pytest.mark.asyncio
async def test_create_execution_routes_user_workflow_after_mm730_cutover(
    tmp_path,
    mock_client_adapter,
    monkeypatch,
):
    release_notes, cutover_record = _write_mm730_cutover_files(tmp_path)
    temporal_settings = settings.temporal.model_copy(
        update={
            "user_workflow_contract_mode": "renamed_contract",
            "user_workflow_v2_task_queue": "mm.workflow.user.v2",
            "user_workflow_release_notes_path": str(release_notes),
            "user_workflow_cutover_record_path": str(cutover_record),
        }
    )
    monkeypatch.setattr(settings, "temporal", temporal_settings)

    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(
            session,
            client_adapter=mock_client_adapter,
        )
        owner_id = uuid4()

        record = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=owner_id,
            title="Cutover run",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters=_valid_user_workflow_parameters(),
            idempotency_key=None,
        )

        start_kwargs = mock_client_adapter.start_workflow.await_args.kwargs
        assert start_kwargs["workflow_type"] == RENAMED_USER_WORKFLOW_TYPE
        assert start_kwargs["input_args"]["workflow_type"] == RENAMED_USER_WORKFLOW_TYPE
        assert record.memo["runtimeWorkflowType"] == RENAMED_USER_WORKFLOW_TYPE
        assert record.memo["runtimeWorkflowContract"] == "renamed_contract"


@pytest.mark.asyncio
async def test_create_execution_routes_pr_merge_automation_workflows_to_dedicated_queue(
    tmp_path,
    mock_client_adapter,
    monkeypatch,
):
    temporal_settings = settings.temporal.model_copy(
        update={
            "merge_automation_workflow_task_queue": "mm.workflow.merge_automation.test"
        }
    )
    monkeypatch.setattr(settings, "temporal", temporal_settings)

    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(
            session,
            client_adapter=mock_client_adapter,
        )

        await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=uuid4(),
            title="Merge automation run",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters={
                "publishMode": "pr",
                "workflow": {
                    "instructions": "Test merge automation routing.",
                    "publish": {
                        "mode": "pr",
                        "mergeAutomation": {"enabled": True},
                    },
                },
            },
            idempotency_key=None,
        )

        start_kwargs = mock_client_adapter.start_workflow.await_args.kwargs
        assert start_kwargs["task_queue"] == "mm.workflow.merge_automation.test"


@pytest.mark.asyncio
async def test_create_execution_keeps_default_priority_without_merge_automation(
    tmp_path,
    mock_client_adapter,
):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(
            session,
            client_adapter=mock_client_adapter,
        )

        await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=uuid4(),
            title="Plain run",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters={
                "publishMode": "pr",
                "workflow": {
                    "instructions": "Test default publish priority.",
                    "publish": {"mode": "pr"},
                },
            },
            idempotency_key=None,
        )

        start_kwargs = mock_client_adapter.start_workflow.await_args.kwargs
        assert start_kwargs["task_queue"] is None


@pytest.mark.asyncio
async def test_create_execution_returns_repair_pending_fallback_when_projection_sync_fails(
    tmp_path, monkeypatch
):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)

        async def fail_projection_sync(source, **kwargs):
            raise RuntimeError(f"projection write failed for {source.workflow_id}")

        monkeypatch.setattr(
            service, "_upsert_projection_from_source", fail_projection_sync
        )

        record = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=uuid4(),
            title="repair pending",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters=_valid_user_workflow_parameters(),
            idempotency_key="repair-pending-create",
        )

        assert record.sync_state is TemporalExecutionProjectionSyncState.REPAIR_PENDING
        assert (
            record.source_mode
            is TemporalExecutionProjectionSourceMode.TEMPORAL_AUTHORITATIVE
        )
        assert "projection write failed" in (record.sync_error or "")

        source = await session.get(TemporalExecutionCanonicalRecord, record.workflow_id)
        projection = await session.get(TemporalExecutionRecord, record.workflow_id)
        assert source is not None
        assert projection is None

@pytest.mark.asyncio
async def test_create_execution_defaults_missing_owner_to_system(tmp_path):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)

        record = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=None,
            title=None,
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters=_valid_user_workflow_parameters(),
            idempotency_key=None,
        )

        assert record.owner_type is TemporalExecutionOwnerType.SYSTEM
        assert record.owner_id == "system"
        assert record.search_attributes["mm_owner_type"] == "system"
        assert record.search_attributes["mm_owner_id"] == "system"

@pytest.mark.asyncio
async def test_create_execution_rejects_unsupported_workflow_type(tmp_path):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)

        with pytest.raises(
            TemporalExecutionValidationError, match="Unsupported workflow type"
        ):
            await service.create_execution(
                workflow_type="MoonMind.Unknown",
                owner_id=uuid4(),
                title=None,
                input_artifact_ref=None,
                plan_artifact_ref=None,
                manifest_artifact_ref=None,
                failure_policy=None,
                initial_parameters={},
                idempotency_key=None,
            )

@pytest.mark.asyncio
async def test_create_execution_rejects_missing_manifest_artifact_ref(tmp_path):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)

        with pytest.raises(
            TemporalExecutionValidationError,
            match="manifestArtifactRef is required",
        ):
            await service.create_execution(
                workflow_type="MoonMind.ManifestIngest",
                owner_id=uuid4(),
                title=None,
                input_artifact_ref=None,
                plan_artifact_ref=None,
                manifest_artifact_ref=None,
                failure_policy=None,
                initial_parameters={},
                idempotency_key=None,
            )

@pytest.mark.asyncio
async def test_create_execution_rejects_pending_upload_temporal_input_artifact_ref(
    tmp_path,
):
    async with temporal_db(tmp_path) as session:
        await _create_temporal_artifact(
            session,
            artifact_id="art_01TESTPENDINGINPUT0000000000",
            status=TemporalArtifactStatus.PENDING_UPLOAD,
        )
        service = TemporalExecutionService(session)

        with pytest.raises(
            TemporalExecutionValidationError,
            match="inputArtifactRef must reference a readable artifact",
        ):
            await service.create_execution(
                workflow_type="MoonMind.UserWorkflow",
                owner_id=uuid4(),
                title=None,
                input_artifact_ref="art_01TESTPENDINGINPUT0000000000",
                plan_artifact_ref=None,
                manifest_artifact_ref=None,
                failure_policy=None,
                initial_parameters=_valid_user_workflow_parameters(),
                idempotency_key=None,
            )

@pytest.mark.asyncio
async def test_create_execution_rejects_unsupported_failure_policy(tmp_path):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)

        with pytest.raises(
            TemporalExecutionValidationError,
            match="Unsupported failurePolicy",
        ):
            await service.create_execution(
                workflow_type="MoonMind.UserWorkflow",
                owner_id=uuid4(),
                title=None,
                input_artifact_ref=None,
                plan_artifact_ref=None,
                manifest_artifact_ref=None,
                failure_policy="explode_loudly",
                initial_parameters=_valid_user_workflow_parameters(),
                idempotency_key=None,
            )

@pytest.mark.asyncio
async def test_create_execution_rejects_empty_failure_policy(tmp_path):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)

        with pytest.raises(
            TemporalExecutionValidationError,
            match="Unsupported failurePolicy",
        ):
            await service.create_execution(
                workflow_type="MoonMind.UserWorkflow",
                owner_id=uuid4(),
                title=None,
                input_artifact_ref=None,
                plan_artifact_ref=None,
                manifest_artifact_ref=None,
                failure_policy="",
                initial_parameters=_valid_user_workflow_parameters(),
                idempotency_key=None,
            )

@pytest.mark.asyncio
async def test_create_execution_rejects_more_than_10_dependencies(tmp_path):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)

        with pytest.raises(
            TemporalExecutionValidationError,
            match="dependsOn can have a maximum of 10 items",
        ):
            await service.create_execution(
                workflow_type="MoonMind.UserWorkflow",
                owner_id=uuid4(),
                title=None,
                input_artifact_ref=None,
                plan_artifact_ref=None,
                manifest_artifact_ref=None,
                failure_policy=None,
                initial_parameters={
                "workflow": {"dependsOn": [f"dep-{i}" for i in range(11)]}
                },
                idempotency_key=None,
            )

@pytest.mark.asyncio
async def test_create_execution_rejects_missing_dependency(tmp_path):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)

        with pytest.raises(
            TemporalExecutionValidationError,
            match="Dependency not found: mm:non-existent",
        ):
            await service.create_execution(
                workflow_type="MoonMind.UserWorkflow",
                owner_id=uuid4(),
                title=None,
                input_artifact_ref=None,
                plan_artifact_ref=None,
                manifest_artifact_ref=None,
                failure_policy=None,
                initial_parameters={"workflow": {"dependsOn": ["mm:non-existent"]}},
                idempotency_key=None,
            )

@pytest.mark.asyncio
async def test_create_execution_rejects_dependency_run_id_identifier(
    tmp_path, mock_client_adapter
):
    async with temporal_db(tmp_path) as session:
        owner_id = uuid4()
        service = TemporalExecutionService(session, client_adapter=mock_client_adapter)

        existing = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=owner_id,
            title="Existing dependency",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters=_valid_user_workflow_parameters(),
            idempotency_key=None,
        )

        with pytest.raises(
            TemporalExecutionValidationError,
            match=f"Dependency {existing.run_id} must use workflowId, not runId.",
        ):
            await service.create_execution(
                workflow_type="MoonMind.UserWorkflow",
                owner_id=owner_id,
                title=None,
                input_artifact_ref=None,
                plan_artifact_ref=None,
                manifest_artifact_ref=None,
                failure_policy=None,
                initial_parameters={"workflow": {"dependsOn": [existing.run_id]}},
                idempotency_key=None,
            )

@pytest.mark.asyncio
async def test_create_execution_rejects_non_run_dependency(tmp_path, mock_client_adapter):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session, client_adapter=mock_client_adapter)
        owner_id = uuid4()

        manifest = await service.create_execution(
            workflow_type="MoonMind.ManifestIngest",
            owner_id=owner_id,
            title="Manifest dependency",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref="artifact://manifest/1",
            failure_policy=None,
            initial_parameters={},
            idempotency_key=None,
        )

        with pytest.raises(
            TemporalExecutionValidationError,
            match=(
                f"Dependency {manifest.workflow_id} is a MoonMind.ManifestIngest "
                "workflow, not a MoonMind.UserWorkflow workflow."
            ),
        ):
            await service.create_execution(
                workflow_type="MoonMind.UserWorkflow",
                owner_id=owner_id,
                title=None,
                input_artifact_ref=None,
                plan_artifact_ref=None,
                manifest_artifact_ref=None,
                failure_policy=None,
                initial_parameters={"workflow": {"dependsOn": [manifest.workflow_id]}},
                idempotency_key=None,
            )

@pytest.mark.asyncio
async def test_create_execution_rejects_unauthorized_dependency(tmp_path, mock_client_adapter):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session, client_adapter=mock_client_adapter)
        foreign_owner = uuid4()
        current_owner = uuid4()

        foreign = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=foreign_owner,
            title="Foreign dependency",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters=_valid_user_workflow_parameters(),
            idempotency_key=None,
        )

        with pytest.raises(
            TemporalExecutionValidationError,
            match=f"Dependency unauthorized: {foreign.workflow_id}",
        ):
            await service.create_execution(
                workflow_type="MoonMind.UserWorkflow",
                owner_id=current_owner,
                title=None,
                input_artifact_ref=None,
                plan_artifact_ref=None,
                manifest_artifact_ref=None,
                failure_policy=None,
                initial_parameters={"workflow": {"dependsOn": [foreign.workflow_id]}},
                idempotency_key=None,
            )

@pytest.mark.asyncio
async def test_create_execution_persists_dependency_edges_and_supports_lookups(
    tmp_path, mock_client_adapter
):
    async with temporal_db(tmp_path) as session:
        owner_id = uuid4()
        service = TemporalExecutionService(session, client_adapter=mock_client_adapter)

        dep1 = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=owner_id,
            title="Dependency 1",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters=_valid_user_workflow_parameters(),
            idempotency_key=None,
        )
        dep2 = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=owner_id,
            title="Dependency 2",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters=_valid_user_workflow_parameters(),
            idempotency_key=None,
        )

        dependent = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=owner_id,
            title="Dependent",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters={
                "workflow": {
                    "dependsOn": [dep1.workflow_id, dep2.workflow_id, dep1.workflow_id]
                }
            },
            idempotency_key=None,
        )

        source = await session.get(TemporalExecutionCanonicalRecord, dependent.workflow_id)
        assert source is not None
        assert source.parameters["workflow"]["dependsOn"] == [dep1.workflow_id, dep2.workflow_id]

        prerequisites = await service.list_prerequisites(dependent.workflow_id)
        assert [edge.prerequisite_workflow_id for edge in prerequisites] == [
            dep1.workflow_id,
            dep2.workflow_id,
        ]
        assert [edge.ordinal for edge in prerequisites] == [1, 2]

        dependents = await service.list_dependents(dep1.workflow_id)
        assert [edge.dependent_workflow_id for edge in dependents] == [dependent.workflow_id]

        snapshot = await service.get_dependency_status_snapshot(
            [dep1.workflow_id, dep2.workflow_id]
        )
        assert snapshot[dep1.workflow_id].title == "Dependency 1"
        assert snapshot[dep2.workflow_id].workflow_type == "MoonMind.UserWorkflow"

@pytest.mark.asyncio
async def test_create_execution_promotes_legacy_task_payload_to_workflow(
    tmp_path, mock_client_adapter
):
    """Legacy ``task`` initial parameters must survive as canonical ``workflow``.

    Typed submissions such as the deployment update path build
    ``initial_parameters={"task": {...}}`` with no dependsOn/remediation. Prior
    to the fix the ``task`` key was popped and never re-promoted, so the
    workflow started with only ``{"failurePolicy": ...}`` and the agent_runtime
    planner failed with "requires non-empty instructions".
    """

    async with temporal_db(tmp_path) as session:
        owner_id = uuid4()
        service = TemporalExecutionService(session, client_adapter=mock_client_adapter)

        execution = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=owner_id,
            title="Update deployment stack moonmind",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy="fail_fast",
            initial_parameters={
                "task": {
                    "instructions": "Run the policy-gated deployment update operation.",
                    "steps": [
                        {
                            "id": "update-moonmind-deployment",
                            "type": "tool",
                            "tool": {
                                "type": "skill",
                                "name": "deployment.update_compose_stack",
                                "inputs": {"stack": "moonmind"},
                            },
                        }
                    ],
                }
            },
            idempotency_key=None,
        )

        record = await session.get(
            TemporalExecutionCanonicalRecord, execution.workflow_id
        )
        assert record is not None
        assert "task" not in record.parameters
        assert record.parameters["failurePolicy"] == "fail_fast"
        workflow_payload = record.parameters["workflow"]
        assert (
            workflow_payload["instructions"]
            == "Run the policy-gated deployment update operation."
        )
        assert workflow_payload["steps"][0]["tool"]["name"] == (
            "deployment.update_compose_stack"
        )

        start_args = mock_client_adapter.start_workflow.await_args.kwargs["input_args"]
        promoted = start_args["initial_parameters"]["workflow"]
        assert (
            promoted["instructions"]
            == "Run the policy-gated deployment update operation."
        )
        assert "task" not in start_args["initial_parameters"]


@pytest.mark.asyncio
async def test_create_execution_persists_remediation_link_and_supports_lookups(
    tmp_path, mock_client_adapter, monkeypatch
):
    monkeypatch.setattr(settings.workflow, "temporal_artifact_backend", "local_fs")
    monkeypatch.setattr(
        settings.workflow,
        "temporal_artifact_root",
        str(tmp_path / "artifacts"),
    )
    async with temporal_db(tmp_path) as session:
        owner_id = uuid4()
        mock_client_adapter.start_workflow.side_effect = [
            SimpleNamespace(run_id="target-temporal-run"),
            SimpleNamespace(run_id="remediation-temporal-run"),
        ]
        service = TemporalExecutionService(session, client_adapter=mock_client_adapter)

        target = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=owner_id,
            title="Target",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters=_valid_user_workflow_parameters(),
            idempotency_key=None,
        )

        remediation = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=owner_id,
            title="Remediate target",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters={
                "workflow": {
                    "instructions": "Investigate the target",
                    "remediation": {
                        "target": {"workflowId": target.workflow_id},
                        "mode": "snapshot",
                        "authorityMode": "approval_gated",
                        "trigger": {"type": "manual"},
                    },
                }
            },
            idempotency_key=None,
        )

        link = await session.get(
            TemporalExecutionRemediationLink, remediation.workflow_id
        )
        assert link is not None
        assert link.remediation_run_id == remediation.run_id
        assert link.target_workflow_id == target.workflow_id
        assert link.target_run_id == target.run_id
        assert link.mode == "snapshot"
        assert link.authority_mode == "approval_gated"
        assert link.status == "created"
        assert link.trigger_type == "manual"
        assert link.active_lock_scope is None
        assert link.active_lock_holder is None
        assert link.latest_action_summary is None
        assert link.outcome is None
        assert link.context_artifact_ref

        remediation_record = await session.get(
            TemporalExecutionCanonicalRecord, remediation.workflow_id
        )
        assert remediation_record is not None
        assert link.context_artifact_ref in (remediation_record.artifact_refs or [])
        assert remediation_record.parameters["workflow"]["remediation"]["target"] == {
            "workflowId": target.workflow_id,
            "runId": target.run_id,
        }
        context_link = (
            await session.execute(
                select(TemporalArtifactLink).where(
                    TemporalArtifactLink.workflow_id == remediation.workflow_id,
                    TemporalArtifactLink.artifact_id == link.context_artifact_ref,
                    TemporalArtifactLink.link_type == "remediation.context",
                )
            )
        ).scalar_one_or_none()
        assert context_link is not None
        start_kwargs = mock_client_adapter.start_workflow.await_args_list[1].kwargs
        assert start_kwargs["input_args"]["initial_parameters"]["workflow"][
            "remediation"
        ]["target"] == {
            "workflowId": target.workflow_id,
            "runId": target.run_id,
        }

        outbound = await service.list_remediation_targets(remediation.workflow_id)
        assert [item.target_workflow_id for item in outbound] == [target.workflow_id]

        inbound = await service.list_remediations_for_target(target.workflow_id)
        assert [item.remediation_workflow_id for item in inbound] == [
            remediation.workflow_id
        ]

        prerequisites = await service.list_prerequisites(remediation.workflow_id)
        assert prerequisites == []


@pytest.mark.asyncio
async def test_create_execution_builds_remediation_context_before_dispatch(
    tmp_path, mock_client_adapter, monkeypatch
):
    monkeypatch.setattr(settings.workflow, "temporal_artifact_backend", "local_fs")
    monkeypatch.setattr(
        settings.workflow,
        "temporal_artifact_root",
        str(tmp_path / "artifacts"),
    )
    async with temporal_db(tmp_path) as session:
        owner_id = uuid4()
        mock_client_adapter.start_workflow.side_effect = [
            SimpleNamespace(run_id="target-temporal-run"),
            SimpleNamespace(run_id="remediation-temporal-run"),
        ]
        service = TemporalExecutionService(session, client_adapter=mock_client_adapter)
        build_calls: list[str] = []
        original_builder = service._remediation_context_builder

        class RecordingBuilder:
            async def build_context(self, *, remediation_workflow_id: str):
                build_calls.append(remediation_workflow_id)
                assert mock_client_adapter.start_workflow.await_count == 1
                return await original_builder().build_context(
                    remediation_workflow_id=remediation_workflow_id
                )

        monkeypatch.setattr(
            service,
            "_remediation_context_builder",
            lambda: RecordingBuilder(),
        )

        target = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=owner_id,
            title="Target",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters=_valid_user_workflow_parameters(),
            idempotency_key=None,
        )

        remediation = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=owner_id,
            title="Remediate target",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters={
                "workflow": {
                    "instructions": "Investigate the target",
                    "remediation": {
                        "target": {"workflowId": target.workflow_id},
                    },
                }
            },
            idempotency_key=None,
        )

        assert build_calls == [remediation.workflow_id]
        assert mock_client_adapter.start_workflow.await_count == 2
        link = await session.get(
            TemporalExecutionRemediationLink, remediation.workflow_id
        )
        assert link is not None
        context_link = (
            await session.execute(
                select(TemporalArtifactLink).where(
                    TemporalArtifactLink.workflow_id == remediation.workflow_id,
                    TemporalArtifactLink.artifact_id == link.context_artifact_ref,
                    TemporalArtifactLink.link_type == "remediation.context",
                )
            )
        ).scalar_one()
        assert context_link.run_id == "remediation-temporal-run"


@pytest.mark.asyncio
async def test_record_remediation_approval_decision_appends_bounded_audit(
    tmp_path, mock_client_adapter
):
    async with temporal_db(tmp_path) as session:
        owner_id = uuid4()
        mock_client_adapter.start_workflow.return_value = SimpleNamespace(
            run_id="remediation-temporal-run"
        )
        service = TemporalExecutionService(session, client_adapter=mock_client_adapter)

        target = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=owner_id,
            title="Target",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters=_valid_user_workflow_parameters(),
            idempotency_key=None,
        )
        remediation = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=owner_id,
            title="Remediate target",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters={
                "workflow": {
                    "remediation": {
                        "target": {"workflowId": target.workflow_id},
                        "mode": "snapshot",
                        "authorityMode": "approval_gated",
                    },
                },
            },
            idempotency_key=None,
        )
        link = await session.get(
            TemporalExecutionRemediationLink, remediation.workflow_id
        )
        assert link is not None
        link.status = "awaiting_approval"
        await session.commit()

        result = await service.record_remediation_approval_decision(
            remediation_workflow_id=remediation.workflow_id,
            request_id=f"{remediation.workflow_id}:approval",
            decision="approved",
            comment="Reviewed blast radius.",
            actor="ops@example.com",
        )

        assert result == {
            "accepted": True,
            "workflowId": remediation.workflow_id,
            "requestId": f"{remediation.workflow_id}:approval",
            "decision": "approved",
        }
        record = await service.describe_execution(remediation.workflow_id)
        audit = record.memo["intervention_audit"]
        assert audit[-1]["action"] == "remediation_approval_approved"
        assert audit[-1]["transport"] == "api"
        assert audit[-1]["summary"] == "Remediation approval approved."
        assert f"{remediation.workflow_id}:approval" in audit[-1]["detail"]
        assert "ops@example.com" in audit[-1]["detail"]

@pytest.mark.asyncio
async def test_record_remediation_approval_decision_rejects_non_pending_target(
    tmp_path, mock_client_adapter
):
    async with temporal_db(tmp_path) as session:
        owner_id = uuid4()
        service = TemporalExecutionService(session, client_adapter=mock_client_adapter)

        execution = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=owner_id,
            title="Ordinary execution",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters=_valid_user_workflow_parameters(),
            idempotency_key=None,
        )

        with pytest.raises(
            TemporalExecutionValidationError,
            match="pending approval-gated remediation",
        ):
            await service.record_remediation_approval_decision(
                remediation_workflow_id=execution.workflow_id,
                request_id=f"{execution.workflow_id}:approval",
                decision="approved",
                comment=None,
                actor="ops@example.com",
            )

@pytest.mark.asyncio
async def test_create_execution_persists_supplied_matching_remediation_run_id(
    tmp_path, mock_client_adapter
):
    async with temporal_db(tmp_path) as session:
        owner_id = uuid4()
        service = TemporalExecutionService(session, client_adapter=mock_client_adapter)

        target = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=owner_id,
            title="Target",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters=_valid_user_workflow_parameters(),
            idempotency_key=None,
        )

        remediation = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=owner_id,
            title="Remediate target",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters={
                "workflow": {
                    "instructions": "Investigate the target",
                    "remediation": {
                        "target": {
                            "workflowId": target.workflow_id,
                            "runId": target.run_id,
                        },
                    },
                }
            },
            idempotency_key=None,
        )

        link = await session.get(
            TemporalExecutionRemediationLink, remediation.workflow_id
        )
        assert link is not None
        assert link.target_run_id == target.run_id
        assert link.mode == "snapshot_then_follow"
        assert link.authority_mode == "observe_only"

@pytest.mark.asyncio
async def test_create_execution_allows_observe_only_remediation_of_system_target(
    tmp_path, mock_client_adapter
):
    async with temporal_db(tmp_path) as session:
        user_owner_id = uuid4()
        service = TemporalExecutionService(session, client_adapter=mock_client_adapter)
        target = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=None,
            owner_type="system",
            title="System target",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters=_valid_user_workflow_parameters(),
            idempotency_key=None,
        )

        remediation = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=user_owner_id,
            title="Remediate system target",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters={
                "workflow": {
                    "instructions": "Investigate the target",
                    "remediation": {
                        "target": {"workflowId": target.workflow_id},
                        "authorityMode": "observe_only",
                    },
                }
            },
            idempotency_key=None,
        )

        link = await session.get(
            TemporalExecutionRemediationLink, remediation.workflow_id
        )
        assert link is not None
        assert link.target_workflow_id == target.workflow_id
        assert link.target_run_id == target.run_id
        assert link.authority_mode == "observe_only"

@pytest.mark.asyncio
async def test_create_execution_rejects_elevated_user_remediation_of_system_target(
    tmp_path, mock_client_adapter
):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session, client_adapter=mock_client_adapter)
        target = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=None,
            owner_type="system",
            title="System target",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters=_valid_user_workflow_parameters(),
            idempotency_key=None,
        )

        with pytest.raises(
            TemporalExecutionValidationError,
            match="Remediation target unauthorized",
        ):
            await service.create_execution(
                workflow_type="MoonMind.UserWorkflow",
                owner_id=uuid4(),
                title="Remediate system target",
                input_artifact_ref=None,
                plan_artifact_ref=None,
                manifest_artifact_ref=None,
                failure_policy=None,
                initial_parameters={
                    "workflow": {
                        "instructions": "Investigate the target",
                        "remediation": {
                            "target": {"workflowId": target.workflow_id},
                            "authorityMode": "admin_auto",
                        },
                    }
                },
                idempotency_key=None,
            )

@pytest.mark.asyncio
async def test_create_execution_rejects_missing_remediation_target_workflow_id(
    tmp_path, mock_client_adapter
):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session, client_adapter=mock_client_adapter)

        with pytest.raises(
            TemporalExecutionValidationError,
            match="workflow.remediation.target.workflowId is required",
        ):
            await service.create_execution(
                workflow_type="MoonMind.UserWorkflow",
                owner_id=uuid4(),
                title="Remediate target",
                input_artifact_ref=None,
                plan_artifact_ref=None,
                manifest_artifact_ref=None,
                failure_policy=None,
                initial_parameters={"workflow": {"remediation": {"target": {}}}},
                idempotency_key=None,
            )

@pytest.mark.asyncio
async def test_create_execution_rejects_remediation_run_id_identifier(
    tmp_path, mock_client_adapter
):
    async with temporal_db(tmp_path) as session:
        owner_id = uuid4()
        service = TemporalExecutionService(session, client_adapter=mock_client_adapter)
        target = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=owner_id,
            title="Target",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters=_valid_user_workflow_parameters(),
            idempotency_key=None,
        )

        with pytest.raises(
            TemporalExecutionValidationError,
            match="must use workflowId, not runId",
        ):
            await service.create_execution(
                workflow_type="MoonMind.UserWorkflow",
                owner_id=owner_id,
                title="Remediate target",
                input_artifact_ref=None,
                plan_artifact_ref=None,
                manifest_artifact_ref=None,
                failure_policy=None,
                initial_parameters={
                "workflow": {"remediation": {"target": {"workflowId": target.run_id}}}
                },
                idempotency_key=None,
            )

@pytest.mark.asyncio
async def test_create_execution_rejects_missing_remediation_target(
    tmp_path, mock_client_adapter
):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session, client_adapter=mock_client_adapter)

        with pytest.raises(
            TemporalExecutionValidationError,
            match="Remediation target not found",
        ):
            await service.create_execution(
                workflow_type="MoonMind.UserWorkflow",
                owner_id=uuid4(),
                title="Remediate target",
                input_artifact_ref=None,
                plan_artifact_ref=None,
                manifest_artifact_ref=None,
                failure_policy=None,
                initial_parameters={
                "workflow": {
                        "remediation": {
                            "target": {"workflowId": "mm:missing-remediation-target"}
                        }
                    }
                },
                idempotency_key=None,
            )

@pytest.mark.asyncio
async def test_create_execution_rejects_non_run_remediation_target(
    tmp_path, mock_client_adapter
):
    async with temporal_db(tmp_path) as session:
        owner_id = uuid4()
        service = TemporalExecutionService(session, client_adapter=mock_client_adapter)
        target = await service.create_execution(
            workflow_type="MoonMind.ManifestIngest",
            owner_id=owner_id,
            title="Manifest target",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref="artifact://manifest/1",
            failure_policy=None,
            initial_parameters={},
            idempotency_key=None,
        )

        with pytest.raises(
            TemporalExecutionValidationError,
            match="not a MoonMind.UserWorkflow workflow",
        ):
            await service.create_execution(
                workflow_type="MoonMind.UserWorkflow",
                owner_id=owner_id,
                title="Remediate target",
                input_artifact_ref=None,
                plan_artifact_ref=None,
                manifest_artifact_ref=None,
                failure_policy=None,
                initial_parameters={
                "workflow": {
                        "remediation": {
                            "target": {"workflowId": target.workflow_id}
                        }
                    }
                },
                idempotency_key=None,
            )

@pytest.mark.asyncio
async def test_create_execution_rejects_mismatched_remediation_target_run_id(
    tmp_path, mock_client_adapter
):
    async with temporal_db(tmp_path) as session:
        owner_id = uuid4()
        service = TemporalExecutionService(session, client_adapter=mock_client_adapter)
        target = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=owner_id,
            title="Target",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters=_valid_user_workflow_parameters(),
            idempotency_key=None,
        )

        with pytest.raises(
            TemporalExecutionValidationError,
            match="target.runId must match",
        ):
            await service.create_execution(
                workflow_type="MoonMind.UserWorkflow",
                owner_id=owner_id,
                title="Remediate target",
                input_artifact_ref=None,
                plan_artifact_ref=None,
                manifest_artifact_ref=None,
                failure_policy=None,
                initial_parameters={
                "workflow": {
                        "remediation": {
                            "target": {
                                "workflowId": target.workflow_id,
                                "runId": "not-current-run",
                            }
                        }
                    }
                },
                idempotency_key=None,
            )

@pytest.mark.asyncio
async def test_create_execution_rejects_remediation_same_session_branch_policy(
    tmp_path, mock_client_adapter
):
    async with temporal_db(tmp_path) as session:
        owner_id = uuid4()
        service = TemporalExecutionService(session, client_adapter=mock_client_adapter)
        target = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=owner_id,
            title="Target",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters=_valid_user_workflow_parameters(),
            idempotency_key=None,
        )

        with pytest.raises(
            TemporalExecutionValidationError,
            match="runtimeContextPolicy must be fresh_agent_run",
        ):
            await service.create_execution(
                workflow_type="MoonMind.UserWorkflow",
                owner_id=owner_id,
                title="Remediate target",
                input_artifact_ref=None,
                plan_artifact_ref=None,
                manifest_artifact_ref=None,
                failure_policy=None,
                initial_parameters={
                    "workflow": {
                        "instructions": "Remediate with an invalid same-session policy.",
                        "remediation": {
                            "target": {"workflowId": target.workflow_id},
                            "checkpointBranchPolicy": {
                                "actionKind": "checkpoint_branch.create_from_remediation_context",
                                "runtimeContextPolicy": "external_provider_continuation",
                            },
                        },
                    }
                },
                idempotency_key=None,
            )


@pytest.mark.asyncio
async def test_create_execution_rejects_remediation_branch_policy_without_fresh_session(
    tmp_path, mock_client_adapter
):
    async with temporal_db(tmp_path) as session:
        owner_id = uuid4()
        service = TemporalExecutionService(session, client_adapter=mock_client_adapter)
        target = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=owner_id,
            title="Target",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters=_valid_user_workflow_parameters(),
            idempotency_key=None,
        )

        with pytest.raises(
            TemporalExecutionValidationError,
            match="runtimeContextPolicy must be fresh_agent_run",
        ):
            await service.create_execution(
                workflow_type="MoonMind.UserWorkflow",
                owner_id=owner_id,
                title="Remediate target",
                input_artifact_ref=None,
                plan_artifact_ref=None,
                manifest_artifact_ref=None,
                failure_policy=None,
                initial_parameters={
                    "workflow": {
                        "instructions": "Remediate with bounded fresh context.",
                        "remediation": {
                            "target": {"workflowId": target.workflow_id},
                            "checkpointBranchPolicy": {
                                "actionKind": "checkpoint_branch.create_from_remediation_context",
                            },
                        },
                    }
                },
                idempotency_key=None,
            )


@pytest.mark.asyncio
async def test_create_execution_rejects_raw_remediation_evidence_refs(
    tmp_path, mock_client_adapter
):
    async with temporal_db(tmp_path) as session:
        owner_id = uuid4()
        service = TemporalExecutionService(session, client_adapter=mock_client_adapter)
        target = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=owner_id,
            title="Target",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters=_valid_user_workflow_parameters(),
            idempotency_key=None,
        )

        with pytest.raises(
            TemporalExecutionValidationError,
            match="evidencePolicy.adapterCaptureRefs\\[0\\] must use a MoonMind artifact ref",
        ):
            await service.create_execution(
                workflow_type="MoonMind.UserWorkflow",
                owner_id=owner_id,
                title="Remediate target",
                input_artifact_ref=None,
                plan_artifact_ref=None,
                manifest_artifact_ref=None,
                failure_policy=None,
                initial_parameters={
                    "workflow": {
                        "instructions": "Remediate with bounded evidence only.",
                        "remediation": {
                            "target": {"workflowId": target.workflow_id},
                            "evidencePolicy": {
                                "adapterCaptureRefs": [
                                    "https://omnigent.example/session/raw"
                                ],
                            },
                        },
                    }
                },
                idempotency_key=None,
            )


@pytest.mark.asyncio
async def test_create_execution_accepts_artifact_backed_remediation_evidence_refs(
    tmp_path, mock_client_adapter, monkeypatch
):
    monkeypatch.setattr(settings.workflow, "temporal_artifact_backend", "local_fs")
    monkeypatch.setattr(
        settings.workflow,
        "temporal_artifact_root",
        str(tmp_path / "artifacts"),
    )
    async with temporal_db(tmp_path) as session:
        owner_id = uuid4()
        mock_client_adapter.start_workflow.side_effect = [
            SimpleNamespace(run_id="target-temporal-run"),
            SimpleNamespace(run_id="remediation-temporal-run"),
        ]
        service = TemporalExecutionService(session, client_adapter=mock_client_adapter)
        target = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=owner_id,
            title="Target",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters=_valid_user_workflow_parameters(),
            idempotency_key=None,
        )

        remediation = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=owner_id,
            title="Remediate target",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters={
                "workflow": {
                    "instructions": "Remediate with bounded evidence only.",
                    "remediation": {
                        "target": {"workflowId": target.workflow_id},
                        "evidencePolicy": {
                            "adapterCaptureRefs": [
                                "artifact://omnigent/captures/session-1"
                            ],
                        },
                    },
                }
            },
            idempotency_key=None,
        )

        link = await session.get(
            TemporalExecutionRemediationLink, remediation.workflow_id
        )
        assert link is not None
        assert link.context_artifact_ref


@pytest.mark.asyncio
async def test_create_execution_rejects_unsupported_remediation_authority_mode(
    tmp_path, mock_client_adapter
):
    async with temporal_db(tmp_path) as session:
        owner_id = uuid4()
        service = TemporalExecutionService(session, client_adapter=mock_client_adapter)
        target = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=owner_id,
            title="Target",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters=_valid_user_workflow_parameters(),
            idempotency_key=None,
        )

        with pytest.raises(
            TemporalExecutionValidationError,
            match="Unsupported workflow.remediation.authorityMode",
        ):
            await service.create_execution(
                workflow_type="MoonMind.UserWorkflow",
                owner_id=owner_id,
                title="Remediate target",
                input_artifact_ref=None,
                plan_artifact_ref=None,
                manifest_artifact_ref=None,
                failure_policy=None,
                initial_parameters={
                "workflow": {
                        "remediation": {
                            "target": {"workflowId": target.workflow_id},
                            "authorityMode": "root_shell",
                        }
                    }
                },
                idempotency_key=None,
            )

@pytest.mark.asyncio
async def test_create_execution_rejects_incompatible_remediation_action_policy(
    tmp_path, mock_client_adapter
):
    async with temporal_db(tmp_path) as session:
        owner_id = uuid4()
        service = TemporalExecutionService(session, client_adapter=mock_client_adapter)
        target = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=owner_id,
            title="Target",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters=_valid_user_workflow_parameters(),
            idempotency_key=None,
        )

        with pytest.raises(
            TemporalExecutionValidationError,
            match="Unsupported workflow.remediation.actionPolicyRef",
        ):
            await service.create_execution(
                workflow_type="MoonMind.UserWorkflow",
                owner_id=owner_id,
                title="Remediate target",
                input_artifact_ref=None,
                plan_artifact_ref=None,
                manifest_artifact_ref=None,
                failure_policy=None,
                initial_parameters={
                "workflow": {
                        "remediation": {
                            "target": {"workflowId": target.workflow_id},
                            "actionPolicyRef": "unknown_policy",
                        }
                    }
                },
                idempotency_key=None,
            )

@pytest.mark.asyncio
async def test_create_execution_keeps_future_remediation_policy_inert(
    tmp_path, mock_client_adapter
):
    async with temporal_db(tmp_path) as session:
        owner_id = uuid4()
        service = TemporalExecutionService(session, client_adapter=mock_client_adapter)

        execution = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=owner_id,
            title="Policy-only task",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters={
                "workflow": {
                    "instructions": "Normal task with future policy metadata.",
                    "remediationPolicy": {
                        "enabled": True,
                        "triggers": ["failed", "attention_required", "stuck"],
                        "createMode": "proposal",
                        "templateRef": "admin_healer_default",
                        "authorityMode": "approval_gated",
                        "maxActiveRemediations": 1,
                        "maxSelfHealingDepth": 1,
                    },
                }
            },
            idempotency_key=None,
        )

        record = await session.get(
            TemporalExecutionCanonicalRecord, execution.workflow_id
        )
        link = await session.get(
            TemporalExecutionRemediationLink, execution.workflow_id
        )

        assert record is not None
        assert record.parameters["workflow"]["remediationPolicy"]["enabled"] is True
        assert link is None
        mock_client_adapter.start_workflow.assert_awaited_once()
        start_args = mock_client_adapter.start_workflow.await_args.kwargs["input_args"]
        assert "remediation" not in start_args["initial_parameters"]["workflow"]

@pytest.mark.asyncio
async def test_create_execution_rejects_nested_remediation_target(
    tmp_path, mock_client_adapter
):
    async with temporal_db(tmp_path) as session:
        owner_id = uuid4()
        service = TemporalExecutionService(session, client_adapter=mock_client_adapter)
        target = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=owner_id,
            title="Target",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters=_valid_user_workflow_parameters(),
            idempotency_key=None,
        )
        first_remediation = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=owner_id,
            title="First remediation",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters={
                "workflow": {"remediation": {"target": {"workflowId": target.workflow_id}}}
            },
            idempotency_key=None,
        )

        with pytest.raises(
            TemporalExecutionValidationError,
            match="Nested remediation targets are not supported",
        ):
            await service.create_execution(
                workflow_type="MoonMind.UserWorkflow",
                owner_id=owner_id,
                title="Nested remediation",
                input_artifact_ref=None,
                plan_artifact_ref=None,
                manifest_artifact_ref=None,
                failure_policy=None,
                initial_parameters={
                "workflow": {
                        "remediation": {
                            "target": {"workflowId": first_remediation.workflow_id}
                        }
                    }
                },
                idempotency_key=None,
            )

@pytest.mark.asyncio
async def test_create_execution_rejects_malformed_remediation_agent_run_ids(
    tmp_path, mock_client_adapter
):
    async with temporal_db(tmp_path) as session:
        owner_id = uuid4()
        service = TemporalExecutionService(session, client_adapter=mock_client_adapter)
        target = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=owner_id,
            title="Target",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters=_valid_user_workflow_parameters(),
            idempotency_key=None,
        )

        with pytest.raises(
            TemporalExecutionValidationError,
            match="workflow.remediation.target.agentRunIds must be a list of strings",
        ):
            await service.create_execution(
                workflow_type="MoonMind.UserWorkflow",
                owner_id=owner_id,
                title="Remediate target",
                input_artifact_ref=None,
                plan_artifact_ref=None,
                manifest_artifact_ref=None,
                failure_policy=None,
                initial_parameters={
                "workflow": {
                        "remediation": {
                            "target": {
                                "workflowId": target.workflow_id,
                                "agentRunIds": ["tr_valid", ""],
                            }
                        }
                    }
                },
                idempotency_key=None,
            )

@pytest.mark.asyncio
async def test_create_execution_rejects_foreign_remediation_agent_run_ids(
    tmp_path, mock_client_adapter
):
    async with temporal_db(tmp_path) as session:
        owner_id = uuid4()
        service = TemporalExecutionService(session, client_adapter=mock_client_adapter)
        target = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=owner_id,
            title="Target",
            input_artifact_ref="artifact://input/target",
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters={
                "stepLedger": {
                    "steps": [
                        {
                            "logicalStepId": "run-tests",
                            "refs": {"agentRunId": "target-agent-run"},
                        }
                    ]
                }
            },
            idempotency_key=None,
        )

        with pytest.raises(
            TemporalExecutionValidationError,
            match="workflow.remediation.target.agentRunIds must belong to the target execution",
        ):
            await service.create_execution(
                workflow_type="MoonMind.UserWorkflow",
                owner_id=owner_id,
                title="Remediate target",
                input_artifact_ref=None,
                plan_artifact_ref=None,
                manifest_artifact_ref=None,
                failure_policy=None,
                initial_parameters={
                "workflow": {
                        "remediation": {
                            "target": {
                                "workflowId": target.workflow_id,
                                "agentRunIds": ["foreign-agent-run"],
                            }
                        }
                    }
                },
                idempotency_key=None,
            )

@pytest.mark.asyncio
async def test_create_execution_accepts_owned_remediation_agent_run_ids(
    tmp_path, mock_client_adapter
):
    async with temporal_db(tmp_path) as session:
        owner_id = uuid4()
        service = TemporalExecutionService(session, client_adapter=mock_client_adapter)
        target = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=owner_id,
            title="Target",
            input_artifact_ref="artifact://input/target",
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters={
                "stepLedger": {
                    "steps": [
                        {
                            "logicalStepId": "run-tests",
                            "refs": {"agentRunId": "target-agent-run"},
                        }
                    ]
                }
            },
            idempotency_key=None,
        )

        remediation = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=owner_id,
            title="Remediate target",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters={
                "workflow": {
                    "remediation": {
                        "target": {
                            "workflowId": target.workflow_id,
                            "agentRunIds": ["target-agent-run"],
                        }
                    }
                }
            },
            idempotency_key=None,
        )

        link = await session.get(
            TemporalExecutionRemediationLink, remediation.workflow_id
        )
        assert link is not None
        assert link.target_workflow_id == target.workflow_id

@pytest.mark.asyncio
async def test_create_execution_normalizes_depends_on_before_limit_and_persistence(
    tmp_path, mock_client_adapter
):
    async with temporal_db(tmp_path) as session:
        owner_id = uuid4()
        service = TemporalExecutionService(session, client_adapter=mock_client_adapter)

        dependencies = [
            await service.create_execution(
                workflow_type="MoonMind.UserWorkflow",
                owner_id=owner_id,
                title=f"Dependency {index}",
                input_artifact_ref=None,
                plan_artifact_ref=None,
                manifest_artifact_ref=None,
                failure_policy=None,
                initial_parameters=_valid_user_workflow_parameters(),
                idempotency_key=None,
            )
            for index in range(1, 11)
        ]
        workflow_ids = [record.workflow_id for record in dependencies]

        created = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=owner_id,
            title="Normalized dependent",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters={
                "workflow": {
                    "dependsOn": workflow_ids + [workflow_ids[0], "   ", None],  # type: ignore[list-item]
                }
            },
            idempotency_key=None,
        )

        source = await session.get(
            TemporalExecutionCanonicalRecord, created.workflow_id
        )
        assert source is not None
        assert source.parameters["workflow"]["dependsOn"] == workflow_ids

@pytest.mark.asyncio
async def test_create_execution_removes_empty_normalized_depends_on_from_parameters(
    tmp_path, mock_client_adapter
):
    async with temporal_db(tmp_path) as session:
        owner_id = uuid4()
        service = TemporalExecutionService(session, client_adapter=mock_client_adapter)

        created = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=owner_id,
            title="Blank dependencies",
            input_artifact_ref="artifact://input/blank-dependencies",
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters={"workflow": {"dependsOn": [None, "   "]}},
            idempotency_key=None,
        )

        source = await session.get(
            TemporalExecutionCanonicalRecord, created.workflow_id
        )
        assert source is not None
        assert source.parameters == {
            "moonspecEnvironmentBlockedPublishAction": "fail"
        }

@pytest.mark.asyncio
async def test_validate_dependencies_rejects_self_dependency(tmp_path):
    """FR-008: A workflow MUST NOT declare itself as a dependency (DOC-REQ-007)."""
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)
        self_id = "mm:self-dependency-test-id"

        with pytest.raises(
            TemporalExecutionValidationError,
            match=f"Workflow cannot depend on itself: {self_id}",
        ):
            await service._validate_dependencies(
                depends_on=[self_id],
                new_workflow_id=self_id,
                owner_id=str(uuid4()),
                owner_type=TemporalExecutionOwnerType.USER,
            )

@pytest.mark.asyncio
async def test_mark_execution_succeeded_fans_out_dependency_resolution_signals(
    tmp_path, mock_client_adapter
):
    async with temporal_db(tmp_path) as session:
        owner_id = uuid4()
        service = TemporalExecutionService(session, client_adapter=mock_client_adapter)

        prerequisite = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=owner_id,
            title="Prerequisite",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters=_valid_user_workflow_parameters(),
            idempotency_key=None,
        )
        dependent_one = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=owner_id,
            title="Dependent one",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters={"workflow": {"dependsOn": [prerequisite.workflow_id]}},
            idempotency_key=None,
        )
        dependent_two = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=owner_id,
            title="Dependent two",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters={"workflow": {"dependsOn": [prerequisite.workflow_id]}},
            idempotency_key=None,
        )

        mock_client_adapter.signal_workflow.reset_mock()
        await service.mark_execution_succeeded(
            workflow_id=prerequisite.workflow_id,
            summary="Prerequisite completed.",
        )

        assert mock_client_adapter.signal_workflow.await_count == 2
        called_ids = {
            call.args[0] for call in mock_client_adapter.signal_workflow.await_args_list
        }
        assert called_ids == {dependent_one.workflow_id, dependent_two.workflow_id}
        payload = mock_client_adapter.signal_workflow.await_args_list[0].args[2]
        assert payload["prerequisiteWorkflowId"] == prerequisite.workflow_id
        assert payload["terminalState"] == "completed"
        assert payload["closeStatus"] == "completed"
        assert payload["failureCategory"] is None

@pytest.mark.asyncio
async def test_record_terminal_state_fans_out_dependency_resolution_signals(
    tmp_path, mock_client_adapter
):
    async with temporal_db(tmp_path) as session:
        owner_id = uuid4()
        service = TemporalExecutionService(session, client_adapter=mock_client_adapter)

        prerequisite = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=owner_id,
            title="Prerequisite",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters=_valid_user_workflow_parameters(),
            idempotency_key=None,
        )
        dependent = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=owner_id,
            title="Dependent",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters={"workflow": {"dependsOn": [prerequisite.workflow_id]}},
            idempotency_key=None,
        )

        mock_client_adapter.signal_workflow.reset_mock()
        await service.record_terminal_state(
            workflow_id=prerequisite.workflow_id,
            state="completed",
            close_status="completed",
            summary="Prerequisite completed from workflow terminal path.",
            finish_summary={
                "finishOutcome": {"code": "SUCCESS"},
                "proposals": {"submittedCount": 1},
            },
        )

        source = await session.get(
            TemporalExecutionCanonicalRecord, prerequisite.workflow_id
        )
        assert source is not None
        assert source.state is MoonMindWorkflowState.COMPLETED
        assert source.close_status is TemporalExecutionCloseStatus.COMPLETED
        assert source.finish_outcome_code == "SUCCESS"
        assert source.finish_summary_json == {
            "finishOutcome": {"code": "SUCCESS"},
            "proposals": {"submittedCount": 1},
        }
        mock_client_adapter.signal_workflow.assert_awaited_once()
        assert mock_client_adapter.signal_workflow.await_args.args[0] == (
            dependent.workflow_id
        )
        assert mock_client_adapter.signal_workflow.await_args.args[1] == (
            "DependencyResolved"
        )
        payload = mock_client_adapter.signal_workflow.await_args.args[2]
        assert payload["prerequisiteWorkflowId"] == prerequisite.workflow_id
        assert payload["terminalState"] == "completed"
        assert payload["closeStatus"] == "completed"


@pytest.mark.asyncio
async def test_record_terminal_state_updates_projection_only_child_workflow(
    tmp_path, mock_client_adapter
):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session, client_adapter=mock_client_adapter)
        owner_id = str(uuid4())
        workflow_id = "resolver:pr:1919:head:aaedf7f60dc1:h:7db9d8b831520627:1"
        now = datetime.now(UTC)
        projection = TemporalExecutionRecord(
            workflow_id=workflow_id,
            run_id=str(uuid4()),
            namespace="default",
            workflow_type=TemporalWorkflowType.USER_WORKFLOW,
            owner_id=owner_id,
            owner_type=TemporalExecutionOwnerType.USER,
            state=MoonMindWorkflowState.EXECUTING,
            close_status=None,
            entry="user_workflow",
            search_attributes={
                "mm_owner_type": "user",
                "mm_owner_id": owner_id,
                "mm_state": "executing",
                "mm_updated_at": now.isoformat(),
                "mm_entry": "user_workflow",
            },
            memo={"title": "Resolve PR #1919", "summary": "Executing."},
            artifact_refs=[],
            parameters={},
            paused=False,
            awaiting_external=False,
            waiting_reason=None,
            attention_required=False,
            projection_version=1,
            last_synced_at=now,
            sync_state=TemporalExecutionProjectionSyncState.FRESH,
            sync_error=None,
            source_mode=TemporalExecutionProjectionSourceMode.PROJECTION_ONLY,
            created_at=now,
            started_at=now,
            updated_at=now,
            closed_at=None,
        )
        session.add(projection)
        await session.commit()

        result = await service.record_terminal_state(
            workflow_id=workflow_id,
            state="failed",
            close_status="failed",
            summary="codex app-server closed unexpectedly",
            error_category="execution_error",
            finish_outcome_code="FAILED",
            finish_summary={"finishOutcome": {"code": "FAILED"}},
        )

        assert isinstance(result, TemporalExecutionRecord)
        assert result.workflow_id == workflow_id
        assert result.state is MoonMindWorkflowState.FAILED
        assert result.close_status is TemporalExecutionCloseStatus.FAILED
        assert result.closed_at is not None
        assert result.finish_outcome_code == "FAILED"
        assert result.finish_summary_json == {"finishOutcome": {"code": "FAILED"}}
        assert result.memo["summary"] == (
            "execution_error: codex app-server closed unexpectedly"
        )
        assert result.memo["error_category"] == "execution_error"
        assert result.search_attributes["mm_state"] == "failed"
        assert (
            result.source_mode
            is TemporalExecutionProjectionSourceMode.PROJECTION_ONLY
        )
        source = await session.get(TemporalExecutionCanonicalRecord, workflow_id)
        assert source is None
        mock_client_adapter.signal_workflow.assert_not_called()


@pytest.mark.asyncio
async def test_record_terminal_state_preserves_existing_terminal_summary(
    tmp_path, mock_client_adapter
):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session, client_adapter=mock_client_adapter)

        created = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=uuid4(),
            title="Cancelable run",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters=_valid_user_workflow_parameters(),
            idempotency_key=None,
        )
        await service.cancel_execution(
            workflow_id=created.workflow_id,
            reason="operator requested cancellation",
            graceful=True,
        )

        await service.record_terminal_state(
            workflow_id=created.workflow_id,
            state="canceled",
            close_status="canceled",
            summary="Execution canceled.",
        )

        canceled = await session.get(
            TemporalExecutionCanonicalRecord, created.workflow_id
        )
        assert canceled is not None
        assert canceled.state is MoonMindWorkflowState.CANCELED
        assert canceled.close_status is TemporalExecutionCloseStatus.CANCELED
        mock_client_adapter.update_workflow.assert_not_called()
        mock_client_adapter.cancel_workflow.assert_awaited_once_with(
            created.workflow_id
        )
        assert canceled.memo["summary"] == "operator requested cancellation"


@pytest.mark.asyncio
async def test_record_terminal_state_indexes_finish_summary(
    tmp_path, mock_client_adapter
):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session, client_adapter=mock_client_adapter)

        created = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=uuid4(),
            title="Finish summary run",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters=_valid_user_workflow_parameters(),
            idempotency_key=None,
        )
        finish_summary = {
            "schemaVersion": "v1",
            "jobId": created.workflow_id,
            "finishOutcome": {
                "code": "NO_CHANGES",
                "stage": "publish",
                "reason": "publish skipped: no local changes",
            },
            "publish": {"status": "skipped"},
        }

        projection = await service.record_terminal_state(
            workflow_id=created.workflow_id,
            state="completed",
            close_status="completed",
            summary="Workflow completed with no changes.",
            finish_outcome_code="NO_CHANGES",
            finish_summary=finish_summary,
        )

        source = await session.get(
            TemporalExecutionCanonicalRecord, created.workflow_id
        )
        assert source is not None
        assert source.finish_outcome_code == "NO_COMMIT"
        assert source.finish_summary_json["finishOutcome"]["code"] == "NO_COMMIT"
        assert (
            source.finish_summary_json["finishOutcome"]["reason"]
            == "No repository commit was needed."
        )
        assert isinstance(projection, TemporalExecutionRecord)
        assert projection.finish_outcome_code == "NO_COMMIT"
        assert projection.finish_summary_json["finishOutcome"]["code"] == "NO_COMMIT"


@pytest.mark.asyncio
async def test_record_terminal_state_accepts_no_commit_as_completed(
    tmp_path, mock_client_adapter
):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session, client_adapter=mock_client_adapter)

        created = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=uuid4(),
            title="No commit run",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters=_valid_user_workflow_parameters(),
            idempotency_key=None,
        )

        projection = await service.record_terminal_state(
            workflow_id=created.workflow_id,
            state="no_commit",
            summary="No repository commit was needed.",
        )

        source = await session.get(
            TemporalExecutionCanonicalRecord, created.workflow_id
        )
        assert source is not None
        assert source.state is MoonMindWorkflowState.NO_COMMIT
        assert source.close_status is TemporalExecutionCloseStatus.COMPLETED
        assert isinstance(projection, TemporalExecutionRecord)
        assert projection.state is MoonMindWorkflowState.NO_COMMIT
        assert projection.close_status is TemporalExecutionCloseStatus.COMPLETED


@pytest.mark.asyncio
async def test_record_terminal_state_accepts_legacy_state_only_at_boundary(
    tmp_path, mock_client_adapter
):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session, client_adapter=mock_client_adapter)

        created = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=uuid4(),
            title="Legacy terminal state run",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters=_valid_user_workflow_parameters(),
            idempotency_key=None,
        )

        projection = await service.record_terminal_state(
            workflow_id=created.workflow_id,
            state="no_changes",
            summary="No repository commit was needed.",
        )

        source = await session.get(
            TemporalExecutionCanonicalRecord, created.workflow_id
        )
        assert source is not None
        assert source.state is MoonMindWorkflowState.NO_COMMIT
        assert projection.state is MoonMindWorkflowState.NO_COMMIT


@pytest.mark.asyncio
async def test_parse_state_rejects_legacy_alias_for_canonical_callers(
    tmp_path, mock_client_adapter
):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session, client_adapter=mock_client_adapter)

        with pytest.raises(TemporalExecutionValidationError, match="Unsupported state"):
            service._parse_state("no_changes")


@pytest.mark.asyncio
async def test_record_terminal_state_derives_snake_case_finish_outcome_code(
    tmp_path, mock_client_adapter
):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session, client_adapter=mock_client_adapter)

        created = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=uuid4(),
            title="Snake finish summary run",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters=_valid_user_workflow_parameters(),
            idempotency_key=None,
        )
        finish_summary = {
            "schema_version": "v1",
            "job_id": created.workflow_id,
            "finish_outcome": {
                "code": "PUBLISH_DISABLED",
                "stage": "publish",
                "reason": "publishing disabled",
            },
        }

        projection = await service.record_terminal_state(
            workflow_id=created.workflow_id,
            state="completed",
            close_status="completed",
            summary="Workflow completed without publishing.",
            finish_outcome_code=None,
            finish_summary=finish_summary,
        )

        source = await session.get(
            TemporalExecutionCanonicalRecord, created.workflow_id
        )
        assert source is not None
        assert source.finish_outcome_code == "PUBLISH_DISABLED"
        assert source.finish_summary_json == finish_summary
        assert isinstance(projection, TemporalExecutionRecord)
        assert projection.finish_outcome_code == "PUBLISH_DISABLED"
        assert projection.finish_summary_json == finish_summary


@pytest.mark.asyncio
async def test_dependency_status_snapshot_repairs_stale_terminal_prerequisite(
    tmp_path, mock_client_adapter
):
    async with temporal_db(tmp_path) as session:
        owner_id = uuid4()
        service = TemporalExecutionService(session, client_adapter=mock_client_adapter)

        prerequisite = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=owner_id,
            title="Prerequisite",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters=_valid_user_workflow_parameters(),
            idempotency_key=None,
        )
        dependent = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=owner_id,
            title="Dependent",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters={"workflow": {"dependsOn": [prerequisite.workflow_id]}},
            idempotency_key=None,
        )

        completed_at = datetime(2026, 4, 21, 22, 8, tzinfo=UTC)
        description = Mock(spec=WorkflowExecutionDescription)
        description.id = prerequisite.workflow_id
        description.run_id = prerequisite.run_id
        description.namespace = "default"
        description.workflow_type = "MoonMind.UserWorkflow"
        description.status = WorkflowExecutionStatus.COMPLETED
        description.start_time = prerequisite.created_at
        description.execution_time = prerequisite.created_at
        description.close_time = completed_at

        async def memo() -> dict[str, object]:
            return {
                "entry": "user_workflow",
                "title": "Prerequisite",
                "summary": "Workflow completed successfully",
            }

        description.memo = memo
        description.search_attributes = {}
        mock_client_adapter.describe_workflow.return_value = description
        mock_client_adapter.signal_workflow.reset_mock()

        snapshot = await service.get_dependency_status_snapshot(
            [prerequisite.workflow_id]
        )

        assert snapshot[prerequisite.workflow_id].state == "completed"
        assert snapshot[prerequisite.workflow_id].close_status == "completed"
        source = await session.get(
            TemporalExecutionCanonicalRecord, prerequisite.workflow_id
        )
        assert source is not None
        assert source.state is MoonMindWorkflowState.COMPLETED
        assert source.close_status is TemporalExecutionCloseStatus.COMPLETED
        assert source.closed_at is not None
        assert source.closed_at.replace(tzinfo=UTC) == completed_at
        mock_client_adapter.signal_workflow.assert_awaited_once()
        assert (
            mock_client_adapter.signal_workflow.await_args.args[0]
            == dependent.workflow_id
        )
        assert mock_client_adapter.signal_workflow.await_args.args[1] == (
            "DependencyResolved"
        )
        payload = mock_client_adapter.signal_workflow.await_args.args[2]
        assert payload["prerequisiteWorkflowId"] == prerequisite.workflow_id
        assert payload["terminalState"] == "completed"

@pytest.mark.asyncio
async def test_dependency_status_snapshot_returns_stale_record_when_terminal_sync_fails(
    tmp_path, mock_client_adapter
):
    async with temporal_db(tmp_path) as session:
        owner_id = uuid4()
        service = TemporalExecutionService(session, client_adapter=mock_client_adapter)

        prerequisite = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=owner_id,
            title="Prerequisite",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters=_valid_user_workflow_parameters(),
            idempotency_key=None,
        )
        prerequisite_workflow_id = prerequisite.workflow_id

        description = Mock(spec=WorkflowExecutionDescription)
        description.status = WorkflowExecutionStatus.COMPLETED
        mock_client_adapter.describe_workflow.return_value = description
        mock_client_adapter.signal_workflow.reset_mock()

        with patch(
            "api_service.core.sync.sync_execution_projection",
            new=AsyncMock(side_effect=RuntimeError("sync failed")),
        ):
            snapshot = await service.get_dependency_status_snapshot(
                [prerequisite_workflow_id]
            )

        assert snapshot[prerequisite_workflow_id].state == "initializing"
        assert snapshot[prerequisite_workflow_id].close_status is None
        mock_client_adapter.signal_workflow.assert_not_awaited()

@pytest.mark.asyncio
async def test_mark_execution_failed_fanout_is_best_effort(tmp_path, mock_client_adapter):
    async with temporal_db(tmp_path) as session:
        owner_id = uuid4()
        service = TemporalExecutionService(session, client_adapter=mock_client_adapter)

        prerequisite = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=owner_id,
            title="Prerequisite",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters=_valid_user_workflow_parameters(),
            idempotency_key=None,
        )
        dependent_one = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=owner_id,
            title="Dependent one",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters={"workflow": {"dependsOn": [prerequisite.workflow_id]}},
            idempotency_key=None,
        )
        dependent_two = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=owner_id,
            title="Dependent two",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters={"workflow": {"dependsOn": [prerequisite.workflow_id]}},
            idempotency_key=None,
        )

        mock_client_adapter.signal_workflow.reset_mock()
        mock_client_adapter.signal_workflow.side_effect = [
            RuntimeError("already closed"),
            None,
        ]

        failed = await service.mark_execution_failed(
            workflow_id=prerequisite.workflow_id,
            error_category="execution_error",
            message="boom",
        )

        assert failed.state is MoonMindWorkflowState.FAILED
        assert mock_client_adapter.signal_workflow.await_count == 2
        called_ids = {
            call.args[0] for call in mock_client_adapter.signal_workflow.await_args_list
        }
        assert called_ids == {dependent_one.workflow_id, dependent_two.workflow_id}

@pytest.mark.asyncio
async def test_create_execution_returns_existing_record_after_idempotency_race(
    tmp_path, monkeypatch
):
    db_url = "sqlite+aiosqlite://"
    from sqlalchemy.pool import StaticPool

    engine = create_async_engine(
        db_url,
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    try:
        async with (
            session_factory() as winner_session,
            session_factory() as loser_session,
        ):
            winner_service = TemporalExecutionService(winner_session)
            loser_service = TemporalExecutionService(loser_session)
            owner_id = uuid4()
            key = "create-race"

            async def race_precheck(
                *,
                idempotency_key,
                owner_id,
                owner_type,
                workflow_type,
            ):
                if idempotency_key == key:
                    await winner_service.create_execution(
                        workflow_type="MoonMind.UserWorkflow",
                        owner_id=owner_id,
                        title="winner",
                        input_artifact_ref=None,
                        plan_artifact_ref=None,
                        manifest_artifact_ref=None,
                        failure_policy=None,
                        initial_parameters=_valid_user_workflow_parameters(),
                        idempotency_key=key,
                    )
                    monkeypatch.setattr(
                        loser_service,
                        "_find_by_create_idempotency",
                        original_find,
                    )
                return None

            original_find = loser_service._find_by_create_idempotency
            monkeypatch.setattr(
                loser_service,
                "_find_by_create_idempotency",
                race_precheck,
            )

            original_commit = loser_session.commit

            async def race_commit():
                await loser_session.flush()
                raise IntegrityError("insert", {}, Exception("duplicate"))

            monkeypatch.setattr(loser_session, "commit", race_commit)

            record = await loser_service.create_execution(
                workflow_type="MoonMind.UserWorkflow",
                owner_id=owner_id,
                title="loser",
                input_artifact_ref=None,
                plan_artifact_ref=None,
                manifest_artifact_ref=None,
                failure_policy=None,
                initial_parameters=_valid_user_workflow_parameters(),
                idempotency_key=key,
            )

            assert record.memo["title"] == "winner"
            assert record.create_idempotency_key == key

            monkeypatch.setattr(loser_session, "commit", original_commit)
    finally:
        await engine.dispose()

@pytest.mark.asyncio
async def test_create_execution_scopes_idempotency_by_owner_type(tmp_path):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)

        user_record = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id="shared-owner",
            owner_type="user",
            title="user owned",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters=_valid_user_workflow_parameters(),
            idempotency_key="shared-idempotency-key",
        )
        service_record = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id="shared-owner",
            owner_type="service",
            title="service owned",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters=_valid_user_workflow_parameters(),
            idempotency_key="shared-idempotency-key",
        )
        service_retry = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id="shared-owner",
            owner_type="service",
            title="service retry",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters=_valid_user_workflow_parameters(),
            idempotency_key="shared-idempotency-key",
        )

        assert user_record.workflow_id != service_record.workflow_id
        assert service_retry.workflow_id == service_record.workflow_id
        assert service_retry.memo["title"] == "service owned"

@pytest.mark.asyncio
async def test_list_executions_syncs_page_in_single_projection_commit(
    tmp_path, monkeypatch
):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)

        await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=uuid4(),
            title="first",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters=_valid_user_workflow_parameters(),
            idempotency_key=None,
        )
        await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=uuid4(),
            title="second",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters=_valid_user_workflow_parameters(),
            idempotency_key=None,
        )

        original_commit = session.commit
        commit_calls = 0

        async def counting_commit():
            nonlocal commit_calls
            commit_calls += 1
            await original_commit()

        monkeypatch.setattr(session, "commit", counting_commit)

        result = await service.list_executions(
            workflow_type=None,
            owner_type=None,
            state=None,
            owner_id=None,
            entry=None,
            page_size=2,
            next_page_token=None,
        )

        assert len(result.items) == 2
        assert commit_calls == 1

@pytest.mark.asyncio
async def test_request_rerun_uses_continue_as_new_same_workflow_id(
    tmp_path, mock_client_adapter
):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)
        service._client_adapter = mock_client_adapter

        created = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=uuid4(),
            title=None,
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters={
                "agentRunId": "6f8b6bf7-6e0c-4d71-9b08-18d489f17a8d",
                "recoverySource": {"workflowId": "mm:source", "runId": "run-old"},
                "recoveryCheckpointRef": "artifact://checkpoint/old",
                "preservedSteps": [{"id": "step-1"}],
                "completedSteps": [{"id": "step-0"}],
                "workflow": {
                    "instructions": "Original task",
                    "recovery": {
                        "kind": "recover_from_failed_step",
                        "sourceWorkflowId": "mm:source",
                        "sourceRunId": "run-old",
                    },
                    "resume": {
                        "kind": "recover_from_failed_step",
                        "sourceWorkflowId": "mm:source",
                        "sourceRunId": "run-old",
                        "failedStepId": "implement",
                        "recoveryCheckpointRef": "artifact://checkpoint/old",
                        "taskInputSnapshotRef": "artifact://snapshot/old",
                    },
                },
            },
            idempotency_key=None,
        )
        created.memo["agentRunId"] = "6f8b6bf7-6e0c-4d71-9b08-18d489f17a8d"
        await session.commit()

        original_run_id = created.run_id
        # After creation, started_at is None until a running-state transition.
        assert created.started_at is None
        workflow_id = created.workflow_id
        response = await service.update_execution(
            workflow_id=workflow_id,
            update_name="RequestRerun",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            parameters_patch=None,
            title=None,
            new_manifest_artifact_ref=None,
            mode=None,
            max_concurrency=None,
            node_ids=None,
            idempotency_key="rerun-1",
        )

        refreshed = await service.describe_execution(workflow_id)
        assert response["accepted"] is True
        assert response["applied"] == "continue_as_new"
        assert response["continue_as_new_cause"] == "manual_rerun"
        assert refreshed.workflow_id == workflow_id
        assert refreshed.run_id != original_run_id
        # started_at now gets populated on first running-state transition
        assert refreshed.started_at is not None
        assert refreshed.rerun_count == 1
        assert refreshed.memo["continue_as_new_cause"] == "manual_rerun"
        assert refreshed.memo["latest_temporal_run_id"] == refreshed.run_id
        assert "agentRunId" not in refreshed.memo
        assert "agentRunId" not in refreshed.parameters
        assert "recoverySource" not in refreshed.parameters
        assert "recoveryCheckpointRef" not in refreshed.parameters
        assert "preservedSteps" not in refreshed.parameters
        assert "completedSteps" not in refreshed.parameters
        assert refreshed.parameters["workflow"] == {
            "instructions": "Original task",
            "recovery": {
                "kind": "exact_full_rerun",
                "sourceWorkflowId": workflow_id,
                "sourceRunId": original_run_id,
            },
        }
        assert refreshed.search_attributes["mm_continue_as_new_cause"] == "manual_rerun"

@pytest.mark.asyncio
async def test_request_rerun_creates_fresh_execution_for_terminal_execution(
    tmp_path, mock_client_adapter
):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)
        service._client_adapter = mock_client_adapter

        created = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=uuid4(),
            title=None,
            input_artifact_ref=None,
            plan_artifact_ref="artifact://plan/1",
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters={
                "agentRunId": "old-agent-run",
                "agent_run_id": "old-agent-run-snake",
                "recoverySource": {"workflowId": "mm:source", "runId": "run-old"},
                "recoveryCheckpointRef": "artifact://checkpoint/old",
                "preservedSteps": [{"id": "step-1"}],
                "completedSteps": [{"id": "step-0"}],
                "workflow": {
                    "instructions": "Original task",
                    "recovery": {
                        "kind": "recover_from_failed_step",
                        "sourceWorkflowId": "mm:source",
                        "sourceRunId": "run-old",
                    },
                    "resume": {
                        "kind": "recover_from_failed_step",
                        "sourceWorkflowId": "mm:source",
                        "sourceRunId": "run-old",
                        "failedStepId": "implement",
                        "recoveryCheckpointRef": "artifact://checkpoint/old",
                        "taskInputSnapshotRef": "artifact://snapshot/old",
                    },
                },
            },
            idempotency_key=None,
        )

        await service.cancel_execution(
            workflow_id=created.workflow_id,
            reason="done",
            graceful=True,
        )
        service._client_adapter.update_workflow.reset_mock()

        source_workflow_id = created.workflow_id
        source_run_id = created.run_id

        response = await service.update_execution(
            workflow_id=created.workflow_id,
            update_name="RequestRerun",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            parameters_patch=None,
            title=None,
            new_manifest_artifact_ref=None,
            mode=None,
            max_concurrency=None,
            node_ids=None,
            idempotency_key="rerun-terminal",
        )
        source = await service.describe_execution(source_workflow_id)
        rerun = await service.describe_execution(response["workflow_id"])

        assert response["accepted"] is True
        assert response["applied"] == "continue_as_new"
        assert response["continue_as_new_cause"] == "manual_rerun"
        assert response["workflow_id"] != source_workflow_id
        assert source.state is MoonMindWorkflowState.CANCELED
        assert source.close_status is TemporalExecutionCloseStatus.CANCELED
        assert rerun.state is MoonMindWorkflowState.INITIALIZING
        assert rerun.parameters["rerunSource"] == {
            "workflowId": source_workflow_id,
            "runId": source_run_id,
        }
        assert "agentRunId" not in rerun.parameters
        assert "agent_run_id" not in rerun.parameters
        assert "recoverySource" not in rerun.parameters
        assert "recoveryCheckpointRef" not in rerun.parameters
        assert "preservedSteps" not in rerun.parameters
        assert "completedSteps" not in rerun.parameters
        assert rerun.parameters["workflow"] == {
            "instructions": "Original task",
            "recovery": {
                "kind": "exact_full_rerun",
                "sourceWorkflowId": source_workflow_id,
                "sourceRunId": source_run_id,
            },
        }
        assert service._client_adapter.update_workflow.await_count == 0


@pytest.mark.asyncio
async def test_request_rerun_pins_patch_recovery_to_terminal_source_execution(
    tmp_path, mock_client_adapter
):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)
        service._client_adapter = mock_client_adapter

        created = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=uuid4(),
            title=None,
            input_artifact_ref=None,
            plan_artifact_ref="artifact://plan/1",
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters={"workflow": {"instructions": "Original task"}},
            idempotency_key=None,
        )
        await service.cancel_execution(
            workflow_id=created.workflow_id,
            reason="done",
            graceful=True,
        )

        source_workflow_id = created.workflow_id
        source_run_id = created.run_id

        response = await service.update_execution(
            workflow_id=source_workflow_id,
            update_name="RequestRerun",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            parameters_patch={"workflow": {
                    "instructions": "Edited task",
                    "recovery": {"kind": "edited_full_retry"},
                }
            },
            title=None,
            new_manifest_artifact_ref=None,
            mode=None,
            max_concurrency=None,
            node_ids=None,
            idempotency_key="rerun-edited",
        )
        rerun = await service.describe_execution(response["workflow_id"])

        assert rerun.parameters["workflow"]["recovery"] == {
            "kind": "edited_full_retry",
            "sourceWorkflowId": source_workflow_id,
            "sourceRunId": source_run_id,
        }


def test_full_retry_recovery_from_patch_rejects_non_string_source_ids():
    with pytest.raises(
        TemporalExecutionValidationError,
        match="workflow.recovery.sourceWorkflowId must be a string",
    ):
        TemporalExecutionService._full_retry_recovery_from_patch(
            {
                "workflow": {
                    "recovery": {
                        "kind": "edited_full_retry",
                        "sourceWorkflowId": 123,
                        "sourceRunId": "run-source",
                    }
                }
            },
            source_workflow_id="mm:source",
            source_run_id="run-source",
        )


def test_full_retry_recovery_from_patch_rejects_forged_source_ids():
    with pytest.raises(
        TemporalExecutionValidationError,
        match="source identifiers must match",
    ):
        TemporalExecutionService._full_retry_recovery_from_patch(
            {
                "workflow": {
                    "recovery": {
                        "kind": "edited_full_retry",
                        "sourceWorkflowId": "mm:other",
                        "sourceRunId": "run-source",
                    }
                }
            },
            source_workflow_id="mm:source",
            source_run_id="run-source",
        )


@pytest.mark.parametrize(
    ("source_workflow_id", "source_run_id"),
    [("", "run-source"), ("mm:source", " "), (None, "run-source")],
)
def test_exact_full_rerun_recovery_rejects_missing_source_identity(
    source_workflow_id: str | None,
    source_run_id: str | None,
) -> None:
    with pytest.raises(
        TemporalExecutionValidationError,
        match="Rerun source workflowId and runId are required",
    ):
        TemporalExecutionService._exact_full_rerun_recovery(
            source_workflow_id=source_workflow_id,
            source_run_id=source_run_id,
        )


def _valid_recovery_checkpoint_payload(
    *,
    workflow_id: str,
    run_id: str,
    snapshot_ref: str,
) -> dict[str, object]:
    return {
        "schemaVersion": "v1",
        "source": {"workflowId": workflow_id, "runId": run_id},
        "taskInputSnapshotRef": snapshot_ref,
        "planDigest": "sha256:plan",
        "failedStep": {
            "logicalStepId": "implement",
            "order": 2,
            "executionOrdinal": 1,
            "title": "Implement",
        },
        "preservedSteps": [
            {
                "logicalStepId": "plan",
                "order": 1,
                "status": "succeeded",
                "sourceExecutionOrdinal": 1,
                "artifacts": {"outputSummary": "artifact://summary"},
                "stateCheckpointRef": "artifact://workspace/before-plan",
            }
        ],
        "preparedArtifactRefs": ["artifact://prepared"],
        "recoveryWorkspace": {
            "branch": "feature",
            "commit": "abc123",
            "checkpointRef": "artifact://checkpoint/source",
        },
    }


def _valid_failed_run_recovery_manifest_payload(
    *,
    workflow_id: str,
    run_id: str,
    checkpoint_ref: str = "artifact://checkpoint/source",
    failed_step_id: str = "implement",
    failed_execution_ordinal: int = 1,
) -> dict[str, object]:
    return {
        "schemaVersion": "v1",
        "contentType": FAILED_RUN_RECOVERY_MANIFEST_CONTENT_TYPE,
        "workflowId": workflow_id,
        "runId": run_id,
        "failedLogicalStepId": failed_step_id,
        "failedExecutionOrdinal": failed_execution_ordinal,
        "checkpointRefs": [
            {
                "category": "checkpoint",
                "status": "available",
                "artifactRef": checkpoint_ref,
                "boundary": "before_recovery_restoration",
            }
        ],
        "validation": {
            "result": "valid",
            "checkpointRef": checkpoint_ref,
            "boundary": "before_recovery_restoration",
        },
        "sideEffectDispositions": [],
        "resumeAllowed": True,
        "recoveryEligibility": {
            "eligible": True,
            "defaultAction": "resume_from_checkpoint",
            "requiredBoundary": "before_recovery_restoration",
            "checkpointRef": checkpoint_ref,
            "sourceWorkflowId": workflow_id,
            "sourceRunId": run_id,
            "operatorGuidance": "resume",
            "evidence": [
                {
                    "category": "checkpoint",
                    "status": "available",
                    "artifactRef": checkpoint_ref,
                    "boundary": "before_recovery_restoration",
                }
            ],
        },
        "createdAt": "2026-06-13T12:00:00+00:00",
    }


def test_recovery_checkpoint_model_requires_plan_identity() -> None:
    with pytest.raises(ValidationError, match="plan identity"):
        RecoveryCheckpointModel.model_validate(
            {
                "schemaVersion": "v1",
                "source": {"workflowId": "mm:source", "runId": "run-source"},
                "taskInputSnapshotRef": "artifact://snapshot",
                "failedStep": {
                    "logicalStepId": "implement",
                    "order": 2,
                    "executionOrdinal": 1,
                },
                "recoveryWorkspace": {"branch": "feature", "commit": "abc123"},
            }
        )


def test_recovery_checkpoint_model_requires_workspace_checkpoint() -> None:
    with pytest.raises(ValidationError, match="workspace"):
        RecoveryCheckpointModel.model_validate(
            {
                "schemaVersion": "v1",
                "source": {"workflowId": "mm:source", "runId": "run-source"},
                "taskInputSnapshotRef": "artifact://snapshot",
                "planDigest": "sha256:plan",
                "failedStep": {
                    "logicalStepId": "implement",
                    "order": 2,
                    "executionOrdinal": 1,
                },
            }
        )


def test_recovery_checkpoint_model_requires_preserved_step_state_checkpoint() -> None:
    with pytest.raises(ValidationError, match="state checkpoint"):
        RecoveryCheckpointModel.model_validate(
            {
                "schemaVersion": "v1",
                "source": {"workflowId": "mm:source", "runId": "run-source"},
                "taskInputSnapshotRef": "artifact://snapshot",
                "planDigest": "sha256:plan",
                "failedStep": {
                    "logicalStepId": "implement",
                    "order": 2,
                    "executionOrdinal": 1,
                },
                "preservedSteps": [
                    {
                        "logicalStepId": "plan",
                        "order": 1,
                        "status": "succeeded",
                        "sourceExecutionOrdinal": 1,
                        "artifacts": {"outputSummary": "artifact://summary"},
                    }
                ],
                "recoveryWorkspace": {"branch": "feature", "commit": "abc123"},
            }
        )


def test_recovery_checkpoint_model_accepts_complete_evidence() -> None:
    checkpoint = RecoveryCheckpointModel.model_validate(
        {
            "schemaVersion": "v1",
            "source": {"workflowId": "mm:source", "runId": "run-source"},
            "taskInputSnapshotRef": "artifact://snapshot",
            "planDigest": "sha256:plan",
            "failedStep": {
                "logicalStepId": "implement",
                "order": 2,
                "executionOrdinal": 1,
            },
            "recoveryWorkspace": {"branch": "feature", "commit": "abc123"},
        }
    )

    assert checkpoint.preserved_steps == []
    assert checkpoint.prepared_artifact_refs == []
    assert checkpoint.recovery_workspace == {"branch": "feature", "commit": "abc123"}


def test_recovery_checkpoint_model_rejects_inline_checkpoint_payload() -> None:
    payload = _valid_recovery_checkpoint_payload(
        workflow_id="mm:source",
        run_id="run-source",
        snapshot_ref="artifact://snapshot",
    )
    payload["recoveryWorkspace"] = {
        "branch": "feature",
        "commit": "abc123",
        "inlineCheckpointPayload": "x" * 4096,
    }

    with pytest.raises(ValidationError, match="inline checkpoint payload"):
        RecoveryCheckpointModel.model_validate(payload)


def test_recovery_checkpoint_model_allows_checkpoint_payload_ref_keys() -> None:
    payload = _valid_recovery_checkpoint_payload(
        workflow_id="mm:source",
        run_id="run-source",
        snapshot_ref="artifact://snapshot",
    )
    payload["recoveryWorkspace"] = {
        "checkpoint_payload_ref": "artifact://checkpoint/payload",
        "inline_checkpoint_metadata": "artifact://checkpoint/metadata",
    }

    checkpoint = RecoveryCheckpointModel.model_validate(payload)

    assert checkpoint.recovery_workspace == {
        "checkpoint_payload_ref": "artifact://checkpoint/payload",
        "inline_checkpoint_metadata": "artifact://checkpoint/metadata",
    }


def _typed_failed_step_recovery_target(
    *, source_workflow_id: str, source_run_id: str, destination_workflow_id: str
) -> WorkflowRecoveryTargetModel:
    capability = resolve_runtime_execution_capabilities("omnigent").model_dump(
        by_alias=True, mode="json"
    )
    capability["checkpointBoundarySupport"]["before_execution"] = [
        "rerun_failed_step"
    ]
    capability["workspaceState"]["boundarySupport"]["before_execution"] = [
        "rerun_failed_step"
    ]
    capability["capabilityDigest"] = ""
    capability = RuntimeExecutionCapabilities.model_validate(
        capability
    ).with_digest().model_dump(by_alias=True, mode="json")
    checkpoint_digest = "sha256:typed-checkpoint"
    return WorkflowRecoveryTargetModel.model_validate(
        {
            "target": {
                "kind": "failed_step",
                "logicalStepId": "implement",
                "sourceStepExecutionId": "step-execution-1",
            },
            "source": {
                "workflowId": source_workflow_id,
                "runId": source_run_id,
                "planRef": "artifact://plan/source",
                "planDigest": "sha256:plan",
                "taskInputSnapshotRef": "artifact://snapshot/source",
            },
            "checkpoint": {
                "ref": "artifact://checkpoint/source",
                "boundary": "before_execution",
                "kind": "worktree_archive",
                "digest": checkpoint_digest,
                "validationRef": "artifact://checkpoint-validation",
                "sourceWorkspaceRef": "workspace://source",
            },
            "continuation": {"phase": "rerun_failed_step"},
            "capabilitySnapshot": capability,
            "preservedStepRefs": ["artifact://preserved"],
            "sideEffectDispositionRef": "artifact://side-effects",
            "sideEffectSafe": True,
            "destination": {
                "workflowId": destination_workflow_id,
                "creationKey": deterministic_recovery_creation_key(
                    source_workflow_id,
                    source_run_id,
                    "failed_step",
                    checkpoint_digest,
                ),
                "runtimeId": "omnigent",
                "executionProfileRef": "provider-profile:primary",
                "workspaceReservationId": "workspace-reservation:destination",
            },
        }
    )


@pytest.mark.asyncio
async def test_typed_recovery_creates_one_pinned_destination_and_frozen_lineage(
    tmp_path, mock_client_adapter
):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session, client_adapter=mock_client_adapter)
        source = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=uuid4(),
            title="typed recovery source",
            input_artifact_ref="artifact://snapshot/source",
            plan_artifact_ref="artifact://plan/source",
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters={
                "agentRunId": "source-agent-run",
                "workflow": {"title": "source", "instructions": "Original"},
            },
            idempotency_key=None,
        )
        source.state = MoonMindWorkflowState.FAILED
        source.close_status = TemporalExecutionCloseStatus.FAILED
        await session.commit()
        target = _typed_failed_step_recovery_target(
            source_workflow_id=source.workflow_id,
            source_run_id=source.run_id,
            destination_workflow_id="mm:typed-recovery-destination",
        )

        first = await service.create_typed_recovery_execution(
            source, recovery_target=target
        )
        second = await service.create_typed_recovery_execution(
            source, recovery_target=target
        )

        assert first["execution"] == second["execution"]
        assert first["execution"]["workflowId"] == "mm:typed-recovery-destination"
        destination = await service.describe_execution(
            first["execution"]["workflowId"]
        )
        assert destination.parameters["recoveryTarget"] == target.model_dump(
            by_alias=True, mode="json"
        )
        assert destination.parameters["recoveryLineage"]["source"] == {
            "workflowId": source.workflow_id,
            "runId": source.run_id,
            "planRef": "artifact://plan/source",
            "planDigest": "sha256:plan",
            "taskInputSnapshotRef": "artifact://snapshot/source",
        }
        assert destination.parameters["recoveryLineage"]["destinationRunId"] == (
            destination.run_id
        )
        assert "agentRunId" not in destination.parameters
        refreshed_source = await service.describe_execution(source.workflow_id)
        assert refreshed_source.state is MoonMindWorkflowState.FAILED
        assert "recoveryTarget" not in refreshed_source.parameters


@pytest.mark.asyncio
async def test_failed_step_recovery_creates_linked_execution_with_source_identity(
    tmp_path, mock_client_adapter
):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)
        service._client_adapter = mock_client_adapter

        created = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=uuid4(),
            title="recover source",
            input_artifact_ref="artifact://input/source",
            plan_artifact_ref="artifact://plan/source",
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters={
                "agentRunId": "old-agent-run",
                "workflow": {"title": "recovery source", "instructions": "Original"},
            },
            idempotency_key=None,
        )
        created.state = MoonMindWorkflowState.FAILED
        created.close_status = TemporalExecutionCloseStatus.FAILED
        created.memo = {
            **created.memo,
            "task_input_snapshot_ref": "artifact://snapshot/source",
            "recovery_checkpoint_ref": "artifact://checkpoint/source",
        }
        await session.commit()

        result = await service.create_failed_step_recovery_execution(
            created,
            recovery_checkpoint_ref=None,
            idempotency_key="recover-1",
            checkpoint_payload=_valid_recovery_checkpoint_payload(
                workflow_id=created.workflow_id,
                run_id=created.run_id,
                snapshot_ref="artifact://snapshot/source",
            ),
            failed_run_recovery_manifest_ref="artifact://recovery/manifest",
            failed_run_recovery_manifest=_valid_failed_run_recovery_manifest_payload(
                workflow_id=created.workflow_id,
                run_id=created.run_id,
            ),
        )

        resumed = await service.describe_execution(result["execution"]["workflowId"])
        assert result["applied"] == "created_resumed_execution"
        assert result["source"] == {
            "workflowId": created.workflow_id,
            "runId": created.run_id,
        }
        assert resumed.parameters["recoverySource"]["sourceWorkflowId"] == created.workflow_id
        assert resumed.parameters["recoverySource"]["sourceRunId"] == created.run_id
        assert resumed.parameters["recoverySource"]["failedStepId"] == "implement"
        assert resumed.parameters["recoverySource"]["recoveryWorkspace"] == {
            "branch": "feature",
            "commit": "abc123",
            "checkpointRef": "artifact://checkpoint/source",
        }
        assert resumed.parameters["recoverySource"]["preservedSteps"][0][
            "logicalStepId"
        ] == "plan"
        task_payload = resumed.parameters["workflow"]
        assert task_payload["recovery"] == {
            "kind": "recover_from_failed_step",
            "sourceWorkflowId": created.workflow_id,
            "sourceRunId": created.run_id,
        }
        assert task_payload["resume"] == {
            "kind": "recover_from_failed_step",
            "sourceWorkflowId": created.workflow_id,
            "sourceRunId": created.run_id,
            "failedStepId": "implement",
            "failedStepExecution": 1,
            "recoveryCheckpointRef": "artifact://checkpoint/source",
            "checkpointBoundary": "before_recovery_restoration",
            "taskInputSnapshotRef": "artifact://snapshot/source",
            "planRef": "artifact://plan/source",
            "planDigest": "sha256:plan",
            "preservedStepRefs": [
                "artifact://workspace/before-plan",
                "artifact://summary",
            ],
            "dependencySignatures": {},
            "workspacePolicy": "restore_pre_execution",
            "failedRunRecoveryManifestRef": "artifact://recovery/manifest",
        }
        assert "agentRunId" not in resumed.parameters


@pytest.mark.asyncio
async def test_failed_step_recovery_accepts_manifest_checkpoint_without_memo_ref(
    tmp_path, mock_client_adapter
):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)
        service._client_adapter = mock_client_adapter

        created = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=uuid4(),
            title="recover source",
            input_artifact_ref="artifact://input/source",
            plan_artifact_ref="artifact://plan/source",
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters={
                "workflow": {"title": "recovery source", "instructions": "Original"},
            },
            idempotency_key=None,
        )
        created.state = MoonMindWorkflowState.FAILED
        created.close_status = TemporalExecutionCloseStatus.FAILED
        created.memo = {
            **created.memo,
            "task_input_snapshot_ref": "artifact://snapshot/source",
        }
        await session.commit()

        result = await service.create_failed_step_recovery_execution(
            created,
            recovery_checkpoint_ref=None,
            idempotency_key="recover-manifest-ref",
            checkpoint_payload=_valid_recovery_checkpoint_payload(
                workflow_id=created.workflow_id,
                run_id=created.run_id,
                snapshot_ref="artifact://snapshot/source",
            ),
            failed_run_recovery_manifest_ref="artifact://recovery/manifest",
            failed_run_recovery_manifest=_valid_failed_run_recovery_manifest_payload(
                workflow_id=created.workflow_id,
                run_id=created.run_id,
                checkpoint_ref="artifact://checkpoint/source",
            ),
        )

        resumed = await service.describe_execution(result["execution"]["workflowId"])
        assert result["applied"] == "created_resumed_execution"
        assert (
            resumed.parameters["workflow"]["resume"]["recoveryCheckpointRef"]
            == "artifact://checkpoint/source"
        )


@pytest.mark.asyncio
async def test_failed_step_recovery_accepts_legacy_checkpoint_memo_key(
    tmp_path, mock_client_adapter
):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)
        service._client_adapter = mock_client_adapter

        created = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=uuid4(),
            title="recover source",
            input_artifact_ref="artifact://input/source",
            plan_artifact_ref="artifact://plan/source",
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters={
                "workflow": {"title": "recovery source", "instructions": "Original"},
            },
            idempotency_key=None,
        )
        created.state = MoonMindWorkflowState.FAILED
        created.close_status = TemporalExecutionCloseStatus.FAILED
        created.memo = {
            **created.memo,
            "task_input_snapshot_ref": "artifact://snapshot/source",
            "resume_checkpoint_ref": "artifact://checkpoint/source",
        }
        await session.commit()

        result = await service.create_failed_step_recovery_execution(
            created,
            recovery_checkpoint_ref=None,
            idempotency_key="recover-legacy-key",
            checkpoint_payload=_valid_recovery_checkpoint_payload(
                workflow_id=created.workflow_id,
                run_id=created.run_id,
                snapshot_ref="artifact://snapshot/source",
            ),
            failed_run_recovery_manifest_ref="artifact://recovery/manifest",
            failed_run_recovery_manifest=_valid_failed_run_recovery_manifest_payload(
                workflow_id=created.workflow_id,
                run_id=created.run_id,
            ),
        )

        resumed = await service.describe_execution(result["execution"]["workflowId"])
        assert result["applied"] == "created_resumed_execution"
        assert (
            resumed.parameters["workflow"]["resume"]["recoveryCheckpointRef"]
            == "artifact://checkpoint/source"
        )


@pytest.mark.asyncio
async def test_selected_step_recovery_starts_from_preserved_step(
    tmp_path, mock_client_adapter
):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)
        service._client_adapter = mock_client_adapter

        created = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=uuid4(),
            title="recover source",
            input_artifact_ref="artifact://input/source",
            plan_artifact_ref="artifact://plan/source",
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters={
                "agentRunId": "old-agent-run",
                "workflow": {"title": "recovery source", "instructions": "Original"},
            },
            idempotency_key=None,
        )
        created.state = MoonMindWorkflowState.FAILED
        created.close_status = TemporalExecutionCloseStatus.FAILED
        created.memo = {
            **created.memo,
            "task_input_snapshot_ref": "artifact://snapshot/source",
            "recovery_checkpoint_ref": "artifact://checkpoint/source",
        }
        await session.commit()

        checkpoint_payload = _valid_recovery_checkpoint_payload(
            workflow_id=created.workflow_id,
            run_id=created.run_id,
            snapshot_ref="artifact://snapshot/source",
        )
        checkpoint_payload["preservedSteps"] = [
            checkpoint_payload["preservedSteps"][0],
            {
                "logicalStepId": "design",
                "order": 2,
                "status": "succeeded",
                "sourceExecutionOrdinal": 1,
                "artifacts": {"outputSummary": "artifact://summary/design"},
                "stateCheckpointRef": "artifact://workspace/before-design",
            },
        ]
        checkpoint_payload["failedStep"] = {
            "logicalStepId": "implement",
            "order": 3,
            "executionOrdinal": 1,
            "title": "Implement",
        }

        result = await service.create_failed_step_recovery_execution(
            created,
            recovery_checkpoint_ref=None,
            idempotency_key="recover-selected",
            checkpoint_payload=checkpoint_payload,
            failed_run_recovery_manifest_ref="artifact://recovery/manifest",
            failed_run_recovery_manifest=_valid_failed_run_recovery_manifest_payload(
                workflow_id=created.workflow_id,
                run_id=created.run_id,
            ),
            selected_start_step_id="design",
        )

        resumed = await service.describe_execution(result["execution"]["workflowId"])
        assert result["relationship"] == "Recovered from selected step"
        assert resumed.parameters["recoverySource"]["recoveryMode"] == "selected_step"
        assert resumed.parameters["recoverySource"]["failedStepId"] == "design"
        assert resumed.parameters["recoverySource"]["selectedStartStepId"] == "design"
        assert [
            step["logicalStepId"]
            for step in resumed.parameters["recoverySource"]["preservedSteps"]
        ] == ["plan"]
        assert resumed.parameters["workflow"]["resume"]["failedStepId"] == "design"
        assert resumed.parameters["workflow"]["resume"]["recoveryMode"] == "selected_step"
        assert resumed.parameters["workflow"]["resume"]["selectedStartStepId"] == "design"


@pytest.mark.asyncio
async def test_selected_step_recovery_rejects_step_without_checkpoint_evidence(
    tmp_path, mock_client_adapter
):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)
        service._client_adapter = mock_client_adapter

        created = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=uuid4(),
            title="recover source",
            input_artifact_ref="artifact://input/source",
            plan_artifact_ref="artifact://plan/source",
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters=_valid_user_workflow_parameters(),
            idempotency_key=None,
        )
        created.state = MoonMindWorkflowState.FAILED
        created.close_status = TemporalExecutionCloseStatus.FAILED
        created.memo = {
            **created.memo,
            "task_input_snapshot_ref": "artifact://snapshot/source",
            "recovery_checkpoint_ref": "artifact://checkpoint/source",
        }
        await session.commit()

        with pytest.raises(
            TemporalExecutionRecoveryCheckpointError,
            match="Selected start step",
        ):
            await service.create_failed_step_recovery_execution(
                created,
                recovery_checkpoint_ref=None,
                idempotency_key="recover-selected",
                checkpoint_payload=_valid_recovery_checkpoint_payload(
                    workflow_id=created.workflow_id,
                    run_id=created.run_id,
                    snapshot_ref="artifact://snapshot/source",
                ),
                failed_run_recovery_manifest_ref="artifact://recovery/manifest",
                failed_run_recovery_manifest=_valid_failed_run_recovery_manifest_payload(
                    workflow_id=created.workflow_id,
                    run_id=created.run_id,
                ),
                selected_start_step_id="missing-step",
            )


@pytest.mark.asyncio
async def test_selected_step_recovery_rejects_step_after_failed_step(
    tmp_path, mock_client_adapter
):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)
        service._client_adapter = mock_client_adapter

        created = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=uuid4(),
            title="recover source",
            input_artifact_ref="artifact://input/source",
            plan_artifact_ref="artifact://plan/source",
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters=_valid_user_workflow_parameters(),
            idempotency_key=None,
        )
        created.state = MoonMindWorkflowState.FAILED
        created.close_status = TemporalExecutionCloseStatus.FAILED
        created.memo = {
            **created.memo,
            "task_input_snapshot_ref": "artifact://snapshot/source",
            "recovery_checkpoint_ref": "artifact://checkpoint/source",
        }
        await session.commit()

        checkpoint_payload = _valid_recovery_checkpoint_payload(
            workflow_id=created.workflow_id,
            run_id=created.run_id,
            snapshot_ref="artifact://snapshot/source",
        )
        checkpoint_payload["preservedSteps"].append(
            {
                "logicalStepId": "notify",
                "order": 4,
                "status": "succeeded",
                "sourceExecutionOrdinal": 1,
                "artifacts": {"outputSummary": "artifact://summary/notify"},
                "stateCheckpointRef": "artifact://workspace/before-notify",
            }
        )

        with pytest.raises(
            TemporalExecutionRecoveryCheckpointError,
            match="must precede the failed step",
        ):
            await service.create_failed_step_recovery_execution(
                created,
                recovery_checkpoint_ref=None,
                idempotency_key="recover-selected",
                checkpoint_payload=checkpoint_payload,
                failed_run_recovery_manifest_ref="artifact://recovery/manifest",
                failed_run_recovery_manifest=_valid_failed_run_recovery_manifest_payload(
                    workflow_id=created.workflow_id,
                    run_id=created.run_id,
                ),
                selected_start_step_id="notify",
            )


@pytest.mark.asyncio
async def test_failed_step_recovery_requires_hydrated_checkpoint_payload(
    tmp_path, mock_client_adapter
):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)
        service._client_adapter = mock_client_adapter

        created = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=uuid4(),
            title="recover source",
            input_artifact_ref="artifact://input/source",
            plan_artifact_ref="artifact://plan/source",
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters=_valid_user_workflow_parameters(),
            idempotency_key=None,
        )
        created.state = MoonMindWorkflowState.FAILED
        created.close_status = TemporalExecutionCloseStatus.FAILED
        created.memo = {
            **created.memo,
            "task_input_snapshot_ref": "artifact://snapshot/source",
            "recovery_checkpoint_ref": "artifact://checkpoint/source",
        }
        await session.commit()

        with pytest.raises(TemporalExecutionRecoveryCheckpointError, match="payload"):
            await service.create_failed_step_recovery_execution(
                created,
                recovery_checkpoint_ref=None,
                idempotency_key="recover-1",
                checkpoint_payload=None,
            )


@pytest.mark.asyncio
async def test_failed_step_recovery_invalid_evidence_does_not_create_execution(
    tmp_path, mock_client_adapter, monkeypatch
):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)
        service._client_adapter = mock_client_adapter

        created = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=uuid4(),
            title="recover source",
            input_artifact_ref="artifact://input/source",
            plan_artifact_ref="artifact://plan/source",
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters=_valid_user_workflow_parameters(),
            idempotency_key=None,
        )
        created.state = MoonMindWorkflowState.FAILED
        created.close_status = TemporalExecutionCloseStatus.FAILED
        created.memo = {
            **created.memo,
            "task_input_snapshot_ref": "artifact://snapshot/source",
            "recovery_checkpoint_ref": "artifact://checkpoint/source",
        }
        await session.commit()

        invalid_payload = _valid_recovery_checkpoint_payload(
            workflow_id=created.workflow_id,
            run_id=created.run_id,
            snapshot_ref="artifact://snapshot/source",
        )
        invalid_payload.pop("planDigest")
        create_execution = AsyncMock(
            side_effect=AssertionError("create_execution must not be called")
        )
        monkeypatch.setattr(service, "create_execution", create_execution)

        with pytest.raises(TemporalExecutionRecoveryCheckpointError, match="invalid"):
            await service.create_failed_step_recovery_execution(
                created,
                recovery_checkpoint_ref=None,
                idempotency_key="recover-1",
                checkpoint_payload=invalid_payload,
                failed_run_recovery_manifest_ref="artifact://recovery/manifest",
                failed_run_recovery_manifest=_valid_failed_run_recovery_manifest_payload(
                    workflow_id=created.workflow_id,
                    run_id=created.run_id,
                ),
            )

        create_execution.assert_not_awaited()


@pytest.mark.asyncio
async def test_failed_step_recovery_rejects_noncanonical_checkpoint_ref(
    tmp_path, mock_client_adapter
):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)
        service._client_adapter = mock_client_adapter

        created = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=uuid4(),
            title="recover source",
            input_artifact_ref="artifact://input/source",
            plan_artifact_ref="artifact://plan/source",
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters=_valid_user_workflow_parameters(),
            idempotency_key=None,
        )
        created.state = MoonMindWorkflowState.FAILED
        created.close_status = TemporalExecutionCloseStatus.FAILED
        created.memo = {
            **created.memo,
            "task_input_snapshot_ref": "artifact://snapshot/source",
            "recovery_checkpoint_ref": "artifact://checkpoint/source",
        }
        await session.commit()

        with pytest.raises(
            TemporalExecutionRecoveryCheckpointError,
            match="does not match",
        ):
            await service.create_failed_step_recovery_execution(
                created,
                recovery_checkpoint_ref="artifact://checkpoint/other",
                idempotency_key="recover-1",
                checkpoint_payload=_valid_recovery_checkpoint_payload(
                    workflow_id=created.workflow_id,
                    run_id=created.run_id,
                    snapshot_ref="artifact://snapshot/source",
                ),
                failed_run_recovery_manifest_ref="artifact://recovery/manifest",
                failed_run_recovery_manifest=_valid_failed_run_recovery_manifest_payload(
                    workflow_id=created.workflow_id,
                    run_id=created.run_id,
                ),
            )


@pytest.mark.asyncio
async def test_failed_step_recovery_rejects_checkpoint_run_mismatch(
    tmp_path, mock_client_adapter
):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)
        service._client_adapter = mock_client_adapter
        created = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=uuid4(),
            title="recover source",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters=_valid_user_workflow_parameters(),
            idempotency_key=None,
        )
        created.state = MoonMindWorkflowState.FAILED
        created.close_status = TemporalExecutionCloseStatus.FAILED
        created.memo = {
            **created.memo,
            "task_input_snapshot_ref": "artifact://snapshot/source",
            "recovery_checkpoint_ref": "artifact://checkpoint/source",
        }
        await session.commit()

        with pytest.raises(TemporalExecutionValidationError, match="runId"):
            await service.create_failed_step_recovery_execution(
                created,
                recovery_checkpoint_ref=None,
                idempotency_key="recover-1",
                checkpoint_payload=_valid_recovery_checkpoint_payload(
                    workflow_id=created.workflow_id,
                    run_id="stale-run-id",
                    snapshot_ref="artifact://snapshot/source",
                ),
                failed_run_recovery_manifest_ref="artifact://recovery/manifest",
                failed_run_recovery_manifest=_valid_failed_run_recovery_manifest_payload(
                    workflow_id=created.workflow_id,
                    run_id=created.run_id,
                ),
            )


@pytest.mark.asyncio
async def test_failed_step_recovery_rejects_checkpoint_plan_mismatch(
    tmp_path, mock_client_adapter
):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)
        service._client_adapter = mock_client_adapter
        created = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=uuid4(),
            title="recover source",
            input_artifact_ref=None,
            plan_artifact_ref="artifact://plan/source",
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters=_valid_user_workflow_parameters(),
            idempotency_key=None,
        )
        created.state = MoonMindWorkflowState.FAILED
        created.close_status = TemporalExecutionCloseStatus.FAILED
        created.memo = {
            **created.memo,
            "task_input_snapshot_ref": "artifact://snapshot/source",
            "recovery_checkpoint_ref": "artifact://checkpoint/source",
        }
        await session.commit()

        payload = _valid_recovery_checkpoint_payload(
            workflow_id=created.workflow_id,
            run_id=created.run_id,
            snapshot_ref="artifact://snapshot/source",
        )
        payload["planRef"] = "artifact://plan/stale"

        with pytest.raises(TemporalExecutionRecoveryCheckpointError, match="plan"):
            await service.create_failed_step_recovery_execution(
                created,
                recovery_checkpoint_ref=None,
                idempotency_key="recover-1",
                checkpoint_payload=payload,
                failed_run_recovery_manifest_ref="artifact://recovery/manifest",
                failed_run_recovery_manifest=_valid_failed_run_recovery_manifest_payload(
                    workflow_id=created.workflow_id,
                    run_id=created.run_id,
                ),
            )


@pytest.mark.asyncio
async def test_failed_step_recovery_rejects_checkpoint_plan_digest_mismatch(
    tmp_path, mock_client_adapter
):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)
        service._client_adapter = mock_client_adapter
        created = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=uuid4(),
            title="recover source",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters=_valid_user_workflow_parameters(),
            idempotency_key=None,
        )
        created.state = MoonMindWorkflowState.FAILED
        created.close_status = TemporalExecutionCloseStatus.FAILED
        created.memo = {
            **created.memo,
            "task_input_snapshot_ref": "artifact://snapshot/source",
            "recovery_checkpoint_ref": "artifact://checkpoint/source",
            "resume_plan_digest": "sha256:source-plan",
        }
        await session.commit()

        with pytest.raises(
            TemporalExecutionRecoveryCheckpointError,
            match="plan identity does not match",
        ):
            await service.create_failed_step_recovery_execution(
                created,
                recovery_checkpoint_ref=None,
                idempotency_key="recover-1",
                checkpoint_payload=_valid_recovery_checkpoint_payload(
                    workflow_id=created.workflow_id,
                    run_id=created.run_id,
                    snapshot_ref="artifact://snapshot/source",
                ),
                failed_run_recovery_manifest_ref="artifact://recovery/manifest",
                failed_run_recovery_manifest=_valid_failed_run_recovery_manifest_payload(
                    workflow_id=created.workflow_id,
                    run_id=created.run_id,
                ),
            )

@pytest.mark.asyncio
async def test_request_rerun_bounds_fresh_execution_idempotency_key(
    tmp_path, mock_client_adapter
):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)
        service._client_adapter = mock_client_adapter

        created = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=uuid4(),
            title=None,
            input_artifact_ref=None,
            plan_artifact_ref="artifact://plan/1",
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters=_valid_user_workflow_parameters(),
            idempotency_key=None,
        )
        await service.cancel_execution(
            workflow_id=created.workflow_id,
            reason="done",
            graceful=True,
        )

        long_idempotency_key = "k" * 128
        first_response = await service.update_execution(
            workflow_id=created.workflow_id,
            update_name="RequestRerun",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            parameters_patch=None,
            title=None,
            new_manifest_artifact_ref=None,
            mode=None,
            max_concurrency=None,
            node_ids=None,
            idempotency_key=long_idempotency_key,
        )
        second_response = await service.update_execution(
            workflow_id=created.workflow_id,
            update_name="RequestRerun",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            parameters_patch=None,
            title=None,
            new_manifest_artifact_ref=None,
            mode=None,
            max_concurrency=None,
            node_ids=None,
            idempotency_key=long_idempotency_key,
        )

        rerun = await service.describe_execution(first_response["workflow_id"])
        assert first_response["workflow_id"] == second_response["workflow_id"]
        assert rerun.create_idempotency_key is not None
        assert len(rerun.create_idempotency_key) <= 128
        assert rerun.create_idempotency_key.startswith("rerun:")

@pytest.mark.asyncio
async def test_request_rerun_creates_fresh_execution_when_temporal_reports_completed(
    tmp_path, mock_client_adapter
):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)
        service._client_adapter = mock_client_adapter

        created = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=uuid4(),
            title=None,
            input_artifact_ref=None,
            plan_artifact_ref="artifact://plan/1",
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters=_valid_user_workflow_parameters(),
            idempotency_key=None,
        )

        service._client_adapter.update_workflow.side_effect = RuntimeError(
            "workflow execution already completed"
        )

        source_workflow_id = created.workflow_id
        response = await service.update_execution(
            workflow_id=created.workflow_id,
            update_name="RequestRerun",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            parameters_patch=None,
            title=None,
            new_manifest_artifact_ref=None,
            mode=None,
            max_concurrency=None,
            node_ids=None,
            idempotency_key="rerun-temporal-completed",
        )
        source = await service.describe_execution(source_workflow_id)
        rerun = await service.describe_execution(response["workflow_id"])

        assert response["accepted"] is True
        assert response["applied"] == "continue_as_new"
        assert response["continue_as_new_cause"] == "manual_rerun"
        assert response["workflow_id"] != source_workflow_id
        assert source.state is MoonMindWorkflowState.INITIALIZING
        assert rerun.state is MoonMindWorkflowState.INITIALIZING
        assert rerun.parameters["rerunSource"]["workflowId"] == source_workflow_id
        assert service._client_adapter.update_workflow.await_count == 1


@pytest.mark.asyncio
async def test_fresh_rerun_expands_unexpanded_jira_orchestrate_template(
    tmp_path, mock_client_adapter
):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)
        service._client_adapter = mock_client_adapter

        source = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=uuid4(),
            title="Run Jira Orchestrate for MM-820",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters={
                "requestType": "workflow",
                "repository": "MoonLadderStudios/MoonMind",
                "targetRuntime": "codex_cli",
                "publishMode": "pr",
                "workflow": {
                    "title": "Run Jira Orchestrate for MM-820",
                    "instructions": "Use the existing Jira Orchestrate workflow.",
                    "tool": {
                        "type": "skill",
                        "name": "jira-orchestrate",
                    },
                    "skill": {"name": "jira-orchestrate"},
                    "inputs": {
                        "jira_issue_key": "MM-820",
                        "source_design_path": (
                            "docs/Steps/StepExecutionsAndCheckpointing.md"
                        ),
                        "constraints": "Do not run implementation inline.",
                    },
                    "runtime": {"mode": "codex_cli"},
                    "publish": {"mode": "pr"},
                    "taskTemplate": {
                        "slug": "jira-orchestrate",
                        "version": "1.0.0",
                    },
                },
            },
            idempotency_key=None,
        )
        await service.record_terminal_state(
            workflow_id=source.workflow_id,
            state="failed",
            close_status="failed",
            summary="failed before template expansion",
        )

        response = await service.update_execution(
            workflow_id=source.workflow_id,
            update_name="RequestRerun",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            parameters_patch=None,
            title=None,
            new_manifest_artifact_ref=None,
            mode=None,
            max_concurrency=None,
            node_ids=None,
            idempotency_key="rerun-jira-orchestrate-template",
        )
        rerun = await service.describe_execution(response["workflow_id"])

    workflow_payload = rerun.parameters["workflow"]
    assert rerun.parameters["stepCount"] == 26
    assert len(workflow_payload["steps"]) == 26
    assert workflow_payload["steps"][0]["tool"]["id"] == "jira.check_blockers"
    assert workflow_payload["steps"][3]["tool"]["id"] == "jira.update_issue_status"
    assert workflow_payload["steps"][7]["skill"]["id"] == "moonspec-tasks"
    assert workflow_payload["steps"][9]["skill"]["id"] == "moonspec-implement"
    assert workflow_payload["steps"][25]["skill"]["id"] == "jira-issue-updater"
    assert workflow_payload["appliedStepTemplates"][0]["slug"] == "jira-orchestrate"
    assert workflow_payload["recovery"] == {
        "kind": "exact_full_rerun",
        "sourceWorkflowId": source.workflow_id,
        "sourceRunId": source.run_id,
    }


@pytest.mark.asyncio
async def test_manifest_only_updates_rejected_for_non_manifest_workflow(
    tmp_path, mock_client_adapter
):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)
        service._client_adapter = mock_client_adapter

        created = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=uuid4(),
            title=None,
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters=_valid_user_workflow_parameters(),
            idempotency_key=None,
        )

        with pytest.raises(TemporalExecutionValidationError) as exc_info:
            await service.update_execution(
                workflow_id=created.workflow_id,
                update_name="UpdateManifest",
                input_artifact_ref=None,
                plan_artifact_ref=None,
                parameters_patch=None,
                title=None,
                new_manifest_artifact_ref=None,
                mode=None,
                max_concurrency=None,
                node_ids=None,
                idempotency_key=None,
            )

        assert "only supported for MoonMind.ManifestIngest" in str(exc_info.value)

@pytest.mark.asyncio
async def test_request_rerun_clears_pause_flags_when_continuing_as_new(
    tmp_path, mock_client_adapter
):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)
        service._client_adapter = mock_client_adapter

        created = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=uuid4(),
            title=None,
            input_artifact_ref=None,
            plan_artifact_ref="artifact://plan/1",
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters=_valid_user_workflow_parameters(),
            idempotency_key=None,
        )

        await service.signal_execution(
            workflow_id=created.workflow_id,
            signal_name="Pause",
            payload={},
            payload_artifact_ref=None,
        )

        rerun = await service.update_execution(
            workflow_id=created.workflow_id,
            update_name="RequestRerun",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            parameters_patch=None,
            title=None,
            new_manifest_artifact_ref=None,
            mode=None,
            max_concurrency=None,
            node_ids=None,
            idempotency_key="rerun-clears-pause",
        )
        refreshed = await service.describe_execution(created.workflow_id)

        assert rerun["applied"] == "continue_as_new"
        assert refreshed.state is MoonMindWorkflowState.EXECUTING
        assert refreshed.paused is False
        assert refreshed.awaiting_external is False

@pytest.mark.asyncio
async def test_update_execution_rejects_unknown_update_name(tmp_path):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)

        created = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=uuid4(),
            title=None,
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters=_valid_user_workflow_parameters(),
            idempotency_key=None,
        )

        with pytest.raises(
            TemporalExecutionValidationError,
            match="Unsupported update name",
        ):
            await service.update_execution(
                workflow_id=created.workflow_id,
                update_name="UnknownUpdate",
                input_artifact_ref=None,
                plan_artifact_ref=None,
                parameters_patch=None,
                title=None,
                idempotency_key=None,
            )

@pytest.mark.asyncio
async def test_update_execution_rejects_run_intervention_updates(
    tmp_path, mock_client_adapter
):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)
        service._client_adapter = mock_client_adapter

        created = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=uuid4(),
            title=None,
            input_artifact_ref=None,
            plan_artifact_ref="artifact://plan/1",
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters=_valid_user_workflow_parameters(),
            idempotency_key=None,
        )

        with pytest.raises(
            TemporalExecutionValidationError,
            match="use /api/executions/\\{id\\}/signal instead",
        ):
            await service.update_execution(
                workflow_id=created.workflow_id,
                update_name="Pause",
            )

        with pytest.raises(
            TemporalExecutionValidationError,
            match="use /api/executions/\\{id\\}/cancel instead",
        ):
            await service.update_execution(
                workflow_id=created.workflow_id,
                update_name="Cancel",
            )

@pytest.mark.asyncio
async def test_signal_pause_recovery_and_external_event_transitions(
    tmp_path, mock_client_adapter
):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)
        service._client_adapter = mock_client_adapter

        created = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=uuid4(),
            title=None,
            input_artifact_ref=None,
            plan_artifact_ref="artifact://plan/1",
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters=_valid_user_workflow_parameters(),
            idempotency_key=None,
        )

        await service.signal_execution(
            workflow_id=created.workflow_id,
            signal_name="Pause",
            payload={},
            payload_artifact_ref=None,
        )
        paused = await service.describe_execution(created.workflow_id)
        assert paused.state is MoonMindWorkflowState.INITIALIZING
        assert paused.paused is True
        assert paused.memo["waiting_reason"] == "operator_paused"
        assert paused.memo["attention_required"] is True
        assert paused.memo["intervention_audit"][-1]["transport"] == "temporal_update"

        await service.signal_execution(
            workflow_id=created.workflow_id,
            signal_name="Resume",
            payload={},
            payload_artifact_ref=None,
        )
        resumed = await service.describe_execution(created.workflow_id)
        assert resumed.state is MoonMindWorkflowState.INITIALIZING
        assert resumed.paused is False
        assert "waiting_reason" not in resumed.memo
        assert resumed.memo["intervention_audit"][-1]["transport"] == "temporal_update"

        await service.signal_execution(
            workflow_id=created.workflow_id,
            signal_name="ExternalEvent",
            payload={"source": "jules", "event_type": "completed"},
            payload_artifact_ref="artifact://events/1",
        )
        signaled = await service.describe_execution(created.workflow_id)
        assert "artifact://events/1" in (signaled.artifact_refs or [])
        assert signaled.state is MoonMindWorkflowState.EXECUTING


@pytest.mark.asyncio
async def test_signal_pause_allows_awaiting_slot(tmp_path, mock_client_adapter):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)
        service._client_adapter = mock_client_adapter

        created = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=uuid4(),
            title=None,
            input_artifact_ref=None,
            plan_artifact_ref="artifact://plan/1",
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters=_valid_user_workflow_parameters(),
            idempotency_key=None,
        )
        source = await service._require_source_execution(created.workflow_id)
        service._set_state(source, MoonMindWorkflowState.AWAITING_SLOT)
        await session.commit()

        await service.signal_execution(
            workflow_id=created.workflow_id,
            signal_name="Pause",
            payload={},
            payload_artifact_ref=None,
        )

        mock_client_adapter.update_workflow.assert_awaited_once_with(
            created.workflow_id,
            "Pause",
        )
        paused = await service.describe_execution(created.workflow_id)
        assert paused.state is MoonMindWorkflowState.AWAITING_SLOT
        assert paused.paused is True
        assert paused.memo["waiting_reason"] == "operator_paused"


@pytest.mark.asyncio
async def test_signal_pause_allows_waiting_on_dependencies(
    tmp_path, mock_client_adapter
):
    """MM-978: AWAITING dependency waits accept lifecycle Pause."""
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)
        service._client_adapter = mock_client_adapter

        created = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=uuid4(),
            title=None,
            input_artifact_ref=None,
            plan_artifact_ref="artifact://plan/1",
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters=_valid_user_workflow_parameters(),
            idempotency_key=None,
        )
        source = await service._require_source_execution(created.workflow_id)
        service._set_state(source, MoonMindWorkflowState.WAITING_ON_DEPENDENCIES)
        await session.commit()

        await service.signal_execution(
            workflow_id=created.workflow_id,
            signal_name="Pause",
            payload={},
            payload_artifact_ref=None,
        )

        mock_client_adapter.update_workflow.assert_awaited_once_with(
            created.workflow_id,
            "Pause",
        )
        paused = await service.describe_execution(created.workflow_id)
        assert paused.state is MoonMindWorkflowState.WAITING_ON_DEPENDENCIES
        assert paused.paused is True
        assert paused.memo["waiting_reason"] == "operator_paused"


@pytest.mark.asyncio
async def test_check_system_paused_reads_persisted_worker_pause_state(tmp_path):
    """MM-978: new execution creation uses persisted system pause state."""
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)

        assert await service.check_system_paused() is False

        subject_id = UUID("00000000-0000-0000-0000-000000000000")
        session.add(
            SettingsOverride(
                scope="workspace",
                workspace_id=subject_id,
                user_id=subject_id,
                key="operations.workers.pause_state",
                value_json={
                    "workersPaused": True,
                    "mode": "drain",
                    "reason": "MM-978 maintenance",
                    "version": 1,
                },
                schema_version=1,
                value_version=1,
            )
        )
        await session.commit()

        assert await service.check_system_paused() is True


@pytest.mark.asyncio
async def test_signal_resume_forwards_payload_via_workflow_update(
    tmp_path, mock_client_adapter
):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)
        service._client_adapter = mock_client_adapter

        created = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=uuid4(),
            title=None,
            input_artifact_ref=None,
            plan_artifact_ref="artifact://plan/1",
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters=_valid_user_workflow_parameters(),
            idempotency_key=None,
        )

        await service.signal_execution(
            workflow_id=created.workflow_id,
            signal_name="Resume",
            payload={"message": "Use the Provider Profiles label."},
            payload_artifact_ref=None,
        )

        service._client_adapter.update_workflow.assert_awaited_once_with(
            created.workflow_id,
            "Resume",
            {"message": "Use the Provider Profiles label."},
        )
        resumed = await service.describe_execution(created.workflow_id)
        assert resumed.state is MoonMindWorkflowState.INITIALIZING
        assert resumed.paused is False
        assert resumed.memo.get("waiting_reason") is None
        assert resumed.memo["intervention_audit"][-1]["transport"] == "temporal_update"
        assert (
            resumed.memo["intervention_audit"][-1]["detail"]
            == "Use the Provider Profiles label."
        )

@pytest.mark.asyncio
async def test_signal_send_message_records_intervention_audit_without_state_change(
    tmp_path, mock_client_adapter
):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)
        service._client_adapter = mock_client_adapter

        created = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=uuid4(),
            title=None,
            input_artifact_ref=None,
            plan_artifact_ref="artifact://plan/1",
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters=_valid_user_workflow_parameters(),
            idempotency_key=None,
        )

        await service.signal_execution(
            workflow_id=created.workflow_id,
            signal_name="SendMessage",
            payload={"message": "Please use Provider Profiles."},
            payload_artifact_ref=None,
        )

        service._client_adapter.update_workflow.assert_awaited_once_with(
            created.workflow_id,
            "SendMessage",
            {"message": "Please use Provider Profiles."},
        )
        refreshed = await service.describe_execution(created.workflow_id)
        assert refreshed.state is created.state
        assert refreshed.memo["intervention_audit"][-1]["action"] == "send_message"
        assert (
            refreshed.memo["intervention_audit"][-1]["transport"] == "temporal_update"
        )
        assert (
            refreshed.memo["intervention_audit"][-1]["detail"]
            == "Please use Provider Profiles."
        )


@pytest.mark.asyncio
async def test_signal_send_message_blocks_secret_before_temporal_update(
    tmp_path, mock_client_adapter, monkeypatch
):
    monkeypatch.setattr(settings.security, "high_security_mode", True)
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)
        service._client_adapter = mock_client_adapter

        created = await service.create_execution(
            workflow_type=RENAMED_USER_WORKFLOW_TYPE,
            owner_id=uuid4(),
            title=None,
            input_artifact_ref=None,
            plan_artifact_ref="artifact://plan/1",
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters={},
            idempotency_key=None,
        )

        with pytest.raises(TemporalExecutionValidationError) as exc_info:
            await service.signal_execution(
                workflow_id=created.workflow_id,
                signal_name="SendMessage",
                payload={"message": "Please use token=blocked-secret-value"},
                payload_artifact_ref=None,
            )

        mock_client_adapter.update_workflow.assert_not_awaited()
        message = str(exc_info.value)
        assert "execution.send_message.message" in message
        assert "blocked-secret-value" not in message


@pytest.mark.asyncio
async def test_signal_send_message_forwards_clean_message_unchanged_with_scan(
    tmp_path, mock_client_adapter, monkeypatch
):
    monkeypatch.setattr(settings.security, "high_security_mode", True)
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)
        service._client_adapter = mock_client_adapter

        created = await service.create_execution(
            workflow_type=RENAMED_USER_WORKFLOW_TYPE,
            owner_id=uuid4(),
            title=None,
            input_artifact_ref=None,
            plan_artifact_ref="artifact://plan/1",
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters={},
            idempotency_key=None,
        )

        message = "Please keep this operator message unchanged."
        await service.signal_execution(
            workflow_id=created.workflow_id,
            signal_name="SendMessage",
            payload={"message": message},
            payload_artifact_ref=None,
        )

        mock_client_adapter.update_workflow.assert_awaited_once_with(
            created.workflow_id,
            "SendMessage",
            {"message": message},
        )
        refreshed = await service.describe_execution(created.workflow_id)
        assert refreshed.memo["intervention_audit"][-1]["detail"] == message


@pytest.mark.asyncio
async def test_signal_send_message_blocks_secret_before_temporal_update(
    tmp_path,
    mock_client_adapter,
    monkeypatch,
):
    monkeypatch.setattr(settings.security, "high_security_mode", True)
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)
        service._client_adapter = mock_client_adapter

        created = await service.create_execution(
            workflow_type=TemporalWorkflowType.USER_WORKFLOW,
            owner_id=uuid4(),
            title=None,
            input_artifact_ref=None,
            plan_artifact_ref="artifact://plan/1",
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters={},
            idempotency_key=None,
        )

        with pytest.raises(
            TemporalExecutionValidationError,
            match="execution\\.send_message\\.message",
        ):
            await service.signal_execution(
                workflow_id=created.workflow_id,
                signal_name="SendMessage",
                payload={"message": "Please use password=super-secret-value"},
                payload_artifact_ref=None,
            )

        service._client_adapter.update_workflow.assert_not_awaited()

@pytest.mark.asyncio
async def test_signal_skip_dependency_wait_routes_update_and_records_audit(
    tmp_path, mock_client_adapter
):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)
        service._client_adapter = mock_client_adapter

        created = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=uuid4(),
            title=None,
            input_artifact_ref=None,
            plan_artifact_ref="artifact://plan/1",
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters=_valid_user_workflow_parameters(),
            idempotency_key=None,
        )
        created.state = MoonMindWorkflowState.WAITING_ON_DEPENDENCIES
        await session.commit()

        await service.signal_execution(
            workflow_id=created.workflow_id,
            signal_name="SkipDependencyWait",
            payload={},
            payload_artifact_ref=None,
        )

        service._client_adapter.update_workflow.assert_awaited_once_with(
            created.workflow_id,
            "SkipDependencyWait",
        )
        refreshed = await service.describe_execution(created.workflow_id)
        assert refreshed.state is MoonMindWorkflowState.AWAITING_SLOT
        assert refreshed.paused is False
        assert (
            refreshed.memo["intervention_audit"][-1]["action"]
            == "skip_dependency_wait"
        )
        assert (
            refreshed.memo["intervention_audit"][-1]["summary"]
            == "Dependency wait skipped by operator."
        )
        assert refreshed.memo.get("waiting_reason") is None

@pytest.mark.asyncio
async def test_signal_send_message_rejects_noncanonical_payload(
    tmp_path, mock_client_adapter
):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)
        service._client_adapter = mock_client_adapter

        created = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=uuid4(),
            title=None,
            input_artifact_ref=None,
            plan_artifact_ref="artifact://plan/1",
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters=_valid_user_workflow_parameters(),
            idempotency_key=None,
        )

        with pytest.raises(
            TemporalExecutionValidationError,
            match="message is required when signal_name is SendMessage",
        ):
            await service.signal_execution(
                workflow_id=created.workflow_id,
                signal_name="SendMessage",
                payload={"clarificationResponse": "Please use Provider Profiles."},
                payload_artifact_ref=None,
            )

@pytest.mark.asyncio
async def test_signal_bypass_dependencies_records_operator_audit(
    tmp_path, mock_client_adapter
):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)
        service._client_adapter = mock_client_adapter

        created = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=uuid4(),
            title=None,
            input_artifact_ref=None,
            plan_artifact_ref="artifact://plan/1",
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters={"workflow": {"dependsOn": []}},
            idempotency_key=None,
        )
        source = await service._require_source_execution(created.workflow_id)
        service._set_state(source, MoonMindWorkflowState.WAITING_ON_DEPENDENCIES)
        await session.commit()

        await service.signal_execution(
            workflow_id=created.workflow_id,
            signal_name="BypassDependencies",
            payload={"reason": "No longer needs the upstream task."},
            payload_artifact_ref=None,
        )

        mock_client_adapter.signal_workflow.assert_awaited_once_with(
            created.workflow_id,
            "BypassDependencies",
            {
                "payload": {"reason": "No longer needs the upstream task."},
                "payload_artifact_ref": None,
            },
        )
        refreshed = await service.describe_execution(created.workflow_id)
        assert refreshed.state is MoonMindWorkflowState.WAITING_ON_DEPENDENCIES
        assert refreshed.memo["summary"] == "Dependency wait bypass requested."
        assert refreshed.memo["intervention_audit"][-1]["action"] == "bypass_dependencies"
        assert (
            refreshed.memo["intervention_audit"][-1]["detail"]
            == "No longer needs the upstream task."
        )

@pytest.mark.asyncio
async def test_signal_bypass_dependencies_outside_wait_does_not_mutate_projection(
    tmp_path, mock_client_adapter
):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)
        service._client_adapter = mock_client_adapter

        created = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=uuid4(),
            title=None,
            input_artifact_ref=None,
            plan_artifact_ref="artifact://plan/1",
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters={"workflow": {"dependsOn": []}},
            idempotency_key=None,
        )
        source = await service._require_source_execution(created.workflow_id)
        service._set_state(source, MoonMindWorkflowState.EXECUTING)
        service._update_summary(source, "Execution in progress.")
        await session.commit()

        await service.signal_execution(
            workflow_id=created.workflow_id,
            signal_name="BypassDependencies",
            payload={"reason": "Operator clicked bypass from stale UI."},
            payload_artifact_ref=None,
        )

        refreshed = await service.describe_execution(created.workflow_id)
        assert refreshed.state is MoonMindWorkflowState.EXECUTING
        assert refreshed.memo["summary"] == "Execution in progress."
        assert refreshed.memo["intervention_audit"][-1]["action"] == "bypass_dependencies"
        assert (
            refreshed.memo["intervention_audit"][-1]["summary"]
            == "Dependency wait bypass ignored outside dependency wait."
        )

@pytest.mark.asyncio
async def test_signal_execution_rejects_unknown_signal_name(tmp_path):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)

        created = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=uuid4(),
            title=None,
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters=_valid_user_workflow_parameters(),
            idempotency_key=None,
        )

        with pytest.raises(
            TemporalExecutionValidationError,
            match="Unsupported signal name",
        ):
            await service.signal_execution(
                workflow_id=created.workflow_id,
                signal_name="UnknownSignal",
                payload=None,
                payload_artifact_ref=None,
            )

@pytest.mark.asyncio
async def test_cancel_marks_terminal_state_and_close_status(
    tmp_path, mock_client_adapter
):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)
        service._client_adapter = mock_client_adapter

        created = await service.create_execution(
            workflow_type="MoonMind.ManifestIngest",
            owner_id=uuid4(),
            title=None,
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref="artifact://manifest/1",
            failure_policy="fail_fast",
            initial_parameters={},
            idempotency_key=None,
        )

        canceled = await service.cancel_execution(
            workflow_id=created.workflow_id,
            reason="stop",
            graceful=True,
        )

        assert canceled.state is MoonMindWorkflowState.CANCELED
        assert canceled.close_status is TemporalExecutionCloseStatus.CANCELED

@pytest.mark.asyncio
async def test_cancel_execution_terminal_record_skips_temporal_call(
    tmp_path, mock_client_adapter
):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)
        service._client_adapter = mock_client_adapter

        created = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=uuid4(),
            title=None,
            input_artifact_ref=None,
            plan_artifact_ref="artifact://plan/1",
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters=_valid_user_workflow_parameters(),
            idempotency_key=None,
        )
        completed = await service.mark_execution_succeeded(
            workflow_id=created.workflow_id,
            summary="already done",
        )
        assert completed.state is MoonMindWorkflowState.COMPLETED
        mock_client_adapter.reset_mock()

        result = await service.cancel_execution(
            workflow_id=created.workflow_id,
            reason="too late",
            graceful=True,
        )

        assert result.state is MoonMindWorkflowState.COMPLETED
        assert result.close_status is TemporalExecutionCloseStatus.COMPLETED
        mock_client_adapter.update_workflow.assert_not_called()
        mock_client_adapter.cancel_workflow.assert_not_called()
        mock_client_adapter.terminate_workflow.assert_not_called()

@pytest.mark.asyncio
async def test_cancel_execution_records_reject_audit_action(
    tmp_path, mock_client_adapter
):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)
        service._client_adapter = mock_client_adapter

        created = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=uuid4(),
            title=None,
            input_artifact_ref=None,
            plan_artifact_ref="artifact://plan/1",
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters=_valid_user_workflow_parameters(),
            idempotency_key=None,
        )

        canceled = await service.cancel_execution(
            workflow_id=created.workflow_id,
            reason="Rejected by operator.",
            graceful=True,
            action="reject",
        )

        assert canceled.state is MoonMindWorkflowState.CANCELED
        assert canceled.memo["intervention_audit"][-1]["action"] == "reject"
        assert (
            canceled.memo["intervention_audit"][-1]["summary"]
            == "Rejected by operator."
        )
        assert canceled.closed_at is not None
        assert canceled.search_attributes["mm_state"] == "canceled"
        mock_client_adapter.update_workflow.assert_not_called()
        mock_client_adapter.cancel_workflow.assert_awaited_once_with(
            created.workflow_id
        )

@pytest.mark.asyncio
async def test_cancel_execution_accepts_projection_only_child_workflow(
    tmp_path, mock_client_adapter
):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)
        service._client_adapter = mock_client_adapter
        owner_id = str(uuid4())
        workflow_id = (
            "resolver:mm:parent:pr:1634:head:"
            "5ed0c032789b901b99da93eaa4877de6609fdf35:1"
        )
        now = datetime.now(UTC)
        projection = TemporalExecutionRecord(
            workflow_id=workflow_id,
            run_id=str(uuid4()),
            namespace="default",
            workflow_type=TemporalWorkflowType.USER_WORKFLOW,
            owner_id=owner_id,
            owner_type=TemporalExecutionOwnerType.USER,
            state=MoonMindWorkflowState.AWAITING_SLOT,
            close_status=None,
            entry="user_workflow",
            search_attributes={
                "mm_owner_type": "user",
                "mm_owner_id": owner_id,
                "mm_state": "awaiting_slot",
                "mm_updated_at": now.isoformat(),
                "mm_entry": "user_workflow",
            },
            memo={"title": "Resolve PR #1634", "summary": "Waiting for slot."},
            artifact_refs=[],
            parameters={},
            paused=False,
            awaiting_external=True,
            waiting_reason="provider_profile_slot",
            attention_required=False,
            projection_version=1,
            last_synced_at=now,
            sync_state=TemporalExecutionProjectionSyncState.FRESH,
            sync_error=None,
            source_mode=TemporalExecutionProjectionSourceMode.PROJECTION_ONLY,
            created_at=now,
            started_at=now,
            updated_at=now,
            closed_at=None,
        )
        session.add(projection)
        await session.commit()

        canceled = await service.cancel_execution(
            workflow_id=workflow_id,
            reason="stop child",
            graceful=True,
        )

        assert canceled.workflow_id == workflow_id
        assert canceled.state is MoonMindWorkflowState.CANCELED
        assert canceled.close_status is TemporalExecutionCloseStatus.CANCELED
        assert canceled.waiting_reason is None
        assert canceled.memo["summary"] == "stop child"
        assert canceled.memo["intervention_audit"][-1]["action"] == "cancel"
        assert canceled.search_attributes["mm_state"] == "canceled"
        mock_client_adapter.update_workflow.assert_not_called()
        mock_client_adapter.cancel_workflow.assert_awaited_once_with(workflow_id)

@pytest.mark.asyncio
async def test_cancel_execution_rejects_orphaned_projection_only_workflow(
    tmp_path, mock_client_adapter
):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)
        service._client_adapter = mock_client_adapter
        owner_id = str(uuid4())
        now = datetime.now(UTC)
        projection = TemporalExecutionRecord(
            workflow_id="resolver:mm:orphaned:pr:1634:head:abc:1",
            run_id=str(uuid4()),
            namespace="default",
            workflow_type=TemporalWorkflowType.USER_WORKFLOW,
            owner_id=owner_id,
            owner_type=TemporalExecutionOwnerType.USER,
            state=MoonMindWorkflowState.AWAITING_SLOT,
            close_status=None,
            entry="user_workflow",
            search_attributes={
                "mm_owner_type": "user",
                "mm_owner_id": owner_id,
                "mm_state": "awaiting_slot",
                "mm_updated_at": now.isoformat(),
                "mm_entry": "user_workflow",
            },
            memo={"title": "Resolve PR #1634", "summary": "Waiting for slot."},
            artifact_refs=[],
            parameters={},
            projection_version=1,
            last_synced_at=now,
            sync_state=TemporalExecutionProjectionSyncState.ORPHANED,
            sync_error="temporal execution missing",
            source_mode=TemporalExecutionProjectionSourceMode.PROJECTION_ONLY,
            created_at=now,
            started_at=now,
            updated_at=now,
            closed_at=None,
        )
        session.add(projection)
        await session.commit()

        with pytest.raises(TemporalExecutionNotFoundError):
            await service.cancel_execution(
                workflow_id=projection.workflow_id,
                reason="stop child",
                graceful=True,
            )

        mock_client_adapter.cancel_workflow.assert_not_called()

@pytest.mark.asyncio
async def test_cancel_execution_best_effort_terminates_workflow_scoped_codex_session(
    tmp_path, mock_client_adapter, monkeypatch
):
    async with temporal_db(tmp_path) as session:
        monkeypatch.setenv("MOONMIND_AGENT_RUNTIME_STORE", str(tmp_path / "agent_jobs"))
        service = TemporalExecutionService(session)
        service._client_adapter = mock_client_adapter

        created = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=uuid4(),
            title=None,
            input_artifact_ref=None,
            plan_artifact_ref="artifact://plan/1",
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters=_valid_user_workflow_parameters(),
            idempotency_key=None,
        )

        store = ManagedSessionStore(_get_managed_session_store_root())
        store.save(
            CodexManagedSessionRecord(
                sessionId=f"sess:{created.workflow_id}:codex_cli",
                sessionEpoch=1,
                agentRunId=created.workflow_id,
                containerId="container-1",
                threadId="thread-1",
                runtimeId="codex_cli",
                imageRef="ghcr.io/moonladderstudios/moonmind:latest",
                controlUrl="docker-exec://container-1",
                status="ready",
                workspacePath=f"/work/agent_jobs/{created.workflow_id}/repo",
                sessionWorkspacePath=f"/work/agent_jobs/{created.workflow_id}/session",
                artifactSpoolPath=f"/work/agent_jobs/{created.workflow_id}/artifacts",
                startedAt=datetime.now(tz=UTC),
            )
        )

        await service.cancel_execution(
            workflow_id=created.workflow_id,
            reason="stop",
            graceful=True,
        )

        mock_client_adapter.assert_has_calls(
            [
                call.update_workflow(
                    f"{created.workflow_id}:session:codex_cli",
                    "TerminateSession",
                    {"reason": "stop"},
                ),
                call.cancel_workflow(created.workflow_id),
            ],
            any_order=False,
        )

@pytest.mark.asyncio
async def test_cancel_execution_prefers_direct_session_record_load_for_codex_task_session(
    tmp_path, mock_client_adapter, monkeypatch
):
    async with temporal_db(tmp_path) as session:
        monkeypatch.setenv("MOONMIND_AGENT_RUNTIME_STORE", str(tmp_path / "agent_jobs"))
        service = TemporalExecutionService(session)
        service._client_adapter = mock_client_adapter

        created = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=uuid4(),
            title=None,
            input_artifact_ref=None,
            plan_artifact_ref="artifact://plan/1",
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters=_valid_user_workflow_parameters(),
            idempotency_key=None,
        )

        store = ManagedSessionStore(_get_managed_session_store_root())
        store.save(
            CodexManagedSessionRecord(
                sessionId=f"sess:{created.workflow_id}:codex_cli",
                sessionEpoch=1,
                agentRunId=created.workflow_id,
                containerId="container-1",
                threadId="thread-1",
                runtimeId="codex_cli",
                imageRef="ghcr.io/moonladderstudios/moonmind:latest",
                controlUrl="docker-exec://container-1",
                status="ready",
                workspacePath=f"/work/agent_jobs/{created.workflow_id}/repo",
                sessionWorkspacePath=f"/work/agent_jobs/{created.workflow_id}/session",
                artifactSpoolPath=f"/work/agent_jobs/{created.workflow_id}/artifacts",
                startedAt=datetime.now(tz=UTC),
            )
        )
        monkeypatch.setattr(
            ManagedSessionStore,
            "list_active",
            lambda self: (_ for _ in ()).throw(
                AssertionError("list_active should not run for canonical Codex session IDs")
            ),
        )

        await service.cancel_execution(
            workflow_id=created.workflow_id,
            reason="stop",
            graceful=True,
        )

        mock_client_adapter.assert_has_calls(
            [
                call.update_workflow(
                    f"{created.workflow_id}:session:codex_cli",
                    "TerminateSession",
                    {"reason": "stop"},
                ),
                call.cancel_workflow(created.workflow_id),
            ],
            any_order=False,
        )

@pytest.mark.asyncio
async def test_cancel_execution_ignores_best_effort_session_terminate_failure(
    tmp_path, mock_client_adapter, monkeypatch
):
    async with temporal_db(tmp_path) as session:
        monkeypatch.setenv("MOONMIND_AGENT_RUNTIME_STORE", str(tmp_path / "agent_jobs"))
        service = TemporalExecutionService(session)
        service._client_adapter = mock_client_adapter
        mock_client_adapter.update_workflow.side_effect = [
            RuntimeError("session closed"),
            None,
        ]

        created = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=uuid4(),
            title=None,
            input_artifact_ref=None,
            plan_artifact_ref="artifact://plan/1",
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters=_valid_user_workflow_parameters(),
            idempotency_key=None,
        )

        store = ManagedSessionStore(_get_managed_session_store_root())
        store.save(
            CodexManagedSessionRecord(
                sessionId=f"sess:{created.workflow_id}:codex_cli",
                sessionEpoch=1,
                agentRunId=created.workflow_id,
                containerId="container-1",
                threadId="thread-1",
                runtimeId="codex_cli",
                imageRef="ghcr.io/moonladderstudios/moonmind:latest",
                controlUrl="docker-exec://container-1",
                status="ready",
                workspacePath=f"/work/agent_jobs/{created.workflow_id}/repo",
                sessionWorkspacePath=f"/work/agent_jobs/{created.workflow_id}/session",
                artifactSpoolPath=f"/work/agent_jobs/{created.workflow_id}/artifacts",
                startedAt=datetime.now(tz=UTC),
            )
        )

        await service.cancel_execution(
            workflow_id=created.workflow_id,
            reason="stop",
            graceful=True,
        )

        mock_client_adapter.assert_has_calls(
            [
                call.update_workflow(
                    f"{created.workflow_id}:session:codex_cli",
                    "TerminateSession",
                    {"reason": "stop"},
                ),
                call.cancel_workflow(created.workflow_id),
            ],
            any_order=False,
        )
        mock_client_adapter.cancel_workflow.assert_awaited_once_with(
            created.workflow_id
        )

@pytest.mark.asyncio
async def test_forced_cancel_marks_failed_with_terminated_close_status(
    tmp_path, mock_client_adapter
):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)
        service._client_adapter = mock_client_adapter

        created = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=uuid4(),
            title=None,
            input_artifact_ref=None,
            plan_artifact_ref="artifact://plan/1",
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters=_valid_user_workflow_parameters(),
            idempotency_key=None,
        )

        terminated = await service.cancel_execution(
            workflow_id=created.workflow_id,
            reason="ops kill",
            graceful=False,
        )

        assert terminated.state is MoonMindWorkflowState.FAILED
        assert terminated.close_status is TemporalExecutionCloseStatus.TERMINATED
        assert terminated.memo["summary"] == "forced_termination: ops kill"

@pytest.mark.asyncio
async def test_forced_cancel_without_reason_uses_force_specific_audit_summary(
    tmp_path, mock_client_adapter
):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)
        service._client_adapter = mock_client_adapter

        created = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=uuid4(),
            title=None,
            input_artifact_ref=None,
            plan_artifact_ref="artifact://plan/1",
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters=_valid_user_workflow_parameters(),
            idempotency_key=None,
        )

        terminated = await service.cancel_execution(
            workflow_id=created.workflow_id,
            reason=None,
            graceful=False,
        )

        assert terminated.state is MoonMindWorkflowState.FAILED
        assert terminated.close_status is TemporalExecutionCloseStatus.TERMINATED
        assert terminated.memo["summary"] == (
            "forced_termination: Force canceled by operator."
        )
        assert terminated.memo["intervention_audit"][-1]["summary"] == (
            "Force canceled by operator."
        )
        mock_client_adapter.terminate_workflow.assert_called_once_with(
            created.workflow_id,
            reason="Force canceled by operator.",
        )

@pytest.mark.asyncio
async def test_request_rerun_can_override_inputs_and_parameters(
    tmp_path, mock_client_adapter
):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)
        service._client_adapter = mock_client_adapter

        created = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=uuid4(),
            title=None,
            input_artifact_ref="artifact://input/original",
            plan_artifact_ref="artifact://plan/original",
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters={"original": True},
            idempotency_key=None,
        )

        await service.update_execution(
            workflow_id=created.workflow_id,
            update_name="RequestRerun",
            input_artifact_ref="artifact://input/new",
            plan_artifact_ref="artifact://plan/new",
            parameters_patch={"force": "yes"},
            title=None,
            new_manifest_artifact_ref=None,
            mode=None,
            max_concurrency=None,
            node_ids=None,
            idempotency_key="rerun-with-overrides",
        )
        refreshed = await service.describe_execution(created.workflow_id)

        assert refreshed.input_ref == "artifact://input/new"
        assert refreshed.plan_ref == "artifact://plan/new"
        assert refreshed.parameters["force"] == "yes"
        assert "artifact://input/new" in refreshed.artifact_refs
        assert "artifact://plan/new" in refreshed.artifact_refs

@pytest.mark.asyncio
async def test_update_inputs_major_reconfiguration_records_distinct_continue_as_new_cause(
    tmp_path, mock_client_adapter
):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)
        service._client_adapter = mock_client_adapter

        created = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=uuid4(),
            title=None,
            input_artifact_ref=None,
            plan_artifact_ref="artifact://plan/original",
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters={
                "recoverySource": {"workflowId": "mm:source", "runId": "run-old"},
                "recoveryCheckpointRef": "artifact://checkpoint/old",
                "preservedSteps": [{"id": "step-1"}],
                "completedSteps": [{"id": "step-0"}],
                "workflow": {
                    "instructions": "Original task",
                    "recovery": {
                        "kind": "recover_from_failed_step",
                        "sourceWorkflowId": "mm:source",
                        "sourceRunId": "run-old",
                    },
                    "resume": {
                        "kind": "recover_from_failed_step",
                        "sourceWorkflowId": "mm:source",
                        "sourceRunId": "run-old",
                        "failedStepId": "implement",
                        "recoveryCheckpointRef": "artifact://checkpoint/old",
                        "taskInputSnapshotRef": "artifact://snapshot/old",
                    },
                },
            },
            idempotency_key=None,
        )

        response = await service.update_execution(
            workflow_id=created.workflow_id,
            update_name="UpdateInputs",
            input_artifact_ref=None,
            plan_artifact_ref="artifact://plan/replacement",
            parameters_patch=None,
            title=None,
            new_manifest_artifact_ref=None,
            mode=None,
            max_concurrency=None,
            node_ids=None,
            idempotency_key="update-major-reconfig",
        )
        refreshed = await service.describe_execution(created.workflow_id)

        assert response["accepted"] is True
        assert response["applied"] == "continue_as_new"
        assert response["continue_as_new_cause"] == "major_reconfiguration"
        assert refreshed.memo["continue_as_new_cause"] == "major_reconfiguration"
        assert refreshed.search_attributes["mm_continue_as_new_cause"] == (
            "major_reconfiguration"
        )
        assert "recoverySource" not in refreshed.parameters
        assert "recoveryCheckpointRef" not in refreshed.parameters
        assert "preservedSteps" not in refreshed.parameters
        assert "completedSteps" not in refreshed.parameters
        assert refreshed.parameters["workflow"] == {"instructions": "Original task"}


@pytest.mark.asyncio
async def test_update_inputs_continue_as_new_preserves_recovery_provenance_for_rollover_only(
    tmp_path, mock_client_adapter
):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)
        service._client_adapter = mock_client_adapter

        created = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=uuid4(),
            title=None,
            input_artifact_ref=None,
            plan_artifact_ref="artifact://plan/original",
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters={
                "recoverySource": {"workflowId": "mm:source", "runId": "run-old"},
                "recoveryCheckpointRef": "artifact://checkpoint/old",
                "preservedSteps": [{"id": "step-1"}],
                "completedSteps": [{"id": "step-0"}],
                "workflow": {
                    "instructions": "Original task",
                    "recovery": {
                        "kind": "recover_from_failed_step",
                        "sourceWorkflowId": "mm:source",
                        "sourceRunId": "run-old",
                    },
                    "resume": {
                        "kind": "recover_from_failed_step",
                        "sourceWorkflowId": "mm:source",
                        "sourceRunId": "run-old",
                        "failedStepId": "implement",
                        "recoveryCheckpointRef": "artifact://checkpoint/old",
                        "taskInputSnapshotRef": "artifact://snapshot/old",
                    },
                },
            },
            idempotency_key=None,
        )

        response = await service.update_execution(
            workflow_id=created.workflow_id,
            update_name="UpdateInputs",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            parameters_patch={"request_continue_as_new": True},
            title=None,
            new_manifest_artifact_ref=None,
            mode=None,
            max_concurrency=None,
            node_ids=None,
            idempotency_key="update-rollover-only",
        )
        refreshed = await service.describe_execution(created.workflow_id)

        assert response["accepted"] is True
        assert response["applied"] == "continue_as_new"
        assert response["continue_as_new_cause"] == "major_reconfiguration"
        assert refreshed.parameters["recoverySource"] == {
            "workflowId": "mm:source",
            "runId": "run-old",
        }
        assert refreshed.parameters["recoveryCheckpointRef"] == "artifact://checkpoint/old"
        assert refreshed.parameters["preservedSteps"] == [{"id": "step-1"}]
        assert refreshed.parameters["completedSteps"] == [{"id": "step-0"}]
        assert refreshed.parameters["workflow"]["recovery"]["kind"] == (
            "recover_from_failed_step"
        )
        assert refreshed.parameters["workflow"]["resume"]["recoveryCheckpointRef"] == (
            "artifact://checkpoint/old"
        )


@pytest.mark.asyncio
async def test_record_progress_triggers_continue_as_new_for_run_threshold(tmp_path):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(
            session, run_continue_as_new_step_threshold=2
        )

        created = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=uuid4(),
            title=None,
            input_artifact_ref=None,
            plan_artifact_ref="artifact://plan/1",
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters=_valid_user_workflow_parameters(),
            idempotency_key=None,
        )
        original_run_id = created.run_id

        first = await service.record_progress(
            workflow_id=created.workflow_id,
            completed_steps=1,
        )
        assert first.run_id == original_run_id
        assert first.step_count == 1

        second = await service.record_progress(
            workflow_id=created.workflow_id,
            completed_steps=1,
        )
        assert second.run_id != original_run_id
        assert second.rerun_count == 1
        assert second.step_count == 0
        assert second.memo["continue_as_new_cause"] == "lifecycle_threshold"
        assert second.search_attributes["mm_continue_as_new_cause"] == (
            "lifecycle_threshold"
        )

@pytest.mark.asyncio
async def test_signal_external_event_requires_source_and_event_type(tmp_path):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)

        created = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=uuid4(),
            title=None,
            input_artifact_ref=None,
            plan_artifact_ref="artifact://plan/1",
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters=_valid_user_workflow_parameters(),
            idempotency_key=None,
        )

        with pytest.raises(TemporalExecutionValidationError):
            await service.signal_execution(
                workflow_id=created.workflow_id,
                signal_name="ExternalEvent",
                payload={"source": "jules"},
                payload_artifact_ref=None,
            )

@pytest.mark.asyncio
async def test_configure_integration_monitoring_persists_visibility_and_callback_key(
    tmp_path,
):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)

        created = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=uuid4(),
            title="Run with integration",
            input_artifact_ref=None,
            plan_artifact_ref="artifact://plan/1",
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters=_valid_user_workflow_parameters(),
            idempotency_key=None,
        )

        configured = await service.configure_integration_monitoring(
            workflow_id=created.workflow_id,
            integration_name="Jules",
            correlation_id=None,
            external_operation_id="task-123",
            normalized_status="running",
            provider_status="in_progress",
            callback_supported=True,
            callback_correlation_key=None,
            recommended_poll_seconds=30,
            external_url="https://jules.example.test/tasks/task-123",
            provider_summary={"queue": "primary"},
            result_refs=["artifact://events/start"],
        )

        assert configured.state is MoonMindWorkflowState.AWAITING_EXTERNAL
        assert configured.awaiting_external is True
        assert configured.search_attributes["mm_integration"] == "jules"
        assert (
            configured.memo["external_url"]
            == "https://jules.example.test/tasks/task-123"
        )
        assert configured.integration_state is not None
        assert configured.integration_state["callback_correlation_key"]
        assert configured.integration_state["external_operation_id"] == "task-123"
        assert "artifact://events/start" in configured.artifact_refs


@pytest.mark.asyncio
async def test_configure_integration_monitoring_rejects_waiting_states(tmp_path):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)

        for state in (
            MoonMindWorkflowState.WAITING_ON_DEPENDENCIES,
            MoonMindWorkflowState.AWAITING_SLOT,
        ):
            created = await service.create_execution(
                workflow_type="MoonMind.UserWorkflow",
                owner_id=uuid4(),
                title=f"Run waiting in {state.value}",
                input_artifact_ref=None,
                plan_artifact_ref="artifact://plan/1",
                manifest_artifact_ref=None,
                failure_policy=None,
                initial_parameters=_valid_user_workflow_parameters(),
                idempotency_key=None,
            )
            source = await service._require_source_execution(created.workflow_id)
            service._set_state(source, state)
            await session.commit()

            with pytest.raises(TemporalExecutionValidationError):
                await service.configure_integration_monitoring(
                    workflow_id=created.workflow_id,
                    integration_name="jules",
                    correlation_id=None,
                    external_operation_id=f"task-{state.value}",
                    normalized_status="running",
                    provider_status="running",
                    callback_supported=True,
                    callback_correlation_key=None,
                    recommended_poll_seconds=30,
                    external_url=None,
                    provider_summary={},
                    result_refs=[],
                )


@pytest.mark.asyncio
async def test_configure_integration_monitoring_rejects_blank_external_operation_id(
    tmp_path,
):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)

        created = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=uuid4(),
            title="Run with invalid integration id",
            input_artifact_ref=None,
            plan_artifact_ref="artifact://plan/1",
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters=_valid_user_workflow_parameters(),
            idempotency_key=None,
        )

        with pytest.raises(
            TemporalExecutionValidationError,
            match="external_operation_id is required",
        ):
            await service.configure_integration_monitoring(
                workflow_id=created.workflow_id,
                integration_name="jules",
                correlation_id=None,
                external_operation_id="   ",
                normalized_status="running",
                provider_status="running",
                callback_supported=True,
                callback_correlation_key=None,
                recommended_poll_seconds=30,
                external_url=None,
                provider_summary={},
                result_refs=[],
            )

@pytest.mark.asyncio
async def test_ingest_integration_callback_deduplicates_provider_event_ids(
    tmp_path, mock_client_adapter
):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)
        service._client_adapter = mock_client_adapter

        created = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=uuid4(),
            title=None,
            input_artifact_ref=None,
            plan_artifact_ref="artifact://plan/1",
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters=_valid_user_workflow_parameters(),
            idempotency_key=None,
        )
        configured = await service.configure_integration_monitoring(
            workflow_id=created.workflow_id,
            integration_name="jules",
            correlation_id="corr-1",
            external_operation_id="task-123",
            normalized_status="running",
            provider_status="running",
            callback_supported=True,
            callback_correlation_key="cb-123",
            recommended_poll_seconds=15,
            external_url=None,
            provider_summary={},
            result_refs=[],
        )

        first = await service.ingest_integration_callback(
            integration_name="jules",
            callback_correlation_key="cb-123",
            payload={
                "event_type": "status_changed",
                "provider_event_id": "evt-1",
                "normalized_status": "running",
                "provider_status": "running",
            },
            payload_artifact_ref=None,
        )
        second = await service.ingest_integration_callback(
            integration_name="jules",
            callback_correlation_key="cb-123",
            payload={
                "event_type": "status_changed",
                "provider_event_id": "evt-1",
                "normalized_status": "running",
                "provider_status": "running",
            },
            payload_artifact_ref=None,
        )

        assert first.workflow_id == configured.workflow_id
        assert second.integration_state["provider_event_ids_seen"] == ["evt-1"]
        assert "Ignored duplicate external event" in second.memo["summary"]

@pytest.mark.asyncio
async def test_wait_cycle_continue_as_new_preserves_active_integration_monitoring(
    tmp_path,
):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(
            session,
            run_continue_as_new_step_threshold=100,
            run_continue_as_new_wait_cycle_threshold=2,
        )

        created = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=uuid4(),
            title=None,
            input_artifact_ref=None,
            plan_artifact_ref="artifact://plan/1",
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters=_valid_user_workflow_parameters(),
            idempotency_key=None,
        )
        configured = await service.configure_integration_monitoring(
            workflow_id=created.workflow_id,
            integration_name="jules",
            correlation_id="corr-continue",
            external_operation_id="task-continue",
            normalized_status="running",
            provider_status="running",
            callback_supported=True,
            callback_correlation_key="cb-continue",
            recommended_poll_seconds=5,
            external_url=None,
            provider_summary={},
            result_refs=[],
        )
        original_run_id = configured.run_id

        updated = await service.record_integration_poll(
            workflow_id=created.workflow_id,
            normalized_status="running",
            provider_status="running",
            observed_at=None,
            recommended_poll_seconds=5,
            external_url=None,
            provider_summary={},
            result_refs=[],
            completed_wait_cycles=2,
        )

        assert updated.run_id != original_run_id
        assert updated.state is MoonMindWorkflowState.AWAITING_EXTERNAL
        assert updated.awaiting_external is True
        assert updated.wait_cycle_count == 0
        assert updated.integration_state["external_operation_id"] == "task-continue"
        assert updated.integration_state["callback_correlation_key"] == "cb-continue"

@pytest.mark.asyncio
async def test_mark_execution_failed_rejects_unknown_error_category(tmp_path):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)

        created = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=uuid4(),
            title=None,
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters=_valid_user_workflow_parameters(),
            idempotency_key=None,
        )

        with pytest.raises(TemporalExecutionValidationError):
            await service.mark_execution_failed(
                workflow_id=created.workflow_id,
                error_category="unknown",
                message="boom",
            )

@pytest.mark.asyncio
async def test_projection_sync_markers_round_trip_between_stale_and_fresh(tmp_path):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)

        created = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=uuid4(),
            title=None,
            input_artifact_ref=None,
            plan_artifact_ref="artifact://plan/1",
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters=_valid_user_workflow_parameters(),
            idempotency_key=None,
        )

        stale = await service.mark_projection_stale(
            workflow_id=created.workflow_id,
            sync_error="visibility lag",
        )
        assert stale.sync_state is TemporalExecutionProjectionSyncState.STALE
        assert stale.sync_error == "visibility lag"

        refreshed = await service.mark_execution_executing(
            workflow_id=created.workflow_id,
            summary="back in sync",
        )
        assert refreshed.sync_state is TemporalExecutionProjectionSyncState.FRESH
        assert refreshed.sync_error is None
        assert refreshed.search_attributes["mm_owner_type"] == "user"

@pytest.mark.asyncio
async def test_update_execution_persists_repair_pending_when_projection_refresh_fails(
    tmp_path, monkeypatch, mock_client_adapter
):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)
        service._client_adapter = mock_client_adapter

        created = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=uuid4(),
            title="Before failure",
            input_artifact_ref=None,
            plan_artifact_ref="artifact://plan/1",
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters=_valid_user_workflow_parameters(),
            idempotency_key=None,
        )
        previous_sync_at = created.last_synced_at

        async def fail_projection_sync(source, **kwargs):
            raise RuntimeError(f"projection write failed for {source.workflow_id}")

        monkeypatch.setattr(
            service, "_upsert_projection_from_source", fail_projection_sync
        )

        response = await service.update_execution(
            workflow_id=created.workflow_id,
            update_name="SetTitle",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            parameters_patch=None,
            title="After failure",
            idempotency_key="repair-pending-update",
        )

        assert response["accepted"] is True

        projection = await session.get(TemporalExecutionRecord, created.workflow_id)
        source = await session.get(
            TemporalExecutionCanonicalRecord, created.workflow_id
        )
        assert projection is not None
        assert source is not None
        assert (
            projection.sync_state is TemporalExecutionProjectionSyncState.REPAIR_PENDING
        )
        assert (
            projection.source_mode
            is TemporalExecutionProjectionSourceMode.TEMPORAL_AUTHORITATIVE
        )
        assert "projection write failed" in (projection.sync_error or "")
        assert projection.last_synced_at == previous_sync_at
        assert projection.memo["title"] == "Before failure"
        assert source.memo["title"] == "After failure"

@pytest.mark.asyncio
async def test_orphaned_projection_rows_are_repaired_from_canonical_lists(tmp_path):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)
        owner_id = uuid4()

        visible = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=owner_id,
            title="visible",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters=_valid_user_workflow_parameters(),
            idempotency_key="visible-row",
        )
        hidden = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=owner_id,
            title="hidden",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters=_valid_user_workflow_parameters(),
            idempotency_key="hidden-row",
        )

        orphaned = await service.mark_projection_orphaned(
            workflow_id=hidden.workflow_id,
            sync_error="temporal execution missing",
        )
        assert orphaned.sync_state is TemporalExecutionProjectionSyncState.ORPHANED

        listed = await service.list_executions(
            workflow_type="MoonMind.UserWorkflow",
            owner_type="user",
            state=None,
            owner_id=owner_id,
            entry="user_workflow",
            page_size=10,
            next_page_token=None,
        )

        assert {item.workflow_id for item in listed.items} == {
            hidden.workflow_id,
            visible.workflow_id,
        }
        assert listed.count == 2

        repaired = await service.describe_execution(hidden.workflow_id)
        assert repaired.sync_state is TemporalExecutionProjectionSyncState.FRESH
        assert repaired.source_mode is (
            TemporalExecutionProjectionSourceMode.TEMPORAL_AUTHORITATIVE
        )

@pytest.mark.asyncio
async def test_orphaned_projection_rows_with_canonical_source_repair_on_read_and_update(
    tmp_path, mock_client_adapter
):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)
        service._client_adapter = mock_client_adapter

        created = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=uuid4(),
            title="hidden",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters=_valid_user_workflow_parameters(),
            idempotency_key="hidden-describe-row",
        )

        await service.mark_projection_orphaned(
            workflow_id=created.workflow_id,
            sync_error="temporal execution missing",
        )

        repaired = await service.describe_execution(created.workflow_id)
        assert repaired.sync_state is TemporalExecutionProjectionSyncState.FRESH
        assert repaired.state is MoonMindWorkflowState.INITIALIZING

        response = await service.update_execution(
            workflow_id=created.workflow_id,
            update_name="SetTitle",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            parameters_patch=None,
            title="Should apply",
            idempotency_key="orphaned-update",
        )
        assert response["accepted"] is True

        updated = await service.describe_execution(created.workflow_id)
        assert updated.memo["title"] == "Should apply"

@pytest.mark.asyncio
async def test_ghost_projection_rows_without_canonical_source_are_hidden(tmp_path):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)
        owner_id = str(uuid4())
        created_at = datetime.now(UTC)
        ghost = TemporalExecutionRecord(
            workflow_id="mm:ghost-row",
            run_id=str(uuid4()),
            namespace="moonmind",
            workflow_type=TemporalWorkflowType.USER_WORKFLOW,
            owner_id=owner_id,
            owner_type=TemporalExecutionOwnerType.USER,
            state=MoonMindWorkflowState.EXECUTING,
            close_status=None,
            entry="user_workflow",
            search_attributes={
                "mm_owner_type": "user",
                "mm_owner_id": owner_id,
                "mm_state": "executing",
                "mm_updated_at": "2026-03-06T00:00:00+00:00",
                "mm_entry": "user_workflow",
            },
            memo={"title": "ghost", "summary": "Ghost row"},
            artifact_refs=[],
            parameters={},
            projection_version=1,
            last_synced_at=created_at,
            sync_state=TemporalExecutionProjectionSyncState.FRESH,
            sync_error=None,
            source_mode=TemporalExecutionProjectionSourceMode.PROJECTION_ONLY,
            started_at=created_at,
            updated_at=created_at,
            closed_at=None,
        )
        session.add(ghost)
        await session.commit()

        listed = await service.list_executions(
            workflow_type="MoonMind.UserWorkflow",
            owner_type="user",
            state=None,
            owner_id=owner_id,
            entry="user_workflow",
            page_size=10,
            next_page_token=None,
        )

        assert listed.count == 0
        assert listed.items == []

        with pytest.raises(TemporalExecutionNotFoundError):
            await service.describe_execution(ghost.workflow_id)


@pytest.mark.asyncio
async def test_describe_execution_accepts_scheduled_temporal_projection(
    tmp_path,
    mock_client_adapter,
):
    async with temporal_db(tmp_path) as session:
        owner_id = str(uuid4())
        created_at = datetime.now(UTC)
        workflow_id = "mm:scheduled-parent-2026-07-14T06:59:11Z"
        runtime_parameters = {
            "targetRuntime": "codex_cli",
            "model": "gpt-5.3-codex-spark",
            "effort": "xhigh",
            "profileId": "codex_openai_oauth",
            "workflow": {
                "runtime": {
                    "mode": "codex_cli",
                    "model": "gpt-5.3-codex-spark",
                    "effort": "xhigh",
                    "executionProfileRef": "codex_openai_oauth",
                }
            },
        }
        description = Mock(spec=WorkflowExecutionDescription)
        description.id = workflow_id
        description.run_id = str(uuid4())
        description.namespace = "default"
        description.workflow_type = "MoonMind.UserWorkflow"
        description.status = WorkflowExecutionStatus.RUNNING
        description.start_time = created_at
        description.execution_time = created_at
        description.close_time = None
        description.search_attributes = {
            "mm_owner_type": "user",
            "mm_owner_id": owner_id,
            "mm_state": "executing",
            "mm_target_runtime": ["codex_cli"],
        }

        async def _memo() -> dict[str, object]:
            return {
                "title": "Scheduled parent",
                "targetRuntime": "codex_cli",
                "parameters": runtime_parameters,
            }

        description.memo = _memo
        mock_client_adapter.describe_workflow.return_value = description
        service = TemporalExecutionService(
            session,
            client_adapter=mock_client_adapter,
        )

        described = await service.describe_execution(workflow_id)

        assert described.workflow_id == workflow_id
        assert described.owner_id == owner_id
        assert described.memo["targetRuntime"] == "codex_cli"
        assert described.parameters == runtime_parameters
        assert (
            described.source_mode
            == TemporalExecutionProjectionSourceMode.TEMPORAL_AUTHORITATIVE
        )
        assert (
            await session.get(TemporalExecutionCanonicalRecord, workflow_id)
            is None
        )


@pytest.mark.asyncio
async def test_mark_execution_succeeded_rejects_terminal_execution(
    tmp_path, mock_client_adapter
):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)
        service._client_adapter = mock_client_adapter

        created = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=uuid4(),
            title=None,
            input_artifact_ref=None,
            plan_artifact_ref="artifact://plan/1",
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters=_valid_user_workflow_parameters(),
            idempotency_key=None,
        )

        await service.cancel_execution(
            workflow_id=created.workflow_id,
            reason="stop",
            graceful=True,
        )

        with pytest.raises(TemporalExecutionValidationError):
            await service.mark_execution_succeeded(workflow_id=created.workflow_id)

        canceled = await service.describe_execution(created.workflow_id)
        assert canceled.state is MoonMindWorkflowState.CANCELED
        assert canceled.close_status is TemporalExecutionCloseStatus.CANCELED

@pytest.mark.asyncio
async def test_list_executions_filters_owner_and_paginates(tmp_path):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)
        owner_a = uuid4()
        owner_b = uuid4()

        for idx in range(3):
            await service.create_execution(
                workflow_type="MoonMind.UserWorkflow",
                owner_id=owner_a,
                title=f"A-{idx}",
                input_artifact_ref=None,
                plan_artifact_ref=None,
                manifest_artifact_ref=None,
                failure_policy=None,
                initial_parameters=_valid_user_workflow_parameters(),
                idempotency_key=f"owner-a-{idx}",
            )
        await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=owner_b,
            title="B-0",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters=_valid_user_workflow_parameters(),
            idempotency_key="owner-b-0",
        )
        await service.create_execution(
            workflow_type="MoonMind.ManifestIngest",
            owner_id=owner_a,
            title="manifest-0",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref="artifact://manifest/owner-a",
            failure_policy=None,
            initial_parameters={},
            idempotency_key="owner-a-manifest-0",
        )

        first_page = await service.list_executions(
            workflow_type="MoonMind.UserWorkflow",
            state=None,
            entry="user_workflow",
            owner_type="user",
            owner_id=owner_a,
            repo=None,
            integration=None,
            page_size=2,
            next_page_token=None,
        )
        assert len(first_page.items) == 2
        assert first_page.next_page_token is not None
        assert first_page.count == 3

        second_page = await service.list_executions(
            workflow_type="MoonMind.UserWorkflow",
            state=None,
            entry="user_workflow",
            owner_type="user",
            owner_id=owner_a,
            repo=None,
            integration=None,
            page_size=2,
            next_page_token=first_page.next_page_token,
        )
        assert len(second_page.items) == 1
        assert second_page.count == 3

        manifest_page = await service.list_executions(
            workflow_type=None,
            state=None,
            entry="manifest",
            owner_type="user",
            owner_id=owner_a,
            page_size=10,
            next_page_token=None,
        )
        assert len(manifest_page.items) == 1
        assert manifest_page.items[0].entry == "manifest"

@pytest.mark.asyncio
async def test_list_executions_orders_by_updated_at_then_workflow_id(tmp_path):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)
        owner_id = uuid4()

        older = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=owner_id,
            title="older",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters=_valid_user_workflow_parameters(),
            idempotency_key="older-row",
        )
        newer = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=owner_id,
            title="newer",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters=_valid_user_workflow_parameters(),
            idempotency_key="newer-row",
        )
        tied_a = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=owner_id,
            title="tied-a",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters=_valid_user_workflow_parameters(),
            idempotency_key="tied-a-row",
        )
        tied_b = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=owner_id,
            title="tied-b",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters=_valid_user_workflow_parameters(),
            idempotency_key="tied-b-row",
        )

        older_updated_at = datetime(2026, 3, 6, 12, 0, tzinfo=UTC)
        newer_updated_at = datetime(2026, 3, 6, 12, 5, tzinfo=UTC)
        tied_updated_at = datetime(2026, 3, 6, 12, 3, tzinfo=UTC)
        older_scheduled_for = datetime(2026, 3, 8, 12, 0, tzinfo=UTC)
        newer_scheduled_for = datetime(2026, 3, 7, 12, 0, tzinfo=UTC)
        for workflow_id, (updated_at, scheduled_for) in {
            older.workflow_id: (older_updated_at, older_scheduled_for),
            newer.workflow_id: (newer_updated_at, newer_scheduled_for),
            tied_a.workflow_id: (tied_updated_at, None),
            tied_b.workflow_id: (tied_updated_at, None),
        }.items():
            source = await session.get(TemporalExecutionCanonicalRecord, workflow_id)
            projection = await session.get(TemporalExecutionRecord, workflow_id)
            assert source is not None
            assert projection is not None
            source.updated_at = updated_at
            source.search_attributes["mm_updated_at"] = updated_at.isoformat()
            projection.updated_at = updated_at
            projection.search_attributes["mm_updated_at"] = updated_at.isoformat()
            projection.scheduled_for = scheduled_for
        await session.commit()

        listed = await service.list_executions(
            workflow_type="MoonMind.UserWorkflow",
            state=None,
            entry="user_workflow",
            owner_type="user",
            owner_id=owner_id,
            repo=None,
            integration=None,
            page_size=10,
            next_page_token=None,
        )

        expected_tied_order = sorted(
            [tied_a.workflow_id, tied_b.workflow_id],
            reverse=True,
        )
        assert [item.workflow_id for item in listed.items] == [
            newer.workflow_id,
            *expected_tied_order,
            older.workflow_id,
        ]

@pytest.mark.asyncio
async def test_list_executions_orders_scheduled_rows_by_latest_scheduled_for(tmp_path):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)
        owner_id = uuid4()

        late = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=owner_id,
            title="late",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters=_valid_user_workflow_parameters(),
            idempotency_key="late-scheduled-row",
        )
        early = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=owner_id,
            title="early",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters=_valid_user_workflow_parameters(),
            idempotency_key="early-scheduled-row",
        )
        running = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=owner_id,
            title="running",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters=_valid_user_workflow_parameters(),
            idempotency_key="running-row",
        )

        late_scheduled_for = datetime(2026, 4, 15, 18, 0, tzinfo=UTC)
        early_scheduled_for = datetime(2026, 4, 15, 9, 0, tzinfo=UTC)

        for workflow_id, scheduled_for in {
            late.workflow_id: late_scheduled_for,
            early.workflow_id: early_scheduled_for,
        }.items():
            source = await session.get(TemporalExecutionCanonicalRecord, workflow_id)
            projection = await session.get(TemporalExecutionRecord, workflow_id)
            assert source is not None
            assert projection is not None
            source.state = MoonMindWorkflowState.SCHEDULED
            source.started_at = None
            projection.state = MoonMindWorkflowState.SCHEDULED
            projection.scheduled_for = scheduled_for
            projection.started_at = None
        await session.commit()

        listed = await service.list_executions(
            workflow_type="MoonMind.UserWorkflow",
            state=None,
            entry="user_workflow",
            owner_type="user",
            owner_id=owner_id,
            repo=None,
            integration=None,
            page_size=10,
            next_page_token=None,
        )

        assert [item.workflow_id for item in listed.items[:2]] == [
            late.workflow_id,
            early.workflow_id,
        ]
        assert running.workflow_id in [item.workflow_id for item in listed.items[2:]]
        assert listed.items[0].started_at is None

@pytest.mark.asyncio
async def test_list_executions_filters_entry_repo_and_integration(tmp_path):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)
        owner_id = uuid4()

        matching = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=owner_id,
            title="Matching run",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters=_valid_user_workflow_parameters(),
            idempotency_key="matching-run",
            repository="Moon/Mind",
            integration="github",
        )
        await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=owner_id,
            title="Other repo",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters=_valid_user_workflow_parameters(),
            idempotency_key="other-repo",
            repository="Other/Repo",
            integration="github",
        )
        await service.create_execution(
            workflow_type="MoonMind.ManifestIngest",
            owner_id=owner_id,
            title="Manifest",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref="artifact://manifest/1",
            failure_policy=None,
            initial_parameters={},
            idempotency_key="manifest-run",
        )
        result = await service.list_executions(
            workflow_type=None,
            state=None,
            entry="user_workflow",
            owner_type="user",
            owner_id=owner_id,
            repo="Moon/Mind",
            integration="github",
            page_size=10,
            next_page_token=None,
        )

        assert result.count == 1
        assert len(result.items) == 1
        assert result.items[0].workflow_id == matching.workflow_id

@pytest.mark.asyncio
async def test_polling_backoff_resets_after_status_change_and_updates_visibility(
    tmp_path,
):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(
            session,
            integration_poll_initial_seconds=5,
            integration_poll_max_seconds=30,
            integration_poll_jitter_ratio=0.0,
        )

        created = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=uuid4(),
            title="Backoff test",
            input_artifact_ref=None,
            plan_artifact_ref="artifact://plan/1",
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters=_valid_user_workflow_parameters(),
            idempotency_key=None,
        )
        configured = await service.configure_integration_monitoring(
            workflow_id=created.workflow_id,
            integration_name="jules",
            correlation_id="corr-backoff",
            external_operation_id="task-backoff",
            normalized_status="queued",
            provider_status="pending",
            callback_supported=True,
            callback_correlation_key="cb-backoff",
            recommended_poll_seconds=None,
            external_url=None,
            provider_summary={},
            result_refs=[],
        )
        assert configured.integration_state["poll_interval_seconds"] == 5
        assert configured.search_attributes["mm_stage"] == "queued"

        first_poll = await service.record_integration_poll(
            workflow_id=created.workflow_id,
            normalized_status="queued",
            provider_status="pending",
            observed_at=None,
            recommended_poll_seconds=None,
            external_url=None,
            provider_summary={},
            result_refs=[],
            completed_wait_cycles=0,
        )
        assert first_poll.integration_state["poll_interval_seconds"] == 10

        second_poll = await service.record_integration_poll(
            workflow_id=created.workflow_id,
            normalized_status="running",
            provider_status="in_progress",
            observed_at=None,
            recommended_poll_seconds=None,
            external_url=None,
            provider_summary={},
            result_refs=[],
            completed_wait_cycles=0,
        )
        assert second_poll.integration_state["poll_interval_seconds"] == 5
        assert second_poll.search_attributes["mm_stage"] == "running"

@pytest.mark.asyncio
async def test_late_non_terminal_callback_is_ignored_after_terminal_completion(
    tmp_path, mock_client_adapter
):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)
        service._client_adapter = mock_client_adapter

        created = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=uuid4(),
            title=None,
            input_artifact_ref=None,
            plan_artifact_ref="artifact://plan/1",
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters=_valid_user_workflow_parameters(),
            idempotency_key=None,
        )
        await service.configure_integration_monitoring(
            workflow_id=created.workflow_id,
            integration_name="jules",
            correlation_id="corr-late",
            external_operation_id="task-late",
            normalized_status="running",
            provider_status="running",
            callback_supported=True,
            callback_correlation_key="cb-late",
            recommended_poll_seconds=5,
            external_url=None,
            provider_summary={},
            result_refs=[],
        )
        await service.ingest_integration_callback(
            integration_name="jules",
            callback_correlation_key="cb-late",
            payload={
                "event_type": "completed",
                "provider_event_id": "evt-complete",
                "normalized_status": "completed",
                "provider_status": "completed",
            },
            payload_artifact_ref=None,
        )
        late = await service.ingest_integration_callback(
            integration_name="jules",
            callback_correlation_key="cb-late",
            payload={
                "event_type": "progress",
                "provider_event_id": "evt-progress",
                "normalized_status": "running",
                "provider_status": "running",
            },
            payload_artifact_ref=None,
        )

        assert late.integration_state["normalized_status"] == "completed"
        assert "Ignored late non-terminal external event" in late.memo["summary"]

@pytest.mark.asyncio
async def test_failed_poll_marks_integration_error_summary(tmp_path):
    async with temporal_db(tmp_path) as session:
        service = TemporalExecutionService(session)

        created = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=uuid4(),
            title=None,
            input_artifact_ref=None,
            plan_artifact_ref="artifact://plan/1",
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters=_valid_user_workflow_parameters(),
            idempotency_key=None,
        )
        await service.configure_integration_monitoring(
            workflow_id=created.workflow_id,
            integration_name="jules",
            correlation_id="corr-fail",
            external_operation_id="task-fail",
            normalized_status="running",
            provider_status="running",
            callback_supported=False,
            callback_correlation_key=None,
            recommended_poll_seconds=5,
            external_url=None,
            provider_summary={},
            result_refs=[],
        )
        failed = await service.record_integration_poll(
            workflow_id=created.workflow_id,
            normalized_status="failed",
            provider_status="errored",
            observed_at=None,
            recommended_poll_seconds=None,
            external_url=None,
            provider_summary={"message": "boom"},
            result_refs=[],
            completed_wait_cycles=0,
        )

        assert failed.memo["error_category"] == "integration_error"
        assert failed.awaiting_external is False
