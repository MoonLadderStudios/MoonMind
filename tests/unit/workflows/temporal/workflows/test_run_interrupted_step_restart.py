"""Interrupted agent steps restart through MoonMind.Run's real step loop.

MoonLadderStudios/MoonMind#4627: when an update replaces the host running an
agent step, only that step receives a new bounded Step Execution. Completed
predecessors are not repeated, the successor restores the predecessor's
verified saved workspace (or honestly starts from admitted inputs when none
exists), repeated interruptions spend one retry budget, an exhausted budget is
a truthful failure, and cancellation starts no successor.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

pytest.importorskip("temporalio")

from moonmind.schemas.agent_runtime_models import AgentExecutionRequest, AgentRunResult
from moonmind.workflows.temporal.workflows import run as run_workflow_module
from moonmind.workflows.temporal.workflows.run import (
    RUN_AGENT_RUNTIME_RETRY_CLASSIFICATION_PATCH,
    RUN_CONDITIONAL_REGISTRY_READ_PATCH,
    RUN_EXPLICIT_STEP_RETRY_RECOMMENDATION_PATCH,
    RUN_INTERRUPTED_STEP_SAVED_WORKSPACE_RESTORE_PATCH,
    RUN_STEP_EXECUTION_MANIFEST_PATCH,
    MoonMindRunWorkflow,
)
from tests.unit.workflows.temporal.workflows.test_run_integration import (
    _immediate_wait_condition,
    _mock_plan_payload,
)

_STEPS = ("first", "second", "interrupted")


def _host_lost(saved: dict | None = None) -> AgentRunResult:
    metadata: dict = {"workPreserved": saved is not None}
    if saved is not None:
        metadata["savedWorkspaceCheckpoint"] = saved
    return AgentRunResult(
        summary="Omnigent session host was lost before the turn finished",
        failureClass="integration_error",
        providerErrorCode="OMNIGENT_SESSION_HOST_LOST",
        retryRecommendation="retry_step_execution",
        metadata=metadata,
    )


def _saved(name: str) -> dict:
    """The realizer's verified ``save_request_workspace`` evidence."""

    return {
        "kind": "worktree_archive",
        "baseCommit": "a" * 40,
        "archiveRef": f"artifact://{name}-archive",
        "archiveDigest": "sha256:" + "1" * 64,
        "manifestRef": f"artifact://{name}-manifest",
        "manifestDigest": "sha256:" + "2" * 64,
        "workspaceDigest": "sha256:" + "3" * 64,
        "checkpointRef": f"artifact://{name}-checkpoint",
    }


def _install_run(monkeypatch, workflow, interrupted_results, *, manifests=None):
    """Run a three-step plan whose last agent step receives scripted results."""

    children: list[tuple[str, AgentExecutionRequest]] = []
    results = list(interrupted_results)

    async def fake_execute_typed_activity(activity_type, payload, **_kwargs):
        assert activity_type == "artifact.read"
        return _mock_plan_payload(
            [
                {
                    "id": step,
                    "tool": {"type": "agent_runtime", "name": "omnigent"},
                    "inputs": {
                        "instructions": f"Run the {step} step.",
                        "runtime": {"mode": "omnigent"},
                    },
                }
                for step in _STEPS
            ],
            edges=[
                {"from": "first", "to": "second"},
                {"from": "second", "to": "interrupted"},
            ],
        )

    async def fake_execute_child_workflow(workflow_name, request, **kwargs):
        assert workflow_name == "MoonMind.AgentRun"
        children.append((str(kwargs["id"]), request))
        if not str(kwargs["id"]).split(":agent:", 1)[1].startswith("interrupted"):
            return AgentRunResult(summary="Completed.")
        outcome = results.pop(0)
        return outcome(workflow) if callable(outcome) else outcome

    async def fake_bind(request):
        return request

    async def fake_record_manifest(logical_step_id, **kwargs):
        if manifests is not None:
            manifests.append((logical_step_id, kwargs))
        return None

    async def fake_retrieval_ref(request, **_kwargs):
        return request

    enabled = {
        RUN_CONDITIONAL_REGISTRY_READ_PATCH,
        RUN_AGENT_RUNTIME_RETRY_CLASSIFICATION_PATCH,
        RUN_EXPLICIT_STEP_RETRY_RECOMMENDATION_PATCH,
        RUN_INTERRUPTED_STEP_SAVED_WORKSPACE_RESTORE_PATCH,
    }
    if manifests is not None:
        enabled.add(RUN_STEP_EXECUTION_MANIFEST_PATCH)
    info = type(
        "WorkflowInfo",
        (),
        {
            "namespace": "default",
            "workflow_id": "wf-update",
            "run_id": "run-update",
            "search_attributes": {},
        },
    )
    module_workflow = run_workflow_module.workflow
    monkeypatch.setattr(module_workflow, "info", info)
    monkeypatch.setattr(module_workflow, "upsert_memo", lambda _memo: None)
    monkeypatch.setattr(module_workflow, "upsert_search_attributes", lambda _a: None)
    monkeypatch.setattr(module_workflow, "now", lambda: datetime.now(timezone.utc))
    monkeypatch.setattr(
        module_workflow,
        "logger",
        type("Logger", (), {"info": lambda *a, **k: None, "warning": lambda *a, **k: None}),
    )
    monkeypatch.setattr(
        run_workflow_module, "execute_typed_activity", fake_execute_typed_activity
    )
    monkeypatch.setattr(
        module_workflow, "execute_child_workflow", fake_execute_child_workflow
    )
    monkeypatch.setattr(module_workflow, "patched", lambda patch: patch in enabled)
    monkeypatch.setattr(module_workflow, "wait_condition", _immediate_wait_condition)
    monkeypatch.setattr(workflow, "_maybe_bind_workflow_scoped_session", fake_bind)
    monkeypatch.setattr(workflow, "_record_step_execution_manifest", fake_record_manifest)
    monkeypatch.setattr(
        workflow, "_request_with_persisted_retrieval_ref", fake_retrieval_ref
    )
    return children


