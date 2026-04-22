from __future__ import annotations

import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import (
    Base,
    MoonMindWorkflowState,
    TemporalArtifactLink,
    TemporalArtifactStatus,
    TemporalExecutionCanonicalRecord,
    TemporalExecutionRemediationLink,
)
from moonmind.workflows.temporal import (
    ExecutionRef,
    LocalTemporalArtifactStore,
    TemporalArtifactRepository,
    TemporalArtifactService,
)
from moonmind.workflows.temporal.remediation_context import (
    RemediationContextBuilder,
    RemediationContextError,
)
from moonmind.workflows.temporal.remediation_actions import (
    RemediationActionAuthorityService,
    RemediationPermissionSet,
    RemediationSecurityProfile,
)
from moonmind.workflows.temporal.remediation_tools import (
    RemediationEvidenceToolError,
    RemediationEvidenceToolService,
    RemediationLiveFollowEvent,
    RemediationLiveFollowResult,
    RemediationLogReadResult,
)
from moonmind.workflows.temporal.service import TemporalExecutionService


async def _create_target_and_remediation(
    session: AsyncSession,
    mock_client_adapter,
    *,
    owner_id=None,
    authority_mode: str = "observe_only",
    action_policy_ref: str = "admin_healer_default",
):
    owner = owner_id or uuid4()
    mock_client_adapter.start_workflow.side_effect = [
        SimpleNamespace(run_id="target-run"),
        SimpleNamespace(run_id="remediation-run"),
    ]
    execution_service = TemporalExecutionService(
        session, client_adapter=mock_client_adapter
    )
    target = await execution_service.create_execution(
        workflow_type="MoonMind.Run",
        owner_id=owner,
        title="Target",
        input_artifact_ref=None,
        plan_artifact_ref=None,
        manifest_artifact_ref=None,
        failure_policy=None,
        initial_parameters={},
        idempotency_key=None,
    )
    remediation = await execution_service.create_execution(
        workflow_type="MoonMind.Run",
        owner_id=owner,
        title="Remediate target",
        input_artifact_ref=None,
        plan_artifact_ref=None,
        manifest_artifact_ref=None,
        failure_policy=None,
        initial_parameters={
            "task": {
                "remediation": {
                    "target": {"workflowId": target.workflow_id},
                    "authorityMode": authority_mode,
                    "actionPolicyRef": action_policy_ref,
                    "approvalPolicy": {
                        "mode": "risk_gated",
                        "autoAllowedRisk": "medium",
                    },
                }
            }
        },
        idempotency_key=None,
    )
    return target, remediation


def _admin_permissions(**overrides):
    data = {
        "can_view_target": True,
        "can_create_remediation": True,
        "can_request_admin_profile": True,
        "can_approve_high_risk": False,
        "can_inspect_audit": False,
    }
    data.update(overrides)
    return RemediationPermissionSet(**data)


