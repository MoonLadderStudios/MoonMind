"""Unit tests for the batch-workflows fan-out helper (MM-1062).

Covers the deterministic per-target child-request construction: issue bindings,
runtime inheritance, shared publish policy, idempotency, the max-workflows cap,
and unsupported-target skips.
"""

from __future__ import annotations

import runpy
import json
import os
import shutil
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from moonmind.workflows.executions.execution_contract import (
    WorkflowContractError,
    is_self_managed_publish_skill,
    resolve_publish_mode_for_skill,
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
        target_kind="skill",
        target_slug="jira-verify",
        publish_mode="none",
        constraints="Be careful",
    )
    defaults.update(overrides)
    return module["TargetConfig"](**defaults)


def _github_config(module, **overrides):
    defaults = dict(
        target_kind="preset",
        target_slug="github-issue-implement",
        publish_mode="branch",
        constraints="",
    )
    defaults.update(overrides)
    return module["TargetConfig"](**defaults)


def test_bind_child_inputs_jira_verify_auto_binds_issue_object_and_key():
    module = _load_module()
    inputs = module["bind_child_inputs"](
        _JIRA_TARGET,
        "skill",
        "jira-verify",
        "Be careful",
    )
    assert inputs["jira_issue"]["key"] == "THOR-123"
    assert inputs["jira_issue_key"] == "THOR-123"
    assert inputs["repository"] == "MoonLadderStudios/MoonMind"
    assert inputs["verification_mode"] == "auto"
    assert inputs["update_status"] is False
    assert inputs["constraints"] == "Be careful"


def test_bind_child_inputs_jira_verify_honors_update_status_flag():
    module = _load_module()
    inputs = module["bind_child_inputs"](
        _JIRA_TARGET,
        "skill",
        "jira-verify",
        "Be careful",
        update_status=True,
    )
    assert inputs["update_status"] is True


def test_bind_child_inputs_jira_verify_uses_fallback_repository_when_missing():
    module = _load_module()
    no_repo_target = dict(_JIRA_TARGET)
    no_repo_target.pop("repository", None)
    inputs = module["bind_child_inputs"](
        no_repo_target,
        "skill",
        "jira-verify",
        "Be careful",
        fallback_repository="MoonLadderStudios/Tactics",
    )
    assert inputs is not None
    assert inputs["repository"] == "MoonLadderStudios/Tactics"


def test_bind_child_inputs_jira_implement_preset_auto_binds_issue_object_and_key():
    module = _load_module()
    inputs = module["bind_child_inputs"](
        _JIRA_TARGET,
        "preset",
        "jira-implement",
        "Be careful",
        run_verify=False,
    )
    assert inputs["jira_issue"]["key"] == "THOR-123"
    assert inputs["jira_issue_key"] == "THOR-123"
    assert inputs["constraints"] == "Be careful"
    assert inputs["run_verify"] is False
    assert "verification_mode" not in inputs


def test_bind_child_inputs_github_auto_binds_issue_object_and_ref():
    module = _load_module()
    inputs = module["bind_child_inputs"](
        _GITHUB_TARGET,
        "preset",
        "github-issue-implement",
        "",
        run_verify=False,
    )
    assert inputs["github_issue"]["number"] == 42
    assert inputs["github_issue_ref"] == "MoonLadderStudios/MoonMind#42"
    assert inputs["constraints"] == ""
    assert inputs["run_verify"] is False


def test_bind_child_inputs_github_orchestrate_auto_binds_issue_object_and_ref():
    module = _load_module()
    inputs = module["bind_child_inputs"](
        _GITHUB_TARGET,
        "preset",
        "github-issue-orchestrate",
        "Keep the change focused",
    )
    assert inputs["github_issue_ref"] == "MoonLadderStudios/MoonMind#42"
    assert inputs["constraints"] == "Keep the change focused"

    goal = module["child_goal_for_target"](
        _GITHUB_TARGET,
        "preset",
        "github-issue-orchestrate",
    )
    assert goal == "Orchestrate GitHub issue MoonLadderStudios/MoonMind#42."


