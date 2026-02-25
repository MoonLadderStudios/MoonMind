"""Unit tests for the orchestrator skill executor."""

from __future__ import annotations

from pathlib import Path

import pytest

from moonmind.workflows.orchestrator import skill_executor


@pytest.fixture(autouse=True)
def _clear_skill_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "WORKFLOW_SKILLS_LOCAL_MIRROR_ROOT",
        "SPEC_SKILLS_LOCAL_MIRROR_ROOT",
        "WORKFLOW_SKILLS_LEGACY_MIRROR_ROOT",
        "SPEC_SKILLS_LEGACY_MIRROR_ROOT",
        "SPEC_SKILLS_WORKSPACE_ROOT",
        "WORKFLOW_REPO_ROOT",
        "SPEC_WORKFLOW_REPO_ROOT",
        "WORKSPACE_ROOT",
        "CODEX_HOME",
    ):
        monkeypatch.delenv(key, raising=False)


def _write_skill(root: Path, name: str, script_name: str = "run.sh") -> Path:
    skill_path = root / name
    scripts_path = skill_path / "scripts"
    scripts_path.mkdir(parents=True, exist_ok=True)
    (skill_path / "SKILL.md").write_text("# skill\n")
    (scripts_path / script_name).write_text("#!/usr/bin/env bash\necho ok\n")
    return skill_path


def test_validate_skill_id_rejects_path_traversal() -> None:
    with pytest.raises(RuntimeError, match="Invalid skill id"):
        skill_executor._validate_skill_id("../evil")


def test_resolve_repo_path_must_stay_in_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()

    with pytest.raises(RuntimeError, match="must remain inside workspace"):
        skill_executor._resolve_repo_path(workspace, {"repo": str(outside)})


def test_resolve_skill_command_rejects_custom_command(tmp_path: Path) -> None:
    script_path = tmp_path / "run.sh"
    script_path.write_text("#!/usr/bin/env bash\n")

    with pytest.raises(RuntimeError, match="skill_args.command"):
        skill_executor._resolve_skill_command(
            script_path=script_path,
            skill_id="update-moonmind",
            repo_path=tmp_path,
            skill_args={"command": "echo owned"},
        )


def test_resolve_skill_command_maps_update_moonmind_flags(tmp_path: Path) -> None:
    script_path = tmp_path / "run-update-moonmind.sh"
    script_path.write_text("#!/usr/bin/env bash\n")

    command, cwd = skill_executor._resolve_skill_command(
        script_path=script_path,
        skill_id="update-moonmind",
        repo_path=tmp_path,
        skill_args={
            "allowDirty": True,
            "noComposePull": True,
            "dryRun": True,
            "restartOrchestrator": True,
        },
    )

    assert cwd == tmp_path
    assert "--allow-dirty" in command
    assert "--no-compose-pull" in command
    assert "--dry-run" in command
    assert "--restart-orchestrator" in command


def test_resolve_skill_command_treats_false_like_false(tmp_path: Path) -> None:
    script_path = tmp_path / "run-update-moonmind.sh"
    script_path.write_text("#!/usr/bin/env bash\n")

    command, _ = skill_executor._resolve_skill_command(
        script_path=script_path,
        skill_id="update-moonmind",
        repo_path=tmp_path,
        skill_args={
            "allow_dirty": "false",
            "no_compose_pull": "0",
            "dry_run": "no",
            "restart_orchestrator": "off",
        },
    )

    assert "--allow-dirty" not in command
    assert "--no-compose-pull" not in command
    assert "--dry-run" not in command
    assert "--restart-orchestrator" not in command


def test_resolve_skill_command_defaults_compose_project_from_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    script_path = tmp_path / "run-update-moonmind.sh"
    script_path.write_text("#!/usr/bin/env bash\n")

    monkeypatch.setenv("COMPOSE_PROJECT_NAME", "moonmind")

    command, _ = skill_executor._resolve_skill_command(
        script_path=script_path,
        skill_id="update-moonmind",
        repo_path=tmp_path,
        skill_args={},
    )

    assert "--compose-project" in command
    assert "moonmind" in command


def test_is_runnable_skill_requires_detectable_script(tmp_path: Path) -> None:
    local_root = tmp_path / ".agents" / "skills" / "local"
    local_root.mkdir(parents=True)
    _write_skill(local_root, "runnable")

    no_script = local_root / "docs-only"
    no_script.mkdir(parents=True)
    (no_script / "SKILL.md").write_text("# docs\n")

    assert skill_executor.is_runnable_skill("runnable", workspace_root=tmp_path)
    assert not skill_executor.is_runnable_skill("docs-only", workspace_root=tmp_path)


def test_list_runnable_skill_names_filters_non_runnable(tmp_path: Path) -> None:
    local_root = tmp_path / ".agents" / "skills" / "local"
    legacy_root = tmp_path / ".agents" / "skills"
    local_root.mkdir(parents=True)
    legacy_root.mkdir(parents=True, exist_ok=True)

    _write_skill(local_root, "alpha")
    _write_skill(legacy_root / "skills", "beta")

    docs_only = local_root / "docs-only"
    docs_only.mkdir(parents=True)
    (docs_only / "SKILL.md").write_text("# docs\n")

    assert skill_executor.list_runnable_skill_names(workspace_root=tmp_path) == (
        "alpha",
        "beta",
    )


def test_main_returns_error_code_without_traceback_for_runtime_errors(
    tmp_path: Path,
) -> None:
    rc = skill_executor.main(["--skill-id", "../nope", "--skill-args-json", "{}"])
    assert rc == 2