def _admin_profile(**overrides):
    data = {
        "profile_ref": "admin_healer",
        "execution_principal": "service:admin-healer",
        "allowed_action_kinds": ("restart_worker", "terminate_session"),
        "enabled": True,
    }
    data.update(overrides)
    return RemediationSecurityProfile(**data)


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
    db_url = f"sqlite+aiosqlite:///{tmp_path}/remediation_context.db"
    engine = create_async_engine(db_url, future=True)
    session_factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    try:
        async with session_factory() as session:
            yield session
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_remediation_context_builder_creates_bounded_linked_artifact(
    tmp_path, mock_client_adapter
):
    async with temporal_db(tmp_path) as session:
        owner_id = uuid4()
        mock_client_adapter.start_workflow.side_effect = [
            SimpleNamespace(run_id="target-run"),
            SimpleNamespace(run_id="remediation-run"),
        ]
        execution_service = TemporalExecutionService(
            session, client_adapter=mock_client_adapter
        )
        artifact_service = TemporalArtifactService(
            TemporalArtifactRepository(session),
            store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
        )

        target = await execution_service.create_execution(
            workflow_type="MoonMind.Run",
            owner_id=owner_id,
            title="Target needing help",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters={},
            idempotency_key=None,
            summary="Target summary",
        )
        target_source = await session.get(
            TemporalExecutionCanonicalRecord, target.workflow_id
        )
        assert target_source is not None
        target_source.artifact_refs = [
            "art_target_summary",
            "artifact://legacy/ref",
            str(tmp_path / "storage" / "raw.log"),
            "https://storage.example/presigned?token=raw-secret",
        ]
        target_source.input_ref = "art_input_ref"
        await session.commit()

        remediation = await execution_service.create_execution(
            workflow_type="MoonMind.Run",
            owner_id=owner_id,
            title="Remediate target",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters={
                "task": {
                    "instructions": "Investigate the target",
                    "remediation": {
                        "target": {
                            "workflowId": target.workflow_id,
                            "stepSelectors": [
                                {
                                    "logicalStepId": "run-tests",
                                    "attempt": "1",
                                    "taskRunId": "tr_selected",
                                    "ignored": "not copied",
                                }
                            ],
                            "taskRunIds": [
                                f"tr_{index:02d}" for index in range(25)
                            ],
                        },
                        "mode": "snapshot_then_follow",
                        "authorityMode": "approval_gated",
                        "evidencePolicy": {
                            "includeStepLedger": True,
                            "tailLines": 999999,
                            "apiKey": "raw-api-key",
                            "nested": {
                                "enabled": True,
                                "password": "raw-password",
                            },
                        },
                        "actionPolicyRef": "admin_healer_default",
                        "approvalPolicy": {
                            "mode": "risk_gated",
                            "autoAllowedRisk": "medium",
                            "token": "raw-token",
                            "reviewers": [
                                {"user": "ops"},
                                {"authorization": "Bearer raw-secret"},
                            ],
                        },
                        "lockPolicy": {
                            "scope": "target_execution",
                            "mode": "exclusive",
                            "storageKey": "backend/raw/key",
                        },
                    },
                }
            },
            idempotency_key=None,
        )

        builder = RemediationContextBuilder(
            session=session,
            artifact_service=artifact_service,
        )
        result = await builder.build_context(
            remediation_workflow_id=remediation.workflow_id,
            principal="service:remediation-context",
        )

        assert result.link.context_artifact_ref == result.artifact.artifact_id
        assert result.artifact.status is TemporalArtifactStatus.COMPLETE
        assert result.artifact.metadata_json["artifact_type"] == "remediation.context"
        assert result.artifact.metadata_json["name"] == "reports/remediation_context.json"

        artifact_link = (
            await session.execute(
                select(TemporalArtifactLink).where(
                    TemporalArtifactLink.artifact_id == result.artifact.artifact_id
                )
            )
        ).scalar_one()
        assert artifact_link.workflow_id == remediation.workflow_id
        assert artifact_link.run_id == remediation.run_id
        assert artifact_link.link_type == "remediation.context"
        assert artifact_link.label == "reports/remediation_context.json"

        remediation_source = await session.get(
            TemporalExecutionCanonicalRecord, remediation.workflow_id
        )
        assert remediation_source is not None
        assert result.artifact.artifact_id in remediation_source.artifact_refs

        _artifact, payload_bytes = await artifact_service.read(
            artifact_id=result.artifact.artifact_id,
            principal="service:remediation-context",
        )
        payload = json.loads(payload_bytes.decode("utf-8"))
        assert payload["schemaVersion"] == "v1"
        assert payload["remediationWorkflowId"] == remediation.workflow_id
        assert payload["target"] == {
            "workflowId": target.workflow_id,
            "runId": target.run_id,
            "title": "Target needing help",
            "summary": "Target summary",
            "state": "initializing",
            "closeStatus": None,
        }
        assert payload["selectedSteps"] == [
            {
                "logicalStepId": "run-tests",
                "attempt": 1,
                "taskRunId": "tr_selected",
            }
        ]
        assert len(payload["evidence"]["taskRuns"]) == 20
        assert payload["evidence"]["taskRuns"][0] == {"taskRunId": "tr_00"}
        assert payload["evidence"]["targetArtifactRefs"] == [
            {"artifact_id": "art_target_summary"},
            {"artifact_id": "art_input_ref", "kind": "input"},
        ]
        assert payload["policies"]["authorityMode"] == "approval_gated"
        assert payload["policies"]["actionPolicyRef"] == "admin_healer_default"
        assert payload["policies"]["evidencePolicy"]["tailLines"] == 2000
        assert payload["policies"]["evidencePolicy"]["includeStepLedger"] is True
        assert payload["policies"]["evidencePolicy"]["nested"] == {"enabled": True}
        assert payload["policies"]["approvalPolicy"]["mode"] == "risk_gated"
        assert payload["policies"]["approvalPolicy"]["reviewers"] == [{"user": "ops"}]
        assert payload["policies"]["lockPolicy"]["mode"] == "exclusive"
        assert payload["liveFollow"] == {
            "mode": "snapshot_then_follow",
            "supported": False,
            "taskRunId": "tr_00",
            "resumeCursor": None,
        }
        assert payload["boundedness"] == {
            "maxTailLines": 2000,
            "maxTaskRunIds": 20,
            "rawLogBodiesIncluded": False,
            "artifactContentsIncluded": False,
        }

        serialized = json.dumps(payload)
        assert str(tmp_path) not in serialized
        assert "storage_key" not in serialized
        assert "presigned" not in serialized.lower()
        assert "raw-secret" not in serialized
        assert "raw-password" not in serialized
        assert "raw-token" not in serialized
        assert "raw-api-key" not in serialized
        assert "password" not in serialized.lower()
        assert "token=" not in serialized.lower()