def test_bind_child_inputs_returns_none_for_provider_mismatch():
    module = _load_module()
    assert (
        module["bind_child_inputs"](_GITHUB_TARGET, "skill", "jira-verify", "")
        is None
    )
    assert (
        module["bind_child_inputs"](
            _JIRA_TARGET,
            "preset",
            "github-issue-implement",
            "",
        )
        is None
    )


def test_child_goal_routes_to_selected_run_capability():
    module = _load_module()
    verify_goal = module["child_goal_for_target"](
        _JIRA_TARGET,
        "skill",
        "jira-verify",
    )
    jira_goal = module["child_goal_for_target"](
        _JIRA_TARGET,
        "preset",
        "jira-implement",
    )
    github_goal = module["child_goal_for_target"](
        _GITHUB_TARGET,
        "preset",
        "github-issue-implement",
    )
    assert verify_goal == "Verify Jira issue THOR-123."
    assert jira_goal is not None
    assert github_goal is not None


def test_load_parent_repository_reads_task_context(tmp_path):
    module = _load_module()
    load_parent_repository = module["_load_parent_repository"]

    task_context = tmp_path / "task_context.json"
    task_context.write_text('{"repository":"MoonLadderStudios/Tactics"}', encoding="utf-8")

    assert (
        load_parent_repository(str(task_context))
        == "MoonLadderStudios/Tactics"
    )


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
    assert payload["task"]["publish"] == {"mode": "none"}
    assert payload["repository"] == "MoonLadderStudios/MoonMind"
    assert payload["task"]["inputs"]["jira_issue_key"] == "THOR-123"
    assert payload["task"]["inputs"]["update_status"] is False
    assert payload["requiredCapabilities"] == ["git", "jira"]
    # The selected skill is authored as a direct skill task.
    assert payload["task"]["tool"] == {"type": "skill", "name": "jira-verify"}
    assert "taskTemplate" not in payload["task"]
    assert "batchTargetPreset" not in payload["task"]
    # Stable, length-bounded idempotency key.
    key = payload["idempotencyKey"]
    assert key.startswith("batch-workflows:jira:THOR-123:sha256:")
    assert len(key) <= module["IDEMPOTENCY_KEY_MAX_LENGTH"]


def test_build_child_request_passes_update_status_for_jira_verify():
    module = _load_module()
    request = module["build_child_request"](
        _JIRA_TARGET,
        config=_jira_config(module, update_status=True),
        runtime=module["RuntimeSelection"](mode="codex_cli"),
        batch_scope="run-1",
        inherit_runtime_from_caller=True,
    )

    assert request is not None
    assert request["payload"]["task"]["inputs"]["update_status"] is True


def test_build_child_request_uses_default_repository_when_target_missing():
    module = _load_module()
    target = dict(_JIRA_TARGET)
    target.pop("repository", None)
    request = module["build_child_request"](
        target,
        config=_jira_config(module),
        runtime=module["RuntimeSelection"](mode="codex_cli"),
        batch_scope="run-1",
        inherit_runtime_from_caller=True,
        default_repository="MoonLadderStudios/Alternate",
    )
    assert request is not None
    assert request["payload"]["repository"] == "MoonLadderStudios/Alternate"
    assert request["payload"]["task"]["inputs"]["repository"] == "MoonLadderStudios/Alternate"


def test_idempotency_key_includes_target_kind_and_slug():
    module = _load_module()
    skill_key = module["_child_idempotency_key"](
        batch_scope="run-1",
        provider="jira",
        ref="THOR-123",
        target_kind="skill",
        target_slug="jira-verify",
    )
    preset_key = module["_child_idempotency_key"](
        batch_scope="run-1",
        provider="jira",
        ref="THOR-123",
        target_kind="preset",
        target_slug="jira-implement",
    )

    assert skill_key != preset_key
    assert skill_key.startswith("batch-workflows:jira:THOR-123:sha256:")
    assert preset_key.startswith("batch-workflows:jira:THOR-123:sha256:")


