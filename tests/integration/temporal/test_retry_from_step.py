"""Retry a failed run from a step through the API against a real Temporal server."""

import asyncio
import json
from datetime import timedelta
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from temporalio import activity, workflow
from temporalio.client import WorkflowExecutionStatus, WorkflowFailureError
from temporalio.common import (
    SearchAttributeKey,
    SearchAttributePair,
    TypedSearchAttributes,
)
from temporalio.exceptions import ActivityError, ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

from api_service.api.routers import executions
from api_service.auth_providers import get_instance_access, get_instance_access_optional
from api_service.db.base import get_async_session
from api_service.db.models import (
    Base,
    TemporalExecutionCanonicalRecord,
    TemporalExecutionRecord,
)
from moonmind.security.operator_admission import OperatorAdmission
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest, AgentRunResult
from moonmind.schemas.temporal_models import StepExecutionIdentityModel
from moonmind.schemas.temporal_activity_models import ArtifactReadInput
from moonmind.workflows.temporal import github_issue_claim_lease as claim_leases
from moonmind.workflows.temporal import retry_from_step, story_output_tools
from moonmind.workflows.temporal.activity_catalog import ARTIFACTS_TASK_QUEUE
from moonmind.workflows.temporal.client import TemporalClientAdapter
from moonmind.workflows.temporal.data_converter import MOONMIND_TEMPORAL_DATA_CONVERTER
from moonmind.workflows.temporal.github_issue_attempts import parse_attempt_comment
from moonmind.workflows.temporal.issue_claim_store import IssueClaimStore
from moonmind.workflows.temporal.retry_from_step import STEP_ORDER_MEMO_KEY
from moonmind.workflows.temporal.service import TemporalExecutionService
from moonmind.workflows.temporal.step_checkpoints import build_step_checkpoint_payload
from moonmind.workflows.temporal.workflows.run import MoonMindUserWorkflow
from tests.helpers.temporal_visibility import register_deployment_search_attributes
from tests.unit.workflows.temporal import test_issue_claim_journey as claim_journey

journey = claim_journey.journey

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]

TASK_QUEUE = "retry-from-step-test"
STEPS = ("step-1", "step-2", "step-3", "step-4")
STEP_CALLS: list[str] = []
STEP_RUN_IDS: dict[str, list[str]] = {}
FAILING_STEP: dict[str, str | None] = {"step": "step-3"}
CHILD_SAVES: dict[str, bytes] = {}
CHILD_SAVE_CALLS: list[StepExecutionIdentityModel] = []


@activity.defn(name="retry_probe.run_step")
async def run_step(step_id: str) -> str:
    STEP_CALLS.append(step_id)
    STEP_RUN_IDS.setdefault(step_id, []).append(activity.info().workflow_run_id)
    if step_id == FAILING_STEP["step"]:
        raise ApplicationError(f"{step_id} failed", non_retryable=True)
    return f"{step_id} ok"


@workflow.defn(name="MoonMind.UserWorkflow")
class StepWorkflow:
    """Follows the run workflow's step-start memo contract."""

    def __init__(self) -> None:
        self._rows: list[dict] = []

    @workflow.query(name="get_step_ledger")
    def get_step_ledger(self) -> dict:
        info = workflow.info()
        return {
            "workflowId": info.workflow_id,
            "runId": info.run_id,
            "steps": self._rows,
        }

    @workflow.run
    async def run(self) -> list[str]:
        now = workflow.now().isoformat()
        self._rows = [
            {
                "logicalStepId": step,
                "order": order,
                "title": f"Step {order}",
                "status": "pending",
                "updatedAt": now,
            }
            for order, step in enumerate(STEPS, start=1)
        ]
        results = []
        for row in self._rows:
            row.update(status="running", startedAt=workflow.now().isoformat())
            workflow.upsert_memo(
                {
                    STEP_ORDER_MEMO_KEY: row["order"],
                    "summary": f"Executing {row['title']}",
                }
            )
            try:
                results.append(
                    await workflow.execute_activity(
                        run_step,
                        row["logicalStepId"],
                        start_to_close_timeout=timedelta(seconds=30),
                    )
                )
            except ActivityError as exc:
                row["status"] = "failed"
                raise ApplicationError(f"{row['logicalStepId']} failed") from exc
            row["status"] = "completed"
            await self.after_step(row["logicalStepId"])
        return results

    async def after_step(self, step_id: str) -> None:
        pass