@pytest.mark.asyncio
async def test_remediation_context_builder_rejects_non_remediation_workflow(
    tmp_path, mock_client_adapter
):
    async with temporal_db(tmp_path) as session:
        execution_service = TemporalExecutionService(
            session, client_adapter=mock_client_adapter
        )
        artifact_service = TemporalArtifactService(
            TemporalArtifactRepository(session),
            store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
        )
        ordinary = await execution_service.create_execution(
            workflow_type="MoonMind.Run",
            owner_id=uuid4(),
            title="Ordinary run",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters={},
            idempotency_key=None,
        )

        builder = RemediationContextBuilder(
            session=session,
            artifact_service=artifact_service,
        )
        with pytest.raises(RemediationContextError, match="No remediation link"):
            await builder.build_context(
                remediation_workflow_id=ordinary.workflow_id,
                principal="service:remediation-context",
            )

        links = (
            await session.execute(
                select(TemporalArtifactLink).where(
                    TemporalArtifactLink.link_type == "remediation.context"
                )
            )
        ).scalars().all()
        assert links == []


class RecordingLogReader:
    def __init__(self) -> None:
        self.calls = []

    async def read_logs(
        self,
        *,
        task_run_id,
        stream,
        cursor=None,
        tail_lines=None,
    ):
        self.calls.append(
            {
                "task_run_id": task_run_id,
                "stream": stream,
                "cursor": cursor,
                "tail_lines": tail_lines,
            }
        )
        return RemediationLogReadResult(
            task_run_id=task_run_id,
            stream=stream,
            lines=("line 1", "line 2"),
            next_cursor="cursor-2",
        )


class RecordingLiveFollower:
    def __init__(self) -> None:
        self.calls = []

    async def follow_logs(self, *, task_run_id, from_sequence=None):
        self.calls.append(
            {"task_run_id": task_run_id, "from_sequence": from_sequence}
        )
        return RemediationLiveFollowResult(
            task_run_id=task_run_id,
            events=(
                RemediationLiveFollowEvent(
                    sequence=43,
                    stream="stdout",
                    text="live line",
                    timestamp="2026-04-21T20:00:00Z",
                ),
            ),
            resume_cursor={"sequence": 43},
        )