def test_build_child_request_authors_selected_preset_as_global_template():
    module = _load_module()
    request = module["build_child_request"](
        _JIRA_TARGET,
        config=_jira_config(
            module,
            target_kind="preset",
            target_slug="jira-implement",
        ),
        runtime=module["RuntimeSelection"](mode="codex_cli"),
        batch_scope="run-1",
        inherit_runtime_from_caller=True,
    )
    template = request["payload"]["task"]["taskTemplate"]
    assert template["slug"] == "jira-implement"
    assert template["scope"] == "global"
    assert "version" not in template
    assert "scopeRef" not in template
    assert "batchTargetPreset" not in request["payload"]["task"]


def test_parse_args_accepts_run_ref_without_preset_version(tmp_path):
    module = _load_module()
    targets = tmp_path / "targets.json"
    targets.write_text("[]", encoding="utf-8")

    args = module["_parse_args"](
        [
            "--targets",
            str(targets),
            "--run-ref",
            "skill:jira-verify",
            "--publish-mode",
            "pr",
            "--max-workflows",
            "1",
        ]
    )

    assert args.run_ref == "skill:jira-verify"
    assert args.update_status is False
    assert not hasattr(args, "target_preset_version")


def test_parse_args_accepts_update_status_flag(tmp_path):
    module = _load_module()
    targets = tmp_path / "targets.json"
    targets.write_text("[]", encoding="utf-8")

    args = module["_parse_args"](
        [
            "--targets",
            str(targets),
            "--run-ref",
            "skill:jira-verify",
            "--publish-mode",
            "none",
            "--update-status",
        ]
    )

    assert args.update_status is True


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


def test_build_child_request_maps_merge_automation_publish_mode_to_contract():
    module = _load_module()
    request = module["build_child_request"](
        _JIRA_TARGET,
        config=_jira_config(module, publish_mode="pr_with_merge_automation"),
        runtime=module["RuntimeSelection"](mode="codex_cli"),
        batch_scope="run-1",
        inherit_runtime_from_caller=True,
    )

    assert request is not None
    assert request["payload"]["task"]["publish"] == {
        "mode": "pr",
        "mergeAutomation": {"enabled": True},
    }


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


def test_build_child_requests_zero_max_workflows_skips_all_targets():
    module = _load_module()
    targets = [
        {**_JIRA_TARGET, "ref": f"THOR-{n}", "jiraIssue": {"key": f"THOR-{n}"}}
        for n in range(3)
    ]
    submissions, skipped = module["build_child_requests"](
        targets,
        config=_jira_config(module),
        runtime=module["RuntimeSelection"](mode="codex_cli"),
        max_workflows=0,
        batch_scope="run-1",
        inherit_runtime_from_caller=True,
    )
    assert submissions == []
    assert [item.ref for item in skipped] == ["THOR-0", "THOR-1", "THOR-2"]
    assert {item.reason for item in skipped} == {"max_workflows_exceeded"}


def test_build_child_requests_negative_max_workflows_skips_all_targets():
    module = _load_module()
    targets = [
        {**_JIRA_TARGET, "ref": f"THOR-{n}", "jiraIssue": {"key": f"THOR-{n}"}}
        for n in range(3)
    ]
    submissions, skipped = module["build_child_requests"](
        targets,
        config=_jira_config(module),
        runtime=module["RuntimeSelection"](mode="codex_cli"),
        max_workflows=-2,
        batch_scope="run-1",
        inherit_runtime_from_caller=True,
    )
    assert submissions == []
    assert [item.ref for item in skipped] == ["THOR-0", "THOR-1", "THOR-2"]
    assert {item.reason for item in skipped} == {"max_workflows_exceeded"}


def test_build_child_requests_skips_unsupported_target():
    module = _load_module()
    submissions, skipped = module["build_child_requests"](
        [_JIRA_TARGET],
        config=_jira_config(
            module,
            target_kind="skill",
            target_slug="some-custom-skill",
        ),
        runtime=module["RuntimeSelection"](mode="codex_cli"),
        max_workflows=25,
        batch_scope="run-1",
        inherit_runtime_from_caller=True,
    )
    assert submissions == []
    assert skipped[0].reason == "unsupported_target"


