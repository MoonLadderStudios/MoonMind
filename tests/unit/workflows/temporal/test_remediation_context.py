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
    TemporalExecutionOwnerType,
    TemporalExecutionProjectionSourceMode,
    TemporalExecutionRecord,
    TemporalExecutionRemediationLink,
    TemporalWorkflowType,
)
from moonmind.config.settings import settings
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


def test_omnigent_evidence_index_is_complete_bounded_and_explicitly_degraded():
    index = RemediationContextBuilder._omnigent_evidence_index(
        {
            "generatedAt": "2026-07-26T12:00:00Z",
            "bridgeEventPageRefs": ["art_bridge_page"],
            "hostLeaseRef": {"artifact_id": "art_host_lease"},
        }
    )

    by_class = {entry["class"]: entry for entry in index}
    assert by_class["bridge_events"] == {
        "class": "bridge_events",
        "status": "available",
        "bounded": True,
        "contentsIncluded": False,
        "refs": [
            {
                "artifact_id": "art_bridge_page",
                "kind": "bridgeEventPageRefs",
            }
        ],
        "observedAt": "2026-07-26T12:00:00Z",
        "freshness": "source_reported",
    }
    assert by_class["provider_and_leases"]["status"] == "available"
    assert by_class["provider_and_leases"]["refs"][0]["artifact_id"] == (
        "art_host_lease"
    )
    assert by_class["prior_remediation"]["status"] == "missing"
    assert by_class["prior_remediation"]["bounded"] is True
    assert by_class["prior_remediation"]["freshness"] == "source_reported"
    assert by_class["prior_remediation"]["degradedReason"] == (
        "historical evidence was not recorded"
    )


def test_context_aggregate_is_degraded_when_omnigent_classes_are_missing():
    target_record = SimpleNamespace(
        workflow_id="target",
        run_id="target-run",
        state=MoonMindWorkflowState.EXECUTING,
        close_status=None,
        memo={},
        parameters={},
        artifact_refs=[],
    )
    remediation_record = SimpleNamespace(
        workflow_id="remediation",
        run_id="remediation-run",
        state=MoonMindWorkflowState.EXECUTING,
        close_status=None,
        memo={},
        parameters={
            "workflow": {
                "remediation": {
                    "target": {"agentRunIds": ["agent-run"]},
                }
            }
        },
        artifact_refs=[],
    )
    link = SimpleNamespace(
        remediation_workflow_id="remediation",
        remediation_run_id="remediation-run",
        target_workflow_id="target",
        target_run_id="target-run",
        authority_mode="observe_only",
        mode="snapshot",
    )

    payload = RemediationContextBuilder(
        session=object(), artifact_service=object()
    )._build_payload(
        link=link,
        remediation_record=remediation_record,
        target_record=target_record,
    )

    evidence = payload["evidence"]
    assert evidence["evidenceDegraded"] is True
    assert "execution_and_steps" in evidence["unavailableEvidenceClasses"]
    assert "prior_remediation" in evidence["unavailableEvidenceClasses"]


def _valid_user_workflow_parameters() -> dict[str, object]:
    return {"workflow": {"instructions": "Test workflow fixture."}}


