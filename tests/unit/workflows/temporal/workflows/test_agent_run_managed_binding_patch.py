"""Replay-safety checks for managed workflow binding in MoonMind.AgentRun."""

import inspect
import re

import pytest

from moonmind.workflows.temporal.workflows import agent_run as agent_run_module
from moonmind.workflows.temporal.workflows.agent_run import (
    AGENT_RUN_WORKFLOW_CHILD_TASK_QUEUE_V2_PATCH,
    MANAGED_RUNTIME_PROFILE_TIER_POLICY_PATCH_ID,
    MoonMindAgentRun,
)


def test_managed_launch_binding_uses_temporal_patch_guard() -> None:
    source = inspect.getsource(MoonMindAgentRun.run)

    assert "workflow.patched(MANAGED_TASK_WORKFLOW_BINDING_PATCH_ID)" in source
    assert "task_workflow_id = parent_info.workflow_id" in source
    assert "task_workflow_id = wf_id" in source
    assert "task_workflow_id=task_workflow_id" in source


def test_managed_launch_profile_tier_policy_is_replay_patched() -> None:
    source = inspect.getsource(MoonMindAgentRun.run)

    assert re.search(
        r"workflow\.patched\(\s*MANAGED_RUNTIME_PROFILE_TIER_POLICY_PATCH_ID\s*\)",
        source,
    )
    assert 'launch_payload["profile_tier_policy_version"]' in source
    assert MANAGED_RUNTIME_PROFILE_TIER_POLICY_PATCH_ID.endswith("-v1")


def test_auto_runtime_rejection_is_replay_patched_at_dispatch() -> None:
    source = inspect.getsource(MoonMindAgentRun.run)

    assert "workflow.patched(RESOLVED_RUNTIME_DISPATCH_PATCH_ID)" in source
    assert "request.agent_id.strip().lower() == AUTO_RUNTIME_SENTINEL" in source


def test_agent_run_workflow_child_task_queue_is_replay_patched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    temporal_settings = agent_run_module.settings.temporal.model_copy(
        update={"user_workflow_v2_task_queue": "mm.workflow.custom.v2"}
    )
    monkeypatch.setattr(agent_run_module.settings, "temporal", temporal_settings)

    monkeypatch.setattr(
        agent_run_module.workflow,
        "patched",
        lambda patch_id: patch_id == AGENT_RUN_WORKFLOW_CHILD_TASK_QUEUE_V2_PATCH,
    )
    assert MoonMindAgentRun._workflow_child_task_queue() == "mm.workflow.custom.v2"

    monkeypatch.setattr(agent_run_module.workflow, "patched", lambda _patch_id: False)
    assert MoonMindAgentRun._workflow_child_task_queue() == "mm.workflow"