@pytest.mark.asyncio
async def test_remediation_evidence_tools_read_only_context_declared_evidence(
    tmp_path, mock_client_adapter
):
    async with temporal_db(tmp_path) as session:
        owner_id = uuid4()
        mock_client_adapter.start_workflow.side_effect = [
            SimpleNamespace(run_id="target-run"),
            SimpleNamespace(run_id="remediation-run"),
        ]
        execution_service = TemporalExecutionService(
            session, client_adapter=mock_client_adapter
        )
        artifact_service = TemporalArtifactService(
            TemporalArtifactRepository(session),
            store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
        )

        target = await execution_service.create_execution(
            workflow_type="MoonMind.Run",
            owner_id=owner_id,
            title="Target",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters={},
            idempotency_key=None,
        )
        target_artifact, _upload = await artifact_service.create(
            principal="service:test",
            content_type="text/plain",
            size_bytes=len(b"target artifact"),
            link=ExecutionRef(
                namespace="default",
                workflow_id=target.workflow_id,
                run_id=target.run_id,
                link_type="target.evidence",
                label="target.txt",
            ),
            metadata_json={"artifact_type": "target.evidence"},
        )
        target_artifact = await artifact_service.write_complete(
            artifact_id=target_artifact.artifact_id,
            principal="service:test",
            payload=b"target artifact",
            content_type="text/plain",
        )
        target_source = await session.get(
            TemporalExecutionCanonicalRecord, target.workflow_id
        )
        assert target_source is not None
        target_source.artifact_refs = [target_artifact.artifact_id]
        await session.commit()

        remediation = await execution_service.create_execution(
            workflow_type="MoonMind.Run",
            owner_id=owner_id,
            title="Remediate target",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters={
                "task": {
                    "remediation": {
                        "target": {
                            "workflowId": target.workflow_id,
                            "taskRunIds": ["tr_allowed"],
                        },
                        "evidencePolicy": {"tailLines": 100},
                    },
                }
            },
            idempotency_key=None,
        )
        builder = RemediationContextBuilder(
            session=session,
            artifact_service=artifact_service,
        )
        build_result = await builder.build_context(
            remediation_workflow_id=remediation.workflow_id
        )

        reader = RecordingLogReader()
        tools = RemediationEvidenceToolService(
            session=session,
            artifact_service=artifact_service,
            log_reader=reader,
        )
        context_reads = 0
        original_read = artifact_service.read

        async def read_spy(*, artifact_id, principal):
            nonlocal context_reads
            if artifact_id == build_result.artifact.artifact_id:
                context_reads += 1
            return await original_read(artifact_id=artifact_id, principal=principal)

        artifact_service.read = read_spy
        context = await tools.get_context(remediation_workflow_id=remediation.workflow_id)
        assert context["target"]["workflowId"] == target.workflow_id

        payload = await tools.read_target_artifact(
            remediation_workflow_id=remediation.workflow_id,
            artifact_ref={"artifact_id": target_artifact.artifact_id},
        )
        assert payload == b"target artifact"

        logs = await tools.read_target_logs(
            remediation_workflow_id=remediation.workflow_id,
            task_run_id="tr_allowed",
            stream="merged",
            tail_lines=999999,
        )
        assert logs.lines == ("line 1", "line 2")
        assert reader.calls == [
            {
                "task_run_id": "tr_allowed",
                "stream": "merged",
                "cursor": None,
                "tail_lines": 100,
            }
        ]
        assert context_reads == 1

        with pytest.raises(RemediationEvidenceToolError, match="not listed"):
            await tools.read_target_artifact(
                remediation_workflow_id=remediation.workflow_id,
                artifact_ref="art_not_in_context",
            )
        with pytest.raises(RemediationEvidenceToolError, match="not listed"):
            await tools.read_target_logs(
                remediation_workflow_id=remediation.workflow_id,
                task_run_id="tr_blocked",
                stream="stdout",
            )