@activity.defn(name="retry_probe.child_save")
async def child_save(identity: StepExecutionIdentityModel) -> dict:
    CHILD_SAVE_CALLS.append(identity)
    step_execution_id = (
        f"{identity.workflow_id}:{identity.run_id}:"
        f"{identity.logical_step_id}:execution:{identity.execution_ordinal}"
    )
    runtime_binding_ref = "omnigent-runtime-binding:sha256:" + "f" * 64
    workspace = {
        "kind": "worktree_archive", "baseCommit": "a" * 40,
        "archiveRef": "artifact://archive", "archiveDigest": "sha256:" + "b" * 64,
        "manifestRef": "artifact://manifest", "manifestDigest": "sha256:" + "c" * 64,
        "workspaceDigest": "sha256:" + "d" * 64,
        "workspaceIdentityDigest": "sha256:" + "e" * 64,
    }
    reference = f"artifact://{identity.workflow_id}/child-save"
    CHILD_SAVES[reference] = json.dumps(build_step_checkpoint_payload(
        identity=identity, boundary="after_execution",
        task_input_snapshot_ref="artifact://input", plan_ref="artifact://plan", workspace=workspace,
        created_at=activity.info().scheduled_time,
    )).encode()
    return {
        "runId": identity.run_id,
        "recordedStepExecution": identity.model_dump(by_alias=True, mode="json"),
        "result": AgentRunResult(summary="Saved child work", metadata={
            "workPreserved": True,
            "parentWorkflowId": identity.workflow_id,
            "parentTemporalRunId": identity.run_id,
            "parentRunId": identity.run_id,
            "childWorkflowId": f"{identity.workflow_id}:saved-step-child",
            "childRunId": f"child-run:{identity.run_id}",
            "runtimeBindingRef": runtime_binding_ref,
            "runtimeSessionContext": {
                "workflowId": identity.workflow_id,
                "runId": identity.run_id,
                "stepExecutionId": step_execution_id,
                "runtimeBindingRef": runtime_binding_ref,
                "epoch": step_execution_id,
            },
            "savedWorkspaceCheckpoint": {**workspace, "checkpointRef": reference},
        }).model_dump(by_alias=True, mode="json"),
    }


@activity.defn(name="artifact.read")
async def read_child_save(request: ArtifactReadInput) -> bytes:
    return CHILD_SAVES[request.artifact_ref]


@workflow.defn(name="MoonMind.UserWorkflow")
class SharedCheckpointWorkflow(StepWorkflow):
    """Replay the production save-adoption boundary before the failed step."""

    @workflow.run
    async def run(self) -> list[str]:
        info = workflow.info()
        self._checkpoint_owner = MoonMindUserWorkflow()
        self._save = await workflow.execute_activity(
            child_save,
            StepExecutionIdentityModel(
                workflowId=info.workflow_id, runId=info.run_id,
                logicalStepId="step-1", executionOrdinal=1,
            ),
            schedule_to_close_timeout=timedelta(seconds=30),
            start_to_close_timeout=timedelta(seconds=30),
        )
        # Production records this identity in its policy-compile Activity.
        self._checkpoint_owner._resilience_policy_run_id = self._save["runId"]
        self._checkpoint_owner._initialize_step_ledger(
            ordered_nodes=[{"id": "step-1", "inputs": {"title": "Saved work"}}],
            dependency_map={"step-1": []}, updated_at=workflow.now(),
        )
        return await super().run()

    async def after_step(self, step_id: str) -> None:
        info = workflow.info()
        owner = self._checkpoint_owner
        if step_id != "step-1":
            fresh = owner._build_agent_execution_request(
                node_inputs={"runtime": {"mode": "omnigent"}}, node_id=step_id,
                tool_name="omnigent", workflow_parameters={},
            )
            assert fresh.step_execution.run_id == info.run_id
            assert fresh.step_execution.step_execution_id == (
                f"{info.workflow_id}:{info.run_id}:{step_id}:execution:1"
            )
            return
        owner._mark_step_running(step_id, updated_at=workflow.now(), summary="Saved work")
        request = AgentExecutionRequest(
            agentKind="external", agentId="omnigent", correlationId=info.workflow_id,
            idempotencyKey="recorded-child",
            stepExecution={
                "workflowId": info.workflow_id, "runId": info.run_id,
                "logicalStepId": step_id, "executionOrdinal": 1,
                "stepExecutionId": f"{info.workflow_id}:{info.run_id}:{step_id}:execution:1",
                "runtimeContextPolicy": "fresh_agent_run",
            },
        )
        request = owner._restore_replayed_agent_step_identity(
            request, self._save["result"],
            child_workflow_id=f"{info.workflow_id}:saved-step-child",
        )
        owner._record_shared_runtime_context(request, self._save["result"])
        recorded = StepExecutionIdentityModel.model_validate(
            self._save["recordedStepExecution"]
        )
        assert request.step_execution.run_id == recorded.run_id
        assert owner._shared_runtime_context.run_id == recorded.run_id
        assert (
            owner._shared_runtime_context.step_execution_id
            == request.step_execution.step_execution_id
        )
        assert await owner._adopt_shared_child_saved_checkpoint(
            step_id, request, self._save["result"],
        )
        assert await owner._record_canonical_step_checkpoint(
            step_id, boundary="after_execution", updated_at=workflow.now(),
        ) == f"artifact://{info.workflow_id}/child-save"


