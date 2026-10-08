"""Registered deterministic tool admission and dispatch through production owners.

Planning, registry admission, Activity binding, artifact authorization, generic
dispatch, the registered filesystem handler, and Step result recording are real.
ActivityEnvironment cases control the workflow engine and external AgentRun;
server cases use Temporal itself with a controlled external AgentRun child.
Both select the relevant patch branches without unrelated backend services.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from temporalio import workflow as temporal_workflow
from temporalio.api.enums.v1 import EventType
from temporalio.testing import ActivityEnvironment, WorkflowEnvironment
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

from api_service.db.models import Base
from moonmind.workflows.skills.skill_dispatcher import SkillActivityDispatcher
from moonmind.workflows.temporal.activity_catalog import (
    ARTIFACTS_TASK_QUEUE,
    SANDBOX_TASK_QUEUE,
    WORKFLOW_TASK_QUEUE,
    TemporalActivityCatalog,
    build_default_activity_catalog,
)
from moonmind.workflows.temporal.activity_runtime import (
    TemporalPlanActivities,
    TemporalSkillActivities,
    build_activity_bindings,
)
from moonmind.workflows.temporal.artifacts import (
    LocalTemporalArtifactStore,
    TemporalArtifactActivities,
    TemporalArtifactRepository,
    TemporalArtifactService,
)
from moonmind.workflows.temporal.story_output_tools import (
    register_story_output_tool_handlers,
)
from moonmind.workflows.temporal.worker_runtime import _build_runtime_planner
from moonmind.workflows.temporal.workflows import run as run_module
from moonmind.workflows.temporal.workflows.run import MoonMindRunWorkflow

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]


_JOURNEY_PATCHES = {
    run_module.RUN_DETERMINISTIC_TOOL_REF_RESOLUTION_PATCH,
    run_module.RUN_AGENT_STEP_INPUTS_HANDOFF_PATCH,
    run_module.RUN_TRUSTED_TOOL_REGISTRY_PATCH,
    run_module.RUN_EXECUTION_SCOPED_PRINCIPAL_PATCH,
    run_module.RUN_TOOL_RUNTIME_SELECTION_CONTEXT_PATCH,
    run_module.RUN_AGENT_RUNTIME_RETRY_CLASSIFICATION_PATCH,
    run_module.RUN_FAILED_RESULT_BLOCKER_PATCH,
}


@asynccontextmanager
async def _artifact_service(tmp_path: Path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/journey.db")
    sessions = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        async with sessions() as session:
            yield TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
            )
    finally:
        await engine.dispose()


def _parameters(root: Path, *, mixed: bool) -> dict[str, Any]:
    steps = [
        {
            "id": "discover",
            "type": "tool",
            "tool": {"id": "document.discover", "inputs": {"directory": str(root)}},
        }
    ]
    if mixed:
        steps.append(
            {
                "id": "consume",
                "type": "skill",
                "instructions": "Summarize the discovered documents.",
                "inputs": {
                    "documentPaths": {
                        "ref": {
                            "node": "discover",
                            "json_pointer": "/outputs/documentPaths",
                        }
                    }
                },
            }
        )
    return {
        "workflow": {
            "instructions": "Discover local documents and summarize when requested.",
            "publish": {"mode": "none"},
            "steps": steps,
        }
    }


def _bindings(service: TemporalArtifactService) -> dict[str, Any]:
    dispatcher = SkillActivityDispatcher()
    register_story_output_tool_handlers(dispatcher)
    full_catalog = build_default_activity_catalog()
    # Preserve the production definitions and routes, selecting only the service
    # families this journey needs rather than replacing them with test handlers.
    needed = {
        "plan.generate",
        "plan.validate",
        "artifact.read",
        "artifact.create",
        "artifact.write_complete",
    }
    catalog = TemporalActivityCatalog(
        activities=tuple(
            entry for entry in full_catalog.activities if entry.activity_type in needed
        ),
        fleets=full_catalog.fleets,
    )
    bindings = build_activity_bindings(
        catalog,
        artifact_activities=TemporalArtifactActivities(service),
        plan_activities=TemporalPlanActivities(
            artifact_service=service, planner=_build_runtime_planner()
        ),
        skill_activities=TemporalSkillActivities(
            dispatcher=dispatcher, artifact_service=service
        ),
    )
    return {
        binding.activity_type: binding.handler
        for binding in bindings
        if binding.activity_type != "mm.tool.execute"
        or binding.task_queue == SANDBOX_TASK_QUEUE
    }


async def _admit(bindings: dict[str, Any], parameters: dict[str, Any], principal: str):
    env = ActivityEnvironment()
    generated = await env.run(
        bindings["plan.generate"], {"principal": principal, "parameters": parameters}
    )
    plan_bytes = await env.run(
        bindings["artifact.read"],
        {"principal": principal, "artifact_ref": generated.plan_ref.artifact_id},
    )
    plan = json.loads(plan_bytes)
    validated = await env.run(
        bindings["plan.validate"],
        {
            "principal": principal,
            "plan_ref": generated.plan_ref.artifact_id,
            "registry_snapshot_ref": plan["metadata"]["registry_snapshot"][
                "artifact_ref"
            ],
        },
    )
    return validated.artifact_id, plan


def _workflow_engine(monkeypatch, bindings, *, agent_result=None):
    calls = []
    children = []
    env = ActivityEnvironment()
    monkeypatch.setattr(
        run_module.workflow, "patched", lambda name: name in _JOURNEY_PATCHES
    )
    monkeypatch.setattr(run_module.workflow, "now", lambda: datetime.now(timezone.utc))
    monkeypatch.setattr(
        run_module.workflow,
        "info",
        lambda: SimpleNamespace(
            namespace="default",
            workflow_id="document-journey",
            run_id="run-973",
            search_attributes={},
            task_queue="mm.workflow",
            parent=None,
        ),
    )
    monkeypatch.setattr(run_module.workflow, "upsert_memo", lambda _: None)
    monkeypatch.setattr(run_module.workflow, "upsert_search_attributes", lambda _: None)
    monkeypatch.setattr(
        run_module.workflow,
        "logger",
        SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None),
    )

    async def wait_condition(predicate, **kwargs):
        assert predicate()

    async def execute_activity(name, payload, **kwargs):
        calls.append((name, payload, kwargs))
        assert name in bindings, f"Unexpected Activity outside admitted journey: {name}"
        return await env.run(bindings[name], payload)

    async def execute_child(name, request, **kwargs):
        assert name == "MoonMind.AgentRun"
        children.append(request)
        return agent_result or {
            "summary": "Summarized discovered documents",
            "output_refs": [],
        }

    monkeypatch.setattr(run_module.workflow, "wait_condition", wait_condition)
    monkeypatch.setattr(run_module.workflow, "execute_activity", execute_activity)
    monkeypatch.setattr(run_module.workflow, "execute_child_workflow", execute_child)
    return calls, children


@pytest.mark.parametrize("mixed", [False, True])
async def test_registered_document_discovery_admission_dispatch_and_dependency(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mixed: bool
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "first.md").write_text("# First document\n")
    (workspace / "second.txt").write_text("Second document\n")
    (workspace / "ignored.py").write_text("pass\n")
    parameters = _parameters(workspace, mixed=mixed)
    principal = "workflow:document-journey"
    async with _artifact_service(tmp_path) as service:
        bindings = _bindings(service)
        plan_ref, plan = await _admit(bindings, parameters, principal)
        assert plan["nodes"][0]["tool"] == {
            "type": "skill",
            "name": "document.discover",
        }
        if mixed:
            assert (
                plan["nodes"][1]["inputs"].get("inputs")
                == parameters["workflow"]["steps"][1]["inputs"]
            )
        calls, children = _workflow_engine(monkeypatch, bindings)
        workflow = MoonMindRunWorkflow()
        workflow._owner_id = "operator"
        await workflow._run_execution_stage(parameters=parameters, plan_ref=plan_ref)

        tools = [call for call in calls if call[0] == "mm.tool.execute"]
        assert len(tools) == 1
        assert tools[0][2]["task_queue"] == SANDBOX_TASK_QUEUE
        assert tools[0][1]["invocation_payload"]["inputs"] == {
            "directory": str(workspace)
        }
        assert tools[0][1]["context"]["runtime_selection"] == {}
        assert tools[0][1]["idempotency_key"]
        assert tools[0][2]["retry_policy"].maximum_attempts == 1
        assert len(children) == int(mixed)
        assert [row["status"] for row in workflow._step_ledger_rows] == [
            "completed"
        ] * (1 + int(mixed))
        if mixed:
            # Inspect the actual child request, not an intermediate resolved
            # dictionary: downstream work must receive the recorded values.
            instructions = children[0].instruction_ref
            assert "first.md" in instructions
            assert "second.txt" in instructions
            assert "json_pointer" not in instructions


async def test_registered_document_discovery_business_failure_records_failed_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    parameters = _parameters(tmp_path / "absent", mixed=False)
    async with _artifact_service(tmp_path) as service:
        bindings = _bindings(service)
        plan_ref, _ = await _admit(bindings, parameters, "workflow:document-journey")
        calls, children = _workflow_engine(monkeypatch, bindings)
        workflow = MoonMindRunWorkflow()
        workflow._owner_id = "operator"
        with pytest.raises(ValueError, match="Directory does not exist"):
            await workflow._run_execution_stage(
                parameters=parameters, plan_ref=plan_ref
            )
        assert len([call for call in calls if call[0] == "mm.tool.execute"]) == 1
        assert not children
        assert workflow._step_ledger_rows[0]["status"] == "failed"


async def test_registered_dispatch_cannot_read_another_execution_registry(
    tmp_path: Path, monkeypatch
):
    from moonmind.config.settings import settings
    from moonmind.workflows.temporal.artifacts import TemporalArtifactAuthorizationError

    monkeypatch.setattr(settings.oidc, "AUTH_PROVIDER", "oidc")
    async with _artifact_service(tmp_path) as service:
        bindings = _bindings(service)
        _, plan = await _admit(
            bindings, _parameters(tmp_path, mixed=False), "workflow:document-journey"
        )
        with pytest.raises(TemporalArtifactAuthorizationError):
            await ActivityEnvironment().run(
                bindings["mm.tool.execute"],
                {
                    "principal": "workflow:another-execution",
                    "registry_snapshot_ref": plan["metadata"]["registry_snapshot"][
                        "artifact_ref"
                    ],
                    "invocation_payload": {
                        "id": "discover",
                        "tool": {"type": "skill", "name": "document.discover"},
                        "inputs": {"directory": str(tmp_path)},
                    },
                },
            )


@temporal_workflow.defn
class _RegisteredDocumentServerJourney:
    """Run the production executor; wait only to make worker restart observable."""

    def __init__(self):
        self.executor = MoonMindRunWorkflow()
        self.completed = False
        self.released = False

    @temporal_workflow.run
    async def run(self, request: dict) -> dict:
        self.executor._owner_id = "operator"
        await self.executor._run_execution_stage(
            parameters=request["parameters"], plan_ref=request["plan_ref"]
        )
        self.completed = True
        await temporal_workflow.wait_condition(lambda: self.released)
        return {"steps": self.executor._step_ledger_rows}

    @temporal_workflow.query
    def finished_steps(self) -> bool:
        return self.completed

    @temporal_workflow.signal
    def release(self):
        self.released = True


@temporal_workflow.defn(name="MoonMind.AgentRun")
class _ControlledAgentRun:
    """External agent boundary; the parent uses its real request builder."""

    @temporal_workflow.run
    async def run(self, request: dict) -> dict:
        return {"summary": request["instruction_ref"], "output_refs": []}


@pytest.mark.parametrize(
    ("mixed", "legacy_handoff"),
    [(False, False), (True, False), (True, True)],
    ids=["tool", "mixed", "legacy-mixed"],
)
async def test_confirmed_registered_tool_result_survives_worker_restart_and_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mixed: bool, legacy_handoff: bool
):
    """Real server history retains a confirmed read result across worker loss.

    The harness pins the relevant existing patch branches, as the in-process
    tests do, without enabling unrelated backend/session/checkpoint services.
    Temporal schedules and records every Activity and child. Only patch
    selection and the external AgentRun implementation are controlled. This
    covers restart after accepted completion; mutation reconciliation remains
    the effect owner's separate coverage.
    """
    from tests.helpers.temporal_visibility import register_deployment_search_attributes

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    document = workspace / "before-restart.md"
    document.write_text("# Saved result\n")
    parameters = _parameters(workspace, mixed=mixed)
    patched = temporal_workflow.patched
    enabled_patches = set(_JOURNEY_PATCHES)
    if legacy_handoff:
        enabled_patches.discard(run_module.RUN_AGENT_STEP_INPUTS_HANDOFF_PATCH)
    monkeypatch.setattr(
        temporal_workflow,
        "patched",
        lambda name: patched(name) if name in enabled_patches else False,
    )
    async with _artifact_service(tmp_path) as service:
        bindings = _bindings(service)
        plan_ref, _ = await _admit(bindings, parameters, "workflow:document-journey")
        async with await WorkflowEnvironment.start_time_skipping() as env:
            await register_deployment_search_attributes(env)

            def orchestration_worker():
                return Worker(
                    env.client,
                    task_queue=WORKFLOW_TASK_QUEUE,
                    workflows=[_RegisteredDocumentServerJourney, _ControlledAgentRun],
                    workflow_runner=UnsandboxedWorkflowRunner(),
                    workflow_failure_exception_types=[Exception],
                    max_cached_workflows=0,
                )

            async with AsyncExitStack() as stack:
                await stack.enter_async_context(
                    Worker(
                        env.client,
                        task_queue=ARTIFACTS_TASK_QUEUE,
                        activities=[
                            bindings[name]
                            for name in (
                                "artifact.read",
                                "artifact.create",
                                "artifact.write_complete",
                            )
                        ],
                    )
                )
                await stack.enter_async_context(
                    Worker(
                        env.client,
                        task_queue=SANDBOX_TASK_QUEUE,
                        activities=[bindings["mm.tool.execute"]],
                    )
                )
                async with orchestration_worker():
                    handle = await env.client.start_workflow(
                        _RegisteredDocumentServerJourney.run,
                        {"parameters": parameters, "plan_ref": plan_ref},
                        id="document-journey",
                        task_queue=WORKFLOW_TASK_QUEUE,
                    )
                    async with asyncio.timeout(20):
                        while not await handle.query(
                            _RegisteredDocumentServerJourney.finished_steps
                        ):
                            await asyncio.sleep(0.05)
                # A restarted worker must reconstruct from the recorded result,
                # not run document.discover against the now-changed filesystem.
                document.unlink()
                # Upgrade the legacy history before replaying on a fresh worker;
                # workflow.patched must preserve its old AgentRun payload.
                enabled_patches.add(run_module.RUN_AGENT_STEP_INPUTS_HANDOFF_PATCH)
                async with orchestration_worker():
                    await handle.signal(_RegisteredDocumentServerJourney.release)
                    result = await asyncio.wait_for(handle.result(), 20)
                    history = await handle.fetch_history()
            assert result["steps"][0]["status"] == "completed"
            scheduled_tools = [
                event
                for event in history.events
                if event.event_type == EventType.EVENT_TYPE_ACTIVITY_TASK_SCHEDULED
                and event.activity_task_scheduled_event_attributes.activity_type.name
                == "mm.tool.execute"
            ]
            assert len(scheduled_tools) == 1
            completed = next(
                event
                for event in history.events
                if event.event_type == EventType.EVENT_TYPE_ACTIVITY_TASK_COMPLETED
                and event.activity_task_completed_event_attributes.scheduled_event_id
                == scheduled_tools[0].event_id
            )
            [recorded_result] = await env.client.data_converter.decode(
                completed.activity_task_completed_event_attributes.result.payloads
            )
            assert recorded_result["outputs"]["documentPaths"] == ["before-restart.md"]
            assert recorded_result["outputs"]["source"] == "filesystem"
            children = [
                event
                for event in history.events
                if event.event_type
                == EventType.EVENT_TYPE_START_CHILD_WORKFLOW_EXECUTION_INITIATED
            ]
            assert len(children) == int(mixed)
            if mixed:
                attributes = children[
                    0
                ].start_child_workflow_execution_initiated_event_attributes
                assert attributes.workflow_type.name == "MoonMind.AgentRun"
                [request] = await env.client.data_converter.decode(
                    attributes.input.payloads
                )
                assert (
                    "before-restart.md" in request["instruction_ref"]
                ) is not legacy_handoff
                assert "json_pointer" not in request["instruction_ref"]
                assert result["steps"][1]["status"] == "completed"
            await Replayer(
                workflows=[_RegisteredDocumentServerJourney, _ControlledAgentRun],
                workflow_runner=UnsandboxedWorkflowRunner(),
            ).replay_workflow(history)


async def test_registered_dispatch_rejects_invalid_resolved_input_nonretryably(
    tmp_path: Path,
):
    from temporalio.exceptions import ApplicationError

    async with _artifact_service(tmp_path) as service:
        bindings = _bindings(service)
        _, plan = await _admit(
            bindings, _parameters(tmp_path, mixed=False), "workflow:document-journey"
        )
        with pytest.raises(
            ApplicationError, match="discover.inputs/directory"
        ) as failure:
            await ActivityEnvironment().run(
                bindings["mm.tool.execute"],
                {
                    "principal": "workflow:document-journey",
                    "registry_snapshot_ref": plan["metadata"]["registry_snapshot"][
                        "artifact_ref"
                    ],
                    "invocation_payload": {
                        "id": "discover",
                        "tool": {"type": "skill", "name": "document.discover"},
                        "inputs": {"directory": 42},
                    },
                },
            )
        assert failure.value.type == "INVALID_INPUT"
        assert failure.value.non_retryable is True
