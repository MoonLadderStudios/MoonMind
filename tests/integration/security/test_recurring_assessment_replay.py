"""Replay native scheduled assessment startup across its authority patch.

The fixture holds later execution at the planning boundary. Startup, schedule
provenance, admission, persistence, cancellation and replay use production code.
"""

from __future__ import annotations

import asyncio
import copy
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy import select
from temporalio import activity, workflow
from temporalio.client import WorkflowFailureError
from temporalio.common import RetryPolicy
from temporalio.exceptions import ApplicationError, CancelledError
from temporalio.testing import ActivityEnvironment
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

from api_service.db import base
from api_service.db.models import AssessmentOccurrenceRecord
from api_service.services.assessment_engagements import build_assessment_proposal
from api_service.services.recurring_workflows_service import (
    RecurringWorkflowsService,
    RecurringWorkflowValidationError,
)
from moonmind.security.operator_admission import OperatorAdmission
from moonmind.security.workload_profiles import AdmittedAssessmentScope
from moonmind.workflows.temporal.activities.omnigent_activities import (
    omnigent_admit_scheduled_occurrence_plan_activity,
)
from moonmind.workflows.temporal.artifacts import (
    LocalTemporalArtifactStore,
    TemporalArtifactActivities,
    TemporalArtifactService,
)
from moonmind.workflows.temporal.client import TemporalClientAdapter
from moonmind.workflows.temporal.service import TemporalExecutionService
from moonmind.workflows.temporal.workflows.run import MoonMindUserWorkflow
from tests.integration.security import test_recurring_assessments as recurring
from tests.integration.security.test_recurring_assessment_credentials import (
    save_authorization,
)
from tests.unit.services.test_assessment_job_authority import authorization

environment = recurring.environment
temporal_environment = recurring.temporal_environment

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]
PATCH = "run-recurring-assessment-authority-v1"


@workflow.defn(name="MoonMind.UserWorkflow")
class _StartupReplayWorkflow(MoonMindUserWorkflow):
    def _execute_kwargs_for_route(self, route, **kwargs):
        result = super()._execute_kwargs_for_route(route, **kwargs)
        result["task_queue"] = workflow.info().task_queue
        result["retry_policy"] = RetryPolicy(
            initial_interval=timedelta(milliseconds=10), maximum_attempts=2
        )
        return result

    async def _run_planning_stage(self, **kwargs):
        # Run the production planner's saved-plan/input checks, then hold later
        # tool execution so a real worker restart must replay the startup prefix.
        plan = await super()._run_planning_stage(**kwargs)
        self._at_planning_boundary = True
        await workflow.wait_condition(lambda: self._cancel_requested)
        return plan

    @workflow.query
    def at_planning_boundary(self) -> bool:
        return getattr(self, "_at_planning_boundary", False)

    @workflow.run
    async def run(self, request: dict) -> dict:
        return await super().run(request)


async def _planning_boundary(handle):
    for _ in range(100):
        if await handle.query("at_planning_boundary"):
            return
        await asyncio.sleep(0.05)
    pytest.fail("Scheduled workflow did not reach its production planning stage")


