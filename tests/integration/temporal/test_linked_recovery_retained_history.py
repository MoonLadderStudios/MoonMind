"""Retained linked-recovery histories survive retirement and worker replacement.

Only external activities are faked. Production workflow code, command emission,
Temporal patch markers, history replay and workflow-worker replacement are real.
"""

from __future__ import annotations

import asyncio
import gzip
import json
from collections.abc import Sequence
from contextlib import AsyncExitStack
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from temporalio import activity, workflow
from temporalio.client import WorkflowHistory
from temporalio.common import (
    RawValue,
    SearchAttributeKey,
    SearchAttributePair,
    TypedSearchAttributes,
)
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

from moonmind.schemas.workflow_recovery_models import (
    deterministic_recovery_creation_key,
)
from moonmind.workflows.executions.runtime_capabilities import (
    resolve_runtime_execution_capabilities,
)
from moonmind.workflows.skills.tool_definitions import default_registry_tool_payload
from moonmind.workflows.skills.tool_registry import (
    compute_registry_digest,
    parse_tool_registry,
)
from moonmind.workflows.temporal.artifacts import TemporalArtifactActivities
from moonmind.workflows.temporal.data_converter import MOONMIND_TEMPORAL_DATA_CONVERTER
from moonmind.workflows.temporal.workflows.run import MoonMindUserWorkflow
from tests.helpers.temporal_visibility import register_deployment_search_attributes

RUN_LINKED_RECOVERY_RETIREMENT_PATCH = "run-linked-recovery-retirement-v1"

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.integration,
    pytest.mark.integration_ci,
    pytest.mark.temporal_boundary,
]

PLAN_REF = "artifact://plan/retained-recovery"
REGISTRY_REF = "artifact://registry/retained-recovery"
CHECKPOINT_REF = "artifact://workspace/before-implement"
RESTORED_REF = "artifact://workspace/restored"
RESTORE_ACTIVITY = "agent_runtime.restore_workspace_checkpoint"
LEGACY_EFFECTS = {
    "step_checkpoint.validate",
    "workspace.apply_policy",
    RESTORE_ACTIVITY,
}


