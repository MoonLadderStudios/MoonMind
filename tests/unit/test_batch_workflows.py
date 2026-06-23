"""Unit tests for the batch-workflows fan-out helper (MM-885).

Covers the deterministic per-target child-request construction: issue bindings,
runtime inheritance, shared publish policy, idempotency, the max-workflows cap,
and unsupported-preset skips. The goal text is validated against the real
server-side goal scheduler so a queued child expands the intended child preset.
"""

from __future__ import annotations

import runpy
from pathlib import Path
from typing import Any

import pytest

from moonmind.workflows.executions.execution_contract import (
    WorkflowContractError,
    is_self_managed_publish_skill,
    resolve_publish_mode_for_skill,
)
from moonmind.workflows.executions.preset_goal_scheduler import (
    schedule_preset_from_goal,
)


def _load_module() -> dict[str, Any]:
    repo_root = Path(__file__).resolve().parents[2]
    return runpy.run_path(
        str(
            repo_root
            / ".agents"
            / "skills"
            / "batch-workflows"
            / "bin"
            / "batch_workflows.py"
        )
    )


_JIRA_TARGET: dict[str, Any] = {
    "provider": "jira",
    "ref": "THOR-123",
    "jiraIssue": {
        "key": "THOR-123",
        "summary": "Wire the widget",
        "description": "Body",
        "url": "https://example.atlassian.net/browse/THOR-123",
        "status": "In Progress",
        "assignee": "Nate",
    },
    "repository": "MoonLadderStudios/MoonMind",
}

_GITHUB_TARGET: dict[str, Any] = {
    "provider": "github",
    "ref": "MoonLadderStudios/MoonMind#42",
    "githubIssue": {
        "repository": "MoonLadderStudios/MoonMind",
        "number": 42,
        "title": "Fix the bug",
        "body": "Body",
        "url": "https://github.com/MoonLadderStudios/MoonMind/issues/42",
        "state": "open",
        "labels": ["bug"],
    },
    "repository": "MoonLadderStudios/MoonMind",
}


def _jira_config(module, **overrides):
    defaults = dict(
        preset_slug="jira-implement",
        preset_version="1.1.0",
        preset_scope="global",
        publish_mode="pr",
        constraints="Be careful",
    )
    defaults.update(overrides)
    return module["TargetConfig"](**defaults)


def _github_config(module, **overrides):
    defaults = dict(
        preset_slug="github-issue-implement",
        preset_version="1.0.0",
        preset_scope="global",
        publish_mode="branch",
        constraints="",
    )
    defaults.update(overrides)
    return module["TargetConfig"](**defaults)


def test_bind_child_inputs_jira_auto_binds_issue_object_and_key():
    module = _load_module()
    inputs = module["bind_child_inputs"](_JIRA_TARGET, "jira-implement", "Be careful")
    assert inputs["jira_issue"]["key"] == "THOR-123"
    assert inputs["jira_issue_key"] == "THOR-123"
    assert inputs["constraints"] == "Be careful"


def test_bind_child_inputs_github_auto_binds_issue_object_and_ref():
    module = _load_module()
    inputs = module["bind_child_inputs"](_GITHUB_TARGET, "github-issue-implement", "")
    assert inputs["github_issue"]["number"] == 42
    assert inputs["github_issue_ref"] == "MoonLadderStudios/MoonMind#42"
    assert inputs["constraints"] == ""


def test_bind_child_inputs_returns_none_for_provider_mismatch():
    module = _load_module()
    assert module["bind_child_inputs"](_GITHUB_TARGET, "jira-implement", "") is None
    assert (
        module["bind_child_inputs"](_JIRA_TARGET, "github-issue-implement", "") is None
    )


def test_child_goal_routes_to_selected_preset_via_scheduler():
    module = _load_module()
    jira_goal = module["child_goal_for_target"](_JIRA_TARGET, "jira-implement")
    github_goal = module["child_goal_for_target"](
        _GITHUB_TARGET, "github-issue-implement"
    )
    assert jira_goal is not None
    assert github_goal is not None
    # The queued child goal must expand the intended preset server-side.
    assert schedule_preset_from_goal(jira_goal).slug == "jira-implement"
    assert schedule_preset_from_goal(github_goal).slug == "github-issue-implement"


