"""Replay-safety checks for managed workflow binding in MoonMind.AgentRun."""

import inspect

from moonmind.workflows.temporal.workflows.agent_run import MoonMindAgentRun


def test_managed_launch_binding_uses_temporal_patch_guard() -> None:
    source = inspect.getsource(MoonMindAgentRun.run)

    assert "workflow.patched(MANAGED_TASK_WORKFLOW_BINDING_PATCH_ID)" in source
    assert "task_workflow_id = parent_info.workflow_id" in source
    assert "task_workflow_id = wf_id" in source