def _parameters(*, state_machine: bool = False) -> dict:
    parameters = {
        "recoverySource": {
            "recoveryAction": "resume_from_workspace_checkpoint",
            "sourceWorkflowId": "mm:source",
            "sourceRunId": "run-source",
            "sourceTaskInputSnapshotRef": "artifact://snapshot/source",
            "sourcePlanRef": PLAN_REF,
            "failedStepId": "implement",
            "failedStepExecution": 1,
            "recoveryCheckpointRef": CHECKPOINT_REF,
            "failedRunRecoveryManifestRef": "artifact://recovery/manifest",
            "recoveryWorkspace": {"checkpointRef": CHECKPOINT_REF},
            "selectedCheckpointBoundary": "before_execution",
            "resumePhase": "rerun_failed_step",
            "targetRuntimeId": "codex_cli",
            "selectedTargetRuntimeId": "codex_cli",
            "capabilitySetVersion": "runtime-execution-capabilities-v2",
            "capabilityDigest": "sha256:test",
            "selectedCapabilityDigest": "sha256:test",
            "checkpointKind": "worktree_archive",
            "checkpointRestoreKinds": ["worktree_archive"],
            "checkpointRestoreActivity": RESTORE_ACTIVITY,
            "registeredRestoreActivity": RESTORE_ACTIVITY,
            "workspaceAuthority": "managed_runtime",
            "checkpointSourceWorkflowId": "mm:source",
            "checkpointSourceRunId": "run-source",
            "sideEffectSafe": True,
            "preservedSteps": [
                {
                    "logicalStepId": "prepare",
                    "status": "succeeded",
                    "sourceExecutionOrdinal": 1,
                    "artifacts": {
                        "outputSummary": "artifact://prepare-summary",
                        "outputPrimary": "artifact://prepare-output",
                    },
                    "stateCheckpointRef": "artifact://workspace/prepare",
                }
            ],
        },
        "workflow": {
            "resume": {
                "kind": "recover_from_failed_step",
                "sourceWorkflowId": "mm:source",
                "sourceRunId": "run-source",
                "failedStepId": "implement",
                "recoveryCheckpointRef": CHECKPOINT_REF,
            }
        },
    }

    if state_machine:
        capabilities = resolve_runtime_execution_capabilities("codex_cli").model_dump(
            by_alias=True, mode="json"
        )
        source = parameters["recoverySource"]
        source["checkpointRecovery"] = {
            "recoveryAction": "resume_from_workspace_checkpoint",
            "resumePhase": "rerun_failed_step",
            "sourceCheckpointRef": CHECKPOINT_REF,
            "sourceCheckpointBoundary": "before_execution",
            "sourceCheckpointKind": "worktree_archive",
            "workspacePolicy": "resume_from_checkpoint",
            "capabilitySnapshot": capabilities,
            "restoreActivity": RESTORE_ACTIVITY,
        }
        parameters["recoveryTarget"] = {
            "target": {
                "kind": "failed_step",
                "logicalStepId": "implement",
                "sourceStepExecutionId": "mm:source:implement:1",
            },
            "source": {
                "workflowId": "mm:source",
                "runId": "run-source",
                "planRef": PLAN_REF,
                "taskInputSnapshotRef": "artifact://snapshot/source",
            },
            "checkpoint": {
                "ref": CHECKPOINT_REF,
                "boundary": "before_execution",
                "kind": "worktree_archive",
                "digest": "checkpoint-digest",
                "validationRef": "artifact://checkpoint/validated",
            },
            "continuation": {"phase": "rerun_failed_step"},
            "capabilitySnapshot": capabilities,
            "sideEffectDispositionRef": "artifact://side-effects/reconciled",
            "sideEffectSafe": True,
            "destination": {
                "workflowId": "set-by-start",
                "creationKey": deterministic_recovery_creation_key(
                    "mm:source",
                    "run-source",
                    "failed_step",
                    "checkpoint-digest",
                    "rerun_failed_step",
                ),
                "runtimeId": "codex_cli",
                "executionProfileRef": "codex-default",
                "workspaceReservationId": "destination-workspace",
            },
        }
    return parameters


