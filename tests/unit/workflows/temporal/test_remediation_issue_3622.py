"""Exact-result verification through production persistence and readers."""

import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker
from temporalio import activity as temporal_activity

from api_service.api.routers.executions import _get_service, router
from api_service.db import models
from api_service.db.base import get_async_session
from api_service.services.remediation_actions import TemporalRemediationControlPlane
from moonmind.schemas.agent_runtime_models import AgentRunResult, ManagedRunRecord
from moonmind.workflows.temporal import (
    LocalTemporalArtifactStore,
    TemporalArtifactRepository,
    TemporalArtifactService,
)
from moonmind.workflows.temporal.activity_runtime import TemporalAgentRuntimeActivities
from moonmind.workflows.temporal.artifacts import ExecutionRef
from moonmind.workflows.temporal.remediation_actions import (
    RemediationActionAuthorityService,
    RemediationMutationGuardPolicy,
    RemediationMutationGuardService,
)
from moonmind.workflows.temporal.remediation_context import RemediationContextBuilder
from moonmind.workflows.temporal.remediation_tools import (
    MoonMindControlPlaneRemediationActionExecutor,
    RemediationEvidenceToolService,
)
from moonmind.workflows.temporal.remediation_verification import (
    CanonicalRecordEvidenceReader,
    RemediationVerificationPhase,
    verification_contract_for,
)
from moonmind.workflows.temporal.runtime.store import ManagedRunStore
from moonmind.workflows.temporal.service import TemporalExecutionService
from moonmind.workflows.temporal.workflows.checkpoint_branch_turn import (
    build_branch_turn_verification_handoff,
)
from tests.unit.api.routers.test_checkpoint_branch_apis import (
    _override_user_dependencies,
)
from tests.unit.workflows.temporal.test_remediation_context import (
    _admin_permissions,
    _admin_profile,
    _create_target_and_remediation,
    _fast_verification_phase,
    _read_artifact_json,
    temporal_db,
)

pytestmark = pytest.mark.asyncio


@pytest.fixture
def mock_client_adapter():
    adapter = MagicMock()
    for operation in (
        "start_workflow",
        "describe_workflow",
        "update_workflow",
        "signal_workflow",
        "cancel_workflow",
        "terminate_workflow",
    ):
        setattr(adapter, operation, AsyncMock())
    return adapter


async def test_production_reader_rejects_unrelated_latest_run(
    tmp_path, mock_client_adapter
):
    async with temporal_db(tmp_path) as session:
        target, _ = await _create_target_and_remediation(session, mock_client_adapter)
        target = await session.get(
            models.TemporalExecutionCanonicalRecord, target.workflow_id
        )
        target.run_id = "unrelated-later-success"
        target.state = models.MoonMindWorkflowState.COMPLETED
        await session.commit()
        snapshot = await CanonicalRecordEvidenceReader(session).read_target_evidence(
            contract=verification_contract_for("execution.pause"),
            workflow_id=target.workflow_id,
            pinned_run_id="target-run",
            stage="immediate_after",
        )
        assert not snapshot.available
        assert "run" in snapshot.degraded_reason.lower()


@pytest.mark.parametrize("missing", [True, False])
async def test_missing_or_wrong_result_cannot_use_source_success(
    tmp_path, mock_client_adapter, missing
):
    async with temporal_db(tmp_path) as session:
        target, remediation, artifacts, authority, guard = await _prepare(
            session, tmp_path, mock_client_adapter, "execution.start_fresh_rerun"
        )
        target.state = models.MoonMindWorkflowState.COMPLETED
        await session.commit()
        executor = AcceptedEffect(
            {
                "workflowId": "absent-result" if missing else target.workflow_id,
                "runId": "admitted-result-run",
            }
        )
        result = await RemediationEvidenceToolService(
            session=session,
            artifact_service=artifacts,
            action_executor=executor,
            verification_phase=_fast_verification_phase(session),
        ).execute_action(
            remediation_workflow_id=remediation.workflow_id,
            authority_result=authority,
            guard_result=guard,
            principal="service:test",
            admitted_principal="service:remediation-context",
        )
        assert result["verification"]["outcome"] == "evidence_unavailable"
        assert result["verification"]["pending"]
        assert executor.effects == 1