@workflow.defn(name="MoonMind.UserWorkflow")
class ChangedWorkflow(StepWorkflow):
    """Installed code whose first command differs from the recorded history."""

    @workflow.run
    async def run(self) -> list[str]:
        await workflow.sleep(timedelta(seconds=1))
        return await super().run()


@activity.defn(name="retry_probe.record_run_id")
async def record_run_id(run_id: str) -> str:
    return run_id


@workflow.defn(name="MoonMind.UserWorkflow")
class ResetIdentitySensitiveWorkflow(StepWorkflow):
    @workflow.run
    async def run(self) -> list[str]:
        recorded = await workflow.execute_activity(
            record_run_id, workflow.info().run_id,
            start_to_close_timeout=timedelta(seconds=30),
        )
        if recorded != workflow.info().run_id:
            workflow.upsert_memo({"unexpected_reset_command": True})
        return await super().run()


def _app(service, session, env, user) -> FastAPI:
    app = FastAPI()
    app.include_router(executions.router)

    async def session_dependency():
        yield session

    app.dependency_overrides[get_async_session] = session_dependency
    app.dependency_overrides[executions._get_service] = lambda: service
    app.dependency_overrides[executions.get_temporal_client] = lambda: env.client
    app.dependency_overrides[get_instance_access] = lambda: user
    app.dependency_overrides[get_instance_access_optional] = lambda: user
    routes = list(app.routes)
    while routes:
        route = routes.pop()
        routes.extend(getattr(getattr(route, "original_router", route), "routes", ()))
        for dependency in getattr(
            getattr(route, "dependant", None), "dependencies", ()
        ):
            if getattr(dependency.call, "__name__", "") in {
                "_current_user_fallback",
                "_strict_current_user",
                "_optional_current_user",
                "get_instance_access",
                "get_instance_access_optional",
            }:
                app.dependency_overrides[dependency.call] = lambda: user
    return app