class FakeActivities:
    """Real registered activities with observable, deterministic external effects."""

    def __init__(self, *, hold_finish: bool = False, hold_plan: bool = False) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.steps: list[str] = []
        self.plan_started = asyncio.Event()
        self.release_plan = asyncio.Event()
        if not hold_plan:
            self.release_plan.set()
        self.finish_started = asyncio.Event()
        self.release_finish = asyncio.Event()
        if not hold_finish:
            self.release_finish.set()
        self.registry = {
            "skills": [default_registry_tool_payload(name="document.discover")]
        }
        self.plan = {
            "plan_version": "1.0",
            "metadata": {
                "title": "Retained recovery",
                "created_at": "2026-10-18T00:00:00Z",
                "registry_snapshot": {
                    "digest": compute_registry_digest(
                        skills=parse_tool_registry(self.registry)
                    ),
                    "artifact_ref": REGISTRY_REF,
                },
            },
            "policy": {"failure_mode": "FAIL_FAST", "max_concurrency": 1},
            "nodes": [
                {
                    "id": name,
                    "tool": {"type": "skill", "name": "document.discover"},
                    "inputs": {},
                }
                for name in ("prepare", "implement", "finish")
            ],
            "edges": [
                {"from": "prepare", "to": "implement"},
                {"from": "implement", "to": "finish"},
            ],
        }

    @activity.defn(dynamic=True)
    async def dispatch(self, args: Sequence[RawValue]) -> object:
        name = activity.info().activity_type
        body = (
            activity.payload_converter().from_payload(args[0].payload, dict)
            if args
            else {}
        )
        self.calls.append((name, body))
        if name == "provider_profile.list":
            return {"profiles": []}
        if name == "resilience.compile_policy":
            return await TemporalArtifactActivities.resilience_compile_policy(
                None, body
            )
        if name == "artifact.read":
            ref = body["artifact_ref"]
            assert ref in (PLAN_REF, REGISTRY_REF), ref
            if ref == PLAN_REF:
                self.plan_started.set()
                await self.release_plan.wait()
            return json.dumps(self.plan if ref == PLAN_REF else self.registry).encode()
        if name == "artifact.create":
            return [{"artifact_id": f"artifact://test/{len(self.calls)}"}, {}]
        if name in {
            "artifact.write_complete",
            "artifact.link",
            "execution.record_terminal_state",
            "execution.notify_completion",
        }:
            return {}
        if name == "step_checkpoint.validate":
            return {"valid": True}
        if name == RESTORE_ACTIVITY:
            return {
                "status": "succeeded",
                "destinationWorkspaceLocator": {
                    "kind": "managed_runtime",
                    "runtimeId": body["destination"]["runtimeId"],
                    "agentRunId": body["destination"]["agentRunId"],
                    "relativePath": "repo",
                },
                "restorationEvidenceRef": RESTORED_REF,
                "restorationEvidenceDigest": "sha256:" + "a" * 64,
            }
        if name == "workspace.apply_policy":
            return {"workspaceRef": RESTORED_REF, "status": "applied"}
        if name in {"step_checkpoint.create", "step_checkpoint.create_v2"}:
            return {
                "checkpointRef": f"artifact://checkpoint/{body['identity']['logicalStepId']}/{body['boundary']}"
            }
        if name == "mm.tool.execute":
            step = body["invocation_payload"]["id"]
            self.steps.append(step)
            if step == "finish":
                self.finish_started.set()
                await self.release_finish.wait()
            return {
                "status": "COMPLETED",
                "outputs": {"summary": f"{step} done", "documentPaths": []},
            }
        raise ApplicationError(
            f"Unconfigured fake activity: {name}", non_retryable=True
        )


def _workflow_worker(client, queue: str) -> Worker:
    return Worker(
        client,
        task_queue=queue,
        workflows=[MoonMindUserWorkflow],
        workflow_runner=UnsandboxedWorkflowRunner(),
    )


async def _activity_workers(
    stack: AsyncExitStack, client, fake: FakeActivities
) -> None:
    for queue in (
        "mm.activity.artifacts",
        "mm.activity.integrations",
        "mm.activity.sandbox",
        "mm.activity.agent_runtime",
        "mm.workflow",
        "mm.workflow.user.v2",
    ):
        await stack.enter_async_context(
            Worker(client, task_queue=queue, activities=[fake.dispatch])
        )


async def _start(env, queue: str, parameters: dict):
    parameters = deepcopy(parameters)
    workflow_id = f"retained-recovery-{uuid4()}"
    if "recoveryTarget" in parameters:
        parameters["recoveryTarget"]["destination"]["workflowId"] = workflow_id
    return await env.client.start_workflow(
        MoonMindUserWorkflow.run,
        {
            "workflow_type": "MoonMind.UserWorkflow",
            "initial_parameters": deepcopy(parameters),
            "plan_artifact_ref": PLAN_REF,
        },
        id=workflow_id,
        task_queue=queue,
        execution_timeout=timedelta(minutes=5),
        search_attributes=TypedSearchAttributes(
            [
                SearchAttributePair(
                    SearchAttributeKey.for_keyword("mm_owner_id"), "test-owner"
                ),
                SearchAttributePair(
                    SearchAttributeKey.for_keyword("mm_owner_type"), "user"
                ),
            ]
        ),
    )


def _activity_names(history) -> list[str]:
    return [
        event.activity_task_scheduled_event_attributes.activity_type.name
        for event in history.events
        if event.HasField("activity_task_scheduled_event_attributes")
    ]


