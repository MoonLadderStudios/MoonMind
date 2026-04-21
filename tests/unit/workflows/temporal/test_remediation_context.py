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
    TemporalArtifactLink,
    TemporalArtifactStatus,
    TemporalExecutionCanonicalRecord,
    TemporalExecutionRemediationLink,
)
from moonmind.workflows.temporal import (
    LocalTemporalArtifactStore,
    TemporalArtifactRepository,
    TemporalArtifactService,
)
from moonmind.workflows.temporal.remediation_context import (
    RemediationContextBuilder,
    RemediationContextError,
)
from moonmind.workflows.temporal.service import TemporalExecutionService


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
        target_source.artifact_refs = ["art_target_summary", "artifact://legacy/ref"]
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
                        },
                        "actionPolicyRef": "admin_healer_default",
                        "approvalPolicy": {
                            "mode": "risk_gated",
                            "autoAllowedRisk": "medium",
                        },
                        "lockPolicy": {
                            "scope": "target_execution",
                            "mode": "exclusive",
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
            {"ref": "artifact://legacy/ref"},
            {"artifact_id": "art_input_ref", "kind": "input"},
        ]
        assert payload["policies"]["authorityMode"] == "approval_gated"
        assert payload["policies"]["actionPolicyRef"] == "admin_healer_default"
        assert payload["policies"]["evidencePolicy"]["tailLines"] == 2000
        assert payload["policies"]["approvalPolicy"]["mode"] == "risk_gated"
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