def test_build_child_request_sets_runtime_inheritance_publish_and_idempotency():
    module = _load_module()
    runtime = module["RuntimeSelection"](
        mode="codex_cli",
        model="gpt-5.5",
        effort="xhigh",
        provider_profile="profile-1",
    )
    request = module["build_child_request"](
        _JIRA_TARGET,
        config=_jira_config(module),
        runtime=runtime,
        batch_scope="run-1",
        inherit_runtime_from_caller=True,
    )
    assert request is not None
    payload = request["payload"]
    # Runtime inheritance contract plus the fallback runtime copy.
    assert payload["runtimeInheritance"] == "caller"
    assert payload["targetRuntime"] == "codex_cli"
    assert payload["task"]["runtime"] == {
        "mode": "codex_cli",
        "model": "gpt-5.5",
        "effort": "xhigh",
        "executionProfileRef": "profile-1",
    }
    # Shared publish policy + repository + bound issue inputs.
    assert payload["task"]["publish"] == {"mode": "pr"}
    assert payload["repository"] == "MoonLadderStudios/MoonMind"
    assert payload["task"]["inputs"]["jira_issue_key"] == "THOR-123"
    assert payload["task"]["batchTargetPreset"]["slug"] == "jira-implement"
    # Stable, length-bounded idempotency key.
    key = payload["idempotencyKey"]
    assert key.startswith("batch-workflows:jira:THOR-123:sha256:")
    assert len(key) <= module["IDEMPOTENCY_KEY_MAX_LENGTH"]


def test_build_child_request_without_caller_omits_inheritance_directive():
    module = _load_module()
    runtime = module["RuntimeSelection"](mode="claude_code")
    request = module["build_child_request"](
        _GITHUB_TARGET,
        config=_github_config(module),
        runtime=runtime,
        batch_scope="run-1",
        inherit_runtime_from_caller=False,
    )
    payload = request["payload"]
    assert "runtimeInheritance" not in payload
    # Fallback runtime is still stamped so the child reuses the caller runtime.
    assert payload["targetRuntime"] == "claude_code"
    assert payload["task"]["publish"] == {"mode": "branch"}


def test_build_child_requests_caps_at_max_workflows():
    module = _load_module()
    targets = [
        {**_JIRA_TARGET, "ref": f"THOR-{n}", "jiraIssue": {"key": f"THOR-{n}"}}
        for n in range(5)
    ]
    submissions, skipped = module["build_child_requests"](
        targets,
        config=_jira_config(module),
        runtime=module["RuntimeSelection"](mode="codex_cli"),
        max_workflows=2,
        batch_scope="run-1",
        inherit_runtime_from_caller=True,
    )
    assert len(submissions) == 2
    overflow = [item for item in skipped if item.reason == "max_workflows_exceeded"]
    assert len(overflow) == 3


def test_build_child_requests_skips_unsupported_preset():
    module = _load_module()
    submissions, skipped = module["build_child_requests"](
        [_JIRA_TARGET],
        config=_jira_config(module, preset_slug="some-custom-preset"),
        runtime=module["RuntimeSelection"](mode="codex_cli"),
        max_workflows=25,
        batch_scope="run-1",
        inherit_runtime_from_caller=True,
    )
    assert submissions == []
    assert skipped[0].reason == "unsupported_preset"


def test_normalize_publish_mode_falls_back_to_pr():
    module = _load_module()
    assert module["_normalize_publish_mode"]("none") == "none"
    assert module["_normalize_publish_mode"]("branch") == "branch"
    assert module["_normalize_publish_mode"]("pr") == "pr"
    assert module["_normalize_publish_mode"]("bogus") == "pr"


def test_batch_workflows_parent_is_self_managed_publish():
    # The parent orchestration queues children and performs no repo publish, so
    # its own publish mode is forced to "none" (parity with batch-pr-resolver).
    assert is_self_managed_publish_skill("batch-workflows") is True
    assert resolve_publish_mode_for_skill("batch-workflows", None) == "none"
    assert resolve_publish_mode_for_skill("batch-workflows", "none") == "none"
    with pytest.raises(WorkflowContractError):
        resolve_publish_mode_for_skill("batch-workflows", "pr")