async def _create_target_and_remediation(
    session: AsyncSession,
    mock_client_adapter,
    *,
    owner_id=None,
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
        workflow_type="MoonMind.UserWorkflow",
        owner_id=owner,
        title="Target",
        input_artifact_ref=None,
        plan_artifact_ref=None,
        manifest_artifact_ref=None,
        failure_policy=None,
        initial_parameters=_valid_user_workflow_parameters(),
        idempotency_key=None,
    )
    remediation = await execution_service.create_execution(
        workflow_type="MoonMind.UserWorkflow",
        owner_id=owner,
        title="Remediate target",
        input_artifact_ref=None,
        plan_artifact_ref=None,
        manifest_artifact_ref=None,
        failure_policy=None,
        initial_parameters={
            "workflow": {
                "remediation": {"target": {"workflowId": target.workflow_id}}
            }
        },
        idempotency_key=None,
    )
    return target, remediation


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
        settings.workflow.temporal_artifact_backend = original_artifact_backend
        settings.workflow.temporal_artifact_root = original_artifact_root


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
            workflow_type="MoonMind.UserWorkflow",
            owner_id=owner_id,
            title="Target needing help",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters={
                **_valid_user_workflow_parameters(),
                "stepLedger": {
                    "steps": [
                        {
                            "logicalStepId": "run-tests",
                            "attempt": 1,
                            "agentRunId": "tr_selected",
                            "checkpointRef": "artifact://checkpoints/run-tests",
                            "checkpointDigest": "sha256:runtests",
                        }
                    ]
                },
                "agentRuns": [
                    {"agentRunId": f"tr_{index:02d}"} for index in range(25)
                ],
            },
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
                            "stepSelectors": [
                                {
                                    "logicalStepId": "run-tests",
                                    "attempt": 1,
                                    "agentRunId": "tr_selected",
                                    "checkpointRef": "artifact://checkpoints/run-tests",
                                    "checkpointDigest": "sha256:runtests",
                                    "checkpointBoundary": "after_execution",
                                    "ignored": "not copied",
                                }
                            ],
                            "agentRunIds": [
                                f"tr_{index:02d}" for index in range(25)
                            ],
                        },
                        "evidencePolicy": {
                            "includeStepLedger": True,
                            "apiKey": "raw-api-key",
                            "nested": {
                                "enabled": True,
                                "password": "raw-password",
                                "authorization": "Bearer raw-secret",
                            },
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
                "agentRunId": "tr_selected",
                "checkpointRef": "artifact://checkpoints/run-tests",
                "checkpointDigest": "sha256:runtests",
                "checkpointBoundary": "after_execution",
            }
        ]
        assert len(payload["evidence"]["agentRuns"]) == 20
        assert payload["evidence"]["agentRuns"][0] == {"agentRunId": "tr_00"}
        assert payload["evidence"]["targetArtifactRefs"] == [
            {"artifact_id": "art_target_summary"},
            {"artifact_id": "art_input_ref", "kind": "input"},
        ]
        # A remediation only observes its target: no action, approval, or
        # lock policy, and no live follow, reaches the agent.
        assert payload["policies"] == {
            "authorityMode": "observe_only",
            "evidencePolicy": {"includeStepLedger": True, "nested": {"enabled": True}},
        }
        assert "liveFollow" not in payload
        assert payload["boundedness"] == {
            "maxAgentRunIds": 20,
            "rawLogBodiesIncluded": False,
            "artifactContentsIncluded": False,
        }

        serialized = json.dumps(payload)
        assert str(tmp_path) not in serialized
        assert "storage_key" not in serialized
        assert "presigned" not in serialized.lower()
        assert "raw-secret" not in serialized
        assert "raw-password" not in serialized
        assert "raw-api-key" not in serialized
        assert "password" not in serialized.lower()
        assert "token=" not in serialized.lower()