@pytest.mark.asyncio
async def test_remediation_evidence_tools_gate_live_follow_by_context_policy(
    tmp_path, mock_client_adapter
):
    async with temporal_db(tmp_path) as session:
        owner_id = uuid4()
        mock_client_adapter.start_workflow.side_effect = [
            SimpleNamespace(run_id="target-run"),
            SimpleNamespace(run_id="remediation-run"),
        ]
        execution_service = TemporalExecutionService(
            session, client_adapter=mock_client_adapter
        )
        artifact_service = TemporalArtifactService(
            TemporalArtifactRepository(session),
            store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
        )
        target = await execution_service.create_execution(
            workflow_type="MoonMind.Run",
            owner_id=owner_id,
            title="Target",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters={},
            idempotency_key=None,
        )
        remediation = await execution_service.create_execution(
            workflow_type="MoonMind.Run",
            owner_id=owner_id,
            title="Remediate target",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters={
                "task": {
                    "remediation": {
                        "target": {
                            "workflowId": target.workflow_id,
                            "taskRunIds": ["tr_live"],
                        },
                        "mode": "snapshot_then_follow",
                    },
                }
            },
            idempotency_key=None,
        )
        builder = RemediationContextBuilder(
            session=session,
            artifact_service=artifact_service,
        )
        result = await builder.build_context(
            remediation_workflow_id=remediation.workflow_id
        )

        follower = RecordingLiveFollower()
        recorded_cursors = []

        async def record_cursor(workflow_id, cursor):
            recorded_cursors.append((workflow_id, cursor))

        tools = RemediationEvidenceToolService(
            session=session,
            artifact_service=artifact_service,
            live_follower=follower,
            cursor_recorder=record_cursor,
        )
        with pytest.raises(RemediationEvidenceToolError, match="not supported"):
            await tools.follow_target_logs(
                remediation_workflow_id=remediation.workflow_id,
                task_run_id="tr_live",
            )

        context = dict(result.payload)
        context["liveFollow"] = {
            "mode": "snapshot_then_follow",
            "supported": True,
            "taskRunId": "tr_live",
            "resumeCursor": {"sequence": 42},
        }
        live_context_artifact, _upload = await artifact_service.create(
            principal="service:test",
            content_type="application/json",
            size_bytes=len(json.dumps(context).encode("utf-8")),
            link=ExecutionRef(
                namespace="default",
                workflow_id=remediation.workflow_id,
                run_id=remediation.run_id,
                link_type="remediation.context",
                label="reports/remediation_context.json",
            ),
            metadata_json={
                "artifact_type": "remediation.context",
                "name": "reports/remediation_context.json",
            },
        )
        live_context_artifact = await artifact_service.write_complete(
            artifact_id=live_context_artifact.artifact_id,
            principal="service:test",
            payload=json.dumps(context).encode("utf-8"),
            content_type="application/json",
        )
        result.link.context_artifact_ref = live_context_artifact.artifact_id
        await session.commit()

        live = await tools.follow_target_logs(
            remediation_workflow_id=remediation.workflow_id,
            task_run_id="tr_live",
        )
        assert live.events[0].text == "live line"
        assert follower.calls == [{"task_run_id": "tr_live", "from_sequence": 42}]
        assert recorded_cursors == [
            (remediation.workflow_id, {"sequence": 43}),
        ]

        with pytest.raises(RemediationEvidenceToolError, match="not listed"):
            await tools.follow_target_logs(
                remediation_workflow_id=remediation.workflow_id,
                task_run_id="tr_other",
            )