@pytest.mark.parametrize("historical", [True, False], ids=["pre-patch", "post-patch"])
async def test_native_assessment_startup_histories_replay_without_readmission(
    environment, temporal_environment, monkeypatch, historical
):
    """No Omnigent plan is present on either side of the startup patch."""
    client = temporal_environment.client
    adapter = TemporalClientAdapter(client=client)
    queue = "recurring-assessment-replay-" + uuid4().hex
    adapter._get_task_queue = lambda *_args, **_kwargs: queue
    monkeypatch.setattr(base, "async_session_maker", environment.sessions)
    monkeypatch.setattr(
        TemporalArtifactService,
        "_build_store_from_settings",
        staticmethod(lambda: LocalTemporalArtifactStore(environment.storage_root)),
    )

    async def get_client(_self):
        return client

    monkeypatch.setattr(TemporalClientAdapter, "get_client", get_client)
    record = authorization(
        recurring={
            "minIntervalSeconds": 60,
            "maxOccurrences": 1,
            "maxJobsPerOccurrence": 1,
            "maxJobSecondsPerOccurrence": 120,
            "maxRunSeconds": 120,
        }
    )
    record = await save_authorization(environment.sessions, record)
    target = {
        "workflowType": "MoonMind.UserWorkflow",
        "planArtifactRef": await recurring.put(
            environment.service, {"fixture": "unused after cancellation"}
        ),
        "initialParameters": {
            "publishMode": "none",
            "assessmentAuthorizationRef": record.ref,
            "workflow": {
                "publish": {"mode": "none"},
                "plan": [{"tool": {"type": "activity", "name": "security.assess"}}],
            },
        },
    }
    target["initialParameters"]["assessmentReview"] = build_assessment_proposal(
        target, record
    )
    owner = RecurringWorkflowsService(
        environment.session,
        temporal_client_adapter=adapter,
        artifact_service=environment.service,
    )
    definition = await owner.create_definition(
        name="Native assessment startup replay",
        description=None,
        enabled=True,
        schedule_type="cron",
        cron="0 6 * * *",
        timezone="UTC",
        scope_type="personal",
        scope_ref=None,
        owner_user_id=None,
        target=target,
        policy={"overlap": {"mode": "skip"}},
        actor=OperatorAdmission(via="recurring-assessment-replay-test"),
    )
    schedule = client.get_schedule_handle(definition.temporal_schedule_id)
    decisions = []

    @activity.defn(name="omnigent.admit_scheduled_occurrence_plan")
    async def admit(payload: dict) -> dict:
        # Inject a lost response after the real Activity commits. Temporal's
        # retry must recover the same reservation from a fresh database session.
        result = await omnigent_admit_scheduled_occurrence_plan_activity(payload)
        async with environment.sessions() as session:
            row = await session.get(
                AssessmentOccurrenceRecord, activity.info().workflow_id
            )
            decisions.append(copy.deepcopy(row.authority_json))
        if activity.info().attempt == 1:
            raise RuntimeError("Fixture lost admission response after commit")
        return result

    @activity.defn(name="execution.record_terminal_state")
    async def terminal(payload: dict) -> dict:
        return await TemporalArtifactActivities(
            environment.service
        ).execution_record_terminal_state(payload)

    def worker():
        return Worker(
            client,
            task_queue=queue,
            workflows=[_StartupReplayWorkflow],
            activities=[admit, terminal],
            workflow_runner=UnsandboxedWorkflowRunner(),
        )

    handle = None
    saved_patch = workflow.patched

    def legacy_patch(patch_id):
        return False if historical and patch_id == PATCH else saved_patch(patch_id)

    try:
        # Only history generation disables the new marker. Replay below always
        # uses the current production startup and Temporal's recorded decisions.
        with patch.object(workflow, "patched", legacy_patch):
            async with worker():
                await schedule.trigger()
                for _ in range(100):
                    actions = (await schedule.describe()).info.recent_actions
                    if actions:
                        break
                    await asyncio.sleep(0.05)
                assert len(actions) == 1
                handle = client.get_workflow_handle(actions[0].action.workflow_id)
                await _planning_boundary(handle)
        original_input = await adapter.read_scheduled_workflow_start_input(
            handle.id, run_id=(await handle.describe()).run_id
        )
        parameters = original_input["initial_parameters"]
        assert "omnigentExecutionPlan" not in parameters
        assert (
            AdmittedAssessmentScope.model_validate(
                parameters["assessmentScope"]
            ).targets
            == record.scope.targets
        )
        assert parameters["system"]["recurrence"]
        before_restart = await handle.fetch_history()
        scheduled = [
            e.activity_task_scheduled_event_attributes.activity_type.name
            for e in before_restart.events
            if e.HasField("activity_task_scheduled_event_attributes")
        ]
        assert scheduled.count("omnigent.admit_scheduled_occurrence_plan") == (
            0 if historical else 1
        )
        if not historical:
            assert len(decisions) == 2 and decisions[0] == decisions[1]
            assert decisions[0]["ordinal"] == 1
            assert decisions[0]["authorizationGeneration"] == record.generation
            assert (
                await environment.session.scalars(select(AssessmentOccurrenceRecord))
            ).all()[0].authority_json == decisions[0]
        else:
            assert decisions == []
            assert (
                await environment.session.scalars(select(AssessmentOccurrenceRecord))
            ).all() == []

        async with worker():
            # A fresh worker replays run(), including the admission condition.
            await _planning_boundary(handle)
            assert len(decisions) == (0 if historical else 2)
            if not historical:
                native = ActivityEnvironment()
                native.info = replace(
                    native.info,
                    namespace=client.namespace,
                    workflow_id=handle.id,
                    workflow_run_id=(await handle.describe()).run_id,
                )
                for discard in (False, True):
                    changed = copy.deepcopy(parameters)
                    if discard:
                        changed.pop("assessmentScope")
                    else:
                        changed["assessmentScope"]["targets"].append(
                            {"artifactRef": "art_unadmitted"}
                        )
                    with pytest.raises(
                        RecurringWorkflowValidationError, match="original authority"
                    ):
                        await native.run(
                            omnigent_admit_scheduled_occurrence_plan_activity,
                            {"workflowId": handle.id, "initialParameters": changed},
                        )
            control = TemporalExecutionService(
                environment.session, client_adapter=adapter
            )
            await control.describe_execution(handle.id)
            await control.cancel_execution(
                workflow_id=handle.id,
                reason="Replay fixture cancellation",
                graceful=True,
            )
            with pytest.raises(WorkflowFailureError) as canceled:
                await handle.result()
            assert isinstance(canceled.value.cause, CancelledError)
            if not historical:
                with pytest.raises(ApplicationError, match="canceled"):
                    await native.run(
                        omnigent_admit_scheduled_occurrence_plan_activity,
                        {"workflowId": handle.id, "initialParameters": parameters},
                    )
        history = await handle.fetch_history()
        assert not any(
            e.HasField("start_child_workflow_execution_initiated_event_attributes")
            or e.HasField("workflow_execution_continued_as_new_event_attributes")
            for e in history.events
        )
        assert len((await schedule.describe()).info.recent_actions) == 1
        if not historical:
            async with environment.sessions() as session:
                rows = (await session.scalars(select(AssessmentOccurrenceRecord))).all()
                assert len(rows) == 1
                retained = rows[0].authority_json
                assert retained["cancelRequestedAt"]
                assert {
                    k: v for k, v in retained.items() if k != "cancelRequestedAt"
                } == decisions[0]
                assert retained["expiresAt"] == decisions[0]["expiresAt"]
        calls_before_replay = len(decisions)
        replayer = Replayer(
            workflows=[_StartupReplayWorkflow],
            workflow_runner=UnsandboxedWorkflowRunner(),
            data_converter=client.data_converter,
        )
        receipts = Path("artifacts/recurring-assessment-replay") / queue
        receipts.mkdir(parents=True, exist_ok=True)
        for label, retained_history in (
            ("before-restart", before_restart),
            ("canceled", history),
        ):
            (receipts / f"{label}.json").write_text(retained_history.to_json())
            await replayer.replay_workflow(retained_history)
        assert len(decisions) == calls_before_replay
        print(f"Retained recurring startup histories: {receipts}")
    finally:
        if handle is not None and (await handle.describe()).status.name == "RUNNING":
            await handle.terminate("Recurring replay fixture cleanup")
        await schedule.delete()
