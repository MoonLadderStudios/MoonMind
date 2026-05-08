from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
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
    remediation_actions,
    remediation_tools,
)
from moonmind.workflows.temporal.remediation_context import (
    RemediationContextBuilder,
    RemediationContextError,
    RemediationLifecyclePublisher,
    build_corrected_instruction_retry_provenance,
    build_remediation_decision_log,
    build_remediation_audit_event,
    build_remediation_continue_as_new_state,
    build_remediation_final_summary,
    build_remediation_prevention_outcome,
    build_remediation_repair_decision,
    build_remediation_summary_block,
    build_target_remediation_linkage_summary,
    normalize_remediation_phase,
    normalize_remediation_resolution,
)
from moonmind.workflows.temporal.remediation_actions import (
    RemediationActionAuthorityService,
    RemediationMutationGuardPolicy,
    RemediationMutationGuardService,
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
        "allowed_action_kinds": ("workload.restart_helper_container", "session.terminate"),
        "enabled": True,
    }
    data.update(overrides)
    return RemediationSecurityProfile(**data)

CANONICAL_REMEDIATION_ACTIONS = {
    "execution.pause",
    "execution.resume",
    "execution.request_rerun_same_workflow",
    "execution.start_fresh_rerun",
    "execution.cancel",
    "execution.force_terminate",
    "session.interrupt_turn",
    "session.clear",
    "session.cancel",
    "session.terminate",
    "session.restart_container",
    "provider_profile.evict_stale_lease",
    "workload.restart_helper_container",
    "workload.reap_orphan_container",
}

def test_remediation_action_authority_lists_canonical_mm483_action_registry():
    service = RemediationActionAuthorityService(session=object())

    allowed = service.list_allowed_actions(
        permissions=_admin_permissions(),
        security_profile=_admin_profile(
            allowed_action_kinds=tuple(sorted(CANONICAL_REMEDIATION_ACTIONS))
        ),
    )

    assert {item["actionKind"] for item in allowed} == CANONICAL_REMEDIATION_ACTIONS
    for item in allowed:
        assert item["riskTier"] in {"low", "medium", "high"}
        assert item["targetType"]
        assert isinstance(item["inputMetadata"], dict)
        assert item["preconditions"]
        assert item["idempotency"]
        assert item["verificationRequired"] is True
        assert item["verificationHint"]
        assert item["auditPayloadShape"]

def test_remediation_action_authority_rejects_legacy_action_aliases():
    service = RemediationActionAuthorityService(session=object())

    allowed = service.list_allowed_actions(
        permissions=_admin_permissions(),
        security_profile=_admin_profile(
            allowed_action_kinds=(
                "restart_worker",
                "terminate_session",
                "workload.restart_helper_container",
                "session.terminate",
            )
        ),
    )

    assert {item["actionKind"] for item in allowed} == {
        "workload.restart_helper_container",
        "session.terminate",
    }

def test_remediation_action_authority_lists_policy_compatible_actions():
    service = RemediationActionAuthorityService(session=object())

    denied = service.list_allowed_actions(
        permissions=RemediationPermissionSet(can_view_target=True),
        security_profile=_admin_profile(),
    )
    assert denied == ()

    allowed = service.list_allowed_actions(
        permissions=_admin_permissions(),
        security_profile=_admin_profile(
            allowed_action_kinds=("workload.restart_helper_container",)
        ),
    )

    assert [item["actionKind"] for item in allowed] == ["workload.restart_helper_container"]
    assert allowed[0]["riskTier"] == "medium"
    assert allowed[0]["targetType"] == "workload_container"
    assert allowed[0]["inputMetadata"]["reason"]["required"] is False

    allowed[0]["inputMetadata"]["reason"]["required"] = True
    listed_again = service.list_allowed_actions(
        permissions=_admin_permissions(),
        security_profile=_admin_profile(
            allowed_action_kinds=("workload.restart_helper_container",)
        ),
    )
    assert listed_again[0]["inputMetadata"]["reason"]["required"] is False