@pytest.mark.asyncio
async def test_remediation_evidence_tools_prepare_action_request_rereads_target_health(
    tmp_path, mock_client_adapter
):
    async with temporal_db(tmp_path) as session:
        owner_id = uuid4()
        mock_client_adapter.start_workflow.side_effect = [
            SimpleNamespace(run_id="target-run"),
            SimpleNamespace(run_id="remediation-run"),
        ]
        execution_service = TemporalExecutionService(
            session, client_adapter=mock_client_adapter
        )
        artifact_service = TemporalArtifactService(
            TemporalArtifactRepository(session),
            store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
        )
        target = await execution_service.create_execution(
            workflow_type="MoonMind.Run",
            owner_id=owner_id,
            title="Target before action",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters={},
            idempotency_key=None,
            summary="Initial target summary",
        )
        remediation = await execution_service.create_execution(
            workflow_type="MoonMind.Run",
            owner_id=owner_id,
            title="Remediate target",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters={
                "task": {
                    "remediation": {
                        "target": {
                            "workflowId": target.workflow_id,
                            "taskRunIds": ["tr_action"],
                        },
                        "actionPolicyRef": "admin_healer_default",
                    },
                }
            },
            idempotency_key=None,
        )
        builder = RemediationContextBuilder(
            session=session,
            artifact_service=artifact_service,
        )
        await builder.build_context(remediation_workflow_id=remediation.workflow_id)

        target_source = await session.get(
            TemporalExecutionCanonicalRecord, target.workflow_id
        )
        assert target_source is not None
        target_source.run_id = "target-run-after-context"
        target_source.state = MoonMindWorkflowState.EXECUTING
        target_source.memo = {
            **target_source.memo,
            "summary": "Fresh target summary",
        }
        await session.commit()

        tools = RemediationEvidenceToolService(
            session=session,
            artifact_service=artifact_service,
        )
        preparation = await tools.prepare_action_request(
            remediation_workflow_id=remediation.workflow_id,
            action_kind="terminate_session",
        )

        assert preparation.remediation_workflow_id == remediation.workflow_id
        assert preparation.action_kind == "terminate_session"
        assert preparation.context_target["runId"] == "target-run"
        assert preparation.target.workflow_id == target.workflow_id
        assert preparation.target.pinned_run_id == "target-run"
        assert preparation.target.current_run_id == "target-run-after-context"
        assert preparation.target.target_run_changed is True
        assert preparation.target.state == "executing"
        assert preparation.target.summary == "Fresh target summary"

        with pytest.raises(RemediationEvidenceToolError, match="actionKind is required"):
            await tools.prepare_action_request(
                remediation_workflow_id=remediation.workflow_id,
                action_kind=" ",
            )


@pytest.mark.asyncio
async def test_remediation_context_builder_rejects_missing_target_record(
    tmp_path, mock_client_adapter
):
    async with temporal_db(tmp_path) as session:
        execution_service = TemporalExecutionService(
            session, client_adapter=mock_client_adapter
        )
        artifact_service = TemporalArtifactService(
            TemporalArtifactRepository(session),
            store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
        )
        remediation = await execution_service.create_execution(
            workflow_type="MoonMind.Run",
            owner_id=uuid4(),
            title="Remediation without valid target",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters={},
            idempotency_key=None,
        )
        session.add(
            TemporalExecutionRemediationLink(
                remediation_workflow_id=remediation.workflow_id,
                remediation_run_id=remediation.run_id,
                target_workflow_id="mm:missing",
                target_run_id="missing-run",
                mode="snapshot",
                authority_mode="observe_only",
                status="created",
            )
        )
        await session.commit()

        builder = RemediationContextBuilder(
            session=session,
            artifact_service=artifact_service,
        )
        with pytest.raises(RemediationContextError, match="Target execution not found"):
            await builder.build_context(
                remediation_workflow_id=remediation.workflow_id,
                principal="service:remediation-context",
            )


@pytest.mark.asyncio
async def test_remediation_action_authority_enforces_authority_modes(
    tmp_path, mock_client_adapter
):
    async with temporal_db(tmp_path) as session:
        _target, remediation = await _create_target_and_remediation(
            session,
            mock_client_adapter,
            authority_mode="observe_only",
        )
        service = RemediationActionAuthorityService(session=session)

        dry_run = await service.evaluate_action_request(
            remediation_workflow_id=remediation.workflow_id,
            action_kind="restart_worker",
            parameters={"reason": "diagnose only"},
            dry_run=True,
            idempotency_key="observe-dry-run",
            requesting_principal="user:operator",
            permissions=_admin_permissions(),
            security_profile=_admin_profile(),
        )
        assert dry_run.decision == "dry_run_only"
        assert dry_run.executable is False

        denied = await service.evaluate_action_request(
            remediation_workflow_id=remediation.workflow_id,
            action_kind="restart_worker",
            parameters={"reason": "side effect"},
            dry_run=False,
            idempotency_key="observe-execute",
            requesting_principal="user:operator",
            permissions=_admin_permissions(),
            security_profile=_admin_profile(),
        )
        assert denied.decision == "denied"
        assert denied.reason == "observe_only_rejects_side_effects"
        assert denied.executable is False