@pytest.mark.parametrize(
    "lost_artifact", ["remediation.action_result", "remediation.verification"]
)
@pytest.mark.parametrize("advance_remediation_run", [False, True])
async def test_lost_receipt_acknowledgment_reuses_effect_and_exact_verdict(
    tmp_path, mock_client_adapter, monkeypatch, lost_artifact, advance_remediation_run
):
    async with temporal_db(tmp_path) as session:
        target, remediation, artifacts, authority, guard = await _prepare(
            session, tmp_path, mock_client_adapter, "execution.start_fresh_rerun"
        )
        candidate = models.TemporalExecutionCanonicalRecord(
            workflow_id="repair-result",
            run_id="accepted-result-run",
            entry=target.entry,
            workflow_type=target.workflow_type,
            state=models.MoonMindWorkflowState.COMPLETED,
        )
        session.add(candidate)
        target.state = models.MoonMindWorkflowState.FAILED
        await session.commit()
        executor = AcceptedEffect(
            {"workflowId": candidate.workflow_id, "runId": candidate.run_id}
        )
        kwargs = dict(
            remediation_workflow_id=remediation.workflow_id,
            authority_result=authority,
            guard_result=guard,
            principal="service:test",
            admitted_principal="service:remediation-context",
        )
        tools = RemediationEvidenceToolService(
            session=session,
            artifact_service=artifacts,
            action_executor=executor,
            verification_phase=_fast_verification_phase(session),
        )
        publish = tools._lifecycle_publisher.publish_json_artifact

        async def lose_ack(**kwargs):
            artifact = await publish(**kwargs)
            if kwargs["artifact_type"] == lost_artifact:
                await session.commit()
                raise RuntimeError("lost acknowledgment after durable publication")
            return artifact

        monkeypatch.setattr(
            tools._lifecycle_publisher, "publish_json_artifact", lose_ack
        )
        with pytest.raises(RuntimeError, match="lost acknowledgment"):
            await tools.execute_action(**kwargs)
        if advance_remediation_run:
            current = await session.get(
                models.TemporalExecutionCanonicalRecord, remediation.workflow_id
            )
            current.run_id = "successor-remediation-run"
            await session.commit()
        if lost_artifact == "remediation.verification":
            candidate.run_id = "unrelated-new-success"
            await session.commit()
        result = await RemediationEvidenceToolService(
            session=session,
            artifact_service=artifacts,
            action_executor=executor,
            verification_phase=_fast_verification_phase(session),
        ).execute_action(**kwargs)
        assert executor.effects == 1
        assert result["verification"]["outcome"] == "verified_resolved"
        assert (
            result["verification"]["resultingIdentity"]["runId"]
            == "accepted-result-run"
        )
        payload = await _read_artifact_json(
            artifacts, result["artifactRefs"]["verification"]
        )
        assert (
            payload["resultingIdentity"] == result["verification"]["resultingIdentity"]
        )


@pytest.mark.parametrize("interruption", ["denied", "canceled"])
async def test_observation_interruption_retains_accepted_obligation(
    tmp_path, mock_client_adapter, interruption
):
    async with temporal_db(tmp_path) as session:
        target, remediation, artifacts, authority, guard = await _prepare(
            session, tmp_path, mock_client_adapter, "execution.start_fresh_rerun"
        )
        executor = AcceptedEffect(
            {"workflowId": target.workflow_id, "runId": target.run_id}
        )
        reader = CanonicalRecordEvidenceReader(session)
        if interruption == "denied":
            reader.read_result_evidence = AsyncMock(
                side_effect=PermissionError("private error")
            )
        phase = RemediationVerificationPhase(
            reader=reader,
            is_canceled=lambda: interruption == "canceled",
            max_poll_cap=0,
        )
        result = await RemediationEvidenceToolService(
            session=session,
            artifact_service=artifacts,
            action_executor=executor,
            verification_phase=phase,
        ).execute_action(
            remediation_workflow_id=remediation.workflow_id,
            authority_result=authority,
            guard_result=guard,
            principal="service:test",
            admitted_principal="service:remediation-context",
        )
        assert result["verification"]["outcome"] == (
            "verification_failed" if interruption == "denied" else "canceled"
        )
        assert result["verification"]["pending"]
        assert "private error" not in result["verification"]["reason"]
        assert executor.effects == 1


