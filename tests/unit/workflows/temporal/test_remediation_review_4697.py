"""Production-owner regression cases for PR #4697 review feedback."""

import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from api_service.db import models
from api_service.services.checkpoint_branch_service import CheckpointBranchService
from moonmind.workflows.temporal.artifacts import TemporalArtifactService
from moonmind.workflows.temporal.remediation_context import (
    RemediationLifecyclePublisher,
)
from moonmind.workflows.temporal.remediation_tools import RemediationEvidenceToolService
from moonmind.workflows.temporal.remediation_verification import (
    CanonicalRecordEvidenceReader,
    RemediationVerificationPhase,
)
from moonmind.workflows.temporal.service import TemporalExecutionService
from tests.unit.workflows.temporal.test_remediation_context import (
    _fast_verification_phase,
    temporal_db,
)
from tests.unit.workflows.temporal.test_remediation_issue_3622 import (  # noqa: F401
    mock_client_adapter as imported_client_adapter,
)
from tests.unit.workflows.temporal.test_remediation_issue_3622 import (
    AcceptedEffect,
    _prepare,
)

@pytest.fixture
def mock_client_adapter(imported_client_adapter):  # noqa: F811 -- pytest fixture dependency
    return imported_client_adapter


pytestmark = pytest.mark.asyncio


def _action_kwargs(remediation, authority, guard):
    return dict(
        remediation_workflow_id=remediation.workflow_id,
        authority_result=authority,
        guard_result=guard,
        principal="service:test",
        admitted_principal="service:remediation-context",
    )


@pytest.mark.parametrize("outcome", ["failed", "canceled", "blocked"])
async def test_branch_terminal_owner_finishes_pending_action(
    tmp_path, mock_client_adapter, outcome
):
    async with temporal_db(tmp_path) as session:
        target, remediation, artifacts, authority, guard = await _prepare(
            session,
            tmp_path,
            mock_client_adapter,
            "checkpoint_branch.create_from_remediation_context",
        )
        branch = models.WorkflowCheckpointBranch(
            branch_id="terminal-branch",
            workflow_id=target.workflow_id,
            source_run_id=target.run_id,
            source_checkpoint_boundary="after_execution",
            source_checkpoint_ref="artifact://base",
            source_checkpoint_digest="sha256:base",
            workspace_policy="continue_from_previous_execution",
        )
        turn = models.WorkflowCheckpointBranchTurn(
            branch_turn_id="terminal-turn",
            branch_id=branch.branch_id,
            source_checkpoint_ref="artifact://base",
            instruction_ref="artifact://instruction",
            instruction_digest="sha256:instruction",
            workspace_policy=branch.workspace_policy,
            created_step_execution_id="terminal-step",
            idempotency_key="terminal-turn",
            status="running",
        )
        session.add_all([branch, turn])
        await session.commit()
        executor = AcceptedEffect(
            dict(
                workflowId=target.workflow_id,
                runId=target.run_id,
                branchId=branch.branch_id,
                branchTurnId=turn.branch_turn_id,
                stepExecutionId=turn.created_step_execution_id,
            )
        )
        tools = RemediationEvidenceToolService(
            session=session,
            artifact_service=artifacts,
            action_executor=executor,
            verification_phase=_fast_verification_phase(session),
        )
        kwargs = _action_kwargs(remediation, authority, guard)
        assert (await tools.execute_action(**kwargs))["verification"]["pending"]
        terminal_kwargs = dict(
            workflow_id=target.workflow_id,
            branch_id=branch.branch_id,
            branch_turn_id=turn.branch_turn_id,
            outcome=outcome,
            agent_result_ref="artifact://result",
            diagnostics_ref="artifact://diagnostics",
        )
        await CheckpointBranchService(session).finalize_turn_execution(
            **terminal_kwargs
        )
        await session.commit()
        link = await session.get(
            models.TemporalExecutionRemediationLink, remediation.workflow_id
        )
        await session.refresh(link)
        saved = link.mutation_guard_ledger_state["entries"][
            authority["request"]["actionId"]
        ]["execution"]
        assert saved["verificationState"] == "complete"
        assert (
            saved["verificationResponse"]["verification"]["outcome"] == "still_failed"
        )
        await CheckpointBranchService(session).finalize_turn_execution(
            **terminal_kwargs
        )
        assert executor.effects == 1