@pytest.mark.usefixtures("deployment_without_recorded_connections")
@pytest.mark.parametrize("expired_claim", [False, True])
@pytest.mark.parametrize("workflow_class", [StepWorkflow, SharedCheckpointWorkflow])
async def test_failed_run_restarts_from_the_failed_step_and_keeps_earlier_steps(
    tmp_path, monkeypatch, journey, expired_claim, workflow_class,
):
    STEP_CALLS.clear()
    STEP_RUN_IDS.clear()
    CHILD_SAVE_CALLS.clear()
    FAILING_STEP["step"] = "step-3"
    # The service replays the kept history on the installed workflow classes.
    monkeypatch.setattr(retry_from_step, "_replay_workflows", lambda: [workflow_class])
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/executions.db")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with (
            async_sessionmaker(engine, expire_on_commit=False)() as session,
            await WorkflowEnvironment.start_local(
                data_converter=MOONMIND_TEMPORAL_DATA_CONVERTER
            ) as env,
        ):
            await register_deployment_search_attributes(env)
            owner = str(uuid4())
            workflow_id = f"mm:{uuid4()}"
            user = OperatorAdmission(via="loopback")
            if expired_claim:
                state, github, _ = journey
                claims = IssueClaimStore(
                    async_sessionmaker(engine, expire_on_commit=False)
                )
                monkeypatch.setattr(
                    story_output_tools, "IssueClaimStore", lambda: claims
                )
                monkeypatch.setattr(
                    "moonmind.workflows.adapters.github_service.GitHubService",
                    lambda: github,
                )
                await story_output_tools.load_github_issue_preset_brief(
                    {"repository": "example/repo", "issueNumber": 3970},
                    {"execution_owner": f"default/{workflow_id}"},
                    github_service_factory=lambda: github,
                )
                clock = claim_leases.utc_now() + timedelta(minutes=31)
                monkeypatch.setattr(claim_leases, "utc_now", lambda: clock)
                assert claim_leases.expired(
                    parse_attempt_comment(
                        (await claims.get(f"default/{workflow_id}")).comment_body
                    ).handoff
                )
                await claim_leases.reconcile_expired_issue(
                    service=github, repository="example/repo", issue_number=3970
                )
                assert state["labels"] == []
            service = TemporalExecutionService(
                session, client_adapter=TemporalClientAdapter(env.client)
            )
            app = _app(service, session, env, user)
            async with (
                Worker(
                    env.client,
                    task_queue=TASK_QUEUE,
                    workflows=[workflow_class],
                    activities=[run_step, child_save],
                    workflow_runner=UnsandboxedWorkflowRunner(),
                ),
                Worker(env.client, task_queue=ARTIFACTS_TASK_QUEUE, activities=[read_child_save]),
            ):
                handle = await env.client.start_workflow(
                    workflow_class.run,
                    id=workflow_id,
                    task_queue=TASK_QUEUE,
                    search_attributes=TypedSearchAttributes(
                        [
                            SearchAttributePair(
                                SearchAttributeKey.for_keyword("mm_owner_id"), owner
                            ),
                            SearchAttributePair(
                                SearchAttributeKey.for_keyword("mm_owner_type"), "user"
                            ),
                            SearchAttributePair(
                                SearchAttributeKey.for_keyword("mm_entry"),
                                "user_workflow",
                            ),
                        ]
                    ),
                )
                with pytest.raises(WorkflowFailureError):
                    await handle.result()
                failed_run_id = (await handle.describe()).run_id
                assert STEP_CALLS == ["step-1", "step-2", "step-3"]
                if workflow_class is SharedCheckpointWorkflow:
                    assert len(CHILD_SAVE_CALLS) == 1
                    assert CHILD_SAVE_CALLS[0].run_id == failed_run_id

                FAILING_STEP["step"] = None
                STEP_CALLS.clear()
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app), base_url="http://test"
                ) as client:
                    unstarted = await client.post(
                        f"/api/executions/{workflow_id}/retry-from-step",
                        json={"idempotencyKey": "retry-unstarted", "stepId": "step-4"},
                    )
                    assert unstarted.status_code == 409
                    assert unstarted.json()["detail"]["reason"] == "step_not_retryable"

                    response = await client.post(
                        f"/api/executions/{workflow_id}/retry-from-step",
                        json={
                            "idempotencyKey": "retry-1",
                            "sourceRunId": failed_run_id,
                        },
                    )
                    assert response.status_code == 202, response.text
                    if expired_claim:
                        assert state["labels"] == ["status: in-progress"]
                    body = response.json()
                    assert body["stepId"] == "step-3"
                    assert body["sourceRunId"] == failed_run_id
                    new_run_id = body["runId"]
                    assert new_run_id != failed_run_id

                    retried = env.client.get_workflow_handle(
                        workflow_id, run_id=new_run_id
                    )
                    async with asyncio.timeout(30):
                        assert await retried.result() == [
                            "step-1 ok",
                            "step-2 ok",
                            "step-3 ok",
                            "step-4 ok",
                        ]
                    # Earlier steps came from history; only the failed step and
                    # its successors ran again.
                    assert STEP_CALLS == ["step-3", "step-4"]
                    assert STEP_RUN_IDS == {
                        "step-1": [failed_run_id],
                        "step-2": [failed_run_id],
                        "step-3": [failed_run_id, new_run_id],
                        "step-4": [new_run_id],
                    }
                    if workflow_class is SharedCheckpointWorkflow:
                        assert len(CHILD_SAVE_CALLS) == 1
                    assert (
                        await retried.describe()
                    ).status == WorkflowExecutionStatus.COMPLETED

                    repeated = await client.post(
                        f"/api/executions/{workflow_id}/retry-from-step",
                        json={
                            "idempotencyKey": "retry-1",
                            "sourceRunId": failed_run_id,
                        },
                    )
                    assert repeated.status_code == 202
                    assert repeated.json()["runId"] == new_run_id

                    # Temporal accepted the reset but MoonMind lost its response
                    # (crash or failed commit), with the row either still on the
                    # failed run or already synced to the successor.
                    for row_run_id in (failed_run_id, new_run_id):
                        for model in (
                            TemporalExecutionCanonicalRecord,
                            TemporalExecutionRecord,
                        ):
                            row = await session.get(model, workflow_id)
                            if row is not None:
                                row.run_id = row_run_id
                                row.last_update_idempotency_key = None
                                row.last_update_response = None
                        await session.commit()
                        recovered = await client.post(
                            f"/api/executions/{workflow_id}/retry-from-step",
                            json={
                                "idempotencyKey": "retry-1",
                                "sourceRunId": failed_run_id,
                            },
                        )
                        assert recovered.status_code == 202, recovered.text
                        assert recovered.json()["runId"] == new_run_id
                        assert recovered.json()["stepId"] == "step-3"

                    other = await client.post(
                        f"/api/executions/{workflow_id}/retry-from-step",
                        json={
                            "idempotencyKey": "retry-2",
                            "sourceRunId": failed_run_id,
                        },
                    )
                    assert other.status_code == 409
                    assert other.json()["detail"]["reason"] == "run_changed"

                    detail = await client.get(f"/api/executions/{workflow_id}")
                    assert detail.status_code == 200
                    assert detail.json()["runId"] == new_run_id

                replay = await Replayer(
                    workflows=[workflow_class],
                    data_converter=MOONMIND_TEMPORAL_DATA_CONVERTER,
                    workflow_runner=UnsandboxedWorkflowRunner(),
                ).replay_workflow(
                    await retried.fetch_history(), raise_on_replay_failure=False
                )
                assert replay.replay_failure is None
    finally:
        await engine.dispose()


