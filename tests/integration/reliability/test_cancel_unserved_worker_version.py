"""Replay the 2026-09-13 slot-wait cancel stranded by a worker replacement."""

import asyncio
import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from temporalio import workflow
from temporalio.client import WorkflowExecutionStatus, WorkflowFailureError
from temporalio.common import VersioningBehavior, WorkerDeploymentVersion
from temporalio.worker import (
    Replayer,
    UnsandboxedWorkflowRunner,
    Worker,
    WorkerDeploymentConfig,
)

from moonmind import release_identity
from moonmind.workflows.temporal.client import TemporalClientAdapter
from moonmind.workflows.temporal.release_routing import (
    bootstrap_version_routing,
    promote_version,
)
from moonmind.workflows.temporal.service import TemporalExecutionService
from moonmind.workflows.temporal.workflows.release_canary import (
    ReleaseCanaryWorkflow,
    inspect_release_activity,
)
from tests.integration.reliability.test_release_routing_journey import connect
from api_service.db.models import (
    MoonMindWorkflowState,
    TemporalExecutionCanonicalRecord,
    TemporalExecutionOwnerType,
    TemporalWorkflowType,
)

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.integration,
    pytest.mark.reliability_journey,
]


@workflow.defn
class CancelSlotWaitChild:
    @workflow.run
    async def run(self):
        await workflow.wait_condition(lambda: False)

    @workflow.query
    def waiting(self):
        return True


@workflow.defn
class CancelSlotWaitParent:
    @workflow.run
    async def run(self):
        await workflow.execute_child_workflow(
            CancelSlotWaitChild.run, id=workflow.info().workflow_id + ":slot"
        )


async def test_cancel_preserves_live_state_until_worker_routing_recovers(
    tmp_path, monkeypatch
):
    client = await connect()
    deployment = "cancel-routing-" + uuid4().hex
    queue = deployment + "-queue"
    root = tmp_path / "image"
    (root / "moonmind").mkdir(parents=True)
    source = root / "moonmind" / "entry.py"
    monkeypatch.setattr(
        release_identity, "__file__", str(root / "moonmind" / "release_identity.py")
    )

    def install(value):
        source.write_text(value)
        manifest = release_identity.build_release(root)
        (root / release_identity.RELEASE_FILE).write_text(json.dumps(manifest))
        return manifest["digest"]

    def spec(build):
        return SimpleNamespace(
            versioning_enabled=True,
            workflows=(ReleaseCanaryWorkflow,),
            deployment_id=deployment,
            build_id=build,
            task_queues=(queue,),
        )

    def worker(build):
        return Worker(
            client,
            task_queue=queue,
            workflows=[
                ReleaseCanaryWorkflow,
                CancelSlotWaitParent,
                CancelSlotWaitChild,
            ],
            activities=[inspect_release_activity],
            workflow_runner=UnsandboxedWorkflowRunner(),
            deployment_config=WorkerDeploymentConfig(
                version=WorkerDeploymentVersion(deployment, build),
                use_worker_versioning=True,
                default_versioning_behavior=VersioningBehavior.AUTO_UPGRADE,
            ),
        )

    original = install("original = True\n")
    async with worker(original):
        await bootstrap_version_routing(client, spec(original))
        parent = await client.start_workflow(
            CancelSlotWaitParent.run,
            id=deployment,
            task_queue=queue,
            execution_timeout=timedelta(seconds=120),
        )
        child = client.get_workflow_handle(deployment + ":slot")
        for _ in range(100):
            try:
                if await child.query("waiting"):
                    break
            except Exception:
                pass
            await asyncio.sleep(0.05)
        else:
            pytest.fail("Slot-wait child did not start")

    # Reproduce the escaped boundary: all old pollers disappear, while the
    # replacement version remains a candidate and Temporal still routes to A.
    replacement = install("replacement = True\n")
    async with worker(replacement):
        assert (await bootstrap_version_routing(client, spec(replacement)))[
            "status"
        ] == "awaiting_promotion"
        record = TemporalExecutionCanonicalRecord(
            workflow_id=parent.id,
            run_id=parent.first_execution_run_id,
            workflow_type=TemporalWorkflowType.USER_WORKFLOW,
            state=MoonMindWorkflowState.AWAITING_SLOT,
            paused=False,
            waiting_reason="provider_profile_slot",
            entry="user_workflow",
            owner_type=TemporalExecutionOwnerType.USER,
        )
        session = MagicMock()
        session.commit = AsyncMock()
        session.refresh = AsyncMock()
        service = TemporalExecutionService(session)
        service._client_adapter = TemporalClientAdapter(client)
        service._require_cancel_target_execution = AsyncMock(return_value=record)
        service._sync_projection_best_effort = AsyncMock(side_effect=lambda row: row)
        service._sync_integration_correlation_record = AsyncMock()
        service._best_effort_terminate_workflow_scoped_managed_sessions = AsyncMock()
        service._fan_out_dependency_resolution = AsyncMock()

        accepted = await service.cancel_execution(
            workflow_id=parent.id, reason="Operator canceled", graceful=True
        )
        description = await parent.describe()
        assert description.status is WorkflowExecutionStatus.RUNNING
        assert description.raw_description.pending_workflow_task.attempt == 1
        assert accepted.state is MoonMindWorkflowState.AWAITING_SLOT
        assert accepted.close_status is None
        assert accepted.closed_at is None
        assert accepted.waiting_reason == "provider_profile_slot"
        assert accepted.memo["summary"].startswith("Cancellation requested.")
        service._fan_out_dependency_resolution.assert_not_awaited()

        await promote_version(
            client,
            deployment=deployment,
            build_id=replacement,
            expected_current=f"{deployment}.{original}",
            task_queue=queue,
            task_queues=(queue,),
            canary_id=deployment + "-recovery",
        )
        with pytest.raises(WorkflowFailureError):
            await asyncio.wait_for(parent.result(), timeout=30)
        assert (await parent.describe()).status is WorkflowExecutionStatus.CANCELED
        assert (await child.describe()).status is WorkflowExecutionStatus.CANCELED
        confirmed = await service.cancel_execution(
            workflow_id=parent.id, reason="Operator canceled", graceful=True
        )
        assert confirmed.state is MoonMindWorkflowState.CANCELED
        assert confirmed.waiting_reason is None
        service._fan_out_dependency_resolution.assert_awaited_once()
        for handle, cls in [
            (parent, CancelSlotWaitParent),
            (child, CancelSlotWaitChild),
        ]:
            history = await handle.fetch_history()
            await Replayer(
                workflows=[cls], workflow_runner=UnsandboxedWorkflowRunner()
            ).replay_workflow(history)
