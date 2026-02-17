"""Unit tests for canonical task payload normalization helpers."""

from __future__ import annotations

import pytest

from moonmind.workflows.agent_queue.task_contract import (
    TaskContractError,
    build_canonical_task_view,
    build_task_stage_plan,
    normalize_queue_job_payload,
)

pytestmark = [pytest.mark.speckit]


def test_normalize_task_payload_derives_capabilities() -> None:
    """`type=task` payloads should derive runtime/git capabilities."""

    normalized = normalize_queue_job_payload(
        job_type="task",
        payload={
            "repository": "Moon/Mind",
            "task": {
                "instructions": "Run tests",
                "skill": {"id": "auto", "args": {}},
                "runtime": {"mode": "codex"},
                "git": {"startingBranch": None, "newBranch": None},
                "publish": {"mode": "branch"},
            },
        },
    )

    assert normalized["targetRuntime"] == "codex"
    assert normalized["requiredCapabilities"] == ["codex", "git"]
    assert normalized["task"]["runtime"]["mode"] == "codex"


def test_normalize_task_payload_container_enabled_adds_docker_capability() -> None:
    """Container-enabled tasks should require docker capability automatically."""

    normalized = normalize_queue_job_payload(
        job_type="task",
        payload={
            "repository": "Moon/Mind",
            "task": {
                "instructions": "Run tests in container",
                "skill": {"id": "auto", "args": {}},
                "runtime": {"mode": "codex"},
                "git": {"startingBranch": None, "newBranch": None},
                "publish": {"mode": "none"},
                "container": {
                    "enabled": True,
                    "image": "mcr.microsoft.com/dotnet/sdk:8.0",
                    "command": ["bash", "-lc", "dotnet --info"],
                },
            },
        },
    )

    assert normalized["requiredCapabilities"] == ["codex", "git", "docker"]
    assert normalized["task"]["container"]["enabled"] is True


def test_normalize_task_payload_rejects_enabled_container_without_image() -> None:
    """Container spec validation should fail when enabled image is missing."""

    with pytest.raises(TaskContractError, match="task.container.image"):
        normalize_queue_job_payload(
            job_type="task",
            payload={
                "repository": "Moon/Mind",
                "task": {
                    "instructions": "Run tests in container",
                    "runtime": {"mode": "codex"},
                    "git": {"startingBranch": None, "newBranch": None},
                    "publish": {"mode": "none"},
                    "container": {
                        "enabled": True,
                        "command": ["bash", "-lc", "dotnet --info"],
                    },
                },
            },
        )


def test_normalize_task_payload_accepts_vault_auth_refs() -> None:
    """Canonical task payload should preserve validated auth secret refs."""

    normalized = normalize_queue_job_payload(
        job_type="task",
        payload={
            "repository": "Moon/Mind",
            "auth": {
                "repoAuthRef": "vault://kv/moonmind/repos/Moon/Mind#github_token",
                "publishAuthRef": "vault://kv/moonmind/repos/Moon/Mind#publish_token",
            },
            "task": {
                "instructions": "Run tests",
                "runtime": {"mode": "codex"},
                "git": {"startingBranch": None, "newBranch": None},
                "publish": {"mode": "branch"},
            },
        },
    )

    assert normalized["auth"]["repoAuthRef"] == (
        "vault://kv/moonmind/repos/Moon/Mind#github_token"
    )
    assert normalized["auth"]["publishAuthRef"] == (
        "vault://kv/moonmind/repos/Moon/Mind#publish_token"
    )


def test_normalize_task_payload_rejects_invalid_auth_ref_scheme() -> None:
    """Auth refs must use vault:// URI format."""

    with pytest.raises(TaskContractError, match="vault:// secret references"):
        normalize_queue_job_payload(
            job_type="task",
            payload={
                "repository": "Moon/Mind",
                "auth": {
                    "repoAuthRef": "https://github.com/token",
                },
                "task": {
                    "instructions": "Run tests",
                    "runtime": {"mode": "codex"},
                    "git": {"startingBranch": None, "newBranch": None},
                    "publish": {"mode": "branch"},
                },
            },
        )