def _workflow() -> MoonMindRunWorkflow:
    workflow = MoonMindRunWorkflow()
    workflow._owner_id = "owner-1"
    workflow._repo = "org/repo"
    workflow._integration = None
    return workflow


def _ids(children):
    return [child_id for child_id, _ in children]


def _row(workflow, logical_step_id):
    return next(
        row for row in workflow._step_ledger_rows if row["logicalStepId"] == logical_step_id
    )


def _intent(request):
    # ``metadata`` carries the per-attempt ledger and execution context.
    return {key: value for key, value in request.parameters.items() if key != "metadata"}


@pytest.mark.asyncio
async def test_host_loss_restarts_only_the_interrupted_step(monkeypatch):
    workflow = _workflow()
    children = _install_run(
        monkeypatch,
        workflow,
        [_host_lost(), AgentRunResult(summary="Completed on the new attempt.")],
    )

    await workflow._run_execution_stage(
        parameters={"publishMode": "none"}, plan_ref="plan-ref"
    )

    assert _ids(children) == [
        "wf-update:agent:first",
        "wf-update:agent:second",
        "wf-update:agent:interrupted",
        "wf-update:agent:interrupted:attempt2:retry1",
    ]
    (_, predecessor), (_, successor) = children[2], children[3]
    # A new attempt of the same workflow and logical step, not a resubmission.
    assert successor.idempotency_key != predecessor.idempotency_key
    assert successor.step_execution.logical_step_id == "interrupted"
    assert predecessor.step_execution.execution_ordinal == 1
    assert successor.step_execution.execution_ordinal == 2
    assert successor.step_execution.reason == "runtime_recovered"
    # Harness, Provider Profile, model and publication intent are unchanged.
    assert successor.agent_id == predecessor.agent_id
    assert successor.execution_profile_ref == predecessor.execution_profile_ref
    assert _intent(successor) == _intent(predecessor)
    # The publication target is the logical step's, not the attempt's; only
    # the per-attempt sandbox locator changes.
    assert {
        k: v for k, v in successor.workspace_spec.items() if k != "workspaceLocator"
    } == {
        k: v for k, v in predecessor.workspace_spec.items() if k != "workspaceLocator"
    }
    assert successor.omnigent_execution_plan == predecessor.omnigent_execution_plan
    assert _row(workflow, "interrupted")["status"] == "completed"
    for step in ("first", "second"):
        assert _row(workflow, step)["attempt"] == 1


def _start_workspace(manifests, attempt_index):
    starts = [
        kwargs
        for step, kwargs in manifests
        if step == "interrupted" and kwargs["phase"] == "start"
    ]
    return starts[attempt_index]["execution"].get("interruptedStepWorkspace")