@pytest.mark.parametrize("terminal_retry", [False, True])
async def test_resume_outage_preserves_terminal_fanout_and_projection(
    tmp_path, mock_client_adapter, monkeypatch, terminal_retry
):
    async with temporal_db(tmp_path) as session:
        target, remediation, artifacts, authority, guard = await _prepare(
            session, tmp_path, mock_client_adapter, "execution.start_fresh_rerun"
        )
        executor = AcceptedEffect(
            dict(workflowId=target.workflow_id, runId=target.run_id)
        )
        tools = RemediationEvidenceToolService(
            session=session,
            artifact_service=artifacts,
            action_executor=executor,
            verification_phase=_fast_verification_phase(session),
        )
        assert (
            await tools.execute_action(**_action_kwargs(remediation, authority, guard))
        )["verification"]["pending"]
        if terminal_retry:
            target.state = models.MoonMindWorkflowState.COMPLETED
            await session.commit()
        service = TemporalExecutionService(session, client_adapter=mock_client_adapter)
        mock_client_adapter.start_workflow.side_effect = None
        mock_client_adapter.start_workflow.return_value = type(
            "Started", (), {"run_id": "dependent-run"}
        )()
        dependent = await service.create_execution(
            workflow_type="MoonMind.UserWorkflow",
            owner_id=target.owner_id,
            title="Unrelated dependent",
            input_artifact_ref=None,
            plan_artifact_ref=None,
            manifest_artifact_ref=None,
            failure_policy=None,
            initial_parameters={"workflow": {"dependsOn": [target.workflow_id]}},
            idempotency_key=None,
        )
        dependent_workflow_id = dependent.workflow_id
        mock_client_adapter.signal_workflow.reset_mock()
        sync = AsyncMock(wraps=service._sync_projection_best_effort)
        monkeypatch.setattr(service, "_sync_projection_best_effort", sync)
        read = TemporalArtifactService.read

        async def outage(self, **kwargs):
            if kwargs.get("principal") == "service:remediation-lifecycle":
                raise OSError("controlled artifact-read outage")
            return await read(self, **kwargs)

        monkeypatch.setattr(TemporalArtifactService, "read", outage)
        with pytest.raises(OSError, match="controlled artifact-read outage"):
            await service.record_terminal_state(
                workflow_id=target.workflow_id, state="completed"
            )
        assert any(
            call.args[0] == dependent_workflow_id
            for call in mock_client_adapter.signal_workflow.await_args_list
        )
        sync.assert_awaited_once()
        await session.refresh(target)
        assert target.state == models.MoonMindWorkflowState.COMPLETED
        assert executor.effects == 1