async def _prepare(session, tmp_path, mock_client_adapter, action_kind):
    target, remediation = await _create_target_and_remediation(
        session, mock_client_adapter, authority_mode="admin_auto"
    )
    artifacts = TemporalArtifactService(
        TemporalArtifactRepository(session),
        store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
    )
    await RemediationContextBuilder(
        session=session, artifact_service=artifacts
    ).build_context(remediation_workflow_id=remediation.workflow_id)
    parameters = {"reason": "controlled repair"}
    if action_kind.startswith("checkpoint_branch"):
        parameters.update(
            remediationWorkflowId=remediation.workflow_id,
            remediationContextRef="artifact://context",
            checkpointRef="artifact://base",
            instructionRef="artifact://instructions",
            instructionDigest="sha256:instructions",
            repository="MoonLadderStudios/MoonMind",
            baseBranch="main",
            baseCommit="a" * 40,
            logicalStepId="repair",
            executionOrdinal=1,
        )
    authority = await RemediationActionAuthorityService(
        session=session
    ).evaluate_action_request(
        remediation_workflow_id=remediation.workflow_id,
        action_kind=action_kind,
        parameters=parameters,
        dry_run=False,
        idempotency_key="repair-3622",
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
        idempotency_key="repair-3622",
        parameters=parameters,
        policy=RemediationMutationGuardPolicy(cooldown_seconds=0),
        now=datetime.now(timezone.utc),
    )
    target = await session.get(
        models.TemporalExecutionCanonicalRecord, target.workflow_id
    )
    return target, remediation, artifacts, authority.to_dict(), guard.to_dict()


class AcceptedEffect:
    def __init__(self, identity):
        self.identity = identity
        self.effects = 0

    async def execute_action(self, **kwargs):
        self.effects += 1
        return {
            "status": "accepted",
            "verificationRequired": True,
            "verificationHint": "Read the accepted result only.",
            "resultingIdentity": self.identity,
            "afterEvidenceRefs": [],
        }