def test_normalize_task_payload_rejects_invalid_runtime() -> None:
    """Unsupported runtime names should fail validation for canonical task jobs."""

    with pytest.raises(TaskContractError, match="targetRuntime"):
        normalize_queue_job_payload(
            job_type="task",
            payload={
                "repository": "Moon/Mind",
                "targetRuntime": "unknown-runtime",
                "task": {
                    "instructions": "Run tests",
                    "runtime": {"mode": "codex"},
                    "git": {"startingBranch": None, "newBranch": None},
                    "publish": {"mode": "branch"},
                },
            },
        )


def test_normalize_task_payload_requires_repository() -> None:
    """Canonical task payloads still require repository after normalization."""

    with pytest.raises(TaskContractError, match="repository"):
        normalize_queue_job_payload(
            job_type="task",
            payload={
                "task": {
                    "instructions": "Run tests",
                    "runtime": {"mode": "codex"},
                    "git": {"startingBranch": None, "newBranch": None},
                    "publish": {"mode": "branch"},
                },
            },
        )


def test_normalize_legacy_exec_payload_adds_task_contract_fields() -> None:
    """Legacy codex_exec jobs should keep legacy keys and gain canonical fields."""

    normalized = normalize_queue_job_payload(
        job_type="codex_exec",
        payload={
            "repository": "Moon/Mind",
            "instruction": "Run tests",
            "publish": {"mode": "pr", "baseBranch": "main"},
        },
    )

    assert normalized["repository"] == "Moon/Mind"
    assert normalized["instruction"] == "Run tests"
    assert normalized["targetRuntime"] == "codex"
    assert normalized["task"]["instructions"] == "Run tests"
    assert normalized["task"]["publish"]["mode"] == "pr"
    assert normalized["requiredCapabilities"] == ["codex", "git", "gh"]


def test_normalize_legacy_exec_payload_requires_repository() -> None:
    """Legacy codex_exec payloads must include repository for worker compatibility."""

    with pytest.raises(TaskContractError, match="repository is required"):
        normalize_queue_job_payload(
            job_type="codex_exec",
            payload={"instruction": "Run tests"},
        )


def test_build_canonical_view_for_skill_payload_sets_skill_id() -> None:
    """Compatibility view should expose concrete skill id for legacy codex_skill jobs."""

    canonical = build_canonical_task_view(
        job_type="codex_skill",
        payload={
            "skillId": "speckit",
            "inputs": {"repo": "Moon/Mind", "instruction": "Run"},
        },
    )

    assert canonical["repository"] == "Moon/Mind"
    assert canonical["task"]["skill"]["id"] == "speckit"
    assert canonical["task"]["instructions"] == "Run"
    assert canonical["targetRuntime"] == "codex"


def test_build_canonical_view_for_skill_payload_requires_repository() -> None:
    """Legacy codex_skill payloads must include repo/repository in inputs or top level."""

    with pytest.raises(TaskContractError, match="repository is required"):
        build_canonical_task_view(
            job_type="codex_skill",
            payload={"skillId": "speckit", "inputs": {"instruction": "Run"}},
        )


def test_task_stage_plan_includes_publish_only_when_enabled() -> None:
    """Task stage plan should include publish only for branch/pr modes."""

    branch_plan = build_task_stage_plan(
        {
            "task": {
                "publish": {"mode": "branch"},
            }
        }
    )
    assert branch_plan == [
        "moonmind.task.prepare",
        "moonmind.task.execute",
        "moonmind.task.publish",
    ]

    none_plan = build_task_stage_plan(
        {
            "task": {
                "publish": {"mode": "none"},
            }
        }
    )
    assert none_plan == [
        "moonmind.task.prepare",
        "moonmind.task.execute",
    ]