@pytest.mark.asyncio
async def test_remediation_action_authority_requires_approval_for_gated_mode(
    tmp_path, mock_client_adapter
):
    async with temporal_db(tmp_path) as session:
        _target, remediation = await _create_target_and_remediation(
            session,
            mock_client_adapter,
            authority_mode="approval_gated",
        )
        service = RemediationActionAuthorityService(session=session)

        pending = await service.evaluate_action_request(
            remediation_workflow_id=remediation.workflow_id,
            action_kind="restart_worker",
            parameters={},
            dry_run=False,
            idempotency_key="gated-pending",
            requesting_principal="user:operator",
            permissions=_admin_permissions(),
            security_profile=_admin_profile(),
        )
        assert pending.decision == "approval_required"
        assert pending.reason == "approval_gated_requires_approval"
        assert pending.executable is False

        approved = await service.evaluate_action_request(
            remediation_workflow_id=remediation.workflow_id,
            action_kind="restart_worker",
            parameters={},
            dry_run=False,
            idempotency_key="gated-approved",
            requesting_principal="user:operator",
            permissions=_admin_permissions(can_approve_high_risk=True),
            security_profile=_admin_profile(),
            approval_ref="approval://ops/1",
        )
        assert approved.decision == "allowed"
        assert approved.executable is True
        assert approved.audit["requestingPrincipal"] == "user:operator"
        assert approved.audit["executionPrincipal"] == "service:admin-healer"


@pytest.mark.asyncio
async def test_remediation_action_authority_enforces_profile_permissions_and_risk(
    tmp_path, mock_client_adapter
):
    async with temporal_db(tmp_path) as session:
        _target, remediation = await _create_target_and_remediation(
            session,
            mock_client_adapter,
            authority_mode="admin_auto",
        )
        service = RemediationActionAuthorityService(session=session)

        view_only = await service.evaluate_action_request(
            remediation_workflow_id=remediation.workflow_id,
            action_kind="restart_worker",
            parameters={},
            dry_run=False,
            idempotency_key="view-only",
            requesting_principal="user:viewer",
            permissions=RemediationPermissionSet(can_view_target=True),
            security_profile=_admin_profile(),
        )
        assert view_only.decision == "denied"
        assert view_only.reason == "admin_profile_permission_required"

        disabled_profile = await service.evaluate_action_request(
            remediation_workflow_id=remediation.workflow_id,
            action_kind="restart_worker",
            parameters={},
            dry_run=False,
            idempotency_key="disabled-profile",
            requesting_principal="user:operator",
            permissions=_admin_permissions(),
            security_profile=_admin_profile(enabled=False),
        )
        assert disabled_profile.decision == "denied"
        assert disabled_profile.reason == "security_profile_disabled"

        allowed = await service.evaluate_action_request(
            remediation_workflow_id=remediation.workflow_id,
            action_kind="restart_worker",
            parameters={},
            dry_run=False,
            idempotency_key="medium-allowed",
            requesting_principal="user:operator",
            permissions=_admin_permissions(),
            security_profile=_admin_profile(),
        )
        assert allowed.decision == "allowed"
        assert allowed.risk == "medium"
        assert allowed.executable is True

        high_risk = await service.evaluate_action_request(
            remediation_workflow_id=remediation.workflow_id,
            action_kind="terminate_session",
            parameters={},
            dry_run=False,
            idempotency_key="high-risk",
            requesting_principal="user:operator",
            permissions=_admin_permissions(),
            security_profile=_admin_profile(),
        )
        assert high_risk.decision == "approval_required"
        assert high_risk.reason == "high_risk_requires_approval"
        assert high_risk.executable is False