def _patches(history) -> set[str]:
    return {
        json.loads(marker.details["patch-data"].payloads[0].data)["id"]
        for event in history.events
        if event.HasField("marker_recorded_event_attributes")
        and (marker := event.marker_recorded_event_attributes).marker_name
        == "core_patch"
    }


async def _replay(history) -> None:
    await Replayer(
        workflows=[MoonMindUserWorkflow],
        data_converter=MOONMIND_TEMPORAL_DATA_CONVERTER,
        workflow_runner=UnsandboxedWorkflowRunner(),
    ).replay_workflow(history)


@pytest.mark.parametrize(
    "state_machine", [False, True], ids=["legacy-policy", "typed-state-machine"]
)
async def test_retained_recovery_replays_and_survives_worker_replacement(
    monkeypatch, state_machine
):
    fake = FakeActivities(hold_finish=True)
    expected_effects = (
        [RESTORE_ACTIVITY]
        if state_machine
        else ["step_checkpoint.validate", "workspace.apply_policy"]
    )
    original_patched = workflow.patched
    async with (
        await WorkflowEnvironment.start_local(
            data_converter=MOONMIND_TEMPORAL_DATA_CONVERTER
        ) as env,
        AsyncExitStack() as stack,
    ):
        await register_deployment_search_attributes(env)
        await _activity_workers(stack, env.client, fake)
        queue = f"retained-recovery-{uuid4()}"
        # Record the pre-retirement command stream. Every other patch is real.
        with monkeypatch.context() as historical:
            historical.setattr(
                workflow,
                "patched",
                lambda patch: (
                    False
                    if patch == RUN_LINKED_RECOVERY_RETIREMENT_PATCH
                    else original_patched(patch)
                ),
            )
            async with _workflow_worker(env.client, queue):
                handle = await _start(
                    env, queue, _parameters(state_machine=state_machine)
                )
                await asyncio.wait_for(fake.finish_started.wait(), 30)
                before = await handle.fetch_history()
                ledger = await handle.query("get_step_ledger")
                assert fake.steps == ["implement", "finish"]
                assert [
                    name for name, _ in fake.calls if name in LEGACY_EFFECTS
                ] == expected_effects
                assert RUN_LINKED_RECOVERY_RETIREMENT_PATCH not in _patches(before)
                assert ledger["steps"][0]["preservedFrom"]["workflowId"] == "mm:source"
        # Original worker/cache is gone. Installed code must derive its old
        # branch from history, without executing already-completed effects.
        await _replay(before)
        async with _workflow_worker(env.client, queue):
            fake.release_finish.set()
            result = await asyncio.wait_for(handle.result(), 30)
            after = await handle.fetch_history()
            after_ledger = await handle.query("get_step_ledger")
        assert result["status"] == "success"
        assert fake.steps == ["implement", "finish"]
        assert [
            name for name, _ in fake.calls if name in LEGACY_EFFECTS
        ] == expected_effects
        assert _activity_names(after).count(expected_effects[-1]) == 1
        assert RUN_LINKED_RECOVERY_RETIREMENT_PATCH not in _patches(after)
        assert (
            after_ledger["steps"][0]["preservedFrom"]
            == ledger["steps"][0]["preservedFrom"]
        )
        await _replay(after)


@pytest.mark.parametrize(
    "parameters",
    [{}, _parameters(), _parameters(state_machine=True)],
    ids=["ordinary", "obsolete-recovery-input", "obsolete-typed-recovery-input"],
)
async def test_new_runs_execute_all_steps_without_legacy_recovery(parameters):
    fake = FakeActivities()
    async with (
        await WorkflowEnvironment.start_local(
            data_converter=MOONMIND_TEMPORAL_DATA_CONVERTER
        ) as env,
        AsyncExitStack() as stack,
    ):
        await register_deployment_search_attributes(env)
        await _activity_workers(stack, env.client, fake)
        queue = f"new-run-{uuid4()}"
        async with _workflow_worker(env.client, queue):
            handle = await _start(env, queue, parameters)
            result = await asyncio.wait_for(handle.result(), 30)
            history = await handle.fetch_history()
            ledger = await handle.query("get_step_ledger")
        assert result["status"] == "success"
        assert fake.steps == ["prepare", "implement", "finish"]
        assert not LEGACY_EFFECTS.intersection(_activity_names(history))
        assert RUN_LINKED_RECOVERY_RETIREMENT_PATCH in _patches(history)
        assert all(not row.get("preservedFrom") for row in ledger["steps"])
        await _replay(history)