@pytest.mark.asyncio
async def test_scheduled_failed_run_remediation_context_embeds_failure_evidence(
    tmp_path, mock_client_adapter
):
    """A schedule-started run has only a projection row and stays remediable."""

    async with temporal_db(tmp_path) as session:
        target_id = "mm:schedule-1-2026-10-18T03:35:00Z"
        artifact_service = TemporalArtifactService(TemporalArtifactRepository(session))
        manifest, _upload = await artifact_service.create(
            principal=f"workflow:{target_id}",
            content_type="application/json",
            metadata_json={"name": "reports/recovery_manifest.json"},
        )
        await artifact_service.write_complete(
            artifact_id=manifest.artifact_id,
            principal=f"workflow:{target_id}",
            payload=json.dumps(
                {"blockedReason": "no_failed_step_execution_to_resume"}
            ).encode("utf-8"),
            content_type="application/json",
        )
        session.add(
            TemporalExecutionRecord(
                workflow_id=target_id,
                run_id="scheduled-run",
                owner_id="system",
                owner_type=TemporalExecutionOwnerType.SYSTEM,
                source_mode=TemporalExecutionProjectionSourceMode.TEMPORAL_AUTHORITATIVE,
                workflow_type=TemporalWorkflowType.USER_WORKFLOW,
                state=MoonMindWorkflowState.FAILED,
                entry="run",
                memo={"title": "Scheduled search"},
                search_attributes={},
                artifact_refs=[],
                parameters={
                    "repository": "MoonLadderStudios/Tactics",
                    "workflow": {
                        "steps": [
                            {
                                "id": "implement",
                                "title": "Implement",
                                "instructions": "Implement the issue.",
                            }
                        ]
                    },
                },
                finish_summary_json={
                    "failure": {
                        "category": "execution_error",
                        "message": "ValueError: PR head no longer matches",
                    },
                    "lastStep": {"id": "implement"},
                    "recoveryManifest": {"manifestRef": manifest.artifact_id},
                    "publishContext": {"large": "x" * 10_000},
                },
            )
        )
        await session.commit()
        ledger_handle = SimpleNamespace(
            query=AsyncMock(
                return_value={
                    "steps": [
                        {
                            "logicalStepId": "implement",
                            "order": 1,
                            "title": "Implement",
                            "status": "failed",
                            "lastError": "ValueError: PR head no longer matches",
                            "artifacts": {"runtimeDiagnostics": "art_diag"},
                        }
                    ]
                }
            )
        )
        mock_client_adapter.get_workflow_handle = AsyncMock(return_value=ledger_handle)
        mock_client_adapter.start_workflow.side_effect = [
            SimpleNamespace(run_id="remediation-run"),
        ]
        execution_service = TemporalExecutionService(
            session, client_adapter=mock_client_adapter
        )

        remediation = await execution_service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=None,
            title="Remediate scheduled run",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters={
                "workflow": {
                    "instructions": "Find why the scheduled run failed.",
                    "remediation": {
                        "target": {"workflowId": target_id, "runId": "scheduled-run"},
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
        assert link.target_run_id == "scheduled-run"
        _artifact, body = await artifact_service.read(
            artifact_id=link.context_artifact_ref,
            principal="service:remediation-context",
            admitted_principal=f"workflow:{remediation.workflow_id}",
        )
        evidence = json.loads(body)["failureEvidence"]
        assert evidence["failure"]["message"] == "ValueError: PR head no longer matches"
        assert "publishContext" not in evidence
        assert evidence["authoredWork"] == {
            "repository": "MoonLadderStudios/Tactics",
            "steps": [
                {
                    "id": "implement",
                    "title": "Implement",
                    "instructions": "Implement the issue.",
                }
            ],
        }
        assert evidence["steps"] == [
            {
                "logicalStepId": "implement",
                "order": 1,
                "title": "Implement",
                "status": "failed",
                "lastError": "ValueError: PR head no longer matches",
                "artifacts": {"runtimeDiagnostics": "art_diag"},
            }
        ]
        assert evidence["documents"] == [
            {
                "kind": "recovery_manifest",
                "artifactRef": manifest.artifact_id,
                "content": {"blockedReason": "no_failed_step_execution_to_resume"},
            }
        ]
        assert evidence["unavailable"] == []
        ledger_handle.query.assert_awaited_once_with("get_step_ledger")


@pytest.mark.asyncio
async def test_remediation_context_builder_enriches_agent_run_evidence(
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
            workflow_type="MoonMind.UserWorkflow",
            owner_id=owner_id,
            title="Target with evidence",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters=_valid_user_workflow_parameters(),
            idempotency_key=None,
            summary="Target summary",
        )
        target_source = await session.get(
            TemporalExecutionCanonicalRecord, target.workflow_id
        )
        assert target_source is not None
        target_source.state = MoonMindWorkflowState.EXECUTING
        target_source.memo = {
            **target_source.memo,
            "remediationEvidence": {
                "selectedSteps": [
                    {
                        "logicalStepId": "run-tests",
                        "attempt": 2,
                        "agentRunId": "tr_live",
                        "status": "failed",
                        "summary": "Integration tests failed",
                        "artifactRefs": [
                            {"artifact_id": "art_step_summary", "kind": "step.summary"}
                        ],
                    }
                ],
                "agentRuns": [
                    {
                        "agentRunId": "tr_live",
                        "observabilitySummaryRef": {"artifact_id": "art_obs"},
                        "stdoutRef": {"artifact_id": "art_stdout"},
                        "stderrRef": {"artifact_id": "art_stderr"},
                        "mergedLogsRef": {"artifact_id": "art_merged"},
                        "diagnosticsRef": {"artifact_id": "art_diag"},
                        "providerSnapshotRef": {"artifact_id": "art_provider"},
                        "continuityRefs": [
                            {
                                "artifact_id": "art_session_summary",
                                "kind": "session.summary",
                            }
                        ],
                    }
                ],
                "diagnosisHints": ["Prefer diagnostics before merged logs"],
            },
        }
        await session.commit()

        remediation = await execution_service.create_execution(
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
                            "stepSelectors": [
                                {
                                    "logicalStepId": "run-tests",
                                    "attempt": 2,
                                    "agentRunId": "tr_live",
                                }
                            ],
                            "agentRunIds": ["tr_live"],
                        },
                    }
                }
            },
            idempotency_key=None,
        )

        result = await RemediationContextBuilder(
            session=session,
            artifact_service=artifact_service,
        ).build_context(remediation_workflow_id=remediation.workflow_id)

        assert result.payload["selectedSteps"] == [
            {
                "logicalStepId": "run-tests",
                "attempt": 2,
                "agentRunId": "tr_live",
                "status": "failed",
                "summary": "Integration tests failed",
                "artifactRefs": [
                    {"artifact_id": "art_step_summary", "kind": "step.summary"}
                ],
            }
        ]
        assert result.payload["evidence"]["agentRuns"] == [
            {
                "agentRunId": "tr_live",
                "observabilitySummaryRef": {"artifact_id": "art_obs"},
                "stdoutRef": {"artifact_id": "art_stdout"},
                "stderrRef": {"artifact_id": "art_stderr"},
                "mergedLogsRef": {"artifact_id": "art_merged"},
                "diagnosticsRef": {"artifact_id": "art_diag"},
                "providerSnapshotRef": {"artifact_id": "art_provider"},
                "continuityRefs": [
                    {"artifact_id": "art_session_summary", "kind": "session.summary"}
                ],
            }
        ]
        assert result.payload["evidence"]["availability"] == [
            {"class": "stdout", "status": "available"},
            {"class": "stderr", "status": "available"},
            {"class": "merged_logs", "status": "available"},
            {"class": "diagnostics", "status": "available"},
            {"class": "provider_snapshot", "status": "available"},
            {"class": "continuity", "status": "available"},
        ]
        assert result.payload["evidence"]["evidenceDegraded"] is True
        assert result.payload["evidence"]["diagnosisHints"] == [
            "Prefer diagnostics before merged logs"
        ]