def test_remediation_action_authority_does_not_advertise_raw_admin_actions():
    service = RemediationActionAuthorityService(session=object())

    allowed = service.list_allowed_actions(
        permissions=_admin_permissions(),
        security_profile=_admin_profile(
            allowed_action_kinds=(
                "workload.restart_helper_container",
                "session.terminate",
                "raw_host_shell",
                "raw_docker",
                "raw_sql",
                "storage_key_read",
                "secret_read",
                "redaction_bypass",
            )
        ),
    )

    action_kinds = {item["actionKind"] for item in allowed}
    assert action_kinds == {"workload.restart_helper_container", "session.terminate"}
    assert not action_kinds.intersection(
        {
            "raw_host_shell",
            "host_shell",
            "docker_daemon",
            "raw_docker",
            "sql_database",
            "raw_sql",
            "storage_key_read",
            "secret_read",
            "redaction_bypass",
        }
    )

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
            "status": "unavailable",
            "mode": "snapshot_then_follow",
            "supported": False,
            "taskRunId": "tr_00",
            "resumeCursor": None,
            "reason": "target is terminal",
            "fallbacks": [],
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
async def test_remediation_context_builder_enriches_task_run_evidence_and_live_follow(
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
            title="Target with evidence",
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
        target_source.state = MoonMindWorkflowState.EXECUTING
        target_source.memo = {
            **target_source.memo,
            "remediationEvidence": {
                "selectedSteps": [
                    {
                        "logicalStepId": "run-tests",
                        "attempt": 2,
                        "taskRunId": "tr_live",
                        "status": "failed",
                        "summary": "Integration tests failed",
                        "artifactRefs": [
                            {"artifact_id": "art_step_summary", "kind": "step.summary"}
                        ],
                    }
                ],
                "taskRuns": [
                    {
                        "taskRunId": "tr_live",
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
                        "liveFollowSupported": True,
                    }
                ],
                "diagnosisHints": ["Prefer diagnostics before merged logs"],
                "liveFollow": {"resumeCursor": {"sequence": 12}},
            },
        }
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
                            "stepSelectors": [
                                {
                                    "logicalStepId": "run-tests",
                                    "attempt": "2",
                                    "taskRunId": "tr_live",
                                }
                            ],
                            "taskRunIds": ["tr_live"],
                        },
                        "mode": "snapshot_then_follow",
                        "evidencePolicy": {
                            "tailLines": 250,
                            "allowLiveFollow": True,
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
                "taskRunId": "tr_live",
                "status": "failed",
                "summary": "Integration tests failed",
                "artifactRefs": [
                    {"artifact_id": "art_step_summary", "kind": "step.summary"}
                ],
            }
        ]
        assert result.payload["evidence"]["taskRuns"] == [
            {
                "taskRunId": "tr_live",
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
            {"class": "live_follow", "status": "available"},
        ]
        assert result.payload["evidence"]["evidenceDegraded"] is False
        assert result.payload["evidence"]["diagnosisHints"] == [
            "Prefer diagnostics before merged logs"
        ]
        assert result.payload["liveFollow"] == {
            "status": "active",
            "mode": "snapshot_then_follow",
            "supported": True,
            "taskRunId": "tr_live",
            "resumeCursor": {"sequence": 12},
            "fallbacks": ["merged_logs", "stdout", "stderr", "diagnostics"],
        }

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
                        "taskRunId": "tr_shared",
                        "status": "succeeded",
                        "summary": "Plan passed",
                        "artifactRefs": [
                            {"artifact_id": "art_plan", "kind": "step.summary"}
                        ],
                    },
                    {
                        "logicalStepId": "implement",
                        "attempt": 2,
                        "taskRunId": "tr_shared",
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
            "task": {
                "remediation": {
                    "target": {
                        "workflowId": target.workflow_id,
                        "stepSelectors": [
                            {
                                "logicalStepId": "implement",
                                "attempt": 2,
                                "taskRunId": "tr_shared",
                            }
                        ],
                        "taskRunIds": ["tr_shared"],
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
                "taskRunId": "tr_shared",
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
                "taskRuns": [
                    {
                        "taskRunId": "tr_history",
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
            "task": {
                "remediation": {
                    "target": {
                        "workflowId": target.workflow_id,
                        "taskRunIds": ["tr_history"],
                    },
                    "mode": "snapshot_then_follow",
                    "evidencePolicy": {"allowLiveFollow": False},
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

        assert result.payload["evidence"]["taskRuns"] == [
            {
                "taskRunId": "tr_history",
                "mergedLogsRef": {"artifact_id": "art_merged"},
            }
        ]
        assert result.payload["evidence"]["evidenceDegraded"] is True
        assert result.payload["evidence"]["unavailableEvidenceClasses"] == [
            "stdout",
            "stderr",
            "diagnostics",
            "provider_snapshot",
            "continuity",
            "live_follow",
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
            {
                "class": "live_follow",
                "status": "denied",
                "reason": "policy denies live observation",
                "fallback": "merged_logs",
            },
        ]
        assert result.payload["liveFollow"] == {
            "status": "policy_denied",
            "mode": "snapshot_then_follow",
            "supported": False,
            "taskRunId": "tr_history",
            "resumeCursor": None,
            "reason": "policy denies live observation",
            "fallbacks": ["merged_logs"],
        }

@pytest.mark.asyncio
async def test_remediation_context_builder_marks_unsupported_live_follow_degraded(
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
        target_source.state = MoonMindWorkflowState.EXECUTING
        target_source.memo = {
            **target_source.memo,
            "remediationEvidence": {
                "taskRuns": [
                    {
                        "taskRunId": "tr_no_live",
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
                "liveFollow": {"resumeCursor": {"sequence": 42}},
            },
        }
        remediation_source = await session.get(
            TemporalExecutionCanonicalRecord, remediation.workflow_id
        )
        assert remediation_source is not None
        remediation_source.parameters = {
            **remediation_source.parameters,
            "task": {
                "remediation": {
                    "target": {
                        "workflowId": target.workflow_id,
                        "taskRunIds": ["tr_no_live"],
                    },
                    "mode": "snapshot_then_follow",
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

        assert result.payload["evidence"]["evidenceDegraded"] is True
        assert result.payload["evidence"]["unavailableEvidenceClasses"] == [
            "live_follow"
        ]
        assert result.payload["evidence"]["availability"][-1] == {
            "class": "live_follow",
            "status": "unsupported",
            "reason": "task run does not support live follow",
            "fallback": "merged_logs",
        }
        assert result.payload["liveFollow"] == {
            "status": "unsupported",
            "mode": "snapshot_then_follow",
            "supported": False,
            "taskRunId": "tr_no_live",
            "resumeCursor": {"sequence": 42},
            "reason": "task run does not support live follow",
            "fallbacks": ["merged_logs", "stdout", "stderr", "diagnostics"],
        }

def test_remediation_lifecycle_summary_audit_and_continuation_are_bounded():
    assert normalize_remediation_phase("diagnosing") == "diagnosing"
    assert normalize_remediation_phase("unknown") == "failed"
    assert normalize_remediation_resolution("resolved_after_action") == (
        "resolved_after_action"
    )
    assert normalize_remediation_resolution("mystery") == "failed"

    summary = build_remediation_summary_block(
        target_workflow_id="target-workflow",
        target_run_id="target-run",
        phase="resolved",
        mode="snapshot_then_follow",
        authority_mode="admin_auto",
        actions_attempted=(
            {
                "kind": "provider_profile.evict_stale_lease",
                "status": "applied",
                "token": "raw-secret",
            },
        ),
        resolution="resolved_after_action",
        lock_conflicts=1,
        approval_count=2,
        evidence_degraded=True,
        escalated=False,
        unavailable_evidence_classes=("live_follow", "artifact_refs"),
        fallbacks_used=("logs/merged",),
        resulting_target_run_id="target-run-2",
    )

    assert summary == {
        "targetWorkflowId": "target-workflow",
        "targetRunId": "target-run",
        "resultingTargetRunId": "target-run-2",
        "phase": "resolved",
        "mode": "snapshot_then_follow",
        "authorityMode": "admin_auto",
        "actionsAttempted": [
            {
                "kind": "provider_profile.evict_stale_lease",
                "status": "applied",
            }
        ],
        "resolution": "resolved_after_action",
        "lockConflicts": 1,
        "approvalCount": 2,
        "evidenceDegraded": True,
        "escalated": False,
        "unavailableEvidenceClasses": ["live_follow", "artifact_refs"],
        "fallbacksUsed": ["logs/merged"],
    }

    audit = build_remediation_audit_event(
        event_id="audit-1",
        event_type="remediation.action",
        actor_user="user:operator",
        execution_principal="service:admin-healer",
        remediation_workflow_id="remediation-workflow",
        remediation_run_id="remediation-run",
        target_workflow_id="target-workflow",
        target_run_id="target-run",
        action_kind="provider_profile.evict_stale_lease",
        risk_tier="medium",
        approval_decision="approved",
        timestamp=datetime(2026, 4, 22, tzinfo=timezone.utc),
        metadata={
            "resolution": "resolved_after_action",
            "token": "raw-secret",
            "url": "https://storage.example/object?token=raw-secret",
        },
    )
    assert audit["timestamp"] == "2026-04-22T00:00:00Z"
    assert audit["metadata"] == {"resolution": "resolved_after_action"}
    assert "raw-secret" not in json.dumps(audit)

    continuation = build_remediation_continue_as_new_state(
        target_workflow_id="target-workflow",
        target_run_id="target-run",
        context_artifact_ref="art_context",
        lock_identity="lock-1",
        action_ledger_ref="ledger-1",
        approval_state="approved",
        retry_budget_state={"actionsAttempted": 1, "token": "raw-secret"},
        live_follow_cursor={"sequence": 42},
    )
    assert continuation == {
        "targetWorkflowId": "target-workflow",
        "targetRunId": "target-run",
        "contextArtifactRef": "art_context",
        "lockIdentity": "lock-1",
        "actionLedgerRef": "ledger-1",
        "approvalState": "approved",
        "retryBudgetState": {"actionsAttempted": 1},
        "liveFollowCursor": {"sequence": 42},
    }

    failed_summary = build_remediation_summary_block(
        target_workflow_id="target-workflow",
        target_run_id="target-run",
        phase="failed",
        mode="snapshot_then_follow",
        authority_mode="approval_gated",
        resolution="failed",
        evidence_degraded=True,
        escalated=True,
        unavailable_evidence_classes=("target_visibility",),
        fallbacks_used=("final_summary_publication", "lock_release_attempt"),
    )
    assert failed_summary["phase"] == "failed"
    assert failed_summary["resolution"] == "failed"
    assert failed_summary["evidenceDegraded"] is True
    assert failed_summary["escalated"] is True
    assert failed_summary["fallbacksUsed"] == [
        "final_summary_publication",
        "lock_release_attempt",
    ]

def test_remediation_summary_allows_hierarchical_target_identifiers():
    summary = build_remediation_summary_block(
        target_workflow_id="/tenant/workflows/target",
        target_run_id="/runs/target-run",
        phase="resolved",
        mode="snapshot_then_follow",
        authority_mode="admin_auto",
        resolution="resolved_after_action",
    )

    assert summary["targetWorkflowId"] == "/tenant/workflows/target"
    assert summary["targetRunId"] == "/runs/target-run"

    repair = build_remediation_repair_decision(
        target_workflow_id="/tenant/workflows/target",
        pinned_run_id="/runs/pinned-target-run",
        current_run_id="/runs/current-target-run",
        decision="skipped",
        decision_reason="target_already_healthy",
        repair_outcome="not_attempted",
    )
    assert repair["target"]["workflowId"] == "/tenant/workflows/target"
    assert repair["target"]["pinnedRunId"] == "/runs/pinned-target-run"
    assert repair["target"]["currentRunId"] == "/runs/current-target-run"
    assert repair["target"]["targetRunChanged"] is True

def test_remediation_lifecycle_repair_prevention_and_decision_log_are_bounded():
    repair = build_remediation_repair_decision(
        target_workflow_id="target-workflow",
        pinned_run_id="target-run",
        current_run_id="target-run-2",
        candidate_action_kind="provider_profile.evict_stale_lease",
        candidate_reason="lease_stale",
        decision="attempted",
        decision_reason="fresh_target_health_and_policy_allowed",
        repair_outcome="repaired",
        fresh_target_health_ref="art_health",
        authority_decision_ref="art_authority",
        guard_decision_ref="art_guard",
        action_request_ref="art_request",
        action_result_ref="art_result",
        verification_ref="art_verification",
        metadata={
            "safe": "value",
            "token": "raw-secret",
            "path": "/tmp/raw/path",
        },
    )

    assert repair == {
        "schemaVersion": "v1",
        "target": {
            "workflowId": "target-workflow",
            "pinnedRunId": "target-run",
            "currentRunId": "target-run-2",
            "targetRunChanged": True,
        },
        "candidate": {
            "actionKind": "provider_profile.evict_stale_lease",
            "reason": "lease_stale",
        },
        "decision": "attempted",
        "decisionReason": "fresh_target_health_and_policy_allowed",
        "artifactRefs": {
            "freshTargetHealth": "art_health",
            "authorityDecision": "art_authority",
            "guardDecision": "art_guard",
            "actionRequest": "art_request",
            "actionResult": "art_result",
            "verification": "art_verification",
        },
        "repairOutcome": "repaired",
        "metadata": {"safe": "value"},
    }

    prevention = build_remediation_prevention_outcome(
        status="findings_reported",
        root_cause_category="provider_profile_lease_recovery_gap",
        summary="Investigated recurrence without creating a PR.",
        findings_ref="art_findings",
        metadata={"authorization": "Bearer raw-secret", "safe": "value"},
    )
    assert prevention == {
        "schemaVersion": "v1",
        "status": "findings_reported",
        "rootCauseCategory": "provider_profile_lease_recovery_gap",
        "summary": "Investigated recurrence without creating a PR.",
        "findingsRef": "art_findings",
        "metadata": {"safe": "value"},
    }

    decision_log = build_remediation_decision_log(
        entries=(
            {
                "timestamp": "2026-05-08T00:00:00Z",
                "phase": "diagnosing",
                "decisionType": "repair_candidate",
                "decision": "attempted",
                "reason": "fresh_target_health_and_policy_allowed",
                "actor": "service:remediation",
                "actionKind": "provider_profile.evict_stale_lease",
                "targetWorkflowId": "target-workflow",
                "targetRunId": "target-run",
                "artifactRefs": {
                    "actionRequest": "art_request",
                    "verification": "art_verification",
                },
                "metadata": {"password": "raw-secret", "safe": "value"},
            },
        )
    )
    assert decision_log == {
        "schemaVersion": "v1",
        "entries": [
            {
                "timestamp": "2026-05-08T00:00:00Z",
                "phase": "diagnosing",
                "decisionType": "repair_candidate",
                "decision": "attempted",
                "reason": "fresh_target_health_and_policy_allowed",
                "actor": "service:remediation",
                "actionKind": "provider_profile.evict_stale_lease",
                "targetWorkflowId": "target-workflow",
                "targetRunId": "target-run",
                "artifactRefs": {
                    "actionRequest": "art_request",
                    "verification": "art_verification",
                },
                "metadata": {"safe": "value"},
            }
        ],
    }

    base_summary = build_remediation_summary_block(
        target_workflow_id="target-workflow",
        target_run_id="target-run",
        phase="resolved",
        mode="snapshot_then_follow",
        authority_mode="admin_auto",
        resolution="resolved_after_action",
        resulting_target_run_id="target-run-2",
    )
    final_summary = build_remediation_final_summary(
        summary=base_summary,
        repair=repair,
        prevention=prevention,
        decision_log_ref="art_decision_log",
        final_audit_ref="art_audit",
        lock_release="released",
        metadata={"token": "raw-secret", "safe": "value"},
    )
    assert final_summary["repair"]["repairOutcome"] == "repaired"
    assert final_summary["prevention"]["status"] == "findings_reported"
    assert final_summary["decisionLogRef"] == "art_decision_log"
    assert final_summary["finalAuditRef"] == "art_audit"
    assert final_summary["lockRelease"] == "released"
    serialized = json.dumps(final_summary, sort_keys=True)
    assert "raw-secret" not in serialized
    assert "/tmp/raw/path" not in serialized

def test_reviewable_prevention_pr_url_survives_final_summary_sanitization():
    repair = build_remediation_repair_decision(
        target_workflow_id="target-workflow",
        pinned_run_id="target-run",
        decision="skipped",
        decision_reason="target_already_healthy",
        repair_outcome="not_attempted",
    )
    prevention = build_remediation_prevention_outcome(
        status="reviewable_change_created",
        root_cause_category="provider_profile_lease_recovery_gap",
        summary="Created a recurrence-prevention change.",
        pull_request_url="https://github.com/org/repo/pull/123?token=raw-secret",
    )
    final_summary = build_remediation_final_summary(
        summary=build_remediation_summary_block(
            target_workflow_id="target-workflow",
            target_run_id="target-run",
            phase="resolved",
            mode="snapshot_then_follow",
            authority_mode="admin_auto",
        ),
        repair=repair,
        prevention=prevention,
        lock_release="released",
    )

    assert (
        final_summary["prevention"]["pullRequestUrl"]
        == "https://github.com/org/repo/pull/123?token=[REDACTED]"
    )
    assert "raw-secret" not in json.dumps(final_summary)

def test_remediation_lifecycle_contract_rejects_invalid_or_incomplete_values():
    with pytest.raises(ValueError, match="repair_outcome"):
        build_remediation_repair_decision(
            target_workflow_id="target-workflow",
            pinned_run_id="target-run",
            decision="attempted",
            decision_reason="fresh",
            repair_outcome="applied",
            action_request_ref="art_request",
            action_result_ref="art_result",
            verification_ref="art_verification",
        )

    with pytest.raises(ValueError, match="attempted repair requires"):
        build_remediation_repair_decision(
            target_workflow_id="target-workflow",
            pinned_run_id="target-run",
            decision="attempted",
            decision_reason="fresh",
            repair_outcome="repaired",
            action_request_ref="art_request",
            action_result_ref="art_result",
        )

    with pytest.raises(ValueError, match="pullRequestUrl"):
        build_remediation_prevention_outcome(
            status="reviewable_change_created",
            root_cause_category="provider_profile_lease_recovery_gap",
            summary="Created a change.",
        )

    with pytest.raises(ValueError, match="lock_release"):
        build_remediation_final_summary(
            summary=build_remediation_summary_block(
                target_workflow_id="target-workflow",
                target_run_id="target-run",
                phase="failed",
                mode="snapshot_then_follow",
                authority_mode="admin_auto",
            ),
            repair=build_remediation_repair_decision(
                target_workflow_id="target-workflow",
                pinned_run_id="target-run",
                decision="skipped",
                decision_reason="target_already_healthy",
                repair_outcome="not_attempted",
            ),
            prevention=build_remediation_prevention_outcome(
                status="no_reviewable_fix",
                root_cause_category="none",
                summary="No recurring defect.",
            ),
            lock_release="unknown",
        )

def test_corrected_instruction_retry_provenance_is_explicit_and_redacted():
    provenance = build_corrected_instruction_retry_provenance(
        original_input_ref="art_original_input",
        remediation_context_ref="art_context",
        corrected_instructions_ref="art_corrected",
        retry_action_kind="execution.retry_failed_step_with_remediation_context",
        reason="publish instructions clarified; token=raw-secret",
        metadata={"password": "raw-secret", "safe": "value"},
    )

    assert provenance == {
        "schemaVersion": "v1",
        "retryActionKind": "execution.retry_failed_step_with_remediation_context",
        "originalInputRef": "art_original_input",
        "remediationContextRef": "art_context",
        "correctedInstructionsRef": "art_corrected",
        "reason": "publish instructions clarified; token=[REDACTED]",
        "metadata": {"safe": "value"},
        "originalInputMutation": False,
    }

def test_remediation_audit_normalizes_string_timestamps():
    audit = build_remediation_audit_event(
        event_id="audit-1",
        event_type="remediation.action",
        actor_user="user:operator",
        execution_principal="service:admin-healer",
        remediation_workflow_id="remediation-workflow",
        remediation_run_id="remediation-run",
        target_workflow_id="target-workflow",
        target_run_id="target-run",
        action_kind="workload.restart_helper_container",
        risk_tier="medium",
        approval_decision="approved",
        timestamp="2026-04-22T01:02:03+02:00",
    )

    assert audit["timestamp"] == "2026-04-21T23:02:03Z"

def test_remediation_audit_rejects_malformed_string_timestamps():
    with pytest.raises(RemediationContextError, match="timestamp must be ISO8601"):
        build_remediation_audit_event(
            event_id="audit-1",
            event_type="remediation.action",
            actor_user="user:operator",
            execution_principal="service:admin-healer",
            remediation_workflow_id="remediation-workflow",
            remediation_run_id="remediation-run",
            target_workflow_id="target-workflow",
            target_run_id="target-run",
            action_kind="workload.restart_helper_container",
            risk_tier="medium",
            approval_decision="approved",
            timestamp="not-a-timestamp",
        )

def test_target_remediation_linkage_summary_is_compact():
    summary = build_target_remediation_linkage_summary(
        target_workflow_id="target-workflow",
        active_remediation_count=2,
        latest_remediation_title="Publish remediation lifecycle phases",
        latest_remediation_status="acting",
        latest_action_kind="workload.restart_helper_container",
        latest_outcome="resolved_after_action",
        active_lock_scope="target_execution",
        active_lock_holder="remediation-workflow",
        last_updated_at=datetime(2026, 4, 22, 1, 2, 3, tzinfo=timezone.utc),
        metadata={"token": "raw-secret", "safe": "value"},
    )

    assert summary == {
        "targetWorkflowId": "target-workflow",
        "activeRemediationCount": 2,
        "latestRemediationTitle": "Publish remediation lifecycle phases",
        "latestRemediationStatus": "acting",
        "latestActionKind": "workload.restart_helper_container",
        "latestOutcome": "resolved_after_action",
        "activeLockScope": "target_execution",
        "activeLockHolder": "remediation-workflow",
        "lastUpdatedAt": "2026-04-22T01:02:03Z",
        "metadata": {"safe": "value"},
    }

@pytest.mark.asyncio
async def test_remediation_lifecycle_publisher_creates_required_artifacts(
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
        publisher = RemediationLifecyclePublisher(
            session=session,
            artifact_service=artifact_service,
        )

        summary = build_remediation_summary_block(
            target_workflow_id=target.workflow_id,
            target_run_id=target.run_id,
            phase="resolved",
            mode="snapshot_then_follow",
            authority_mode="admin_auto",
            actions_attempted=({"kind": "workload.restart_helper_container", "status": "applied"},),
            resolution="resolved_after_action",
        )
        artifacts = []
        for artifact_type, name, payload in (
            (
                "remediation.plan",
                "reports/remediation_plan.json",
                {"steps": ["diagnose", "act"]},
            ),
            (
                "remediation.decision_log",
                "logs/remediation_decision_log.ndjson",
                {"decision": "acting"},
            ),
            (
                "remediation.action_request",
                "reports/remediation_action_request-1.json",
                {"actionKind": "workload.restart_helper_container"},
            ),
            (
                "remediation.action_result",
                "reports/remediation_action_result-1.json",
                {"status": "applied"},
            ),
            (
                "remediation.verification",
                "reports/remediation_verification-1.json",
                {"targetHealthy": True},
            ),
            (
                "remediation.summary",
                "reports/remediation_summary.json",
                summary,
            ),
        ):
            artifacts.append(
                await publisher.publish_json_artifact(
                    remediation_workflow_id=remediation.workflow_id,
                    artifact_type=artifact_type,
                    name=name,
                    payload=payload,
                    target_workflow_id=target.workflow_id,
                    target_run_id=target.run_id,
                    principal="service:test",
                )
            )

        links = (
            await session.execute(
                select(TemporalArtifactLink).where(
                    TemporalArtifactLink.workflow_id == remediation.workflow_id,
                    TemporalArtifactLink.link_type.in_(
                        [
                            "remediation.plan",
                            "remediation.decision_log",
                            "remediation.action_request",
                            "remediation.action_result",
                            "remediation.verification",
                            "remediation.summary",
                        ]
                    ),
                )
            )
        ).scalars().all()
        assert [artifact.metadata_json["artifact_type"] for artifact in artifacts] == [
            "remediation.plan",
            "remediation.decision_log",
            "remediation.action_request",
            "remediation.action_result",
            "remediation.verification",
            "remediation.summary",
        ]
        assert {link.link_type for link in links} == {
            "remediation.plan",
            "remediation.decision_log",
            "remediation.action_request",
            "remediation.action_result",
            "remediation.verification",
            "remediation.summary",
        }
        remediation_record = await session.get(
            TemporalExecutionCanonicalRecord, remediation.workflow_id
        )
        assert remediation_record is not None
        for artifact in artifacts:
            assert artifact.artifact_id in remediation_record.artifact_refs

        _artifact, payload_bytes = await artifact_service.read(
            artifact_id=artifacts[-1].artifact_id,
            principal="service:test",
        )
        published_summary = json.loads(payload_bytes)
        assert published_summary["authorityMode"] == "admin_auto"
        assert published_summary["resolution"] == "resolved_after_action"

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

class RecordingActionExecutor:
    def __init__(self) -> None:
        self.calls = []

    async def execute_action(self, *, action_request, guard_result, target_health):
        self.calls.append(
            {
                "action_request": action_request,
                "guard_result": guard_result,
                "target_health": target_health,
            }
        )
        return {
            "status": "applied",
            "beforeStateRef": "artifact://before-state",
            "afterStateRef": "artifact://after-state",
            "sideEffects": [{"kind": "subsystem_call", "status": "accepted"}],
            "verification": {
                "status": "verified",
                "targetWorkflowId": target_health.workflow_id,
            },
        }


class SensitiveHintActionExecutor:
    def __init__(self) -> None:
        self.calls = []

    async def execute_action(self, *, action_request, guard_result, target_health):
        self.calls.append(
            {
                "action_request": action_request,
                "guard_result": guard_result,
                "target_health": target_health,
            }
        )
        return {
            "status": "applied",
            "beforeStateRef": "artifact://before-state",
            "afterStateRef": "artifact://after-state",
            "verificationHint": (
                "inspect /work/agent_jobs/mm:secret/repo/.env "
                "with token=raw-secret-token"
            ),
            "sideEffects": [{"kind": "subsystem_call", "status": "accepted"}],
            "verification": {"status": "verified"},
        }


class StatusOnlyActionExecutor:
    def __init__(self, status: str) -> None:
        self.status = status
        self.calls = []

    async def execute_action(self, *, action_request, guard_result, target_health):
        self.calls.append(
            {
                "action_request": action_request,
                "guard_result": guard_result,
                "target_health": target_health,
            }
        )
        return {
            "status": self.status,
            "message": f"returned {self.status}",
            "sideEffects": [],
            "verification": {"status": "not_verified"},
        }

async def _read_artifact_json(artifact_service, artifact_id: str):
    _artifact, payload = await artifact_service.read(
        artifact_id=artifact_id,
        principal="service:test",
    )
    return json.loads(payload.decode("utf-8"))

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
            action_kind="session.terminate",
        )

        assert preparation.remediation_workflow_id == remediation.workflow_id
        assert preparation.action_kind == "session.terminate"
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
async def test_remediation_execute_action_delegates_and_publishes_lifecycle_artifacts(
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

        action_kind = "workload.restart_helper_container"
        authority = await RemediationActionAuthorityService(
            session=session
        ).evaluate_action_request(
            remediation_workflow_id=remediation.workflow_id,
            action_kind=action_kind,
            parameters={"reason": "restart helper"},
            dry_run=False,
            idempotency_key="execute-action-1",
            requesting_principal="workflow:remediator",
            permissions=_admin_permissions(),
            security_profile=_admin_profile(
                allowed_action_kinds=(action_kind,),
            ),
        )
        guard = await RemediationMutationGuardService(session=session).evaluate(
            remediation_workflow_id=remediation.workflow_id,
            remediation_run_id=remediation.run_id,
            target_workflow_id=target.workflow_id,
            target_run_id=target.run_id,
            action_kind=action_kind,
            idempotency_key="execute-action-1",
            parameters={"reason": "restart helper"},
            policy=RemediationMutationGuardPolicy(cooldown_seconds=0),
            now=datetime(2026, 4, 23, tzinfo=timezone.utc),
        )

        executor = RecordingActionExecutor()
        tools = RemediationEvidenceToolService(
            session=session,
            artifact_service=artifact_service,
            action_executor=executor,
        )

        result = await tools.execute_action(
            remediation_workflow_id=remediation.workflow_id,
            authority_result=authority.to_dict(),
            guard_result=guard.to_dict(),
            principal="service:test",
        )

        artifact_links = (
            await session.execute(
                select(TemporalArtifactLink).where(
                    TemporalArtifactLink.workflow_id == remediation.workflow_id,
                    TemporalArtifactLink.link_type.in_(
                        [
                            "remediation.action_request",
                            "remediation.action_result",
                            "remediation.verification",
                        ]
                    ),
                )
            )
        ).scalars().all()
        link = await session.get(
            TemporalExecutionRemediationLink,
            remediation.workflow_id,
        )

        assert executor.calls[0]["action_request"]["actionKind"] == action_kind
        assert executor.calls[0]["target_health"].workflow_id == target.workflow_id
        assert result["status"] == "applied"
        assert result["artifactRefs"]["actionRequest"]
        assert result["artifactRefs"]["actionResult"]
        assert result["artifactRefs"]["verification"]
        assert {artifact.link_type for artifact in artifact_links} == {
            "remediation.action_request",
            "remediation.action_result",
            "remediation.verification",
        }
        assert link is not None
        assert link.latest_action_summary == action_kind
        assert link.outcome == "applied"

@pytest.mark.asyncio
async def test_remediation_execute_action_publishes_v1_request_and_result_artifacts(
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
        await RemediationContextBuilder(
            session=session,
            artifact_service=artifact_service,
        ).build_context(remediation_workflow_id=remediation.workflow_id)

        action_kind = "workload.restart_helper_container"
        action_id = "execute-action-v1"
        authority = await RemediationActionAuthorityService(
            session=session
        ).evaluate_action_request(
            remediation_workflow_id=remediation.workflow_id,
            action_kind=action_kind,
            parameters={"reason": "Authorization: Bearer raw-secret-token"},
            dry_run=False,
            idempotency_key=action_id,
            requesting_principal="workflow:remediator",
            permissions=_admin_permissions(),
            security_profile=_admin_profile(allowed_action_kinds=(action_kind,)),
        )
        guard = await RemediationMutationGuardService(session=session).evaluate(
            remediation_workflow_id=remediation.workflow_id,
            remediation_run_id=remediation.run_id,
            target_workflow_id=target.workflow_id,
            target_run_id=target.run_id,
            action_kind=action_kind,
            idempotency_key=action_id,
            parameters={"reason": "restart helper"},
            policy=RemediationMutationGuardPolicy(cooldown_seconds=0),
            now=datetime(2026, 4, 23, tzinfo=timezone.utc),
        )
        tools = RemediationEvidenceToolService(
            session=session,
            artifact_service=artifact_service,
            action_executor=SensitiveHintActionExecutor(),
        )

        result = await tools.execute_action(
            remediation_workflow_id=remediation.workflow_id,
            authority_result=authority.to_dict(),
            guard_result=guard.to_dict(),
            principal="service:test",
        )

        request_payload = await _read_artifact_json(
            artifact_service, result["artifactRefs"]["actionRequest"]
        )
        result_payload = await _read_artifact_json(
            artifact_service, result["artifactRefs"]["actionResult"]
        )

        assert request_payload["schemaVersion"] == "v1"
        assert request_payload["actionId"] == action_id
        assert request_payload["actionKind"] == action_kind
        assert request_payload["requester"] == "workflow:remediator"
        assert request_payload["target"] == {
            "workflowId": target.workflow_id,
            "resourceKind": "workload_container",
        }
        assert request_payload["riskTier"] == "medium"
        assert request_payload["dryRun"] is False
        assert request_payload["idempotencyKey"] == action_id
        assert "raw-secret-token" not in json.dumps(request_payload, sort_keys=True)

        assert result_payload["schemaVersion"] == "v1"
        assert result_payload["actionId"] == action_id
        assert result_payload["actionKind"] == action_kind
        assert result_payload["status"] == "applied"
        assert result_payload["message"]
        assert result_payload["appliedAt"]
        assert result_payload["beforeStateRef"] == "artifact://before-state"
        assert result_payload["afterStateRef"] == "artifact://after-state"
        assert result_payload["verificationRequired"] is True
        assert result_payload["verificationHint"]
        assert "raw-secret-token" not in result_payload["verificationHint"]
        assert "/work/agent_jobs" not in result_payload["verificationHint"]
        assert result_payload["sideEffects"] == [
            {"kind": "subsystem_call", "status": "accepted"}
        ]

@pytest.mark.asyncio
async def test_remediation_execute_action_rejects_unsupported_result_status(
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
        await RemediationContextBuilder(
            session=session,
            artifact_service=artifact_service,
        ).build_context(remediation_workflow_id=remediation.workflow_id)

        action_kind = "workload.restart_helper_container"
        authority = await RemediationActionAuthorityService(
            session=session
        ).evaluate_action_request(
            remediation_workflow_id=remediation.workflow_id,
            action_kind=action_kind,
            parameters={"reason": "restart helper"},
            dry_run=False,
            idempotency_key="unsupported-status",
            requesting_principal="workflow:remediator",
            permissions=_admin_permissions(),
            security_profile=_admin_profile(allowed_action_kinds=(action_kind,)),
        )
        guard = await RemediationMutationGuardService(session=session).evaluate(
            remediation_workflow_id=remediation.workflow_id,
            remediation_run_id=remediation.run_id,
            target_workflow_id=target.workflow_id,
            target_run_id=target.run_id,
            action_kind=action_kind,
            idempotency_key="unsupported-status",
            parameters={"reason": "restart helper"},
            policy=RemediationMutationGuardPolicy(cooldown_seconds=0),
            now=datetime(2026, 4, 23, tzinfo=timezone.utc),
        )
        tools = RemediationEvidenceToolService(
            session=session,
            artifact_service=artifact_service,
            action_executor=StatusOnlyActionExecutor("mystery_status"),
        )

        with pytest.raises(RemediationEvidenceToolError, match="Unsupported action result status"):
            await tools.execute_action(
                remediation_workflow_id=remediation.workflow_id,
                authority_result=authority.to_dict(),
                guard_result=guard.to_dict(),
                principal="service:test",
            )

@pytest.mark.asyncio
async def test_remediation_execute_action_rejects_mismatched_authority_or_guard_context(
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

        action_kind = "workload.restart_helper_container"
        authority = await RemediationActionAuthorityService(
            session=session
        ).evaluate_action_request(
            remediation_workflow_id=remediation.workflow_id,
            action_kind=action_kind,
            parameters={"reason": "restart helper"},
            dry_run=False,
            idempotency_key="execute-action-context",
            requesting_principal="workflow:remediator",
            permissions=_admin_permissions(),
            security_profile=_admin_profile(
                allowed_action_kinds=(action_kind,),
            ),
        )
        guard = await RemediationMutationGuardService(session=session).evaluate(
            remediation_workflow_id=remediation.workflow_id,
            remediation_run_id=remediation.run_id,
            target_workflow_id=target.workflow_id,
            target_run_id=target.run_id,
            action_kind=action_kind,
            idempotency_key="execute-action-context",
            parameters={"reason": "restart helper"},
            policy=RemediationMutationGuardPolicy(cooldown_seconds=0),
            now=datetime(2026, 4, 23, tzinfo=timezone.utc),
        )
        executor = RecordingActionExecutor()
        tools = RemediationEvidenceToolService(
            session=session,
            artifact_service=artifact_service,
            action_executor=executor,
        )

        stale_authority = authority.to_dict()
        stale_authority["remediationWorkflowId"] = "mm:other-remediation"
        with pytest.raises(RemediationEvidenceToolError, match="authorityResult"):
            await tools.execute_action(
                remediation_workflow_id=remediation.workflow_id,
                authority_result=stale_authority,
                guard_result=guard.to_dict(),
                principal="service:test",
            )

        stale_guard = guard.to_dict()
        stale_guard["targetWorkflowId"] = "mm:other-target"
        with pytest.raises(RemediationEvidenceToolError, match="guardResult"):
            await tools.execute_action(
                remediation_workflow_id=remediation.workflow_id,
                authority_result=authority.to_dict(),
                guard_result=stale_guard,
                principal="service:test",
            )

        stale_run_guard = guard.to_dict()
        stale_run_guard["lock"] = {
            **stale_run_guard["lock"],
            "targetRunId": "other-run",
        }
        with pytest.raises(RemediationEvidenceToolError, match="target run"):
            await tools.execute_action(
                remediation_workflow_id=remediation.workflow_id,
                authority_result=authority.to_dict(),
                guard_result=stale_run_guard,
                principal="service:test",
            )

        assert executor.calls == []

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
            action_kind="workload.restart_helper_container",
            parameters={"reason": "diagnose only"},
            dry_run=True,
            idempotency_key="observe-dry-run",
            requesting_principal="user:operator",
            permissions=_admin_permissions(),
            security_profile=_admin_profile(),
        )
        assert dry_run.decision == "dry_run_only"
        assert dry_run.executable is False
        dry_run_payload = dry_run.to_dict()
        assert dry_run_payload["request"]["dryRun"] is True
        assert dry_run_payload["result"]["status"] == "no_op"
        assert dry_run_payload["result"]["verificationRequired"] is False
        assert dry_run_payload["result"]["verificationHint"] is None

        denied = await service.evaluate_action_request(
            remediation_workflow_id=remediation.workflow_id,
            action_kind="workload.restart_helper_container",
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
            action_kind="workload.restart_helper_container",
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
            action_kind="workload.restart_helper_container",
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
            action_kind="workload.restart_helper_container",
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
            action_kind="workload.restart_helper_container",
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
            action_kind="workload.restart_helper_container",
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
        payload = allowed.to_dict()
        assert payload["schemaVersion"] == "v1"
        assert payload["request"]["actionKind"] == "workload.restart_helper_container"
        assert payload["request"]["riskTier"] == "medium"
        assert payload["request"]["dryRun"] is False
        assert payload["result"]["status"] == "applied"
        assert payload["result"]["verificationRequired"] is True
        assert payload["result"]["verificationHint"]
        assert payload["result"]["sideEffects"] == []
        assert payload["audit"]["executionPrincipal"] == "service:admin-healer"

        high_risk = await service.evaluate_action_request(
            remediation_workflow_id=remediation.workflow_id,
            action_kind="session.terminate",
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
async def test_remediation_action_authority_rejects_unsupported_authority_mode(
    tmp_path, mock_client_adapter
):
    async with temporal_db(tmp_path) as session:
        _target, remediation = await _create_target_and_remediation(
            session,
            mock_client_adapter,
            authority_mode="admin_auto",
        )
        link = await session.get(
            TemporalExecutionRemediationLink,
            remediation.workflow_id,
        )
        assert link is not None
        link.authority_mode = "stale_typo"
        await session.commit()

        service = RemediationActionAuthorityService(session=session)
        result = await service.evaluate_action_request(
            remediation_workflow_id=remediation.workflow_id,
            action_kind="workload.restart_helper_container",
            parameters={},
            dry_run=False,
            idempotency_key="unsupported-authority",
            requesting_principal="user:operator",
            permissions=_admin_permissions(),
            security_profile=_admin_profile(),
        )

        assert result.decision == "denied"
        assert result.reason == "unsupported_authority_mode"
        assert result.executable is False

@pytest.mark.asyncio
async def test_remediation_action_authority_cache_keys_include_request_shape(
    tmp_path, mock_client_adapter
):
    async with temporal_db(tmp_path) as session:
        _target, remediation = await _create_target_and_remediation(
            session,
            mock_client_adapter,
            authority_mode="admin_auto",
        )
        service = RemediationActionAuthorityService(session=session)

        allowed = await service.evaluate_action_request(
            remediation_workflow_id=remediation.workflow_id,
            action_kind="workload.restart_helper_container",
            parameters={},
            dry_run=False,
            idempotency_key="same-idempotency-key",
            requesting_principal="user:operator",
            permissions=_admin_permissions(),
            security_profile=_admin_profile(),
        )
        high_risk = await service.evaluate_action_request(
            remediation_workflow_id=remediation.workflow_id,
            action_kind="session.terminate",
            parameters={},
            dry_run=False,
            idempotency_key="same-idempotency-key",
            requesting_principal="user:operator",
            permissions=_admin_permissions(),
            security_profile=_admin_profile(),
        )
        dry_run = await service.evaluate_action_request(
            remediation_workflow_id=remediation.workflow_id,
            action_kind="workload.restart_helper_container",
            parameters={},
            dry_run=True,
            idempotency_key="same-idempotency-key",
            requesting_principal="user:operator",
            permissions=_admin_permissions(),
            security_profile=_admin_profile(),
        )

        assert allowed.decision == "allowed"
        assert high_risk.decision == "denied"
        assert high_risk.reason == "idempotency_key_reused_with_different_request"
        assert dry_run.decision == "denied"
        assert dry_run.reason == "idempotency_key_reused_with_different_request"

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
            action_kind="workload.restart_helper_container",
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
            action_kind="workload.restart_helper_container",
            parameters={"token": "different-secret"},
            dry_run=False,
            idempotency_key="dedupe-redact",
            requesting_principal="user:operator",
            permissions=_admin_permissions(),
            security_profile=_admin_profile(),
        )

        assert duplicate is not result
        assert duplicate.decision == "denied"
        assert duplicate.reason == "idempotency_key_reused_with_different_request"
        serialized = json.dumps(result.to_dict(), sort_keys=True)
        assert "raw-secret-token" not in serialized
        assert "/work/agent_jobs" not in serialized
        assert "Bearer" not in serialized
        assert result.audit["requestingPrincipal"] == "user:operator"
        assert result.audit["executionPrincipal"] == "service:admin-healer"

def test_remediation_action_redaction_handles_null_and_single_segment_paths(
    monkeypatch,
):
    monkeypatch.setattr(
        remediation_actions,
        "redact_sensitive_text",
        lambda value: None if value is None else str(value),
    )

    assert remediation_actions._redact_text(None) == ""
    assert remediation_actions._redact_text("/tmp") == "[REDACTED_PATH]"


def test_remediation_tool_redaction_handles_null_redactor_and_single_pass_payload(
    monkeypatch,
):
    calls = []

    def redact_payload(value):
        calls.append(value)
        return {
            "hint": "inspect /work/agent_jobs/mm:secret/repo/.env",
            "nested": ["https://example.test/download?token=raw-secret-token"],
        }

    monkeypatch.setattr(remediation_tools, "redact_sensitive_payload", redact_payload)
    monkeypatch.setattr(
        remediation_tools,
        "redact_sensitive_text",
        lambda value: None if value == "return-none" else str(value),
    )

    assert remediation_tools._redact_text("return-none") is None
    assert remediation_tools._redact_payload_value({"ignored": "input"}) == {
        "hint": "inspect [REDACTED_PATH]",
        "nested": ["[REDACTED_URL]"],
    }
    assert calls == [{"ignored": "input"}]

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

        raw_action_kinds = (
            "raw_host_shell",
            "raw_sql",
            "raw_docker",
            "raw_volume_mount",
            "raw_network_egress",
            "secret_read",
            "redaction_bypass",
        )
        for index, action_kind in enumerate(raw_action_kinds):
            raw_access = await service.evaluate_action_request(
                remediation_workflow_id=remediation.workflow_id,
                action_kind=action_kind,
                parameters={"command": "docker ps", "token": "raw-secret-token"},
                dry_run=False,
                idempotency_key=f"raw-action-{index}",
                requesting_principal="user:operator",
                permissions=_admin_permissions(),
                security_profile=_admin_profile(),
            )
            assert raw_access.decision == "denied"
            assert raw_access.reason == "raw_access_action_denied"
            assert raw_access.executable is False

        missing = await service.evaluate_action_request(
            remediation_workflow_id="mm:missing-remediation",
            action_kind="workload.restart_helper_container",
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
            action_kind="workload.restart_helper_container",
        )

        service = RemediationActionAuthorityService(session=session)
        decision = await service.evaluate_action_request(
            remediation_workflow_id=preparation.remediation_workflow_id,
            action_kind=preparation.action_kind,
            parameters={"reason": f"target state was {preparation.target.state}"},
            dry_run=False,
            idempotency_key="prepared-action",
            requesting_principal="workflow:remediator",
            permissions=_admin_permissions(),
            security_profile=_admin_profile(),
        )

        assert preparation.target.workflow_id == target.workflow_id
        assert decision.decision == "allowed"
        assert decision.target_workflow_id == target.workflow_id

@pytest.mark.asyncio
async def test_remediation_action_authority_validates_action_inputs(
    tmp_path, mock_client_adapter
):
    async with temporal_db(tmp_path) as session:
        _target, remediation = await _create_target_and_remediation(
            session,
            mock_client_adapter,
            authority_mode="admin_auto",
        )
        service = RemediationActionAuthorityService(session=session)

        invalid = await service.evaluate_action_request(
            remediation_workflow_id=remediation.workflow_id,
            action_kind="workload.restart_helper_container",
            parameters={"unknown": "value"},
            dry_run=False,
            idempotency_key="invalid-inputs",
            requesting_principal="user:operator",
            permissions=_admin_permissions(),
            security_profile=_admin_profile(),
        )

        assert invalid.decision == "denied"
        assert invalid.reason == "unsupported_action_parameter"
        assert invalid.executable is False

        wrong_type = await service.evaluate_action_request(
            remediation_workflow_id=remediation.workflow_id,
            action_kind="workload.restart_helper_container",
            parameters={"reason": False},
            dry_run=False,
            idempotency_key="invalid-input-type",
            requesting_principal="user:operator",
            permissions=_admin_permissions(),
            security_profile=_admin_profile(),
        )

        assert wrong_type.decision == "denied"
        assert wrong_type.reason == "invalid_action_parameter_type"
        assert wrong_type.executable is False

@pytest.mark.asyncio
async def test_remediation_mutation_guard_enforces_exclusive_locks_and_recovery(
    tmp_path, mock_client_adapter
):
    async with temporal_db(tmp_path) as session:
        target, remediation = await _create_target_and_remediation(
            session,
            mock_client_adapter,
            authority_mode="admin_auto",
        )
        service = RemediationMutationGuardService(session=session)
        now = datetime(2026, 4, 22, tzinfo=timezone.utc)

        first = await service.evaluate(
            remediation_workflow_id=remediation.workflow_id,
            remediation_run_id=remediation.run_id,
            target_workflow_id=target.workflow_id,
            target_run_id=target.run_id,
            action_kind="workload.restart_helper_container",
            idempotency_key="lock-1",
            parameters={},
            policy=RemediationMutationGuardPolicy(
                lock_ttl_seconds=60,
                cooldown_seconds=0,
                max_attempts_per_action_kind=5,
            ),
            now=now,
        )
        duplicate_holder = await service.evaluate(
            remediation_workflow_id=remediation.workflow_id,
            remediation_run_id=remediation.run_id,
            target_workflow_id=target.workflow_id,
            target_run_id=target.run_id,
            action_kind="workload.restart_helper_container",
            idempotency_key="lock-1",
            parameters={},
            policy=RemediationMutationGuardPolicy(
                lock_ttl_seconds=60,
                cooldown_seconds=0,
                max_attempts_per_action_kind=5,
            ),
            now=now,
        )
        expired_same_holder = await service.evaluate(
            remediation_workflow_id=remediation.workflow_id,
            remediation_run_id=remediation.run_id,
            target_workflow_id=target.workflow_id,
            target_run_id=target.run_id,
            action_kind="workload.restart_helper_container",
            idempotency_key="lock-expired-holder",
            parameters={},
            policy=RemediationMutationGuardPolicy(
                lock_ttl_seconds=60,
                cooldown_seconds=0,
                max_attempts_per_action_kind=5,
            ),
            now=now + timedelta(seconds=61),
        )
        conflict = await service.evaluate(
            remediation_workflow_id="mm:other-remediator",
            remediation_run_id="other-run",
            target_workflow_id=target.workflow_id,
            target_run_id=target.run_id,
            action_kind="workload.restart_helper_container",
            idempotency_key="lock-2",
            parameters={},
            policy=RemediationMutationGuardPolicy(
                lock_ttl_seconds=60,
                cooldown_seconds=0,
                max_attempts_per_action_kind=5,
            ),
            now=now + timedelta(seconds=62),
        )
        recovered = await service.evaluate(
            remediation_workflow_id="mm:other-remediator",
            remediation_run_id="other-run",
            target_workflow_id=target.workflow_id,
            target_run_id=target.run_id,
            action_kind="workload.restart_helper_container",
            idempotency_key="lock-3",
            parameters={},
            policy=RemediationMutationGuardPolicy(
                lock_ttl_seconds=60,
                cooldown_seconds=0,
                max_attempts_per_action_kind=5,
            ),
            now=now + timedelta(seconds=122),
        )
        await service.release_lock(first.lock.lock_id)
        lost = await service.evaluate(
            remediation_workflow_id=remediation.workflow_id,
            remediation_run_id=remediation.run_id,
            target_workflow_id=target.workflow_id,
            target_run_id=target.run_id,
            action_kind="workload.restart_helper_container",
            idempotency_key="lock-4",
            parameters={},
            policy=RemediationMutationGuardPolicy(
                lock_ttl_seconds=60,
                cooldown_seconds=0,
                max_attempts_per_action_kind=5,
            ),
            now=now + timedelta(seconds=62),
        )

        assert first.decision == "allowed"
        assert first.lock.status == "acquired"
        assert first.lock.created_at == now
        assert duplicate_holder.lock.lock_id == first.lock.lock_id
        assert expired_same_holder.lock.status == "recovered"
        assert expired_same_holder.lock.expires_at == now + timedelta(seconds=121)
        assert conflict.decision == "denied"
        assert conflict.reason == "mutation_lock_conflict"
        assert recovered.decision == "allowed"
        assert recovered.lock.status == "recovered"
        assert lost.decision == "denied"
        assert lost.reason == "mutation_lock_lost"

@pytest.mark.asyncio
async def test_remediation_mutation_guard_enforces_ledger_budgets_and_cooldowns(
    tmp_path, mock_client_adapter
):
    async with temporal_db(tmp_path) as session:
        target, remediation = await _create_target_and_remediation(
            session,
            mock_client_adapter,
            authority_mode="admin_auto",
        )
        service = RemediationMutationGuardService(session=session)
        now = datetime(2026, 4, 22, tzinfo=timezone.utc)
        policy = RemediationMutationGuardPolicy(
            max_actions_per_target=2,
            max_attempts_per_action_kind=2,
            cooldown_seconds=30,
            lock_ttl_seconds=1,
        )

        first = await service.evaluate(
            remediation_workflow_id=remediation.workflow_id,
            remediation_run_id=remediation.run_id,
            target_workflow_id=target.workflow_id,
            target_run_id=target.run_id,
            action_kind="workload.restart_helper_container",
            idempotency_key="ledger-1",
            parameters={"reason": "first"},
            policy=policy,
            now=now,
        )
        duplicate = await service.evaluate(
            remediation_workflow_id=remediation.workflow_id,
            remediation_run_id=remediation.run_id,
            target_workflow_id=target.workflow_id,
            target_run_id=target.run_id,
            action_kind="workload.restart_helper_container",
            idempotency_key="ledger-1",
            parameters={"reason": "first"},
            policy=policy,
            now=now + timedelta(seconds=1),
        )
        unsafe_reuse = await service.evaluate(
            remediation_workflow_id=remediation.workflow_id,
            remediation_run_id=remediation.run_id,
            target_workflow_id=target.workflow_id,
            target_run_id=target.run_id,
            action_kind="workload.restart_helper_container",
            idempotency_key="ledger-1",
            parameters={"reason": "changed"},
            policy=policy,
            now=now + timedelta(seconds=2),
        )
        cooldown = await service.evaluate(
            remediation_workflow_id=remediation.workflow_id,
            remediation_run_id=remediation.run_id,
            target_workflow_id=target.workflow_id,
            target_run_id=target.run_id,
            action_kind="workload.restart_helper_container",
            idempotency_key="ledger-2",
            parameters={"reason": "first"},
            policy=policy,
            now=now + timedelta(seconds=3),
        )
        second = await service.evaluate(
            remediation_workflow_id=remediation.workflow_id,
            remediation_run_id=remediation.run_id,
            target_workflow_id=target.workflow_id,
            target_run_id=target.run_id,
            action_kind="session.terminate",
            idempotency_key="ledger-3",
            parameters={"reason": "second"},
            policy=policy,
            now=now + timedelta(seconds=40),
        )
        exhausted = await service.evaluate(
            remediation_workflow_id=remediation.workflow_id,
            remediation_run_id=remediation.run_id,
            target_workflow_id=target.workflow_id,
            target_run_id=target.run_id,
            action_kind="session.terminate",
            idempotency_key="ledger-4",
            parameters={"reason": "third"},
            policy=policy,
            now=now + timedelta(seconds=80),
        )

        assert first.decision == "allowed"
        assert duplicate is first
        assert unsafe_reuse.decision == "denied"
        assert unsafe_reuse.reason == "idempotency_key_unsafe_reuse"
        assert cooldown.decision == "denied"
        assert cooldown.reason == "action_cooldown_active"
        assert second.decision == "allowed"
        assert exhausted.decision == "escalate"
        assert exhausted.reason == "action_budget_exhausted"

@pytest.mark.asyncio
async def test_remediation_mutation_guard_persists_locks_and_ledger_across_service_restart(
    tmp_path, mock_client_adapter
):
    async with temporal_db(tmp_path) as session:
        target, remediation = await _create_target_and_remediation(
            session,
            mock_client_adapter,
            authority_mode="admin_auto",
        )
        mock_client_adapter.start_workflow.side_effect = [
            SimpleNamespace(run_id="other-run")
        ]
        other = await TemporalExecutionService(
            session, client_adapter=mock_client_adapter
        ).create_execution(
            workflow_type="MoonMind.Run",
            owner_id=target.owner_id,
            title="Second remediator",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters={
                "task": {
                    "remediation": {
                        "target": {"workflowId": target.workflow_id},
                        "authorityMode": "admin_auto",
                    }
                }
            },
            idempotency_key=None,
        )
        now = datetime(2026, 4, 22, tzinfo=timezone.utc)
        policy = RemediationMutationGuardPolicy(
            lock_ttl_seconds=60,
            cooldown_seconds=0,
            max_attempts_per_action_kind=5,
        )

        first_service = RemediationMutationGuardService(session=session)
        first = await first_service.evaluate(
            remediation_workflow_id=remediation.workflow_id,
            remediation_run_id=remediation.run_id,
            target_workflow_id=target.workflow_id,
            target_run_id=target.run_id,
            action_kind="workload.restart_helper_container",
            idempotency_key="durable-ledger-1",
            parameters={"reason": "restart durable"},
            policy=policy,
            now=now,
        )

        restarted_service = RemediationMutationGuardService(session=session)
        replay = await restarted_service.evaluate(
            remediation_workflow_id=remediation.workflow_id,
            remediation_run_id=remediation.run_id,
            target_workflow_id=target.workflow_id,
            target_run_id=target.run_id,
            action_kind="workload.restart_helper_container",
            idempotency_key="durable-ledger-1",
            parameters={"reason": "restart durable"},
            policy=policy,
            now=now + timedelta(seconds=1),
        )
        conflict = await restarted_service.evaluate(
            remediation_workflow_id=other.workflow_id,
            remediation_run_id=other.run_id,
            target_workflow_id=target.workflow_id,
            target_run_id=target.run_id,
            action_kind="workload.restart_helper_container",
            idempotency_key="durable-lock-conflict",
            parameters={},
            policy=policy,
            now=now + timedelta(seconds=2),
        )

        assert first.decision == "allowed"
        assert first.lock.status == "acquired"
        assert replay.decision == "allowed"
        assert replay.ledger.duplicate is True
        assert replay.lock.lock_id == first.lock.lock_id
        assert conflict.decision == "denied"
        assert conflict.reason == "mutation_lock_conflict"
        assert conflict.lock.lock_id == first.lock.lock_id

@pytest.mark.asyncio
async def test_remediation_mutation_guard_persists_released_lock_across_service_restart(
    tmp_path, mock_client_adapter
):
    async with temporal_db(tmp_path) as session:
        target, remediation = await _create_target_and_remediation(
            session,
            mock_client_adapter,
            authority_mode="admin_auto",
        )
        now = datetime(2026, 4, 22, tzinfo=timezone.utc)
        policy = RemediationMutationGuardPolicy(
            lock_ttl_seconds=60,
            cooldown_seconds=0,
            max_attempts_per_action_kind=5,
        )

        first_service = RemediationMutationGuardService(session=session)
        first = await first_service.evaluate(
            remediation_workflow_id=remediation.workflow_id,
            remediation_run_id=remediation.run_id,
            target_workflow_id=target.workflow_id,
            target_run_id=target.run_id,
            action_kind="workload.restart_helper_container",
            idempotency_key="release-durable-1",
            parameters={},
            policy=policy,
            now=now,
        )
        await first_service.release_lock(first.lock.lock_id)
        await session.commit()

        link = await session.get(
            TemporalExecutionRemediationLink,
            remediation.workflow_id,
        )
        restarted_service = RemediationMutationGuardService(session=session)
        lost = await restarted_service.evaluate(
            remediation_workflow_id=remediation.workflow_id,
            remediation_run_id=remediation.run_id,
            target_workflow_id=target.workflow_id,
            target_run_id=target.run_id,
            action_kind="workload.restart_helper_container",
            idempotency_key="release-durable-holder",
            parameters={},
            policy=policy,
            now=now + timedelta(seconds=1),
        )
        other = await restarted_service.evaluate(
            remediation_workflow_id="mm:other-remediator",
            remediation_run_id="other-run",
            target_workflow_id=target.workflow_id,
            target_run_id=target.run_id,
            action_kind="workload.restart_helper_container",
            idempotency_key="release-durable-other",
            parameters={},
            policy=policy,
            now=now + timedelta(seconds=2),
        )

        assert link is not None
        assert link.active_lock_scope is None
        assert link.active_lock_holder is None
        assert link.mutation_guard_lock_state["released"] is True
        assert lost.decision == "denied"
        assert lost.reason == "mutation_lock_lost"
        assert other.decision == "allowed"
        assert other.lock.status == "acquired"

@pytest.mark.asyncio
async def test_remediation_mutation_guard_rejects_nested_and_changed_targets(
    tmp_path, mock_client_adapter
):
    async with temporal_db(tmp_path) as session:
        target, remediation = await _create_target_and_remediation(
            session,
            mock_client_adapter,
            authority_mode="admin_auto",
        )
        service = RemediationMutationGuardService(session=session)
        now = datetime(2026, 4, 22, tzinfo=timezone.utc)

        self_target = await service.evaluate(
            remediation_workflow_id=remediation.workflow_id,
            remediation_run_id=remediation.run_id,
            target_workflow_id=remediation.workflow_id,
            target_run_id=remediation.run_id,
            action_kind="workload.restart_helper_container",
            idempotency_key="nested-1",
            parameters={},
            policy=RemediationMutationGuardPolicy(),
            now=now,
            target_is_remediation=True,
        )
        nested = await service.evaluate(
            remediation_workflow_id=remediation.workflow_id,
            remediation_run_id=remediation.run_id,
            target_workflow_id="mm:other-remediation",
            target_run_id="other-run",
            action_kind="workload.restart_helper_container",
            idempotency_key="nested-2",
            parameters={},
            policy=RemediationMutationGuardPolicy(),
            now=now,
            target_is_remediation=True,
        )
        allowed_nested = await service.evaluate(
            remediation_workflow_id=remediation.workflow_id,
            remediation_run_id=remediation.run_id,
            target_workflow_id="mm:other-remediation",
            target_run_id="other-run",
            action_kind="workload.restart_helper_container",
            idempotency_key="nested-3",
            parameters={},
            policy=RemediationMutationGuardPolicy(allow_nested_remediation=True),
            now=now,
            target_is_remediation=True,
        )
        changed = await service.evaluate(
            remediation_workflow_id=remediation.workflow_id,
            remediation_run_id=remediation.run_id,
            target_workflow_id=target.workflow_id,
            target_run_id=target.run_id,
            action_kind="workload.restart_helper_container",
            idempotency_key="fresh-1",
            parameters={},
            policy=RemediationMutationGuardPolicy(target_change_policy="rediagnose"),
            now=now + timedelta(seconds=10),
            target_freshness={
                "pinnedRunId": target.run_id,
                "currentRunId": "new-run",
                "state": "executing",
                "summary": "new summary",
                "sessionIdentity": "session-2",
                "targetRunChanged": True,
            },
        )
        material_drift = await service.evaluate(
            remediation_workflow_id=remediation.workflow_id,
            remediation_run_id=remediation.run_id,
            target_workflow_id=target.workflow_id,
            target_run_id=target.run_id,
            action_kind="workload.restart_helper_container",
            idempotency_key="fresh-state-drift",
            parameters={},
            policy=RemediationMutationGuardPolicy(target_change_policy="escalate"),
            now=now + timedelta(seconds=11),
            target_freshness={
                "pinnedRunId": target.run_id,
                "currentRunId": target.run_id,
                "pinnedState": "executing",
                "state": "failed",
                "pinnedSummary": "old summary",
                "summary": "old summary",
                "pinnedSessionIdentity": "session-1",
                "sessionIdentity": "session-1",
                "targetRunChanged": False,
            },
        )
        unavailable = await service.evaluate(
            remediation_workflow_id=remediation.workflow_id,
            remediation_run_id=remediation.run_id,
            target_workflow_id=target.workflow_id,
            target_run_id=target.run_id,
            action_kind="workload.restart_helper_container",
            idempotency_key="fresh-2",
            parameters={},
            policy=RemediationMutationGuardPolicy(),
            now=now + timedelta(seconds=20),
            target_freshness=None,
            require_target_freshness=True,
        )

        assert self_target.decision == "denied"
        assert self_target.reason == "self_target_denied"
        assert nested.decision == "denied"
        assert nested.reason == "nested_remediation_denied"
        assert allowed_nested.decision == "allowed"
        assert changed.decision == "rediagnose"
        assert changed.reason == "target_materially_changed"
        assert material_drift.decision == "escalate"
        assert material_drift.reason == "target_materially_changed"
        assert unavailable.decision == "denied"
        assert unavailable.reason == "target_health_unavailable"

@pytest.mark.asyncio
async def test_remediation_mutation_guard_serialization_redacts_sensitive_values(
    tmp_path, mock_client_adapter
):
    async with temporal_db(tmp_path) as session:
        target, remediation = await _create_target_and_remediation(
            session,
            mock_client_adapter,
            authority_mode="admin_auto",
        )
        service = RemediationMutationGuardService(session=session)
        result = await service.evaluate(
            remediation_workflow_id=remediation.workflow_id,
            remediation_run_id=remediation.run_id,
            target_workflow_id=target.workflow_id,
            target_run_id=target.run_id,
            action_kind="workload.restart_helper_container",
            idempotency_key="redact-guard",
            parameters={
                "token": "secret-token-value",
                "path": "/work/agent_jobs/mm:secret/repo/.env",
                "url": "https://example.test/object?X-Amz-Signature=secret",
            },
            policy=RemediationMutationGuardPolicy(),
            now=datetime(2026, 4, 22, tzinfo=timezone.utc),
        )

        payload = result.to_dict()
        serialized = json.dumps(payload, sort_keys=True)
        assert payload["schemaVersion"] == "v1"
        assert payload["decision"] == "allowed"
        assert payload["lock"]["createdAt"] == "2026-04-22T00:00:00Z"
        assert "secret-token-value" not in serialized
        assert "/work/agent_jobs" not in serialized
        assert "X-Amz-Signature" not in serialized