@pytest.mark.parametrize("fixture", ["legacy-policy", "typed-state-machine"])
async def test_pre_retirement_history_replays_on_installed_workflow(fixture):
    """These immutable histories were recorded using the pre-retirement checkout.

    They are deliberately not regenerated from the candidate under test: doing
    that could bless a changed dependency or deleted legacy command sequence.
    """
    path = (
        Path(__file__).resolve().parents[2]
        / "fixtures"
        / "temporal"
        / "linked_recovery"
        / f"{fixture}.json.gz"
    )
    history_json = json.loads(gzip.decompress(path.read_bytes()))
    workflow_id = history_json["events"][0]["workflowExecutionStartedEventAttributes"][
        "workflowId"
    ]
    history = WorkflowHistory.from_json(workflow_id, history_json)
    names = _activity_names(history)
    assert RUN_LINKED_RECOVERY_RETIREMENT_PATCH not in _patches(history)
    assert history.events[-1].HasField("workflow_execution_completed_event_attributes")
    if fixture == "legacy-policy":
        assert [name for name in names if name in LEGACY_EFFECTS] == [
            "step_checkpoint.validate",
            "workspace.apply_policy",
        ]
    else:
        assert [name for name in names if name in LEGACY_EFFECTS] == [RESTORE_ACTIVITY]
        assert "run-typed-recovery-target-entry-v1" in _patches(history)
        assert "run-checkpoint-recovery-state-machine-v1" in _patches(history)
    await _replay(history)


async def test_restarted_old_run_still_restores_when_recovery_was_not_reached(
    monkeypatch,
):
    """A new live task must not re-evaluate retirement after legacy entry replay."""
    fake = FakeActivities(hold_plan=True)
    original_patched = workflow.patched
    async with (
        await WorkflowEnvironment.start_local(
            data_converter=MOONMIND_TEMPORAL_DATA_CONVERTER
        ) as env,
        AsyncExitStack() as stack,
    ):
        await register_deployment_search_attributes(env)
        await _activity_workers(stack, env.client, fake)
        queue = f"pre-recovery-restart-{uuid4()}"
        with monkeypatch.context() as historical:
            historical.setattr(
                workflow,
                "patched",
                lambda patch: (
                    False
                    if patch == RUN_LINKED_RECOVERY_RETIREMENT_PATCH
                    else original_patched(patch)
                ),
            )
            async with _workflow_worker(env.client, queue):
                handle = await _start(env, queue, _parameters())
                await asyncio.wait_for(fake.plan_started.wait(), 30)
                before = await handle.fetch_history()
                assert not LEGACY_EFFECTS.intersection(_activity_names(before))
                assert not fake.steps
        await _replay(before)
        async with _workflow_worker(env.client, queue):
            fake.release_plan.set()
            result = await asyncio.wait_for(handle.result(), 30)
            after = await handle.fetch_history()
            ledger = await handle.query("get_step_ledger")
        assert result["status"] == "success"
        assert fake.steps == ["implement", "finish"]
        assert [name for name, _ in fake.calls if name in LEGACY_EFFECTS] == [
            "step_checkpoint.validate",
            "workspace.apply_policy",
        ]
        assert ledger["steps"][0]["preservedFrom"]["workflowId"] == "mm:source"
        assert RUN_LINKED_RECOVERY_RETIREMENT_PATCH not in _patches(after)
        await _replay(after)