@pytest.mark.asyncio
async def test_remediation_context_builder_matches_selected_step_by_full_identity(
    tmp_path, mock_client_adapter
):
    async with temporal_db(tmp_path) as session:
        target, remediation = await _create_target_and_remediation(
            session,
            mock_client_adapter,
        )
        target_source = await session.get(
            TemporalExecutionCanonicalRecord, target.workflow_id
        )
        assert target_source is not None
        target_source.memo = {
            **target_source.memo,
            "remediationEvidence": {
                "selectedSteps": [
                    {
                        "logicalStepId": "plan",
                        "attempt": 1,
                        "agentRunId": "tr_shared",
                        "status": "succeeded",
                        "summary": "Plan passed",
                        "artifactRefs": [
                            {"artifact_id": "art_plan", "kind": "step.summary"}
                        ],
                    },
                    {
                        "logicalStepId": "implement",
                        "attempt": 2,
                        "agentRunId": "tr_shared",
                        "status": "failed",
                        "summary": "Implementation failed",
                        "artifactRefs": [
                            {"artifact_id": "art_implement", "kind": "step.summary"}
                        ],
                    },
                ]
            },
        }
        remediation_source = await session.get(
            TemporalExecutionCanonicalRecord, remediation.workflow_id
        )
        assert remediation_source is not None
        remediation_source.parameters = {
            **remediation_source.parameters,
            "workflow": {
                "remediation": {
                    "target": {
                        "workflowId": target.workflow_id,
                        "stepSelectors": [
                            {
                                "logicalStepId": "implement",
                                "attempt": 2,
                                "agentRunId": "tr_shared",
                            }
                        ],
                        "agentRunIds": ["tr_shared"],
                    }
                }
            },
        }
        await session.commit()

        artifact_service = TemporalArtifactService(
            TemporalArtifactRepository(session),
            store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
        )
        result = await RemediationContextBuilder(
            session=session,
            artifact_service=artifact_service,
        ).build_context(remediation_workflow_id=remediation.workflow_id)

        assert result.payload["selectedSteps"] == [
            {
                "logicalStepId": "implement",
                "attempt": 2,
                "agentRunId": "tr_shared",
                "status": "failed",
                "summary": "Implementation failed",
                "artifactRefs": [
                    {"artifact_id": "art_implement", "kind": "step.summary"}
                ],
            }
        ]


