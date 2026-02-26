from __future__ import annotations

import runpy
from pathlib import Path
from typing import Any


def _load_module() -> dict[str, Any]:
    repo_root = Path(__file__).resolve().parents[2]
    return runpy.run_path(
        str(
            repo_root
            / ".agents"
            / "skills"
            / "batch-pr-resolver"
            / "bin"
            / "batch_pr_resolver.py"
        )
    )


def test_is_local_head_uses_cross_repository_flag():
    module = _load_module()
    is_local_head = module["_is_local_head"]

    pr: dict[str, Any] = {
        "isCrossRepository": True,
        "headRepository": {"nameWithOwner": "", "name": "MoonMind"},
        "headRepositoryOwner": {"login": "MoonLadderStudios"},
    }

    assert is_local_head(pr, "MoonLadderStudios/MoonMind") is False


def test_is_local_head_accepts_same_repo_without_name_with_owner():
    module = _load_module()
    is_local_head = module["_is_local_head"]

    pr: dict[str, Any] = {
        "isCrossRepository": False,
        "headRepository": {"nameWithOwner": "", "name": "MoonMind"},
        "headRepositoryOwner": {"login": "MoonLadderStudios"},
    }

    assert is_local_head(pr, "MoonLadderStudios/MoonMind") is True


def test_is_local_head_rejects_fork_owner_mismatch():
    module = _load_module()
    is_local_head = module["_is_local_head"]

    pr: dict[str, Any] = {
        "isCrossRepository": False,
        "headRepository": {"nameWithOwner": "", "name": "MoonMind"},
        "headRepositoryOwner": {"login": "another-org"},
    }

    assert is_local_head(pr, "MoonLadderStudios/MoonMind") is False


def test_build_queue_request_sets_branch_publish_with_matching_branches():
    module = _load_module()
    build_queue_request = module["_build_queue_request"]
    runtime_selection = module["RuntimeSelection"]

    request = build_queue_request(
        "MoonLadderStudios/MoonMind",
        pr_number=42,
        branch="feature/example",
        runtime=runtime_selection(mode="codex", model="gpt-5-codex", effort="high"),
        merge_method="squash",
        max_iterations=3,
        priority=0,
        max_attempts=3,
    )

    payload = request["payload"]
    task = payload["task"]
    git = task["git"]

    assert payload["targetRuntime"] == "codex"
    assert task["runtime"]["mode"] == "codex"
    assert task["runtime"]["model"] == "gpt-5-codex"
    assert task["runtime"]["effort"] == "high"
    assert task["publish"]["mode"] == "branch"
    assert git["startingBranch"] == "feature/example"
    assert git["newBranch"] == "feature/example"


def test_load_parent_runtime_selection_prefers_runtime_config(tmp_path: Path):
    module = _load_module()
    load_parent_runtime_selection = module["_load_parent_runtime_selection"]

    task_context = tmp_path / "task_context.json"
    task_context.write_text(
        (
            "{"
            '"runtime":"codex",'
            '"runtimeConfig":{"mode":"gemini","model":"gemini-2.5-pro","effort":"medium"}'
            "}"
        ),
        encoding="utf-8",
    )

    runtime = load_parent_runtime_selection(str(task_context))
    assert runtime is not None
    assert runtime.mode == "gemini"
    assert runtime.model == "gemini-2.5-pro"
    assert runtime.effort == "medium"


def test_resolve_runtime_selection_uses_inherited_values(tmp_path: Path):
    module = _load_module()
    resolve_runtime_selection = module["_resolve_runtime_selection"]

    task_context = tmp_path / "task_context.json"
    task_context.write_text(
        (
            "{"
            '"runtimeConfig":{"mode":"claude","model":"claude-3.7-sonnet","effort":"low"}'
            "}"
        ),
        encoding="utf-8",
    )
    args = type(
        "Args",
        (),
        {
            "task_context_path": str(task_context),
            "runtime_mode": None,
            "runtime_model": None,
            "runtime_effort": None,
        },
    )()

    runtime = resolve_runtime_selection(args)
    assert runtime.mode == "claude"
    assert runtime.model == "claude-3.7-sonnet"
    assert runtime.effort == "low"


def test_resolve_runtime_selection_prefers_explicit_over_inherited(tmp_path: Path):
    module = _load_module()
    resolve_runtime_selection = module["_resolve_runtime_selection"]

    task_context = tmp_path / "task_context.json"
    task_context.write_text(
        (
            "{"
            '"runtimeConfig":{"mode":"codex","model":"gpt-5-codex","effort":"low"}'
            "}"
        ),
        encoding="utf-8",
    )
    args = type(
        "Args",
        (),
        {
            "task_context_path": str(task_context),
            "runtime_mode": "gemini",
            "runtime_model": "gemini-2.5-pro",
            "runtime_effort": "high",
        },
    )()

    runtime = resolve_runtime_selection(args)
    assert runtime.mode == "gemini"
    assert runtime.model == "gemini-2.5-pro"
    assert runtime.effort == "high"