def test_normalize_publish_mode_falls_back_to_pr():
    module = _load_module()
    assert module["_normalize_publish_mode"]("none") == "none"
    assert module["_normalize_publish_mode"]("branch") == "branch"
    assert module["_normalize_publish_mode"]("pr") == "pr"
    assert (
        module["_normalize_publish_mode"]("pr_with_merge_automation")
        == "pr_with_merge_automation"
    )
    assert module["_normalize_publish_mode"]("bogus") == "pr"


def test_batch_workflows_parent_is_side_effect_only_publish():
    # The parent orchestration queues children and performs no repo publish, so
    # its own publish mode is not resolved to agent-owned repository publishing.
    assert is_self_managed_publish_skill("batch-workflows") is False
    assert resolve_publish_mode_for_skill("batch-workflows", None) == "none"
    with pytest.raises(WorkflowContractError):
        resolve_publish_mode_for_skill("batch-workflows", "auto")
    with pytest.raises(WorkflowContractError):
        resolve_publish_mode_for_skill("batch-workflows", "pr")


def test_materialized_snapshot_queues_five_targets_from_external_repo(tmp_path):
    """The active snapshot wins even when the target repo owns conflicting skills."""
    repo_root = Path(__file__).resolve().parents[2]
    snapshot = tmp_path / "skills_active"
    shutil.copytree(repo_root / ".agents" / "skills" / "batch-workflows", snapshot / "batch-workflows")
    shutil.copytree(repo_root / ".agents" / "skills" / "_shared", snapshot / "_shared")
    external_repo = tmp_path / "external-repo"
    (external_repo / ".agents" / "skills" / "batch-workflows" / "bin").mkdir(parents=True)
    (external_repo / ".agents" / "skills" / "batch-workflows" / "bin" / "batch_workflows.py").write_text(
        "raise SystemExit('repository-owned helper executed')\n", encoding="utf-8"
    )
    trap = tmp_path / "import-trap"
    trap.mkdir()
    for name in ("moonmind.py", "api_service.py"):
        (trap / name).write_text("raise AssertionError('service graph imported')\n", encoding="utf-8")
    targets = [
        {"provider": "jira", "ref": f"THOR-{number}", "jiraIssue": {"key": f"THOR-{number}"}, "repository": "acme/widgets"}
        for number in range(705, 710)
    ]
    (external_repo / "targets.json").write_text(json.dumps(targets), encoding="utf-8")

    posts: list[dict[str, Any]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            length = int(self.headers["Content-Length"])
            posts.append(json.loads(self.rfile.read(length)))
            body = json.dumps({"workflowId": f"child-{len(posts)}"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            return None

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    env = {
        **os.environ,
        "PYTHONPATH": str(trap),
        "MOONMIND_URL": f"http://127.0.0.1:{server.server_port}",
        "MOONMIND_STEP_EXECUTION_ID": "step-external-1",
        "MOONMIND_ACTIVE_SKILLS_DIR": str(snapshot),
    }
    try:
        completed = subprocess.run(
            [
                sys.executable,
                str(snapshot / "batch-workflows" / "bin" / "batch_workflows.py"),
                "--targets", "targets.json", "--run-ref", "skill:jira-verify",
                "--publish-mode", "none", "--artifacts-dir", str(external_repo / "artifacts"),
            ],
            cwd=external_repo,
            env=env,
            text=True,
            capture_output=True,
            timeout=20,
            check=False,
        )
    finally:
        server.shutdown()
        thread.join(timeout=5)
    assert completed.returncode == 0, completed.stderr
    assert len(posts) == 5
    keys = [post["payload"]["idempotencyKey"] for post in posts]
    assert len(set(keys)) == 5
    evidence = json.loads((external_repo / "artifacts" / "batch-workflows-result.json").read_text())
    assert evidence["executionRef"] == "step-external-1"
    assert evidence["created"] == 5
    assert [item["ref"] for item in evidence["queued"]] == [f"THOR-{n}" for n in range(705, 710)]