@pytest.mark.asyncio
async def test_successor_restores_the_predecessors_verified_saved_workspace(
    monkeypatch,
):
    workflow = _workflow()
    manifests: list = []
    children = _install_run(
        monkeypatch,
        workflow,
        [_host_lost(_saved("first-attempt")), AgentRunResult(summary="Completed.")],
        manifests=manifests,
    )

    await workflow._run_execution_stage(
        parameters={"publishMode": "none"}, plan_ref="plan-ref"
    )

    (_, predecessor), (_, successor) = children[2], children[3]
    assert "workspaceCheckpointRestoreRef" not in predecessor.workspace_spec
    # The successor's fresh sandbox is materialized from the saved bytes
    # through the existing Omnigent checkpoint-restore boundary.
    assert successor.workspace_spec["workspaceCheckpointRestoreRef"] == (
        "artifact://first-attempt-archive"
    )
    assert successor.step_execution.reason == "runtime_recovered"
    assert _start_workspace(manifests, 0) is None
    assert _start_workspace(manifests, 1) == {
        "source": "saved_workspace",
        "archiveRef": "artifact://first-attempt-archive",
        "archiveDigest": "sha256:" + "1" * 64,
        "savedByExecutionOrdinal": 1,
    }
    assert _row(workflow, "interrupted")["status"] == "completed"


@pytest.mark.asyncio
async def test_successor_without_a_verified_save_says_it_starts_from_inputs(
    monkeypatch,
):
    workflow = _workflow()
    manifests: list = []
    unverified = {**_saved("dead-container"), "archiveRef": "/work/dead/repo"}
    children = _install_run(
        monkeypatch,
        workflow,
        [_host_lost(unverified), AgentRunResult(summary="Completed.")],
        manifests=manifests,
    )

    await workflow._run_execution_stage(
        parameters={"publishMode": "none"}, plan_ref="plan-ref"
    )

    successor = children[3][1]
    # A dead container path is not a save; nothing is restored from it.
    assert "workspaceCheckpointRestoreRef" not in successor.workspace_spec
    assert _start_workspace(manifests, 1) == {
        "source": "admitted_inputs",
        "reason": "no_verified_saved_workspace",
    }


@pytest.mark.asyncio
async def test_repeated_interruption_keeps_the_latest_verified_save(monkeypatch):
    workflow = _workflow()
    manifests: list = []
    children = _install_run(
        monkeypatch,
        workflow,
        [
            _host_lost(_saved("attempt-1")),
            # The second host is lost before it could save anything new.
            _host_lost(),
            _host_lost(_saved("attempt-3")),
            AgentRunResult(summary="Completed."),
        ],
        manifests=manifests,
    )

    await workflow._run_execution_stage(
        parameters={"publishMode": "none"}, plan_ref="plan-ref"
    )

    restored = [
        request.workspace_spec.get("workspaceCheckpointRestoreRef")
        for child_id, request in children
        if ":agent:interrupted" in child_id
    ]
    assert restored == [
        None,
        "artifact://attempt-1-archive",
        "artifact://attempt-1-archive",
        "artifact://attempt-3-archive",
    ]
    assert _start_workspace(manifests, 2)["savedByExecutionOrdinal"] == 1
    assert _start_workspace(manifests, 3)["savedByExecutionOrdinal"] == 3


@pytest.mark.asyncio
async def test_repeated_interruptions_spend_one_budget_then_fail_truthfully(
    monkeypatch,
):
    workflow = _workflow()
    children = _install_run(monkeypatch, workflow, [_host_lost()] * 4)

    with pytest.raises(ValueError, match="OMNIGENT_SESSION_HOST_LOST"):
        await workflow._run_execution_stage(
            parameters={"publishMode": "none"}, plan_ref="plan-ref"
        )

    interrupted = [
        request for child_id, request in children if ":agent:interrupted" in child_id
    ]
    # Each interruption is a new ordinal; a later update never resets the count.
    assert [r.step_execution.execution_ordinal for r in interrupted] == [1, 2, 3, 4]
    assert _ids(children)[-1] == "wf-update:agent:interrupted:attempt4:retry3"
    assert _ids(children).count("wf-update:agent:first") == 1
    assert _ids(children).count("wf-update:agent:second") == 1
    assert _row(workflow, "interrupted")["status"] == "failed"


@pytest.mark.asyncio
async def test_cancellation_during_host_loss_starts_no_successor(monkeypatch):
    workflow = _workflow()

    def cancelled_then_host_lost(running_workflow):
        # The operator cancels while the interrupted attempt is reported.
        running_workflow._cancel_requested = True
        return _host_lost()

    children = _install_run(monkeypatch, workflow, [cancelled_then_host_lost])

    await workflow._run_execution_stage(
        parameters={"publishMode": "none"}, plan_ref="plan-ref"
    )

    assert _ids(children) == [
        "wf-update:agent:first",
        "wf-update:agent:second",
        "wf-update:agent:interrupted",
    ]
    assert _row(workflow, "interrupted")["attempt"] == 1
