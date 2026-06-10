"""In-flight compatibility regression coverage for the WP5a contract rename.

The MoonMind Task -> Workflow Execution hard switch renames Python symbols in
the core contract layer (``moonmind.workflows.executions``), but serialized
payload shapes, persisted values, and replay-stable patch identifiers written
by ``MoonMind.Run`` histories MUST NOT change until the MoonMind.UserWorkflow
v2 cutover (MM-730).  These tests pin the legacy_run contract so the rename
cannot drift wire shapes for in-flight runs.
"""

from __future__ import annotations

from moonmind.workflows.executions.execution_contract import (
    CanonicalWorkflowExecutionPayload,
    build_canonical_workflow_view,
    build_workflow_stage_plan,
)
from moonmind.workflows.executions.job_types import (
    CANONICAL_WORKFLOW_JOB_TYPE,
    LEGACY_WORKFLOW_JOB_TYPES,
)
from moonmind.workflows.executions.model_resolver import resolve_effective_model

def test_canonical_job_type_value_is_unchanged_for_legacy_run_histories() -> None:
    assert CANONICAL_WORKFLOW_JOB_TYPE == "task"
    assert LEGACY_WORKFLOW_JOB_TYPES == frozenset({"codex_exec", "codex_skill"})

def test_payload_still_accepts_and_serializes_the_legacy_task_envelope() -> None:
    """Previous payload shape: top-level "task" node with task-shaped keys."""

    legacy_shaped = {
        "repository": "MoonLadderStudios/MoonMind",
        "targetRuntime": "codex",
        "task": {
            "instructions": "Implement MM-123",
            "proposeTasks": False,
            "publish": {"mode": "pr"},
        },
    }
    model = CanonicalWorkflowExecutionPayload.model_validate(legacy_shaped)
    dumped = model.model_dump(by_alias=True, exclude_none=False)

    # The serialized envelope key remains "task" until the v2 cutover.
    assert "task" in dumped
    assert dumped["task"]["instructions"] == "Implement MM-123"
    assert dumped["task"]["proposeTasks"] is False

def test_canonical_view_preserves_legacy_wire_keys() -> None:
    canonical = build_canonical_workflow_view(
        job_type=CANONICAL_WORKFLOW_JOB_TYPE,
        payload={
            "repository": "MoonLadderStudios/MoonMind",
            "task": {"instructions": "Implement MM-123"},
        },
    )
    assert set(canonical) >= {"repository", "targetRuntime", "task", "requiredCapabilities"}
    assert canonical["task"]["publish"]["mode"]

def test_stage_plan_values_are_replay_stable() -> None:
    stages = build_workflow_stage_plan(
        {"task": {"publish": {"mode": "pr"}}}
    )
    assert stages == [
        "moonmind.task.prepare",
        "moonmind.task.execute",
        "moonmind.task.publish",
    ]

def test_model_source_value_task_override_is_unchanged() -> None:
    resolved, source = resolve_effective_model(
        runtime_id="codex_cli",
        profile=None,
        requested_model="gpt-5.5",
    )
    assert resolved == "gpt-5.5"
    assert source == "task_override"

def test_workflow_scoped_session_patch_ids_keep_legacy_values() -> None:
    """Replay-stable workflow.patched ids must not change for MoonMind.Run."""

    from moonmind.workflows.temporal.workflows import run as run_module

    assert (
        run_module.RUN_WORKFLOW_SCOPED_SESSION_TERMINATION_PATCH
        == "run-task-scoped-session-termination-v1"
    )
    assert (
        run_module.RUN_WORKFLOW_SCOPED_SESSION_TERMINATION_UPDATE_PATCH
        == "run-task-scoped-session-termination-v2"
    )
    assert (
        run_module.RUN_WORKFLOW_SCOPED_SESSION_TERMINATION_UPDATE_EXECUTE_PATCH
        == "run-task-scoped-session-termination-v3"
    )
    assert (
        run_module.RUN_DEFER_WORKFLOW_SCOPED_SESSION_UNTIL_SLOT_PATCH
        == "run-defer-task-scoped-session-until-slot-v1"
    )
    assert (
        run_module.RUN_WORKFLOW_SCOPED_SESSION_CLEAR_BETWEEN_STEPS_PATCH
        == "run-task-scoped-session-clear-between-steps-v1"
    )
    assert (
        run_module.RUN_WORKFLOW_SCOPED_SESSION_CLEAR_ACTIVITY_SIGNAL_PATCH
        == "run-task-scoped-session-clear-activity-signal-v1"
    )
    assert (
        run_module.RUN_WORKFLOW_SCOPED_SESSION_TERMINATION_ACTIVITY_SIGNAL_PATCH
        == "run-task-scoped-session-termination-v4"
    )

def test_checkpoint_payload_keeps_task_input_snapshot_wire_key() -> None:
    from moonmind.schemas.temporal_models import StepExecutionCheckpointModel

    field = StepExecutionCheckpointModel.model_fields["task_input_snapshot_ref"]
    assert field.alias == "taskInputSnapshotRef"