@pytest.mark.asyncio
async def test_remediation_action_authority_redacts_audits_and_deduplicates(
    tmp_path, mock_client_adapter
):
    async with temporal_db(tmp_path) as session:
        _target, remediation = await _create_target_and_remediation(
            session,
            mock_client_adapter,
            authority_mode="admin_auto",
        )
        service = RemediationActionAuthorityService(session=session)

        result = await service.evaluate_action_request(
            remediation_workflow_id=remediation.workflow_id,
            action_kind="restart_worker",
            parameters={
                "token": "raw-secret-token",
                "path": "/work/agent_jobs/mm:secret/repo/.env",
                "note": "Authorization: Bearer raw-secret-token",
            },
            dry_run=False,
            idempotency_key="dedupe-redact",
            requesting_principal="user:operator",
            permissions=_admin_permissions(),
            security_profile=_admin_profile(),
        )
        duplicate = await service.evaluate_action_request(
            remediation_workflow_id=remediation.workflow_id,
            action_kind="restart_worker",
            parameters={"token": "different-secret"},
            dry_run=False,
            idempotency_key="dedupe-redact",
            requesting_principal="user:operator",
            permissions=_admin_permissions(),
            security_profile=_admin_profile(),
        )

        assert duplicate is result
        serialized = json.dumps(result.to_dict(), sort_keys=True)
        assert "raw-secret-token" not in serialized
        assert "/work/agent_jobs" not in serialized
        assert "Bearer" not in serialized
        assert result.audit["requestingPrincipal"] == "user:operator"
        assert result.audit["executionPrincipal"] == "service:admin-healer"


@pytest.mark.asyncio
async def test_remediation_action_authority_denies_raw_access_and_unknown_targets(
    tmp_path, mock_client_adapter
):
    async with temporal_db(tmp_path) as session:
        _target, remediation = await _create_target_and_remediation(
            session,
            mock_client_adapter,
            authority_mode="admin_auto",
        )
        service = RemediationActionAuthorityService(session=session)

        raw_access = await service.evaluate_action_request(
            remediation_workflow_id=remediation.workflow_id,
            action_kind="raw_host_shell",
            parameters={"command": "docker ps"},
            dry_run=False,
            idempotency_key="raw-shell",
            requesting_principal="user:operator",
            permissions=_admin_permissions(),
            security_profile=_admin_profile(),
        )
        assert raw_access.decision == "denied"
        assert raw_access.reason == "raw_access_action_denied"

        missing = await service.evaluate_action_request(
            remediation_workflow_id="mm:missing-remediation",
            action_kind="restart_worker",
            parameters={},
            dry_run=False,
            idempotency_key="missing-target",
            requesting_principal="user:operator",
            permissions=_admin_permissions(),
            security_profile=_admin_profile(),
        )
        assert missing.decision == "denied"
        assert missing.reason == "remediation_link_not_found"
        assert "missing-remediation" not in missing.audit["summary"]


@pytest.mark.asyncio
async def test_remediation_action_authority_uses_prepared_action_context(
    tmp_path, mock_client_adapter
):
    async with temporal_db(tmp_path) as session:
        target, remediation = await _create_target_and_remediation(
            session,
            mock_client_adapter,
            authority_mode="admin_auto",
        )
        artifact_service = TemporalArtifactService(
            TemporalArtifactRepository(session),
            store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
        )
        builder = RemediationContextBuilder(
            session=session,
            artifact_service=artifact_service,
        )
        await builder.build_context(remediation_workflow_id=remediation.workflow_id)
        tools = RemediationEvidenceToolService(
            session=session,
            artifact_service=artifact_service,
        )
        preparation = await tools.prepare_action_request(
            remediation_workflow_id=remediation.workflow_id,
            action_kind="restart_worker",
        )

        service = RemediationActionAuthorityService(session=session)
        decision = await service.evaluate_action_request(
            remediation_workflow_id=preparation.remediation_workflow_id,
            action_kind=preparation.action_kind,
            parameters={"targetState": preparation.target.state},
            dry_run=False,
            idempotency_key="prepared-action",
            requesting_principal="workflow:remediator",
            permissions=_admin_permissions(),
            security_profile=_admin_profile(),
        )

        assert preparation.target.workflow_id == target.workflow_id
        assert decision.decision == "allowed"
        assert decision.target_workflow_id == target.workflow_id