@pytest.mark.parametrize("source_class,installed_class", [
    (StepWorkflow, ChangedWorkflow),
    (ResetIdentitySensitiveWorkflow, ResetIdentitySensitiveWorkflow),
])
async def test_retry_is_refused_when_installed_code_cannot_replay_the_kept_history(
    tmp_path, monkeypatch, source_class, installed_class,
):
    STEP_CALLS.clear()
    FAILING_STEP["step"] = "step-2"

    monkeypatch.setattr(retry_from_step, "_replay_workflows", lambda: [installed_class])
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/executions.db")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with (
            async_sessionmaker(engine, expire_on_commit=False)() as session,
            await WorkflowEnvironment.start_local(
                data_converter=MOONMIND_TEMPORAL_DATA_CONVERTER
            ) as env,
        ):
            await register_deployment_search_attributes(env)
            owner = str(uuid4())
            workflow_id = f"mm:{uuid4()}"
            user = OperatorAdmission(via="loopback")
            service = TemporalExecutionService(
                session, client_adapter=TemporalClientAdapter(env.client)
            )
            app = _app(service, session, env, user)
            async with Worker(
                env.client,
                task_queue=TASK_QUEUE,
                workflows=[source_class],
                activities=[run_step, record_run_id],
                workflow_runner=UnsandboxedWorkflowRunner(),
            ):
                handle = await env.client.start_workflow(
                    source_class.run,
                    id=workflow_id,
                    task_queue=TASK_QUEUE,
                    search_attributes=TypedSearchAttributes(
                        [
                            SearchAttributePair(
                                SearchAttributeKey.for_keyword("mm_owner_id"), owner
                            ),
                            SearchAttributePair(
                                SearchAttributeKey.for_keyword("mm_owner_type"), "user"
                            ),
                            SearchAttributePair(
                                SearchAttributeKey.for_keyword("mm_entry"),
                                "user_workflow",
                            ),
                        ]
                    ),
                )
                with pytest.raises(WorkflowFailureError):
                    await handle.result()
                failed_run_id = (await handle.describe()).run_id
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app), base_url="http://test"
                ) as client:
                    response = await client.post(
                        f"/api/executions/{workflow_id}/retry-from-step",
                        json={"idempotencyKey": "retry-changed"},
                    )
                assert response.status_code == 409
                assert response.json()["detail"]["reason"] == "history_not_replayable"
                # Nothing was reset: the failed run is still the latest run.
                description = await env.client.get_workflow_handle(
                    workflow_id
                ).describe()
                assert description.run_id == failed_run_id
                assert description.status == WorkflowExecutionStatus.FAILED
    finally:
        await engine.dispose()