async def test_pending_rerun_resumes_without_repeating_accepted_effect(
    tmp_path, mock_client_adapter
):
    async with temporal_db(tmp_path) as session:
        target, remediation, artifacts, authority, guard = await _prepare(
            session, tmp_path, mock_client_adapter, "execution.start_fresh_rerun"
        )
        candidate = models.TemporalExecutionCanonicalRecord(
            workflow_id="repair-result",
            run_id="accepted-result-run",
            namespace=target.namespace,
            workflow_type=target.workflow_type,
            owner_id=target.owner_id,
            owner_type=target.owner_type,
            entry=target.entry,
            state=models.MoonMindWorkflowState.EXECUTING,
        )
        session.add(candidate)
        target.state = models.MoonMindWorkflowState.FAILED
        await session.commit()
        executor = AcceptedEffect(
            {"workflowId": candidate.workflow_id, "runId": candidate.run_id}
        )

        # Use the production action adapter; only external execution creation
        # is controlled. Its returned identity must reach the real reader.
        class ExecutionBoundary:
            async def create_fresh_rerun_execution(self, **kwargs):
                executor.effects += 1
                return {
                    "accepted": True,
                    "workflow_id": candidate.workflow_id,
                    "run_id": candidate.run_id,
                }

        control = TemporalRemediationControlPlane(execution_service=ExecutionBoundary())
        action_executor = MoonMindControlPlaneRemediationActionExecutor(
            adapters=control.handlers()
        )
        kwargs = dict(
            remediation_workflow_id=remediation.workflow_id,
            authority_result=authority,
            guard_result=guard,
            principal="service:test",
            admitted_principal="service:remediation-context",
        )
        result = await RemediationEvidenceToolService(
            session=session,
            artifact_service=artifacts,
            action_executor=action_executor,
            verification_phase=_fast_verification_phase(session),
        ).execute_action(**kwargs)
        assert result["verification"]["pending"] is True
        assert result["verification"]["outcome"] is None
        pending_ref = result["artifactRefs"]["verification"]
        source_workflow_id = target.workflow_id
        await session.refresh(remediation)
        # A new service/session owner resumes after worker/API interruption.
        session.expire_all()
        candidate = await session.get(
            models.TemporalExecutionCanonicalRecord, "repair-result"
        )
        # Existing durable terminal notification completes verification even
        # after the caller disappeared; no browser or second tool call needed.
        await TemporalExecutionService(
            session, client_adapter=mock_client_adapter
        ).record_terminal_state(workflow_id=candidate.workflow_id, state="completed")
        result = await RemediationEvidenceToolService(
            session=session,
            artifact_service=artifacts,
            action_executor=action_executor,
            verification_phase=_fast_verification_phase(session),
        ).execute_action(**kwargs)
        assert executor.effects == 1
        assert result["verification"]["outcome"] == "verified_resolved"
        assert (
            result["verification"]["resultingIdentity"]["runId"]
            == "accepted-result-run"
        )
        assert result["artifactRefs"]["verification"] != pending_ref
        payload = await _read_artifact_json(
            artifacts, result["artifactRefs"]["verification"]
        )
        assert payload["targetStates"]["before"]["state"] == "failed"
        assert (
            payload["targetStates"]["immediateAfter"]["workflowId"] == "repair-result"
        )
        link = await session.get(
            models.TemporalExecutionRemediationLink, kwargs["remediation_workflow_id"]
        )
        assert link.verification_outcome == "verified_resolved"
        source = await session.get(
            models.TemporalExecutionCanonicalRecord, source_workflow_id
        )
        assert source.state == models.MoonMindWorkflowState.FAILED


