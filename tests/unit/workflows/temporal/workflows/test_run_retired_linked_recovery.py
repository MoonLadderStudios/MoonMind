"""New runs have no linked failed-step recovery mode.

Linked recovery executions started with a frozen recovery input
(``recoverySource``, ``recoveryTarget``, ``workflow.resume``) that preserved
earlier steps and restored the failed step's workspace. Retry from a step
replaced them, so a run that still carries those keys executes its whole plan
like any other run.
"""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

import pytest
from temporalio import workflow
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from moonmind.workflows.temporal.workflows.run import (
    RUN_LINKED_RECOVERY_RETIREMENT_PATCH,
    MoonMindUserWorkflow,
)

_REGISTRY_REF = "art:sha256:registry"
_PLAN_REF = "art:sha256:plan"

_RETIRED_LINKED_RECOVERY_PARAMETERS: dict[str, Any] = {
    "recoverySource": {
        "recoveryAction": "resume_from_workspace_checkpoint",
        "sourceWorkflowId": "mm:source",
        "sourceRunId": "run-source",
        "sourceTaskInputSnapshotRef": "artifact://snapshot/source",
        "sourcePlanDigest": "sha256:source-plan",
        "failedStepId": "implement",
        "failedStepExecution": 1,
        "recoveryCheckpointRef": "artifact://workspace/before-implement",
        "failedRunRecoveryManifestRef": "artifact://recovery/manifest",
        "recoveryWorkspace": {"checkpointRef": "artifact://workspace/before-implement"},
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
    "recoveryTarget": {
        "target": {"kind": "failed_step", "logicalStepId": "implement"},
        "continuation": {"phase": "rerun_failed_step"},
    },
    "workflow": {
        "recovery": {
            "kind": "recover_from_failed_step",
            "sourceWorkflowId": "mm:source",
            "sourceRunId": "run-source",
        },
        "resume": {
            "kind": "recover_from_failed_step",
            "sourceWorkflowId": "mm:source",
            "sourceRunId": "run-source",
            "failedStepId": "implement",
            "recoveryCheckpointRef": "artifact://workspace/before-implement",
            "taskInputSnapshotRef": "artifact://snapshot/source",
        },
    },
}


def _tool(name: str) -> dict[str, Any]:
    return {
        "name": name,
        "description": f"{name} deterministic tool",
        "inputs": {"schema": {"type": "object"}},
        "outputs": {"schema": {"type": "object"}},
        "executor": {
            "activity_type": "mm.tool.execute",
            "selector": {"mode": "by_capability"},
        },
        "requirements": {"capabilities": ["integration:github"]},
        "policies": {
            "timeouts": {"start_to_close_seconds": 60, "schedule_to_close_seconds": 120},
            "retries": {"max_attempts": 1},
        },
    }


def _plan() -> bytes:
    return json.dumps(
        {
            "plan_version": "1.0",
            "metadata": {
                "title": "Retired recovery input",
                "created_at": "2026-10-18T00:00:00Z",
                "registry_snapshot": {"digest": "reg:sha256:1", "artifact_ref": _REGISTRY_REF},
            },
            "policy": {"failure_mode": "FAIL_FAST", "max_concurrency": 1},
            "nodes": [
                {"id": "prepare", "tool": {"type": "skill", "name": "test.prepare"}, "inputs": {}},
                {"id": "implement", "tool": {"type": "skill", "name": "test.implement"}, "inputs": {}},
            ],
            "edges": [{"from": "prepare", "to": "implement"}],
        }
    ).encode()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "parameters",
    [{}, _RETIRED_LINKED_RECOVERY_PARAMETERS],
    ids=["ordinary-run", "retired-linked-recovery-input"],
)
async def test_new_run_never_enters_linked_recovery_mode(
    monkeypatch: pytest.MonkeyPatch, parameters: dict[str, Any]
) -> None:
    activity_types: list[str] = []
    dispatched: list[str] = []

    async def fake_execute_activity(activity_type: str, payload: Any, **_kwargs: Any) -> Any:
        activity_types.append(activity_type)
        body = payload if isinstance(payload, dict) else payload.model_dump(by_alias=False)
        if activity_type == "artifact.read":
            if body.get("artifact_ref") == _REGISTRY_REF:
                return json.dumps(
                    {"skills": [_tool("test.prepare"), _tool("test.implement")]}
                ).encode()
            return _plan()
        if activity_type == "mm.tool.execute":
            dispatched.append(body["invocation_payload"]["id"])
            return {"status": "COMPLETED", "outputs": {"summary": "done"}}
        return {"status": "COMPLETED", "outputs": {}}

    async def fake_planning_stage(self, **_kwargs: Any) -> str:
        return _PLAN_REF

    monkeypatch.setattr(
        MoonMindUserWorkflow, "_trusted_owner_metadata", lambda self: ("user", "user-1")
    )
    monkeypatch.setattr(MoonMindUserWorkflow, "_run_planning_stage", fake_planning_stage)
    monkeypatch.setattr(workflow, "execute_activity", fake_execute_activity)
    # Keep patch-gated side paths (resilience policy, checkpoint capture, ...)
    # out of this run while selecting the new-run retirement path. Retained
    # command histories are exercised separately with the actual Replayer.
    monkeypatch.setattr(
        workflow, "patched",
        lambda patch_id: patch_id == RUN_LINKED_RECOVERY_RETIREMENT_PATCH,
    )
    monkeypatch.setattr(workflow, "upsert_search_attributes", lambda _attrs: None)
    monkeypatch.setattr(workflow, "upsert_memo", lambda _memo: None)

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="retired-linked-recovery",
            workflows=[MoonMindUserWorkflow],
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            handle = await env.client.start_workflow(
                MoonMindUserWorkflow.run,
                {
                    "workflow_type": "MoonMind.UserWorkflow",
                    "initial_parameters": json.loads(json.dumps(parameters)),
                    "plan_artifact_ref": _PLAN_REF,
                },
                id=f"retired-linked-recovery-{len(parameters)}",
                task_queue="retired-linked-recovery",
                execution_timeout=timedelta(minutes=5),
            )
            result = await handle.result()
            # The query below replays the history, which calls the in-process
            # activity fake again, so read the run's own calls first.
            run_dispatched = list(dispatched)
            run_activity_types = set(activity_types)
            ledger = await handle.query("get_step_ledger")

    assert result["status"] == "success"
    # Every plan step runs as a first execution of this run.
    assert run_dispatched == ["prepare", "implement"]
    rows = {row["logicalStepId"]: row for row in ledger["steps"]}
    for row in rows.values():
        assert row["status"] == "completed"
        assert row["executionOrdinal"] == 1
        assert row.get("preservedFrom") is None
        assert row["timing"].get("preserved") is not True
    # No workspace restore of a failed source run.
    assert not {"step_checkpoint.validate", "workspace.apply_policy"} & run_activity_types


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("continued_run_id", "carried", "patch_enabled", "retired"),
    [
        (None, {}, True, True),
        (None, {"linkedRecoveryRetired": False}, True, True),
        ("previous-run", {}, True, False),
        ("previous-run", {"linkedRecoveryRetired": False}, True, False),
        ("previous-run", {"linkedRecoveryRetired": True}, True, True),
        ("previous-run", {"linkedRecoveryRetired": True}, False, False),
    ],
)
async def test_retirement_choice_preserves_genuine_continuations_only(
    monkeypatch: pytest.MonkeyPatch,
    continued_run_id: str | None,
    carried: dict[str, Any],
    patch_enabled: bool,
    retired: bool,
) -> None:
    from types import SimpleNamespace

    from temporalio.exceptions import ApplicationError

    instance = MoonMindUserWorkflow()
    monkeypatch.setattr(workflow, "info", lambda: SimpleNamespace(
        continued_run_id=continued_run_id,
    ))
    monkeypatch.setattr(workflow, "patched", lambda _patch: patch_enabled)

    def stop_after_decision(_payload: Any) -> Any:
        raise ValueError("stop after retirement decision")

    monkeypatch.setattr(instance, "_initialize_from_payload", stop_after_decision)
    with pytest.raises(ApplicationError, match="stop after retirement decision"):
        await instance.run({"remediation_loop_continuation": carried})
    assert instance._linked_recovery_retired is retired


@pytest.mark.parametrize("retired", [False, True])
def test_continue_as_new_carries_frozen_retirement_choice(
    monkeypatch: pytest.MonkeyPatch, retired: bool,
) -> None:
    from moonmind.workflows.temporal.remediation_loop import RemediationLoopState

    instance = MoonMindUserWorkflow()
    instance._linked_recovery_retired = retired
    instance._remediation_loop_state = RemediationLoopState(
        loopId="loop", phase="verification_pending", consumedBudgets={}
    )
    instance._original_input_payload = {}
    monkeypatch.setattr(workflow, "patched", lambda _patch: False)
    carried = instance._build_remediation_loop_continue_as_new_input(ordered_nodes=[])
    assert carried["remediation_loop_continuation"]["linkedRecoveryRetired"] is retired