@pytest.mark.asyncio
async def test_remediation_context_builder_records_historical_degraded_evidence(
    tmp_path, mock_client_adapter
):
    async with temporal_db(tmp_path) as session:
        target, remediation = await _create_target_and_remediation(
            session,
            mock_client_adapter,
        )
        target_source = await session.get(
            TemporalExecutionCanonicalRecord, target.workflow_id
        )
        assert target_source is not None
        target_source.state = MoonMindWorkflowState.FAILED
        target_source.memo = {
            **target_source.memo,
            "remediationEvidence": {
                "agentRuns": [
                    {
                        "agentRunId": "tr_history",
                        "mergedLogsRef": {"artifact_id": "art_merged"},
                    }
                ]
            },
        }
        remediation_source = await session.get(
            TemporalExecutionCanonicalRecord, remediation.workflow_id
        )
        assert remediation_source is not None
        remediation_source.parameters = {
            **remediation_source.parameters,
            "workflow": {
                "remediation": {
                    "target": {
                        "workflowId": target.workflow_id,
                        "agentRunIds": ["tr_history"],
                    },
                }
            },
        }
        await session.commit()

        artifact_service = TemporalArtifactService(
            TemporalArtifactRepository(session),
            store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
        )
        result = await RemediationContextBuilder(
            session=session,
            artifact_service=artifact_service,
        ).build_context(remediation_workflow_id=remediation.workflow_id)

        assert result.payload["evidence"]["agentRuns"] == [
            {
                "agentRunId": "tr_history",
                "mergedLogsRef": {"artifact_id": "art_merged"},
            }
        ]
        assert result.payload["evidence"]["evidenceDegraded"] is True
        assert result.payload["evidence"]["unavailableEvidenceClasses"][:5] == [
            "stdout",
            "stderr",
            "diagnostics",
            "provider_snapshot",
            "continuity",
        ]
        assert result.payload["evidence"]["availability"] == [
            {
                "class": "stdout",
                "status": "missing",
                "fallback": "merged_logs",
            },
            {
                "class": "stderr",
                "status": "missing",
                "fallback": "merged_logs",
            },
            {"class": "merged_logs", "status": "available"},
            {
                "class": "diagnostics",
                "status": "missing",
                "fallback": "merged_logs",
            },
            {
                "class": "provider_snapshot",
                "status": "missing",
                "fallback": "merged_logs",
            },
            {
                "class": "continuity",
                "status": "missing",
                "fallback": "merged_logs",
            },
        ]


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
            workflow_type="MoonMind.UserWorkflow",
            owner_id=uuid4(),
            title="Ordinary run",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters=_valid_user_workflow_parameters(),
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
            workflow_type="MoonMind.UserWorkflow",
            owner_id=uuid4(),
            title="Remediation without valid target",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters=_valid_user_workflow_parameters(),
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