@pytest.mark.parametrize(
    "report_case",
    [
        "exact",
        "workflow",
        "run",
        "branch",
        "turn",
        "step",
        "head_ref",
        "head_digest",
        "head_version",
        "producer",
        "verdict",
        "missing",
        "contamination",
    ],
)
async def test_exact_branch_report_finishes_pending_verification(
    tmp_path, mock_client_adapter, report_case, monkeypatch
):
    async with temporal_db(tmp_path) as session:
        target, remediation, artifacts, authority, guard = await _prepare(
            session,
            tmp_path,
            mock_client_adapter,
            "checkpoint_branch.create_from_remediation_context",
        )
        target.state = models.MoonMindWorkflowState.FAILED
        branch = models.WorkflowCheckpointBranch(
            branch_id="accepted-branch",
            workflow_id=target.workflow_id,
            root_workflow_id=target.workflow_id,
            label="Accepted repair",
            runtime_context_policy="fresh_context",
            source_run_id=target.run_id,
            source_checkpoint_boundary="after_execution",
            source_checkpoint_ref="artifact://base",
            source_checkpoint_digest="sha256:base",
            workspace_policy="continue_from_previous_execution",
            remediation_loop_id="repair-loop",
            remediation_head_status="candidate",
            current_head_step_execution_id="accepted-step",
            current_head_checkpoint_ref="artifact://candidate",
            current_head_checkpoint_digest="sha256:candidate",
            current_head_version=2,
        )
        turn = models.WorkflowCheckpointBranchTurn(
            branch_turn_id="accepted-turn",
            branch_id=branch.branch_id,
            source_checkpoint_ref="artifact://base",
            instruction_ref="artifact://instructions",
            instruction_digest="sha256:instructions",
            workspace_policy=branch.workspace_policy,
            created_step_execution_id="accepted-step",
            idempotency_key="accepted-turn",
            status="checking",
            completed_at=datetime.now(timezone.utc),
            diagnostics={
                "verificationHandoff": build_branch_turn_verification_handoff(
                    branch_id=branch.branch_id,
                    branch_turn_id="accepted-turn",
                    agent_result_ref="artifact://compute",
                    diagnostics_ref=None,
                    checkpoint_ref=branch.current_head_checkpoint_ref,
                    checkpoint_digest=branch.current_head_checkpoint_digest,
                    terminal_disposition="delivered_verification_pending",
                    delivery_outcome="succeeded",
                    source_namespace=target.namespace,
                    source_workflow_id=target.workflow_id,
                    source_run_id=target.run_id,
                    verification_pending=True,
                )
            },
        )
        session.add_all([branch, turn])
        await session.commit()
        executor = AcceptedEffect(
            {
                "workflowId": target.workflow_id,
                "runId": target.run_id,
                "branchId": branch.branch_id,
                "branchTurnId": turn.branch_turn_id,
                "stepExecutionId": turn.created_step_execution_id,
            }
        )
        kwargs = dict(
            remediation_workflow_id=remediation.workflow_id,
            authority_result=authority,
            guard_result=guard,
            principal="service:test",
            admitted_principal="service:remediation-context",
        )
        tools = RemediationEvidenceToolService(
            session=session,
            artifact_service=artifacts,
            action_executor=executor,
            verification_phase=_fast_verification_phase(session),
        )
        pending = await tools.execute_action(**kwargs)
        assert pending["verification"]["pending"]
        assert pending["verification"]["outcome"] is None
        # The production verifier owner accepts and binds this report to the
        # exact candidate; its durable notification resumes the saved action.
        evidence = dict(
            inputHeadRef="artifact://candidate",
            inputHeadDigest="sha256:candidate",
            inputHeadVersion=2,
            preVerificationWorkspaceDigest="sha256:candidate",
            postVerificationWorkspaceDigest="sha256:candidate",
            verdict="FULLY_IMPLEMENTED",
        )
        report_payload = {
            "workflowId": target.workflow_id,
            "runId": target.run_id,
            "branchId": branch.branch_id,
            "branchTurnId": turn.branch_turn_id,
            "stepExecutionId": turn.created_step_execution_id,
            "verification": evidence,
            "verdict": "FULLY_IMPLEMENTED",
        }
        outer_fields = {
            "workflow": "workflowId",
            "run": "runId",
            "branch": "branchId",
            "turn": "branchTurnId",
            "step": "stepExecutionId",
        }
        if report_case in outer_fields:
            report_payload[outer_fields[report_case]] = "unrelated"
        if report_case in {"head_ref", "head_digest", "head_version"}:
            field = {
                "head_ref": "inputHeadRef",
                "head_digest": "inputHeadDigest",
                "head_version": "inputHeadVersion",
            }[report_case]
            evidence[field] = (
                1
                if report_case == "head_version"
                else "artifact://wrong" if report_case == "head_ref" else "sha256:wrong"
            )
        if report_case == "verdict":
            evidence["verdict"] = "unknown-verdict"
        if report_case == "contamination":
            evidence["postVerificationWorkspaceDigest"] = "sha256:changed"
        if report_case == "producer":
            report, _ = await artifacts.create(
                principal="service:remediation-context",
                content_type="application/json",
                link=ExecutionRef(
                    namespace=target.namespace,
                    workflow_id=target.workflow_id,
                    run_id=target.run_id,
                    link_type="output.moonspec_verify",
                ),
                metadata_json={"producer": "activity:agent_runtime.publish_artifacts"},
            )
            await artifacts.write_complete(
                artifact_id=report.artifact_id,
                payload=json.dumps(report_payload).encode(),
                principal="service:remediation-context",
            )
            report_ref = f"artifact://{report.artifact_id}"
        else:
            workspace = tmp_path / "workspace"
            report_path = workspace / "var/artifacts/moonspec-verify/final.json"
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(json.dumps(report_payload), encoding="utf-8")
            run_store = ManagedRunStore(tmp_path / "runs")
            run_store.save(
                ManagedRunRecord(
                    runId="objective-verifier",
                    agentId="codex_cli",
                    runtimeId="codex_cli",
                    status="completed",
                    startedAt=datetime.now(timezone.utc),
                    workspacePath=str(workspace),
                )
            )
            activities = TemporalAgentRuntimeActivities(
                artifact_service=artifacts, run_store=run_store
            )
            monkeypatch.setattr(
                activities,
                "execution_notify_completion",
                AsyncMock(return_value={"status": "skipped"}),
            )
            source_namespace, source_workflow_id, source_run_id = (
                target.namespace,
                target.workflow_id,
                target.run_id,
            )
            monkeypatch.setattr(
                temporal_activity,
                "info",
                lambda: SimpleNamespace(
                    namespace=source_namespace,
                    workflow_id=source_workflow_id,
                    workflow_run_id=source_run_id,
                ),
            )
            published = await activities.agent_runtime_publish_artifacts(
                AgentRunResult(
                    summary="Verifier finished.",
                    metadata={
                        "agentRunId": "objective-verifier",
                        "verify_artifact_path": "var/artifacts/moonspec-verify/final.json",
                    },
                )
            )
            report_ref = (
                "artifact://" + published.metadata["moonSpecVerify"]["gateResultRef"]
            )
        await session.commit()
        await session.refresh(target)
        app = FastAPI()
        app.include_router(router)
        mock_client_adapter.describe_workflow.return_value = None
        monkeypatch.setattr(
            "api_service.api.routers.executions.get_temporal_artifact_service",
            lambda db: TemporalArtifactService(
                TemporalArtifactRepository(db),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            ),
        )
        _override_user_dependencies(
            app,
            SimpleNamespace(id=target.owner_id, email="fixture@example.test", roles=[]),
        )
        async with async_sessionmaker(
            session.bind, expire_on_commit=False
        )() as api_session:

            async def db_session():
                yield api_session

            app.dependency_overrides[get_async_session] = db_session
            app.dependency_overrides[_get_service] = lambda: TemporalExecutionService(
                api_session, client_adapter=mock_client_adapter
            )
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://fixture"
            ) as client:
                response = await client.post(
                    f"/api/executions/{target.workflow_id}/checkpoint-branches/{branch.branch_id}/verification",
                    json={
                        "verifierArtifactRef": (
                            "artifact://missing"
                            if report_case == "missing"
                            else report_ref
                        )
                    },
                )
        await session.refresh(branch)
        await session.refresh(target)
        if report_case not in {"exact", "contamination"}:
            assert response.status_code == 409, response.text
            await session.refresh(branch)
            assert branch.latest_verification_ref is None
            assert executor.effects == 1
            return
        assert response.status_code == 200, response.text
        result = await tools.execute_action(**kwargs)
        assert executor.effects == 1
        assert result["verification"]["outcome"] == (
            "still_failed" if report_case == "contamination" else "verified_resolved"
        )
        identity = result["verification"]["resultingIdentity"]
        assert identity["branchTurnId"] == "accepted-turn"
        assert identity["checkpointRef"] == "artifact://candidate"
        assert identity["headVersion"] == 2
        assert identity["verifierArtifactRef"] == report_ref
        assert target.state == models.MoonMindWorkflowState.FAILED
        payload = await _read_artifact_json(
            artifacts, result["artifactRefs"]["verification"]
        )
        assert payload["resultingIdentity"] == identity

        # Another head/turn's success cannot be reused for this action.
        branch.current_head_step_execution_id = "unrelated-step"
        await session.commit()
        snapshot = await CanonicalRecordEvidenceReader(session).read_result_evidence(
            contract=verification_contract_for(authority["request"]["actionKind"]),
            workflow_id=target.workflow_id,
            pinned_run_id=target.run_id,
            stage="immediate_after",
            action_result={"resultingIdentity": executor.identity},
        )
        assert not snapshot.available