@pytest.mark.parametrize("publication_order", ["pending_first", "terminal_first"])
async def test_concurrent_pending_observation_cannot_suppress_terminal(
    tmp_path, mock_client_adapter, monkeypatch, publication_order
):
    async with temporal_db(tmp_path) as session:
        target, remediation, artifacts, authority, guard = await _prepare(
            session, tmp_path, mock_client_adapter, "execution.start_fresh_rerun"
        )
        # Retain the production owner in the active session while a peer
        # publishes. A locking read must refresh it before appending refs.
        retained_owner = await session.get(
            models.TemporalExecutionCanonicalRecord, remediation.workflow_id
        )
        candidate = models.TemporalExecutionCanonicalRecord(
            workflow_id="concurrent-candidate",
            run_id="candidate-run",
            namespace=target.namespace,
            workflow_type=target.workflow_type,
            owner_id=target.owner_id,
            owner_type=target.owner_type,
            entry=target.entry,
            state=models.MoonMindWorkflowState.EXECUTING,
        )
        session.add(candidate)
        await session.commit()
        executor = AcceptedEffect(
            dict(workflowId=candidate.workflow_id, runId=candidate.run_id)
        )
        active_read = asyncio.Event()
        terminal_publishing = asyncio.Event()
        pending_published = asyncio.Event()
        terminal_finished = asyncio.Event()

        class ActiveReader(CanonicalRecordEvidenceReader):
            async def read_result_evidence(self, **kwargs):
                snapshot = await super().read_result_evidence(**kwargs)
                active_read.set()
                await asyncio.wait_for(terminal_publishing.wait(), timeout=10)
                return snapshot

        publish = RemediationLifecyclePublisher.publish_json_artifact

        async def overlap(self, **kwargs):
            if kwargs["artifact_type"] != "remediation.verification":
                return await publish(self, **kwargs)
            if kwargs["payload"]["pending"]:
                if publication_order == "terminal_first":
                    await asyncio.wait_for(terminal_finished.wait(), timeout=10)
                artifact = await publish(self, **kwargs)
                pending_published.set()
                await asyncio.wait_for(terminal_finished.wait(), timeout=10)
                return artifact
            terminal_publishing.set()
            if publication_order == "pending_first":
                await asyncio.wait_for(pending_published.wait(), timeout=10)
            return await publish(self, **kwargs)

        monkeypatch.setattr(
            RemediationLifecyclePublisher, "publish_json_artifact", overlap
        )
        tools = RemediationEvidenceToolService(
            session=session,
            artifact_service=artifacts,
            action_executor=executor,
            verification_phase=RemediationVerificationPhase(
                reader=ActiveReader(session), max_poll_cap=0
            ),
        )
        task = asyncio.create_task(
            tools.execute_action(**_action_kwargs(remediation, authority, guard))
        )
        try:
            await asyncio.wait_for(active_read.wait(), timeout=10)
            factory = async_sessionmaker(session.bind, expire_on_commit=False)
            async with factory() as terminal_session:
                await TemporalExecutionService(
                    terminal_session, client_adapter=mock_client_adapter
                ).record_terminal_state(
                    workflow_id=candidate.workflow_id, state="completed"
                )
            terminal_finished.set()
            response = await asyncio.wait_for(task, timeout=10)
        finally:
            terminal_finished.set()
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        await session.refresh(
            await session.get(
                models.TemporalExecutionRemediationLink, remediation.workflow_id
            )
        )
        link = await session.get(
            models.TemporalExecutionRemediationLink, remediation.workflow_id
        )
        saved = link.mutation_guard_ledger_state["entries"][
            authority["request"]["actionId"]
        ]["execution"]
        assert saved["verificationState"] == "complete"
        assert (
            saved["verificationResponse"]["verification"]["outcome"]
            == "verified_resolved"
        )
        assert response["verification"]["outcome"] == "verified_resolved"
        assert executor.effects == 1
        async with factory() as persisted_session:
            canonical = await persisted_session.get(
                models.TemporalExecutionCanonicalRecord, remediation.workflow_id
            )
            for ref in response["artifactRefs"].values():
                assert ref in canonical.artifact_refs
            receipts = (
                (
                    await persisted_session.execute(
                        select(models.TemporalArtifact)
                        .join(models.TemporalArtifactLink)
                        .where(
                            models.TemporalArtifactLink.workflow_id
                            == remediation.workflow_id,
                            models.TemporalArtifactLink.link_type
                            == "remediation.verification",
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert len(receipts) == 2
            observations = []
            for receipt in receipts:
                assert receipt.artifact_id in canonical.artifact_refs
                _, payload = await artifacts.read(
                    artifact_id=receipt.artifact_id,
                    principal="service:remediation-lifecycle",
                )
                observations.append(json.loads(payload)["pending"])
            assert sorted(observations) == [False, True]
